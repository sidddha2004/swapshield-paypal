# Model selection on validation v2

Rule: lowest validation-v2 expected cost (FP 80, FN 6200); ties: higher F1, then simpler. 304 validation pairs, 304 test pairs.

| Method | Threshold | Validation cost (₹) | FP | FN | Validation F1 |
|---|---:|---:|---:|---:|---:|
| Trained fusion (chosen) | 0.03 | 4,800 | 60 | 0 | 0.835 |
| Qwen3-VL mismatch only | 0.05 | 8,480 | 106 | 0 | 0.741 |
| DINOv2 similarity only | 0.13 | 9,040 | 113 | 0 | 0.729 |

Chosen: **Trained fusion** at threshold 0.03. On the 304 test pairs: precision 0.727, recall 0.980, F1 0.835 (95% CI 0.790–0.879); FP 56, FN 3.

The v2 test results for all three methods were seen before this selection ran.
