"""Optional outbound events for Zapier (or any webhook): deadlines and actions.

Set ``ZAPIER_WEBHOOK_URL`` to a Zapier "Catch Hook" URL and route the events to
Slack, email or a sheet. Delivery is best effort: a failure is logged and never
blocks or changes the dispute workflow. Payloads are flat for easy mapping and
never include the buyer's message, which is untrusted text.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger("swapshield.notify")

EVENTS = {
    "dispute.received": "New PayPal dispute",
    "case.verified": "Returned item verified",
    "resolution.ready": "Resolution ready for your confirmation",
    "paypal.action_sent": "Sent to PayPal",
    "paypal.action_failed": "PayPal rejected an action",
    "deadline.approaching": "PayPal response deadline approaching",
}
DEADLINE_BUCKETS = (6, 24, 48)  # hours; each case alerts once per bucket it enters


def hours_left(iso: str | None, *, now: float | None = None) -> int | None:
    if not iso:
        return None
    due = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    return round((due - (now if now is not None else time.time())) / 3600)


def money(amount: str | None, currency: str | None) -> str:
    if amount is None:
        return "—"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "", "")
    return f"{symbol}{amount}" if symbol else f"{amount} {currency or ''}".strip()


def build_payload(event: str, case: dict[str, Any], *, detail: str = "", dashboard_url: str = "") -> dict[str, Any]:
    item = case.get("item_name") or case.get("item_sku") or "Disputed item"
    left = hours_left(case.get("seller_response_due"))
    due = "deadline passed" if left is not None and left < 0 else (f"{left} h left to respond" if left is not None else "no deadline")
    summary = f"{EVENTS.get(event, event)}: {item}, {money(case.get('amount'), case.get('currency'))}, {due}."
    if detail:
        summary += f" {detail}"
    return {
        "event": event,
        "summary": summary,
        "case_id": case.get("case_id"),
        "dispute_id": case.get("dispute_id"),
        "item": item,
        "amount": case.get("amount"),
        "currency": case.get("currency"),
        "hours_left": left,
        "seller_response_due": case.get("seller_response_due"),
        "workflow_status": case.get("workflow_status"),
        "detail": detail,
        "dashboard_url": dashboard_url,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class Notifier:
    def __init__(self, url: str = "", *, dashboard_url: str = "", timeout_seconds: float = 5.0) -> None:
        self.url = url.strip()
        self.dashboard_url = dashboard_url.strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @classmethod
    def from_env(cls) -> "Notifier":
        return cls(os.getenv("ZAPIER_WEBHOOK_URL", ""), dashboard_url=os.getenv("SWAPSHIELD_DASHBOARD_URL", "http://127.0.0.1:5173"))

    def payload(self, event: str, case: dict[str, Any], detail: str = "") -> dict[str, Any]:
        return build_payload(event, case, detail=detail, dashboard_url=self.dashboard_url)

    def deliver(self, payload: dict[str, Any]) -> bool:
        if not self.configured:
            return False
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except (URLError, TimeoutError, OSError) as exc:
            log.warning("event %s not delivered: %s", payload.get("event"), exc)
            return False

    def emit(self, event: str, case: dict[str, Any] | None, detail: str = "") -> None:
        """Fire and forget: never raises, never blocks the request."""
        if not self.configured or not case:
            return
        payload = self.payload(event, case, detail)
        threading.Thread(target=self.deliver, args=(payload,), daemon=True).start()


def due_bucket(left: int | None) -> int | None:
    """Smallest bucket the case is inside, e.g. 41 h left -> 48."""
    if left is None or left < 0:
        return None
    return next((bucket for bucket in DEADLINE_BUCKETS if left <= bucket), None)
