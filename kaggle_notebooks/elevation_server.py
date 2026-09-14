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
#            "plot_width": str (opt),   # e.g. "30ft" - context only
#            "theme": str (optional),   # defaults to Modern Luxury Contemporary
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
DEFAULT_FEATURES = (
    "ground floor open car porch with a modern sedan, upper floor glass balcony, "
    "vertical teak wood louvers, black aluminum window frames"
)


def _floor_rules(floors: int):
    """Returns (floor_str, floor_neg, width, height) - the story-count-specific
    prompt fragment, negative-prompt fragment, and canvas aspect ratio. Driven
    by the STRUCTURED floors int the app sends, not parsed from free text."""
    if floors <= 1:
        return (
            "strictly single-story modern luxury bungalow, ground floor only, low profile flat roof",
            "second floor, third floor, multi-story, upper floor, balcony on top",
            1152,
            640,
        )
    if floors == 2:
        return (
            "strictly double-story G+1 luxury modern house, exactly two vertical levels, "
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


def _build_prompt(theme: str, plot_width: str, floors: int, features: str):
    floor_str, floor_neg, w, h = _floor_rules(floors)
    width_frag = f"{plot_width} plot frontage, " if plot_width else ""
    prompt = (
        "street photography, orthographic 2D direct front elevation view of a "
        f"{floor_str}, {width_frag}{theme} style, {features}. Direct eye-level "
        "straight camera shot from across the street curb, perpendicular centered "
        "facade view, tight building framing, clear daytime, sunny lighting with "
        "subtle natural reflections on glass, smooth concrete, dark textured stone, "
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
        # The app's short prompt IS the exterior "features" text. Empty -> the
        # model's own sensible default features.
        features = (payload.get("prompt") or "").strip() or DEFAULT_FEATURES

        seed = payload.get("seed", -1)
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            seed = -1
        if seed == -1:
            seed = random.randint(0, 2147483647)

        prompt, negative_prompt, w, h = _build_prompt(theme, plot_width, floors, features)

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
def _start_cloudflared():
    subprocess.run(
        "wget -q -O cloudflared "
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
        "&& chmod +x cloudflared",
        shell=True,
        check=False,
    )
    proc = subprocess.Popen(
        ["./cloudflared", "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # cloudflared prints the public URL to its log within a few seconds.
    for line in proc.stdout:
        print(line, end="")
        if "trycloudflare.com" in line:
            # Keep the process alive; the URL is now visible above.
            break
    threading.Thread(target=lambda: [print(l, end="") for l in proc.stdout], daemon=True).start()


threading.Thread(target=_start_cloudflared, daemon=True).start()
time.sleep(8)  # give cloudflared a moment to print the URL above

import nest_asyncio  # noqa: E402
import uvicorn  # noqa: E402

nest_asyncio.apply()
print("\nStarting server on :8000 - copy the trycloudflare.com URL above into .env as KAGGLE_HOUSE_API_URL\n")
uvicorn.run(app, host="0.0.0.0", port=8000)
