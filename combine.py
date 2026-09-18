"""
Walks the entire output/ folder (output/{letter}/{category}/*.json),
combines every business record into one list, and removes duplicates.

Dedup strategy:
- Primary key: source_url. Two records with the same source_url are the
  exact same business (e.g. if a page ever got crawled more than once
  across different runs).
- When duplicates are found, keeps whichever record is more complete
  (fewer null fields) rather than just the first one seen — so if an
  earlier crawl saved a name-only fallback entry and a later recrawl got
  the real data, the real data wins.

Produces:
  combined_output.json — the full deduplicated dataset
  combined_output.csv  — the same data as CSV, for easy viewing in Excel
"""

import csv
import json
from pathlib import Path

OUTPUT_DIR = Path("output")
COMBINED_JSON_PATH = Path("combined_output.json")
COMBINED_CSV_PATH = Path("combined_output.csv")

FIELDS = ["name", "description", "phone_number", "email", "address", "website", "source_url"]


def completeness_score(record: dict) -> int:
    """How many of the real data fields are non-null/non-empty. Higher = more complete."""
    return sum(
        1 for field in ("description", "phone_number", "email", "address", "website")
        if record.get(field)
    )


def main():
    if not OUTPUT_DIR.exists():
        print("No output/ folder found.")
        return

    all_records = []
    files_read = 0
    files_skipped = 0

    for file in OUTPUT_DIR.rglob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  [skip] could not read {file}: {e}")
            files_skipped += 1
            continue

        if not isinstance(data, list):
            print(f"  [skip] not a list: {file}")
            files_skipped += 1
            continue

        all_records.extend(data)
        files_read += 1

    print(f"Read {files_read} files ({files_skipped} skipped), {len(all_records)} raw records total.")

    # Dedup by source_url, keeping the most complete version of each.
    by_url = {}
    no_url_records = []

    for record in all_records:
        url = record.get("source_url")
        if not url:
            # Shouldn't normally happen, but keep these rather than lose them.
            no_url_records.append(record)
            continue

        existing = by_url.get(url)
        if existing is None or completeness_score(record) > completeness_score(existing):
            by_url[url] = record

    combined = list(by_url.values()) + no_url_records
    duplicates_removed = len(all_records) - len(combined)

    with open(COMBINED_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2, ensure_ascii=False)

    with open(COMBINED_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in combined:
            writer.writerow(record)

    print(f"\nDone.")
    print(f"  Total raw records:      {len(all_records)}")
    print(f"  Duplicates removed:     {duplicates_removed}")
    print(f"  Final unique records:   {len(combined)}")
    print(f"  Saved -> {COMBINED_JSON_PATH}")
    print(f"  Saved -> {COMBINED_CSV_PATH}")


if __name__ == "__main__":
    main()