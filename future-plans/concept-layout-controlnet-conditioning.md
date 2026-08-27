# Make the AI "Concept Layout" accurate: feed our real geometry into ControlNet

## Status

Planned, not started (2026-08-25). This is the improvement path for the friend-hosted Kaggle
SDXL+ControlNet floor-plan model (`app/providers/kaggle_autocad.py`, the "Concept Layout" card),
chosen after a full senior-level survey of every option (prompting, params, model swaps, LoRAs,
compositing). It is a TWO-SIDED change: the friend's Kaggle notebook AND this repo.

## The core idea (why this is THE lever, not prompting)

The model's real problem is NOT its aesthetic - it's that it **invents interior geometry that ignores
the user's actual plot**. Root cause, confirmed by reading the notebook: `create_plot_boundary()` draws
only the plot's OUTER rectangle as the ControlNet conditioning image - it has no interior walls, so
ControlNet only constrains the outer edge and the model hallucinates every interior wall at random.

This repo ALREADY computes the exact, correct wall layout (`app/pipeline/floor_layout.py`'s
`layout_floor()`). If we render those real walls as a clean edge map and feed THAT as the ControlNet
conditioning image (instead of the empty rectangle), ControlNet forces the model to trace the real
plan. Result: the model's drafting aesthetic applied to OUR accurate geometry - accurate + pretty at
once. This is worth more than every prompting/param tweak combined.

**Do NOT waste effort on these (intrinsic diffusion limits, already proven dead ends):**
- Legible text INSIDE the AI image - diffusion can't render reliable text; solved by compositing (Phase
  2), never by prompting.
- Real DXF/vector output - it's an image model, outputs pixels forever. The deterministic
  `blueprint_svg.py`/`blueprint_dxf.py` system stays the sole source of truth + the only real `.dxf`.
- **Prompt-format nuance (important, easy to get wrong):** do NOT rewrite the notebook's comma-separated
  keyword prompt into natural-language sentences. That lesson (`app/pipeline/prompts.py`) was for
  `gpt-image-1`, an INSTRUCTION-following model. SDXL is a raw diffusion model - comma/tag-style prompts
  are its NATIVE format and work better than prose. Once ControlNet drives the geometry, the prompt
  barely needs to describe rooms at all.

---

## Phase 1 (core, biggest win): real-geometry ControlNet conditioning

### Our repo side (`app/providers/kaggle_autocad.py` + a small new renderer)
- **New: a conditioning-image renderer** (a small pure function, e.g. `render_conditioning_edge_map(rects,
  dimensions) -> bytes` in a new `app/pipeline/conditioning_image.py`, or reuse geometry from
  `floor_layout.py`). Draws, on a **1024x1024 black canvas** (SDXL-native):
  - The plot placed CENTERED and scaled by aspect ratio to fit within a max box (mirror the friend's
    existing `create_plot_boundary()` math: `max_box = 1024 - 160 = 864`, so the placement is identical
    and known) - this is what makes Phase 2 label compositing alignment deterministic.
  - Every room rectangle's outline + the outer plot boundary as **thin WHITE lines** (~2-3px) - a clean
    canny-style edge map (white lines on black is exactly what the existing Canny ControlNet expects).
    Just wall centerlines; no poché, no furniture, no text.
  - Return PNG bytes. Keep it PURE/deterministic (no I/O), same discipline as `blueprint_svg.py`.
- **`generate_floor_plan()` sends one conditioning image per floor**, floor-ordered, built from the SAME
  `layout_floor(floor["rooms"], dimensions)` rects the deterministic blueprint already uses - so the AI
  traces the exact same layout as the "Floor N Layout" card. Add them to the request payload (new field,
  see contract below). This means `generate_floor_plan()` needs the per-floor room layout, which the
  pipeline already has (`room_layout`) - thread it in, OR compute the layout here from the same Gemini
  call. Cleanest: pass the already-computed `room_layout` into `generate_floor_plan()` from
  `generate_house.py` (it's computed just above the floor-plan step) rather than recomputing.

### Friend's notebook side (Sonnet produces the full paste-ready notebook on request)
Minimal, backward-compatible changes to the existing notebook (Sections numbered as in their current file):
- **`PlotParameters` gains** `conditioning_images: Optional[List[str]] = None` (base64 PNGs,
  floor-ordered, one per floor).
- **In `run_generation()`'s per-floor loop:** if `conditioning_images` is provided and has an entry for
  this floor, decode it and use it AS the ControlNet `image` (the `pipe(image=...)` arg), instead of
  calling `create_plot_boundary()`. If absent, fall back to `create_plot_boundary()` exactly as today
  (full backward compat - a caller that sends no conditioning image still works).
- **Raise `controlnet_conditioning_scale` 0.8 -> ~0.95** WHEN a real conditioning image is used (so it
  follows our walls tightly instead of drifting); keep 0.8 for the fallback path.
- **Negative prompt: add the full text exclusion** (`"text, labels, room names, numbers, writing,
  watermark, letters, numbers"`) so the output is deliberately TEXT-FREE (Phase 2 adds our own labels).
- **Prompt can be simplified** to a generic per-floor line (e.g. `"2D architectural floor plan, top view,
  furniture, black walls on white, clean line drawing"`) - the ROOMS are now determined by the
  conditioning image, not the prompt, so the hardcoded per-floor room lists in `build_floor_prompt()`
  matter far less. Keep the fixed seed (already added) and submit-then-poll (already added) as-is.
- **Everything else unchanged** - FastAPI submit/poll endpoints, Cloudflare tunnel, response shape.

### Contract change (request only; response unchanged)
`POST /generate_floorplans` body gains optional `conditioning_images: [<base64 png>, ...]` (floor-ordered,
length == floors). Response still `{"status":"done","floors":[{floor_number, image_base64 (JPEG)...}]}`.

### Phase 1 result
Text-free AI floor plans that actually trace the user's real dimensions and room layout - the
transformative change. Labels come in Phase 2.

---

## Phase 2 (labels): composite our accurate text onto the text-free AI image

Once the AI output traces our geometry, our labels are guaranteed to align (same base geometry) - which
removes the exact objection that killed the earlier "v4" composite attempt (unpredictable alignment).
- After `generate_floor_plan()` gets each floor's text-free JPEG back, **composite** (PIL) each room's
  name + area at that room's CENTER, computed via the SAME 1024x1024 placement math the conditioning
  renderer used (so we know precisely where each room landed).
- **Place labels at room CENTERS only** (robust to the small wall jitter ControlNet can still introduce).
  Do NOT composite precise dimension strings onto the AI image (those live perfectly on the deterministic
  "Floor N Layout" card already) - centers are safe, wall-exact annotations are not.
- This can live in `kaggle_autocad.py` (post-process the returned bytes before storing) or a small helper.

### Phase 2 result
Accurate geometry + AI drafting aesthetic + legible, correct room labels - all three at once.

---

## Phase 3 (optional, only if Phases 1-2 aren't enough): model-level upgrades
- **MLSD ControlNet instead of Canny** - MLSD is a straight-line detector purpose-built for
  architectural/rectilinear structures (Canny is tuned for organic edges); documented as better for
  buildings. Requires a working SDXL MLSD checkpoint (e.g. `ControlNet-Union-SDXL-1.0`, which supports
  MLSD among 12 control types) - a bigger notebook change than Phase 1, so deferred. With Phase 1's clean
  white-on-black wall map already feeding Canny well, this is a refinement, not a necessity.
- **Floor-plan / architectural LoRA** on top of base SDXL (e.g. Civitai architectural LoRAs) for better
  line-art coherence. **CHECK EACH LoRA'S LICENSE before any commercial use** - same RPLAN-style trap
  documented under "Real DXF/AutoCAD-format export" in CLAUDE.md.
- **Param tuning:** `num_inference_steps` 25->35; `controlnet_conditioning_scale` fine-tune. Marginal
  next to Phase 1.

---

## Explicit split of work (for the future Sonnet session)
- **NOTEBOOK (friend's code, Sonnet outputs a complete paste-ready notebook):** `PlotParameters` new
  field; per-floor conditioning-image use with `create_plot_boundary()` fallback; conditioning_scale
  bump; text-free negative prompt; simplified prompt. Nothing else touched.
- **THIS REPO (Sonnet edits):** new conditioning-edge-map renderer (pure, 1024x1024, white walls on
  black, aspect-ratio placement matching the notebook); `generate_floor_plan()` builds + sends one per
  floor (needs the per-floor `room_layout` threaded in from `generate_house.py`); Phase 2 label
  compositing on the returned images; tests (pure renderer asserts real PNG + known placement; provider
  test asserts conditioning images are sent, mocked like the existing `test_kaggle_autocad.py`).

## Verification
- Local, free: extend `scripts/test_autocad_kaggle_model.py` to also build + send conditioning images and
  save the returned floors; **visually inspect** that the AI output now matches the requested dimensions/
  layout (compare side by side with the deterministic "Floor N Layout" render of the same plot).
- `pytest` green (new pure-renderer test + updated provider test).
- Confirm backward compat: the notebook still works if `conditioning_images` is omitted.

## Honest caveats (state these plainly, don't oversell)
- Still NOT real CAD/vector data - the deterministic DXF stays the only real AutoCAD export.
- ControlNet is not pixel-perfect - geometry will track our walls closely but can still jitter slightly;
  that's why Phase 2 labels sit at room centers, not on walls.
- Requires the friend to update + redeploy their notebook (their code, their GPU) - a coordination
  dependency, not something this repo can ship alone. Each notebook restart also rotates the Cloudflare
  tunnel URL (update `KAGGLE_AUTOCAD_API_URL` in `.env`), same as every other Kaggle model here.
