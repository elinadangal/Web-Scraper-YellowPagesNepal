import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from src.models.sitemap import Sitemap

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
        now = time.time()
        async with self._lock:
            if now - self._last_log_time < self.interval:
                return
            self._last_log_time = now
            self._write(sitemap)

    def log_now(self, sitemap: Sitemap, label: str = ""):
        self._last_log_time = time.time()
        entry = self._snapshot(sitemap)
        if label:
            entry["event"] = label
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")