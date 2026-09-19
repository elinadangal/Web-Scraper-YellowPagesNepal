import re
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

def category_filename_slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"\s*&\s*", "&", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r'[\\/:*?"<>|]', "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s

def name_from_url(business_url: str) -> str:
    slug = urlparse(business_url).path.strip("/")
    return slug.replace("-", " ").replace("_", " ").strip().title() or business_url

def get_total_pages(html: str) -> int:
    m = re.search(r"page\s+\d+\s+of\s+(\d+)", html, re.I)
    return int(m.group(1)) if m else 1

def build_extraction_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    main_container = soup.select_one(
        ".main-content, .business-detail, .company-details, #listing-detail, .card-body"
    ) or soup

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

    title_tag = soup.find("h1") or soup.find("title")
    fallback_name = title_tag.get_text(strip=True) if title_tag else ""

    return body_text[:8000], fallback_name