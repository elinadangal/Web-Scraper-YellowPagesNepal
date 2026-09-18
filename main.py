import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import httpx
from bs4 import BeautifulSoup
from json_repair import repair_json
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

from sitemap import Sitemap, AlphabetGroup, Category, ListingPage

BASE = "https://www.yellowpagesnepal.com"
# LLM_URL = "https://dev-models.wiseai.wiseyak.com/v1/chat/completions"
# LLM_URL = "https://stage-llm.wiseai.wiseyak.com/v1/chat/completions"
LLM_URL = "http://45.115.219.58:20003/v1/chat/completions"


OUTPUT_DIR = Path("output")
SITEMAP_PATH = Path("sitemap.json")
PROGRESS_LOG_PATH = Path("progress_log.jsonl")
OUTPUT_DIR.mkdir(exist_ok=True)

LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]

BLACKLISTED_PATHS = [
    "/", "/listing/new", "/feedback", "/categories", "/about-us", "/contact",
    "/login", "/register", "/become-a-partner", "/advertise-with-us",
    "/blog", "weblink", "yellowpagesnepal",
]

CRAWL_CONCURRENCY = 6
LLM_CONCURRENCY = 4
PAGE_WORKERS = 8
crawl_sem = asyncio.Semaphore(CRAWL_CONCURRENCY)
llm_sem = asyncio.Semaphore(LLM_CONCURRENCY)

# Guards sitemap.json writes so multiple workers never interleave partial
# writes to the same file at once.
sitemap_save_lock = asyncio.Lock()


# --------------------------------------------------------------------------
# Naming / path helpers
# --------------------------------------------------------------------------

def category_filename_slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"\s*&\s*", "&", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r'[\\/:*?"<>|]', "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def output_path(letter: str, category_name: str, page: int) -> Path:
    slug = category_filename_slug(category_name)
    category_dir = OUTPUT_DIR / letter / slug
    category_dir.mkdir(parents=True, exist_ok=True)
    return category_dir / f"root_{letter}_{slug}_{page}.json"


def name_from_url(business_url: str) -> str:
    slug = urlparse(business_url).path.strip("/")
    return slug.replace("-", " ").replace("_", " ").strip().title() or business_url


def get_total_pages(html: str) -> int:
    m = re.search(r"page\s+\d+\s+of\s+(\d+)", html, re.I)
    return int(m.group(1)) if m else 1


# --------------------------------------------------------------------------
# Level 3: business detail page -> LLM extraction
# --------------------------------------------------------------------------

def build_extraction_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    main_container = soup.select_one(
        ".main-content, .business-detail, .company-details, #listing-detail, .card-body"
    ) or soup

    # Phone/email are rendered as <img src=".../texttoimage.php?text=...">
    # to block scrapers — pull the real value out of the query string.
    obfuscated_values = []
    for img in main_container.find_all("img", src=True):
        src = img["src"]
        if "texttoimage.php" not in src:
            continue
        qs = parse_qs(urlparse(src).query)
        values = qs.get("text")
        if values:
            obfuscated_values.append(values[0])

    body_text = main_container.get_text(separator="\n", strip=True)

    if obfuscated_values:
        hidden_block = "\n".join(f"Hidden contact value: {v}" for v in obfuscated_values)
        body_text = f"{body_text}\n\n{hidden_block}"

    # Fallback name source for junk/spam listings where the LLM finds
    # nothing clean to call a "name" — better to keep the entry with a
    # rough name than drop it entirely.
    title_tag = soup.find("h1") or soup.find("title")
    fallback_name = title_tag.get_text(strip=True) if title_tag else ""

    return body_text[:8000], fallback_name


async def extract_business_details(client: httpx.AsyncClient, page_text: str) -> dict:
    prompt = f"""
    Extract business contact details from the provided text.

    Return a strict, valid JSON object with this EXACT structure:
    {{
        "name": "Business Name or null",
        "description": "Full description or null",
        "phone_number": "All phone/mobile/fax numbers separated by commas or null",
        "email": "Email address or null",
        "address": "Full physical address or null",
        "website": "Website URL or null"
    }}

    Rules:
    - Phone numbers and emails may appear as lines like "Hidden contact value: X" —
      these came from images on the page and are just as valid as normal text.
      Classify each one as phone_number or email based on its format.
    - If multiple phone numbers exist, combine them with commas like "4275166, 4280132".
    - Return ONLY raw JSON. No introductory text or markdown ticks.

    Content:
    {page_text}
    """

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    async with llm_sem:
        for attempt in (1, 2, 3):
            try:
                response = await client.post(LLM_URL, json=payload, timeout=60.0)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"].strip()

                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    content = match.group(0)

                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    parsed = json.loads(repair_json(content))

                if isinstance(parsed, list):
                    parsed = next((x for x in parsed if isinstance(x, dict)), {})
                if not isinstance(parsed, dict):
                    parsed = {}
                return parsed
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429 and attempt < 3:
                    # Rate limited — respect Retry-After if the server sent
                    # one, otherwise back off longer than a plain 502 since
                    # this means "you're sending too fast", not "try again
                    # immediately".
                    retry_after = e.response.headers.get("retry-after")
                    wait = float(retry_after) if retry_after else 5 * attempt
                    print(f"     LLM 429 (rate limited), waiting {wait}s, retrying ({attempt}/3)...")
                    await asyncio.sleep(wait)
                    continue
                if status in (502, 503, 504) and attempt < 3:
                    print(f"     LLM {status}, retrying ({attempt}/3)...")
                    await asyncio.sleep(2 * attempt)
                    continue
                print(f"     Extraction Error: {e}")
                return {}
            except Exception as e:
                if attempt < 3:
                    print(f"     LLM error, retrying ({attempt}/3): {e}")
                    await asyncio.sleep(2 * attempt)
                    continue
                print(f"     Extraction Error: {e}")
                return {}
        return {}


async def fetch_and_extract(crawler, http_client, config, business_url: str) -> dict | None:
    try:
        result = None
        for attempt in (1, 2):
            async with crawl_sem:
                result = await crawler.arun(url=business_url, config=config)
            if result.success and result.html:
                break
            if attempt == 1:
                await asyncio.sleep(1.5)

        if not result.success or not result.html:
            print(f"     [drop] crawl failed after retry: {business_url}")
            return None

        page_text, fallback_name = build_extraction_text(result.html)
        details = await extract_business_details(http_client, page_text)

        if not isinstance(details, dict):
            print(f"     [drop] extraction returned non-dict: {business_url}")
            return None

        if not details.get("name"):
            details["name"] = fallback_name or name_from_url(business_url)

        name = details["name"].lower()
        if any(bad in name for bad in ["weblink", "yellow pages nepal"]):
            print(f"     [drop] filtered as non-business entry: {business_url}")
            return None

        details["source_url"] = business_url
        return details
    except Exception as e:
        print(f"     [drop] exception: {business_url}: {e}")
        return None


# --------------------------------------------------------------------------
# Progress log: a timestamped history of totals, separate from sitemap.json
# (which only holds the *current* state). This answers "how many pages
# were done at time T" across a run, including across machines.
# --------------------------------------------------------------------------

class ProgressLogger:
    def __init__(self, path: Path, interval_seconds: float = 60.0):
        self.path = path
        self.interval = interval_seconds
        self._last_log_time = 0.0
        self._lock = asyncio.Lock()

    def _snapshot(self, sitemap: Sitemap) -> dict:
        total_pages = sum(g.total_pages for g in sitemap.alphabets.values())
        completed_pages = sum(g.completed_pages for g in sitemap.alphabets.values())
        total_categories = sum(len(g.categories) for g in sitemap.alphabets.values())
        completed_categories = sum(
            sum(1 for c in g.categories.values() if c.is_complete)
            for g in sitemap.alphabets.values()
        )
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "pages_completed": completed_pages,
            "pages_total": total_pages,
            "categories_completed": completed_categories,
            "categories_total": total_categories,
        }

    def _write(self, sitemap: Sitemap):
        entry = self._snapshot(sitemap)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    async def maybe_log(self, sitemap: Sitemap):
        """Called frequently by workers; only actually writes once per
        `interval` seconds, so the log reads as a clean timeline
        (t1: X pages, t2: Y pages, ...) instead of a line per page."""
        now = time.time()
        async with self._lock:
            if now - self._last_log_time < self.interval:
                return
            self._last_log_time = now
            self._write(sitemap)

    def log_now(self, sitemap: Sitemap, label: str = ""):
        """Force an entry regardless of interval — used at run start/end."""
        self._last_log_time = time.time()
        entry = self._snapshot(sitemap)
        if label:
            entry["event"] = label
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


progress_logger = ProgressLogger(PROGRESS_LOG_PATH, interval_seconds=60.0)


# --------------------------------------------------------------------------
# Sitemap building (Level 1 + Level 2 discovery)
# --------------------------------------------------------------------------

async def build_sitemap(crawler: AsyncWebCrawler, config: CrawlerRunConfig) -> Sitemap:
    """Discovers Alphabets -> Categories -> Total Pages and outputs sitemap.json."""
    print("Building Sitemap hierarchy...")
    sitemap = Sitemap()

    for letter in LETTERS:
        alpha_group = AlphabetGroup(letter=letter)
        cat_url = f"{BASE}/categories?st={letter}"

        async with crawl_sem:
            result = await crawler.arun(url=cat_url, config=config)

        if not result.success or not result.html:
            continue

        soup = BeautifulSoup(result.html, "html.parser")
        marker = soup.find(string=re.compile(r"Search results for", re.I))
        scope = marker.find_parent(["div", "section"]) if marker else soup
        container = scope.find_next("ul") if scope else soup

        for a in (container or soup).find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True)
            if not href or not text or any(b in href.lower() for b in BLACKLISTED_PATHS):
                continue

            full_url = urljoin(BASE + "/", href)
            slug = urlparse(full_url).path.strip("/")
            if not slug or slug in alpha_group.categories:
                continue

            async with crawl_sem:
                cat_res = await crawler.arun(url=full_url, config=config)

            total_pages = get_total_pages(cat_res.html) if (cat_res.success and cat_res.html) else 1

            category = Category(
                name=text, slug=slug, url=full_url, letter=letter, total_pages=total_pages
            )

            for p in range(1, total_pages + 1):
                p_url = full_url if p == 1 else f"{full_url}?page={p}"
                category.pages[p] = ListingPage(page_num=p, url=p_url)

            alpha_group.categories[slug] = category

        sitemap.alphabets[letter] = alpha_group
        print(f" -> Letter [{letter}]: Discovered {len(alpha_group.categories)} categories, {alpha_group.total_pages} total pages.")

    sitemap.serialize(SITEMAP_PATH)
    return sitemap


# --------------------------------------------------------------------------
# Level 2/3: processing a single listing page
# --------------------------------------------------------------------------

def _count_businesses_in_file(out_path: Path) -> int:
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        return len(existing) if isinstance(existing, list) else 0
    except Exception:
        return 0


async def process_page_job(crawler, http_client, config, letter: str, category_name: str, page: ListingPage):
    out_path = output_path(letter, category_name, page.page_num)

    # Resume check: skip if file already exists on disk — but still read it
    # so business_count reflects reality instead of staying at 0.
    if out_path.exists():
        page.processed = True
        page.file_path = str(out_path)
        page.business_count = _count_businesses_in_file(out_path)
        return

    async with crawl_sem:
        result = await crawler.arun(url=page.url, config=config)

    if not result.success or not result.html:
        print(f"    Failed to fetch listing page {page.url}")
        return

    soup = BeautifulSoup(result.html, "html.parser")
    business_links = []
    seen = set()
    for h3 in soup.find_all("h3"):
        a = h3.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if not href or "page=" in href.lower() or any(b in href.lower() for b in BLACKLISTED_PATHS):
            continue
        full_url = urljoin(BASE + "/", href)
        if full_url not in seen:
            seen.add(full_url)
            business_links.append(full_url)

    if not business_links:
        print(f"    [warn] no <h3><a> business links found on page {page.page_num} of '{category_name}'")
        return

    tasks = [fetch_and_extract(crawler, http_client, config, url) for url in business_links]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    businesses = [r for r in results if isinstance(r, dict)]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(businesses, f, indent=4, ensure_ascii=False)

    page.processed = True
    page.business_count = len(businesses)
    page.file_path = str(out_path)
    print(f"    Saved {len(businesses)}/{len(business_links)} businesses -> {out_path.name}")


async def page_worker(worker_id: int, crawler, http_client, config, queue: asyncio.Queue, sitemap: Sitemap):
    processed_counter = 0
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break

        letter, category_name, page = item
        try:
            await process_page_job(crawler, http_client, config, letter, category_name, page)

            processed_counter += 1
            if processed_counter % 5 == 0:
                async with sitemap_save_lock:
                    sitemap.serialize(SITEMAP_PATH)
            await progress_logger.maybe_log(sitemap)

        except Exception as e:
            print(f"[Worker {worker_id}] Error processing page {page.page_num} of {category_name}: {e}")
        finally:
            queue.task_done()


async def main():
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    async with httpx.AsyncClient() as http_client:
        async with AsyncWebCrawler(verbose=False) as crawler:

            if SITEMAP_PATH.exists():
                print("Loading existing sitemap.json...")
                sitemap = Sitemap.deserialize(SITEMAP_PATH)
            else:
                sitemap = await build_sitemap(crawler, config)

            sitemap.show_progress_summary()
            progress_logger.log_now(sitemap, label="run_start")

            workers = [
                asyncio.create_task(page_worker(i, crawler, http_client, config, queue, sitemap))
                for i in range(PAGE_WORKERS)
            ]

            for group in sitemap.alphabets.values():
                for cat in group.categories.values():
                    for page in cat.pages.values():
                        if not page.processed:
                            await queue.put((group.letter, cat.name, page))

            await queue.join()

            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)

            sitemap.serialize(SITEMAP_PATH)
            sitemap.show_progress_summary()
            progress_logger.log_now(sitemap, label="run_end")


if __name__ == "__main__":
    asyncio.run(main())