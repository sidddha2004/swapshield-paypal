# SwapShield in Postman

Try the PayPal dispute workflow from Postman, without the dashboard.

## Files

| File | What it is |
|---|---|
| `SwapShield.postman_collection.json` | The collection: a dispute walkthrough and the safety checks, each with tests |
| `SwapShield-local.postman_environment.json` | `baseUrl` and the reviewer name |
| `openapi.json` | The full API description, exported from FastAPI |

Regenerate all three after an API change:

```bash
python scripts/build_postman.py
```

## Run it

1. Start the API with mock PayPal (no PayPal account needed). Step 5 runs the real vision models, so this needs the GPU environment:

   ```bash
   PAYPAL_MODE=mock python -m uvicorn service.app:app --host 127.0.0.1 --port 8000
   ```

2. In Postman, choose **Import** and select the collection and the environment, then pick the **SwapShield local (mock PayPal)** environment.
3. Open **Dispute walkthrough** and run the requests in order, or use **Run collection** to run everything:

   | Step | What happens |
   |---|---|
   | 1. Health | Models, auditor, resolution agent and PayPal mode |
   | 2. Sync disputes from PayPal | Disputes become cases (Disputes v1 API) |
   | 3. List cases | Queue sorted by PayPal's response deadline |
   | 4. Open the case | Capture, amounts, deadline, buyer message, action log |
   | 5. Verify the returned item | DINOv2 + Qwen3-VL + calibrated fusion + auditor, plus the evidence PDF |
   | 6. Draft the resolution | The agent proposes; it cannot call PayPal |
   | 7. Confirm and execute on PayPal | One idempotent refund, provide-evidence or send-message call |
   | 8. Download the evidence report | The PDF that PayPal receives |
   | 9. Confirm again | Returns the logged result: no double refund |

4. Then run **Safety checks**: an unconfirmed action, a $9,999 refund on a $149 dispute (the buyer message tries a prompt injection), accusatory wording, a forged webhook and a signed one.

From the command line, with the same API running:

```bash
npx newman run postman/SwapShield.postman_collection.json --working-dir .
```

All 29 assertions pass against mock PayPal.

## Against the PayPal sandbox

Set `PAYPAL_MODE=` (empty) and the sandbox credentials in `.env`, as described in [docs/PAYPAL_INTEGRATION.md](../docs/PAYPAL_INTEGRATION.md). The walkthrough expects the fixture dispute `PP-D-27803` and falls back to the first case in the queue. The webhook requests in **Safety checks** use the mock signature header, so in sandbox mode the signed one is rejected, by design.
