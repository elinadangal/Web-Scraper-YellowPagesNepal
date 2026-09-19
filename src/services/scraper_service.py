import asyncio
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig

from config.settings import (
    BASE_URL, LETTERS, BLACKLISTED_PATHS, CRAWL_SEM, SITEMAP_PATH, SITEMAP_SAVE_LOCK
)
from src.models.sitemap import Sitemap, AlphabetGroup, Category, ListingPage
from src.repository.file_repository import FileRepository
from src.repository.progress_logger import ProgressLogger
from src.services.llm_service import LLMService
from src.utils.text_helpers import get_total_pages, build_extraction_text, name_from_url

class ScraperService:
    def __init__(self, repo: FileRepository, logger: ProgressLogger):
        self.repo = repo
        self.logger = logger

    async def fetch_and_extract(self, crawler, http_client, config, business_url: str) -> dict | None:
        try:
            result = None
            for attempt in (1, 2):
                async with CRAWL_SEM:
                    result = await crawler.arun(url=business_url, config=config)
                if result.success and result.html:
                    break
                if attempt == 1:
                    await asyncio.sleep(1.5)

            if not result.success or not result.html:
                print(f"     [drop] crawl failed after retry: {business_url}")
                return None

            page_text, fallback_name = build_extraction_text(result.html)
            details = await LLMService.extract_business_details(http_client, page_text)

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

    async def build_sitemap(self, crawler: AsyncWebCrawler, config: CrawlerRunConfig) -> Sitemap:
        print("Building Sitemap hierarchy...")
        sitemap = Sitemap()

        for letter in LETTERS:
            alpha_group = AlphabetGroup(letter=letter)
            cat_url = f"{BASE_URL}/categories?st={letter}"

            async with CRAWL_SEM:
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

                full_url = urljoin(BASE_URL + "/", href)
                slug = urlparse(full_url).path.strip("/")
                if not slug or slug in alpha_group.categories:
                    continue

                async with CRAWL_SEM:
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

    async def process_page_job(self, crawler, http_client, config, letter: str, category_name: str, page: ListingPage):
        out_path = self.repo.get_output_path(letter, category_name, page.page_num)

        if out_path.exists():
            page.processed = True
            page.file_path = str(out_path)
            page.business_count = self.repo.count_businesses_in_file(out_path)
            return

        async with CRAWL_SEM:
            result = await crawler.arun(url=page.url, config=config)

        if not result.success or not result.html:
            print(f"     Failed to fetch listing page {page.url}")
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
            full_url = urljoin(BASE_URL + "/", href)
            if full_url not in seen:
                seen.add(full_url)
                business_links.append(full_url)

        if not business_links:
            print(f"     [warn] no <h3><a> business links found on page {page.page_num} of '{category_name}'")
            return

        tasks = [self.fetch_and_extract(crawler, http_client, config, url) for url in business_links]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        businesses = [r for r in results if isinstance(r, dict)]

        self.repo.save_json(out_path, businesses)

        page.processed = True
        page.business_count = len(businesses)
        page.file_path = str(out_path)
        print(f"     Saved {len(businesses)}/{len(business_links)} businesses -> {out_path.name}")

    async def page_worker(self, worker_id: int, crawler, http_client, config, queue: asyncio.Queue, sitemap: Sitemap):
        processed_counter = 0
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                break

            letter, category_name, page = item
            try:
                await self.process_page_job(crawler, http_client, config, letter, category_name, page)

                processed_counter += 1
                if processed_counter % 5 == 0:
                    async with SITEMAP_SAVE_LOCK:
                        sitemap.serialize(SITEMAP_PATH)
                await self.logger.maybe_log(sitemap)

            except Exception as e:
                print(f"[Worker {worker_id}] Error processing page {page.page_num} of {category_name}: {e}")
            finally:
                queue.task_done()