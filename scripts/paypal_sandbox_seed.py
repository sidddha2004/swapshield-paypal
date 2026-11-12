"""Create real PayPal *sandbox* orders to dispute during a demo.

Usage (sandbox credentials in .env, PAYPAL_ENV=sandbox):

    python -m scripts.paypal_sandbox_seed create            # prints approval links
    # open each link, log in with your sandbox *personal* (buyer) account, approve
    python -m scripts.paypal_sandbox_seed capture ORDER_ID  # captures, prints capture ID

Then, as the sandbox buyer on https://www.sandbox.paypal.com, open the Resolution
Center and report "item not as described" for that payment. Click "Sync disputes"
in the dashboard (or let the CUSTOMER.DISPUTE.CREATED webhook arrive).

Refuses to run against live PayPal.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from service.paypal_client import PayPalClient


DEMO_ITEMS = [
    {"sku": "MSE-RGB-102", "name": "RGB wired gaming mouse", "value": "39.99"},
    {"sku": "TBL-WAL-412", "name": "Walnut side table", "value": "149.00"},
    {"sku": "LMP-CER-077", "name": "Ceramic table lamp", "value": "64.50"},
]


def client() -> PayPalClient:
    if os.getenv("PAYPAL_ENV", "sandbox").strip().lower() != "sandbox":
        sys.exit("Refusing to seed: PAYPAL_ENV must be 'sandbox'.")
    return PayPalClient(
        client_id=os.getenv("PAYPAL_CLIENT_ID", ""),
        client_secret=os.getenv("PAYPAL_CLIENT_SECRET", ""),
        environment="sandbox",
    )


def create() -> None:
    gateway = client()
    for item in DEMO_ITEMS:
        invoice = f"SWAP-{uuid.uuid4().hex[:8].upper()}"
        order = gateway._call(
            "POST",
            "/v2/checkout/orders",
            body={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": item["sku"],
                        "invoice_id": invoice,
                        "amount": {
                            "currency_code": "USD",
                            "value": item["value"],
                            "breakdown": {"item_total": {"currency_code": "USD", "value": item["value"]}},
                        },
                        "items": [
                            {
                                "name": item["name"],
                                "sku": item["sku"],
                                "quantity": "1",
                                "category": "PHYSICAL_GOODS",
                                "unit_amount": {"currency_code": "USD", "value": item["value"]},
                            }
                        ],
                    }
                ],
                "payment_source": {
                    "paypal": {
                        "experience_context": {
                            "brand_name": "SwapShield Demo Store",
                            "shipping_preference": "GET_FROM_FILE",
                            "user_action": "PAY_NOW",
                            "return_url": "https://example.com/return",
                            "cancel_url": "https://example.com/cancel",
                        }
                    }
                },
            },
            request_id=f"seed-{invoice}",
        )
        approve = next((link["href"] for link in order.get("links", []) if link.get("rel") == "payer-action"), None)
        print(json.dumps({"order_id": order["id"], "sku": item["sku"], "invoice": invoice, "approve_url": approve}))


def capture(order_id: str) -> None:
    gateway = client()
    result = gateway._call("POST", f"/v2/checkout/orders/{order_id}/capture", body={}, request_id=f"capture-{order_id}")
    captures = result["purchase_units"][0]["payments"]["captures"]
    for item in captures:
        print(json.dumps({"order_id": order_id, "capture_id": item["id"], "status": item["status"], "amount": item["amount"]}))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "create":
        create()
    elif len(sys.argv) >= 3 and sys.argv[1] == "capture":
        capture(sys.argv[2])
    else:
        sys.exit(__doc__)
