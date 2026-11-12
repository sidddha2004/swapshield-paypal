"""End-to-end tests for the PayPal dispute workflow in mock mode.

The real ML models are replaced with the same fakes used in ml/tests so the
tests run on CPU without model downloads.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import service.app as service_app
from ml.swapshield_ml.auditor import EvidenceAuditor
from ml.swapshield_ml.pipeline import VerifierPipeline
from ml.swapshield_ml.quality import ImageQuality
from ml.swapshield_ml.schemas import VisualObservation, VlmAssessment
from service.cases import CaseStore
from service.notify import Notifier
from service.paypal_client import MockPayPalClient
from service.resolution_agent import (
    ResolutionAgent,
    ResolutionValidationError,
    check_action,
    parse_agent_json,
)


REPO = Path(__file__).resolve().parents[2]
MOCK_HEADERS = {MockPayPalClient.WEBHOOK_SECRET_HEADER: MockPayPalClient.WEBHOOK_SECRET}
MATCH_CASE = "CASE-PP-D-27803"  # $39.99 mouse
INJECTION_CASE = "CASE-PP-D-27811"  # $149.00 table, buyer message contains a prompt injection


class FakeEncoder:
    def __init__(self, similarity: float) -> None:
        self.similarity = similarity

    def compare(self, dispatch_images, return_images) -> float:
        return self.similarity


class FakeVerifier:
    def __init__(self, assessment: VlmAssessment) -> None:
        self.assessment = assessment

    def verify(self, dispatch_image, return_image) -> VlmAssessment:
        return self.assessment


def make_pipeline(kind: str) -> VerifierPipeline:
    quality = lambda image: ImageQuality(0.9, 0.9, 0.9, 0.9)  # noqa: E731
    if kind == "match":
        assessment = VlmAssessment(True, 0.95, 0.05, (), ())
        return VerifierPipeline(FakeEncoder(0.96), FakeVerifier(assessment), quality_fn=quality)
    if kind == "mismatch":
        observation = VisualObservation(
            "color", "walnut brown", "light oak", "material", ("dispatch_image", "return_image")
        )
        assessment = VlmAssessment(True, 0.08, 0.94, (observation,), ())
        return VerifierPipeline(FakeEncoder(0.42), FakeVerifier(assessment), quality_fn=quality)
    assessment = VlmAssessment(False, 0.9, 0.05, (), ("rear model label",))
    return VerifierPipeline(FakeEncoder(0.96), FakeVerifier(assessment), quality_fn=quality)


def png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (96, 96), color).save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingNotifier(Notifier):
    """Captures events synchronously instead of posting them."""

    def __init__(self) -> None:
        super().__init__("https://hooks.zapier.test/catch", dashboard_url="http://dashboard.test")
        self.events: list[dict] = []

    def emit(self, event, case, detail=""):
        if case:
            self.events.append(self.payload(event, case, detail))

    def deliver(self, payload):
        self.events.append(payload)
        return True


class WorkflowTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.paypal = MockPayPalClient()
        service_app._paypal = self.paypal
        service_app._paypal_loaded = True
        service_app._store = CaseStore(":memory:")
        service_app._auditor = EvidenceAuditor()
        service_app._agent = ResolutionAgent()
        service_app._pipeline = make_pipeline("match")
        self.notifier = RecordingNotifier()
        service_app._notifier = self.notifier
        self.client = TestClient(service_app.app)

    def tearDown(self) -> None:
        self.client.close()
        if service_app._store is not None:
            service_app._store.close()
        service_app._paypal = None
        service_app._paypal_loaded = False
        service_app._store = None
        service_app._pipeline = None
        service_app._agent = None
        service_app._notifier = None

    def sync(self) -> None:
        response = self.client.post("/v1/paypal/sync")
        self.assertEqual(response.status_code, 200, response.text)

    def verify(self, case_id: str, kind: str = "match", *, sku: str = "MSE-RGB-102") -> dict:
        service_app._pipeline = make_pipeline(kind)
        response = self.client.post(
            f"/v1/cases/{case_id}/verify",
            files={
                "dispatch_image": ("d.png", png_bytes((20, 20, 30)), "image/png"),
                "return_image": ("r.png", png_bytes((25, 22, 31)), "image/png"),
            },
            data={"dispatch_sku": sku, "return_sku": sku, "dispatch_weight_grams": "100", "return_weight_grams": "101"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def execute(self, case_id: str, **overrides):
        body = {
            "action": "refund_full",
            "reviewer_name": "Priya",
            "confirm": True,
            "buyer_message": "We inspected the returned item and issued a refund.",
            "evidence_notes": "",
        }
        body.update(overrides)
        return self.client.post(f"/v1/cases/{case_id}/resolution/execute", json=body)


class IngestTests(WorkflowTestBase):
    def test_sync_creates_cases_sorted_by_response_deadline(self) -> None:
        self.sync()
        cases = self.client.get("/v1/cases").json()["cases"]
        self.assertEqual(len(cases), 5)
        self.assertEqual(cases[0]["dispute_id"], "PP-D-27811")  # earliest seller_response_due
        self.assertEqual(cases[0]["captured_amount"], "149.00")
        self.assertEqual(cases[0]["currency"], "USD")

    def test_repeated_sync_and_webhook_do_not_duplicate_cases(self) -> None:
        self.sync()
        self.sync()
        event = {"id": "WH-1", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-D-27803"}}
        response = self.client.post("/v1/paypal/webhook", content=json.dumps(event), headers=MOCK_HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["case_id"], MATCH_CASE)
        self.assertEqual(len(self.client.get("/v1/cases").json()["cases"]), 5)

    def test_webhook_with_bad_signature_is_rejected(self) -> None:
        event = {"id": "WH-2", "event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {"dispute_id": "PP-D-27803"}}
        response = self.client.post("/v1/paypal/webhook", content=json.dumps(event))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/v1/cases").json()["cases"], [])

    def test_unrelated_webhook_events_are_acknowledged_and_ignored(self) -> None:
        event = {"id": "WH-3", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}
        response = self.client.post("/v1/paypal/webhook", content=json.dumps(event), headers=MOCK_HEADERS)
        self.assertEqual(response.json()["ignored"], "PAYMENT.CAPTURE.COMPLETED")

    def test_health_reports_paypal_mode(self) -> None:
        health = self.client.get("/health").json()
        self.assertEqual(health["paypal"], {"configured": True, "mode": "mock"})
        self.assertEqual(health["resolution_agent"]["authority"], "proposal_only")


class ResolutionFlowTests(WorkflowTestBase):
    def test_matching_return_end_to_end_refund(self) -> None:
        self.sync()
        case = self.verify(MATCH_CASE, "match")
        self.assertEqual(case["verification"]["risk"]["decision"], "approve")
        self.assertTrue(case["has_report"])
        self.assertTrue(self.client.get(f"/v1/cases/{MATCH_CASE}/report.pdf").content.startswith(b"%PDF"))

        drafted = self.client.post(f"/v1/cases/{MATCH_CASE}/resolution/draft").json()
        self.assertEqual(drafted["draft"]["proposed_action"], "refund_full")
        self.assertEqual(drafted["draft"]["authority"], "proposal_only")
        self.assertEqual(self.paypal.calls, [], "drafting must never call PayPal")

        response = self.execute(MATCH_CASE, buyer_message=drafted["draft"]["buyer_message"])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["execution"]["amount"], "39.99")
        self.assertTrue(result["execution"]["paypal_refund_id"].startswith("MOCK-REFUND-"))
        self.assertEqual(result["workflow_status"], "resolved")
        self.assertIn("executed:refund_full", [item["action"] for item in result["actions"]])
        self.assertEqual(result["actions"][-1]["actor"], "reviewer:Priya")

    def test_double_execute_refunds_once(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        first = self.execute(MATCH_CASE).json()
        second = self.execute(MATCH_CASE)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["execution"]["replayed"])
        self.assertEqual(second.json()["execution"]["paypal_refund_id"], first["execution"]["paypal_refund_id"])
        self.assertEqual(self.paypal.calls, [("refund", "3GG279541U471931P")])

    def test_replay_with_accusatory_text_is_refused(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        self.assertEqual(self.execute(MATCH_CASE).status_code, 200)
        replay = self.execute(MATCH_CASE, buyer_message="This looks like fraud.")
        self.assertEqual(replay.status_code, 422)
        self.assertEqual(len(self.paypal.calls), 1)

    def test_execute_requires_confirmation(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        self.assertEqual(self.execute(MATCH_CASE, confirm=False).status_code, 400)
        self.assertEqual(self.paypal.calls, [])

    def test_execute_requires_verification(self) -> None:
        self.sync()
        response = self.execute(MATCH_CASE)
        self.assertEqual(response.status_code, 422)
        self.assertIn("verification", response.json()["detail"])
        self.assertEqual(self.paypal.calls, [])

    def test_partial_refund_cannot_reach_captured_amount(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        response = self.execute(MATCH_CASE, action="refund_partial", refund_amount="9999")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.paypal.calls, [])
        ok = self.execute(MATCH_CASE, action="refund_partial", refund_amount="10.00")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["execution"]["amount"], "10.00")

    def test_mismatch_routes_to_evidence_submission_not_rejection(self) -> None:
        self.sync()
        case = self.verify(INJECTION_CASE, "mismatch", sku="TBL-WAL-412")
        self.assertEqual(case["verification"]["risk"]["decision"], "review")
        draft = self.client.post(f"/v1/cases/{INJECTION_CASE}/resolution/draft").json()["draft"]
        self.assertEqual(draft["proposed_action"], "submit_evidence")
        self.assertIn("color", draft["evidence_notes"])
        self.assertNotIn("9999", json.dumps(draft))
        response = self.execute(
            INJECTION_CASE, action="submit_evidence", evidence_notes=draft["evidence_notes"], buyer_message=""
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.paypal.calls, [("provide_evidence", "PP-D-27811")])
        self.assertEqual(response.json()["execution"]["files"], ["swapshield-PP-D-27811.pdf"])

    def test_recapture_only_allows_asking_for_photos(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "recapture")
        draft = self.client.post(f"/v1/cases/{MATCH_CASE}/resolution/draft").json()["draft"]
        self.assertEqual(draft["proposed_action"], "request_photos")
        self.assertEqual(draft["allowed_actions"], ["request_photos"])
        self.assertEqual(self.execute(MATCH_CASE).status_code, 422)  # refund refused
        sent = self.execute(MATCH_CASE, action="request_photos", buyer_message=draft["buyer_message"])
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual(sent.json()["workflow_status"], "awaiting_evidence")  # open for new photos
        self.assertEqual(self.paypal.calls, [("send_message", "PP-D-27803")])

    def test_accusatory_reviewer_text_is_blocked(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        response = self.execute(MATCH_CASE, buyer_message="This looks like fraud but here is your refund.")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.paypal.calls, [])


class AlertTests(WorkflowTestBase):
    def events(self, name: str) -> list[dict]:
        return [event for event in self.notifier.events if event["event"] == name]

    def set_due_in(self, dispute_id: str, hours: float) -> None:
        import time
        dispute = self.paypal.get_dispute(dispute_id)
        due = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + hours * 3600))
        dispute["seller_response_due_date"] = due
        service_app._store.upsert_from_dispute(dispute)

    def park_all(self) -> None:
        """Move every fixture dispute well outside the alert windows, so tests never depend on today's date."""
        for dispute in self.paypal.list_disputes():
            self.set_due_in(dispute["dispute_id"], 500)

    def test_new_disputes_alert_once(self) -> None:
        self.sync()
        self.sync()
        self.assertEqual(len(self.events("dispute.received")), 5)

    def test_workflow_events_carry_no_buyer_text(self) -> None:
        self.sync()
        self.verify(MATCH_CASE, "match")
        self.client.post(f"/v1/cases/{MATCH_CASE}/resolution/draft")
        self.execute(MATCH_CASE)
        names = [event["event"] for event in self.notifier.events]
        for expected in ("case.verified", "resolution.ready", "paypal.action_sent"):
            self.assertIn(expected, names)
        self.assertNotIn("scroll wheel", json.dumps(self.notifier.events))
        self.assertTrue(self.events("paypal.action_sent")[0]["summary"].startswith("Sent to PayPal: RGB wired gaming mouse, $39.99"))

    def test_deadline_alerts_once_per_bucket(self) -> None:
        self.sync()
        self.park_all()
        self.set_due_in("PP-D-27811", 30.5)
        self.set_due_in("PP-D-27803", 200)
        self.set_due_in("PP-D-27815", 5.5)
        first = self.client.post("/v1/alerts/deadlines").json()["alerts"]
        self.assertEqual(sorted(alert["dispute_id"] for alert in first), ["PP-D-27811", "PP-D-27815"])
        table = next(alert for alert in first if alert["dispute_id"] == "PP-D-27811")
        self.assertIn("Walnut side table, $149.00, 30 h left to respond", table["summary"])
        self.assertEqual(self.client.post("/v1/alerts/deadlines").json()["alerts"], [])
        self.set_due_in("PP-D-27811", 20)
        again = self.client.post("/v1/alerts/deadlines").json()["alerts"]
        self.assertEqual([alert["dispute_id"] for alert in again], ["PP-D-27811"])

    def test_resolved_cases_get_no_deadline_alert(self) -> None:
        self.sync()
        self.park_all()
        self.verify(MATCH_CASE, "match")
        self.execute(MATCH_CASE)
        for dispute_id in ("PP-D-27811", "PP-D-27815"):
            self.set_due_in(dispute_id, 200)
        self.set_due_in("PP-D-27803", 3)
        alerts = self.client.post("/v1/alerts/deadlines").json()["alerts"]
        self.assertNotIn("PP-D-27803", [alert["dispute_id"] for alert in alerts])

    def test_dry_run_records_nothing(self) -> None:
        self.sync()
        self.park_all()
        self.set_due_in("PP-D-27811", 200)
        self.set_due_in("PP-D-27803", 200)
        self.set_due_in("PP-D-27815", 5)
        dry = self.client.post("/v1/alerts/deadlines?dry_run=true").json()["alerts"]
        self.assertEqual([alert["delivered"] for alert in dry], [False])
        self.assertEqual(len(self.client.post("/v1/alerts/deadlines").json()["alerts"]), 1)


class ReplayModeTests(WorkflowTestBase):
    """SWAPSHIELD_PIPELINE=replay: the real API without a GPU, for the demo photos only."""

    def setUp(self) -> None:
        super().setUp()
        service_app._pipeline = None  # replay must never need the vision pipeline
        self.env = mock.patch.dict(os.environ, {"SWAPSHIELD_PIPELINE": "replay"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        super().tearDown()

    def demo_files(self, case: str) -> dict:
        folder = REPO / "public" / "demo"
        return {
            "dispatch_image": ("d.jpg", (folder / f"{case}-dispatch.jpg").read_bytes(), "image/jpeg"),
            "return_image": ("r.jpg", (folder / f"{case}-return.jpg").read_bytes(), "image/jpeg"),
        }

    def test_demo_lamp_replays_and_flows_to_refund(self) -> None:
        self.sync()
        response = self.client.post(
            "/v1/cases/CASE-PP-D-27815/verify",
            files=self.demo_files("case-pp-d-27815"),
            data={"dispatch_sku": "LMP-CER-077", "return_sku": "LMP-CER-077"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        case = response.json()
        self.assertEqual(case["verification"]["risk"]["decision"], "approve")
        self.assertIn("Ceramic table lamp", case["verification"]["replay"]["pair"])
        self.assertTrue(case["has_report"])
        draft = self.client.post("/v1/cases/CASE-PP-D-27815/resolution/draft").json()["draft"]
        self.assertEqual(draft["proposed_action"], "refund_full")
        done = self.execute("CASE-PP-D-27815", buyer_message=draft["buyer_message"])
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(done.json()["execution"]["amount"], "64.50")

    def test_swapped_table_replays_as_review(self) -> None:
        self.sync()
        case = self.client.post(
            "/v1/cases/CASE-PP-D-27826/verify",
            files=self.demo_files("case-pp-d-27826"),
            data={"dispatch_sku": "TBL-GRY-188", "return_sku": "TBL-GRY-188"},
        ).json()
        self.assertEqual(case["verification"]["risk"]["decision"], "review")

    def test_unknown_photos_are_refused_not_invented(self) -> None:
        self.sync()
        response = self.client.post(
            f"/v1/cases/{MATCH_CASE}/verify",
            files={
                "dispatch_image": ("d.png", png_bytes((20, 20, 30)), "image/png"),
                "return_image": ("r.png", png_bytes((25, 22, 31)), "image/png"),
            },
            data={"dispatch_sku": "MSE-RGB-102", "return_sku": "MSE-RGB-102"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("Replay mode only knows the demo photo pairs", response.json()["detail"])

    def test_swapped_order_does_not_match(self) -> None:
        files = self.demo_files("case-pp-d-27815")
        files["dispatch_image"], files["return_image"] = files["return_image"], files["dispatch_image"]
        response = self.client.post("/v1/verify", files=files, data={"dispatch_sku": "X", "return_sku": "X"})
        self.assertEqual(response.status_code, 422)

    def test_health_reports_replay(self) -> None:
        self.assertTrue(self.client.get("/health").json()["models"]["pipeline"].startswith("replay"))


class AgentContractTests(unittest.TestCase):
    verification = {
        "risk": {"decision": "review", "score": 91, "reasons": []},
        "evidence_sources": ["dispatch_image", "return_image"],
    }

    def parse(self, payload: dict):
        return parse_agent_json(
            json.dumps(payload),
            permitted=("submit_evidence", "refund_full", "refund_partial"),
            available_ids={"dispatch_image", "return_image"},
            max_refund="149.00",
            verification=self.verification,
        )

    def valid(self, **overrides) -> dict:
        payload = {
            "proposed_action": "submit_evidence",
            "refund_amount": None,
            "buyer_message": "We have shared our inspection with PayPal.",
            "evidence_notes": "Colour differs between dispatch_image and return_image.",
            "rationale": "Material colour difference was observed.",
            "cited_evidence_ids": ["dispatch_image", "return_image"],
        }
        payload.update(overrides)
        return payload

    def test_valid_proposal_parses(self) -> None:
        self.assertEqual(self.parse(self.valid()).proposed_action, "submit_evidence")

    def test_rejects_extra_keys(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            self.parse({**self.valid(), "execute_now": True})

    def test_rejects_unknown_evidence_ids(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            self.parse(self.valid(cited_evidence_ids=["buyer_history"]))

    def test_rejects_injected_oversized_refund(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            self.parse(self.valid(proposed_action="refund_partial", refund_amount="9999", evidence_notes=""))

    def test_rejects_action_not_permitted_by_evidence(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            self.parse(self.valid(proposed_action="request_photos"))

    def test_rejects_accusatory_language(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            self.parse(self.valid(rationale="The buyer is clearly running a scam."))

    def test_check_action_requires_verification(self) -> None:
        with self.assertRaises(ResolutionValidationError):
            check_action(
                action="refund_full", refund_amount=None, buyer_message="hi", evidence_notes="",
                verification=None, max_refund="10.00",
            )

    def test_llm_failure_falls_back_to_deterministic_draft(self) -> None:
        agent = ResolutionAgent(endpoint="http://127.0.0.1:9/unreachable", model="test", timeout_seconds=0.5)
        case = {"verification": {**self.verification, "features": {"vision_similarity": 0.4}}, "item_name": "table"}
        draft = agent.draft(case)
        self.assertEqual(draft.source, "deterministic_fallback")
        self.assertEqual(draft.api_status, "failed")
        self.assertEqual(draft.proposed_action, "submit_evidence")


class FixtureTests(unittest.TestCase):
    def test_fixtures_are_valid_json(self) -> None:
        fixture_dir = REPO / "service" / "fixtures" / "paypal"
        for name in ("disputes.json", "captures.json"):
            self.assertIsInstance(json.loads((fixture_dir / name).read_text(encoding="utf-8")), list)


if __name__ == "__main__":
    unittest.main()
