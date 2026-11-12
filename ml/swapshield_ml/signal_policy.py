"""Single-signal decision policy, selected on validation v2 (docs/EVALUATION.md).

A drop-in alternative to the fusion model's ``score_risk``: the decision uses one
model signal against a validation-chosen threshold. Two rules are kept from the
product's design and sit outside the benchmark, whose images carry no serials
or weights: low image quality always asks for clearer photos, and a serial or
weight mismatch always routes to review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ml.swapshield_ml.contracts import Decision, RiskFeatures, RiskResult

KIND = "swapshield-single-signal-policy"
SIGNALS = {
    # higher score = more likely a substitution
    "vlm_mismatch": lambda features: features.vlm_mismatch,
    "vision_dissimilarity": lambda features: 1.0 - features.vision_similarity,
}
SERIAL_MISMATCH_REVIEW = 0.70
WEIGHT_DELTA_REVIEW = 0.18


class PolicyError(ValueError):
    """The decision-policy artifact is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    signal: str
    threshold: float
    min_image_quality: float
    selected_on: str = ""

    def score_risk(self, features: RiskFeatures) -> RiskResult:
        features.validate()
        score = min(max(float(SIGNALS[self.signal](features)), 0.0), 1.0)
        objective_mismatch = features.serial_mismatch > SERIAL_MISMATCH_REVIEW or features.weight_delta > WEIGHT_DELTA_REVIEW
        if features.image_quality < self.min_image_quality:
            decision = Decision.RECAPTURE
        elif score >= self.threshold or objective_mismatch:
            decision = Decision.REVIEW
        else:
            decision = Decision.APPROVE

        reasons: list[str] = []
        if score >= self.threshold:
            reasons.append("Vision-language verifier found product-level discrepancies"
                           if self.signal == "vlm_mismatch" else "Visual identity is materially different from dispatch evidence")
        if features.serial_mismatch > SERIAL_MISMATCH_REVIEW:
            reasons.append("Serial or model identifier does not match the order record")
        if features.weight_delta > WEIGHT_DELTA_REVIEW:
            reasons.append("Parcel weight differs beyond the configured tolerance")
        if features.image_quality < self.min_image_quality:
            reasons.append("Image quality is below the evidence threshold; capture clearer product photos")
        if not reasons:
            reasons.append("All available return evidence is consistent with dispatch")
        return RiskResult(score, round(score * 100), decision, tuple(reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": KIND,
            "signal": self.signal,
            "decision": {"threshold": self.threshold, "min_image_quality": self.min_image_quality},
            "selected_on": self.selected_on,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SignalPolicy":
        try:
            if raw["schema_version"] != 1 or raw["kind"] != KIND:
                raise PolicyError("unsupported decision-policy artifact")
            if raw["signal"] not in SIGNALS:
                raise PolicyError(f"unknown signal {raw['signal']!r}")
            threshold = float(raw["decision"]["threshold"])
            quality = float(raw["decision"]["min_image_quality"])
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, PolicyError):
                raise
            raise PolicyError("decision-policy artifact is malformed") from exc
        if not (0.0 < threshold < 1.0 and 0.0 <= quality <= 1.0):
            raise PolicyError("decision-policy thresholds are out of range")
        return cls(signal=raw["signal"], threshold=threshold, min_image_quality=quality, selected_on=str(raw.get("selected_on", "")))


def load_signal_policy(path: str | Path) -> SignalPolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError("decision-policy artifact is not valid JSON") from exc
    return SignalPolicy.from_dict(raw)
