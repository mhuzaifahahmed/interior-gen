"""Live verification for Phase 1/2 of
future-plans/concept-layout-controlnet-conditioning.md - sends REAL
conditioning images (traced from this repo's own layout_floor() geometry)
to the friend-hosted Kaggle notebook, exactly like
app/providers/kaggle_autocad.py's generate_floor_plan() now does, and saves
the returned floors for visual inspection. Standalone - does not touch the
app's pipeline/DB, does not call OpenAI.

Usage:
    .venv/Scripts/python.exe scripts/test_autocad_kaggle_conditioning.py <tunnel_url>
"""

import base64
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline.conditioning_image import render_conditioning_edge_map
from app.pipeline.floor_layout import layout_floor

POLL_INTERVAL_SECONDS = 5
POLL_MAX_SECONDS = 600

DIMENSIONS = {"length": 40, "width": 60, "unit": "ft"}
ROOMS = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1.2},
    {"name": "Dining Room", "area": 1},
    {"name": "Bedroom 1", "area": 1.3},
    {"name": "Bedroom 2", "area": 1.3},
    {"name": "Bathroom", "area": 0.6},
]


def main(base_url: str) -> None:
    base_url = base_url.rstrip("/")

    rects = layout_floor(ROOMS, DIMENSIONS)
    conditioning_png = render_conditioning_edge_map(rects, DIMENSIONS)
    conditioning_b64 = base64.b64encode(conditioning_png).decode("utf-8")

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "conditioning_sent_floor1.png").write_bytes(conditioning_png)
    print(f"Wrote the conditioning image we're sending to {out_dir / 'conditioning_sent_floor1.png'}")

    params = {
        "length": DIMENSIONS["length"],
        "width": DIMENSIONS["width"],
        "unit": DIMENSIONS["unit"],
        "floors": 1,
        "bedrooms": 2,
        "bathrooms": 1,
        "notes": "modern style",
        "conditioning_images": [conditioning_b64],
    }

    print(f"Submitting job to {base_url}/generate_floorplans (with 1 conditioning image)...")
    submit = httpx.post(f"{base_url}/generate_floorplans", json=params, timeout=30)
    submit.raise_for_status()
    submit_data = submit.json()
    print(f"Submit response: {submit_data}")

    job_id = submit_data.get("job_id")
    if not job_id:
        print("No job_id returned - the notebook's endpoint may not match the expected contract.")
        return

    deadline = time.monotonic() + POLL_MAX_SECONDS
    while True:
        if time.monotonic() > deadline:
            print(f"Gave up after {POLL_MAX_SECONDS}s - job never completed.")
            return

        time.sleep(POLL_INTERVAL_SECONDS)
        poll = httpx.get(f"{base_url}/generate_floorplans/status/{job_id}", timeout=30)
        poll.raise_for_status()
        job = poll.json()
        status = job.get("status")
        print(f"  poll -> status={status}")

        if status == "done":
            for floor in job["floors"]:
                n = floor["floor_number"]
                img_bytes = base64.b64decode(floor["image_base64"])
                out_path = out_dir / f"autocad_conditioning_floor{n}.jpg"
                out_path.write_bytes(img_bytes)
                print(f"  wrote {out_path} ({len(img_bytes)} bytes)")
                print(f"  used_real_geometry: {floor.get('used_real_geometry')}")
                print(f"  prompt used for floor {n}: {floor.get('prompt_used')}")
            print("\nDone. Compare autocad_conditioning_floor1.jpg against conditioning_sent_floor1.png")
            print("to confirm the AI actually traced our real geometry instead of inventing its own.")
            return

        if status == "failed":
            print(f"Job failed: {job.get('detail')}")
            return


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_autocad_kaggle_conditioning.py <tunnel_url>")
        sys.exit(1)
    main(sys.argv[1])
