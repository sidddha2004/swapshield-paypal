"""Build service/fixtures/replay/verifications.json from the recorded demo data.

    python scripts/build_replay_fixtures.py

Reads public/demo/demo-data.json (recorded from the real API on a GPU) and the
demo photos next to it, and stores each pair's verification under the pixel
fingerprints of its two photos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.replay import fingerprint  # noqa: E402

DEMO = ROOT / "public" / "demo"
OUT = ROOT / "service" / "fixtures" / "replay" / "verifications.json"


def main() -> None:
    data = json.loads((DEMO / "demo-data.json").read_text(encoding="utf-8"))
    names = {case["case_id"]: case.get("item_name") or case["case_id"] for case in data["cases"]}
    pairs = []
    for case_id, recorded in sorted(data["recorded"].items()):
        verification = dict(recorded["verification"])
        verification.pop("records", None)
        photos = {}
        for side in ("dispatch", "return"):
            path = ROOT / "public" / recorded["photos"][side].lstrip("/")
            with Image.open(path) as image:
                photos[side] = fingerprint(image)
        pairs.append({
            "label": f"{names[case_id]} ({case_id})",
            "photos": {side: recorded["photos"][side].replace("/demo/", "public/demo/", 1) for side in ("dispatch", "return")},
            "dispatch_fingerprint": photos["dispatch"],
            "return_fingerprint": photos["return"],
            "recorded_on": data.get("recorded_at", ""),
            "verification": verification,
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "public/demo/demo-data.json", "pairs": pairs}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(pairs)} pairs to {OUT}")


if __name__ == "__main__":
    main()
