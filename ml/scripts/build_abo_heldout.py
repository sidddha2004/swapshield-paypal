"""Download only the test split of a larger ABO subset, for a held-out benchmark.

Selection is identical to `build_abo_subset` (same seed, categories and split
rule), so the products are exactly the test split that builder would choose;
only the train and validation images, which a locked model does not need, are
skipped.

    python -m ml.scripts.build_abo_heldout --output data/real/abo/heldout-v2 --items 1000
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from ml.swapshield_ml.abo_subset import (
    DEFAULT_CATEGORIES,
    LISTING_SHARDS,
    SPIN_METADATA_KEY,
    build_manifest_records,
    download_file,
    download_selected_images,
    parse_listing_files,
    parse_spin_views,
    select_products,
    write_attribution,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--items", type=int, default=1000)
    parser.add_argument("--views-per-item", type=int, default=3)
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--seed", type=int, default=5050)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--split", choices=["validation", "test"], default="test")
    args = parser.parse_args()

    cache = args.output / ".cache"
    listing_paths = []
    for key in LISTING_SHARDS:
        destination = cache / PurePosixPath(key).name
        download_file(key, destination)
        listing_paths.append(destination)
    spins = cache / PurePosixPath(SPIN_METADATA_KEY).name
    download_file(SPIN_METADATA_KEY, spins)

    categories = [category.upper() for category in args.categories]
    listings = parse_listing_files(listing_paths, categories)
    views = parse_spin_views(spins, {item.spin_id for item in listings})
    products = select_products(
        listings, views, categories=categories, item_count=args.items,
        views_per_item=args.views_per_item, seed=args.seed,
    )
    test = [product for product in products if product.split == args.split]
    download_selected_images(test, args.output, workers=args.workers)
    records = build_manifest_records(test)
    write_jsonl(records, args.output / "manifest.jsonl")
    write_attribution(args.output)
    summary = {
        "dataset": "Amazon Berkeley Objects (ABO)", "license": "CC BY 4.0", "seed": args.seed,
        "selected_items": args.items, "split": args.split, "items": len(test), "cases": len(records),
        "categories": categories, "views_per_item": args.views_per_item,
    }
    (args.output / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
