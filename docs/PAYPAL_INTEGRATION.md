# PayPal integration

SwapShield connects its return-verification AI to PayPal's dispute process, so an
"item not as described" claim goes from dispute to resolution in one reviewed flow.

```
PayPal dispute ──webhook / sync──▶ case (Disputes v1 + Payments v2 capture)
        │
        ▼
Returned-item photos ──▶ DINOv2 + Qwen3-VL + calibrated fusion + evidence auditor
        │                         (existing SwapShield pipeline, unchanged)
        ▼
Resolution agent ──▶ proposal (refund / partial refund / submit evidence / ask for photos)
        │                 schema-validated, capped, cannot call PayPal
        ▼
Named reviewer confirms ──▶ one idempotent PayPal call ──▶ append-only audit log
```

## PayPal APIs used

| API | Endpoint | Used for |
|---|---|---|
| OAuth 2.0 | `POST /v1/oauth2/token` | Client-credentials token (cached) |
| Webhooks | `CUSTOMER.DISPUTE.CREATED / UPDATED / RESOLVED` | New and changed disputes become cases |
| Webhooks | `POST /v1/notifications/verify-webhook-signature` | Reject forged webhooks |
| Disputes v1 | `GET /v1/customer/disputes`, `GET /v1/customer/disputes/{id}` | Sync, dispute context, deadline, buyer message |
| Disputes v1 | `POST /v1/customer/disputes/{id}/provide-evidence` | Submit evidence notes + PDF report when items differ |
| Disputes v1 | `POST /v1/customer/disputes/{id}/send-message` | Ask the buyer for clearer photos |
| Payments v2 | `GET /v2/payments/captures/{id}` | Captured amount = refund ceiling |
| Payments v2 | `POST /v2/payments/captures/{id}/refund` | Full or partial refund, `PayPal-Request-Id` for idempotency |
| Orders v2 | `POST /v2/checkout/orders`, `.../capture` | Sandbox seed script only |

Code: [`service/paypal_client.py`](../service/paypal_client.py) (standard-library HTTP,
no SDK dependency). `MockPayPalClient` implements the same interface from
[`service/fixtures/paypal`](../service/fixtures/paypal). Those fixtures are synthetic,
shaped like sandbox responses; they are not recorded production data.

## What each verification outcome allows

| Pipeline decision | Actions the agent may propose and the reviewer may execute |
|---|---|
| `approve` (matches dispatch) | Full refund, partial refund |
| `review` (material differences) | Submit evidence to PayPal, full refund, partial refund |
| `recapture` (evidence too weak) | Ask buyer for clearer photos |

`service/resolution_agent.py::check_action` enforces this table, the refund cap and
the language rules. It runs when the agent drafts and again on execution.

## Running it

### Mock mode (no PayPal account, offline)

```bash
# .env: PAYPAL_MODE=mock
python -m uvicorn service.app:app --host 127.0.0.1 --port 8000
npm run dev
```

Open the **PayPal disputes** tab and click **Sync disputes**. Three fixture disputes
appear, including `PP-D-27811`, whose buyer message contains a prompt-injection attempt.

### Sandbox mode

1. Create a sandbox REST app at developer.paypal.com and copy its client ID and secret into `.env`
   (`PAYPAL_MODE=` empty, `PAYPAL_ENV=sandbox`).
2. `python -m scripts.paypal_sandbox_seed create`, then open each approval link as the
   sandbox **personal** account and approve.
3. `python -m scripts.paypal_sandbox_seed capture <ORDER_ID>`
4. As the sandbox buyer on sandbox.paypal.com, open the Resolution Center and report
   "item not as described" for the payment.
5. Click **Sync disputes** in the dashboard. For webhooks, expose the API with a tunnel
   (for example `cloudflared tunnel --url http://127.0.0.1:8000`), subscribe the app's
   webhook to `CUSTOMER.DISPUTE.*` at `https://<tunnel>/v1/paypal/webhook`, and set
   `PAYPAL_WEBHOOK_ID`.
6. Verify, draft, confirm. The refund, message or evidence appears in the sandbox
   merchant account.

Sandbox disputes may need time, or the sandbox-only `require-evidence` action, before
they reach `WAITING_FOR_SELLER_RESPONSE`, which is the state where evidence can be submitted.

## Tests

```bash
python -m unittest discover -s service/tests -t .
```

The tests cover webhook signature rejection, idempotent ingest, confirmation and
verification requirements, refund cap, double-execute deduplication, decision-to-action
gating, accusatory-language blocking, the injection fixture, agent schema validation, and
LLM failure fallback.
