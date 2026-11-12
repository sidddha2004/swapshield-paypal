"""Turn cached feature rows into single-signal baseline predictions.

The output has the same shape as the fusion scorer's, so the same evaluator
(threshold chosen on validation by cost, then applied once to test) measures
each baseline exactly as it measures the trained fusion model.

    python -m ml.scripts.baseline_predictions FEATURES [FEATURES ...] --signal dinov2|vlm --output OUT
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SIGNALS = {
    # higher probability = more likely a substitution
    "dinov2": lambda features: 1.0 - float(features["vision_similarity"]),
    "vlm": lambda features: float(features["vlm_mismatch"]),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("features", nargs="+", type=Path)
    parser.add_argument("--signal", required=True, choices=sorted(SIGNALS))
    parser.add_argument("--include-split", action="append", choices=["validation", "test"])
    parser.add_argument("--min-image-quality", type=float, default=0.46)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    splits = set(args.include_split or ["validation", "test"])
    score = SIGNALS[args.signal]
    rows = []
    for path in args.features:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") not in splits:
                continue
            probability = min(max(score(row["features"]), 0.0), 1.0)
            quality = float(row["features"].get("image_quality", 1.0))
            rows.append({
                "case_id": row["case_id"],
                "split": row["split"],
                "label": row["label"],
                "target": row["target"],
                "category": row.get("category"),
                "slices": row.get("slices", []),
                "probability": probability,
                # same evidence gate as the full pipeline, so recapture rates compare
                "decision": "recapture" if quality < args.min_image_quality else ("review" if probability >= 0.5 else "approve"),
                "latency_ms": row.get("latency_ms", 0.0),
            })
    if not rows:
        raise SystemExit("no rows matched")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "signal": args.signal, "rows": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
