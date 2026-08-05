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
  `room_layout_json`/`blueprint_keys_json`/`blueprint_status` (see "Free algorithmic blueprint step" below
  - a separate thing from `floor_plan_key`/`floor_plan_status`, don't conflate them), `meta_json`. New table
  → needed no entry in `db.py`'s `_migrate_missing_columns()` at first (that only covers adding columns to
  an *existing* table; `create_all()` creates new tables on its own) - but the blueprint fields, added
  later, DID need one, since by then `houseproject` was an existing table on dev machines. This surfaced a
  real gap: `_migrate_missing_columns()` had been hardcoded to `PRAGMA table_info(project)` only, so it
  silently never would have migrated `houseproject`. Fixed by generalizing it to `_NEW_COLUMNS_BY_TABLE`
  (a `{table_name: [(column, ddl), ...]}` dict) instead of a single flat list - keep using this form for any
  future new column on either table.
- **Endpoints** (`app/main.py`): `POST /api/house-projects` (multipart plot photo + `length`/`width`/
  `unit`/`prompt` form fields) and `GET /api/house-projects/{id}`, mirroring `create_project`/`get_project`'s
  exact validate → store → background-task → poll shape.
- **Pipeline** (`app/pipeline/generate_house.py`'s `run_house_pipeline`, mirrors `run_pipeline`): (1)
  best-effort `analyze_plot()` (Gemini vision, same call shape as `describe_room`) → `plot_description`;
  (2) best-effort `generate_floor_plan()` — see "Floor-plan vendor" below, currently always a no-op; (3)
  best-effort **free algorithmic blueprint step** — see below, a separate stage from (2); (4)
  **non-best-effort** `generate_house_render()` (OpenAI `gpt-image-1` edit call) → `render_key` - a
  failure here fails the whole house project, same treatment `generate_image` gets in the room pipeline,
  since it's this feature's core paid deliverable. Its image input is the ground floor's drawn blueprint
  when the blueprint step succeeded, falling back to the raw plot photo otherwise - see below. Prompts
  composed by `app/pipeline/house_prompts.py`'s `build_house_prompt()` (dimensions + optional
  plot_description + optional room_layout summary + optional truncated user prompt, capped at
  `USER_PROMPT_MAX_CHARS`), following the same "natural-language edit paragraph, not keyword soup" lesson
  learned for room-redesign prompts. `HOUSE_PROMPT_VERSION` is `v2` (bumped for this stage's addition).
- **Provider seam extension** (`app/providers/base.py`): 4 new abstract methods — `analyze_plot()` and
  `generate_floor_plan()` are best-effort (degrade to `None`, same contract as `describe_room`);
  `generate_house_render()` is not (mirrors `generate_image`); `generate_room_layout()` is never-empty/
  never-raising (same contract category as `generate_materials`, not `analyze_plot`). `GeminiProvider`
  implements `analyze_plot` and `generate_room_layout` for real, and `generate_house_render` by delegating
  to its existing `generate_image`; `generate_floor_plan` always returns `None` (Gemini has no floor-plan
  capability - that vendor slot is `app/providers/idealhouse.py`, composed in by `HybridProvider`, not this
  class). `OpenAIImageProvider.generate_house_render()` shares its HTTP call shape with `generate_image()`
  via a private `_edit_image()` helper, but stays its own public method so this feature's params never
  tangle with room-redesign's tier/fidelity semantics - it uses its own dedicated
  `OPENAI_HOUSE_INPUT_FIDELITY` setting (default `high`), deliberately separate from
  `OPENAI_IMAGE_INPUT_FIDELITY` (which the room-redesign Economical tier also reads), so raising the house
  render's fidelity never silently raises Economical's cost too.
- **Free algorithmic blueprint step** (`app/pipeline/floor_layout.py` + `app/pipeline/blueprint_svg.py`,
  wired into `run_house_pipeline` as its own stage): NOT the same thing as the `floor_plan_key`/
  `floor_plan_status`/`generate_floor_plan()`/`idealhouse.py` slot above, which stays reserved for a future
  *real, paid* floor-plan vendor and is untouched by this feature. This is a free, in-house alternative that
  solves the same underlying problem (image models can't honor exact dimensions) a different way: instead
  of asking an image model to draw a floor plan, `GeminiProvider.generate_room_layout()` (free text call,
  same quota as `analyze_plot`) returns a structured room list per floor - room names + a relative *area
  weight* per room, not literal square footage - and floor count guessed from the user's free-text prompt
  (default 1 floor). `app/pipeline/floor_layout.py`'s `layout_floor()` is a **pure, deterministic** function
  (no I/O, nothing to mock in tests) that slices the plot into non-overlapping room rectangles via simple
  recursive "slice and dice" (cutting the box along its longer side, at whichever split keeps the two
  resulting room groups' weights closest to balanced) - it always rescales to exactly fill the plot's real
  length × width, so it doesn't matter whether Gemini's area numbers are realistic, only their relative
  proportions. `app/pipeline/blueprint_svg.py`'s `render_floor_blueprint()` draws each floor's rectangles to
  a labeled PNG via **Pillow** (`PIL.ImageDraw`) - despite the module's name (kept for the concept it
  represents), it deliberately does NOT use real SVG/`cairosvg`: Pillow is already a dependency, draws
  straight to PNG (what both the frontend `<img>` and the `gpt-image-1` edit call need), and this avoids a
  new native-library dependency with a real install-risk on Windows, for a browser-crispness benefit not
  needed since there's no client-side interactivity in this version. Same reasoning ruled out Konva.js/
  Fabric.js/HTML Canvas entirely - those are client-side interactive JS libraries, no fit for a Python
  backend producing a static file. Both `generate_room_layout()` (via `fallback_room_layout()`, floor count
  guessed from a "N floor(s)" regex on the prompt, generic room list per floor) and `parse_room_layout()`
  (never `{}`, returns `None` on any structural problem to trigger that fallback) follow the exact
  never-empty/parse-then-fallback shape already established by `generate_materials`/`parse_materials`/
  `fallback_materials` - reuse that shape for any future structured-JSON Gemini call. The whole blueprint
  stage is wrapped in a local `try/except` in `run_house_pipeline` (best-effort, like `analyze_plot`/
  `generate_floor_plan`) - a bug in this newer code must not take down the render step; on failure,
  `blueprint_status="failed"` and the render step transparently falls back to the raw plot photo, with
  `build_house_prompt()`'s `using_blueprint_image` flag keeping the prompt's opening sentence accurate
  either way. Only the **ground floor's** (index 0) blueprint image is sent to `gpt-image-1` as the visual
  reference (confirmed design decision - it drives exterior massing most directly); upper floors are
  described in the text prompt only via `build_house_prompt()`'s `room_layout` argument, not shown as an
  image. Adjacency between rooms (e.g. "bathroom near bedroom") is deliberately out of scope for v1 - only
  area-based slicing - to ship the simpler algorithm first; a future upgrade could have Gemini also return
  adjacency hints for `layout_floor()` to try to honor.
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
- **"Concept Layout" labeling requirement**: whenever a real floor-plan vendor is wired in (the
  `floor_plan_key`/`floor_plan_status`/`idealhouse.py` slot, still inert), `renderHouseResults()`
  (`static/app.js`) must label that card **"Concept Layout — not a precise blueprint"**, never anything
  implying dimensional accuracy - no current image-gen floor-plan API guarantees exact measurements. The
  frontend already contains this exact label, gated on `floor_plan_status === "done"`, so it's ready the
  moment a vendor makes that state reachable; if `floor_plan_status` is anything else, no floor-plan card
  renders at all (not a broken placeholder). This is deliberately DIFFERENT from the free algorithmic
  blueprint cards (gated on `blueprint_status === "done"` and `blueprint_urls`), labeled "Floor N Layout"
  with a non-disclaiming description - those genuinely are dimensionally accurate (computed directly from
  the stated plot dimensions, not guessed by an image model), so they earn a different label. Don't merge
  these two labeling paths even though they look superficially similar.
- **Frontend**: `static/index.html`'s tab bar (`#tab-bar`, `switchTab()` in `app.js`) toggles between the
  room-redesign panel and the house panel independently - each keeps its own upload/progress/error/results
  state machine (`showState()` for rooms, `showHouseState()` for the house tab), so switching tabs mid-flow
  doesn't disturb the other tab's in-progress work. House progress uses the same stage-stepper CSS as
  room-redesign but with 4 stages (Analyzing plot / Drawing floor plan / Generating render / Finalizing) and
  no concept-preview cards, since there's one deliverable render this pass, not 3 tiers.
- **Tests**: `tests/test_idealhouse.py`, `tests/test_house_prompts.py`, `tests/test_house_pipeline.py`,
  `tests/test_house_api.py` (mirrors `test_api.py`'s pattern of mocking both `get_provider` and
  `get_storage`), `tests/test_gemini_house.py`, `tests/test_floor_layout.py` (pure unit tests, no mocks),
  `tests/test_blueprint_svg.py` (asserts real decodable PNG output), plus a `generate_house_render` case
  added to `tests/test_openai_provider.py`.

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

   **Per-tier variety via `random.choice()` on `paint`/`flooring`/`palette`/`decor`.** These four
   `TIER_SPECS[tier]` fields are now **lists** of phrasings (not single strings) - `build_prompt()`
   picks one via `random.choice()` per call, specifically so the same tier doesn't render the identical
   look on every generation for the same room. `label`, `lighting_temp`, `ceiling`, `feature_wall`,
   `materials`, `density`, and `structure_reminder` are unchanged, still single strings - only the four
   fields above got the randomized-list treatment. **Real bug hit and fixed while adding this**: a
   from-scratch rewrite of `TIER_SPECS` briefly dropped several of the single-string fields
   `build_prompt()` unconditionally reads (`label` itself, plus `lighting_temp`/`ceiling`/
   `feature_wall`/`materials`/`density`/`structure_reminder`), which crashed every generation
   (`KeyError`) since `build_prompt()` never got updated to match - a reminder that `TIER_SPECS` and
   `build_prompt()`'s field reads must always be edited together, whichever one changes first.
   `tests/test_prompts.py`'s tests that used to assert against a single fixed string per field (e.g.
   `TIER_SPECS["premium"]["paint"]` directly) now check "does the prompt contain whichever option was
   actually chosen this call" instead (e.g. `any(option in prompt for option in TIER_SPECS[tier]["flooring"])`,
   or finding the chosen option via `next(p for p in ... if p in prompt)` before asserting on its
   position) - re-run several times when touching these tests, since a naive fixed-string assertion
   will pass or fail depending on which random choice landed that run.

Async: FastAPI `BackgroundTasks` + client polling (no real queue yet — hardening-phase item). Each
Project's `meta_json` carries `PROMPT_VERSION` so outputs are reproducible/defensible.

Layout: `app/{main,config,db,models,schemas}.py`, `app/pipeline/` (prompts + orchestration),
`app/providers/` (base + gemini + openai + hybrid), `app/storage/` (base + local + s3), `static/`
(vanilla HTML/JS — no build step), `data/` (sqlite + local storage, gitignored), `tests/`.

### Frontend (`static/`)

**"Sage & Linen" visual system** (light, soft palette — warm linen/cream `#faf6ee` base, sage-mint
`#3f7a61` accent, soft beige containers, 12–22px rounded corners on cards/buttons/inputs, soft
mint-tinted hairline borders) — a full retheme of the prior **"Aurelian Monolith"** system (dark charcoal
`#131313` base, champagne-gold `#e9c176` accent, sharp 0px corners), done at the user's explicit request
for something calmer/warmer and less "flashy" than the old luxury-editorial look. The header and footer
use `bg-background` (the same linen tone as the page), not a separate white/`surface` shade — an explicit
user request so the chrome doesn't read as a different layer from the page.

**Two-font pairing: Ranade (headings/display) + Poppins (body/labels/technical data)** — replaced an
earlier one-font pass (Manrope for everything) at the user's explicit request to combine these two
specific fonts instead. `fontFamily` in each page's inline `tailwind.config` maps `headline-md`/
`headline-sm`/`display-lg`/`display-lg-mobile` to `["Ranade"]` and `body-md`/`body-lg`/`label-caps`/
`technical-data` to `["Poppins"]`; `components.css`'s hardcoded `.tier-name`/`.materials-open-btn`
font-family rules follow the same split. Poppins loads fine from Google Fonts' CDN. **Ranade does not** —
a real bug, not a theoretical one: linking Fontshare's own CDN (`https://api.fontshare.com/v2/css?f[]=
ranade@...`) returned a valid 200 CSS response with correct `@font-face` rules (confirmed via `curl`, and
the exact same font-file URL loaded fine when fetched manually via the `FontFace` API in-page), but when
loaded through a normal `<link rel="stylesheet">` tag the browser never registered any `Ranade` entry in
`document.fonts` at all and never issued requests for the referenced font files — headings silently fell
back to the browser's default serif with no console error. Root cause not fully pinned down (isolated to
something about that specific cross-origin CDN response/redirect chain, reproduced in headless Chromium,
not chased further once the fix below made it moot). **Fix: self-host Ranade** — the three weights used
(400/500/700; Fontshare's API doesn't offer a 600 for this family) were downloaded once from Fontshare's
CDN into `static/fonts/*.woff2`, and `static/fonts.css` declares plain same-origin `@font-face` rules
pointing at them; all three HTML pages link `/static/fonts.css` instead of the Fontshare URL. Self-hosting
sidesteps whatever cross-origin quirk caused the failure (same origin as everything else in `static/`, no
CDN redirect chain) and is more consistent with the no-build-step, self-contained doctrine anyway. If
Ranade's available weights ever need to change, re-fetch
`https://api.fontshare.com/v2/css?f[]=ranade@<weights>&display=swap` to find the current file URLs, don't
re-add the CDN `<link>` itself.

One more real bug hit during the retheme, worth remembering for the next one: several
`text-outline`/`text-on-background` usages on login/signup were tuned for the *old* dark theme, where
those tokens were light colors sitting on a dark page — on the new light theme the same class names now
point to muted/dark tokens, which either read as low-contrast on a light page (fixed by switching
label/icon text to `text-on-surface-variant`) or vanish against a dark decorative photo overlay (fixed by
hardcoding `text-white` on the signup hero's logo wordmark, which sits directly on an unfaded photo, not
the page background) — any theme swap needs to re-check every text color that sits on a decorative
photo/gradient, not just page-background text.

Component structure, copy, and functionality were unchanged by any of the above — this was a token/font
swap only. The retheme touched color tokens, font-family values, and `borderRadius` in each page's inline
`tailwind.config` (plus the matching hardcoded hex/font-family in `components.css` — see its top-of-file
comment — and a handful of decorative hex values baked directly into markup `style=`/`stroke=` attributes
for hero glows, dot-pattern backgrounds, and the login page's SVG grid).

Full Tailwind rebuild is still **Tailwind CDN**, no build step (inline `tailwind.config` script per page,
same config repeated in `index.html`/`login.html`/`signup.html`) — adopted at the user's explicit request
to use Stitch's actual markup/framework, not just its color values. See "Stitch reconciliation" below for
which mockup screens were used as a base and what was stripped (that reconciliation predates the retheme
and still describes the real backend-contract constraints, independent of the color/font choice).

`static/style.css` still exists **unchanged**, used only by `static/terms.html`/`privacy.html` (out of
scope for the rebuild — still on the old light vanilla theme, a known inconsistency, flag to the user
before treating those pages as done). `static/components.css` is the **real** stylesheet `index.html`
loads alongside Tailwind — it holds only the styles for HTML fragments `app.js` injects via
`.innerHTML` template strings (`result-card`/`img-wrap`/`caption`/`tier-name`/`tier-desc`/
`download-btn`, `stage-item`/`stage-icon`/`stage-label` + `@keyframes spin`, `concept-card`/
`concept-skeleton`/`concept-img`/`concept-label` + `@keyframes shimmer`, the materials table/note/
open-button classes) plus a few JS-toggled state classes (`dropzone.is-dragover`, `.is-changing` on the
progress message/subtitle, `.tab-btn.is-active`, `[data-reveal].is-revealed`) — these can't be Tailwind
utility classes because `app.js` builds them as plain semantic strings, independent of any CSS
framework. Everything else in the three HTML pages is plain Tailwind utilities in the markup.

**A real, easy-to-reintroduce bug, found and fixed during the rebuild**: Tailwind utility classes are
*author-origin* CSS, so a class like `.flex{display:flex}` beats the browser's *user-agent-origin*
`[hidden]{display:none}` rule at equal specificity (origin wins ties, not just specificity) — so any
element combining the native `hidden` **attribute** (which `app.js` toggles via `el.hidden = true/false`
on `#lightbox` and `#materials-modal-overlay`) with a Tailwind `flex`/`grid`/`block` class for its shown
state would render **visible even while "hidden"**. Fixed with one global rule in `components.css`:
`[hidden] { display: none !important; }`. Don't remove it, and don't reintroduce the bug by adding a
`hidden` Tailwind *class* (as opposed to the attribute) to an element `app.js` also toggles via
`.hidden = ` — pick one mechanism per element, never both (see `nav-auth-guest`/`nav-auth-user` for the
classList-only pattern used for elements *not* driven by the native attribute).

**Motion / GSAP animation conventions** — GSAP is loaded via CDN in `index.html`'s `<head>`
(`gsap.min.js` + `ScrollTrigger.min.js`, `gsap.registerPlugin(ScrollTrigger)` near the top of
`app.js`), governed by the `gsap-transitions` skill (`.claude/skills/gsap-transitions/SKILL.md`) —
read it before adding any new animation. Every GSAP-driven interaction in this codebase follows the
same two rules: (1) guard with `if (typeof gsap === "undefined" || prefersReducedMotion) { ...instant
fallback... }` so motion-sensitive users and a failed CDN load both still get a fully working, static
UI, never a broken/invisible one; (2) for any **staggered** tween inside a `gsap.timeline()`, use
`gsap.set()` for the start state followed by `.to()` for the end state — never `.from()` + `stagger`
+ an overlapping timeline position, which was observed live in this project to silently freeze at its
start values with the timeline itself reporting `progress() === 1`. See the skill file for the full
incident writeup.

**Dropdown menus specifically use a "morph" open/close animation, not an instant hidden-class
toggle** — established by the nav account menu (`#nav-user-menu` in `index.html`, `openNavUserMenu()`/
`closeNavUserMenu()` in `app.js`) at the user's explicit request that *every* dropdown added to this
site going forward reuse the same motion, "so everywhere having the same animation looks more
uniform." The reference implementation: on open, `gsap.set()` the menu to `{ transformOrigin: "top
right"` (or whichever corner it's anchored from), `scale: 0.85, opacity: 0, y: -8 }`, then `.to()` it
to `{ scale: 1, opacity: 1, y: 0, duration: 0.32, ease: "back.out(1.7)" }` for a springy "pop," with
its direct children staggered in right after (`gsap.set` to `{ opacity: 0, y: -6 }`, `.to()` to
`{ opacity: 1, y: 0, duration: 0.22, ease: "power2.out", stagger: 0.05 }`, started at `"-=0.18"` so it
overlaps the container's own settle). Close is a quicker, plainer fade (`scale: 0.9, opacity: 0,
y: -6, duration: 0.16, ease: "power1.in"`), with the `hidden` class re-applied only in `onComplete` —
never before the fade finishes, or the menu would visibly snap away instead of dissolving. Kill any
in-flight timeline (`if (tl) tl.kill()`) before starting a new open/close, same rapid-click-safety
discipline as the tab-switch timeline. **When adding a new dropdown, copy this exact easing/duration/
stagger recipe rather than inventing a new one** — that consistency is the whole point of the request.

Structure preserved from the original vanilla build: single-screen, clarity-first flow per tab (upload
→ progress → results/error, one state visible at a time via `showState()`/`showHouseState()` in
`app.js`), a **real-signal-driven progress stepper** (4 stages per tab, keyed off real poll-response
data, not a timer — see `deriveStageIndex()`/`deriveHouseStageIndex()`), live concept-preview cards for
the room tab, and a lightbox + materials modal. None of that logic changed in the rebuild — only the
markup/CSS around it did. `static/terms.html`/`privacy.html` (served via `GET /terms`/`/privacy`) remain
simple, honestly-worded placeholders, unchanged and unstyled to match the new theme (see above).
`tests/test_api.py`'s `test_full_upload_and_poll_flow` and `tests/test_auth.py` exercise the contracts
this frontend depends on; `test_terms_page_serves`/`test_privacy_page_serves` cover those two routes.

#### Stitch reconciliation (what was kept vs. discarded from the mockups)

Google Stitch produced 8 mockup screens + `aurelian_monolith/DESIGN.md` (all under
`stitch_archivision_ai_studio/`, kept in the repo as a design reference, not shipped code — 6 in the
original delivery, 2 more — login/signup — added later via
`stitch_archivision_ai_studio_login_and_signup.zip`, also kept). The mockups' visual system was
genuinely good and was fully adopted (see above), but every screen also invented a large amount of
functionality with **no backend support** — none of the fabricated parts were ported, and they must not
creep back in later. Recorded here so the reasoning doesn't have to be re-derived:

- **Base screens actually used** (user's explicit picks after reviewing screenshots): home page =
  `archai_home_luxury_edition`; room-redesign upload = `archai_luxury_room_redesign` (the "Room Redesign
  Studio" cinematic-hero version, not `room_redesign_upload_luxury`); login =
  `archai_login_luxury_edition`; signup = `archai_sign_up_luxury_edition`. House upload, both progress
  screens, and the room-results grid were never designed by Stitch at all — built fresh in the same
  visual language. House results loosely adapts `build_a_house_results_luxury`'s "Computed Floor
  Layouts" grid idea (mapped onto the real `blueprint_urls` cards, not that mockup's fabricated stats).
  `archai_luxury_home` and `room_redesign_upload_luxury` (the other home/room variants) and
  `archai_luxury_concept_results` (a fully fictional single-mansion "Build a House" results mockup) were
  not used as a base for anything.
- **Real backend contract (the only source of truth for what the UI may claim to do)**:
  `POST /api/projects` takes exactly `file`, `style_notes` (free text, ≤150 chars), `city` (≤80 chars,
  empty allowed); returns original + Economical/Mid/Premium images, `room_description`, per-tier
  materials (name/spec/price/currency/source_url/is_estimate + total), `materials_status`.
  `POST /api/house-projects` takes exactly `file`, `length`, `width`, `unit`, `prompt` (≤200 chars);
  returns `plot_description`, per-floor blueprint images (`blueprint_urls`), one render, an inert
  `floor_plan` slot. `POST /api/auth/{signup,login}` take exactly the fields in `SignupRequest`/
  `LoginRequest` (`app/schemas.py`). There are exactly **two generator tools** (Room Redesign, Build a
  House), a landing tab, and now login/signup — no third tool, no other pages.
- **Stripped — invented inputs with no backend field** (room-redesign upload mockups): a "Design
  Intensity" selector, "Elements to Preserve" checkboxes, a "Material Luxury" slider, an "Architectural
  Vibe" enum dropdown, style-preset chips wired to nothing, a fake system log/console, fake
  resolution-detection metadata, a "SCANNING_PROTOCOL_V4.2" label. Kept only the two real inputs
  (free-text style notes, city) inside the restyled "Style Parameters" card.
  - **How to keep them out (code-level)**: the real `<form id="upload-form">` in `index.html` posts
    exactly `style_notes` and `city` as form fields (see `app.js`'s submit handler and
    `main.py::create_project`'s `Form(...)` params) — any new input added to this form needs a
    matching `Form(...)` param added to `create_project` AND threaded through
    `run_pipeline(...)`/`build_prompt(...)` before it does anything; a purely decorative input (no
    matching backend param) is the exact mistake being guarded against here.
- **Stripped — invented outputs with no backend field** (home pages, house-results mockup): a third
  tool/tab ("Material Analysis"/"Structural Builds"), fake global-office listings, a fake project
  portfolio, precision metrics (±2mm spatial mapping, structural efficiency %, "STRUCTURAL INTEGRITY:
  99.8%", global building-code compliance, real-time BIM export), a "CAD-Ready" export claim,
  whitepaper/consultation CTAs, "Output Tiers" framed as CAD/DWG/DXF drafts + ISO-compliant BOM specs,
  per-floor bed/bath counts, build-cost estimates, solar %, structural load, carbon rating, lat/long
  site coordinates, Export-CAD/Share-Proposal/VR-Walkthrough/Reserve-Build-Slot buttons. The home
  hero's "01/02/03" stat row and floating image card were rewritten to state only true things (structure
  preservation, generation speed, real material pricing; "Real Output — actual pipeline result, not a
  mockup" over a genuine `static/examples/premium.jpg` image instead of a stock render).
  - **How to keep them out (code-level)**: `renderHouseResults()`/`renderResults()` in `app.js` only
    ever render fields that are actually present in the `GET /api/*-projects/{id}` JSON response
    (`ProjectStatusResponse`/`HouseProjectStatusResponse` in `app/schemas.py`) — a card or stat block
    with no corresponding schema field is fabricated UI by definition. Before adding any new
    result-card content, check whether the field exists in the schema first; if it doesn't, either add
    it for real on the backend or don't render it.
- **Login/signup — stripped**: the fake "Authentication sequence initialized"/setTimeout-then-`alert()`
  success simulation and the color-swap "Access Granted" fake success state (both replaced with a real
  `fetch()` to `/api/auth/login`/`signup` and a real redirect or inline error); the dead Google/Apple
  social-login buttons (no OAuth backend exists); the "Encrypted with AES-256" badge (a false technical
  claim — passwords are bcrypt-hashed at rest, the badge implied a specific transport-layer guarantee
  that was never actually implemented or verified); the placeholder text in Full Name/Email/Password
  (removed per user request — the mockup's fake example values like "Mies van der Rohe" read as
  pre-filled data, not a hint). **Also removed, per user request**: the visible Username and Role form
  fields — `signup.html`'s JS now **auto-derives** a username from the email's local part (sanitized to
  the required `[a-z0-9_]{3,32}` charset, see `app/auth.py::validate_username`) plus a short random
  suffix for collision-avoidance, retrying once with a fresh suffix on a `409`; `role` is simply omitted
  (already optional server-side). The user never sees or picks their own username now, even though it
  still exists and still names their S3 storage prefix (see "Authentication & per-user storage" below).
  **Real bug fixed**: Chrome/Edge force a light yellow/white autofill background via an internal
  `-webkit-box-shadow` inset trick that plain `background-color` CSS can't override — both pages'
  `<style>` blocks now include a `-webkit-autofill` override (transparent-looking inset box-shadow +
  `-webkit-text-fill-color`) so autofilled fields stay on the dark theme instead of flashing white.
- **Kept / reused**: the token-driven system itself (Tailwind config + the `components.css` fragment
  styles — now the "Sage & Linen" palette, see top of this section) so retheming again only means
  swapping token values, not markup; the lightbox pattern; the tab-switcher structure; the dropzone
  corner-accent framing and "Initialize Canvas"-style camera icon; the real example showcase (mapped
  onto `static/examples/*.jpg`); the login/signup visual layout (card + decorative blueprint-grid
  background / split hero panel), re-wired to real endpoints.
- **Standing rule**: no UI element (card, stat, button, badge) may imply data or a capability the
  backend doesn't actually produce. If a future design pass (Stitch or otherwise) suggests something
  new, check `app/schemas.py`'s response models first — if the field isn't there, it doesn't get
  rendered until it is.

#### Stitch MCP connection (attempted, currently broken server-side)

A `stitch` MCP server is registered at **local scope** for this project (`claude mcp add --transport
http stitch https://stitch.googleapis.com/mcp --header "X-Goog-Api-Key: ..."`, added via Stitch's own
"Export → MCP" panel) — the intent was to pull the live Stitch project's screens/code directly instead
of working from exported screenshots/zips. As of this writing, `claude mcp list` shows the server as
reachable but **tool listing fails**: `tools fetch failed — can't resolve reference #/$defs/ScreenInstance
from id #` — a malformed JSON Schema `$ref` in the server's own tool definitions, not something fixable
from this side. No `stitch`-prefixed tools have successfully surfaced in any session yet. If revisiting
this: check `claude mcp list` again (new sessions pick up newly-registered MCP servers; this project's
session that added it did not have the tools available mid-session), and if it's still broken, that's a
report-to-Google/Stitch issue, not a local config problem. The API key is stored in the MCP header config
(`.claude.json`, local/project scope), not in `.env` — it's a Stitch account key, unrelated to the
Gemini/OpenAI/SerpApi keys documented elsewhere in this file.

## Authentication & per-user storage

Real accounts, not a placeholder — added because the generators are gated behind login (user's explicit
choice: easier to keep each user's own inputs/prompts/outputs organized in S3 under their own username,
rather than the earlier flat `local.input|output/{project_id}/...` layout with no owner at all).

- **`User`** (`app/models.py`): `id`, `username` (unique, charset-validated `[a-z0-9_]{3,32}` by
  `app.auth.validate_username` — deliberately strict because it's also used verbatim as an S3 key path
  segment, so this charset can never produce a path-traversal or otherwise unsafe key), `email`
  (unique), `full_name`, `role`, `password_hash`. `Project`/`HouseProject` each gained a nullable
  `user_id` FK (nullable only so any pre-auth dev-DB rows still load — not backfilled, this is a dev
  prototype; every *new* row always gets a real owner).
- **`app/auth.py`**: password hashing uses the **`bcrypt`** library **directly** (`hashpw`/`checkpw`),
  deliberately **not `passlib`** — a real, live-hit compatibility break: `passlib` 1.7.4 (its last
  release, unmaintained) hard-fails against modern `bcrypt` (5.x removed the internal
  `__about__.__version__` attribute passlib's backend-detection probes for), confirmed by an actual
  `ValueError`/`AttributeError` when tested, not a theoretical concern. `get_current_user()`/
  `require_user()` read `request.session["user_id"]` (Starlette `SessionMiddleware`, signed cookie via
  `settings.session_secret_key` / `SESSION_SECRET_KEY` in `.env` — falls back to an insecure dev-only
  default if unset, fine for local dev, **not** for any real deployment).
- **Endpoints** (`app/main.py`): `POST /api/auth/signup` (username/email uniqueness + charset/length
  validation, hashes password, sets session), `POST /api/auth/login` (accepts username **or** email as
  `identifier`), `POST /api/auth/logout`, `GET /api/auth/me`. `GET /login`/`GET /signup` serve the
  static pages (same `FileResponse` pattern as `/terms`/`/privacy`).
- **Generator gating**: `create_project`/`create_house_project` require `Depends(require_user)` and set
  `project.user_id = user.id`. `get_project`/`get_house_project` return **404 (not 403)** for both
  "doesn't exist" and "exists but isn't yours" — deliberately indistinguishable so project ids aren't
  enumerable across accounts (a 403 would confirm the id is real).
- **Per-user S3 namespacing**: storage keys changed from `local.input|output/{project_id}/...` to
  **`users/{username}/input/{project_id}/...`** and **`users/{username}/output/{project_id}/...`** —
  `create_project`/`create_house_project` build the input key with the logged-in user's username, and
  pass `username` through to `run_pipeline(...)`/`run_house_pipeline(...)` (`app/pipeline/generate.py`,
  `generate_house.py`), which use it to build every output key. Both pipeline functions treat `username`
  as **optional** (falls back to the old flat `local.output/...` prefix when omitted) purely so direct
  unit tests (`test_pipeline.py`, `test_house_pipeline.py`) that call them without going through the API
  don't need updating — the real endpoints always pass it.
- **Tests**: `tests/test_auth.py` (signup/login/logout/me, duplicate username/email rejection, bad
  password, username-charset rejection, owner-only 404s). `tests/test_api.py`/`test_house_api.py` gained
  a `_signup_and_login()` helper (every generator test now signs up a fresh random-suffixed user first)
  and their storage-key assertions were updated to the `users/{username}/...` shape; also added
  `test_create_project_requires_login` / `test_cannot_view_another_users_project` (and house
  equivalents).

### Google OAuth ("Continue with Google")

Sits **alongside** username/password auth, not a replacement (user's explicit choice) — both work on the
same `User` table and both end the same way (`request.session["user_id"] = user.id`), so every other
auth-gated code path (`require_user`, generator gating, per-user S3 namespacing) needed zero changes.

- **Flow, plain `httpx`, no OAuth library**: `app/google_oauth.py` implements the three-request
  Authorization Code flow directly against Google's own endpoints (`build_authorize_url`,
  `exchange_code_for_token`, `fetch_userinfo`) — deliberately not `authlib` or similar, since the flow is
  only three requests and the project already depends on `httpx` for the Gemini/SerpApi providers. Kept in
  its own module (mirrors `app/providers/serpapi.py`'s shape) specifically so tests can monkeypatch
  `google_oauth.exchange_code_for_token`/`fetch_userinfo` directly — `app/main.py` calls them as
  `google_oauth.exchange_code_for_token(...)` (module attribute access), not a `from ... import
  exchange_code_for_token`, which is what makes that monkeypatching actually take effect.
- **Endpoints** (`app/main.py`): `GET /api/auth/google/login` — 503s if `GOOGLE_CLIENT_ID`/
  `GOOGLE_CLIENT_SECRET` aren't set (`google_oauth.is_configured()`), otherwise stashes a random CSRF
  `state` in the session and redirects to Google's consent screen. `GET /api/auth/google/callback` —
  validates `state` against the session (mismatch/missing → fail closed, redirect to
  `/login?error=google_auth_failed`, never a 500), exchanges the code, fetches userinfo, then
  find-or-creates the `User`: existing `google_sub` → log in directly; no `google_sub` match but the
  email already has a password account → **link** (set `google_sub` on the existing row, same account,
  not a duplicate) rather than creating a second account for the same person; no match at all → create a
  new `User` with a server-side-derived username (see below) and log in. Every failure path (bad state,
  Google returning `?error=`, the token/userinfo exchange itself failing) redirects to
  `/login?error=google_auth_failed` — `login.html`'s JS reads that query param and shows it in the same
  error slot password-login failures use.
- **`User.google_sub`** (`app/models.py`): nullable, set only for accounts that have ever signed in with
  Google. **Not** enforced unique at the DB level on existing dev databases — SQLite's `ALTER TABLE ADD
  COLUMN` (this project's whole migration mechanism, see `app/db.py`) can't add a `UNIQUE` constraint
  retroactively, so uniqueness here is application-level only (`get_user_by_google_sub` lookup before
  create) — acceptable for this dev prototype, same standard as the other additive-only migrated columns.
- **`User.password_hash` stays required, deliberately not made nullable for Google-only accounts** — same
  underlying SQLite constraint problem (an existing NOT NULL column can't be relaxed via `ALTER TABLE`
  without a full table rebuild). Instead, `app.auth.unusable_password_hash()` stores a real bcrypt hash of
  a random value nobody knows, so `POST /api/auth/login` naturally 401s for these accounts (no schema
  change needed, no special-casing in the login path either).
- **`app.auth.derive_username_from_email()`**: server-side re-implementation of `signup.html`'s
  client-side `deriveUsername()` — needed because Google sign-in creates the account entirely
  server-side, with no signup form (and thus no client-side JS) in between. Unlike the client-side
  version's single-attempt gamble, this one actually checks the DB and retries with a fresh random suffix
  (up to 5 times) until it finds a genuinely free username, rather than relying on a 409 retry loop that
  doesn't exist server-side.
- **Setup** (real steps, not hypothetical — walk through these to actually enable it): Google Cloud
  Console → create/select a project → **APIs & Services → OAuth consent screen** (User type External; add
  your own account under *Test users* while the app is in "Testing" mode, required until Google verifies
  it) → **APIs & Services → Credentials → Create Credentials → OAuth client ID** (Application type: Web
  application; **Authorized redirect URI** must exactly match `GOOGLE_REDIRECT_URI` below) → copy the
  Client ID/Secret into `.env` (never `.env.example`, same convention as every other real credential in
  this file):
  ```
  GOOGLE_CLIENT_ID=...
  GOOGLE_CLIENT_SECRET=...
  GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/auth/google/callback
  ```
  Leaving these blank keeps Google sign-in cleanly disabled (`google_login()` 503s) without touching
  password auth at all.
- **Frontend**: `login.html`/`signup.html` each gained an "OR" divider + a "Continue with Google" button
  (official multi-color "G" mark inlined as SVG, not an icon font) linking straight to
  `/api/auth/google/login` — no JS involved on the button itself, it's a plain link, since the whole flow
  is server-side redirects.
- **Tests**: `tests/test_google_oauth.py` — monkeypatches `google_oauth.exchange_code_for_token`/
  `fetch_userinfo` (never a real Google network call, same testing convention as every other provider).
  Covers: 503 when unconfigured, a real `state` round-trip through `/login` → `/callback`, new-account
  creation, linking to a pre-existing password account by email, a second Google login reusing the
  already-linked account (not creating a duplicate), and all three failure paths (state mismatch, Google's
  own `?error=`, the token exchange raising `httpx.HTTPStatusError`) redirecting to
  `/login?error=google_auth_failed` instead of 500ing.

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
