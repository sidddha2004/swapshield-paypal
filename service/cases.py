"""SQLite-backed store for PayPal-linked return cases and their audit log.

Cases are keyed by PayPal dispute ID, so webhook deliveries and manual syncs
are idempotent. The ``actions`` table is append-only: every PayPal call a
reviewer confirms is recorded with who confirmed it and PayPal's response.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    dispute_id TEXT UNIQUE NOT NULL,
    capture_id TEXT,
    invoice_id TEXT,
    reason TEXT,
    dispute_status TEXT,
    lifecycle_stage TEXT,
    amount TEXT,
    currency TEXT,
    captured_amount TEXT,
    seller_response_due TEXT,
    item_sku TEXT,
    item_name TEXT,
    buyer_message TEXT,
    workflow_status TEXT NOT NULL DEFAULT 'awaiting_evidence',
    dispute_json TEXT NOT NULL,
    verification_json TEXT,
    draft_json TEXT,
    report_pdf BLOB,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT,
    detail_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_case ON actions(case_id);
"""

WORKFLOW_STATUSES = ("awaiting_evidence", "verified", "drafted", "resolved")


def case_id_for(dispute_id: str) -> str:
    return f"CASE-{dispute_id}"


def summarize_dispute(dispute: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields SwapShield needs from a Disputes v1 resource."""
    transactions = dispute.get("disputed_transactions") or [{}]
    transaction = transactions[0] if transactions else {}
    items = transaction.get("items") or [{}]
    item = items[0] if items else {}
    amount = dispute.get("dispute_amount") or transaction.get("gross_amount") or {}
    buyer_messages = [
        str(message.get("content", ""))
        for message in dispute.get("messages", []) or []
        if message.get("posted_by") == "BUYER"
    ]
    return {
        "dispute_id": str(dispute["dispute_id"]),
        "capture_id": transaction.get("seller_transaction_id"),
        "invoice_id": transaction.get("invoice_number"),
        "reason": dispute.get("reason"),
        "dispute_status": dispute.get("status"),
        "lifecycle_stage": dispute.get("dispute_life_cycle_stage"),
        "amount": amount.get("value"),
        "currency": amount.get("currency_code"),
        "seller_response_due": dispute.get("seller_response_due_date"),
        "item_sku": item.get("item_id"),
        "item_name": item.get("item_name") or item.get("item_description"),
        "buyer_message": buyer_messages[-1][:1000] if buyer_messages else None,
    }


class CaseStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # -- writes --------------------------------------------------------------------
    def upsert_from_dispute(self, dispute: dict[str, Any], *, captured_amount: str | None = None) -> str:
        summary = summarize_dispute(dispute)
        case_id = case_id_for(summary["dispute_id"])
        now = time.time()
        with self._lock:
            existing = self._conn.execute("SELECT case_id FROM cases WHERE case_id = ?", (case_id,)).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE cases SET dispute_status=?, lifecycle_stage=?, seller_response_due=?,
                       buyer_message=?, dispute_json=?, captured_amount=COALESCE(?, captured_amount), updated_at=?
                       WHERE case_id=?""",
                    (
                        summary["dispute_status"],
                        summary["lifecycle_stage"],
                        summary["seller_response_due"],
                        summary["buyer_message"],
                        json.dumps(dispute),
                        captured_amount,
                        now,
                        case_id,
                    ),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cases (case_id, dispute_id, capture_id, invoice_id, reason, dispute_status,
                       lifecycle_stage, amount, currency, captured_amount, seller_response_due, item_sku,
                       item_name, buyer_message, dispute_json, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        case_id,
                        summary["dispute_id"],
                        summary["capture_id"],
                        summary["invoice_id"],
                        summary["reason"],
                        summary["dispute_status"],
                        summary["lifecycle_stage"],
                        summary["amount"],
                        summary["currency"],
                        captured_amount or summary["amount"],
                        summary["seller_response_due"],
                        summary["item_sku"],
                        summary["item_name"],
                        summary["buyer_message"],
                        json.dumps(dispute),
                        now,
                        now,
                    ),
                )
            self._conn.commit()
        return case_id

    def save_verification(self, case_id: str, verification: dict[str, Any], report_pdf: bytes | None) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE cases SET verification_json=?, report_pdf=?, draft_json=NULL,
                   workflow_status=CASE WHEN workflow_status='resolved' THEN 'resolved' ELSE 'verified' END,
                   updated_at=? WHERE case_id=?""",
                (json.dumps(verification), report_pdf, time.time(), case_id),
            )
            self._conn.commit()

    def save_draft(self, case_id: str, draft: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE cases SET draft_json=?,
                   workflow_status=CASE WHEN workflow_status='resolved' THEN 'resolved' ELSE 'drafted' END,
                   updated_at=? WHERE case_id=?""",
                (json.dumps(draft), time.time(), case_id),
            )
            self._conn.commit()

    def set_workflow_status(self, case_id: str, status: str) -> None:
        if status not in WORKFLOW_STATUSES:
            raise ValueError(f"unknown workflow status {status}")
        with self._lock:
            self._conn.execute(
                "UPDATE cases SET workflow_status=?, updated_at=? WHERE case_id=?",
                (status, time.time(), case_id),
            )
            self._conn.commit()

    def mark_resolved(self, case_id: str) -> None:
        self.set_workflow_status(case_id, "resolved")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def log_action(
        self, case_id: str, action: str, *, actor: str, detail: dict[str, Any], request_id: str | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO actions (case_id, action, actor, request_id, detail_json, created_at) VALUES (?,?,?,?,?,?)",
                (case_id, action, actor, request_id, json.dumps(detail), time.time()),
            )
            self._conn.commit()

    # -- reads ---------------------------------------------------------------------
    def _row_to_case(self, row: sqlite3.Row, *, full: bool) -> dict[str, Any]:
        data = {
            key: row[key]
            for key in row.keys()
            if key not in {"dispute_json", "verification_json", "draft_json", "report_pdf"}
        }
        data["has_report"] = row["report_pdf"] is not None
        data["verification"] = json.loads(row["verification_json"]) if row["verification_json"] else None
        data["draft"] = json.loads(row["draft_json"]) if row["draft_json"] else None
        if full:
            data["dispute"] = json.loads(row["dispute_json"])
            data["actions"] = self.actions(row["case_id"])
        return data

    def list_cases(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM cases ORDER BY
                   CASE workflow_status WHEN 'resolved' THEN 1 ELSE 0 END,
                   seller_response_due IS NULL, seller_response_due ASC"""
            ).fetchall()
        return [self._row_to_case(row, full=False) for row in rows]

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return self._row_to_case(row, full=True) if row else None

    def report_pdf(self, case_id: str) -> bytes | None:
        with self._lock:
            row = self._conn.execute("SELECT report_pdf FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        return bytes(row["report_pdf"]) if row and row["report_pdf"] is not None else None

    def actions(self, case_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT action, actor, request_id, detail_json, created_at FROM actions WHERE case_id=? ORDER BY id",
                (case_id,),
            ).fetchall()
        return [
            {
                "action": row["action"],
                "actor": row["actor"],
                "request_id": row["request_id"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def has_executed(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT detail_json FROM actions WHERE request_id=? AND action LIKE 'executed:%' ORDER BY id DESC",
                (request_id,),
            ).fetchone()
        return json.loads(row["detail_json"]) if row else None
