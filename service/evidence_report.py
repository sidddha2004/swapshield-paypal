"""Render a one-page evidence report PDF with Pillow (no extra dependencies).

The report is what SwapShield attaches to PayPal's provide-evidence call: the
dispatch and returned-item photos side by side, the compared records, the
structured observations with evidence IDs, and a neutral statement that a
human reviewer approved the submission.
"""

from __future__ import annotations

import textwrap
import time
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PAGE = (1240, 1754)  # A4 at 150 dpi
MARGIN = 70
INK = (24, 28, 36)
MUTED = (96, 104, 118)
RULE = (214, 218, 226)
ACCENT = (0, 48, 135)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "arialbd.ttf") if bold else ("DejaVuSans.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fit(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    copy = image.convert("RGB").copy()
    copy.thumbnail(box)
    canvas = Image.new("RGB", box, (244, 246, 249))
    canvas.paste(copy, ((box[0] - copy.width) // 2, (box[1] - copy.height) // 2))
    return canvas


def render_evidence_report(
    *,
    case: dict[str, Any],
    verification: dict[str, Any],
    dispatch_image: Image.Image,
    return_image: Image.Image,
    records: dict[str, Any],
) -> bytes:
    page = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(page)
    width = PAGE[0] - 2 * MARGIN
    y = MARGIN

    draw.text((MARGIN, y), "Returned-item inspection report", font=_font(40, True), fill=INK)
    y += 56
    meta = (
        f"PayPal dispute {case.get('dispute_id')}  ·  capture {case.get('capture_id') or 'n/a'}  ·  "
        f"invoice {case.get('invoice_id') or 'n/a'}  ·  generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
    )
    draw.text((MARGIN, y), meta, font=_font(19), fill=MUTED)
    y += 40
    draw.line((MARGIN, y, PAGE[0] - MARGIN, y), fill=RULE, width=2)
    y += 26

    gap = 30
    box = ((width - gap) // 2, 560)
    for index, (label, image, ref) in enumerate(
        (("Dispatched item", dispatch_image, "dispatch_image"), ("Returned item", return_image, "return_image"))
    ):
        x = MARGIN + index * (box[0] + gap)
        draw.text((x, y), f"{label}  [{ref}]", font=_font(22, True), fill=ACCENT)
        page.paste(_fit(image, box), (x, y + 36))
    y += box[1] + 60

    risk = verification.get("risk", {})
    features = verification.get("features", {})
    rows = [
        ("SKU", records.get("dispatch_sku"), records.get("return_sku")),
        ("Serial", records.get("dispatch_serial") or "—", records.get("return_serial") or "—"),
        (
            "Weight (g)",
            records.get("dispatch_weight_grams") or "—",
            records.get("return_weight_grams") or "—",
        ),
    ]
    draw.text((MARGIN, y), "Records compared", font=_font(24, True), fill=INK)
    y += 40
    columns = (MARGIN, MARGIN + 260, MARGIN + 700)
    for header, x in zip(("Field", "Dispatch", "Return"), columns):
        draw.text((x, y), header, font=_font(19, True), fill=MUTED)
    y += 30
    for row in rows:
        for value, x in zip(row, columns):
            draw.text((x, y), str(value), font=_font(20), fill=INK)
        y += 30
    y += 20

    draw.text((MARGIN, y), "Model evidence", font=_font(24, True), fill=INK)
    y += 40
    summary = (
        f"Visual similarity {features.get('vision_similarity', 0):.2f}   ·   "
        f"Vision-language mismatch {features.get('vlm_mismatch', 0):.2f}   ·   "
        f"Image quality {features.get('image_quality', 0):.2f}   ·   "
        f"Calibrated review score {risk.get('score', '—')}/100"
    )
    draw.text((MARGIN, y), summary, font=_font(20), fill=INK)
    y += 38
    observations = (verification.get("vlm_assessment") or {}).get("observations", [])[:4]
    lines = [
        f"• {item.get('attribute')}: dispatched '{item.get('dispatch_value')}', returned "
        f"'{item.get('return_value')}' ({item.get('severity')}) [{', '.join(item.get('evidence_ids', []))}]"
        for item in observations
    ] or ["• No product-level differences were reported by the vision-language comparison."]
    lines += [f"• {reason}" for reason in risk.get("reasons", [])[:3]]
    for line in lines:
        for wrapped in textwrap.wrap(line, width=96):
            draw.text((MARGIN, y), wrapped, font=_font(19), fill=INK)
            y += 28
    y += 24
    draw.line((MARGIN, y, PAGE[0] - MARGIN, y), fill=RULE, width=2)
    y += 20
    statement = (
        "This report documents a comparison between the merchant's dispatch evidence and the item received back. "
        "It is produced by SwapShield AI as decision support: it makes no claim about the buyer's intent, and a "
        "human reviewer approved its submission. PayPal makes the final decision on this dispute."
    )
    for wrapped in textwrap.wrap(statement, width=118):
        draw.text((MARGIN, y), wrapped, font=_font(18), fill=MUTED)
        y += 26

    buffer = BytesIO()
    page.save(buffer, format="PDF", resolution=150.0)
    return buffer.getvalue()
