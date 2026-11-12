# Held-out benchmark v2

304 item-disjoint test pairs (152 genuine, 152 substitutions) from Amazon Berkeley Objects, none of whose products appear in the v1 train, validation or test sets. Each method's threshold was chosen by cost on the v1 validation split; the test pairs were scored once.

| Method | Threshold | Precision | Recall | F1 | Avg. precision | TP | FP | TN | FN | Recapture |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Trained fusion (v1 threshold) | 0.92 | 0.984 | 0.789 | 0.876 | 0.976 | 120 | 2 | 150 | 32 | 53.3% |
| Qwen3-VL mismatch only | 0.90 | 0.986 | 0.908 | 0.945 | 0.982 | 138 | 2 | 150 | 14 | 53.3% |
| DINOv2 similarity only | 0.40 | 0.818 | 0.737 | 0.775 | 0.886 | 112 | 25 | 127 | 40 | 53.3% |
