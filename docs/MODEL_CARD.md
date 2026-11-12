# SwapShield fusion model card

## Intended use

SwapShield compares dispatch and return evidence for possible item substitution.
It recommends approve, recapture, or human review. It must not accuse a customer,
reject a refund automatically, block an account, or move money.

## Model

- Frozen DINOv2-small pair similarity.
- Quantized Qwen3-VL-4B structured visual observations.
- Class-balanced logistic fusion model.
- Five-fold out-of-fold Platt calibration.
- Validation-selected operating threshold of 0.03 (validation v2, 304 pairs; v1 used 0.92, kept in `fusion-model-v1.json`).
- Quality abstention below 0.46.

The portable JSON model is stored at `evaluation/results/fusion-model.json`.

An optional API-based LLM runs after this model as an evidence-consistency
auditor. It cannot change the score or recommendation and is not a component of
the measured classifier. Its output is schema-validated and replaced with a
deterministic fallback on any provider or validation failure.

## Data and split

The benchmark uses 120 Amazon Berkeley Objects identities across chair, sofa,
table, and lamp categories. Product identities were separated before pair
generation: 176 train pairs, 32 validation pairs, and 32 test pairs. Two test
pairs previously used for a hardware smoke test were disclosed and excluded,
leaving 30 locked test pairs.

The benchmark contains different-view genuine pairs and same-category product
substitutions. It does not establish production performance on merchant photos,
electronics, apparel, serial labels, empty boxes, damage, or missing accessories.

## Shipped setting: held-out result (v2)

The threshold was chosen by expected cost on 304 new validation pairs (₹80 per
review of a genuine return, ₹6,200 per missed substitution), then applied to 304
test pairs whose products appear nowhere in the training or validation data.

| Metric | At 0.03 (shipped) | At 0.92 (v1) |
|---|---:|---:|
| Precision | 0.727 | 0.984 |
| Recall | 0.980 | 0.789 |
| F1 | 0.835 (95% CI 0.790–0.879) | 0.876 (0.827–0.914) |
| Genuine returns sent to review (of 152) | 56 | 2 |
| Substitutions missed (of 152) | 3 | 32 |
| Expected cost on test | ₹23,080 | ₹1,98,560 |
| p50 / p95 latency | 9.6 s / 17.8 s | 9.6 s / 17.8 s |

Review means a person checks before any refund; it never refuses the buyer. The
test results were seen before the validation-v2 selection ran (disclosed in
docs/EVALUATION.md).

## Locked v1 test result (30 pairs, threshold 0.92)

| Metric | Result |
|---|---:|
| Precision | 1.000 |
| Recall | 0.867 |
| F1 | 0.929 |
| Average precision | 0.996 |
| Calibration error | 0.046 |
| False positives | 0 |
| False negatives | 2 |
| Recapture rate | 0.400 |
| Genuine recapture rate | 0.333 |
| p50 latency | 11.5 s |
| p95 latency | 65.4 s |

The 95% bootstrap intervals are precision [1.000, 1.000], recall [0.667,
1.000], and F1 [0.800, 1.000]. The wide ranges reflect the 30-pair test size.

## Known limitations

- At the shipped threshold, about a third of genuine returns (56 of 152) go to a
  person before the refund. This is the price of catching 98% of substitutions
  under the declared costs; a merchant can raise the threshold to trade misses
  for fewer reviews.
- 53% of v2 pairs fell under the photo-quality gate, which was set on phone
  photos and scores studio images low. It needs recalibrating on merchant photos.
- Sofas are the weakest v2 category.

- Both misses were lamps; lamp recall was 0.500 on eight cases.
- Forty percent of test pairs requested recapture, including one third of
  genuine pairs. This is safe but introduces operational friction.
- The p95 latency is too high for high-throughput synchronous operation.
- Identifier and weight coefficients are zero because the ABO visual benchmark
  contains no serial or weight variation. Those signals are implemented but not
  validated by this benchmark.
- The dataset is small and furniture-only. These metrics must not be presented
  as general merchant-return performance.
- The optional evidence-auditor API has not been included in these held-out
  precision, recall, calibration, or latency measurements.

## Financial assumptions

The threshold was selected on validation with ₹80 per false positive and ₹6,200
per missed substitution. The locked test produced ₹0 false-positive cost and
₹12,400 estimated missed-substitution loss.

## Safety and monitoring

Every adverse recommendation requires a human reviewer. Low-quality or invalid
model evidence causes recapture. Deployment monitoring should track precision,
recall, abstention, latency, category drift, unsupported-claim rate, and reviewer
overrides.
