# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

B2B AI interior-design tool. A user uploads a room photo; the backend returns **3 visually distinct
redesigns** — **Economical / Mid / Premium** — that preserve the real room structure (walls, windows,
doors, layout, camera angle). Phase 1 (current) is the backend pipeline + a bare-bones localhost frontend
that reliably produces and saves those 3 tiers. Materials-list + city-pricing (originally deferred, spec
preserved in the plan §9) is now **built** — see "Materials & pricing feature" below. A second, independent
**"Build a House" tab** (plot photo + dimensions → concept render) also now exists — see "Build a House
feature" below.

Full plan: `C:\Users\User\.claude\plans\act-as-senior-technical-purrfect-globe.md`.

Originally built on **free tiers only**; that constraint was later dropped for image generation (see
"Provider split" below) — read it before touching `app/providers/`.

## Commands

- Install: `pip install -r requirements.txt` (or `./.venv/Scripts/python.exe -m pip install ...` on this
  machine's venv)
- Run dev server: `uvicorn app.main:app --reload` (serves `/` and the API on port 8000)
- Run all tests: `pytest`
- Run one test: `pytest tests/test_pipeline.py::test_run_pipeline_success -q`
- Config/secrets: copy `.env.example` → `.env` and fill in real keys. **Never put real secrets in
  `.env.example`** — it is git-tracked; only `.env` is gitignored.

## Provider split (important — read before editing app/providers/)

Originally planned as a single free Gemini provider for both image generation and text. **Google removed
free-tier Gemini image generation in Dec 2025** (image-gen quota is now `limit: 0` on the free tier,
paid-only). Current setup is a **hybrid**, wired in `app/providers/hybrid.py`:

- **Room description (text)**: `app/providers/gemini.py` — Gemini `gemini-2.5-flash`, still free
  (separate quota from image gen).
- **Image generation**: `app/providers/openai.py` (`OpenAIImageProvider`) — OpenAI's Images API, the sole
  image backend. Paid; model is `gpt-image-1`, not `gpt-image-2` which retires Oct 2026. It's an
  instruction-following edit model (like Nano Banana), not raw diffusion — no `negative_prompt`/`strength`
  channel exists; `Provider.generate_image(image_bytes, prompt)` takes only a fully-composed prompt, and
  exclusions ("no chandelier") are stated directly inside it by `build_prompt()`.
  **Real cost trap, found via a live test call, not docs**: `OPENAI_IMAGE_QUALITY` alone does NOT
  control cost the way it looks like it should - `OPENAI_IMAGE_INPUT_FIDELITY` is a SEPARATE param
  that also drives cost heavily and silently defaults to the expensive tier if omitted from the
  request. A real smoke test with only `quality="low"` set cost **$0.10/image**, ~20x the ~$0.005
  "low quality" figure quoted in third-party pricing articles (which describe generation cost, not
  edit/image-input cost). Both params are now always sent explicitly (`openai.py` includes
  `input_fidelity` unconditionally) - `test_generate_image_always_sends_input_fidelity_explicitly`
  guards this regression. `input_fidelity` isn't purely a cost knob either - OpenAI describes it as
  controlling "fidelity to the original input image(s)", i.e. structure preservation, so if a
  real-money-driven push toward `"low"` starts showing structural drift, that's the first thing to
  revert to `"high"`, accepting the cost jump, before touching prompts/anything else.
  **A free Cloudflare Workers AI/SD1.5 img2img fallback existed earlier and was deliberately removed
  entirely** (not disabled/dormant — the module, its tests, `STRENGTH_BY_TIER`, `build_negative_prompt`,
  `IMAGE_PROVIDER` switching, and all config/env entries are gone) once OpenAI became the real backend,
  because SD1.5-era concepts (diffusion `strength` dial, negative-prompt channel, 77-token CLIP limit)
  don't apply to an instruction-following editor and were only adding confusion. If a free image fallback
  is ever wanted again, it needs a fresh implementation, not a revival of the old `cloudflare.py`.
  Three abandoned investigations, kept on record: Pixazo (Flux Schnell img2img) - their API gateway
  Cloudflare-bot-blocks server-side requests (403 even with a valid key), unusable for backend
  integration. NVIDIA NIM's Qwen-Image-Edit - `NVIDIA_API_KEY` placeholder exists but no provider code;
  its hosted invocation shape was never confirmed (image-edit models aren't in the standard `/v1/models`
  catalog, likely needs NVCF function-ID-based invocation instead of a friendly model name - the exact
  request shape lives on a JS-rendered `build.nvidia.com` page automated fetching couldn't scrape). Two
  exploratory Colab notebooks (`colab/qwen_image_edit_experiment.ipynb`,
  `colab/mage_flow_edit_turbo_experiment.ipynb`) compare alternate open-weight edit models against the
  old Cloudflare/SD1.5 pipeline they were written against — historical experiment records, not part of
  the runtime; left as-is even though the pipeline they compare against no longer exists in the app.
- **Tier notes (room-specific prompt customization)**: `GeminiProvider.generate_tier_notes` analyzes the
  actual uploaded photo once and returns a short, tier-specific instruction per tier (e.g. "repaint over
  visible water stains" for economical) that `build_prompt()` inserts at high priority. This exists because
  a fully generic prompt under-transformed a badly damaged room for the economical tier (looked barely
  renovated) — see `TIER_NOTES_PROMPT` in `gemini.py`. Best-effort: on failure/parse error it degrades to
  `{}` and the pipeline continues without room-specific notes, same as `describe_room`.
- `get_provider()` (`app/providers/__init__.py`) returns `HybridProvider` — this is the composition root.
  `GeminiProvider.generate_image` still exists but is unused/dormant (would work again if billing is ever
  enabled on the Gemini project) — don't delete it without checking with the user first.
- **User style prompt**: `static/index.html`'s style-prompt `<input>` (above the Generate button) lets the
  user type free text (e.g. "modern, blue accents") that's sent as `style_notes` form data on
  `POST /api/projects`, stored on `Project.user_style_notes`, and threaded through
  `run_pipeline(..., user_style_notes=...)` into every tier's `build_prompt(..., user_notes=...)` call - see
  `prompts.py`. Inserted right after `tier_note`, ahead of the tier's own generic `paint` field, so it can
  actually steer the tier's default look rather than being truncated away or drowned out. Capped at
  `USER_NOTES_MAX_CHARS` (150) - enforced in **both** `build_prompt()` and `main.py`'s endpoint, since the
  frontend's `maxlength` is trivially bypassable by anyone calling the API directly.

## Materials & pricing feature

After the 3 tiers are generated, each tier (except the original) can show an itemized materials list
with local pricing + source links + a rough total, localized to a city the user types on the **upload
screen** (not after — see below).

- **City input** (`static/index.html`'s `#city-input`, above Generate): persisted to `localStorage`
  (`CITY_STORAGE_KEY` in `app.js`) so it only shows the `e.g. Karachi` placeholder the first time - after
  that it stays filled with whatever was last entered until the user changes it. **Empty city is a
  deliberate, supported choice**: `app.js`'s submit handler shows a `confirm()` warning ("pricing & local
  material info won't be available - only images will be generated") and, if confirmed, submits with an
  empty `city` - this is the **images-only path**, and it must result in **zero Gemini materials calls**
  (see below), not just an empty result.
- **`POST /api/projects` gained a `city` Form field** (`app/main.py`) - trimmed/length-capped
  (`CITY_MAX_CHARS`), empty allowed. No separate materials endpoint - city is known at upload time, so it
  threads straight into `run_pipeline(..., city=city)`.
- **Concurrency design (the reason this isn't a second `BackgroundTasks` call)**: FastAPI's
  `BackgroundTasks` run **sequentially** (Starlette awaits them in order), so adding a second background
  task for materials would NOT overlap image generation - it would just run strictly after. Instead,
  `run_pipeline()` (`app/pipeline/generate.py`) opens a `ThreadPoolExecutor(max_workers=3)` **at the
  start** (right after `room_description`/`tier_notes`), submitting one `generate_materials` call per
  tier, and joins the futures (`future.result(timeout=MATERIALS_TIMEOUT_SECONDS)`) **after** the image
  loop - so the 3 materials lookups genuinely overlap the ~30-60s of image generation instead of adding to
  it. If `city` is empty/falsy, this whole block is skipped entirely - `materials_status="skipped"`, and
  `provider.generate_materials` is never called (verified by
  `test_run_pipeline_without_city_skips_materials`).
- **3 DEDICATED Gemini API keys, one fixed per tier** (`gemini_materials_api_key_economical`/`_mid`/
  `_premium` in `app/config.py`, exposed via the `Settings.gemini_materials_api_keys` property - a
  `{tier: key}` dict, not a round-robin list): each tier always hits its own, same key every run, so it
  gets a genuinely **separate rate-limit quota** with zero contention between tiers. Deliberately kept
  separate from `gemini_api_key` (used only for the text calls - `describe_room`/`generate_tier_notes`,
  which run on every project regardless of city) so materials never competes with those either. Degrades
  gracefully per-tier: any tier without its own dedicated key configured falls back to `gemini_api_key`.
  `GeminiProvider` caches one `genai.Client` per distinct key (`_clients_by_key` in `gemini.py`) so
  concurrent calls actually use distinct clients, not one shared connection.
- **Real pricing via SerpApi + Gemini synthesis, NOT Gemini's own grounding tool** (`GeminiProvider.
  generate_materials` in `gemini.py`, `app/providers/serpapi.py`). **Real, live-tested reason for this
  design**: `types.Tool(google_search=types.GoogleSearch())` grounding hit a 429 `RESOURCE_EXHAUSTED` wall
  on every Gemini key/project tested (main key, and 3 brand-new never-used materials-key projects) - not a
  quota-exhaustion issue, an immediate hard block, and Google's own AI developer forum has multiple current
  reports of billing NOT fixing this (a platform-side bug, not user/config error). So materials pricing
  does its own search instead via SerpApi (`serpapi.search()`, 250 searches/month free, no card).
  **PER-ITEM search, not one combined search per tier** (`_tier_line_items()` in `gemini.py`) - a real,
  live-observed reason: an earlier version fired ONE combined query per tier (e.g. "price of grey ceramic
  tile, cream paint, cool lighting in Karachi"). A single query only ever returned real matches for
  whichever term Google treats as the primary e-commerce category - almost always flooring/tile - so most
  of a tier's other items (ceiling, feature wall, decor...) came back as pure LLM guesses no matter how the
  prompt was worded. Fix: each tier's items are a FIXED, deterministic list of 6 (`_tier_line_items()` -
  flooring, paint, lighting, ceiling, feature wall, decor, skipping empty fields) with **one dedicated
  SerpApi search per item**, not a model-chosen free-form 5-9 item list. Cost: 6 searches/tier × 3 tiers =
  18/generation, ~13-14 generations/month on the free tier (down from ~83 at 1-search-per-tier) - an
  explicit, accepted tradeoff the user chose after seeing most items come back as guesses. `NUM_RESULTS` in
  `serpapi.py` was also bumped 6→12 - a free improvement (SerpApi bills per call, not per result count)
  that widens the odds a specific item's real price appears somewhere in its own dedicated results.
  Each item's own results are labeled and kept separate in the prompt (`_format_line_items_with_results()`)
  and the model is told to price each item ONLY from its own labeled results, never borrowing a link shown
  under a different item - a **plain, non-grounded** Gemini call (cheap, no special tool, no
  grounding-quota risk) synthesizes real prices where a real listing was found, or its own rough estimate
  when it wasn't - never omitting an item or leaving a price blank either way.
  **Defense in depth**: `_sanitize_source_urls()` strips any `source_url` the model returned that doesn't
  match ANY of the real links returned across all of that tier's per-item searches, downgrading that item
  to `is_estimate=True` instead of ever showing a possibly-hallucinated link as real. Checked against the
  union of all items' real links, not strictly per-item - a stricter check would need to match the model's
  returned item `name` back to the exact fixed label, and a minor rename would then wrongly strip an
  otherwise-real link; cross-item link reuse is a smaller, lower-stakes failure mode (still a real,
  resolving URL) than that false-negative. If a single item's search fails (quota/network), that's not
  fatal - the rest of the tier's items are unaffected, that one item just can't be marked real. SerpApi's
  per-request timeout is 30s (bumped from 20s after a real observed `ReadTimeout` on one item during live
  testing).
  **Retry on transient Gemini 503s** (`_generate_content_with_retry()`, `MATERIALS_GEMINI_MAX_ATTEMPTS`):
  a real, live-observed failure during testing - `gemini-3.5-flash` intermittently returns a 503
  ("currently experiencing high demand"), a transient Google-side overload, not a code bug (hit twice in
  this project's own testing). Without a retry, a single transient 503 on the synthesis call discarded 6
  already-successful, real SerpApi searches and collapsed the whole tier straight to
  `fallback_materials()`'s generic estimates - a real user-visible incident this fixed (up to 3 attempts,
  2s apart, retrying only `genai_errors.ServerError`/5xx, not auth/bad-request errors that would just fail
  identically again). `MATERIALS_TIMEOUT_SECONDS` in `generate.py` was bumped accordingly (45→75→100) to
  give the retries and the longer SerpApi timeout room to actually complete before the outer future times
  out. If the Gemini call still fails after retries are exhausted, `fallback_materials()` synthesizes the
  same fixed item list straight from `tier_spec`'s own fields, each with a guaranteed-to-resolve
  Google-search URL (not a guessed product link) - so the UI never renders a blank price or a dead link
  even in the worst case. Both prices and the total are **plain strings** (not strictly numeric), a
  deliberate simplification since real-world listings mix currencies/formats freely - the frontend just
  displays them as-is.
  **If grounding is ever revisited** (e.g. billing genuinely fixes it on a project): the fix is swapping
  the SerpApi call + prompt back for the `types.Tool(google_search=...)` config this replaced - the rest
  of the pipeline (parse/sanitize/fallback, the per-tier key selection, the concurrency design) doesn't
  need to change either way.
- **Data model**: `Project.city`, `Project.materials_json` (dict keyed by tier), `Project.materials_status`
  (`idle` → `running` → `done`/`skipped`; `failed` reserved, not currently reachable since per-tier failures
  degrade to fallback data instead). `ProjectStatusResponse` exposes `materials`/`materials_status`.
  **Migration note**: `init_db()` (`app/db.py`) now runs a tiny additive `ALTER TABLE` migration after
  `create_all()` for exactly this reason - `create_all()` only creates missing *tables*, never adds
  columns to an existing one, so an existing dev DB from before this feature would 500 with "no such
  column" without it.
- **Frontend rendering** (`static/app.js`'s `renderMaterialsSection()`): each tier card gets a
  "View materials & cost" button that opens a shared popup modal (`#materials-modal-overlay`, styled like
  the image lightbox) with an itemized table (name/spec, price + an "Estimate" badge when `is_estimate`, a
  source link or `—`) and a total - not an in-card `<details>` dropdown (an earlier design), which clipped/
  wrapped text awkwardly in the narrow card layout. By the time results render, `run_pipeline()` has
  already joined the materials futures, so `materials_status` should already be settled (`done` or
  `skipped`) - the `running` UI state exists defensively but isn't the normal path. Clicks on the button
  `stopPropagation()` so they don't also trigger the card's click-to-open-lightbox handler.

## Build a House feature

A second, independent tab (`static/index.html`'s tab bar) alongside room-redesign: upload a plot/land
photo + length×width dimensions + a free-text prompt (e.g. "2 floors, 3 bedrooms, modern style") → get
back a plot analysis and an AI-generated exterior/interior concept render. It has its own data model, own
pipeline module, and its own endpoints — deliberately not folded into the room-redesign `Project`/
`run_pipeline`/`/api/projects` path, since the fields and stages genuinely differ.

- **Data model**: `HouseProject` (`app/models.py`) — its own table, not a reuse of `Project`. Fields:
  `plot_image_key`, `dimensions_json` (`{"length", "width", "unit"}` as JSON-as-text, same convention as
  `materials_json`), `prompt`, `plot_description`, `floor_plan_key`/`floor_plan_status`, `render_key`,
  `meta_json`. New table → needed no entry in `db.py`'s `_migrate_missing_columns()` (that only covers
  adding columns to an *existing* table; `create_all()` creates new tables on its own).
- **Endpoints** (`app/main.py`): `POST /api/house-projects` (multipart plot photo + `length`/`width`/
  `unit`/`prompt` form fields) and `GET /api/house-projects/{id}`, mirroring `create_project`/`get_project`'s
  exact validate → store → background-task → poll shape.
- **Pipeline** (`app/pipeline/generate_house.py`'s `run_house_pipeline`, mirrors `run_pipeline`): (1)
  best-effort `analyze_plot()` (Gemini vision, same call shape as `describe_room`) → `plot_description`;
  (2) best-effort `generate_floor_plan()` — see "Floor-plan vendor" below, currently always a no-op; (3)
  **non-best-effort** `generate_house_render()` (OpenAI `gpt-image-1` edit call) → `render_key` - a
  failure here fails the whole house project, same treatment `generate_image` gets in the room pipeline,
  since it's this feature's core paid deliverable. Prompts composed by `app/pipeline/house_prompts.py`'s
  `build_house_prompt()` (dimensions + optional plot_description + optional truncated user prompt, capped
  at `USER_PROMPT_MAX_CHARS`), following the same "natural-language edit paragraph, not keyword soup"
  lesson learned for room-redesign prompts.
- **Provider seam extension** (`app/providers/base.py`): 3 new abstract methods — `analyze_plot()` and
  `generate_floor_plan()` are best-effort (degrade to `None`, same contract as `describe_room`);
  `generate_house_render()` is not (mirrors `generate_image`). `GeminiProvider` implements `analyze_plot`
  for real and `generate_house_render` by delegating to its existing `generate_image`; `generate_floor_plan`
  always returns `None` (Gemini has no floor-plan capability - that vendor slot is
  `app/providers/idealhouse.py`, composed in by `HybridProvider`, not this class).
  `OpenAIImageProvider.generate_house_render()` shares its HTTP call shape with `generate_image()` via a
  private `_edit_image()` helper, but stays its own public method so this feature's params never tangle
  with room-redesign's tier/fidelity semantics.
- **Floor-plan vendor: deliberately deferred, not forgotten.** The original spec called for a real 2D
  floor-plan generation step (dimensions/prompt → floor plan image) before the render step, since general
  image models don't reliably respect exact measurements. Two vendors were researched (docs read live, not
  assumed) and both were rejected for v1:
  - **ModelsLab's Floor Planning API** (`docs.modelslab.com/interior-api/floor-planning`) is the wrong tool
    entirely - it's SD1.5-style img2img (`strength`/`guidance_scale` params) that takes an *existing
    interior room photo* as input. No dimension parameters, no plot/land photo support at all.
  - **ideal.house's Floor Plan API** (`ideal.house/api/docs/floor-plan-api`) is real and works
    (`POST /api/v1/floorPlan/generate` → async poll `GET /api/v1/floorPlan/result?taskId=...`), but (a)
    **no free tier** - $39 minimum for 1,000 credits, ~$0.39-0.78/generation, and (b) **cannot honor exact
    dimensions or plot geometry** - it only accepts `bedrooms`/`bathrooms`/a coarse `grossArea` *range*
    string (e.g. "100-120m²")/`extras`/a text `prompt`, plus an optional *floor-plan* reference image (not
    a land photo). There's no way to feed in an exact length×width as a real geometric constraint.
  - User decision: ship plot analysis + render only for v1; hold off on any floor-plan vendor until
    evaluated directly. `app/providers/idealhouse.py` exists as the wired-but-inert vendor slot - its
    `generate_floor_plan()` just logs "not configured" and returns `None`, and the pipeline treats that as
    an expected, non-fatal state (`floor_plan_status="not_configured"`), not a failure.
  - **To wire in a real vendor later**: implement the actual `httpx` create+poll calls inside
    `app/providers/idealhouse.py` only (the docstring there records the exact request/response shapes
    above) - no changes needed to `base.py`, `hybrid.py`, the pipeline, or the API layer, since the
    interface and the "best-effort, None means unavailable" contract are already in place.
  - **Also deferred, and dropped from scope**: corner-point/irregular-plot input. Neither vendor can use
    plot geometry at all, so v1 only collects length×width - building corner-point UI now would be
    misleading. Revisit only if a future vendor can actually consume geometry.
- **"Concept Layout" labeling requirement**: whenever a real floor-plan vendor is wired in,
  `renderHouseResults()` (`static/app.js`) must label that card **"Concept Layout — not a precise
  blueprint"**, never anything implying dimensional accuracy - no current image-gen floor-plan API
  guarantees exact measurements. The frontend already contains this exact label, gated on
  `floor_plan_status === "done"`, so it's ready the moment a vendor makes that state reachable; if
  `floor_plan_status` is anything else, no floor-plan card renders at all (not a broken placeholder).
- **Frontend**: `static/index.html`'s tab bar (`#tab-bar`, `switchTab()` in `app.js`) toggles between the
  room-redesign panel and the house panel independently - each keeps its own upload/progress/error/results
  state machine (`showState()` for rooms, `showHouseState()` for the house tab), so switching tabs mid-flow
  doesn't disturb the other tab's in-progress work. House progress uses the same stage-stepper CSS as
  room-redesign but with only 3 stages (Analyzing plot / Generating render / Finalizing) and no
  concept-preview cards, since there's one deliverable image this pass, not 3 tiers.
- **Tests**: `tests/test_idealhouse.py`, `tests/test_house_prompts.py`, `tests/test_house_pipeline.py`,
  `tests/test_house_api.py` (mirrors `test_api.py`'s pattern of mocking both `get_provider` and
  `get_storage`), `tests/test_gemini_house.py`, plus a `generate_house_render` case added to
  `tests/test_openai_provider.py`.

## Architecture (big picture)

Upload → FastAPI stores the original + creates a `Project` row (SQLite) → a **background job** runs the
3-tier pipeline → each tier calls the provider's image generation with a distinct prompt → all 4 images
land in object storage → the frontend **polls** `GET /api/projects/{id}` until `status=done` and renders
them.

Three deliberate seams keep the free/solo build swappable — respect them when adding code:

1. **Provider seam** (`app/providers/`): all model calls go through the `Provider` interface
   (`generate_image`, `describe_room`, `generate_tier_notes`, `generate_materials`). See "Provider split"
   above for the current hybrid wiring. Swapping a model/vendor should only ever mean touching this
   directory.
2. **Storage seam** (`app/storage/`): image **bytes** go through a `Storage` interface — `local.py`
   (filesystem, dev default) or `s3.py` (boto3, S3-compatible → currently a real **AWS S3** bucket in
   `.env`, R2 still viable). SQLite stores **metadata + storage keys only**, never image blobs. Keys are
   namespaced by purpose: uploads under `local.input/{project_id}/original.png`, generated tiers under
   `local.output/{project_id}/{tier}.png` (`app/main.py` and `app/pipeline/generate.py` respectively) -
   keeps "things the user gave us" separate from "things we generated" instead of one flat pile of UUID
   folders.
   **Test isolation, hard-learned**: `get_storage()` reads `settings.storage_backend` from `.env` - which
   has been `s3` for real local dev use for a while. Any test that exercises the real app
   (`TestClient(app)`) but only mocks `get_provider()` without also mocking `get_storage()` will silently
   write real objects into the live S3 bucket on every `pytest` run. This actually happened - the bucket
   filled up with tiny 4x4 placeholder images from `test_api.py`'s `FakeProvider` before it was caught
   (bucket was wiped clean after). `tests/test_api.py`'s `FakeStorage` + `monkeypatch.setattr(main_module,
   "get_storage", ...)` is the fix - any new test hitting `TestClient(app)` must mock **both**
   `get_provider` and `get_storage`, not just the former.
3. **Tier/prompt seam** (`app/pipeline/prompts.py`): tier differentiation is driven by a structured
   per-tier **style spec** dict (`TIER_SPECS`) plus a shared `PRESERVE_STRUCTURE` block. This dict is
   intentionally the seed of the future materials/pricing DB — keep it structured, not free-text. Encodes
   a real renovation cost-impact methodology (material quality ladder + lighting-temperature ladder,
   prioritized paint -> flooring -> lighting -> feature walls -> decor), not arbitrary tier adjectives.

   **Prompt format: natural-language edit instructions, not keyword soup.** `build_prompt()` composes
   `TIER_SPECS` into a paragraph of imperatives ("Paint the walls with...", "Do not include...") because
   the image backend, OpenAI `gpt-image-1`, is an instruction-following editor (like Nano Banana), not raw
   diffusion. This replaced an earlier comma-separated descriptor format after a **real failure**: that
   format read to an instruction model as a generation spec for a target image, not an edit instruction
   for the input photo, so a real 3-tier generation came back with Premium/Mid as entirely different rooms
   (only Economical, with the weakest vocabulary, loosely resembled the input).

   `NEGATIVE_ADDITIONS` (tier-specific exclusions like "chandelier" for budget/mid) is rendered as a
   positive-prompt "Do not include: ..." sentence in `build_prompt()` — instruction-following models are
   built to follow exclusions stated directly in plain language, unlike diffusion models. There is no
   separate negative-prompt channel or `strength` param anywhere in this codebase — both were SD1.5-only
   concepts tied to the removed Cloudflare fallback (see "Provider split" above) and were deleted along
   with it, not kept for interface compatibility.

   `tier_note` and `user_notes` (optional 3rd/4th args to `build_prompt()`) are inserted right after the
   structural lock, ahead of the tier's own generic content, so they can actually steer the render instead
   of being drowned out by the tier's defaults.

   **Real cost trap on the OpenAI side, found via a live test call, not docs**: `OPENAI_IMAGE_QUALITY`
   alone does NOT control cost - `OPENAI_IMAGE_INPUT_FIDELITY` is a separate param that also drives cost
   heavily (and controls "fidelity to the original input image(s)", i.e. structure preservation itself) -
   see `app/providers/openai.py`'s module docstring for the full story and current `low`/`low` setting.
   Also: `gpt-image-2` does not accept `input_fidelity` at all (400 error) - only `gpt-image-1` (and
   presumably `-mini`/`-1.5`) support it, which is why `OPENAI_IMAGE_MODEL=gpt-image-1` despite its
   Oct 2026 deprecation - a deliberate, informed choice, not an oversight.

   **v7: room-type common sense + explicit depth lock.** A real generation turned a hallway into a
   bedroom (added a bed/wardrobe that don't belong in a hallway) and changed a hallway's depth between
   tiers - both are "architecture changes" caused by the image model defaulting to generic "furnish this
   space" instincts rather than reasoning about what's actually in the photo. Root cause diagnosis: the
   old `describe_room()` prompt (`app/providers/gemini.py`) was written for the SD1.5-era 77-token budget
   ("under 15 words... extremely terse") and only asked for window/door positions and room shape - it
   never asked Gemini to name what *type* of space it's looking at or its depth/proportions, so that
   information never reached the image model at all. Fixed two ways: (1) `describe_room()` now asks
   Gemini to state the room's type (hallway, bedroom, living room, etc. - based on its own judgement of
   what's visible, not a fixed enum) and its approximate depth/proportions in plain sentences (no longer
   artificially terse, since OpenAI has no token-budget pressure); (2) a new always-on
   `ROOM_TYPE_COMMON_SENSE` sentence in `build_prompt()` (`app/pipeline/prompts.py`) instructs the model
   to only add furniture/decor realistic for the space it's actually looking at, and `PRESERVE_STRUCTURE`
   now explicitly locks the room's depth/size, not just walls/windows/proportions in the abstract.
   Deliberately **not** hardcoded to a fixed list of room types (e.g. only "hall" vs "bedroom") - the
   instruction leans on the model's own common sense about the specific photo, so it generalizes to
   whatever room type actually appears. `PROMPT_VERSION` bumped to `v7`.

   **v8/v9: per-tier `structure_reminder`, restated after the material instructions, for any tier that
   reworks the ceiling plane.** A real generation showed Premium hallucinating a different room (wrong
   column layout, narrower space) - its vivid luxury vocabulary (marble, brass, "designer ceiling") was
   strong enough to pull `gpt-image-1` off the input photo even with the universal structural lock present
   earlier in the prompt. Fix: `TIER_SPECS[tier]["structure_reminder"]`, when non-empty, is appended by
   `build_prompt()` right after the material/lighting/ceiling instructions - specifically *after* the
   vocabulary that causes the pull, not just alongside `PRESERVE_STRUCTURE` near the top (`v8`, Premium
   only at first). Threaded a `tier` argument through `Provider.generate_image()` -> `HybridProvider` ->
   `OpenAIImageProvider` so Premium also requests `input_fidelity="high"` from the real API call
   (`HIGH_FIDELITY_TIERS` in `openai.py`) while Economical/Mid keep the cheap configured default - a real
   API-parameter difference, not just prompt wording, deliberately scoped to only the tier that showed
   drift (costs more per the input_fidelity cost trap above).

   Side-by-side comparison of all three tiers (`v9`) surfaced the same failure mode in Mid: its `ceiling`
   field is `"false ceiling with a warm cove lighting strip"` - a real rework of the ceiling plane, same
   category of change as Premium's designer cove ceiling - but Mid's `structure_reminder` was still empty,
   so it had no counter-anchor after that instruction. A real generation showed Mid's room depth (indicated
   by receding ceiling/floor lines) flattening compared to Economical and Premium. Economical's `ceiling`
   field is explicitly `"no false ceiling"` - it never touches that surface, which is why it's the one tier
   that correctly needs no reminder. Fix: gave Mid the same generic reminder text Premium already uses (not
   reworded for any specific room type - the trigger is "this tier reworks the ceiling," which threatens
   depth in any room, not just hallways). `PROMPT_VERSION` bumped to `v9`.

   **Mid escalated to `input_fidelity="high"` too** (`HIGH_FIDELITY_TIERS` in `openai.py`, now
   `{"premium", "mid"}`). The `v9` prompt-only fix above was tried first per the cost-trap reasoning - but
   a later real generation showed the same failure shape recurring (camera angle/room geometry drifting)
   even with both prompt-level protections already in place (`PRESERVE_STRUCTURE`'s explicit "do not
   change the camera angle or perspective" line, plus Mid's own `structure_reminder`). Since the
   text-level fix was already present and drift continued, that confirms a fidelity/anchoring-strength
   issue rather than a prompt-wording gap - the same conclusion Premium's original fix was based on.
   Economical has shown no such drift and stays on the cheap configured default.

Async: FastAPI `BackgroundTasks` + client polling (no real queue yet — hardening-phase item). Each
Project's `meta_json` carries `PROMPT_VERSION` so outputs are reproducible/defensible.

Layout: `app/{main,config,db,models,schemas}.py`, `app/pipeline/` (prompts + orchestration),
`app/providers/` (base + gemini + openai + hybrid), `app/storage/` (base + local + s3), `static/`
(vanilla HTML/JS — no build step), `data/` (sqlite + local storage, gitignored), `tests/`.

### Frontend (`static/`)

Single-screen, clarity-first flow: upload → progress → results (or error), one state visible at a
time via `showState()` in `app.js` (the whole upload screen — hero + upload card + tier-explainer band
— is one `#upload-view` container toggled together) — never more than one primary action on screen.
Wired directly to the real backend: `POST /api/projects` (upload) then polls `GET /api/projects/{id}`
every 3s until `status: "done"`, rendering `images.{original,economical,mid,premium}` and
`room_description` from the response — no separate/mocked frontend data path. Deliberately restrained
by design decision (not an oversight): all four result cards are visually uniform (image + text label +
one-line descriptor, no per-tier color coding) so the *generated images* carry the tier differences,
not the UI chrome. **The progress screen is real-signal-driven, not a fixed timer or scripted checklist**
(this replaced two earlier designs - a `PROGRESS_MESSAGES` rotation, then a 9-item growing checklist -
both noted here only so this doc's history doesn't read as contradictory). Current design: a compact,
always-visible 4-stage stepper (`STAGES` in `app.js` - Analyzing/Generating/Estimating/Finalizing, each
with a pending/active-spinner/done icon, never a growing list) plus live concept preview cards
(`CONCEPT_PREVIEW_TIERS`) that resolve from a shimmering skeleton to the real thumbnail. Both are driven
by REAL data already present on every poll of `GET /api/projects/{id}` - per-tier image keys,
`materials_status`, `room_description` - not a script or a countdown; `deriveStageIndex()` computes the
current stage from that real data each poll, and the progress bar jumps in 4 discrete 25% increments as
stages complete (100% reserved for the real `done` response) rather than animating on a clock. Only the
very first stage has no finer-grained real signal to key off (describe_room/tier_notes run before the
image loop) - it shows immediately at submit and yields the moment anything real appears. A 15s
stall-reassurance timer fades in "Still working — almost there…" if nothing real has happened recently,
rather than leaving a bare spinner with no text. The standalone rotating `.spinner` that used to sit above
the main status text was removed as redundant once the stepper had its own active-stage spinner icon.
The landing screen carries a nav bar
(`Overview`/`Examples` anchors + a CTA scrolling to
`#upload-card`), a real **example showcase** (`static/examples/*.jpg` - actual pipeline output, not
mockups, picked for good structure preservation) at `#examples`, a three-card **tier explainer band**
(Economical/Mid/Premium finish descriptions), and per-result descriptors - copy for the explainer band
and descriptors is sourced from the real `prompts.py` tier methodology (paint→wood/mouldings→marble+brass,
cool→warm→luxury lighting), so keep them in sync if the tier specs change. `static/terms.html` and
`static/privacy.html` (served via `GET /terms` and `GET /privacy` in `main.py`) are simple, honestly-worded
placeholder pages appropriate for the prototype stage - not real legal review, flag to the user before
treating them as sufficient for an actual launch. Stays vanilla HTML/CSS/JS, no build step.
`tests/test_api.py`'s `test_full_upload_and_poll_flow` exercises the exact contract this frontend
depends on; `test_terms_page_serves`/`test_privacy_page_serves` cover the new routes.

## Testing convention

All provider calls in tests are **mocked** — no real Gemini, OpenAI, or SerpApi network calls in the test
suite (costs money / quotas are limited and must never be burned by CI). Pattern: monkeypatch the
module-level client call (see `tests/test_openai_provider.py`, `tests/test_pipeline.py` for the
FakeProvider pattern; `tests/test_gemini_materials.py` monkeypatches both `genai.Client` and
`gemini_module.serpapi.search` to test per-key client caching and the search→synthesis flow without a real
network call; `tests/test_serpapi.py` monkeypatches `httpx.get` to test `serpapi.search()` in isolation).
Prompt tests assert the three tier specs are mutually distinct and each
carries the preserve-structure instruction.

## Known limitations to keep in mind (not bugs to "fix" silently)

- gpt-image-1 preserves structure via prompt instructions + `input_fidelity`, not a hard geometric lock
  (e.g. ControlNet-depth) — real generations have shown drift (wrong room type, changed depth), addressed
  so far via prompt fixes (see Tier/prompt seam section above). ControlNet-depth is the eventual hardening
  upgrade if prompt-only fixes prove insufficient, but it requires direct access to a diffusion model's
  denoising loop (self-hosted, e.g. `diffusers` + a controlnet-depth checkpoint) - neither gpt-image-1 nor
  any previously-used hosted API exposes that, so it can't be bolted onto the current provider seam without
  new self-hosted infrastructure. Current prompt version is `v9` (`PROMPT_VERSION` in `prompts.py`) - see
  the Tier/prompt seam section above for the full v7/v8/v9 history.
- Gemini text quota (room description) is free/separate from image gen; image generation is paid
  (OpenAI). Localhost-only deployment is intentional for Phase 1.
- `GEMINI_IMAGE_MODEL` env var / Gemini image path is dormant, not deleted — kept for a possible future
  billing-enabled fallback.

## Git identity

This repo pushes to the **professional** GitHub account (`mhuzaifahahmed`) via the SSH alias remote
`git@github-mhuzaifahahmed:mhuzaifahahmed/interior-gen.git`, with per-repo local identity
`mhuzaifahahmed <mhuzaifah282@gmail.com>`. Never use `git config --global` here. See the machine-wide
`~/.claude/CLAUDE.md` for the full multi-account routing convention.
