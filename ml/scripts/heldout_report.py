"""Score the held-out benchmark: locked fusion model and single-signal baselines.

Every method gets the same protocol: its threshold is chosen by cost on the v1
validation split (never on test), then applied once to the held-out test pairs.

    python -m ml.scripts.heldout_report \
        --model evaluation/results/fusion-model.json \
        --validation-features evaluation/results/v1-validation-features.jsonl \
        --test-features artifacts/bench/heldout-features.jsonl \
        --out evaluation/results/heldout-v2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run(*args: str) -> None:
    subprocess.run([sys.executable, "-m", *args], check=True, stdout=subprocess.DEVNULL)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--validation-features", type=Path, required=True)
    parser.add_argument("--test-features", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="output prefix, e.g. evaluation/results/heldout-v2")
    args = parser.parse_args()

    work = args.out.parent / (args.out.name + "-work")
    work.mkdir(parents=True, exist_ok=True)
    validation = [row for row in _read(args.validation_features) if row.get("split") == "validation"]
    test = [row for row in _read(args.test_features) if row.get("split") == "test"]
    combined = work / "features.jsonl"
    _write(combined, validation + test)

    reports: dict[str, dict] = {}
    _run("ml.scripts.score_fusion_model", str(args.model), str(combined), "--output", str(work / "fusion.jsonl"))
    for name in ("fusion", "dinov2", "vlm"):
        if name != "fusion":
            _run("ml.scripts.baseline_predictions", str(combined), "--signal", name, "--output", str(work / f"{name}.jsonl"))
        _run("ml.scripts.evaluate_real_predictions", str(work / f"{name}.jsonl"), "--json-output", str(work / f"{name}-report.json"))
        reports[name] = json.loads((work / f"{name}-report.json").read_text(encoding="utf-8"))

    labels = {"fusion": "Trained fusion (v1 threshold)", "dinov2": "DINOv2 similarity only", "vlm": "Qwen3-VL mismatch only"}
    summary = {
        "protocol": "item-disjoint held-out v2: thresholds chosen on v1 validation, test opened once",
        "test_cases": len(test),
        "test_genuine": sum(1 for row in test if row["target"] == 0),
        "test_substitution": sum(1 for row in test if row["target"] == 1),
        "methods": {
            name: {
                "label": labels[name],
                "threshold": report["threshold_selection"]["threshold"],
                **{key: report["test"][key] for key in ("precision", "recall", "f1", "average_precision", "confusion", "recapture_rate", "genuine_recapture_rate") if key in report["test"]},
                "intervals": report["test"].get("bootstrap_95_ci"),
                "slices": report["test"].get("slices"),
                "latency_ms": report["test"].get("latency_ms"),
            }
            for name, report in reports.items()
        },
    }
    args.out.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Held-out benchmark v2",
        "",
        f"{summary['test_cases']} item-disjoint test pairs ({summary['test_genuine']} genuine, {summary['test_substitution']} substitutions) "
        "from Amazon Berkeley Objects, none of whose products appear in the v1 train, validation or test sets. "
        "Each method's threshold was chosen by cost on the v1 validation split; the test pairs were scored once.",
        "",
        "| Method | Threshold | Precision | Recall | F1 | Avg. precision | TP | FP | TN | FN | Recapture |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("fusion", "vlm", "dinov2"):
        m = summary["methods"][name]
        c = m["confusion"]
        lines.append(
            f"| {m['label']} | {m['threshold']:.2f} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | "
            f"{m.get('average_precision', float('nan')):.3f} | {c['tp']} | {c['fp']} | {c['tn']} | {c['fn']} | "
            f"{m.get('recapture_rate', 0) * 100:.1f}% |"
        )
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
