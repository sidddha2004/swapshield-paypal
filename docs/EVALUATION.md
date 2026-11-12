# Evaluation contract

SwapShield's positive class is **return substitution**: the returned item is
not the item or variant recorded at dispatch. An empty parcel is treated as the
null-item endpoint of the same class. Damage-only and customer-intent claims
are outside scope.

## Split policy

1. Product identities are assigned to train, validation, and test before pair generation.
2. Multiple views of one identity stay in the same split.
3. Hard negatives are different identities from the same product category.
4. Test labels are not used for feature selection, threshold selection, or calibration.
5. Seeded synthetic results are reported separately from real-image results.

## Required reporting

- Precision, recall, F1, PR-AUC and confusion matrix.
- Calibration error and a reliability diagram.
- Bootstrap 95% confidence intervals.
- Category, blur, occlusion, viewpoint and serial-number slices.
- False-positive review/friction cost and false-negative merchandise loss.
- DINOv2-only, VLM-only, objective-signals-only, and fused-model ablations.
- p50 and p95 latency on the declared hardware.

The operating threshold is selected on validation data using declared merchant
cost assumptions. Accuracy is not a headline metric.

## Implemented real-image protocol

The manifest validator rejects duplicate cases, label/identity contradictions,
unsafe paths, missing files, unsupported labels, and any product identity that
appears in more than one split. Source and licence are mandatory for every pair.

The ABO subset builder assigns identities to splits before it creates pairs,
then makes one different-angle genuine pair and one same-category substitution
pair per selected identity. The default seed is 5050 and the exact source views
are recorded in `dataset_summary.json`. No SKU, serial, or weight shortcut is
included in this vision-only benchmark.

The GPU extractor writes one prediction at a time and supports `--resume`, so a
long local run remains recoverable. The evaluator selects its threshold on
validation only, then reports the untouched test split with:

- Precision, recall, F1, average precision, and confusion matrix.
- Expected calibration error and deterministic bootstrap 95% intervals.
- False-positive review cost and missed-substitution merchandise loss.
- Recapture rate and genuine-return recapture friction.
- p50/p95 end-to-end latency and category/corruption slices.

Synthetic POC metrics and real-image reports are stored and presented
separately. The six-case manifest template validates the workflow only and must
never be presented as the final benchmark. The generated ABO test report also
needs to be labelled as an ABO visual benchmark, not evidence of production
performance on merchant returns.

## Fusion training protocol

The first trained baseline uses five bounded signals: DINOv2 dissimilarity,
Qwen3-VL mismatch, identifier mismatch, clipped weight deviation, and image
quality deficit. For the ABO visual-only benchmark, identifier and weight
signals remain zero by design.

The classifier is a class-balanced logistic regression. Standardization and
base-model fitting use only train. Platt calibration is fitted on deterministic
five-fold out-of-fold train scores. The financial operating threshold is then
selected on validation. The final scaler, coefficients, calibration parameters,
cost assumptions, quality-abstention threshold, and decision threshold are
stored in a portable JSON artifact; no unsafe pickle is required.

Two test cases (`ABO-TES-B07124WCZY-G` and `ABO-TES-B07124WCZY-S`) were executed
as hardware smoke tests before model fitting. They are explicitly excluded from
the final test report. No model parameter or threshold may be changed after the
remaining test predictions are opened.

## Frozen v1 result

The final 30-pair test produced precision 1.000, recall 0.867, F1 0.929,
average precision 0.996, and calibration error 0.046 at the validation-selected
0.92 threshold. The confusion matrix is TP=13, FP=0, TN=15, FN=2. Both misses
were lamps, giving the eight-case lamp slice 0.500 recall. Recapture was requested
for 40% of all test pairs and 33.3% of genuine pairs. End-to-end RTX 5050 latency
was 11.5 seconds p50 and 65.4 seconds p95.

The report is frozen in `evaluation/results/real-report.json`; known limitations
and intended use are documented in `docs/MODEL_CARD.md`.

## Held-out benchmark v2 (304 unseen pairs)

The v1 test has 30 pairs, too few to separate the methods. v2 scores the
**unchanged** shipped model on 304 new pairs.

**Protocol**

- `ml.scripts.build_abo_heldout` selects 1,000 ABO products with the v1 seed and
  split rule and downloads only the test split: 152 products, 304 pairs (152
  genuine, 152 same-category substitutions).
- `ml.scripts.make_heldout_manifest` checks every product ID against the v1
  manifest (train, validation and test): **0 overlaps**. It interleaves
  categories and labels so any prefix of the run stays balanced.
- The fusion model, its calibration and the quality gate stay frozen. Every
  method's threshold is chosen by cost (₹80 per false positive, ₹6,200 per
  miss) on the **v1 validation split**, then applied once to the 304 pairs
  (`ml.scripts.heldout_report`).
- Baselines use the same features and the same evaluator: DINOv2 similarity
  alone, and the Qwen3-VL mismatch score alone.

**Result** (95% bootstrap intervals; `evaluation/results/heldout-v2.json`)

| Method | Threshold | Precision | Recall | F1 (95% CI) | Avg. precision | TP | FP | TN | FN |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| Trained fusion (v1 threshold) | 0.92 | 0.984 | 0.789 | **0.876** (0.827–0.914) | 0.976 | 120 | 2 | 150 | 32 |
| Qwen3-VL mismatch only | 0.90 | 0.986 | 0.908 | **0.945** (0.917–0.971) | 0.982 | 138 | 2 | 150 | 14 |
| DINOv2 similarity only | 0.40 | 0.818 | 0.737 | 0.775 (0.719–0.829) | 0.886 | 112 | 25 | 127 | 40 |

Recall by category for the shipped model: chair 0.84, lamp 0.84, table 0.82,
sofa 0.66. Latency per case: 9.6 s median, 17.8 s p95 on an RTX 5050 laptop GPU.

**What it shows**

1. **Genuine customers are rarely flagged.** The shipped model flagged 2 of 152
   genuine returns (precision 0.984), which matches the v1 result.
2. **The fusion model is not the best method.** The VLM score alone catches
   more substitutions (recall 0.908 against 0.789), and the two F1 intervals do
   not overlap. On the v1 test the same pattern appeared (VLM alone 1.000, fusion
   0.929 on the same 30 pairs) but was within noise. The fusion gives weight to
   DINOv2 similarity, which is the weakest signal (F1 0.775) and the source of
   most false flags when used alone.
3. **The photo-quality gate is too strict for these images.** 53% of pairs (52%
   of genuine ones) fall below the 0.46 quality threshold and would be asked for
   clearer photos. The gate was set on phone photos; ABO's studio views score
   low on its sharpness measure. It needs recalibrating on real merchant photos.

The shipped model was **not** changed after these results. Choosing between
fusion and VLM-only has to be done on validation data, with a fresh test set to
confirm it.

Reproduce:

```bash
python -m ml.scripts.build_abo_heldout --output data/real/abo/heldout-v2 --items 1000
python -m ml.scripts.make_heldout_manifest data/real/abo/heldout-v2/manifest.jsonl data/real/abo/manifest.jsonl --output data/real/abo/heldout-v2/heldout-test.jsonl
python -m ml.scripts.extract_real_features data/real/abo/heldout-v2/heldout-test.jsonl artifacts/bench/heldout-features.jsonl --split test --allow-test-only --resume
python -m ml.scripts.heldout_report --model evaluation/results/fusion-model.json --validation-features evaluation/results/v1-validation-features.jsonl --test-features artifacts/bench/heldout-features.jsonl --out evaluation/results/heldout-v2
```

## Model selection on validation v2 (rule fixed before running)

The v2 test showed the VLM score alone beating the shipped fusion. Switching
on that evidence would be choosing a model on the test set. Instead, a new
validation set picks the method, under this rule, written down before any
validation result existed:

- **Data:** the validation split of the same 1,000-product ABO selection:
  304 pairs, 0 product overlap with the v1 data or the v2 test.
- **Candidates:** trained fusion (frozen), Qwen3-VL mismatch alone, DINOv2
  similarity alone.
- **Rule:** for each method, choose the threshold with the lowest expected cost
  on validation v2 (₹80 per false flag, ₹6,200 per missed substitution). Ship
  the method with the lowest cost. Ties go to the higher validation F1, then to
  the simpler method.
- **Report:** the chosen method's threshold is applied to the v2 test pairs
  and reported, with the disclosure that those test results were already seen
  when the selection ran.

### Result

Ran on 304 validation pairs ("lowest validation-v2 expected cost (FP 80, FN 6200); ties: higher F1, then simpler"):

| Method | Threshold | Validation cost (₹) | FP | FN | Validation F1 |
|---|---:|---:|---:|---:|---:|
| **Trained fusion (chosen)** | 0.03 | 4,800 | 60 | 0 | 0.835 |
| Qwen3-VL mismatch only | 0.05 | 8,480 | 106 | 0 | 0.741 |
| DINOv2 similarity only | 0.13 | 9,040 | 113 | 0 | 0.729 |

The trained fusion is kept, and its threshold moves from 0.92 (chosen on the
32-pair v1 validation set) to **0.03**. On the larger set,
the declared costs (a missed substitution costs 77 times a false flag) favour
catching nearly every swap.

On the 304 v2 test pairs at 0.03: precision 0.727,
recall 0.980, F1 0.835 (95% CI 0.790–0.879);
56 of 152 genuine returns go to review, 3 of 152
substitutions are missed. Expected cost on test: ₹23,080, against ₹1,98,560 at
the v1 threshold and ₹86,960 for the VLM score alone at its v1 threshold. For
transparency, at their own validation-v2 thresholds the single-signal methods
reach a lower test cost (VLM ₹9,120, DINOv2 ₹8,880) by sending about 75% of
genuine returns to review; the rule chose on validation, not on these numbers.

"Review" in SwapShield means a person looks before anything happens: the
reviewer can still refund in full. It never refuses the buyer. A merchant who
prefers fewer reviews can raise the threshold in
`evaluation/results/fusion-model.json`; the v1 model is kept unchanged in
`fusion-model-v1.json`.

The v2 test results for all three methods were seen before this selection ran.
