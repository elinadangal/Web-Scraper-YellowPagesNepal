import asyncio
import httpx
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

from config.settings import SITEMAP_PATH, PROGRESS_LOG_PATH, PAGE_WORKERS
from src.models.sitemap import Sitemap
from src.repository.file_repository import FileRepository
from src.repository.progress_logger import ProgressLogger
from src.services.scraper_service import ScraperService

async def main():
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    repo = FileRepository()
    logger = ProgressLogger(PROGRESS_LOG_PATH, interval_seconds=60.0)
    service = ScraperService(repo, logger)

    async with httpx.AsyncClient() as http_client:
        async with AsyncWebCrawler(verbose=False) as crawler:

            if SITEMAP_PATH.exists():
                print("Loading existing sitemap.json...")
                sitemap = Sitemap.deserialize(SITEMAP_PATH)
            else:
                sitemap = await service.build_sitemap(crawler, config)

            sitemap.show_progress_summary()
            logger.log_now(sitemap, label="run_start")

            workers = [
                asyncio.create_task(
                    service.page_worker(i, crawler, http_client, config, queue, sitemap)
                )
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
            logger.log_now(sitemap, label="run_end")

if __name__ == "__main__":
    asyncio.run(main())