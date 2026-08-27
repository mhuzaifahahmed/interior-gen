"""Live verification for Phase 2 of
future-plans/concept-layout-controlnet-conditioning.md - calls the REAL
app.providers.kaggle_autocad.generate_floor_plan() (the actual code path the
app's pipeline uses), so both the real-geometry ControlNet conditioning
(Phase 1) AND the accurate room-label compositing (Phase 2) run exactly as
they do in production. Standalone - does not touch the app's DB/pipeline,
does not call OpenAI.

Usage:
    .venv/Scripts/python.exe scripts/test_autocad_kaggle_labels.py <tunnel_url>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.providers.kaggle_autocad import generate_floor_plan

DIMENSIONS = {"length": 40, "width": 60, "unit": "ft"}
ROOM_LAYOUT = {
    "floors": [
        {
            "floor_number": 1,
            "rooms": [
                {"name": "Living Room", "area": 3},
                {"name": "Kitchen", "area": 1.2},
                {"name": "Dining Room", "area": 1},
                {"name": "Bedroom 1", "area": 1.3},
                {"name": "Bedroom 2", "area": 1.3},
                {"name": "Bathroom", "area": 0.6},
            ],
        }
    ]
}


def main(base_url: str) -> None:
    settings.kaggle_autocad_api_url = base_url

    result = generate_floor_plan(
        "a rectangular plot",
        DIMENSIONS,
        "1 floor, 2 bedrooms, 1 bathroom, modern style",
        room_layout=ROOM_LAYOUT,
    )
    if result is None:
        print("generate_floor_plan() returned None - check the logged exception above.")
        return

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    for i, image_bytes in enumerate(result, start=1):
        out_path = out_dir / f"autocad_with_labels_floor{i}.png"
        out_path.write_bytes(image_bytes)
        print(f"wrote {out_path} ({len(image_bytes)} bytes)")

    print("\nDone. This is exactly what the app's own pipeline produces for the")
    print("'Concept Layout' card - real traced geometry + our own composited labels.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_autocad_kaggle_labels.py <tunnel_url>")
        sys.exit(1)
    main(sys.argv[1])
