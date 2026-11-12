"""Build a held-out test manifest from a larger ABO subset.

Keeps only the new subset's test split, drops any case whose product identity
appeared anywhere in an earlier manifest (train, validation or test), and
interleaves categories and labels so that any prefix of the run is balanced.
The fusion model and its threshold stay locked from the earlier run; this
script never looks at model outputs.

    python -m ml.scripts.make_heldout_manifest NEW_MANIFEST OLD_MANIFEST [OLD_MANIFEST ...] --output OUT
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path


def _identities(record: dict) -> set[str]:
    keys = ("dispatch_item_id", "return_item_id", "item_id", "substitute_item_id")
    found = {str(record[key]) for key in keys if record.get(key)}
    for value in record.get("slices", []) or []:
        if isinstance(value, str) and value.startswith("item:"):
            found.add(value.split(":", 1)[1])
    return found


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("new_manifest", type=Path)
    parser.add_argument("old_manifests", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", choices=["validation", "test"], default="test")
    parser.add_argument("--exclude", nargs="*", type=Path, default=[], help="further manifests whose identities must not appear")
    args = parser.parse_args()

    seen: set[str] = set()
    for path in [*args.old_manifests, *args.exclude]:
        for record in _read(path):
            seen |= _identities(record)

    test = [record for record in _read(args.new_manifest) if record.get("split") == args.split]
    if not test or not _identities(test[0]):
        raise SystemExit("manifest records carry no item identities; cannot prove the split is disjoint")
    kept = [record for record in test if not (_identities(record) & seen)]
    dropped = len(test) - len(kept)

    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in kept:
        buckets[(str(record.get("category")), str(record.get("label")))].append(record)
    for bucket in buckets.values():
        bucket.sort(key=lambda record: str(record["case_id"]))
    ordered = [record for group in zip_longest(*[buckets[key] for key in sorted(buckets)]) for record in group if record]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for record in ordered:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    labels = defaultdict(int)
    for record in ordered:
        labels[str(record.get("label"))] += 1
    print(json.dumps({
        "output": str(args.output),
        "test_cases_in_new_subset": len(test),
        "dropped_for_identity_overlap": dropped,
        "held_out_cases": len(ordered),
        "labels": dict(labels),
        "old_identities_checked": len(seen),
    }, indent=2))


if __name__ == "__main__":
    main()
