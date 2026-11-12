"""Choose the shipped decision method on validation v2, by the rule in docs/EVALUATION.md.

For each candidate, pick the cost-optimal threshold on validation v2; ship the
lowest-cost method (ties: higher validation F1, then the simpler method). Then
report the chosen method on the v2 test pairs with that threshold.

    python -m ml.scripts.select_method \
        --model evaluation/results/fusion-model.json \
        --validation-features artifacts/bench/validation-v2-features.jsonl \
        --test-features artifacts/bench/heldout-features.jsonl \
        --out evaluation/results/selection-v2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ml.swapshield_ml.real_evaluation import evaluate_real_predictions, load_real_predictions, select_cost_threshold

FP_COST, FN_COST = 80.0, 6200.0
# simplest first: used only to break exact ties on cost and F1
SIMPLICITY = {"vlm": 0, "dinov2": 1, "fusion": 2}
LABELS = {"fusion": "Trained fusion", "vlm": "Qwen3-VL mismatch only", "dinov2": "DINOv2 similarity only"}


def _run(*args: str) -> None:
    subprocess.run([sys.executable, "-m", *args], check=True, stdout=subprocess.DEVNULL)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _f1(confusion: dict[str, int]) -> float:
    tp, fp, fn = confusion["tp"], confusion["fp"], confusion["fn"]
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--validation-features", type=Path, required=True)
    parser.add_argument("--test-features", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    work = args.out.parent / (args.out.name + "-work")
    work.mkdir(parents=True, exist_ok=True)
    validation = [row for row in _read(args.validation_features) if row.get("split") == "validation"]
    test = [row for row in _read(args.test_features) if row.get("split") == "test"]
    combined = work / "features.jsonl"
    _write(combined, validation + test)

    _run("ml.scripts.score_fusion_model", str(args.model), str(combined), "--output", str(work / "fusion.jsonl"))
    for signal in ("vlm", "dinov2"):
        _run("ml.scripts.baseline_predictions", str(combined), "--signal", signal, "--output", str(work / f"{signal}.jsonl"))

    candidates = {}
    for name in ("fusion", "vlm", "dinov2"):
        predictions = load_real_predictions(work / f"{name}.jsonl")
        val = [item for item in predictions if item.split == "validation"]
        selection = select_cost_threshold(val, false_positive_cost=FP_COST, false_negative_cost=FN_COST)
        confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        for item in val:
            predicted = item.probability >= selection["threshold"]
            key = ("tp" if item.target else "fp") if predicted else ("fn" if item.target else "tn")
            confusion[key] += 1
        candidates[name] = {
            "label": LABELS[name],
            "validation_threshold": selection["threshold"],
            "validation_cost": selection["expected_cost"],
            "validation_confusion": confusion,
            "validation_f1": _f1(confusion),
        }

    chosen = min(candidates, key=lambda name: (candidates[name]["validation_cost"], -candidates[name]["validation_f1"], SIMPLICITY[name]))
    report = evaluate_real_predictions(load_real_predictions(work / f"{chosen}.jsonl"),
                                       false_positive_cost=FP_COST, false_negative_cost=FN_COST)
    test_result = report["test"]
    summary = {
        "rule": "lowest validation-v2 expected cost (FP 80, FN 6200); ties: higher F1, then simpler",
        "validation_cases": len(validation),
        "test_cases": len(test),
        "candidates": candidates,
        "chosen": chosen,
        "chosen_threshold": report["threshold_selection"]["threshold"],
        "test": {key: test_result[key] for key in ("precision", "recall", "f1", "average_precision", "confusion",
                                                    "bootstrap_95_ci", "recapture_rate", "genuine_recapture_rate", "slices")},
        "disclosure": "The v2 test results for all three methods were seen before this selection ran.",
    }
    args.out.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    lines = ["# Model selection on validation v2", "",
             f"Rule: {summary['rule']}. {len(validation)} validation pairs, {len(test)} test pairs.", "",
             "| Method | Threshold | Validation cost (₹) | FP | FN | Validation F1 |", "|---|---:|---:|---:|---:|---:|"]
    for name, item in sorted(candidates.items(), key=lambda pair: pair[1]["validation_cost"]):
        c = item["validation_confusion"]
        lines.append(f"| {item['label']}{' (chosen)' if name == chosen else ''} | {item['validation_threshold']:.2f} | "
                     f"{item['validation_cost']:,.0f} | {c['fp']} | {c['fn']} | {item['validation_f1']:.3f} |")
    t, ci = summary["test"], summary["test"]["bootstrap_95_ci"]
    lines += ["", f"Chosen: **{LABELS[chosen]}** at threshold {summary['chosen_threshold']:.2f}. On the 304 test pairs: "
              f"precision {t['precision']:.3f}, recall {t['recall']:.3f}, F1 {t['f1']:.3f} "
              f"(95% CI {ci['f1'][0]:.3f}–{ci['f1'][1]:.3f}); FP {t['confusion']['fp']}, FN {t['confusion']['fn']}.",
              "", summary["disclosure"]]
    args.out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
