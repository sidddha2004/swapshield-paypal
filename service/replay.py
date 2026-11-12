"""Replay mode: run the real API without a GPU.

With ``SWAPSHIELD_PIPELINE=replay`` the API does everything it normally does
(PayPal mock or sandbox calls, the resolution agent, the hard rules, evidence
PDFs, alerts) except run the two vision models. For the demo photo pairs it
returns the verification the real models produced for them, recorded on a GPU.
Any other photo is refused with a clear message: replay never invents a result.

Pairs are matched by a fingerprint of the decoded pixels, so a renamed copy of a
demo photo still matches, and a different photo never does.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image

DEFAULT_PATH = Path(__file__).resolve().parent / "fixtures" / "replay" / "verifications.json"


def fingerprint(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    digest = hashlib.sha256(f"{rgb.width}x{rgb.height}:".encode())
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def replay_enabled() -> bool:
    return os.getenv("SWAPSHIELD_PIPELINE", "").strip().lower() == "replay"


class ReplayStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.pairs: dict[tuple[str, str], dict[str, Any]] = {
            (item["dispatch_fingerprint"], item["return_fingerprint"]): item for item in raw["pairs"]
        }

    def lookup(self, dispatch: Image.Image, returned: Image.Image) -> dict[str, Any] | None:
        item = self.pairs.get((fingerprint(dispatch), fingerprint(returned)))
        if item is None:
            return None
        verification = copy.deepcopy(item["verification"])
        verification["replay"] = {"pair": item["label"], "recorded_on": item["recorded_on"]}
        return verification

    def labels(self) -> list[str]:
        return sorted(item["label"] for item in self.pairs.values())
