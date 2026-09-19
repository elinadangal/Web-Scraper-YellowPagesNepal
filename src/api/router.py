from fastapi import APIRouter
from pathlib import Path
from src.models.sitemap import Sitemap
from config.settings import SITEMAP_PATH

router = APIRouter(prefix="/api/v1", tags=["Scraper"])

@router.get("/status")
async def get_scraper_status():
    """Endpoint to check current sitemap progress via API."""
    if not Path(SITEMAP_PATH).exists():
        return {"status": "No sitemap found", "progress": 0}
    
    sitemap = Sitemap.deserialize(Path(SITEMAP_PATH))
    total_pages = sum(g.total_pages for g in sitemap.alphabets.values())
    completed_pages = sum(g.completed_pages for g in sitemap.alphabets.values())
    
    return {
        "status": "completed" if completed_pages == total_pages else "in_progress",
        "completed_pages": completed_pages,
        "total_pages": total_pages,
        "percentage": round((completed_pages / total_pages * 100), 2) if total_pages > 0 else 0
    }