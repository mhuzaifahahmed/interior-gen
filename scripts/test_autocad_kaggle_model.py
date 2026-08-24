"""Standalone test for the friend-hosted Kaggle "AutoCAD/floor plan" model -
NOT wired into this app's pipeline (settings.kaggle_autocad_api_url is
deliberately inert, see app/config.py). This script hits it directly via
plain HTTP, independent of the elevation/house-render model, so it never
touches OpenAI or the Kaggle house-render endpoint.

Submit-then-poll, matching the notebook's fixed contract (2026-08-24):
    POST {BASE_URL}/generate_floorplans -> {"status": "started", "job_id": str}
    GET  {BASE_URL}/generate_floorplans/status/{job_id}
        -> {"status": "running"}
        -> {"status": "done", "plot_size", "total_floors", "floors": [...]}
        -> {"status": "failed", "detail": str}

Usage:
    .venv/Scripts/python.exe scripts/test_autocad_kaggle_model.py <tunnel_url>

Saves each floor's returned image (still a raster JPEG, per the notebook's
own code - NOT real vector/DXF data) to scripts/output/ for visual review.
"""

import base64
import sys
import time
from pathlib import Path

import httpx

POLL_INTERVAL_SECONDS = 5
POLL_MAX_SECONDS = 600  # generous - multi-floor SDXL generation can take a while

TEST_PARAMS = {
    "length": 40,
    "width": 60,
    "unit": "ft",
    "floors": 2,
    "bedrooms": 3,
    "bathrooms": 2,
    "notes": "garage, modern style",
}


def main(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    print(f"Submitting job to {base_url}/generate_floorplans ...")
    print(f"Params: {TEST_PARAMS}")

    submit = httpx.post(f"{base_url}/generate_floorplans", json=TEST_PARAMS, timeout=30)
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
            out_dir = Path(__file__).resolve().parent / "output"
            out_dir.mkdir(exist_ok=True)
            for floor in job["floors"]:
                n = floor["floor_number"]
                img_bytes = base64.b64decode(floor["image_base64"])
                out_path = out_dir / f"autocad_model_floor{n}.jpg"
                out_path.write_bytes(img_bytes)
                print(f"  wrote {out_path} ({len(img_bytes)} bytes)")
                print(f"  prompt used for floor {n}: {floor['prompt_used']}")
            print("\nDone. These are raster JPEGs (per the notebook's own output format) -")
            print("open them to visually judge quality, but they are NOT real DXF/vector data.")
            return

        if status == "failed":
            print(f"Job failed: {job.get('detail')}")
            return


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_autocad_kaggle_model.py <tunnel_url>")
        sys.exit(1)
    main(sys.argv[1])
