# Zapier alerts

SwapShield can tell your team when a PayPal dispute needs them: a new dispute, a
resolution waiting for a reviewer, a PayPal action sent or rejected, and a
response deadline coming up. Events go to a Zapier webhook, and Zapier routes
them to Slack, email, Microsoft Teams, a Google Sheet or anything else it supports.

Alerts are optional and best effort. If Zapier is not configured or unreachable,
the dispute workflow carries on unchanged. The buyer's own message is never
sent, because it is untrusted text.

## Events

| `event` | When | Example `summary` |
|---|---|---|
| `dispute.received` | A new dispute arrives (webhook or sync) | New PayPal dispute: Walnut side table, $149.00, 41 h left to respond. Upload the dispatch and return photos to verify it. |
| `case.verified` | The returned item has been verified | Returned item verified: RGB wired gaming mouse, $39.99, 7 days left… Matches the dispatch record (score 2). |
| `resolution.ready` | The agent drafted a proposal | Resolution ready for your confirmation: … Proposed: refund full. A named reviewer must confirm. |
| `paypal.action_sent` | A reviewer confirmed and PayPal accepted | Sent to PayPal: … refund full confirmed by Priya. |
| `paypal.action_failed` | PayPal rejected the action | PayPal rejected an action: … submit evidence: PayPal returned HTTP 422 … |
| `deadline.approaching` | A case enters the 48 h, 24 h or 6 h window | PayPal response deadline approaching: Walnut side table, $149.00, 35 h left to respond. Within the 48 h window. |

Every payload is flat JSON, so Zapier fields map directly:
`event, summary, case_id, dispute_id, item, amount, currency, hours_left,
seller_response_due, workflow_status, detail, dashboard_url, occurred_at`.

## Set it up (about 10 minutes)

1. **Catch the events.** In Zapier, create a Zap with the trigger **Webhooks by
   Zapier → Catch Hook** and copy its URL.
2. **Point SwapShield at it** in `.env`, then restart the API:

   ```
   ZAPIER_WEBHOOK_URL=https://hooks.zapier.com/hooks/catch/...
   SWAPSHIELD_DASHBOARD_URL=http://127.0.0.1:5173
   ```

   `GET /health` then reports `"alerts": {"zapier": true}`.
3. **Send a test event.** Click **Sync disputes** in the dashboard (or run step 2
   of the Postman collection). Each new dispute sends `dispute.received`, which
   Zapier uses as the sample.
4. **Add the action**, for example **Slack → Send Channel Message**, with the
   message set to `summary` and a link to `dashboard_url`. A **Filter** step on
   `event` lets you send only deadlines and failures to a busy channel.
5. **Deadline checks.** Deadlines need a schedule. Create a second Zap:
   **Schedule by Zapier → Every Hour**, then **Webhooks by Zapier → POST** to
   `https://<your-api>/v1/alerts/deadlines`. The API must be reachable from
   Zapier: a tunnel such as `cloudflared tunnel --url http://127.0.0.1:8000`,
   or a hosted deployment. A local cron job calling the same URL also works.

   Each open case alerts once per window (48 h, 24 h, 6 h). Calling the endpoint
   every hour does not repeat an alert, and resolved cases are skipped.
   `POST /v1/alerts/deadlines?dry_run=true` shows what would be sent without
   sending or recording anything.

## Code

- `service/notify.py`: payloads, delivery, deadline windows.
- `service/app.py`: events at each workflow step, plus `POST /v1/alerts/deadlines`.
- `service/tests/test_paypal_workflow.py` (`AlertTests`): one alert per new
  dispute, no buyer text in any event, one deadline alert per window, none for
  resolved cases, and dry runs that record nothing.
