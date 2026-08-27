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
         "bedrooms": int, "bathrooms": int, "notes": str,
         "conditioning_images": [<base64 png>, ...]}  # optional, see below
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

REAL-GEOMETRY CONTROLNET CONDITIONING (2026-08-27, Phase 1 of
future-plans/concept-layout-controlnet-conditioning.md): the model's real
flaw wasn't its aesthetic, it was that its own create_plot_boundary() only
ever drew the plot's outer rectangle as the ControlNet conditioning image -
no interior walls - so ControlNet only constrained the outer edge and the
model hallucinated every room at random, ignoring the actual room program
entirely. When `room_layout` is given (the same structured room list the
deterministic blueprint step already computes - see
app/pipeline/generate_house.py, which now runs that step BEFORE calling this
so the layout exists in time), this module renders one white-lines-on-black
edge map PER FLOOR from the EXACT SAME rectangles
(app/pipeline/floor_layout.py's layout_floor(), pure/deterministic - calling
it again here on the same inputs always reproduces the identical rects the
blueprint PNG used) via app/pipeline/conditioning_image.py, and sends them as
`conditioning_images` in the request. This is additive to the contract - the
notebook falls back to its own create_plot_boundary() when the field is
omitted, so this stays fully backward compatible with a not-yet-updated
notebook. When conditioning images ARE sent, this module also composites our
own accurate room-name labels onto each returned image afterward (Phase 2) -
see _composite_room_labels() - since the AI output is asked to stay
deliberately TEXT-FREE now that real geometry drives it, and our labels are
guaranteed to land in the right place because both the conditioning image and
the label positions are computed from the exact same rects via the exact same
placement math (app/pipeline/conditioning_image.py's plot_to_canvas_box()).
"""

import base64
import io
import logging
import re
import time

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.pipeline.conditioning_image import CANVAS_SIZE, plot_to_canvas_box, render_conditioning_edge_map
from app.pipeline.floor_layout import layout_floor
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


def _conditioning_images_by_floor(room_layout: dict | None, dimensions: dict) -> dict[int, list[dict]]:
    """Returns {floor_number: rects} for every floor room_layout describes,
    using the SAME pure layout_floor() call the deterministic blueprint step
    already used on this same room_layout - guaranteed to reproduce identical
    rectangles (same inputs, same deterministic function), so the conditioning
    image and the blueprint PNG always agree on where every room lands."""
    if not room_layout or not room_layout.get("floors"):
        return {}
    by_floor: dict[int, list[dict]] = {}
    for floor in room_layout["floors"]:
        floor_number = floor.get("floor_number")
        if floor_number is None:
            continue
        by_floor[floor_number] = layout_floor(floor.get("rooms") or [], dimensions)
    return by_floor


def _composite_room_labels(image: Image.Image, rects: list[dict], dimensions: dict) -> Image.Image:
    """Draws each room's name at its rectangle's CENTER (robust to the small
    wall-position jitter ControlNet can still introduce - never on a wall/
    edge, where jitter would misalign a label). Position is computed as a
    FRACTION of the conditioning canvas, then scaled to the actual returned
    image size, so this stays correct regardless of the exact resolution the
    model returns. White text with a black outline so it stays legible over
    whatever color the AI happened to render at that point."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    x0, y0, box_w, box_h = plot_to_canvas_box(length, width)

    draw = ImageDraw.Draw(image)
    font_size = max(14, image.width // 32)
    font = ImageFont.load_default(size=font_size)
    scale_x = image.width / CANVAS_SIZE
    scale_y = image.height / CANVAS_SIZE

    for rect in rects:
        center_x = x0 + (rect["x"] + rect["w"] / 2) / length * box_w
        center_y = y0 + (rect["y"] + rect["h"] / 2) / width * box_h
        px, py = center_x * scale_x, center_y * scale_y

        text = str(rect.get("name") or "")
        if not text:
            continue
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        origin = (px - tw / 2, py - th / 2)
        for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            draw.text((origin[0] + dx, origin[1] + dy), text, fill="black", font=font)
        draw.text(origin, text, fill="white", font=font)

    return image


def generate_floor_plan(
    plot_description: str | None,
    dimensions: dict,
    prompt: str,
    room_layout: dict | None = None,
) -> list[bytes] | None:
    base_url = settings.kaggle_autocad_api_url
    if not base_url:
        return None

    floors = _explicit_floor_count(prompt) or 1
    bedrooms = _parse_int_before_word(prompt, _BEDROOM_RE) or 3
    bathrooms = _parse_int_before_word(prompt, _BATHROOM_RE) or 2
    notes = (prompt or "")[:200]

    rects_by_floor = _conditioning_images_by_floor(room_layout, dimensions)

    payload = {
        "length": float(dimensions.get("length") or 40),
        "width": float(dimensions.get("width") or 60),
        "unit": dimensions.get("unit") or "ft",
        "floors": floors,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "notes": notes,
    }
    if rects_by_floor:
        # Floor-ordered, matching the vendor's own per-floor loop - real
        # geometry beats the notebook's own hardcoded empty-rectangle
        # fallback (see module docstring). Sent only when we actually have a
        # real layout; omitting the field keeps a not-yet-updated notebook
        # working exactly as before (its own create_plot_boundary() fallback).
        payload["conditioning_images"] = [
            base64.b64encode(render_conditioning_edge_map(rects_by_floor[floor_number], dimensions)).decode(
                "utf-8"
            )
            for floor_number in sorted(rects_by_floor)
        ]

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
                    # Phase 2: composite our own accurate room labels onto the
                    # (deliberately text-free, per the updated negative prompt)
                    # AI image - only possible for floors we actually sent a
                    # real conditioning image for, since label placement uses
                    # those same rects. See _composite_room_labels()'s docstring.
                    rects = rects_by_floor.get(floor.get("floor_number"))
                    if rects:
                        image = _composite_room_labels(image, rects, dimensions)
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
