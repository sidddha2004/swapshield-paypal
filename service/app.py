"""FastAPI adapter for the local RTX inference pipeline."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional convenience outside the GPU extra
    pass

import json
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from ml.swapshield_ml.auditor import EvidenceAuditor
from ml.swapshield_ml.pipeline import VerificationRequest, VerifierPipeline
from service.cases import CaseStore, case_id_for
from service.evidence_report import render_evidence_report
from service.replay import ReplayStore, replay_enabled
from service.paypal_client import EvidenceFile, PayPalError, PayPalGateway, paypal_from_env
from service.notify import DEADLINE_BUCKETS, Notifier, due_bucket, hours_left
from service.resolution_agent import ResolutionAgent, ResolutionValidationError, check_action


MAX_UPLOAD_BYTES = int(os.getenv("SWAPSHIELD_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("SWAPSHIELD_MAX_IMAGE_PIXELS", "30000000"))
_pipeline: VerifierPipeline | None = None
_auditor: EvidenceAuditor | None = None
_paypal: PayPalGateway | None = None
_paypal_loaded = False
_store: CaseStore | None = None
_agent: ResolutionAgent | None = None
_notifier: Notifier | None = None
_replay: ReplayStore | None = None
ACTIONABLE_DISPUTE_STATUSES = {"WAITING_FOR_SELLER_RESPONSE", "OPEN", "UNDER_REVIEW"}
DISPUTE_EVENTS = {"CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"}

app = FastAPI(
    title="SwapShield Local Verifier",
    version="1.0.0",
    description=(
        "Defense-only return evidence comparison linked to PayPal disputes. Money moves only on explicit, "
        "named reviewer confirmation; nothing is auto-rejected and PayPal adjudicates contested cases."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _decision_policy_path() -> str | None:
    path = os.getenv("SWAPSHIELD_DECISION_POLICY", "evaluation/results/decision-policy.json").strip()
    return path if path and Path(path).exists() else None


def _decision_kind() -> str:
    path = _decision_policy_path()
    if not path:
        return "fusion"
    try:
        return f"single-signal:{json.loads(Path(path).read_text(encoding='utf-8'))['signal']}"
    except (OSError, ValueError, KeyError):
        return "invalid-policy"


def get_pipeline() -> VerifierPipeline:
    global _pipeline
    if _pipeline is None:
        threshold = float(os.getenv("SWAPSHIELD_REVIEW_THRESHOLD", "0.68"))
        min_image_quality = float(os.getenv("SWAPSHIELD_MIN_IMAGE_QUALITY", "0.46"))
        risk_fn = None
        fusion_model_path = os.getenv(
            "SWAPSHIELD_FUSION_MODEL", "evaluation/results/fusion-model.json"
        ).strip()
        if fusion_model_path:
            from ml.swapshield_ml.fusion import load_fusion_model

            fusion_model = load_fusion_model(fusion_model_path)
            threshold = fusion_model.threshold
            min_image_quality = fusion_model.min_image_quality
            risk_fn = fusion_model.score_risk
        policy_path = _decision_policy_path()
        if policy_path:
            # selected on validation v2 (docs/EVALUATION.md); overrides the fusion decision
            from ml.swapshield_ml.signal_policy import load_signal_policy

            policy = load_signal_policy(policy_path)
            threshold = policy.threshold
            min_image_quality = policy.min_image_quality
            risk_fn = policy.score_risk
        from ml.swapshield_ml.vision import DinoPairEncoder
        from ml.swapshield_ml.vlm import QwenVisualVerifier

        _pipeline = VerifierPipeline(
            DinoPairEncoder(
                model_name=os.getenv("SWAPSHIELD_VISION_MODEL", "facebook/dinov2-small"),
                device=os.getenv("SWAPSHIELD_DEVICE", "cuda"),
            ),
            QwenVisualVerifier(
                model_name=os.getenv("SWAPSHIELD_VLM_MODEL", "Qwen/Qwen3-VL-4B-Instruct"),
                four_bit=os.getenv("SWAPSHIELD_VLM_4BIT", "true").lower() == "true",
                max_pixels=int(os.getenv("SWAPSHIELD_VLM_MAX_PIXELS", "262144")),
                oom_retry_pixels=int(os.getenv("SWAPSHIELD_VLM_OOM_RETRY_PIXELS", "131072")),
            ),
            threshold=threshold,
            min_image_quality=min_image_quality,
            risk_fn=risk_fn,
        )
    return _pipeline


def get_auditor() -> EvidenceAuditor:
    global _auditor
    if _auditor is None:
        _auditor = EvidenceAuditor.from_env()
    return _auditor


def get_paypal() -> PayPalGateway:
    global _paypal, _paypal_loaded
    if not _paypal_loaded:
        _paypal = paypal_from_env()
        _paypal_loaded = True
    if _paypal is None:
        raise HTTPException(
            status_code=503,
            detail="PayPal is not configured. Set PAYPAL_MODE=mock, or PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.",
        )
    return _paypal


def get_store() -> CaseStore:
    global _store
    if _store is None:
        _store = CaseStore(os.getenv("SWAPSHIELD_CASE_DB", "artifacts/swapshield_cases.db"))
    return _store


def get_agent() -> ResolutionAgent:
    global _agent
    if _agent is None:
        _agent = ResolutionAgent.from_env()
    return _agent


def get_replay() -> ReplayStore:
    global _replay
    if _replay is None:
        _replay = ReplayStore()
    return _replay


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier.from_env()
    return _notifier


def _paypal_status() -> dict[str, object]:
    try:
        gateway = get_paypal()
    except HTTPException:
        return {"configured": False, "mode": "not_configured"}
    return {"configured": True, "mode": gateway.mode}


async def _decode_image(upload: UploadFile):
    if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG, and WebP evidence is accepted")
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Evidence image exceeds the configured size limit")
    try:
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        image = Image.open(BytesIO(content))
        image.load()
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Evidence file is not a valid image") from exc


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "SwapShield Local Verifier",
        "status": "ready",
        "documentation": "/docs",
        "health": "/health",
        "policy": "recommendation_only",
    }


@app.get("/health")
def health() -> dict[str, object]:
    pipeline = _pipeline
    fusion_path = os.getenv(
        "SWAPSHIELD_FUSION_MODEL", "evaluation/results/fusion-model.json"
    ).strip()
    auditor = get_auditor()
    return {
        "status": "ready",
        "policy": "recommendation_only",
        "models": {
            "dinov2": "loaded" if pipeline and getattr(pipeline.pair_encoder, "is_loaded", False) else "lazy",
            "qwen3_vl": "loaded" if pipeline and getattr(pipeline.visual_verifier, "is_loaded", False) else "lazy",
            "fusion": "trained" if fusion_path and Path(fusion_path).exists() else "baseline",
            "decision": _decision_kind(),
            "pipeline": "replay (recorded GPU results for the demo photos)" if replay_enabled() else "live",
        },
        "auditor": {
            "mode": "llm_api" if auditor.configured else "deterministic_fallback",
            "model": auditor.model or None,
            "authority": "advisory_only",
        },
        "paypal": _paypal_status(),
        "resolution_agent": {
            "mode": "llm_api" if get_agent().configured else "deterministic_fallback",
            "authority": "proposal_only",
        },
        "alerts": {"zapier": get_notifier().configured},
    }


@app.post("/v1/verify")
async def verify_return(
    dispatch_image: UploadFile = File(...),
    return_image: UploadFile = File(...),
    dispatch_sku: str = Form(..., min_length=1, max_length=80),
    return_sku: str = Form(..., min_length=1, max_length=80),
    dispatch_serial: str | None = Form(default=None, max_length=120),
    return_serial: str | None = Form(default=None, max_length=120),
    dispatch_weight_grams: float | None = Form(default=None, gt=0),
    return_weight_grams: float | None = Form(default=None, ge=0),
) -> dict[str, object]:
    dispatch = await _decode_image(dispatch_image)
    returned = await _decode_image(return_image)
    return await _run_verification(
        dispatch,
        returned,
        dispatch_sku=dispatch_sku,
        return_sku=return_sku,
        dispatch_serial=dispatch_serial,
        return_serial=return_serial,
        dispatch_weight_grams=dispatch_weight_grams,
        return_weight_grams=return_weight_grams,
    )


async def _run_verification(
    dispatch: Image.Image,
    returned: Image.Image,
    *,
    dispatch_sku: str,
    return_sku: str,
    dispatch_serial: str | None,
    return_serial: str | None,
    dispatch_weight_grams: float | None,
    return_weight_grams: float | None,
) -> dict[str, Any]:
    if replay_enabled():
        recorded = get_replay().lookup(dispatch, returned)
        if recorded is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Replay mode only knows the demo photo pairs in public/demo/ ("
                    + "; ".join(get_replay().labels())
                    + "). Run without SWAPSHIELD_PIPELINE=replay, on a GPU, to verify your own photos."
                ),
            )
        return recorded
    request = VerificationRequest(
        dispatch_image=dispatch,
        return_image=returned,
        dispatch_sku=dispatch_sku,
        return_sku=return_sku,
        dispatch_serial=dispatch_serial,
        return_serial=return_serial,
        dispatch_weight_grams=dispatch_weight_grams,
        return_weight_grams=return_weight_grams,
    )
    try:
        result = await run_in_threadpool(get_pipeline().verify, request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = result.to_dict()
    audit = await run_in_threadpool(get_auditor().audit, payload)
    payload["auditor_assessment"] = audit.to_dict()
    return payload


# --------------------------------------------------------------------------------------
# PayPal dispute resolution workflow
#
# Dispute in (webhook or sync) -> verify returned item with the existing pipeline ->
# resolution agent drafts a proposal -> named reviewer confirms -> one idempotent
# PayPal call (refund, provide evidence, or buyer message) -> append-only action log.
# --------------------------------------------------------------------------------------


def _ingest_dispute(gateway: PayPalGateway, dispute_id: str, fallback: dict[str, Any] | None = None) -> str:
    try:
        dispute = gateway.get_dispute(dispute_id)
    except PayPalError:
        if not fallback or "dispute_id" not in fallback:
            raise
        dispute = fallback
    captured_amount = None
    transactions = dispute.get("disputed_transactions") or []
    capture_id = transactions[0].get("seller_transaction_id") if transactions else None
    if capture_id:
        try:
            capture = gateway.get_capture(capture_id)
            captured_amount = (capture.get("amount") or {}).get("value")
        except PayPalError:
            captured_amount = None
    store = get_store()
    is_new = store.get(f"CASE-{dispute['dispute_id']}") is None
    case_id = store.upsert_from_dispute(dispute, captured_amount=captured_amount)
    if is_new:
        get_notifier().emit("dispute.received", store.get(case_id), "Upload the dispatch and return photos to verify it.")
    return case_id


def _require_case(case_id: str) -> dict[str, Any]:
    case = get_store().get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@app.post("/v1/paypal/webhook")
async def paypal_webhook(request: Request) -> dict[str, object]:
    gateway = get_paypal()
    raw = await request.body()
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook body is not JSON") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")
    verified = await run_in_threadpool(gateway.verify_webhook_signature, dict(request.headers), event)
    if not verified:
        raise HTTPException(status_code=401, detail="Webhook signature verification failed")
    event_type = event.get("event_type")
    if event_type not in DISPUTE_EVENTS:
        return {"received": True, "ignored": event_type}
    resource = event.get("resource") or {}
    dispute_id = resource.get("dispute_id")
    if not dispute_id:
        raise HTTPException(status_code=422, detail="Dispute event has no dispute_id")
    try:
        case_id = await run_in_threadpool(_ingest_dispute, gateway, str(dispute_id), resource)
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    get_store().log_action(
        case_id, "webhook", actor="paypal", detail={"event_id": event.get("id"), "event_type": event_type}
    )
    return {"received": True, "case_id": case_id}


@app.post("/v1/paypal/sync")
async def paypal_sync() -> dict[str, object]:
    gateway = get_paypal()
    case_ids: list[str] = []
    try:
        disputes = await run_in_threadpool(gateway.list_disputes)
        for summary in disputes:
            case_ids.append(
                await run_in_threadpool(_ingest_dispute, gateway, str(summary["dispute_id"]), summary)
            )
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"synced": len(case_ids), "case_ids": case_ids, "mode": gateway.mode}


@app.get("/v1/cases")
def list_cases() -> dict[str, object]:
    return {"cases": get_store().list_cases(), "paypal": _paypal_status()}


@app.get("/v1/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    return _require_case(case_id)


@app.get("/v1/cases/{case_id}/report.pdf")
def get_case_report(case_id: str) -> Response:
    _require_case(case_id)
    pdf = get_store().report_pdf(case_id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="No evidence report yet; verify the case first")
    return Response(content=pdf, media_type="application/pdf")


@app.post("/v1/cases/{case_id}/verify")
async def verify_case(
    case_id: str,
    dispatch_image: UploadFile = File(...),
    return_image: UploadFile = File(...),
    dispatch_sku: str = Form(..., min_length=1, max_length=80),
    return_sku: str = Form(..., min_length=1, max_length=80),
    dispatch_serial: str | None = Form(default=None, max_length=120),
    return_serial: str | None = Form(default=None, max_length=120),
    dispatch_weight_grams: float | None = Form(default=None, gt=0),
    return_weight_grams: float | None = Form(default=None, ge=0),
) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["workflow_status"] == "resolved":
        raise HTTPException(status_code=409, detail="This case has already been resolved on PayPal")
    dispatch = await _decode_image(dispatch_image)
    returned = await _decode_image(return_image)
    records: dict[str, Any] = {
        "dispatch_sku": dispatch_sku,
        "return_sku": return_sku,
        "dispatch_serial": dispatch_serial,
        "return_serial": return_serial,
        "dispatch_weight_grams": dispatch_weight_grams,
        "return_weight_grams": return_weight_grams,
    }
    verification = await _run_verification(dispatch, returned, **records)
    verification["records"] = records
    report = await run_in_threadpool(
        lambda: render_evidence_report(
            case=case, verification=verification, dispatch_image=dispatch, return_image=returned, records=records
        )
    )
    store = get_store()
    store.save_verification(case_id, verification, report)
    store.log_action(
        case_id,
        "verified",
        actor="swapshield",
        detail={"decision": verification["risk"]["decision"], "score": verification["risk"]["score"]},
    )
    updated = _require_case(case_id)
    decision = {"approve": "Matches the dispatch record", "review": "Does not clearly match; needs a closer look", "recapture": "Photos too unclear to decide"}
    get_notifier().emit("case.verified", updated, f"{decision.get(verification['risk']['decision'], 'Verified')} (score {verification['risk']['score']}).")
    return updated


@app.post("/v1/cases/{case_id}/resolution/draft")
async def draft_resolution(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["workflow_status"] == "resolved":
        raise HTTPException(status_code=409, detail="This case has already been resolved on PayPal")
    try:
        draft = await run_in_threadpool(get_agent().draft, case)
    except ResolutionValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    store = get_store()
    store.save_draft(case_id, draft.to_dict())
    store.log_action(
        case_id,
        "drafted",
        actor=f"resolution_agent:{draft.source}",
        detail={"proposed_action": draft.proposed_action, "refund_amount": draft.refund_amount},
    )
    updated = _require_case(case_id)
    get_notifier().emit("resolution.ready", updated, f"Proposed: {draft.proposed_action.replace('_', ' ')}. A named reviewer must confirm.")
    return updated


class ExecuteRequest(BaseModel):
    action: Literal["refund_full", "refund_partial", "submit_evidence", "request_photos"]
    reviewer_name: str = Field(min_length=2, max_length=80)
    confirm: bool
    refund_amount: str | None = Field(default=None, max_length=20)
    buyer_message: str = Field(default="", max_length=1200)
    evidence_notes: str = Field(default="", max_length=1200)


@app.post("/v1/cases/{case_id}/resolution/execute")
async def execute_resolution(case_id: str, body: ExecuteRequest) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Execution requires explicit reviewer confirmation")
    case = _require_case(case_id)
    gateway = get_paypal()
    store = get_store()

    # The hard rules apply to every request, replays included, before anything else.
    try:
        amount = check_action(
            action=body.action,
            refund_amount=body.refund_amount,
            buyer_message=body.buyer_message,
            evidence_notes=body.evidence_notes,
            verification=case["verification"],
            max_refund=case.get("captured_amount") or case.get("amount"),
        )
    except ResolutionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request_id = f"swapshield-{case_id}-{body.action}"
    if body.action == "request_photos":
        prior = sum(1 for item in case["actions"] if item["action"] == "executed:request_photos")
        request_id += f"-{prior + 1}"
    else:
        replay = store.has_executed(request_id)
        if replay is not None:
            return {**case, "execution": {**replay, "replayed": True}}
    if case["workflow_status"] == "resolved":
        raise HTTPException(status_code=409, detail="This case has already been resolved on PayPal")
    if case.get("dispute_status") not in ACTIONABLE_DISPUTE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Dispute status {case.get('dispute_status')} is not actionable")

    actor = f"reviewer:{body.reviewer_name.strip()}"
    detail: dict[str, Any]
    try:
        if body.action in {"refund_full", "refund_partial"}:
            if not case.get("capture_id"):
                raise HTTPException(status_code=409, detail="Dispute has no capture to refund")
            response = await run_in_threadpool(
                lambda: gateway.refund_capture(
                    case["capture_id"],
                    amount=str(amount),
                    currency=case["currency"],
                    note=body.buyer_message,
                    request_id=request_id,
                )
            )
            detail = {
                "paypal_refund_id": response.get("id"),
                "paypal_status": response.get("status"),
                "amount": str(amount),
                "currency": case["currency"],
            }
        elif body.action == "submit_evidence":
            report = store.report_pdf(case_id)
            if report is None:
                raise HTTPException(status_code=409, detail="Verify the case to generate the evidence report first")
            files = [EvidenceFile(f"swapshield-{case['dispute_id']}.pdf", "application/pdf", report)]
            response = await run_in_threadpool(
                lambda: gateway.provide_evidence(
                    case["dispute_id"], notes=body.evidence_notes, files=files, request_id=request_id
                )
            )
            detail = {"paypal_status": response.get("status", "SUBMITTED"), "files": [files[0].filename]}
        else:
            await run_in_threadpool(
                lambda: gateway.send_message(case["dispute_id"], message=body.buyer_message, request_id=request_id)
            )
            detail = {"paypal_status": "MESSAGE_SENT"}
    except PayPalError as exc:
        store.log_action(
            case_id,
            f"failed:{body.action}",
            actor=actor,
            request_id=request_id,
            detail={"error": str(exc), "status": exc.status, "debug_id": exc.debug_id},
        )
        get_notifier().emit("paypal.action_failed", case, f"{body.action.replace('_', ' ')}: {exc}")
        raise HTTPException(status_code=502, detail=f"PayPal rejected the action: {exc}") from exc

    detail["paypal_mode"] = gateway.mode
    store.log_action(case_id, f"executed:{body.action}", actor=actor, request_id=request_id, detail=detail)
    get_notifier().emit("paypal.action_sent", case, f"{body.action.replace('_', ' ')} confirmed by {body.reviewer_name.strip()}.")
    if body.action == "request_photos":
        store.set_workflow_status(case_id, "awaiting_evidence")
    else:
        store.mark_resolved(case_id)
    return {**_require_case(case_id), "execution": detail}


@app.post("/v1/alerts/deadlines")
async def deadline_alerts(within_hours: int = 48, dry_run: bool = False) -> dict[str, Any]:
    """Send one alert per open case as it enters each deadline bucket (48 h, 24 h, 6 h).

    Call it on a schedule (Zapier Schedule, cron). Repeated calls do not repeat
    an alert: each case alerts once per bucket, recorded in its action log.
    """
    notifier = get_notifier()
    store = get_store()
    sent: list[dict[str, Any]] = []
    for summary in store.list_cases():
        if summary["workflow_status"] == "resolved":
            continue
        left = hours_left(summary.get("seller_response_due"))
        bucket = due_bucket(left)
        if bucket is None or left is None or left > within_hours:
            continue
        marker = f"alerted:deadline:{bucket}h"
        case = store.get(summary["case_id"])
        if case is None or any(entry["action"] == marker for entry in case["actions"]):
            continue
        payload = notifier.payload("deadline.approaching", case, f"Within the {bucket} h window.")
        delivered = False if dry_run else await run_in_threadpool(notifier.deliver, payload)
        if delivered:
            store.log_action(case["case_id"], marker, actor="alerts", detail={"hours_left": left, "bucket_hours": bucket})
        sent.append({**payload, "delivered": delivered})
    return {"configured": notifier.configured, "buckets_hours": list(DEADLINE_BUCKETS), "alerts": sent}
