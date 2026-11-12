"""Thin PayPal REST client used by the SwapShield resolution workflow.

Only standard-library HTTP is used, mirroring ``ml/swapshield_ml/auditor.py``.
Credentials are read from the server environment and never reach the browser.

``MockPayPalClient`` implements the same interface from recorded sandbox-shaped
fixtures so tests and offline demos work without network access.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
LIVE_BASE_URL = "https://api-m.paypal.com"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "paypal"


class PayPalError(RuntimeError):
    """Raised when PayPal rejects a request or cannot be reached."""

    def __init__(self, message: str, *, status: int | None = None, debug_id: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.debug_id = debug_id


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    filename: str
    content_type: str
    data: bytes


class PayPalGateway(Protocol):
    mode: str

    def list_disputes(self, *, statuses: tuple[str, ...] = ...) -> list[dict[str, Any]]: ...
    def get_dispute(self, dispute_id: str) -> dict[str, Any]: ...
    def get_capture(self, capture_id: str) -> dict[str, Any]: ...
    def refund_capture(
        self, capture_id: str, *, amount: str, currency: str, note: str, request_id: str
    ) -> dict[str, Any]: ...
    def provide_evidence(
        self, dispute_id: str, *, notes: str, files: list[EvidenceFile], request_id: str
    ) -> dict[str, Any]: ...
    def send_message(self, dispute_id: str, *, message: str, request_id: str) -> dict[str, Any]: ...
    def verify_webhook_signature(self, headers: dict[str, str], event: dict[str, Any]) -> bool: ...


def _multipart(fields: list[tuple[str, str | None, str, bytes]]) -> tuple[bytes, str]:
    """Encode ``[(name, filename, content_type, data)]`` as multipart/form-data.

    A list (not a dict) because PayPal accepts repeated ``evidence_file`` parts.
    """
    boundary = f"swapshield-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, filename, content_type, data in fields:
        disposition = f'form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {content_type}\r\n\r\n".encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class PayPalClient:
    """REST client for Disputes v1, Payments v2 refunds and webhook verification."""

    mode = "sandbox"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        environment: str = "sandbox",
        webhook_id: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("PayPal client ID and secret are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = "live" if environment == "live" else "sandbox"
        self.mode = self.environment
        self.base_url = LIVE_BASE_URL if self.environment == "live" else SANDBOX_BASE_URL
        self.webhook_id = webhook_id
        self.timeout_seconds = timeout_seconds
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    # -- transport -----------------------------------------------------------------
    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            request = Request(
                f"{self.base_url}/v1/oauth2/token",
                data=urlencode({"grant_type": "client_credentials"}).encode(),
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                method="POST",
            )
            payload = self._send(request)
            self._token = str(payload["access_token"])
            self._token_expiry = time.time() + float(payload.get("expires_in", 300))
            return self._token

    def _send(self, request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            debug_id = None
            message = f"PayPal returned HTTP {exc.code}"
            try:
                parsed = json.loads(detail)
                debug_id = parsed.get("debug_id")
                message = f"{message}: {parsed.get('name', '')} {parsed.get('message', '')}".strip()
            except json.JSONDecodeError:
                pass
            raise PayPalError(message, status=exc.code, debug_id=debug_id) from exc
        except (URLError, TimeoutError) as exc:
            raise PayPalError("PayPal API is unreachable") from exc

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        raw: tuple[bytes, str] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        data: bytes | None = None
        if raw is not None:
            data, headers["Content-Type"] = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return self._send(Request(f"{self.base_url}{path}", data=data, headers=headers, method=method))

    # -- API surface ---------------------------------------------------------------
    def list_disputes(
        self, *, statuses: tuple[str, ...] = ("WAITING_FOR_SELLER_RESPONSE", "UNDER_REVIEW", "OPEN")
    ) -> list[dict[str, Any]]:
        payload = self._call("GET", "/v1/customer/disputes?page_size=50")
        items = payload.get("items", [])
        return [item for item in items if not statuses or item.get("status") in statuses]

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/customer/disputes/{dispute_id}")

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v2/payments/captures/{capture_id}")

    def refund_capture(
        self, capture_id: str, *, amount: str, currency: str, note: str, request_id: str
    ) -> dict[str, Any]:
        body = {"amount": {"value": amount, "currency_code": currency}, "note_to_payer": note[:255]}
        return self._call("POST", f"/v2/payments/captures/{capture_id}/refund", body=body, request_id=request_id)

    def provide_evidence(
        self, dispute_id: str, *, notes: str, files: list[EvidenceFile], request_id: str
    ) -> dict[str, Any]:
        evidence_input = {"evidences": [{"evidence_type": "OTHER", "notes": notes[:2000]}]}
        fields: list[tuple[str, str | None, str, bytes]] = [
            ("input", None, "application/json", json.dumps(evidence_input).encode("utf-8")),
        ]
        fields.extend(("evidence_file", item.filename, item.content_type, item.data) for item in files)
        return self._call(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            raw=_multipart(fields),
            request_id=request_id,
        )

    def send_message(self, dispute_id: str, *, message: str, request_id: str) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/send-message",
            body={"message": message[:2000]},
            request_id=request_id,
        )

    def verify_webhook_signature(self, headers: dict[str, str], event: dict[str, Any]) -> bool:
        if not self.webhook_id:
            return False
        lowered = {key.lower(): value for key, value in headers.items()}
        required = (
            "paypal-auth-algo",
            "paypal-cert-url",
            "paypal-transmission-id",
            "paypal-transmission-sig",
            "paypal-transmission-time",
        )
        if any(not lowered.get(name) for name in required):
            return False
        body = {
            "auth_algo": lowered["paypal-auth-algo"],
            "cert_url": lowered["paypal-cert-url"],
            "transmission_id": lowered["paypal-transmission-id"],
            "transmission_sig": lowered["paypal-transmission-sig"],
            "transmission_time": lowered["paypal-transmission-time"],
            "webhook_id": self.webhook_id,
            "webhook_event": event,
        }
        try:
            result = self._call("POST", "/v1/notifications/verify-webhook-signature", body=body)
        except PayPalError:
            return False
        return result.get("verification_status") == "SUCCESS"


class MockPayPalClient:
    """In-memory PayPal stand-in replaying sandbox-shaped fixtures.

    Honors ``PayPal-Request-Id`` idempotency like the real API: repeating a
    request ID returns the original response instead of acting twice.
    """

    mode = "mock"
    WEBHOOK_SECRET_HEADER = "x-swapshield-mock-signature"
    WEBHOOK_SECRET = "mock-signature-ok"

    def __init__(self, fixture_dir: Path = FIXTURE_DIR) -> None:
        self._disputes: dict[str, dict[str, Any]] = {}
        self._captures: dict[str, dict[str, Any]] = {}
        disputes_path = fixture_dir / "disputes.json"
        captures_path = fixture_dir / "captures.json"
        if disputes_path.exists():
            for item in json.loads(disputes_path.read_text(encoding="utf-8")):
                self._disputes[item["dispute_id"]] = item
        if captures_path.exists():
            for item in json.loads(captures_path.read_text(encoding="utf-8")):
                self._captures[item["id"]] = item
        self._idempotent: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def _once(self, request_id: str, action: str, target: str, build: Any) -> dict[str, Any]:
        with self._lock:
            if request_id in self._idempotent:
                return copy.deepcopy(self._idempotent[request_id])
            self.calls.append((action, target))
            result = build()
            self._idempotent[request_id] = result
            return copy.deepcopy(result)

    def list_disputes(
        self, *, statuses: tuple[str, ...] = ("WAITING_FOR_SELLER_RESPONSE", "UNDER_REVIEW", "OPEN")
    ) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(item)
            for item in self._disputes.values()
            if not statuses or item.get("status") in statuses
        ]

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        if dispute_id not in self._disputes:
            raise PayPalError("RESOURCE_NOT_FOUND", status=404)
        return copy.deepcopy(self._disputes[dispute_id])

    def get_capture(self, capture_id: str) -> dict[str, Any]:
        if capture_id not in self._captures:
            raise PayPalError("RESOURCE_NOT_FOUND", status=404)
        return copy.deepcopy(self._captures[capture_id])

    def refund_capture(
        self, capture_id: str, *, amount: str, currency: str, note: str, request_id: str
    ) -> dict[str, Any]:
        capture = self.get_capture(capture_id)
        if float(amount) > float(capture["amount"]["value"]) or currency != capture["amount"]["currency_code"]:
            raise PayPalError("UNPROCESSABLE_ENTITY: refund exceeds captured amount", status=422)

        def build() -> dict[str, Any]:
            self._captures[capture_id]["status"] = (
                "REFUNDED" if float(amount) >= float(capture["amount"]["value"]) else "PARTIALLY_REFUNDED"
            )
            return {
                "id": f"MOCK-REFUND-{uuid.uuid4().hex[:10].upper()}",
                "status": "COMPLETED",
                "amount": {"value": amount, "currency_code": currency},
                "note_to_payer": note,
            }

        return self._once(request_id, "refund", capture_id, build)

    def provide_evidence(
        self, dispute_id: str, *, notes: str, files: list[EvidenceFile], request_id: str
    ) -> dict[str, Any]:
        self.get_dispute(dispute_id)

        def build() -> dict[str, Any]:
            self._disputes[dispute_id]["status"] = "UNDER_REVIEW"
            return {
                "links": [{"rel": "self", "href": f"mock://disputes/{dispute_id}", "method": "GET"}],
                "evidence_files": [item.filename for item in files],
                "status": "UNDER_REVIEW",
            }

        return self._once(request_id, "provide_evidence", dispute_id, build)

    def send_message(self, dispute_id: str, *, message: str, request_id: str) -> dict[str, Any]:
        self.get_dispute(dispute_id)

        def build() -> dict[str, Any]:
            self._disputes[dispute_id].setdefault("messages", []).append(
                {"posted_by": "SELLER", "content": message, "time_posted": _now_iso()}
            )
            return {"links": [{"rel": "self", "href": f"mock://disputes/{dispute_id}", "method": "GET"}]}

        return self._once(request_id, "send_message", dispute_id, build)

    def verify_webhook_signature(self, headers: dict[str, str], event: dict[str, Any]) -> bool:
        lowered = {key.lower(): value for key, value in headers.items()}
        return lowered.get(self.WEBHOOK_SECRET_HEADER) == self.WEBHOOK_SECRET


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def paypal_from_env() -> PayPalGateway | None:
    """Build the configured gateway: ``PAYPAL_MODE=mock`` or credentials for sandbox/live."""
    mode = os.getenv("PAYPAL_MODE", "").strip().lower()
    if mode == "mock":
        return MockPayPalClient()
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        return None
    return PayPalClient(
        client_id=client_id,
        client_secret=secret,
        environment=os.getenv("PAYPAL_ENV", "sandbox").strip().lower(),
        webhook_id=os.getenv("PAYPAL_WEBHOOK_ID", "").strip(),
    )
