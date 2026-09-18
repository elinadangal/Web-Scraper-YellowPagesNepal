import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ListingPage:
    page_num: int
    url: str
    processed: bool = False
    business_count: int = 0
    file_path: Optional[str] = None


@dataclass
class Category:
    name: str
    slug: str
    url: str
    letter: str
    total_pages: int = 0
    pages: Dict[int, ListingPage] = field(default_factory=dict)

    @property
    def completed_pages_count(self) -> int:
        return sum(1 for p in self.pages.values() if p.processed)

    @property
    def is_complete(self) -> bool:
        return self.total_pages > 0 and self.completed_pages_count == self.total_pages


@dataclass
class AlphabetGroup:
    letter: str
    total_categories: int = 0
    categories: Dict[str, Category] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        return sum(cat.total_pages for cat in self.categories.values())

    @property
    def completed_pages(self) -> int:
        return sum(cat.completed_pages_count for cat in self.categories.values())

    @property
    def is_complete(self) -> bool:
        return len(self.categories) > 0 and all(cat.is_complete for cat in self.categories.values())


@dataclass
class Sitemap:
    base_url: str = "https://www.yellowpagesnepal.com"
    alphabets: Dict[str, AlphabetGroup] = field(default_factory=dict)

    def serialize(self, file_path: Path = Path("sitemap.json")):
        """Serializes the complete tree to JSON. Writes to a temp file first
        and atomically replaces the real file, so a Ctrl+C or crash mid-write
        can never leave you with a half-written, corrupt sitemap.json."""
        data = {
            "base_url": self.base_url,
            "alphabets": {
                letter: {
                    "letter": group.letter,
                    "total_categories": len(group.categories),
                    "categories": {
                        slug: {
                            "name": cat.name,
                            "slug": cat.slug,
                            "url": cat.url,
                            "letter": cat.letter,
                            "total_pages": cat.total_pages,
                            "pages": {
                                str(num): asdict(page) for num, page in cat.pages.items()
                            }
                        }
                        for slug, cat in group.categories.items()
                    }
                }
                for letter, group in self.alphabets.items()
            }
        }
        file_path = Path(file_path)
        tmp_path = file_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(file_path)

    @classmethod
    def deserialize(cls, file_path: Path = Path("sitemap.json")) -> "Sitemap":
        """Deserializes state tree from JSON."""
        if not file_path.exists():
            return cls()

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        sitemap = cls(base_url=data.get("base_url", ""))
        for letter, alpha_data in data.get("alphabets", {}).items():
            group = AlphabetGroup(
                letter=letter,
                total_categories=alpha_data.get("total_categories", 0)
            )
            for slug, cat_data in alpha_data.get("categories", {}).items():
                category = Category(
                    name=cat_data["name"],
                    slug=cat_data["slug"],
                    url=cat_data["url"],
                    letter=cat_data["letter"],
                    total_pages=cat_data["total_pages"]
                )
                for num_str, page_data in cat_data.get("pages", {}).items():
                    category.pages[int(num_str)] = ListingPage(**page_data)

                group.categories[slug] = category

            sitemap.alphabets[letter] = group

        return sitemap

    def show_progress_summary(self):
        """Displays total and per-alphabet breakdown of progress."""
        print("\n" + "=" * 65)
        print("                  SITEMAP PROGRESS SUMMARY                   ")
        print("=" * 65)
        print(f"{'Letter':<8} | {'Categories':<12} | {'Completed Pages / Total':<25} | {'Status'}")
        print("-" * 65)

        grand_total_pages = 0
        grand_completed_pages = 0

        for letter in sorted(self.alphabets.keys()):
            group = self.alphabets[letter]
            tot_pages = group.total_pages
            comp_pages = group.completed_pages
            grand_total_pages += tot_pages
            grand_completed_pages += comp_pages

            status = "DONE" if group.is_complete else f"({comp_pages}/{tot_pages})"
            cats_str = f"{len(group.categories)}"
            pages_str = f"{comp_pages} / {tot_pages}"

            print(f"[{letter}]{'':<5} | {cats_str:<12} | {pages_str:<25} | {status}")

        print("-" * 65)
        rem = grand_total_pages - grand_completed_pages
        print(f"TOTALS: {grand_completed_pages}/{grand_total_pages} Pages Done. ({rem} remaining)")
        print("=" * 65 + "\n")


if __name__ == "__main__":
    sitemap_file = Path("sitemap.json")
    if sitemap_file.exists():
        sitemap = Sitemap.deserialize(sitemap_file)
        sitemap.show_progress_summary()
    else:
        print("No sitemap.json found. Run main.py first to build the sitemap.")