import json
from pathlib import Path
from config.settings import OUTPUT_DIR
from src.utils.text_helpers import category_filename_slug

class FileRepository:
    def __init__(self, output_dir: Path = OUTPUT_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)

    def get_output_path(self, letter: str, category_name: str, page: int) -> Path:
        slug = category_filename_slug(category_name)
        category_dir = self.output_dir / letter / slug
        category_dir.mkdir(parents=True, exist_ok=True)
        return category_dir / f"root_{letter}_{slug}_{page}.json"

    def count_businesses_in_file(self, path: Path) -> int:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data) if isinstance(data, list) else 0
        except Exception:
            return 0

    def save_json(self, path: Path, data: list | dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)