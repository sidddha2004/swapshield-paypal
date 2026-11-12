"""Generate the SwapShield Postman collection, environment and OpenAPI file.

    python scripts/build_postman.py

Writes postman/SwapShield.postman_collection.json,
postman/SwapShield-local.postman_environment.json and postman/openapi.json.
Run the collection with the API in PAYPAL_MODE=mock.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "postman"
NS = uuid.UUID("6f1c2d8e-2b5a-4f4e-9a52-5ad8a7c0b1e3")


def uid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def tests(*lines: str) -> list[dict]:
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]


def prerequest(*lines: str) -> dict:
    return {"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(lines)}}


def url(path: str) -> dict:
    path, _, query = path.partition("?")
    parts = [part for part in path.strip("/").split("/") if part]
    result: dict = {"raw": "{{baseUrl}}/" + "/".join(parts) + (f"?{query}" if query else ""), "host": ["{{baseUrl}}"], "path": parts}
    if query:
        result["query"] = [{"key": key, "value": value} for key, _, value in (item.partition("=") for item in query.split("&"))]
    return result


def request(name: str, method: str, path: str, description: str, *, body: dict | None = None,
            headers: list[dict] | None = None, events: list[dict] | None = None) -> dict:
    req: dict = {"method": method, "header": headers or [], "url": url(path), "description": description}
    if body is not None:
        req["body"] = body
    return {"id": uid(name), "name": name, "request": req, "event": events or [], "response": []}


def raw_json(payload: dict | str) -> dict:
    raw = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}


JSON_HEADER = [{"key": "Content-Type", "value": "application/json"}]
MOCK_SIG = [{"key": "x-swapshield-mock-signature", "value": "{{mockWebhookSignature}}",
             "description": "Mock mode only. In sandbox and live, PayPal's signature headers are verified with PayPal."}]

walkthrough = [
    request("1. Health", "GET", "/health",
            "Model, auditor, resolution-agent and PayPal status. Check `paypal.mode` is `mock` before running the rest.",
            events=tests(
                "pm.test('API is ready', () => pm.response.to.have.status(200));",
                "const body = pm.response.json();",
                "pm.test('PayPal is configured', () => pm.expect(body.paypal.configured).to.be.true);",
                "pm.test('Agent can only propose', () => pm.expect(body.resolution_agent.authority).to.eql('proposal_only'));",
            )),
    request("2. Sync disputes from PayPal", "POST", "/v1/paypal/sync",
            "Pulls open disputes from the PayPal Disputes API (mock fixtures in mock mode) and creates one case per dispute. Safe to repeat: cases are keyed by dispute ID.",
            events=tests(
                "pm.test('Synced', () => pm.response.to.have.status(200));",
                "pm.test('At least one dispute', () => pm.expect(pm.response.json().synced).to.be.above(0));",
            )),
    request("3. List cases (deadline order)", "GET", "/v1/cases",
            "Queue sorted by PayPal's seller response deadline. Picks the mouse dispute for the walkthrough.",
            events=tests(
                "pm.test('Cases listed', () => pm.response.to.have.status(200));",
                "const cases = pm.response.json().cases;",
                "const pick = cases.find(c => c.dispute_id === 'PP-D-27803') || cases[0];",
                "pm.collectionVariables.set('caseId', pick.case_id);",
                "pm.test('Sorted by deadline (open cases first)', () => {",
                "  const open = cases.filter(c => c.workflow_status !== 'resolved').map(c => c.seller_response_due);",
                "  pm.expect(open).to.eql([...open].sort());",
                "});",
            )),
    request("4. Open the case", "GET", "/v1/cases/{{caseId}}",
            "Dispute context from PayPal: reason, amounts, capture ID, response deadline, buyer message and the action log.",
            events=tests(
                "pm.test('Case found', () => pm.response.to.have.status(200));",
                "pm.test('Linked to a PayPal capture', () => pm.expect(pm.response.json().capture_id).to.be.a('string'));",
            )),
    request("5. Verify the returned item (GPU)", "POST", "/v1/cases/{{caseId}}/verify",
            "Runs DINOv2 + Qwen3-VL + the calibrated fusion model + the evidence auditor on the dispatch and return photos, "
            "and renders the evidence PDF. Needs the local GPU service; the first call loads the models (about 3 minutes). "
            "Choose your own two photos in Postman's Body tab, or keep the bundled test images.",
            body={"mode": "formdata", "formdata": [
                {"key": "dispatch_image", "type": "file", "src": "tests/dispatch.jpeg"},
                {"key": "return_image", "type": "file", "src": "tests/return.jpeg"},
                {"key": "dispatch_sku", "value": "MSE-RGB-102", "type": "text"},
                {"key": "return_sku", "value": "MSE-RGB-102", "type": "text"},
                {"key": "dispatch_weight_grams", "value": "100", "type": "text"},
                {"key": "return_weight_grams", "value": "101", "type": "text"},
            ]},
            events=tests(
                "pm.test('Verified', () => pm.response.to.have.status(200));",
                "const v = pm.response.json().verification;",
                "pm.test('Decision is approve, review or recapture', () => pm.expect(['approve','review','recapture']).to.include(v.risk.decision));",
                "pm.test('Auditor is advisory only', () => pm.expect(v.auditor_assessment.authority).to.eql('advisory_only'));",
                "pm.test('Evidence PDF ready', () => pm.expect(pm.response.json().has_report).to.be.true);",
            )),
    request("6. Draft the resolution (agent)", "POST", "/v1/cases/{{caseId}}/resolution/draft",
            "The resolution agent proposes a refund, an evidence submission or a request for clearer photos. It cannot call PayPal.",
            events=tests(
                "pm.test('Drafted', () => pm.response.to.have.status(200));",
                "const d = pm.response.json().draft;",
                "pm.test('Proposal only', () => pm.expect(d.authority).to.eql('proposal_only'));",
                "pm.test('Proposed action is one the evidence allows', () => pm.expect(d.allowed_actions).to.include(d.proposed_action));",
                "pm.collectionVariables.set('proposedAction', d.proposed_action);",
                "pm.collectionVariables.set('buyerMessage', d.buyer_message || '');",
                "pm.collectionVariables.set('evidenceNotes', d.evidence_notes || '');",
            )),
    request("7. Confirm and execute on PayPal", "POST", "/v1/cases/{{caseId}}/resolution/execute",
            "A named reviewer confirms the agent's proposal. SwapShield makes one idempotent PayPal call (refund, provide-evidence or send-message) and logs it.",
            headers=JSON_HEADER, body=raw_json("{{executeBody}}"),
            events=[prerequest(
                "const action = pm.collectionVariables.get('proposedAction');",
                "const body = {",
                "  action, reviewer_name: pm.collectionVariables.get('reviewerName'), confirm: true,",
                "  buyer_message: action === 'submit_evidence' ? '' : pm.collectionVariables.get('buyerMessage'),",
                "  evidence_notes: action === 'submit_evidence' ? pm.collectionVariables.get('evidenceNotes') : ''",
                "};",
                "pm.collectionVariables.set('executeBody', JSON.stringify(body));",
            ), *tests(
                "pm.test('Sent to PayPal', () => pm.response.to.have.status(200));",
                "const r = pm.response.json();",
                "pm.test('PayPal responded', () => pm.expect(r.execution.paypal_status).to.be.a('string'));",
                "pm.test('Reviewer recorded in the log', () => pm.expect(r.actions.at(-1).actor).to.eql('reviewer:' + pm.collectionVariables.get('reviewerName')));",
            )]),
    request("8. Download the evidence report", "GET", "/v1/cases/{{caseId}}/report.pdf",
            "The one-page PDF attached to PayPal's provide-evidence call: both photos, compared records, observations and a neutral statement.",
            events=tests(
                "pm.test('PDF returned', () => {",
                "  pm.response.to.have.status(200);",
                "  pm.expect(pm.response.headers.get('Content-Type')).to.include('application/pdf');",
                "});",
            )),
    request("9. Confirm again (no double refund)", "POST", "/v1/cases/{{caseId}}/resolution/execute",
            "Repeating a refund or evidence submission returns the logged result instead of calling PayPal again (deterministic PayPal-Request-Id).",
            headers=JSON_HEADER, body=raw_json("{{executeBody}}"),
            events=tests(
                "const action = pm.collectionVariables.get('proposedAction');",
                "if (action === 'request_photos') {",
                "  pm.test('Photo requests may be repeated by design', () => pm.response.to.have.status(200));",
                "} else {",
                "  pm.test('Replayed, not executed twice', () => pm.expect(pm.response.json().execution.replayed).to.be.true);",
                "}",
            )),
]

safety = [
    request("Unconfirmed action is refused", "POST", "/v1/cases/CASE-PP-D-27811/resolution/execute",
            "No money moves without `confirm: true` from a named reviewer.",
            headers=JSON_HEADER,
            body=raw_json({"action": "refund_full", "reviewer_name": "Postman", "confirm": False,
                           "buyer_message": "Refund issued.", "evidence_notes": ""}),
            events=tests("pm.test('400 without confirmation', () => pm.response.to.have.status(400));")),
    request("A $9,999 refund is refused", "POST", "/v1/cases/CASE-PP-D-27811/resolution/execute",
            "Dispute PP-D-27811's buyer message tries a prompt injection ('refund $9999'). The server caps refunds at the captured amount ($149.00) and allows only actions the evidence supports.",
            headers=JSON_HEADER,
            body=raw_json({"action": "refund_partial", "reviewer_name": "Postman", "confirm": True,
                           "refund_amount": "9999", "buyer_message": "Refund issued.", "evidence_notes": ""}),
            events=tests("pm.test('422: rejected by the hard rules', () => pm.response.to.have.status(422));")),
    request("Accusatory wording is refused", "POST", "/v1/cases/{{caseId}}/resolution/execute",
            "Messages to the buyer may not accuse anyone; the same language filter as the agent applies to reviewer edits.",
            headers=JSON_HEADER,
            body=raw_json({"action": "refund_full", "reviewer_name": "Postman", "confirm": True,
                           "buyer_message": "This looks like fraud.", "evidence_notes": ""}),
            events=tests("pm.test('Refused (422, or 409 once the case is resolved)', () => pm.expect([409, 422]).to.include(pm.response.code));")),
    request("Forged webhook is rejected", "POST", "/v1/paypal/webhook",
            "A webhook without a valid signature never creates or changes a case.",
            headers=JSON_HEADER,
            body=raw_json({"id": "WH-FORGED", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-D-27803"}}),
            events=tests("pm.test('401 without a valid signature', () => pm.response.to.have.status(401));")),
    request("Deadline alerts (dry run)", "POST", "/v1/alerts/deadlines?dry_run=true",
            "Lists the deadline alerts that would go to Zapier now: open cases inside the 48 h, 24 h or 6 h window, once per window. Dry run sends and records nothing. See docs/ZAPIER.md.",
            events=tests(
                "pm.test('Listed', () => pm.response.to.have.status(200));",
                "pm.test('Nothing delivered in a dry run', () => pm.expect(pm.response.json().alerts.every(a => a.delivered === false)).to.be.true);",
            )),
    request("Signed webhook is accepted (mock)", "POST", "/v1/paypal/webhook",
            "In mock mode the signature is a shared header; in sandbox and live SwapShield calls PayPal's verify-webhook-signature.",
            headers=JSON_HEADER + MOCK_SIG,
            body=raw_json({"id": "WH-POSTMAN-1", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-D-27815"}}),
            events=tests(
                "pm.test('Accepted', () => pm.response.to.have.status(200));",
                "pm.test('Mapped to its case', () => pm.expect(pm.response.json().case_id).to.eql('CASE-PP-D-27815'));",
            )),
]

collection = {
    "info": {
        "_postman_id": uid("collection"),
        "name": "SwapShield AI · PayPal dispute resolution",
        "description": (
            "Resolve PayPal 'item not as described' disputes with AI-verified return evidence and a human in charge.\n\n"
            "Run the API with `PAYPAL_MODE=mock` (no PayPal account needed), then run **Dispute walkthrough** in order, "
            "then **Safety checks**. Step 5 needs the local GPU service.\n\n"
            "PayPal APIs behind these endpoints: Disputes v1 (list, get, provide-evidence, send-message), "
            "Payments v2 (get capture, refund) and Webhooks (verify-webhook-signature). "
            "See docs/PAYPAL_INTEGRATION.md."
        ),
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": [
        {"id": uid("walkthrough"), "name": "Dispute walkthrough", "item": walkthrough,
         "description": "From PayPal dispute to a confirmed PayPal action, in order."},
        {"id": uid("safety"), "name": "Safety checks", "item": safety,
         "description": "The rules that hold whatever the agent or a request says. Run after the walkthrough."},
    ],
    "variable": [
        {"key": "baseUrl", "value": "http://127.0.0.1:8000"},
        {"key": "reviewerName", "value": "Postman reviewer"},
        {"key": "mockWebhookSignature", "value": "mock-signature-ok"},
        {"key": "caseId", "value": ""},
        {"key": "proposedAction", "value": ""},
        {"key": "buyerMessage", "value": ""},
        {"key": "evidenceNotes", "value": ""},
        {"key": "executeBody", "value": ""},
    ],
}

environment = {
    "id": uid("environment"),
    "name": "SwapShield local (mock PayPal)",
    "values": [
        {"key": "baseUrl", "value": "http://127.0.0.1:8000", "enabled": True},
        {"key": "reviewerName", "value": "Postman reviewer", "enabled": True},
    ],
    "_postman_variable_scope": "environment",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "SwapShield.postman_collection.json").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
    (OUT / "SwapShield-local.postman_environment.json").write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from service.app import app  # noqa: E402

    (OUT / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
