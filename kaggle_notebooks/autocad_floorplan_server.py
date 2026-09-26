# ============================================================
# AI Concept Layout - Floor-plan FastAPI server (Kaggle)
# ============================================================
# Version-controlled REFERENCE COPY of the friend-hosted AutoCAD/Concept-Layout
# Kaggle notebook (SDXL + Canny ControlNet). Kaggle notebooks are edited in
# Kaggle's own UI, but this notebook "was never version-controlled and got
# lost/rediscovered more than once" (per kaggle_notebooks/elevation_server.py's
# header) - so this copy lives in the repo now so the exact contract the app
# depends on is never guessed.
#
# WHAT THIS IS: Stable Diffusion XL + Canny ControlNet, IMAGE diffusion. It
# traces a white-lines-on-black conditioning edge-map the app sends (this repo's
# real computed room geometry - app/pipeline/conditioning_image.py) and renders
# a stylized 2D floor plan per floor. It is NOT a source of vector/CAD data -
# see this repo's CLAUDE.md ("Real DXF/AutoCAD-format export") for the honest
# quality caveat. The real .dxf export and the accurate Computed Layout come
# from the deterministic blueprint_svg.py/blueprint_dxf.py, NOT this model.
#
# CONTRACT the app (app/providers/kaggle_autocad.py's generate_floor_plan) calls:
#   POST /generate_floorplans
#     body: {"length": float, "width": float, "unit": str,
#            "floors": int, "bedrooms": int, "bathrooms": int, "notes": str,
#            "conditioning_images": [b64 PNG, ...]  # optional, one per floor -
#                                     # real geometry the model traces; when
#                                     # absent the model falls back to its own
#                                     # create_plot_boundary()
#            "doors": [[door_spec, ...], ...]}       # optional, one list per
#                                     # floor, parallel to conditioning_images -
#                                     # see the "DOORS" section below
#     -> {"status": "started", "job_id": str}
#   GET /generate_floorplans/status/{job_id}
#     -> {"status": "running"}
#        | {"status": "done", "plot_size", "total_floors",
#           "floors": [{"floor_number", "floor_title", "prompt_used",
#                       "used_real_geometry", "image_base64" (JPEG)}, ...]}
#        | {"status": "failed", "detail"}
#
# DOORS (2026-09-26): the app computes the SAME swing-arc doors the Computed
# Layout draws (interior doors + the front entrance on the chosen facing edge,
# with all the real suppression logic already applied) and sends them as
# draw-ready specs in CANVAS-FRACTION coordinates (fractions of 1024, the
# conditioning-image canvas the model traces). The model can't draw accurate
# doors itself (it only ever sees pixels), so - exactly like the app already
# composites accurate room labels onto the returned image - draw_doors_on_image()
# below draws the leaf line + quarter-circle swing arc from those specs, so the
# Concept Layout's doors match the Computed Layout's precisely. Each door spec:
#   {"leaf": [[fx0,fy0],[fx1,fy1]],   # leaf line, canvas fractions
#    "arc":  [fx0,fy0,fx1,fy1],       # arc bounding box, canvas fractions
#    "arc_start": deg, "arc_end": deg,# PIL arc sweep (0=+x, 90=+y, cw)
#    "label": "ENTRANCE" | null,      # only the front door carries a label
#    "label_at": [fx,fy] | null}
# Polarity: the model's output polarity is random (light-on-dark vs dark-on-
# light). draw_doors_on_image() picks the door INK to contrast the model's own
# output, and the app's _normalize_dark_background() later inverts a dark image
# to light - so doors and background flip together and the final card always
# reads as dark doors on a light plan.
#
# ------------------------------------------------------------
# KAGGLE CELL BOILERPLATE (not part of this reference file - prepend these as
# their own cells at the top of the Kaggle notebook, unchanged):
#   !fuser -k 8000/tcp || true
#   !pip install -q diffusers transformers accelerate controlnet_aux opencv-python
#   !pip install -q fastapi uvicorn pydantic python-multipart nest-asyncio
# ...and the cloudflared tunnel cell at the very end:
#   !wget -q -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
#   !dpkg -i cloudflared-linux-amd64.deb
#   !cloudflared tunnel --url http://localhost:8000
# ============================================================

import os
import io
import gc
import time
import uuid
import base64
import threading
import asyncio
from typing import Optional, Dict, List

import torch
from PIL import Image, ImageDraw, ImageFont, ImageStat
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from diffusers import (
    StableDiffusionXLControlNetPipeline,
    ControlNetModel,
    DPMSolverMultistepScheduler
)

gc.collect()
torch.cuda.empty_cache()
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.benchmark = True

print("🧹 GPU Memory Cleaned!")

# ==========================================
# LOAD AI MODELS (SDXL + CANNY CONTROLNET)
# ==========================================
print("🔄 Loading ControlNet Canny SDXL...")
controlnet = ControlNetModel.from_pretrained(
    "diffusers/controlnet-canny-sdxl-1.0",
    torch_dtype=torch.float16,
    use_safetensors=True
)

print("🔄 Loading SDXL Pipeline...")
pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
    "stabilityai/stable-diffusion-xl-base-1.0",
    controlnet=controlnet,
    torch_dtype=torch.float16,
    use_safetensors=True
)

pipe.scheduler = DPMSolverMultistepScheduler.from_config(
    pipe.scheduler.config,
    use_karras_sigmas=True
)
pipe.enable_attention_slicing()
pipe.vae.enable_slicing()
pipe.to("cuda")

print("✅ SDXL + ControlNet Pipeline Loaded Successfully!")

# ==========================================
# HELPER FUNCTIONS: BOUNDARY & COMPACT PROMPTS
# ==========================================
def create_plot_boundary(length: float, width: float, canvas_size: int = 1024) -> Image.Image:
    """Length aur Width ke mutabiq aspect-ratio boundary map draw karta hai.

    FALLBACK ONLY - used when the caller doesn't send a real conditioning
    image. Unchanged.
    """
    boundary_img = Image.new("L", (canvas_size, canvas_size), 0)
    draw = ImageDraw.Draw(boundary_img)

    max_box_size = canvas_size - 160
    aspect = width / max(length, 1.0)

    if aspect >= 1.0:
        box_w = max_box_size
        box_h = int(max_box_size / aspect)
    else:
        box_h = max_box_size
        box_w = int(max_box_size * aspect)

    x1 = (canvas_size - box_w) // 2
    y1 = (canvas_size - box_h) // 2
    x2 = x1 + box_w
    y2 = y1 + box_h

    draw.rectangle([x1, y1, x2, y2], outline=255, width=6)
    return boundary_img.convert("RGB")


def decode_conditioning_image(b64_png: str, canvas_size: int = 1024) -> Image.Image:
    """Decodes a real-geometry ControlNet conditioning image sent by the app.
    Unchanged."""
    img_bytes = base64.b64decode(b64_png)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if img.size != (canvas_size, canvas_size):
        img = img.resize((canvas_size, canvas_size))
    return img


# ==========================================
# DOORS (2026-09-26): draw the app-computed swing-arc doors onto the model's
# own output, so the Concept Layout shows the same doors as the Computed Layout.
# See this file's header "DOORS" section for the full contract/rationale.
# ==========================================
def draw_doors_on_image(img: Image.Image, door_specs) -> Image.Image:
    """Draws each door's leaf line + quarter-circle swing arc (and the front
    door's ENTRANCE label) from the app's canvas-fraction specs. The ink color
    contrasts the model's own output polarity, so the app's later
    dark-background inversion keeps the final card as dark doors on a light
    plan. Fully defensive - a malformed spec is skipped, never fatal."""
    if not door_specs:
        return img
    img = img.convert("RGB")
    W, H = img.size
    mean_lum = ImageStat.Stat(img.convert("L")).mean[0]
    ink = (0, 0, 0) if mean_lum >= 128 else (255, 255, 255)
    draw = ImageDraw.Draw(img)
    line_w = max(2, W // 350)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(12, W // 55))
    except Exception:
        font = ImageFont.load_default()

    for d in door_specs:
        try:
            leaf = d.get("leaf")
            if leaf and len(leaf) == 2:
                p0 = (leaf[0][0] * W, leaf[0][1] * H)
                p1 = (leaf[1][0] * W, leaf[1][1] * H)
                draw.line([p0, p1], fill=ink, width=line_w)

            arc = d.get("arc")
            if arc and len(arc) == 4:
                box = [arc[0] * W, arc[1] * H, arc[2] * W, arc[3] * H]
                draw.arc(box, start=d.get("arc_start", 0), end=d.get("arc_end", 90), fill=ink, width=line_w)

            label = d.get("label")
            label_at = d.get("label_at")
            if label and label_at and len(label_at) == 2:
                lx, ly = label_at[0] * W, label_at[1] * H
                try:
                    bbox = draw.textbbox((0, 0), label, font=font)
                    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                except Exception:
                    tw, th = len(label) * 6, 11
                draw.text((lx - tw / 2, ly - th / 2), label, fill=ink, font=font)
        except Exception as door_err:
            print(f"⚠️ skipped a malformed door spec: {door_err}")

    return img


def build_floor_prompt(
    floor_idx: int,
    total_floors: int,
    length: float,
    width: float,
    unit: str,
    bedrooms: int,
    bathrooms: int,
    notes: str = "",
    using_real_geometry: bool = False,
) -> str:
    """CLIP 77-token limit ke andar clean architectural prompt. Unchanged."""
    prefix = (
        "2D architectural CAD floor plan, top-down orthographic view, professional CAD drafting, "
        "pure white background, solid thick black filled walls, poche wall style, "
        "high contrast, crisp clean vector line art, sharp black linework, "
        "simple minimal furniture symbols, one bed per bedroom, one sofa in living room, "
        "one dining table, kitchen counter, bathroom fixtures, uncluttered"
    )

    if using_real_geometry:
        details = f"Floor {floor_idx} of {total_floors}" if total_floors > 1 else "Single-storey floor plan"
    elif floor_idx == 1:
        details = "Ground floor, entrance, porch, living, kitchen, powder room"
        if total_floors > 1:
            details += ", staircase, 1 bedroom"
        else:
            details += f", {bedrooms} bedrooms, {bathrooms} bathrooms"
    elif floor_idx == 2:
        details = f"Floor 2, stairs, family lounge, {max(1, bedrooms - 1)} master bedrooms, terrace"
    else:
        details = f"Floor {floor_idx}, stairs, rooftop lounge, guest suite"

    notes_str = f", {notes.strip()}" if notes.strip() else ""
    return f"{prefix}, {details}, plot {length}x{width} {unit}{notes_str}"


# ==========================================
# FASTAPI APP WITH ASYNC POLLING
# ==========================================
app = FastAPI(title="AI Floor Plan Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

generation_lock = threading.Lock()
jobs: Dict[str, dict] = {}


class PlotParameters(BaseModel):
    length: float
    width: float
    unit: str = "ft"
    floors: int = 1
    bedrooms: int = 3
    bathrooms: int = 2
    notes: Optional[str] = ""
    conditioning_images: Optional[List[str]] = None
    # 2026-09-26: draw-ready swing-arc door specs, one list per floor, parallel
    # to conditioning_images. See draw_doors_on_image()/this file's header.
    doors: Optional[List[List[dict]]] = None


def run_generation(job_id: str, params: PlotParameters):
    try:
        with generation_lock:
            negative_prompt = (
                "text, words, letters, numbers, digits, typography, captions, annotations, "
                "dimension text, dimension lines, measurements, handwriting, scribbles, symbols, "
                "labels, room names, title, watermark, signature, logo, gibberish text, "
                "3d, isometric view, perspective, realistic photos, ambient lighting, shadows, "
                "clutter, decor, plants, rugs, messy, busy, crowded furniture, "
                "trees, dark background, blurry lines, broken walls, thin walls, outline only walls, lowres, "
                "gray, grey, washed out, hazy, sketchy, low contrast, faded, noisy texture, mottled, grunge, dirty"
            )
            floor_results = []

            for f in range(1, params.floors + 1):
                generator = torch.Generator(device='cuda').manual_seed(42)

                conditioning_img = None
                idx = f - 1
                if params.conditioning_images and idx < len(params.conditioning_images):
                    try:
                        conditioning_img = decode_conditioning_image(params.conditioning_images[idx])
                    except Exception as decode_err:
                        print(f"⚠️ [Job {job_id[:6]}] Could not decode conditioning image for floor {f}: {decode_err}")
                        conditioning_img = None

                using_real_geometry = conditioning_img is not None
                if not using_real_geometry:
                    conditioning_img = create_plot_boundary(params.length, params.width)

                conditioning_scale = 0.95 if using_real_geometry else 0.8

                prompt = build_floor_prompt(
                    f, params.floors, params.length, params.width, params.unit,
                    params.bedrooms, params.bathrooms, params.notes, using_real_geometry
                )
                geometry_note = "real geometry" if using_real_geometry else "fallback boundary"
                print(f"🚀 [Job {job_id[:6]}] Generating Floor {f}/{params.floors} ({geometry_note})...")

                out_img = pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image=conditioning_img,
                    controlnet_conditioning_scale=conditioning_scale,
                    num_inference_steps=35,
                    guidance_scale=10.5,
                    generator=generator
                ).images[0]

                # 2026-09-26: overlay the app-computed swing-arc doors (matching
                # the Computed Layout) - generation above is UNCHANGED; this only
                # draws on top of the finished image. Best-effort: a failure here
                # must never fail the whole floor.
                if params.doors and idx < len(params.doors):
                    try:
                        out_img = draw_doors_on_image(out_img, params.doors[idx])
                    except Exception as door_err:
                        print(f"⚠️ [Job {job_id[:6]}] Door overlay failed for floor {f}: {door_err}")

                buffer = io.BytesIO()
                out_img.save(buffer, format="JPEG", quality=85)
                img_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

                floor_results.append({
                    "floor_number": f,
                    "floor_title": "Ground Floor" if f == 1 else f"Floor {f}",
                    "prompt_used": prompt,
                    "used_real_geometry": using_real_geometry,
                    "image_base64": img_b64
                })

            jobs[job_id] = {
                "status": "done",
                "plot_size": f"{params.length}x{params.width} {params.unit}",
                "total_floors": params.floors,
                "floors": floor_results
            }
            print(f"✅ [Job {job_id[:6]}] Completed successfully!")
    except Exception as e:
        print(f"❌ [Job {job_id[:6]}] Failed: {e}")
        jobs[job_id] = {"status": "failed", "detail": str(e)}


@app.post("/generate_floorplans")
def generate_floorplans_endpoint(params: PlotParameters):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "running"}
    threading.Thread(target=run_generation, args=(job_id, params), daemon=True).start()
    return {"status": "started", "job_id": job_id}


@app.get("/generate_floorplans/status/{job_id}")
def generate_floorplans_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


# ==========================================
# START FASTAPI SERVER IN BACKGROUND
# ==========================================
def start_server():
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


server_thread = threading.Thread(target=start_server, daemon=True)
server_thread.start()
print("🚀 FastAPI Server active on Port 8000!")
