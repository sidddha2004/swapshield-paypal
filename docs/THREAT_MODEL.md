# Defense-only threat model

## Protected asset

Merchant margin lost when a returned parcel contains a substituted, wrong, or
missing item and the refund is issued as though the original product arrived.

## In scope

- Wrong SKU or variant.
- Different serial-numbered unit.
- Visually similar cheaper substitute.
- Missing product or materially incomplete return.
- Unreliable evidence that requires recapture.

## Out of scope

- Inferring customer intent.
- Identifying how to bypass return controls.
- Customer profiling beyond the submitted order evidence.
- Automatic refund rejection, account blocking, or dispute submission.
- Damage assessment and counterfeit certification in the first release.

## Safety boundary

SwapShield produces evidence and a bounded recommendation. Uncertainty routes
to recapture or human review. It never labels a person as fraudulent.

The optional external LLM auditor is advisory-only and executes after the
calibrated recommendation is fixed. Uploaded/OCR strings remain untrusted data;
the auditor sees only a compact evidence payload, must cite known evidence IDs,
and cannot add actions or alter the score. Provider errors, invalid JSON,
unsupported citations, or accusatory language activate a deterministic fallback.
The API endpoint is operator-configured, and its secret remains server-side.

## PayPal resolution workflow

SwapShield can now act on PayPal disputes, so money can move. The controls are
built into the code, not just the prompt:

| Threat | Control |
|---|---|
| Forged webhook creates or changes cases | Every webhook is checked with PayPal's `verify-webhook-signature` against `PAYPAL_WEBHOOK_ID`; failures return 401 and nothing is stored. Cases are always re-fetched from the Disputes API rather than trusted from the webhook body. |
| Replayed webhook or repeated sync | Cases are keyed by dispute ID; ingest is an upsert. |
| Double refund (double click, retry, replay) | Each execution uses a deterministic `PayPal-Request-Id` (`swapshield-<case>-<action>`), so PayPal deduplicates. SwapShield also returns the logged result instead of calling again, and a resolved case refuses further actions. |
| Prompt injection via the buyer's dispute message | The buyer text is passed to the agent as quoted, untrusted data. The agent's output is schema-validated, and `check_action` enforces the allowed actions per verification decision and the refund cap (≤ captured amount) at draft time **and again** at execution. The bundled fixture `PP-D-27811` contains an injection attempt and is covered by a test. |
| Agent acting on its own | The resolution agent has no reference to the PayPal client. Only `POST /v1/cases/{id}/resolution/execute` calls PayPal, and only with `confirm: true` plus a named reviewer, which is written to the append-only action log. |
| Adverse outcome without human or PayPal review | SwapShield never denies a claim. When evidence differs, the only adverse-leaning action is submitting the evidence report to PayPal, which adjudicates. Accusatory language is blocked in every outgoing text. |
| Credential leakage | PayPal credentials stay in the FastAPI process environment. The browser only sees case data. The seed script refuses to run unless `PAYPAL_ENV=sandbox`. |
