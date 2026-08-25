"""Real integration for a friend-hosted Kaggle SDXL+ControlNet floor-plan
model, filling the previously-inert Provider.generate_floor_plan() vendor
slot (app/providers/idealhouse.py stays the default/fallback when this isn't
configured - see HybridProvider's _default_floor_plan_provider()).

IMPORTANT - read before trusting this vendor's output: evaluated live on
2026-08-24 (see CLAUDE.md's "Real DXF/AutoCAD-format export" entry) and found
to produce garbled/hallucinated room-label text and structurally broken
geometry (disconnected rectangles, repeated room bands) - it is still a raw
Stable Diffusion XL + ControlNet image model, NOT a source of real vector/CAD
data, despite the endpoint's name. This module exists so the EXISTING,
already-built "Concept Layout - not a precise blueprint" card (gated on
floor_plan_status == "done", see static/app.js's renderHouseResults()) can
show its output for direct visual inspection on the site - not because the
output has been judged good enough for production use. Treat every image
this returns with the same skepticism as the CLAUDE.md write-up.

Real, CONFIRMED contract (read directly from the friend's notebook code and
live-tested via scripts/test_autocad_kaggle_model.py, not assumed):
    POST {base_url}/generate_floorplans
        {"length": float, "width": float, "unit": str, "floors": int,
         "bedrooms": int, "bathrooms": int, "notes": str}
        -> {"status": "started", "job_id": str}
    GET {base_url}/generate_floorplans/status/{job_id}
        -> {"status": "running"}
        -> {"status": "done", "floors": [{"floor_number", "floor_title",
             "prompt_used", "image_base64" (JPEG)}, ...], ...}
        -> {"status": "failed", "detail": str}

Submit-then-poll is REQUIRED, not a style choice - a plain blocking call
reliably hit Cloudflare's free-tunnel ~100-120s timeout (524) in live
testing, independent of whether the GPU work itself succeeds. See
app/providers/kaggle.py's generate_images_batch() for the same pattern
already established elsewhere in this codebase for the identical reason.

ALL floors' images are returned (floor-ordered), matching
Provider.generate_floor_plan()'s list[bytes] | None contract - the model
generates one image per floor natively (its own notebook loops
`for f in range(1, floors+1)`), so an earlier version of this module that
only kept `floors_out[0]` was silently discarding every floor past the
first for any multi-floor request. Each floor sorted by `floor_number`
before returning, in case the vendor's own list isn't already ordered.
Returned as re-encoded PNG bytes (the model itself returns JPEG) so the
interface's documented "PNG bytes" contract holds exactly, not just
approximately.
"""

import base64
import io
import logging
import re
import time

import httpx
from PIL import Image

from app.config import settings
from app.providers.gemini import _explicit_floor_count

logger = logging.getLogger(__name__)

SUBMIT_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 30
# Generous ceiling - a real 2-floor test took ~75s total; multi-floor
# requests generate sequentially server-side (one SDXL pass per floor), so
# this leaves real headroom for a 4-5 floor request.
POLL_MAX_SECONDS = 480

_BEDROOM_RE = "bedroom"
_BATHROOM_RE = "bathroom"


def _parse_int_before_word(prompt: str, word: str) -> int | None:
    """Same style/purpose as gemini.py's _explicit_floor_count() - only
    returns a count when the composed requirements sentence
    (_compose_house_requirements() in app/main.py) actually states one."""
    match = re.search(rf"(\d+)\s*{word}", prompt or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def generate_floor_plan(plot_description: str | None, dimensions: dict, prompt: str) -> list[bytes] | None:
    base_url = settings.kaggle_autocad_api_url
    if not base_url:
        return None

    floors = _explicit_floor_count(prompt) or 1
    bedrooms = _parse_int_before_word(prompt, _BEDROOM_RE) or 3
    bathrooms = _parse_int_before_word(prompt, _BATHROOM_RE) or 2
    notes = (prompt or "")[:200]

    payload = {
        "length": float(dimensions.get("length") or 40),
        "width": float(dimensions.get("width") or 60),
        "unit": dimensions.get("unit") or "ft",
        "floors": floors,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "notes": notes,
    }

    try:
        submit = httpx.post(
            f"{base_url.rstrip('/')}/generate_floorplans", json=payload, timeout=SUBMIT_TIMEOUT_SECONDS
        )
        submit.raise_for_status()
        job_id = submit.json().get("job_id")
        if not job_id:
            logger.error("unexpected kaggle_autocad submit response shape (no job_id)")
            return None

        status_url = f"{base_url.rstrip('/')}/generate_floorplans/status/{job_id}"
        deadline = time.monotonic() + POLL_MAX_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.error("kaggle_autocad job %s did not complete within %ss", job_id, POLL_MAX_SECONDS)
                return None

            time.sleep(POLL_INTERVAL_SECONDS)
            poll = httpx.get(status_url, timeout=POLL_TIMEOUT_SECONDS)
            poll.raise_for_status()
            job = poll.json()
            status = job.get("status")

            if status == "done":
                floors_out = job.get("floors") or []
                if not floors_out:
                    logger.error("kaggle_autocad job %s reported done with no floors", job_id)
                    return None
                floors_out = sorted(floors_out, key=lambda f: f.get("floor_number", 0))
                png_images = []
                for floor in floors_out:
                    jpeg_bytes = base64.b64decode(floor["image_base64"])
                    image = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    png_images.append(buffer.getvalue())
                return png_images

            if status == "failed":
                logger.error("kaggle_autocad job %s failed: %s", job_id, job.get("detail"))
                return None

            # status == "running" (or any other in-progress value) - keep polling.
    except Exception:
        logger.exception("kaggle_autocad generate_floor_plan failed")
        return None
