import json
import tempfile
import unittest
from pathlib import Path

from ml.swapshield_ml.contracts import Decision, RiskFeatures
from ml.swapshield_ml.signal_policy import PolicyError, SignalPolicy, load_signal_policy


def features(**overrides) -> RiskFeatures:
    values = {"vision_similarity": 0.9, "vlm_mismatch": 0.05, "serial_mismatch": 0.0, "weight_delta": 0.0, "image_quality": 0.9}
    values.update(overrides)
    return RiskFeatures(**values)


POLICY = SignalPolicy(signal="vlm_mismatch", threshold=0.9, min_image_quality=0.46)


class SignalPolicyTests(unittest.TestCase):
    def test_low_mismatch_is_approved(self) -> None:
        self.assertEqual(POLICY.score_risk(features()).decision, Decision.APPROVE)

    def test_mismatch_at_threshold_goes_to_review(self) -> None:
        result = POLICY.score_risk(features(vlm_mismatch=0.9))
        self.assertEqual(result.decision, Decision.REVIEW)
        self.assertEqual(result.score, 90)

    def test_quality_gate_wins(self) -> None:
        self.assertEqual(POLICY.score_risk(features(vlm_mismatch=0.95, image_quality=0.3)).decision, Decision.RECAPTURE)

    def test_objective_mismatch_still_reviews(self) -> None:
        self.assertEqual(POLICY.score_risk(features(serial_mismatch=1.0)).decision, Decision.REVIEW)
        self.assertEqual(POLICY.score_risk(features(weight_delta=0.3)).decision, Decision.REVIEW)

    def test_round_trip_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(POLICY.to_dict()), encoding="utf-8")
            self.assertEqual(load_signal_policy(path), POLICY)
            path.write_text(json.dumps({**POLICY.to_dict(), "signal": "astrology"}), encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_signal_policy(path)


if __name__ == "__main__":
    unittest.main()
