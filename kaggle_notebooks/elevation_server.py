# ============================================================
# Build a House - Front Elevation FastAPI server (Kaggle)
# ============================================================
# Version-controlled REFERENCE COPY of what gets pasted into the Kaggle
# notebook cell for the "Build a House" exterior elevation model. Kaggle
# notebooks are edited in Kaggle's own UI (not run from this repo), but - unlike
# the friend-hosted AutoCAD notebook, which was never version-controlled and got
# lost/rediscovered more than once - this copy lives in the repo so the exact
# contract the app depends on is never guessed.
#
# WHAT THIS IS: RealVisXL_V4.0 (SDXL) TEXT-TO-IMAGE - it generates a
# photorealistic front elevation from scratch. It takes NO input image (it is
# NOT img2img), so the app's plot photo is irrelevant here (and optional).
#
# CONTRACT the app (app/providers/kaggle.py's generate_house_render) calls:
#   POST /generate_elevation
#     body: {"prompt": str,            # SHORT user exterior-features text (may be "")
#            "floors": int (optional),  # 1..3 - structured story count
#            "garage": bool (optional), # True -> include car porch; False -> exclude; omit -> no signal
#            "color": str (optional),   # e.g. "clay, terracotta, olive green, ..." - resolved
#                                        # COLOR_PROFILE words for the user's chosen exterior
#                                        # palette (app/pipeline/house_prompts.py's
#                                        # color_palette_words()); omit -> no color signal, the
#                                        # model picks its own colors (2026-09-18, v15 - see this
#                                        # repo's CLAUDE.md for why this is structured, not
#                                        # embedded in `prompt`: a trailing text clause was too
#                                        # weak against this notebook's own hardcoded color
#                                        # vocabulary in _build_prompt below)
#            "style": str (optional),   # e.g. "Mediterranean villa architecture, stucco plaster
#                                        # walls, terracotta clay tile pitched roof, arched
#                                        # openings, ..." - resolved HOUSE_STYLE_PROFILES words
#                                        # for the user's chosen exterior architectural style
#                                        # (app/pipeline/house_prompts.py's house_style_words());
#                                        # omit -> no style signal, the model keeps its default
#                                        # Modern Luxury Contemporary look (2026-09-18, v16 - same
#                                        # structured-field reasoning as "color" above: this
#                                        # notebook is hardcoded around a modern-luxury look in
#                                        # several places, which would fight any other style the
#                                        # same way "dark textured stone" fought light palettes -
#                                        # see _build_prompt/_floor_rules below)
#            "plot_width": str (opt),   # e.g. "30ft" - context only
#            "theme": str (optional),   # defaults to Modern Luxury Contemporary; only used as a
#                                        # fallback label when no "style" is given (see _build_prompt)
#            "seed": int (optional)}    # -1 / omitted -> random
#     -> {"status": "started", "job_id": str}
#   GET /generate_elevation/status/{job_id}
#     -> {"status": "running"}
#      | {"status": "done", "generated_image_base64": <PNG base64>}
#      | {"status": "failed", "detail": str}
#
# SUBMIT-THEN-POLL (not one blocking call): the free Cloudflare quick tunnel
# hard-kills any request open past ~100s with a 524, and a 28-step RealVisXL
# render on a T4 (plus a possible cold model load) can exceed that. The submit
# and each poll are near-instant; generation runs on a background thread.
#
# The APP sends only a SHORT prompt + a structured floor count - ALL the heavy
# prompt scaffolding (camera framing, photoreal vocabulary, per-floor-count
# aspect-ratio + negative-prompt rules) lives HERE, so the app side stays
# minimal and low-error (same split as room-redesign's build_kaggle_prompt).
#
# HOW TO RUN IN KAGGLE:
#   1. Enable GPU (T4) in the notebook settings, and enable Internet.
#   2. Paste this whole file into one cell and run it.
#   3. It prints a public https URL (the Cloudflare tunnel). Copy it into this
#      repo's .env as KAGGLE_HOUSE_API_URL=..., and set HOUSE_IMAGE_PROVIDER=kaggle.
#   4. The tunnel URL changes every time the notebook session restarts - update
#      .env each time (same as the room-redesign KAGGLE_API_URL).
# ============================================================

import gc
import subprocess
import threading
import time
import uuid
import base64
import random
from io import BytesIO

import torch
from diffusers import DPMSolverMultistepScheduler, StableDiffusionXLPipeline

# ---- 1. Dependencies (Kaggle images usually have torch/diffusers already) ----
subprocess.run(
    "pip install -q fastapi 'uvicorn[standard]' nest-asyncio pyngrok",
    shell=True,
    check=False,
)

# ---- 2. Load the model once (kept warm for the whole session) ----
torch.cuda.empty_cache()
gc.collect()

MODEL_ID = "SG161222/RealVisXL_V4.0"
print(f"Loading {MODEL_ID} ...")

pipe = StableDiffusionXLPipeline.from_pretrained(
    MODEL_ID, torch_dtype=torch.float16, variant="fp16", use_safetensors=True
)
pipe.enable_model_cpu_offload()  # T4 VRAM headroom
pipe.scheduler = DPMSolverMultistepScheduler.from_config(
    pipe.scheduler.config, algorithm_type="dpmsolver++", use_karras_sigmas=True
)
print("Model ready.")

# ---- 3. Prompt scaffolding (owned here, NOT in the app) ----
DEFAULT_THEME = "Modern Luxury Contemporary"
# Garage-NEUTRAL default features (real user report 2026-09: a garage appeared
# in the render when none was asked for). A car porch is added ONLY when the
# app's structured `garage` flag says so - see _build_prompt below.
DEFAULT_FEATURES = (
    "upper floor glass balcony, vertical teak wood louvers, "
    "black aluminum window frames, landscaped front entrance"
)
GARAGE_FEATURE = "ground floor open car porch with a modern sedan"
# When the user did NOT ask for a garage, actively steer the model away from
# one (RealVisXL otherwise biases toward putting a garage on any modern-luxury
# facade - a positive-prompt omission alone doesn't stop it).
NO_GARAGE_NEGATIVE = "garage, car porch, carport, driveway, parked car, vehicle, garage door"


def _floor_rules(floors: int):
    """Returns (floor_str, floor_neg, width, height) - the story-count-specific
    prompt fragment, negative-prompt fragment, and canvas aspect ratio. Driven
    by the STRUCTURED floors int the app sends, not parsed from free text.

    De-biased (2026-09-18, v16): these fragments used to assert "modern
    luxury"/"flat roof" regardless of the chosen style (e.g. "strictly
    single-story MODERN LUXURY bungalow... LOW PROFILE FLAT ROOF") - that
    fought any non-modern `style` the same way the old hardcoded "dark
    textured stone" fought light color palettes, since a Mediterranean or
    Traditional house doesn't have a flat roof. Now purely about STORY COUNT,
    not aesthetic - the aesthetic comes entirely from the style/theme slot in
    _build_prompt() below. The no-style default render is unaffected in
    practice, since DEFAULT_THEME ("Modern Luxury Contemporary") still fills
    that slot when no style is chosen."""
    if floors <= 1:
        return (
            "strictly single-story house, ground floor only, single level",
            "second floor, third floor, multi-story, upper floor, balcony on top",
            1152,
            640,
        )
    if floors == 2:
        return (
            "strictly double-story G+1 house, exactly two vertical levels, "
            "ground floor and first floor only",
            "single story, 1 floor, 3 floors, triple story, 4 floors, high-rise",
            1024,
            1024,
        )
    return (
        "three-story G+2 residential townhouse, exactly three vertical levels",
        "single story, 1 floor, 2 floors, 4 floors, skyscraper",
        896,
        1152,
    )


def _build_prompt(theme: str, plot_width: str, floors: int, features: str, garage, color: str = None, style: str = None):
    """garage: True -> include a car porch; False -> actively exclude one;
    None -> no signal (old app/backward-compat), leave the model to decide.

    color (2026-09-18, v15): the app's resolved COLOR_PROFILE words for the
    user's chosen exterior palette (e.g. "clay, terracotta, olive green, warm
    brown, sand, stone, and natural wood tones"), or None/empty for "no
    preference". Given a DEDICATED, front-loaded slot right after the theme
    and before the free-text features - the same "never-cut priority slot"
    treatment the room-redesign app gives color in build_kaggle_prompt(),
    mirrored here on the notebook side since this model owns its own
    scaffolding. When color IS given, the trailing hardcoded material clause
    drops "dark" from "dark textured stone" (kept generic "textured stone")
    so a light palette isn't fought by a baked-in dark material word.

    style (2026-09-18, v16): the app's resolved HOUSE_STYLE_PROFILES words
    for the user's chosen exterior architectural style (e.g. "Mediterranean
    villa architecture, stucco plaster walls, terracotta clay tile pitched
    roof, arched windows and doorways, wrought-iron balconies"), or
    None/empty for "no preference". When given, it REPLACES the "{theme}
    style" fragment entirely (rather than being appended alongside it) - the
    app-sent style descriptor becomes the dedicated aesthetic slot, same
    front-loaded priority treatment as color. When style is None/empty, the
    `theme` fragment fills that slot exactly as before (backward-compatible -
    an un-updated app/request keeps the old "Modern Luxury Contemporary
    style" behavior)."""
    floor_str, floor_neg, w, h = _floor_rules(floors)
    width_frag = f"{plot_width} plot frontage, " if plot_width else ""

    # Compose the exterior features. When a garage IS requested, ensure a car
    # porch is present even if the user's short features text didn't spell one
    # out; when it's explicitly NOT requested, don't add one.
    if garage is True and "porch" not in features.lower() and "garage" not in features.lower():
        features = f"{features}, {GARAGE_FEATURE}" if features else GARAGE_FEATURE

    color = (color or "").strip()
    color_frag = f"exterior color palette of {color}, facade finished in {color}, " if color else ""
    stone_word = "textured stone" if color else "dark textured stone"

    style = (style or "").strip()
    style_frag = f"{style}, " if style else f"{theme} style, "

    # A style with no features text (empty `prompt` + a style chosen, see
    # _run_job) would otherwise leave a dangling ", ." right before "Direct
    # eye-level..." - strip the trailing separator so the sentence still
    # reads cleanly either way.
    descriptor = f"{style_frag}{color_frag}{features}".strip().rstrip(", ")

    prompt = (
        "street photography, orthographic 2D direct front elevation view of a "
        f"{floor_str}, {width_frag}{descriptor}. Direct eye-level "
        "straight camera shot from across the street curb, perpendicular centered "
        "facade view, tight building framing, clear daytime, sunny lighting with "
        f"subtle natural reflections on glass, smooth concrete, {stone_word}, "
        "wood textures, Hasselblad 8k architectural photograph, pristine "
        "photorealistic details, crisp straight lines"
    )
    negative_prompt = (
        f"{floor_neg}, backyard, patio, swimming pool, courtyard, garden lounge, "
        "interior room view, 3/4 angle, side perspective view, corner shot, tilted "
        "camera, fish-eye distortion, tall solid boundary wall blocking porch, "
        "closed security gate hiding ground floor, massive green lawn covering "
        "bottom half, driveway taking over frame, blurry, CGI cartoon, 3d render "
        "sketch, oversaturated, text, labels, watermark"
    )
    if garage is False:
        negative_prompt = f"{NO_GARAGE_NEGATIVE}, {negative_prompt}"
    return prompt, negative_prompt, w, h


# ---- 4. Job store + background worker (submit-then-poll) ----
_jobs = {}
_jobs_lock = threading.Lock()
# Only one GPU render at a time - a single SDXL pipe is not safe under
# concurrent calls (same lesson as the room-redesign notebook).
_gpu_lock = threading.Lock()


def _run_job(job_id: str, payload: dict):
    try:
        theme = (payload.get("theme") or DEFAULT_THEME).strip() or DEFAULT_THEME
        plot_width = (payload.get("plot_width") or "").strip()
        try:
            floors = int(payload.get("floors") or 2)
        except (TypeError, ValueError):
            floors = 2
        floors = max(1, min(3, floors))
        style = (payload.get("style") or "").strip() or None  # resolved HOUSE_STYLE_PROFILES words, or None
        # The app's short prompt IS the exterior "features" text. Empty ->
        # falls back to DEFAULT_FEATURES ONLY when no style was chosen - those
        # defaults ("glass balcony, black aluminum window frames") are modern-
        # specific and would fight a chosen non-modern style (e.g.
        # Mediterranean) the same way the old hardcoded "dark textured stone"
        # fought light color palettes. With a real style, an empty features
        # string is left empty - the style descriptor alone carries the look.
        features = (payload.get("prompt") or "").strip() or ("" if style else DEFAULT_FEATURES)

        seed = payload.get("seed", -1)
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = -1
        if seed == -1:
            seed = random.randint(0, 2147483647)

        garage = payload.get("garage", None)  # True / False / None (no signal)
        color = (payload.get("color") or "").strip() or None  # resolved COLOR_PROFILE words, or None
        prompt, negative_prompt, w, h = _build_prompt(theme, plot_width, floors, features, garage, color, style)

        with _gpu_lock:
            generator = torch.Generator("cuda").manual_seed(seed)
            image = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=w,
                height=h,
                num_inference_steps=28,
                guidance_scale=5.5,
                generator=generator,
            ).images[0]

        buf = BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        with _jobs_lock:
            _jobs[job_id] = {"status": "done", "generated_image_base64": b64}
    except Exception as exc:  # noqa: BLE001 - report any failure back to the poller
        with _jobs_lock:
            _jobs[job_id] = {"status": "failed", "detail": str(exc)}


# ---- 5. FastAPI app ----
from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI()


class ElevationRequest(BaseModel):
    prompt: str = ""
    floors: int | None = None
    plot_width: str | None = None
    theme: str | None = None
    seed: int | None = -1
    garage: bool | None = None  # True -> include car porch; False -> exclude; None -> no signal
    color: str | None = None  # resolved COLOR_PROFILE words for the exterior palette; None -> no signal
    style: str | None = None  # resolved HOUSE_STYLE_PROFILES words for the architectural style; None -> no signal


@app.get("/")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.post("/generate_elevation")
def generate_elevation(req: ElevationRequest):
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "running"}
    threading.Thread(target=_run_job, args=(job_id, req.dict()), daemon=True).start()
    return {"status": "started", "job_id": job_id}


@app.get("/generate_elevation/status/{job_id}")
def elevation_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return {"status": "failed", "detail": "unknown job_id"}
    return job


# ---- 6. Public tunnel (Cloudflare quick tunnel, no account/token needed) ----
#
# IMPORTANT, real bug fixed here (2026-09): this used to start cloudflared and
# scan for the URL on a background daemon thread. Jupyter/ipykernel only
# reliably routes print() output into the cell's visible output when it comes
# from the notebook's MAIN execution thread - prints issued from a spawned
# thread frequently vanish (a well-documented ipykernel behavior, not a bug in
# this script's logic). That's why the AutoCAD notebook's URL always showed up
# and this one didn't: this was the one script in this repo doing tunnel
# detection off-thread. Fixed by running cloudflared + URL detection
# synchronously on the MAIN thread, with explicit flush=True and a loud
# banner, before ever starting uvicorn.
import re  # noqa: E402

subprocess.run(
    "wget -q -O cloudflared "
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
    "&& chmod +x cloudflared",
    shell=True,
    check=False,
)

_cf_proc = subprocess.Popen(
    ["./cloudflared", "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

_TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
_tunnel_url = None
_deadline = time.monotonic() + 30  # cloudflared normally prints the URL within a few seconds
for _line in _cf_proc.stdout:
    print(_line, end="", flush=True)
    _match = _TUNNEL_URL_RE.search(_line)
    if _match:
        _tunnel_url = _match.group(0)
        break
    if time.monotonic() > _deadline:
        print("WARNING: no trycloudflare.com URL seen within 30s - see raw log above.", flush=True)
        break

if _tunnel_url:
    print(
        "\n"
        + "=" * 72
        + f"\nPUBLIC URL: {_tunnel_url}\n"
        + "Copy this into .env as KAGGLE_HOUSE_API_URL and restart your local server.\n"
        + "=" * 72
        + "\n",
        flush=True,
    )
else:
    print(
        "\nERROR: could not detect a trycloudflare.com URL from cloudflared's output.\n"
        "Check the raw log above for what went wrong (e.g. a download/network failure).\n",
        flush=True,
    )

# Drain the rest of cloudflared's log on a background thread so its stdout
# pipe never fills up and blocks the process - we don't need to print these
# further lines (the URL is already shown above), just keep reading them.
threading.Thread(target=lambda: [None for _ in _cf_proc.stdout], daemon=True).start()

import nest_asyncio  # noqa: E402
import uvicorn  # noqa: E402
import asyncio  # noqa: E402

# NOTE (2026-09): plain uvicorn.run() failed inside Kaggle's already-running
# event loop (nest_asyncio patches the loop but uvicorn.run() still tries to
# create/manage its own) - user found the real fix live: build the
# Config/Server explicitly and drive it via asyncio.get_event_loop() so it
# reuses the existing (nest_asyncio-patched) loop instead of fighting it.
config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
server = uvicorn.Server(config)

nest_asyncio.apply()
print("Starting server on :8000 ...\n", flush=True)
asyncio.get_event_loop().run_until_complete(server.serve())
