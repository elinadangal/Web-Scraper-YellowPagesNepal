import asyncio
from pathlib import Path

BASE_URL = "https://www.yellowpagesnepal.com"
# LLM_URL = "https://dev-models.wiseai.wiseyak.com/v1/chat/completions"
# LLM_URL = "https://stage-llm.wiseai.wiseyak.com/v1/chat/completions"
LLM_URL = "http://45.115.219.58:20003/v1/chat/completions"

OUTPUT_DIR = Path("output")
SITEMAP_PATH = Path("sitemap.json")
PROGRESS_LOG_PATH = Path("progress_log.jsonl")

LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]

BLACKLISTED_PATHS = [
    "/", "/listing/new", "/feedback", "/categories", "/about-us", "/contact",
    "/login", "/register", "/become-a-partner", "/advertise-with-us",
    "/blog", "weblink", "yellowpagesnepal",
]

CRAWL_CONCURRENCY = 6
LLM_CONCURRENCY = 4
PAGE_WORKERS = 8

CRAWL_SEM = asyncio.Semaphore(CRAWL_CONCURRENCY)
LLM_SEM = asyncio.Semaphore(LLM_CONCURRENCY)
SITEMAP_SAVE_LOCK = asyncio.Lock()