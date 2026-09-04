# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working with the user

Whenever an issue/bug is fixed, explain what was wrong and how it was fixed in detail, in the very next
message right after the fix is done - not deferred to a later summary, not skipped. The user relies on
these explanations to understand what happened and to relay updates (e.g. to their own lead/team).

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
- **Self-hosted models: Kaggle → Modal → Kaggle again (2026-08).** Read this whole entry before touching
  `IMAGE_PROVIDER`/`HOUSE_IMAGE_PROVIDER` — the default has flipped twice, for two different real reasons,
  and could flip again.
  - **Kaggle → Modal (first switch)**: see the "Why Modal replaced Kaggle" bullet below — a second Kaggle
    account's phone verification rejected every number tried (confirmed regional gap, not user error).
  - **Modal → Kaggle (second switch, 2026-08-20)**: Modal's cold starts (2-3 min after ~2 min idle, even
    with the GPU memory snapshot optimization — see "Cold starts" below) were judged too slow for real
    usage. Separately, the original phone-verification blocker was worked around by getting **3 Kaggle
    accounts from 3 different people** — so Kaggle became viable again. `IMAGE_PROVIDER` is back to
    `"kaggle"` (default in `app/config.py` and `.env`) for room-redesign. `HOUSE_IMAGE_PROVIDER` stays
    `"openai"` for now — no trained house model exists yet — but the plumbing for `"kaggle"` there is
    ALREADY built in advance (2026-08-20 follow-up, before the model itself was ready, so wiring it in
    later is a config change only): `KaggleImageProvider.generate_house_render()` exists, using its own
    dedicated `settings.kaggle_house_api_url` (a separate Kaggle account/notebook from room-redesign's,
    same room-vs-house URL split already established for Modal). Its request/response contract is an
    ASSUMED, NOT YET CONFIRMED shape (same as `generate_image()`'s: `POST {url}/generate`,
    `{"image_base64", "prompt"}` → `{"status", "generated_image_base64"}`) — every Kaggle notebook in this
    project so far has used that exact shape, but if the real house model's endpoint differs (different
    path, job-polling like the batch endpoint, different param/response keys), update only
    `generate_house_render()` and `_generate_url()`'s house counterpart to match — nothing else in the
    pipeline needs to change, since callers only ever invoke the generic `generate_house_render(image_bytes,
    prompt)` shape regardless of backend. No prompt-shortening is applied yet either (unlike room's CLIP
    77-token handling) since the house model's actual token-limit behavior isn't confirmed - add a
    house-specific equivalent of `_prepare_kaggle_prompt()` if that turns out to be needed, don't reuse the
    room one (it's built from `TIER_SPECS`, a room-redesign-only concept). `hybrid.py`'s
    `_default_house_image_provider()` already routes `house_image_provider="kaggle"` to this. To activate:
    set `KAGGLE_HOUSE_API_URL` + `HOUSE_IMAGE_PROVIDER=kaggle` in `.env` once the model is live — no code
    change needed. The Modal house-render model was separately flagged by the user as "not ok at all" and
    is being replaced with this Kaggle one — Modal code stays in the repo, dormant, not deleted, in case
    it's needed again. The user also floated a possible 3rd Kaggle account for an AI-drawn "autocad map"
    (floor plan) — **not built**: this project already tried and reverted an AI-image-model-drawn floor
    plan (see "v4" under Build a House below) because the image model hallucinated dimension/area text; the
    free, deterministic Pillow-drawn blueprint (`blueprint_svg.py`, "v5" below) replaced it and stays the
    only floor-plan renderer unless a specific model proven to avoid that failure mode is confirmed first.
  - **Real, recurring trade-off of Kaggle vs. Modal, worth remembering next time this flips**: Kaggle has
    no permanent URL — its Cloudflare tunnel URL changes every time the notebook session restarts, so
    `KAGGLE_API_URL` in `.env` needs manual updating each time — plus a 30 GPU-hr/week quota per account.
    Modal has a permanent URL and effectively unlimited pay-per-use quota, but real cold-start latency
    (2-3 min after ~2 min idle) unless paying for `min_containers=1` to stay warm continuously (a real,
    large recurring cost — roughly $0.60/T4-hour, so ~$440/month for one model kept warm 24/7, not a
    one-time "subscription" — Modal has no flat plan that removes cold starts, only pay-per-second
    compute). Kaggle, in turn, has **no paid tier at all** — its GPU quota is a fixed platform limit with
    no way to buy more, at any price, and no way to keep a session running indefinitely even if quota
    remains (hard ~9-12hr session runtime cap regardless of payment).
  - Original migration story, unchanged below. A parallel, free/self-hosted alternate image backend
  (`RealVisXL_V5.0` + `xinsir/controlnet-depth-sdxl-1.0`, classic SD-style img2img with
  `negative_prompt`/`num_inference_steps`/`guidance_scale` — NOT the instruction-following OpenAI shape)
  was first hosted on a **Kaggle notebook + Cloudflare quick tunnel** (`app/providers/kaggle.py`), then
  migrated to **Modal** (`app/providers/modal_provider.py`) — both still exist in the codebase, Kaggle kept
  dormant not deleted, selected via `IMAGE_PROVIDER`/`HOUSE_IMAGE_PROVIDER` toggles in `app/config.py`.
  - **Why Modal replaced Kaggle as the default**: hosting a SECOND model (for "Build a House") needed a
    second Kaggle account, and its phone-verification step rejected every number tried, including via
    VPN — later confirmed as a genuine **regional gap, not user error**: several platforms (NVIDIA's own
    developer forums have many threads on this) are missing Pakistan (+92) from their SMS-OTP country
    dropdown entirely, so no number could ever have worked. Modal's signup is GitHub/Google OAuth only,
    no phone step anywhere in the flow — that's what actually unblocked hosting more than one model.
  - **`IMAGE_PROVIDER`** (room-redesign backend): `"kaggle"` (default as of 2026-08-20) →
    `KaggleImageProvider`, `"modal"` → `ModalImageProvider` (kept configured, dormant), `"openai"` → falls
    through to the always-real OpenAI fallback.
    **`HOUSE_IMAGE_PROVIDER`** (Build a House backend): `"openai"` (default as of 2026-08-20 — no Kaggle
    house model exists) or `"modal"` (dormant) — a SEPARATE
    toggle from `IMAGE_PROVIDER`, following this file's established room-vs-house settings-isolation
    pattern, so switching one never silently changes the other. Kaggle was NEVER wired into house
    rendering (its model was trained on interior redesign only) — Modal is the first self-hosted option
    for house renders.
  - **`HybridProvider`'s three-way provider split, a real bug avoided by design**: `_openai` (ALWAYS a
    real `OpenAIImageProvider`, never swapped by any toggle — the universal reliability fallback for a
    failing room provider), `_house_image_provider` (resolved from `HOUSE_IMAGE_PROVIDER`, used ONLY for
    `generate_house_render`), `_room_image_provider` (resolved from `IMAGE_PROVIDER`, used for
    `generate_image`/`generate_images_batch`). These were previously conflated (`_house_image_provider`
    doubled as the room-redesign fallback target) — that broke the moment `HOUSE_IMAGE_PROVIDER` could
    also be `"modal"`: a failing Modal room provider would have fallen back to a *also potentially
    failing* Modal house provider, defeating the whole point of the fallback. Fixed by keeping `_openai`
    as its own always-real attribute, independent of either toggle.
    `test_generate_image_falls_back_to_openai_not_to_the_house_provider_when_room_fails` guards this.
  - **What actually changed technically, not just the hosting provider**: Modal has no free-tier
    equivalent of Kaggle's Cloudflare quick-tunnel ~100s response timeout (the real, live-hit constraint
    that forced `kaggle.py`'s submit-then-poll job pattern — a plain blocking batch call was timing out
    at the tunnel's edge even when the GPU work itself was succeeding). `modal_provider.py`'s
    `generate_images_batch()` is therefore simpler: ONE blocking HTTP call to a Modal web endpoint, no
    job-status polling. The GPU-memory lessons carry over unchanged, since they were T4-VRAM-driven, not
    platform-driven — Modal's T4 has the identical 16GB ceiling: `attention_slicing` + `vae_slicing`
    enabled, 768×768 (not 1024) for the 3-tier batch call specifically (a real batch-of-3 test OOM'd
    during VAE decode at 1024×1024 even with slicing enabled), and an OOM-safe sequential fallback inside
    the Modal deployment itself if the batched call still exceeds VRAM.
  - **Deployment shape**: Modal has no notebook UI at all (unlike Kaggle) — the model-loading + generation
    code lives in plain `.py` files, checked into this repo under `modal/` (`modal/modal_room_redesign.py`,
    `modal/modal_house_generation.py` — unlike the old Kaggle notebook, which was only ever pasted by hand
    into Kaggle's UI and never version-controlled, these ARE real source files, since a Modal deployment is
    meant to be reproducible via a plain CLI command). Deploy/redeploy with
    `modal deploy modal/modal_room_redesign.py` (or the house one) from a local terminal, which prints a
    **permanent** HTTPS URL (unlike Kaggle's tunnel URL, this doesn't rotate on its own — no more "update
    `.env` every time the notebook restarts", only redeploy when the code itself changes). Two separate
    Modal apps (`room-redesign`, `house-generation`), matching the existing room-vs-house provider
    separation — each with its own model instance/cache Volume, so a change to one never risks the other.
    Model weights are cached in a `modal.Volume` so only the first cold start pays the ~14GB Hugging Face
    download.
  - **Cost**: Modal's free tier is $30/month compute credit (GitHub/Google OAuth, no card required to
    start) billed per-second — on a T4 (same GPU class Kaggle gave), a ~90s 3-tier batch generation costs
    roughly $0.02, i.e. **~1,500 full generations/month free**, with zero idle cost between requests
    (unlike a Kaggle session, which burns its weekly quota just sitting open).
  - **Cold starts are the real, inherent trade-off of any scale-to-zero serverless GPU platform** — not a
    Modal-specific defect, and not fixable by raising `timeout` (that only controls how long Modal waits
    before killing a slow function; it does not reduce actual wall-clock time). `scaledown_window=120` on
    both `@app.cls` decorators is what actually controls this: after 2 minutes with no requests (a value
    WE chose, not something Modal does automatically on a fixed timer), the container shuts down, and the
    next request pays the cost of reloading the ~14GB model onto the GPU from scratch. Real, live-measured
    numbers: a fully cold container (house-generation, single image) took **~274s**; a warm one, **~90-135s**.
    **GPU memory snapshots** (`enable_memory_snapshot=True` + `experimental_options={"enable_gpu_snapshot":
    True}` + `@modal.enter(snap=True)`) — an ALPHA Modal feature that snapshots the container's memory
    (including the model already loaded onto the GPU) after the first successful start, so future cold
    starts restore from that snapshot instead of reloading from scratch — cut this roughly in half in real
    testing: house-generation cold start dropped from ~274s to **~120s**; room-redesign's 3-tier batch cold
    start came in at **~137s**, landing within the normal warm-request range (89-186s) rather than clearly
    separate from it. Real risk, stated directly in Modal's own docs: alpha status, can behave
    unpredictably (multi-GPU setups, `torch.compile` interactions) — kept enabled anyway since it's free
    and the measured benefit was real, but if generations start failing in ways that look snapshot-related,
    this is the first thing to try disabling. `startup_timeout=300` is also set explicitly on both deployments
    (separate from the regular `timeout=600`, which otherwise covers both container-startup AND
    execution time combined per Modal's docs) — pure headroom, not something that's actually been hit.
    Genuinely eliminating cold starts (not just shortening them) would mean keeping a container always
    warm (`min_containers=1` or a scheduled keep-alive ping) — real money on Modal's per-second billing
    (a T4 kept warm 24/7 would burn through the entire $30/month free credit in about 50 hours), so this
    was deliberately not done; the accepted trade-off is "free, but the first request after ~2 minutes
    idle costs an extra ~2 minutes" rather than "always instant, but costs real money every month."
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

- **Real user-supplied room measurements (2026-08), replacing the Gemini-guess-only quantity input.**
  Previously the ONLY source of room floor area for materials pricing was `estimate_room_area()`
  (`GeminiProvider`) - a pure vision guess from the photo, injected into `generate_materials()`'s prompt
  where Gemini itself does the per-sqft×area / per-fixture math (no Python-side multiplication exists or
  was added - that stays entirely LLM-side). Now the upload form (`static/index.html`, next to `#city-input`)
  also has optional **Length / Width / Height + unit (ft/m)** inputs (`#room-length`/`#room-width`/
  `#room-height`/`#room-dimension-unit`). All three are optional; Length+Width together compute a real
  `area_sqft` via `app/main.py`'s `_compute_room_dimensions()` (meters converted to feet, sanity-bounded
  1-200ft per dimension to reject obvious garbage); Height is independently optional ON TOP of that and
  additionally computes `wall_area_sqft` (`2*(L+W)*H`, a rectangular-room assumption) for Paint/wall-finish's
  own quantity rule specifically, since paintable wall area and floor area are different numbers.
  **A real measurement is authoritative and skips the Gemini guess entirely** - `run_pipeline()`
  (`app/pipeline/generate.py`) only calls `provider.estimate_room_area()` when the user didn't supply one;
  when they did, that call doesn't happen at all (not just overridden after the fact - a wasted API call
  avoided). `Provider.generate_materials()` gained an optional `wall_area_sqft` param (threaded through
  `HybridProvider`) alongside the existing `room_area_sqft` one; `GeminiProvider.generate_materials()`
  appends a separate sentence to the pricing prompt's `area_block` telling Gemini to use `wall_area_sqft`
  (not floor area) specifically for Paint's multiplication rule, when given. Persisted as
  `Project.room_dimensions_json` (`{"length","width","height","unit","area_sqft","wall_area_sqft"}`,
  same JSON-as-text convention as `HouseProject.dimensions_json`) - null when the user left the fields
  blank, in which case pricing behaves exactly as before this feature (Gemini-guess fallback). Also
  mirrored into the S3 input `metadata.json` (see below) and exposed back to the frontend via
  `ProjectStatusResponse.room_dimensions`. Leaving all three fields blank is a fully supported,
  zero-friction path - this is a "more accurate if given" input, never a required one.
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
  image. Adjacency between rooms was originally out of scope for v1 (only pure area-based slicing) - a
  full per-room adjacency solver is still not built, but a real, live-user-reported problem this caused
  ("no sense of living room being in the middle") was fixed on 2026-08-25 with a much smaller, targeted
  change: **public/private zone grouping** (`app/pipeline/floor_layout.py`). `_slice()` itself is
  completely unchanged, still pure weight-balanced recursive splitting - the fix is a single STABLE sort
  of the room list, BEFORE slicing, into a "public zone" (garage, entry/foyer, living/lounge/family/
  drawing, dining, kitchen, powder/guest bath, study/office - same keyword vocabulary as
  `blueprint_svg.py`'s furniture dispatcher) ahead of everything else (the "private zone" - bedrooms,
  bathrooms, hallways, storage, unrecognized names). Since `_slice()` always splits a *contiguous* sublist,
  reordering the input is enough to guarantee public rooms cluster together and private rooms cluster
  together as two connected regions, without needing to touch the splitting algorithm itself. Being a
  STABLE sort, it also preserves whatever fine-grained order Gemini/fallback already gave rooms *within*
  a zone - e.g. "Master Bedroom" immediately followed by "Master Bathroom" in the input list stays
  adjacent in the output, confirmed both by a real generation (Master Bedroom ended up directly next to
  Master Ensuite Bathroom) and by `tests/test_floor_layout.py`'s connectivity/adjacency regression tests
  (`test_layout_floor_groups_public_rooms_together_not_scattered`,
  `test_layout_floor_public_rooms_form_one_contiguous_block`,
  `test_layout_floor_preserves_relative_order_within_a_zone`). A full adjacency-graph solver (the harder
  problem - e.g. Gemini also returning explicit adjacency hints for `layout_floor()` to honor) is still
  not built and would be the next step up if zone-grouping alone proves insufficient.
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
- **Real DXF/AutoCAD-format export - researched 2026-08-24, BUILT the same day.** User tried multiple
  self-hosted AI models on Kaggle looking for one that outputs genuine AutoCAD-format geometry and got
  garbage every time ("random lines and shapes... not AutoCAD format at all"). Root cause, confirmed via
  live research, not assumption: **every model that was tried was almost certainly an image-diffusion
  model** (Stable-Diffusion-family) asked to *draw a picture that looks like a CAD drawing* - that always
  produces meaningless pixel noise dressed as line art, because diffusion image models have no concept of
  geometry/coordinates, only pixels. This project already hit and documented the exact same failure mode
  once before (see "v4" above - an image model hallucinating garbage dimension text on a floor plan,
  reverted to the deterministic Pillow renderer "v5"). **A real AutoCAD file (DXF/DWG) is coordinate data,
  not an image** - the fix is a model class that outputs actual vector geometry (room polygons/line
  segments as numbers), not pixels, which can then be converted to a real `.dxf` deterministically.
  - **Built: zero-risk path, no AI model at all.** This codebase's own `app/pipeline/floor_layout.py`
    already computes exact room rectangles (real coordinates, in real feet/meters, from the user's stated
    plot dimensions) for the existing Pillow-drawn blueprint (`blueprint_svg.py`). That data is genuine
    vector geometry - it was just previously only ever rendered to a PNG. **`app/pipeline/blueprint_dxf.py`**
    (`render_floor_blueprint_dxf(floor_number, rects, dimensions) -> bytes`) now feeds those SAME
    rectangles into **`ezdxf`** (`ezdxf>=1.3` in `requirements.txt`, MIT license, pure Python, no GPU, no
    Kaggle hosting) to produce a real, valid `.dxf` file (R2010 format) that opens in actual AutoCAD - one
    per floor, alongside (not replacing) the existing PNG. Pure/deterministic like `blueprint_svg.py` -
    no I/O, geometry only (room outlines on a `ROOMS` layer + the exterior boundary on `WALLS` + a
    name/dimensions `TEXT` label per room + a title line), deliberately NOT replicating the PNG's
    furniture/door/window symbols (those are picture-presentation heuristics, not information a DXF
    consumer needs duplicated). `$INSUNITS` is set from the stated unit (`ft`→2, `m`→6) so the file opens
    at the correct real-world scale.
    - **Wired into `app/pipeline/generate_house.py`'s existing blueprint step**: right after each floor's
      PNG is generated and stored, its DXF is generated and stored too, in its own nested `try/except` so a
      DXF-serialization bug can never take down the PNG blueprint (which the render step depends on as its
      image-edit reference). Keys collected into `HouseProject.blueprint_dxf_keys_json` (new column,
      additive `_NEW_COLUMNS_BY_TABLE` migration in `db.py`) - same floor-ordered JSON-list-of-keys
      convention as `blueprint_keys_json`, may be shorter than it (or empty) if DXF export failed for some/
      all floors while the PNG still succeeded. Exposed as `HouseProjectStatusResponse.blueprint_dxf_urls`.
    - **Frontend**: each "Floor N Layout" card (`renderHouseResults()` in `static/app.js`) gets a
      "Download AutoCAD File (.dxf)" link (reusing the existing `.materials-open-btn` style) below its
      caption, shown only when that floor's DXF export actually succeeded - omitted otherwise, no broken
      link.
    - **Tests**: `tests/test_blueprint_dxf.py` (parses the output back with `ezdxf.read()` to confirm it's
      a genuine, valid DXF, and asserts the room polygon coordinates exactly match `layout_floor()`'s
      numbers - the whole point of this feature is that the DXF can never disagree with the PNG since both
      come from the identical rectangles); `tests/test_house_pipeline.py`/`test_house_api.py` extended to
      cover the new key/URL.
  - **v2 (2026-09-02): real structured geometry, not bare boxes.** Real, direct user feedback: "its just
    making the boxes to the side and telling us hey your autocad is done see there is no structure or
    intelligence involved" - fair criticism of v1, which only wrote room rectangles + text labels + the
    plot boundary, none of the wall/door/window geometry `blueprint_svg.py` already computes for the PNG.
    Fixed by SERIALIZING that same already-computed geometry into real DXF entities instead of discarding
    it - no new logic invented, reuses `blueprint_svg.py`'s own `_shared_edge()` (which two rooms share a
    wall) and `_should_suppress_direct_door()` (the real-circulation-corridor door rule, see that module's
    docstring) directly, so the PNG and the DXF can never disagree about which rooms connect to which.
    - **Real-thickness walls**: `WALL_THICKNESS_M` (0.15m/~6in, a standard residential interior wall,
      converted to the plot's unit via `room_specs.to_plot_unit()`) - drawn as ezdxf `LWPOLYLINE`s with
      `const_width` set, a genuine DXF "wide polyline" attribute that renders with real visible thickness
      in any CAD viewer, not a thin line pretending to be a wall. Exterior walls are derived per-room (only
      the edges that actually lie on the plot boundary), one segment per room's contribution - never drawn
      twice. Interior walls are derived from `combinations(rects, 2)` + `_shared_edge()` (each shared wall
      drawn exactly once, not once per room either side of it).
    - **Real door/window OPENINGS, not just symbols drawn on top.** Unlike a raster PNG (where a door can
      be "erased" as a gap in solid poché), a DXF wall is line geometry - so a real opening means splitting
      the wall polyline into two segments with an actual gap between them, not one continuous line with a
      symbol overlaid on it. `_draw_gapped_wall()` does this for both doors (`DOOR_WIDTH_M`, ~0.9m/3ft,
      interior walls only, respecting the same suppression rule as the PNG) and windows (`WINDOW_WIDTH_M`,
      ~1.2m/4ft, exterior walls only) - centered on the segment's midpoint, same heuristic-placement
      precedent `blueprint_svg.py`'s own door/window functions already established. A door gets a leaf line
      + quarter-circle swing arc (`DOORS` layer); a window gets a single glazing line (`WINDOWS` layer) -
      both drawn directly in the plot's real coordinates, no pixel/scale conversion needed (unlike the PNG
      renderer, ezdxf works natively in real-world units).
    - **Still deliberately NOT included**: furniture (a picture-presentation heuristic for the PNG, not
      information a DXF consumer needs duplicated - unchanged scope from v1) and filled poché (CAD users
      want editable line geometry per wall, not a filled black region - the PNG stays the only poché
      renderer; walls here are real LINE entities you can select/edit individually in AutoCAD).
    - **Verification**: real output validated with `ezdxf`'s own `doc.audit()` (0 errors - not just
      "parses," genuinely well-formed), and visually reasoned through by inspecting every wall segment's
      real coordinates directly (confirmed gaps land exactly where doors/windows should be, exterior walls
      cover the full perimeter, interior partition walls appear at real room boundaries).
    - **Tests rewritten**: the v1 tests assumed one closed rectangle-polyline per room on a `ROOMS` layer -
      structurally incompatible with v2's split wall-segment model, so they were rewritten (not just
      patched) to check the new real properties: `const_width > 0` on every wall polyline, the exterior
      perimeter is fully covered, a door exists between two rooms with no hallway, a door is genuinely
      absent between two non-bathroom private rooms when a hallway exists (checked by real coordinate
      position, not just presence/absence), windows exist on exterior-facing rooms. 434/434 passing.
  - **The actual AI research models that generate true vector floor-plan geometry** (for if organic,
    non-rectangular AI-designed layouts are wanted later, beyond what the deterministic slice-and-dice
    algorithm produces) - four found, evaluated for real commercial hostability, not just "does it exist":

    | Model | What it outputs | Pretrained weights? | License (commercial use?) | Training data | Verdict |
    |---|---|---|---|---|---|
    | **HouseDiffusion** (`aminshabani/house_diffusion`, CVPR 2023) | Room polygons via diffusion denoising | Yes, a "temporary" Google Drive link | **Explicitly NON-commercial** - the repo's own LICENSE states "The code and the model weights in this repository are not allowed for commercial usage" (custom license layered on GPLv3) | RPLAN | **Disqualified for this B2B product** - the license says so in plain text, not a gray area |
    | **House-GAN++** (`ennauata/houseganpp`, CVPR 2021) | Room polygons via graph-constrained GAN, iterative refinement | Yes, `test.py` runs a released checkpoint | Plain **GPLv3** - commercial use IS allowed, but copyleft applies to anything the code is linked/distributed with (running it purely as a self-hosted backend service that never ships the code to end users is the standard way SaaS products use GPL code without triggering redistribution obligations - not AGPL, so no "network use counts as distribution" clause - but this is not legal advice, worth a real lawyer's 10 minutes before shipping) | **RPLAN** - restricted-access, explicitly "non-commercial research and academic purposes" only, no redistribution | **Legally murky even though the code license allows commercial use** - the released *weights* were trained on data whose own license forbids non-research use; using someone else's RPLAN-trained checkpoint commercially risks violating RPLAN's terms even though House-GAN++'s own code license doesn't forbid it |
    | **FloorplanGAN** (`luozn15/FloorplanGAN`) | Room polygons, vector generator + raster discriminator, explicitly designed to export to **DWG/SVG directly** (closest to "actual AutoCAD format" of anything found) | **No** - repo says evaluation/pretrained-model docs are "Coming soon..." with no working download found | Code is MIT (commercial-friendly) | RPLAN | **Best output format on paper, but not currently usable** - no weights to download means training from scratch on RPLAN would be required, and RPLAN itself is non-commercial-only, so even a self-trained model would carry the same data-license risk as House-GAN++ |
    | **FloorGenT** (`lericson/floorgent`) | Line segments as a token sequence (autoregressive, GPT-style) | Not found/documented | Not stated in the repo | Not RPLAN-based (uses its own simulated/sensor data) - the one candidate NOT tied to RPLAN's restriction | Interesting for a from-scratch retrain (clean data-license story) but immature - no ready weights, research-prototype maturity only |

    Also checked and ruled out: **GSDiff** and **C2Plan** (2025/2026 papers, no confirmed public
    weights/license found - too new to evaluate); **CubiCasa5K** (a *different* task - vectorizes an
    *existing* floor-plan photo into SVG, doesn't generate a new layout from dimensions, so it doesn't fit
    this pipeline's actual need); every purely commercial SaaS tool that claims "AI floor plan → DXF"
    (Maket.ai, Plans2BIM, ai-architectures.com, etc.) - none is self-hostable on Kaggle, they're closed
    cloud APIs, the opposite of what was asked for.
  - **The one commercially-clean dataset found: `m-agour/ResPlan`** - 17,000 real residential floor plans
    in vector format, **CC BY 4.0** (commercial use explicitly permitted, attribution required), code MIT.
    **No pretrained generative model ships with it** - only benchmark/evaluation baselines. This is the
    real path to an eventually-commercial, from-scratch-trained AI floor-plan generator with a clean
    license story (e.g. reproducing FloorplanGAN's or House-GAN++'s architecture but training it on
    ResPlan instead of RPLAN) - real engineering effort (weeks, GPU training time), not a drop-in model,
    and not started.
  - **Bottom line / recommendation**: ship the `ezdxf`-based export of the already-computed deterministic
    geometry first (cheap, accurate, zero legal risk, no new hosting) if the actual goal is "give users a
    real AutoCAD file." Only chase a generative AI floor-plan model if the goal is specifically *organic,
    AI-designed* (non-rectangular, non-slice-and-dice) room layouts - and if so, HouseDiffusion and
    House-GAN++'s *released* checkpoints are commercially unusable/legally risky as-is; the real option is
    training a fresh model on ResPlan, which is unbuilt.
  - **A friend-hosted Kaggle model - first REJECTED (2026-08-24), then WIRED IN ANYWAY at explicit user
    request (2026-08-25) as a supplementary visual, never a replacement for the working DXF export.**
    The full generation script was reviewed directly: it is **Stable Diffusion XL + ControlNet (Canny edge
    conditioning)**, i.e. still an image-diffusion model, the exact category already diagnosed as the root
    cause of the user's original "random lines and shapes, not AutoCAD format" complaint. ControlNet
    doesn't change that verdict - it only conditions the diffusion process on an edge-map "hint", it
    doesn't make the model output coordinates. The output is JPEG/PNG pixels - there is no DXF/DWG/vector
    data anywhere in it, confirmed by reading the actual notebook code twice (once before an earlier,
    fixable submit-then-poll timeout bug, once after). A real live generation, run end-to-end through this
    app's own `HybridProvider.generate_floor_plan()` wiring (not a guess), showed garbled/hallucinated
    room-label text (e.g. a title reading "42 1") and geometry that only loosely resembles the requested
    room program - the same failure category already diagnosed, now confirmed live, not just predicted.
    **User's explicit decision after being shown this evidence and asked directly (AskUserQuestion)**: keep
    both systems - the deterministic `blueprint_svg.py`/`blueprint_dxf.py` pair stays the sole source of
    the real `.dxf` file (nothing about it changed), and this vendor's output shows ALONGSIDE it as a
    separate, clearly-labeled "Concept Layout" visual reference card, not a substitute. Explicitly
    rejected: removing the deterministic system in favor of this vendor alone (would leave the site with
    **zero** real AutoCAD export capability, since this model cannot produce one at any input-tuning level -
    that's a property of being a diffusion model, not a prompting problem).
  - **Real, CONFIRMED contract** (`app/providers/kaggle_autocad.py`, read from the friend's own FastAPI
    notebook code, not assumed): `POST {url}/generate_floorplans` with
    `{length, width, unit, floors, bedrooms, bathrooms, notes}` → `{"status":"started","job_id"}`, then
    `GET {url}/generate_floorplans/status/{job_id}` polled until `{"status":"done","floors":[{"floor_number",
    "floor_title","prompt_used","image_base64" (JPEG)}, ...]}` or `{"status":"failed","detail"}`.
    **Submit-then-poll is required, not a style choice** - the model's own first version blocked inside a
    single request and reliably hit Cloudflare's free-tunnel ~100-120s timeout (524), confirmed via a real
    failed call, before the friend split it into a background-thread + job-status-polling design (the exact
    same fix `app/providers/kaggle.py`'s `generate_images_batch()` already uses for the identical reason).
  - **One image per floor - a real gap fixed, not assumed to be fine.** The model generates a SEPARATE
    SDXL pass per floor natively (`for f in range(1, floors+1)` in the friend's own code) and returns all of
    them in one `floors` list. An earlier version of `kaggle_autocad.py` only kept `floors_out[0]`,
    silently discarding floor 2+ for any multi-floor request - caught and fixed the same day, once the user
    asked directly "if I want 3 floors, does it make 3 separate images?" `Provider.generate_floor_plan()`'s
    return type changed from `bytes | None` to `list[bytes] | None` (floor-ordered, same convention as
    `blueprint_keys_json`) to carry all of them - `idealhouse.py`/`gemini.py`'s inert stubs are unaffected
    (still just return `None`, valid under either type). `HouseProject.floor_plan_keys_json` (new column,
    additive migration) stores the real list; the legacy single `floor_plan_key` column is kept populated
    with the first floor's key only, for backward compatibility with old consumers of `images.floor_plan`.
    `HouseProjectStatusResponse.floor_plan_urls` is the new list field frontend code should read.
  - **Real-geometry ControlNet conditioning + label compositing - DONE and LIVE-VERIFIED (2026-08-27)**,
    both the repo side AND the friend's notebook (pasted the generated code, redeployed, `.env`'s
    `KAGGLE_AUTOCAD_API_URL` updated to the new tunnel) - see
    `future-plans/concept-layout-controlnet-conditioning.md` for the full plan and the real verification
    run's evidence (the AI's traced geometry matched our sent conditioning image's 6-room layout almost
    exactly, `used_real_geometry: true`, no garbled text, and every one of our composited labels landed
    inside its correct room - see `scripts/output/autocad_with_labels_floor1.png`).
    Diagnosis: the model's real flaw wasn't its aesthetic, it was that the friend's own
    `create_plot_boundary()` only ever drew the plot's OUTER rectangle as the ControlNet conditioning
    image (no interior walls), so ControlNet only constrained the outer edge and the model hallucinated
    every room at random - it never saw the user's actual room program at all. Fix, on THIS repo's side
    only so far: **`app/pipeline/conditioning_image.py`** (new, pure, visually verified) renders the
    exact same rectangles `layout_floor()`/`blueprint_svg.py` already compute as a clean white-lines-on-
    black edge map; `app/providers/kaggle_autocad.py`'s `generate_floor_plan()` gained an optional
    `room_layout` param and, when given, sends one such conditioning image per floor as
    `conditioning_images` (base64 PNGs) in the request - additive to the contract, so an un-updated
    notebook keeps working via its own `create_plot_boundary()` fallback. It ALSO now composites our
    own accurate room-name labels onto each returned image at room centers
    (`_composite_room_labels()`), since the notebook's negative prompt is being updated to make the AI
    output deliberately text-free - alignment is guaranteed because both the conditioning image and the
    label positions are computed from the identical rects via the identical placement math
    (`conditioning_image.py`'s `plot_to_canvas_box()`). Required reordering
    `app/pipeline/generate_house.py`'s pipeline (`HOUSE_PROMPT_VERSION` bumped to `v7`): the room-
    layout/blueprint stage now runs BEFORE the AI floor-plan stage (was after), since the layout must
    exist before it can be sent as conditioning - the two cancellation checkpoints moved with their
    stages. `Provider.generate_floor_plan()`'s signature gained `room_layout: dict | None = None` across
    all implementations (`idealhouse.py`/`GeminiProvider` ignore it - no real conditioning to build).
    **The notebook-side half (accepting `conditioning_images`, raising `controlnet_conditioning_scale`
    to 0.95 when using real geometry, prompt simplification) is DONE too** - pasted into the friend's
    notebook and redeployed; a live end-to-end test against the real tunnel confirmed both halves work
    together correctly (see the plan file's Status section for the verification evidence).
  - **Text-hallucination mitigation identified, sent to the friend, not yet applied on their side**: their
    notebook's `negative_prompt` never excludes text/labels at all - the model is spontaneously adding
    labels because "architectural blueprint" implies them in its training data, then rendering them as
    garbage (a well-documented, language-independent diffusion weakness, not a prompt-wording bug - this
    file already reached the identical conclusion once for a different, since-reverted experiment - see
    "v4" above). Standard real-world mitigation: add `"text, labels, room names, numbers, writing,
    watermark"` to the negative prompt so the model doesn't attempt text at all - no text beats garbled
    text. This is a change to the friend's own notebook, outside this repo - not applied here.
  - **Dev-only render-skip toggle added alongside this** (`settings.house_render_enabled`, `app/config.py`,
    default `true`) - when set `false` in `.env`, `run_house_pipeline()` skips `generate_house_render()`
    entirely (zero Kaggle/OpenAI calls) so the blueprint/DXF/AutoCAD-concept work can be checked end-to-end
    on the real site without needing the separate elevation-render Kaggle session running, and without any
    risk of an accidental OpenAI charge. Not meant as a production setting - flip back to `true` once the
    elevation model's session is live again.
- **"Concept Layout" labeling requirement**: `renderHouseResults()` (`static/app.js`) labels every card
  sourced from `floor_plan_urls` (the real vendor slot above, now live via `kaggle_autocad.py`) as
  **"Concept Layout — not a precise blueprint"** (or "Concept Layout - Floor N" when there's more than
  one), never anything implying dimensional accuracy - no current image-gen floor-plan API guarantees
  exact measurements. Gated on `floor_plan_status === "done"` and a non-empty `floor_plan_urls` list; if
  neither holds, no floor-plan card renders at all (not a broken placeholder) - this is the expected state
  whenever the vendor's Kaggle session isn't running (`floor_plan_status` degrades to `"not_configured"`,
  matching this feature's original best-effort contract from before a real vendor existed). This is
  deliberately DIFFERENT from the free algorithmic blueprint cards (gated on `blueprint_status === "done"`
  and `blueprint_urls`), labeled "Floor N Layout" with a non-disclaiming description - those genuinely are
  dimensionally accurate (computed directly from the stated plot dimensions, not guessed by an image
  model), so they earn a different label. Don't merge these two labeling paths even though they look
  superficially similar - a real, live user confusion this file now records: after seeing both card types
  in the same results grid, the user initially mistook the deterministic "Floor N Layout" cards for the AI
  vendor's output and asked why they looked nothing alike - they are, by design, two unrelated systems.
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
- **v3: two renders, a dedicated house render quality, a real CAD-look blueprint, and floor-count/
  common-sense room placement** (2026-08-12, driven by direct user feedback that the results "aren't good
  enough"). Four changes, all still behind the same best-effort/non-best-effort contracts above:
  - **Generate BOTH renders, not one** — **SUPERSEDED by v4 below.** This originally added a second,
    blueprint-sourced 3D isometric render (`render_layout_key`) alongside the photoreal exterior render.
    v4 removed that second render entirely (input, processing, storage column, schema field, frontend
    card - all of it), replacing it with a per-floor 2D CAD plan instead. Left here only so the history
    of what changed and why is traceable; the exterior render itself (`render_key`, edited from the real
    plot photo) is unchanged and still the primary, non-best-effort deliverable.
  - **Dedicated house render quality** - `OPENAI_HOUSE_IMAGE_QUALITY` (default `high`, `app/config.py`),
    separate from the room tiers' `OPENAI_IMAGE_QUALITY` (`low`) for the same reason
    `OPENAI_HOUSE_INPUT_FIDELITY` is separate from `OPENAI_IMAGE_INPUT_FIDELITY` - this feature only
    produces 1-2 renders/generation (vs. 3 tiers), so `high` is affordable and doesn't touch
    room-redesign's cost. `OpenAIImageProvider._edit_image()` gained an optional `quality` param
    (defaulting to the room setting when omitted) so `generate_house_render()` can pass its own.
  - **`HOUSE_PROMPT_VERSION` bumped to `v3`** (`app/pipeline/house_prompts.py`): richer photoreal/3D
    vocabulary for both branches; a **hard floor-count constraint** ("the building must have exactly N
    stories") stated whenever there's real basis for one (a computed `room_layout`, or the user's prompt
    explicitly naming a count - deliberately NOT a guessed default, since asserting a wrong guess is worse
    than asserting nothing); and a new `HOUSE_NEGATIVE_PROMPT` constant - a "Do not include" sentence
    grounded in researched, real AI-architecture-render failure modes (warped/misaligned windows, broken
    roofline geometry, floating/impossible structure, wrong story count, duplicated buildings/openings,
    smeared materials, inconsistent context, stray text/watermarks), always appended to both branches -
    same "state exclusions in plain positive-prompt language" reasoning as room-redesign's
    `NEGATIVE_ADDITIONS`.
  - **Floor-count-aware, common-sense room layout** (`app/providers/gemini.py`): `ROOM_LAYOUT_PROMPT_TEMPLATE`
    now explicitly instructs Gemini to use the user's exact stated floor count (never inventing extras) and
    to place rooms by real-world convention - living/kitchen/dining/garage/utility only on the ground
    floor, bedrooms/bathrooms/study on upper floors, never a garage or kitchen above ground, at least one
    bathroom on any floor with bedrooms. Since prompt instructions aren't a hard guarantee (the same lesson
    documented throughout this file for room-redesign's structure preservation), `generate_room_layout()`
    also deterministically **enforces** an explicitly-stated floor count via `_enforce_floor_count()` -
    truncates extra floors or pads missing ones using the same ground/upper room convention - as a
    prompt-fix-plus-deterministic-backstop pair, not prompt wording alone. `fallback_room_layout()` (the
    never-empty path for when the Gemini call itself fails) is now floor-aware too, using the same
    ground/upper/single-storey room sets (`_GROUND_FLOOR_ROOMS`/`_UPPER_FLOOR_ROOMS`/`_SINGLE_STOREY_ROOMS`)
    instead of repeating one generic 5-room list on every floor.
  - **`app/pipeline/blueprint_svg.py` rewritten into a real CAD-style drawing**, not just a flat two-color
    treemap. Still the exact same pure `render_floor_blueprint(floor_number, rects, dimensions) -> bytes`
    signature (no caller change), still Pillow-only (see the module docstring for why, unchanged from v1).
    Adds: double-line exterior walls (the plot boundary plus a parallel outward-offset line) and double-line
    interior partitions (each room's own nested-rectangle boundary); door openings with a swing-arc symbol,
    heuristically placed at each pair of adjacent rooms' shared-wall midpoint (`_shared_edge()`/
    `_draw_door()` - a simple adjacency detector off the rectangles' shared boundaries, not a construction-
    grade layout, matching the user's own "okayish accuracy... good representation" bar); window marks on
    every room edge that lies on the exterior plot boundary; real dimension lines along the top and left
    edges (per-segment measurements plus one overall length/width dimension, derived directly from the
    rectangles' actual boundaries - always honest even though door/window placement is heuristic); a scale
    bar and north arrow; and a title block. Uses `ImageFont.load_default(size=N)` (Pillow ≥10.4, already the
    floor) for scalable text - no bundled font, no new dependency. **Real bug hit and fixed while building
    this**: PIL's default bitmap font renders unsupported glyphs (em dash, the ≈ sign, superscript ²) as
    visible tofu boxes - confirmed by actually rendering and inspecting the PNG, not assumed - so every
    label uses plain ASCII (`-`, `~`, `sq {unit}`) instead. Also real: the background grid is drawn across
    the **whole canvas**, not just the plot box - room rectangles always tile the plot completely
    (`layout_floor()` never leaves gaps), so a plot-only grid would be entirely invisible; extending it into
    the margins gives a visible "drafting sheet" backdrop instead of dead code.
  - **Deferred, not part of this pass**: visual theme/style parity between the house tab and Room Redesign
    (image hero, status chip, concept-preview shimmer tiles, results hierarchy). The house tab already
    shares Room Redesign's exact tokens and component CSS, so this is pure polish-porting - parked at the
    user's explicit request to get the core render/blueprint quality right first. See
    `future-plans/build-a-house-theme-parity.md`.
- **v4: per-floor AI-drawn "CAD plan," replacing the 3D isometric render — TRIED, then REVERTED in v5
  below.** (2026-08-13). Kept here as a real record of what was tried and why it didn't work, not
  something to resurrect casually.
  - **Premise correction that started this, worth keeping on record**: the user compared this project's
    deterministic `blueprint_svg.py` output against another AI system's floor plan and initially believed
    our blueprint images were already AI-generated (they're 100% Pillow-drawn geometry, zero model calls -
    confirmed by matching the title text `"FLOOR {n} - COMPUTED LAYOUT"` back to `blueprint_svg.py`'s own
    literal string). Once corrected, the agreed v4 approach was: keep geometry deterministic, but ask an
    image model to REDRAW the accurate blueprint for presentation polish (furniture, door/window
    craftsmanship, a staircase symbol) - one `gpt-image-1` edit call per floor
    (`app/pipeline/cad_prompts.py`'s `build_cad_plan_prompt()`, a new `generate_cad_plan()` provider
    method, `cad_plan_keys_json`/`cad_plan_urls` storage/schema, a "Verified Layout" (blueprint) +
    "Architectural Plan" (AI) card pair in the UI).
  - **Why it was reverted**: a real generation showed the image model hallucinating malformed dimension/
    area text when asked to render technical content - e.g. `"18.4 x 522.59 ft"`, `"21.6 x 1 f0.0 ft"` -
    confirming a well-documented image-model weakness (precise text rendering), not a prompt-wording
    problem. The user's explicit instruction at that point: don't just make the prompt longer, separate
    architectural planning from image rendering, and treat any image the model produces with invented
    room dimensions/areas as a hard failure regardless of how good the geometry looks. Compositing
    accurate deterministic text onto the AI image afterward was considered and rejected too - there's no
    guarantee an image-EDIT call preserves exact pixel alignment with its input, so overlaying
    known-correct dimension lines/labels risked looking *more* obviously broken (misaligned) than the
    hallucinated-text problem it was meant to fix, not less.
  - **What was removed, cleanly** (this work was never committed to git, so no dangling history):
    `app/pipeline/cad_prompts.py` deleted entirely; `Provider.generate_cad_plan()` and its
    implementations in `OpenAIImageProvider`/`GeminiProvider`/`HybridProvider` removed;
    `OPENAI_CAD_INPUT_FIDELITY`/`OPENAI_CAD_IMAGE_QUALITY` settings removed; `HouseProject.
    cad_plan_keys_json`, `HouseProjectStatusResponse.cad_plan_urls`, and the CAD-plan frontend cards all
    removed; `meta_json`'s `cad_plans_generated` reverted back to nothing (the per-floor blueprint step's
    own `blueprint_generated` already covers it).
  - **The 3D isometric render removal from v4 stands, independent of this revert**: `render_layout_key`
    (which WAS briefly committed to git, so its DB column is an intentionally-orphaned artifact on any
    install that ran that commit - additive migrations can't drop it, but nothing reads it),
    `images.render_from_layout`, and the `using_blueprint_image` branch/param in `build_house_prompt()`
    stay gone - that decision didn't depend on the CAD-plan experiment's outcome.
- **v5: furniture and a staircase added directly to the deterministic renderer instead - no image model
  touches the floor plan at all.** (2026-08-13, same day, immediate follow-up to the v4 revert). This is
  the "even better implementation" the user's own architectural spec called out as the highest-accuracy
  option: skip the image model for technical/geometric content entirely rather than trying to constrain
  it via prompting or validation-and-retry.
  - **`app/pipeline/blueprint_svg.py`'s `render_floor_blueprint()` gained a `total_floors: int = 1`
    parameter** and now draws, deterministically: furniture symbols dispatched by a keyword match on each
    room's name (bed/nightstands/wardrobe for bedrooms, sofa/coffee-table/chair for living/lounge/family/
    drawing rooms, table+4 chairs for dining, counter/sink/fridge for kitchens, toilet/basin/tub for
    bathrooms, desk/chair for study/office, a car outline for garages; entry/foyer/hallway/storage/utility/
    unrecognized names get no furniture - restraint over a guessed icon); and a staircase symbol (steps +
    an UP or DN arrow, `"UP"` when `floor_number < total_floors`, `"DN"` on the top floor) whenever
    `total_floors > 1`, placed in the floor's largest room. Same "legible mark, not construction-grade"
    precedent already established for door/window placement - not real circulation space, since none is
    reserved by `floor_layout.py`'s algorithm.
  - **Real bug hit and fixed while building this, worth the specific note**: an early version sized the
    bed as a fraction of the room's own dimensions with a "stay below 58% height" margin that looked safe
    on paper, but a real rendered small-plot test showed the bed's pillow line crossing directly through
    the room label text. Root cause: the label's rendered height is a roughly FIXED pixel amount (driven
    by font size), not proportional to room size, so a percentage-based margin doesn't scale correctly -
    a tall room's label doesn't get taller. Fixed by computing the label's actual clearance band in
    pixels (`_LABEL_CLEARANCE_PX = 26`) and clamping the bed - and skipping the wardrobe entirely when it
    would still reach into that band - against it directly, confirmed by re-rendering the exact
    reproducing case and visually inspecting the output before considering it fixed.
  - **`app/pipeline/generate_house.py`** passes `total_floors=len(room_layout["floors"])` into
    `render_floor_blueprint()` for each floor. `HOUSE_PROMPT_VERSION` is `v5`.
  - **Verification**: rendered and visually inspected (not just unit-tested) single-floor, two-floor,
    different room counts (including the exact "2 floors, 3 bedrooms each, kitchen each floor" case that
    originally prompted this whole investigation), a small/constrained plot, and every recognized room
    type - confirmed no furniture/label overlaps, correct UP/DN staircase direction per floor, and no
    crashes. `tests/test_blueprint_svg.py` covers the `total_floors` param, the bed/label
    non-overlap regression, and a smoke test across every recognized (and one unrecognized) room-name
    keyword.
  - **`blueprint_svg.py`'s own v6 (2026-08-25): solid poché (filled-black) walls, replacing the
    double-thin-line model.** Real trigger: a live side-by-side comparison against a friend-hosted AI
    model's output (see "Real DXF/AutoCAD-format export" below) showed the AI version reading as more
    "professionally drafted" despite being dimensionally inaccurate and text-garbled - solid poché was
    identified as the single biggest visual signature this renderer was missing (every real
    architectural drawing fills walls solid; this one drew two thin outlines). Technique: `_draw_wall_base()`
    fills the whole plot footprint (extended outward by `WALL_EXTERIOR_EXTRA_PX`) solid black FIRST, then
    `_carve_room()` cuts each room's interior back out of that base, inset by `WALL_HALF_THICKNESS_PX` -
    whatever black remains between two carved-out interiors automatically reads as a correct, aligned
    partition wall, and the band left around the whole plot automatically reads as the exterior wall, with
    no separate line-drawing/alignment logic needed for either (this is what guarantees every wall
    junction lines up exactly, unlike hand-drawing each wall as its own stroke - a real risk the old
    double-line approach carried). A side effect of the technique, not deliberately engineered but not
    wrong either: interior partition walls come out 2x as thick as the exterior wall (two rooms each
    contribute one inset vs. only one room + the outward extension) - which happens to match a real
    light-frame-residential convention. `_draw_door()`/`_draw_windows()` now cut their openings straight
    through the solid poché band (same hinge+leaf+arc door symbol and glazing-line window convention as
    before) instead of erasing a double-line gap. Furniture's minimum interior padding was bumped to
    `INTERIOR_CLEARANCE_PX` (was a bare `4.0`) so symbols can never visually cross into the now-thicker
    wall band. Verified both via the existing test suite (unchanged pass/fail contract - no test asserts
    exact pixel colors, only valid-PNG/differs-when-expected) and by rendering and visually inspecting
    real output: a full 2-floor 40x60ft house (6 rooms/floor) and a stress-test tight 24x20ft/5-room plot -
    confirmed clean wall junctions, correctly-cut door/window openings, and no furniture/label overlaps at
    either scale. `blueprint_dxf.py` (the real `.dxf` export) was deliberately NOT changed - CAD users want
    editable line geometry, not filled poché, so the DXF stays line-based; this upgrade is scoped to the
    PNG display renderer only.
  - **`blueprint_svg.py`'s v7 (2026-08-25, same day): richer furniture detail + title-block-style
    labels** - the second step of the same "make the floor plan look genuinely professional" pass, after
    poché walls. Furniture: beds now draw two distinct rounded pillows + a folded-blanket line instead of
    a single pillow stroke; the living-room sofa gets cushion-divider ticks and the armchair is rounded
    (reads as a chair, not a second sofa corner); the kitchen counter gets four stove burners and the
    fridge gets a door-split line; bathrooms gain a separate stand-up shower stall (square + diagonal
    drain-pan mark) in the opposite corner from the tub, but ONLY when `has_room_for_shower` confirms real
    space alongside the tub/toilet/basin already placed - a cramped half-bath still gets just those three,
    not a fourth fixture squeezed in at a size that would look wrong. Two new recognized room types added
    to the keyword dispatcher: **laundry/utility** (washer + dryer, each a square with a round drum) and
    **closet/wardrobe/dressing** (a hanging rod with tick marks for hangers) - `utility` previously got no
    furniture at all (grouped with purely circulatory rooms like hallway/storage); it now does. Labels:
    room names render in all-caps in the rendered label only (the underlying room name/data is untouched)
    plus a thin divider rule between the name and its area line - the one small typographic touch that
    turns "two lines of text" into something that reads as a real title-block entry. Verified via the
    extended test suite (`tests/test_blueprint_svg.py` - the room-type smoke test now covers laundry/
    closet too, plus a new shower-stall conditional-rendering regression test) and by rendering and
    visually inspecting real output at both a full 40x60ft 2-floor house and a tight 26x22ft 6-room stress
    test - confirmed no overlaps, correct burner/pillow/divider placement, and correct conditional shower
    behavior (present in a spacious master bath, absent in a small half-bath).
- **v6: structured dropdown inputs replace the single free-text requirements field (2026-08-20).**
  Previously the ONLY way to steer floor count/room mix was one free-text `#house-prompt` input, with
  floor count derived by regex-guessing "N floor(s)" out of whatever the user typed
  (`_explicit_floor_count`/`_guess_floor_count` in `gemini.py`). The "Plot Parameters" panel
  (`static/index.html`) now has **Floors** (integer `<select>`, 1-5), **Bedrooms**, **Bathrooms**
  (`<select>`s), and a small free-text **"Anything else?" extras** input (`#house-extras`, still capped at
  `USER_PROMPT_MAX_CHARS`). **A Garage + Kitchen-on-every-floor checkbox pair was tried and dropped
  same-day** - felt like an arbitrarily incomplete amenities list sitting next to the real dropdowns (only
  2 items, no obvious reason those two and not others) rather than expanding it further, per explicit user
  feedback. Things like a garage now just go in `extras` as free text, same as any other requirement -
  there is no dedicated field for them.
  - **`app/main.py`'s `create_house_project`** gained `floor_count`/`bedrooms`/`bathrooms`/`extras` Form
    fields (clamped: floors 1-10 matching `_explicit_floor_count`'s own clamp, bed/bath counts 0-20).
    `_compose_house_requirements()` turns these into one natural-language sentence (e.g. `"2 floors,
    3 bedrooms, 2 bathrooms. Extras: garage, dirty kitchen each floor"`), **deliberately still using the
    literal word "floor(s)" next to the number** so `house_prompts.py`'s regex-based
    `_resolve_floor_count()` fallback stays correct even when `room_layout` is unavailable (e.g. the
    blueprint step failed) - belt-and-suspenders with the real int passed separately, not a redundant
    duplicate. This composed string is stored as `HouseProject.prompt` (the SAME column every downstream
    consumer already reads - `analyze_plot`, `generate_room_layout`, `build_house_prompt`, `meta_json` - so
    nothing downstream needed to change shape) - **not a new column**, keeping this additive rather than a
    parallel-plumbing rewrite. The raw structured selections are ALSO persisted separately as
    `HouseProject.house_inputs_json` (new column, additive migration in `app/db.py`) and exposed back via
    `HouseProjectStatusResponse.house_inputs`/`.dimensions`, so a resumed/pending-login flow or a future
    "edit this project" UI has the real structured values, not just the composed sentence. **Raw free-text
    `prompt` is still accepted** for direct API callers that predate this feature (backward compat) - only
    used when NO structured field was given at all.
  - **Explicit floor count skips the regex guess entirely, not just informs it.** `floor_count` (the real
    int from the dropdown) is threaded through `run_house_pipeline()` → `Provider.generate_room_layout(...,
    floor_count=...)` (new optional param, `base.py`/`hybrid.py`/`gemini.py`) straight into
    `_enforce_floor_count()`'s existing deterministic truncate/pad backstop - `_explicit_floor_count(prompt)`
    regex-parsing only runs now as the fallback for callers that don't supply an explicit value (there are
    none in the live pipeline anymore, but the parameter stays optional for API/test flexibility).
    `fallback_room_layout()` (the never-empty path for when the Gemini call itself fails) also gained the
    same `floor_count` param, same priority-over-regex-guess treatment.
  - `HOUSE_PROMPT_VERSION` bumped to `v6`.
- **v8 (2026-08-27): real feasibility hard-gate + guaranteed minimum room sizes + garage/front-yard
  parsing** - driven by an external senior-architect-style spec the user supplied for the layout engine
  (feasibility-first, min/preferred/max room dimensions, functional zoning, garage/front-yard logic,
  validation). Two decisions were confirmed with the user before building (`AskUserQuestion`): garage/
  front-yard requirements are parsed from the existing free-text `extras` field (NOT new structured
  frontend fields - a Garage/Kitchen checkbox pair was already tried and dropped once, see the `v6` entry
  above), and an infeasible request HARD GATES (blocks the blueprint/AI floor-plan stages with a plain
  explanation) rather than degrading silently. `future-plans/feasibility-checker.md` (previously deferred,
  pending exactly these two decisions) is now built - see that file for why it ended up pure-deterministic
  math rather than the hybrid math+Gemini design originally recommended there.
  - **`app/pipeline/room_specs.py`** (new) - real-world minimum room dimensions per room type (bedroom,
    bathroom, kitchen, living, dining, garage, study, laundry, closet, foyer, default), defined in meters
    and converted to the plot's own unit via `to_plot_unit()`. `classify_room_category()` is a lightweight
    keyword classifier - deliberately a SEPARATE list from `blueprint_svg.py`'s furniture-dispatch
    keywords (same real-world categories, different question: this decides room SIZE, that decides
    furniture SYMBOLS - not unified, to avoid risking a furniture-drawing regression from a sizing-only
    change). `garage_min_area_sqm(cars, unit)` scales a garage's minimum width per requested car count
    (side-by-side bays), depth fixed.
  - **`app/pipeline/floor_layout.py`'s `layout_floor()` gained a minimum-area guarantee**: previously a
    room could shrink to an unusable sliver purely because Gemini's relative-weight guess was small next
    to other rooms on the same floor, with zero real-world floor. Now each room's real minimum area
    (`room_specs.min_area_for_room()`) is reserved FIRST, and only the AREA REMAINING after every room's
    minimum is distributed proportionally to Gemini's weights - so a weight only ever controls how much
    space a room gets ABOVE its guaranteed usable minimum, never whether it gets one at all. Falls back to
    the original pure-weight behavior (defensively - expected to already be caught by the feasibility gate
    below) when the sum of every room's minimum would exceed the plot's own area. Gained an optional
    `garage_cars` param threaded through to `room_specs`. **Known limitation, stated plainly**: this only
    guarantees minimum AREA, not minimum WIDTH/DEPTH individually - a room could theoretically still come
    out as a thin sliver satisfying its area floor with a bad aspect ratio; a true 2D constraint solver
    respecting width AND depth independently is a bigger, separate effort (not built).
  - **`app/pipeline/feasibility.py`** (new) - `check_feasibility(rooms, dimensions, total_floors,
    garage_cars)` sums every room's real minimum area (from `room_specs`) + a flat 20% circulation/wall
    overhead (`CIRCULATION_OVERHEAD_FRACTION`) + a fixed staircase footprint allowance for multi-floor
    buildings (`STAIRCASE_MIN_AREA_SQM` - a feasibility-only reservation; `blueprint_svg.py` still draws
    the staircase as a symbol inside the largest room, not a dedicated reserved rectangle - giving it a
    REAL reserved footprint is a separate, larger change, not done here), compares against the available
    building footprint, and classifies `feasible`/`tight`/`not_feasible` with a plain-language explanation
    quoting the actual numbers (never generic). Pure/deterministic, no I/O.
  - **`app/pipeline/house_requirements.py`** (new) - deterministically parses garage car count and
    front-yard depth out of the SAME free-text requirements string (`_compose_house_requirements()`'s
    output, which folds in the `extras` field) via regex, same style as `kaggle_autocad.py`'s
    `_parse_int_before_word()` for bedroom/bathroom counts - NOT trusted to Gemini's own probabilistic
    inclusion of a garage room or accounting for yard space. **Known limitation, stated plainly**: this
    project has no plot-orientation input (no "which edge faces the road" field), so a front yard is
    reserved along a FIXED convention (the plot's `width`/y=0 edge, matching `floor_layout.py`'s own
    coordinate convention) - a real footprint reservation, not a verified road-facing edge. When no
    explicit depth is stated, `DEFAULT_FRONT_YARD_DEPTH_M` (3m) is used.
  - **`app/pipeline/generate_house.py` wiring (`HOUSE_PROMPT_VERSION` bumped to `v8`)**: right after
    `generate_room_layout()` succeeds, garage/front-yard are parsed from the requirements text; a front
    yard reduces `building_dimensions` (a copy of `dimensions` with `width` reduced by the yard depth)
    BEFORE any room is placed - `layout_floor()`/`render_floor_blueprint()`/`render_floor_blueprint_dxf()`
    all use this reduced footprint, not the raw plot; a requested-but-missing garage room is injected
    into the ground floor's room list (mutating `room_layout` in place, so `room_layout_json` reflects it
    too). `check_feasibility()` then runs per floor against `building_dimensions`; the WORST verdict across
    floors wins. On `not_feasible`: `blueprint_status`/`floor_plan_status` both become `"infeasible"`,
    `blueprint_keys_json`/`blueprint_dxf_keys_json` stay empty, `provider.generate_floor_plan()` is never
    called (no misleading AI visualization of geometry that doesn't fit) - but the exterior render step
    still runs normally (a photoreal visualization of the plot itself isn't misleading the same way an
    ill-fitting blueprint would be). `HouseProject.feasibility_json` (new column, additive migration)
    stores the full result; `meta_json` also carries `feasibility_verdict` for parity with its other
    derived booleans.
  - **Schema/API**: `HouseProjectStatusResponse.feasibility` (new, `Optional[dict]`), built in
    `app/main.py`'s `_house_project_to_response()`.
  - **Frontend**: `static/app.js`'s `renderHouseFeasibilityBanner()` shows a banner above the results grid
    - red/blocking styling for `not_feasible`, a softer `tertiary`-toned informational style for `tight`;
    hidden entirely for `feasible`/no feasibility data (old projects predating this feature).
  - **Tests**: `tests/test_room_specs.py`, `tests/test_feasibility.py`, `tests/test_house_requirements.py`
    (all new, pure unit tests), `tests/test_floor_layout.py` extended (min-area guarantee, garage car-count
    scaling; the old exact-3:1-ratio proportionality test was updated since minimum-area reservation now
    legitimately compresses ratios between two equally-categorized rooms), `tests/test_house_pipeline.py`
    extended (hard-gate on an infeasible program, garage injection, front-yard footprint reduction).
    412/412 passing.
  - **Explicitly NOT built in this pass** (real scope, separate future work, not silently skipped): a full
    2D room-packing solver honoring width/depth independently (not just area); real staircase footprint
    reservation + shape (straight/L/U) selection; an adjacency graph beyond the existing public/private
    zone clustering; multi-candidate layout generation + scoring. These map directly to "Phase 3" in the
    architecture report given to the user before this work started.
- **v9 (2026-08-29): the staircase becomes a REAL reserved room, not a symbol.** Previously
  `blueprint_svg.py` drew a staircase symbol INSIDE whichever room ended up largest on a multi-floor
  building - no real footprint, not counted by feasibility beyond a flat area guess. Now:
  - **`app/pipeline/room_specs.py`** gained a `"staircase"` category (1.2m x 2.7m minimum - a compact
    run + landing) and a `"stair"` keyword classifier entry.
  - **`app/pipeline/floor_layout.py`'s zoning extended from 2 tiers to 3**: public (0) → circulation (1,
    staircase only) → private (2), via `_zone_key()`. Since `_slice()` always splits a CONTIGUOUS sublist
    (unchanged, see the existing public/private docstring), sorting every floor the same way gives the
    staircase a CONSISTENT relative position across floors - explicitly documented as NOT pixel-exact
    interior alignment (this project's rectangular slice-and-dice algorithm can't reserve an identical
    interior rectangle on every floor without a fundamentally different, non-rectangular-region layout
    algorithm - a stated limitation, not an oversight). Exterior wall alignment across floors was already
    exact for an unrelated, simpler reason (every floor renders the identical plot boundary).
  - **`app/pipeline/generate_house.py`** injects a `"Staircase"` room into EVERY floor's room list (not
    just ground floor, unlike garage) whenever `total_floors > 1` and no room already classifies as one -
    same pattern as garage injection, right before the feasibility check, so it's counted like any other
    room via the real minimum-area guarantee. `feasibility.py`'s old flat `STAIRCASE_MIN_AREA_SQM`
    overhead is now CONDITIONAL - only applied when the room list doesn't already contain a real
    staircase room (avoids double-counting once the pipeline's own injection runs; still protects a
    direct/test caller that bypasses it).
  - **`app/pipeline/blueprint_svg.py`**: the old `_draw_staircase()` (searched for the largest room) is
    gone, replaced by `_furnish_staircase()` in the normal furniture dispatch (triggered by `"stair"` in
    the room name, like every other room type) - it only decides HOW to draw the run, not WHERE the room
    goes. Shape is chosen DYNAMICALLY from the room's own real post-layout aspect ratio (not guessed in
    advance): elongated → `_draw_straight_stair_run()`, squarer → `_draw_l_shaped_stair_run()` (two
    flights meeting at a landing). UP/DN direction logic (already correct) is unchanged, just threaded
    through the furniture pass instead of a separate post-hoc call. `blueprint_dxf.py` needed NO changes -
    it already draws whatever rects `layout_floor()` returns generically, no special-casing per room type.
  - **Tests**: `tests/test_blueprint_svg.py` updated (the two staircase tests now include a real
    `"Staircase"` room in their rects, since a symbol is no longer drawn without one) + a new dynamic-shape
    test; `tests/test_floor_layout.py` gained a direct `_zone_key()` ordering test (the actual guarantee -
    a concrete geometric adjacency test is a regression case for one room mix, NOT a general proof, since
    the slice-and-dice split doesn't guarantee a circulation room touches both neighboring zones for every
    possible weight combination - documented plainly rather than overclaimed); `tests/test_house_pipeline.py`
    gained per-floor injection + single-floor-skips-injection tests. 417/417 passing. Visually verified
    both the straight-run and L-shaped-run cases render correctly with real doors connecting to
    neighboring rooms (inherited for free from the existing door-drawing logic, since the staircase is now
    a real room like any other).
  - Addresses `future-plans/house-layout-spec-checklist.md` items #16 (now ✅ done) and #17 (now 🟡
    partial - see that file for exactly what's still missing).
- **v10 (2026-08-31): the AI Concept Layout call and the exterior render call now run CONCURRENTLY**,
  requested directly by the user chasing Build a House's ~5min generation time. Confirmed first that
  neither call's inputs depend on the other's output (`build_house_prompt()` only needs `dimensions`/
  `prompt`/`plot_description`/`room_layout`, all already computed by the time either call would start) -
  they were only ever sequential because the code happened to be written that way, not because of a real
  dependency. `run_house_pipeline()` now submits both `provider.generate_floor_plan(...)` and
  `provider.generate_house_render(...)` to a `ThreadPoolExecutor(max_workers=2)` before collecting either
  result, dropping wall time for this pair from `t(floor_plan) + t(render)` to roughly
  `max(t(floor_plan), t(render))`. Same "worker threads only call the provider, the main thread does every
  `session.add()`/`commit()` after `future.result()`" pattern already established by `generate.py`'s
  materials `ThreadPoolExecutor` - no worker thread here ever touches `session`/`house_project`, so there's
  no new SQLAlchemy thread-safety concern. The two calls keep their EXACT prior error-handling asymmetry:
  `generate_floor_plan` stays best-effort (an exception still only degrades to
  `floor_plan_status="not_configured"`, never fails the project), `generate_house_render` stays NOT
  best-effort (`future.result()` re-raises its exception, which still propagates to the outer
  try/except and fails the whole project, unchanged). **Accepted tradeoff**: the single cancellation
  checkpoint that used to sit strictly between these two calls ("before render started") now sits BEFORE
  both are launched instead - cancelling can no longer stop render once floor_plan has already started (or
  vice versa). The frontend needed ZERO changes - `deriveHouseStageIndex()`/`computeHouseProgressTarget()`
  (`static/app.js`) were already purely real-signal-driven and monotonic (never regress, only advance on
  new evidence), so they handle the two stages completing in whatever order/overlap naturally without any
  edits. `HOUSE_PROMPT_VERSION` bumped to `v10`. Tests:
  `test_run_house_pipeline_runs_floor_plan_and_render_concurrently` (new) is a REAL timing-based regression
  guard (both fake calls sleep 0.3s; asserts total wall time stays well under the 0.6s a sequential
  implementation would take) - not just "behavior is unchanged," a test that would actually catch a
  silent regression back to sequential. 418/418 passing. Room-redesign's own generation pipeline
  (`app/pipeline/generate.py`) was checked too - its 3 tiers already run concurrently via
  `generate_images_batch()`/a per-tier `ThreadPoolExecutor`, so there was no analogous sequential-that-
  could-be-parallel gap to fix there; see `future-plans/todo-and-pending-checks.md` for what IS still
  pending on the room-redesign speed side (waiting on a real timing report from the user).
- **v11 (2026-09-01): `generate_floor_plan()` (the Kaggle "Concept Layout" call) is fully DECOUPLED from
  the house project's `done` status, superseding v10's mere concurrency.** Real motivation: a live,
  measured call against the friend-hosted notebook took **~2 minutes per floor** (SDXL+ControlNet
  diffusion on a T4, floors generated sequentially on the notebook's own side, not parallelizable there) -
  v10's concurrency with the exterior render could only ever avoid ADDING the render's time on top of
  that; it could never shrink the ~2min/floor number itself, and with `HOUSE_RENDER_ENABLED=false` (no
  render running at all in current dev config) there was nothing left to overlap with, so v10's win was
  invisible in practice. Since `generate_floor_plan` is explicitly documented as a SUPPLEMENTARY visual
  (see "Concept Layout" labeling requirement below - never a replacement for the deterministic
  blueprint/DXF, which stay the authoritative, accurate deliverable), there's no reason the whole
  project's completion should wait on it. Now: once the blueprint/DXF/feasibility stage finishes,
  `floor_plan_status` is set to `"running"` and `generate_floor_plan()` is handed to a new
  `_run_floor_plan_stage()` helper running on a **fire-and-forget daemon thread that is NEVER joined** -
  the main pipeline immediately continues to the (still synchronous, still NOT best-effort) render call
  and then to `house_project.status = "done"`, without waiting on the Kaggle call at all. The detached
  thread opens its OWN fresh `Session(engine)` and re-fetches the row by id whenever it eventually
  finishes, committing `floor_plan_status="done"`/`"not_configured"` + the image keys - the frontend
  already polls `floor_plan_status` independently of overall project status
  (`renderHouseResults()`/`deriveHouseStageIndex()` in `static/app.js`), so this needed **zero frontend
  changes**. `generate_house_render` keeps its exact prior contract (synchronous, not best-effort, still
  fails the whole project on error) - the `ThreadPoolExecutor` from v10 was removed entirely since only
  one call remains that the main thread needs to wait for. **Accepted tradeoff**: cancelling a house
  project can no longer stop the Concept Layout call once it has started (already true after v10 as a
  narrow race window; now permanent) - a cancelled project's detached thread may still write a
  floor-plan image after the fact, same "harmless orphan" treatment already accepted elsewhere in this
  file for S3 objects, since `list_house_projects` excludes cancelled rows from view regardless.
  `HOUSE_PROMPT_VERSION` bumped to `v11`.
  **Real test-infrastructure lesson hit while building this**: `_run_floor_plan_stage`'s detached thread
  is the first place in this codebase that opens a genuinely concurrent second `Session` against a test's
  in-memory SQLite engine while the main thread is ALSO still writing to it. A bare `sqlite://` (`:memory:`)
  engine gives each thread its own separate, table-less database (the lesson already recorded once for a
  room-redesign cancellation test); forcing `StaticPool` (one shared connection) to work around THAT then
  causes a DIFFERENT failure - two threads issuing real concurrent writes over one shared sqlite3
  connection object corrupted each other's transaction state, observed live as a spurious
  `StaleDataError: 0 rows matched`. Fixed by giving `tests/test_house_pipeline.py`'s `make_test_engine()`
  a real temp **file** database instead of `:memory:` (`tempfile.gettempdir()/interior_gen_test_<uuid>.db`)
  - a real file gives each thread its own actual connection, with SQLite's normal file-level locking
  serializing concurrent writers, which is more faithful to how production actually works (a real sqlite
  file or Postgres) than either single-connection in-memory workaround. Tests that assert on
  `floor_plan_status`/`floor_plan_key` now poll (`wait_for_floor_plan_status()` in
  `test_house_pipeline.py`, `_poll_until_floor_plan_settled()` in `test_house_api.py`) instead of asserting
  immediately after `run_house_pipeline()`/the create request returns, since that field specifically may
  still read `"running"` for a brief moment - every other field (`status`, `render_key`, `blueprint_*`) is
  still guaranteed settled synchronously and needed no such change. 418/418 passing, confirmed stable
  across repeated full-suite runs (the race was real and would show up intermittently under load, not on
  every run).
- **Live-measured Kaggle Concept Layout timing (2026-09-01, `scripts/test_autocad_kaggle_labels.py`
  against a real tunnel)**: **~2 minutes per floor**, submit-then-poll, single-floor request (6 rooms). A
  multi-floor request takes proportionally longer since the notebook generates floors sequentially in its
  own `for f in range(1, floors+1)` loop - there is no floor-level parallelism available on the notebook
  side today (a single T4 GPU under one `generation_lock`). This is the number the v11 decoupling above
  responds to - see `future-plans/todo-and-pending-checks.md` for the live verification detail of the v3
  token-budget-fixed prompt (contrast/vignette fixed, poché walls and residual scribble-text still open).
- **Kaggle session-offline detection (2026-09-01)**, `app/providers/session_errors.py` (new, shared by
  every Kaggle-backed provider). Real trigger: a Build a House generation that looked "stuck forever with
  no error" turned out to be caused by the friend-hosted Kaggle notebook's session having stopped - the
  old code had no way to tell "the tunnel has nothing listening behind it" apart from any other
  unexpected exception; both just logged a traceback and silently degraded (best-effort calls) or failed
  with a raw httpx error message (the non-best-effort render call). `classify_kaggle_failure(exc,
  vendor_label)` recognizes two specific failure shapes as "the session is offline": any
  `httpx.TransportError` (ConnectError/ConnectTimeout/ReadTimeout/etc. - httpx couldn't complete the
  request at all) and an `httpx.HTTPStatusError` with a Cloudflare-tunnel-shaped status code (502, 503,
  521-526, 530 - codes Cloudflare's own edge returns when the tunnel is up but nothing answers behind it,
  never codes the notebook's own FastAPI app would return itself) - genuinely unexpected errors (a bug
  inside a running notebook, a malformed response) are deliberately NOT reclassified this way, so a real,
  different bug is never hidden behind a misleading "session is offline" message. When classified, raises
  `KaggleSessionUnavailableError` with a plain-language, actionable message ("Start/restart the Kaggle
  notebook...") instead of the raw exception.
  - **`kaggle_autocad.py`'s `generate_floor_plan()`** (Concept Layout, best-effort): now raises
    `KaggleSessionUnavailableError` for a classified failure instead of silently returning `None` (same as
    "vendor not configured") - genuinely unclassified errors still return `None`, unchanged.
    `_run_floor_plan_stage()` (`app/pipeline/generate_house.py`) catches this specific exception and sets
    a NEW, distinct `floor_plan_status="unavailable"` + `floor_plan_error=<message>` (additive column,
    migration in `db.py`), separate from `"not_configured"` (no vendor URL set at all - an expected,
    silent state with nothing to explain). Exposed via
    `HouseProjectStatusResponse.floor_plan_error`/`floor_plan_status`. `static/app.js`'s
    `renderHouseResults()` shows a visible notice ("Concept Layout unavailable - <message>", styled like
    the existing "tight fit" feasibility banner, spanning the full results-grid width) when
    `floor_plan_status === "unavailable"` - `"not_configured"` still renders nothing, unchanged (that's
    the correct, silent behavior for a feature that was never set up at all).
  - **`kaggle.py`'s `generate_image`/`generate_images_batch`/`generate_house_render`**: same
    classification applied at each `httpx` call site. For the room-redesign methods (best-effort from
    `HybridProvider`'s perspective - it already falls back to OpenAI on any exception), this changes
    nothing user-visible (the fallback still fires identically) but makes the resulting `logger.exception`
    call in `hybrid.py` say plainly "session appears offline" instead of a generic connection traceback -
    a real operator-visibility win with zero behavior change. For `generate_house_render` (NOT
    best-effort - its exception propagates to `house_project.error` and is shown verbatim via
    `showHouseError()`), this directly improves what the user sees on a real failure, not just server logs.
  - **Real test-infra lesson hit while adding this**: an existing test
    (`test_generate_floor_plan_returns_none_on_request_exception` in `tests/test_kaggle_autocad.py`)
    encoded the OLD silent-swallow behavior for exactly the failure shape (`httpx.ConnectError`) this
    feature changes - updated to
    `test_generate_floor_plan_raises_session_unavailable_on_connection_error` (asserts the new `raises`
    behavior) plus a new `test_generate_floor_plan_returns_none_on_unclassified_exception` (confirms
    genuinely unrelated errors still degrade silently, unchanged).
- **Real circulation corridor for the private zone (2026-09-02)** - real user feedback: "I don't want
  crisp, I want an intelligent one which doesn't just make boxes and lines but adds some true meaning to
  the map." Diagnosis: the Kaggle Concept Layout AI model can't add this on its own - it's forced to
  trace our own geometry almost exactly (`controlnet_conditioning_scale=0.95`), so "meaning" has to come
  from the deterministic layout engine itself, not further AI prompting/tuning. User's explicit choice
  (`AskUserQuestion`, two options offered: loosen the AI's conditioning freedom vs. improve the
  deterministic engine): **"Both, but engine first."** Before this, every private-zone room just touched
  whichever neighbor the slice-and-dice split happened to put next to it - `blueprint_svg.py`'s generic
  door logic then drew a door at every shared wall, including straight from one bedroom into the next,
  which reads as arbitrary box-slicing rather than a real design.
  - **`app/pipeline/floor_layout.py`**: the public/circulation-vs-private split (previously just an
    ordering trick inside one big recursive `_slice()` call - zones were never actually separate boxes)
    is now a REAL one-time box split. `_split_box()` (extracted from `_slice()`'s own inline "cut along
    the longer side" logic, unchanged behavior) is called once, manually, at the real zone boundary
    (`private_start`, the index in the zone-sorted room list where rooms become private) - `front_box`
    (public+circulation) still recurses through `_slice()` exactly as before; `private_box` routes into
    a new `_layout_private_zone()`. That function only builds a corridor when there are at least
    `MIN_ROOMS_FOR_CORRIDOR` (3) private rooms AND enough depth remains after reserving a real
    `HALLWAY_WIDTH_M` (1.1m/~3.6ft, a standard single-loaded corridor width) strip along whichever edge
    of the private box borders the front zone (so it's actually reachable from there, not just present
    somewhere) - otherwise it falls back unchanged to the original plain `_slice()` (this is what keeps
    the pre-existing `test_layout_floor_preserves_relative_order_within_a_zone` test, which only has 2
    private rooms, passing completely untouched). The remaining rooms are packed via a new `_pack_row()`
    - a simplified, single-axis-only version of `_slice()`'s splitting (always cuts along the row's
    length axis, never flips based on aspect ratio) that packs rooms in their EXISTING input order,
    which automatically preserves the pre-existing "master bedroom next to its own ensuite bathroom"
    adjacency with no separate suite-detection logic needed, since sequential packing keeps sequential
    neighbors adjacent to each other as well as to the corridor. A synthetic `{"name": "Hallway", ...}`
    rect is inserted into the returned list alongside the real rooms.
  - **`app/pipeline/blueprint_svg.py`**: with a real Hallway rect now present, the existing generic
    `combinations(rects, 2)` + `_shared_edge()` door logic already draws a door between the hallway and
    every room touching it, for free. A new `_should_suppress_direct_door(a, b)` stops it from ALSO
    drawing a direct door between two adjacent private-zone rooms when a hallway exists on the floor -
    UNLESS at least one of them is a bathroom (`room_specs.classify_room_category`), which keeps a
    realistic bedroom+ensuite-bathroom direct door while removing "bedroom opens straight into bedroom."
    No suppression at all when there's no hallway (small private zones keep the original "any shared
    edge gets a door" behavior, since direct adjacency is their only way in). Needed new imports
    (`_zone_key` from `floor_layout.py`, `classify_room_category` from `room_specs.py`) - blueprint_svg.py
    was previously fully self-contained.
  - **Real bug hit and fixed via visual inspection, not just unit tests** (this project's own
    documented lesson, applied again): a corridor row's rooms each span the row's FULL depth, so a
    shared side-wall between two such rooms now spans nearly the room's entire height/width - the door's
    old exact-midpoint placement collided visually with the room's own CENTERED label
    (`_draw_room_label`). Fixed by biasing the door position to 30% along the shared edge
    (`_DOOR_POSITION_FRACTION`) instead of the exact 50% midpoint - clears the small label-clearance zone
    on corridor-row edges while remaining a plausible door position on shorter, pre-existing
    (non-corridor) edges too. Confirmed by rendering a real mixed public+private room program and
    visually inspecting: a real hallway strip, doors from each private room onto it, no direct
    bedroom-to-bedroom doors, bathroom-adjacent doors kept and no longer overlapping their room labels.
  - **Zero changes needed** in `blueprint_dxf.py` (already draws whatever rects `layout_floor()` returns
    generically - the Hallway rect just becomes another labeled room outline in the `.dxf`, which is
    correct), `conditioning_image.py`/`kaggle_autocad.py`'s label compositing (same generic-iteration
    reason - the AI Concept Layout card gets the corridor "for free" as a bonus, since it traces our real
    geometry), `feasibility.py` (the existing flat 20% circulation-overhead fraction already approximates
    this, erring toward stricter feasibility now that a real corridor exists, never falsely-permissive),
    or `blueprint_svg.py`'s furniture dispatch (a room literally named "Hallway" already fell through to
    "no furniture symbol" via the pre-existing entry/foyer/hallway/storage catch-all).
  - **Explicitly NOT attempted this pass** (real, harder problems, left for later - see
    `future-plans/house-layout-spec-checklist.md`): non-rectangular/L-shaped rooms, a double-loaded
    (two-facing-rows) corridor, plumbing-zone vertical stacking across floors, a full adjacency-graph
    solver. AI conditioning-freedom tuning (the other half of the user's "both" choice) is a separate,
    not-yet-started next step.
  - **Real production robustness bug found and fixed while adding this**: the new tests (more detached
    `_run_floor_plan_stage` threads running per test session) turned an intermittent full-suite flake
    into a consistent failure - `floor_plan_status` stuck at `"running"` forever, never settling. Root
    cause traced to `app/db.py`'s SQLite engine having no `busy_timeout` set - any writer that finds the
    file locked by another connection fails IMMEDIATELY (`sqlite3.OperationalError: database is locked`)
    instead of waiting, and `_run_floor_plan_stage`'s DB-write section had no error handling around it at
    all, so that exception silently killed the detached thread mid-write with zero diagnostic trail. This
    was always a latent risk for any multi-threaded writer, not something v11 introduced, but v11's
    detached thread made real overlapping writes far more likely to actually occur (and, in tests, share
    the same real dev DB file other tests' still-running threads were also writing to - see the
    "Resilient loading" testing note above). Fixed with two changes: `connect_args={"check_same_thread":
    False, "timeout": 30}` on the SQLite engine (gives real room to wait/retry on a lock collision instead
    of failing on the first one - psycopg2 doesn't take this kwarg, so it's gated on `_is_sqlite` same as
    `check_same_thread`), and `_run_floor_plan_stage`'s Session block wrapped in its own try/except that
    logs (`logger.exception`) rather than letting a failure there vanish silently - there's no session
    left to update the row from if the write itself is what failed, so logging is the only thing that
    keeps this diagnosable. Confirmed fixed via 5 consecutive full-suite runs, all 431/431 passing
    (previously flaked ~1 in 3-4 runs under full-suite load).
  - **Real frontend gap found and fixed the same day, live-reported by the user** ("where is the picture
    of kaggle model?? nope not there as well" on a project the DB confirmed had a real, successful
    `floor_plan_status="done"` result): `pollHouseProject()` (`static/app.js`) stops polling entirely the
    instant the OVERALL project reaches `status="done"` - correct before v11 (every stage was already
    settled by then), but v11 deliberately decouples the Concept Layout call from that status, so it's
    now common for `floor_plan_status` to still be `"running"` at that exact moment. The card was
    rendered once with whatever snapshot existed then, and never revisited - the CLAUDE.md v11 entry's
    claim that "the frontend already polls floor_plan_status independently... needed zero frontend
    changes" was WRONG (only true while the project itself was still in progress). Fixed with a light,
    separate `pollFloorPlanCatchUp()` loop that starts only when results have just been shown with
    `floor_plan_status === "running"`, re-fetches every 3s, and calls the already-idempotent
    `renderHouseResults(data)` again once it settles - guarded by `lastDisplayedHouseProjectId` so a
    late catch-up poll can never clobber a NEWER generation's UI if the user starts another one first.
    Found a SECOND, related gap while investigating: `renderHistoryHouseCard()` (the History modal) never
    included Concept Layout thumbnails at all, in any state, past or present - fixed by adding
    `floor_plan_urls` thumbnails there too (gated on `floor_plan_status === "done"`), reusing the same
    `_house_project_to_response()` fields `list_house_projects()` already returns.
- **v12 (2026-09-03): the plot photo is now OPTIONAL.** Investigated at the user's request (they
  suspected the image input wasn't actually being used and floated removing it entirely). Real finding:
  the photo IS load-bearing, but only in two places, both of which already had "no photo" as a valid,
  handled case elsewhere in this codebase - `analyze_plot()` (best-effort Gemini vision → 
  `plot_description`, already degrades to `None` on failure) and `generate_house_render()` (the
  photoreal exterior render, an image-EDIT call - currently DISABLED anyway via
  `settings.house_render_enabled=False` in `.env`). The floor plan, blueprint, `.dxf` export, and the
  Kaggle "Concept Layout" card use ONLY dimensions + room program and have NEVER touched the photo -
  confirmed by reading `generate_house.py` directly, not assumed. Recommendation given to the user:
  make the photo optional for Build a House specifically (Room Redesign's photo stays mandatory - it's
  the thing gpt-image-1/Kaggle actually edit there, not decorative) so users aren't blocked by an input
  that, with the render disabled, only ever fed a best-effort text description. Approved and built:
  - `app/main.py::create_house_project`'s `file` param changed from `UploadFile = File(...)` to
    `UploadFile | None = File(None)`; validation/`storage.put` for the plot image now only run when a
    file was actually given. Found and fixed a real latent bug while doing this: `storage_namespace`
    (needed unconditionally for the metadata.json key and the background task, not just the plot-image
    key) had been computed inline right next to the plot-image `storage.put` call - moved it out so it's
    always computed regardless of whether a photo was uploaded.
  - `app/pipeline/generate_house.py::run_house_pipeline`: `plot_bytes` is `None` when
    `house_project.plot_image_key` is `None` (no upload happened) - `analyze_plot` is skipped entirely
    (not called-then-caught) when `plot_bytes is None`, and the exterior render step gained a third skip
    condition (alongside the existing `house_render_enabled` dev toggle): `plot_bytes is None` → logs and
    skips, same "project still completes as done with whatever else succeeded" treatment as the existing
    toggle-off case.
  - `static/index.html`'s house dropzone label gained a small "Optional — your floor plan works from
    dimensions alone" line. `static/app.js`: `updateHouseGenerateBtnState()` no longer requires
    `houseSelectedFile` (only both dimensions); the submit handler only appends `file` to `FormData` when
    one was actually selected, and the 401→`savePendingGeneration()` path guards every `houseSelectedFile.*`
    read since the file may now genuinely be absent.
  - Covered by `tests/test_house_api.py::test_full_house_upload_without_photo_still_completes` - asserts
    the project reaches `status="done"` with `plot_description`/`images.plot`/`images.render` all `None`,
    and uses a `FakeProvider` subclass whose `analyze_plot`/`generate_house_render` methods `raise
    AssertionError` if called at all (not just "return None" - a genuine call-count guard, not a
    behavior-only check).
  - **Deliberately deferred** (per user's explicit instruction, tracked in
    `future-plans/subscription-and-access-roadmap.md`, NOT built yet): making the photo conditionally
    REQUIRED based on subscription plan/chosen model (e.g. "upload only if using OpenAI, optional on
    Kaggle"). That's a plan-specific rule for the future subscription phase - this pass only makes the
    photo unconditionally optional for every user, on every plan, right now.
- **v13 (2026-09-03): real bedroom+bathroom SUITES + front-of-house public ordering.** Direct, detailed
  user critique of a live layout: "master washroom has to be attached with master bedroom in order for it
  to be called master washroom... all the washrooms are on a side and all the bedrooms are on the side of
  house and there is a hallway in between... garage and entrances from the main side and first it is the
  living room and then kitchen and then rooms - it doesn't make sense." Both are real root-cause bugs in
  the deterministic engine (`app/pipeline/floor_layout.py`), NOT the AI model (the Kaggle Concept Layout
  model just traces our geometry at `controlnet_conditioning_scale=0.95`, so "meaning" has to come from
  the engine). Fixed two ways, visually verified by rendering the exact reported room program before/after
  (this project's repeated lesson - not unit tests alone):
  - **Bedroom+bathroom suites** (`_arrange_suites()` in `floor_layout.py`): the corridor previously packed
    private-zone rooms in Gemini's RAW list order, so all-bedrooms-then-all-bathrooms input clustered them
    on opposite ends - a "master bathroom" ended up nowhere near the master bedroom. Now the private list
    is reordered into ensuite pairs ([MasterBed, MasterBath, Bed2, Bath2, ...] - master paired first,
    preferring a bath whose own name says master/ensuite; remaining bedrooms pair with remaining baths in
    order; leftover common baths + non-bed/bath rooms go standalone at the end) and each pair is tagged
    with a shared `suite` id carried through `_pack_row()` into the rects. Purely a reorder+tag step - same
    room set, same weight sum, so exact plot tiling is unaffected.
  - **Suite-aware doors** (`_should_suppress_direct_door()` in `blueprint_svg.py`, also used by
    `blueprint_dxf.py`): a bathroom now keeps a direct door ONLY to a room carrying the SAME suite id
    (its own bedroom), never into a neighbor it merely got packed beside; two adjacent bathrooms never
    interconnect; and a tagged ensuite bathroom is additionally kept from opening onto the hallway (it's
    private to its bedroom), while a standalone/common bathroom (no suite tag) still opens onto the
    hallway. This REPLACED the old rule ("keep the door whenever either adjacent room is a bathroom"),
    which drew a wrong door from a bathroom into whatever bedroom sat beside it once suites interleave
    bedrooms and bathrooms along the row. The pre-existing bedroom<->bedroom suppression (via the hallway)
    is unchanged.
  - **Front-of-house public ordering** (`_public_order_key()`/`_sort_key()` in `floor_layout.py`): the
    public zone is one contiguous sliced region, but its rooms were placed in Gemini's arbitrary order
    (garage bottom-center, entry top-left in the reported case). Now a stable secondary sort orders public
    rooms garage/entry -> living -> dining -> study -> kitchen, so the entrance/garage cluster lands
    together at the "main side" and the kitchen sits next to the private hallway as the public->private
    transition. `_zone_key()` (the primary public/circulation/private split) is unchanged; this only
    orders WITHIN the public zone.
  - **Verification**: rendered the reported program (garage/entry/living/dining/kitchen + 3 bedrooms + 3
    bathrooms) plus three edge cases (more bedrooms than bathrooms -> unpaired bedrooms open to the
    hallway; a shared/common bathroom -> opens to the hallway while the two ensuites open only to their
    bedrooms; a 2-storey upper floor with a staircase) and visually confirmed each. Tests:
    `tests/test_floor_layout.py` gained `test_layout_floor_pairs_master_bathroom_with_master_bedroom` and
    `test_layout_floor_interleaves_bathrooms_with_bedrooms_not_clustered`; `tests/test_blueprint_svg.py`'s
    door-suppression tests were updated for the suite model (ensuite now requires a matching `suite` tag)
    plus new cases for bathroom-into-unrelated-bedroom, two-adjacent-bathrooms, and untagged-bathroom
    suppression. 442/442 passing.
  - **Still NOT done** (real, harder problems - the user's "public at front / private at back" front-to-
    back orientation is only partially honored: the public/private split still follows the plot's longer
    axis, so on a wide plot public/private read as left/right rather than front/back; also no L-shaped/
    non-rectangular rooms, no double-loaded corridor, no full adjacency-graph solver). **Fixed the very
    next day - see v14 immediately below**, which addresses this exact "still not done" note.
- **v14 (2026-09-04): true front-to-back zoning, real room min/max proportions, kitchen-dining
  adjacency, and garage buffered by a real Entry room.** A full, detailed 10-priority architectural
  critique of a live layout (circulation → zoning → proportions → living/dining/kitchen → garage →
  bedrooms → bathrooms → storage → doors/windows → furniture), matching this project's own
  `future-plans/house-layout-spec-checklist.md` gaps #10/#11/#15/#18 almost exactly: garage sat in a
  disruptive central position instead of the front, dining was oversized relative to its function, and
  public rooms (garage/entry/living/kitchen) scattered instead of flowing front-to-back from the
  entrance. Scoped to a Phase 1 covering the highest-leverage items buildable within the existing pure
  rectangle slice-and-dice model (see `future-plans/house-layout-spec-checklist.md` for what's still
  explicitly deferred - non-rectangular rooms, a real validate-regenerate loop, true road-facing
  orientation with no plot-orientation input).
  - **True front-to-back zoning** (`floor_layout.py`'s `_split_box_along_y()`): the ONE top-level
    public-vs-private split now ALWAYS cuts along the plot's y-axis (public in the low-y "front" band,
    private in the high-y "back" band), replacing the exact v13 limitation noted above where it cut
    along whichever side of the box was longer. This project already treats y=0 as the road-facing
    front everywhere else (`house_requirements.py`'s front-yard convention reserves depth along that
    same edge) - this makes the layout engine consistent with its own established convention on every
    plot, regardless of aspect ratio. Every nested split (`_slice()` within each zone, the hallway
    placement in `_layout_private_zone()`) is unaffected - `_split_box_along_y()` returns the exact same
    `(front_box, back_box, split_along_width=False)` shape `_split_box()` already did for its own
    h<w branch, so `_layout_private_zone()` needed zero changes.
  - **Real room min/max proportions, not min/unbounded** (`room_specs.ROOM_MAX_MULTIPLIER`,
    `max_area_for_room()`, `floor_layout._clamp_to_max_and_redistribute()`): previously a room's area
    above its guaranteed minimum was driven by Gemini's relative weight ALONE, unbounded on the high
    end - exactly why a heavily-weighted dining room could balloon. Each category now has a real max
    multiplier on its own minimum (bedroom 1.8×, bathroom 1.5×, kitchen 2.2×, dining 1.8×, living 4.0×
    since it's meant to be the dominant social space, garage/staircase 1.0× - pinned to exactly their
    functional minimum, never grown by leftover weight; unclassified/`"default"` rooms get
    `math.inf` - no real-world size exists to bound an unknown room type against). A single clamp-then-
    redistribute pass gives any excess taken from a capped room to other rooms that can still absorb it.
    **A real bug caught via visual inspection, not just unit tests**: the first version redistributed
    excess to ANY non-capped room including PINNED ones' own leftover pool, which (in a specific
    test's room mix - a plot much bigger than a 3-room list's combined caps needed) caused ALL rooms
    including a real "Staircase" room to hit their caps simultaneously, triggering a fallback where
    `_slice()`'s ratio-only proportional splitting rescaled everything to fill the box - and because
    Staircase's cap was tiny relative to Living Room's, it got squeezed into an unusable 40×0.87ft
    sliver, falling below `_FURNITURE_MIN_BOX_H` and silently drawing no staircase symbol in EITHER
    the single- or multi-floor render (confirmed via a real before/after pixel diff, not assumed).
    Fixed with a three-tier redistribution (`pinned` param, parallel to weights): tier 1 = non-pinned,
    non-capped rooms; tier 2 = non-pinned rooms even past their own cap (better to let a flexible room
    type modestly exceed its cap than distort a pinned one); tier 3 = every room, only reachable when a
    zone is ENTIRELY pinned rooms. Garage/staircase now never absorb redistributed excess under normal
    circumstances, confirmed by `test_layout_floor_pinned_garage_never_absorbs_redistributed_excess`
    (an 80×80ft plot for just a Garage+Bedroom stays pinned near its true ~157 sqft minimum, not
    inflated by the huge plot).
  - **Kitchen-dining adjacency** (`_PUBLIC_ORDER_RANKS` in `floor_layout.py`): dining moved to
    immediately precede kitchen (previously separated by living/study - not even list-adjacent, so the
    "list order → likely spatial adjacency" mechanism this whole scheme relies on never applied to
    them). Kitchen still ranks last (closest to the hallway transition, unchanged intent).
  - **Garage buffered from living space via a real Entry room**
    (`generate_house.py`'s garage-injection block gained an Entry-injection sibling;
    `blueprint_svg._should_suppress_garage_direct_door()`): when a garage was requested but no
    foyer/entry/lobby room exists, a real "Entry" room is now guaranteed to exist (same "real data,
    don't leave it to chance" pattern as the pre-existing garage/staircase injection). A direct door
    between Garage and Living/Kitchen/Dining is suppressed whenever an Entry room exists on the floor -
    garage traffic routes through Entry instead (the generic door loop already draws Garage↔Entry and
    Entry↔Living/Kitchen/Dining doors for free, same mechanism the Hallway rect already relies on for
    the private zone). `HOUSE_PROMPT_VERSION` bumped to `v12`.
  - **Verification**: rendered the exact reported room program (garage/entry/living/dining/kitchen + 3
    bedroom+bathroom suites) plus 3 edge cases (a 90×30ft wide/shallow plot stress-testing the forced
    y-axis split, a no-garage floor confirming Entry injection/suppression correctly no-ops, and a
    24×22ft small plot near the feasibility boundary confirming max-area capping doesn't fight the
    min-area guarantee) and visually inspected all four - confirmed garage-to-living door correctly
    absent (solid wall, no arc) while Garage↔Entry↔Living still connects, kitchen/dining adjacent with a
    door between them, and front-back zoning holding on every plot aspect ratio. 454/454 tests passing
    (12 new).
  - **Still NOT done** (real, harder problems, left for later - see
    `future-plans/house-layout-spec-checklist.md`): non-rectangular/L-shaped rooms, a double-loaded
    corridor, a full adjacency-graph solver, a real validate-then-regenerate loop, true road-facing
    orientation (no plot-orientation input exists at all), and zone-level (public-vs-private) area
    capping (Phase 1's max-area caps are intra-zone only). **The "no plot-orientation input" gap is
    now closed - see v15 immediately below.**
- **v15 (2026-09-04): real, optional North/South/East/West plot-facing input, defaulting to South.**
  Direct follow-up to v14's own "still not done" note - real user request: "take an input from the user
  while generating and ask the facing from north south east west side and make it optional and if the
  user doesnt choose anything keep the entrance from south."
  - **`static/index.html`** gained an optional "Which way does the entrance face?" `<select>`
    (`#house-facing`, blank/North/South/East/West) in the Plot Parameters panel, right above the existing
    free-text extras field. **`static/app.js`** only appends `facing` to the upload `FormData` when the
    user actually picked one (same "optional, only sent when non-empty" pattern as the plot photo).
  - **`app/main.py`'s `create_house_project`** gained a `facing: str | None = Form(None)` field -
    deliberately UNVALIDATED at this layer (matches the existing "silently correct obvious nonsense"
    treatment for floor_count/bedrooms/bathrooms) and folded into the EXISTING `house_inputs_json` dict
    alongside floor_count/bedrooms/bathrooms/extras - no new DB column needed, `HouseProjectStatusResponse
    .house_inputs` already exposes the whole dict.
  - **The real orientation convention** (`app/pipeline/floor_layout.py`'s `layout_floor()` docstring,
    the authoritative source): this project's north arrow (`blueprint_svg.py`'s decorative `_draw_north_
    arrow()`) has always pointed "up" (low real-y) - v15 makes that meaningful instead of arbitrary/
    decorative by defining low-y = North, matching standard map/architectural-plan convention (north up).
    South facing therefore puts the public/entrance zone at HIGH-y (the bottom of the rendered plan);
    east/west orient it along the x-axis instead.
  - **Implementation: a coordinate TRANSFORM around the unchanged core algorithm, not threading direction
    through the recursive logic.** `layout_floor()` was split into a thin public wrapper + the pre-
    existing algorithm (renamed `_layout_floor_core()`, verbatim, still always computing with public-at-
    low-y). The wrapper: for north, no transform. For south, mirrors every rect along y
    (`y = width - y - h`) - the whole computed layout flips top-to-bottom, moving the public zone from
    low-y to high-y. For east/west, the core algorithm runs on dimensions with length/width SWAPPED (so
    its own low-y front band ends up spanning the real x-axis once transposed back: `x = old_y, y =
    old_x, w = old_h, h = old_w`), then east additionally mirrors along x. This was a deliberate design
    choice over generalizing `_layout_private_zone()`'s hallway-edge logic to all 4 directions - it keeps
    every nested piece of this module (`_slice()`, the suite/hallway/corridor logic, `_pack_row()`) working
    in ONE unchanged coordinate convention; only the top-level wrapper needs to know about compass
    directions at all. Verified numerically (all 4 facings tile the plot exactly, no overlaps) AND
    visually (rendered the same house under all 4 facings and inspected each - garage-suppression, suite
    pairing, and kitchen-dining adjacency all continued to work correctly regardless of orientation).
  - **Two defaults, two different layers, both documented**: `layout_floor()`'s OWN default when `facing`
    is omitted/unrecognized is `"north"` - a neutral library default that keeps every pre-existing direct
    caller/test (none of which pass `facing`) behaving exactly as before this parameter existed. The
    PRODUCT default of `"south"` (the user's actual request) is resolved one layer up, in
    `run_house_pipeline()`, which then passes an explicit `facing="south"` down whenever the form didn't
    supply one - so a real generation with no facing chosen genuinely gets south, while a direct
    `layout_floor()` call (scripts, tests) is unaffected unless it opts in.
  - **`facing` threaded through the full pipeline**: `run_house_pipeline(..., facing_input=...)` resolves
    the real `facing` value BEFORE the `try` block that calls Gemini's `generate_room_layout()` - a real
    bug hit and fixed during this pass: an earlier version resolved it INSIDE that try, right after the
    Gemini call, so when that call raised, `facing` was never assigned, but code further down the (still-
    running) function unconditionally referenced it, causing an `UnboundLocalError` that masked the real,
    original failure. `front_yard_depth` trimming is now facing-aware too - it trims the `width` dimension
    for north/south facings (the yard is a strip along the y-extent) or `length` for east/west (a strip
    along the x-extent), matching whichever axis is actually "depth" for that orientation.
  - **The AI Concept Layout card stays in agreement with the real blueprint**: `Provider.generate_floor_
    plan()` gained an optional `facing` parameter (threaded through `base.py`/`hybrid.py`/`gemini.py`'s
    stub/`idealhouse.py`'s stub/`kaggle_autocad.py`'s real implementation), reaching `kaggle_autocad.py`'s
    `_conditioning_images_by_floor()` - which MUST receive the same resolved facing the real blueprint
    step used, or the AI card's conditioning image would silently trace a different (north-default)
    orientation than what the deterministic blueprint actually rendered. `_run_floor_plan_stage()` passes
    the pipeline's resolved `facing` through to this call.
  - **Tests**: `tests/test_floor_layout.py` gained 8 new tests (north-is-the-default, each of the 4
    facings puts the public zone on the correct edge, unrecognized/case-insensitive facing handling,
    exact-tiling for all 4 facings); `tests/test_house_pipeline.py` gained the south-by-default and
    explicit-facing-honored tests (both via a `layout_floor` spy, same pattern as the existing front-yard
    test); `tests/test_house_api.py` gained a facing-persists-in-house_inputs test;
    `tests/test_kaggle_autocad.py` gained a test confirming two different facings produce genuinely
    different conditioning images (not silently ignored). 465/465 passing (11 new).
- **v16 (2026-09-04): garage now gets a real, car-fitting WIDTH, not just the right total area.** Real,
  specific user report (spotted directly in a live render): a garage came out 6.1ft wide against a real
  ~8.9ft minimum for one car (a car's own width alone is ~6ft, before door-opening clearance) - the
  garage's AREA was correct (156.9 sq ft, exactly its computed minimum) but its SHAPE wasn't, because
  the general weighted-slicing algorithm has only ever guaranteed area, never width/depth individually
  (a real, already-documented limitation - see `room_specs.py`'s own "Known limitation" notes - but
  garage is the one room type where a wrong shape, not just a smaller-than-ideal area, makes it flatly
  non-functional for its one job).
  - **`room_specs.garage_dimensions(cars, unit) -> (width, depth)`** (new) returns garage's real minimum
    width/depth as two numbers, not just their product - the same underlying spec numbers
    `garage_min_area_sqm()` already used (2.7m/car width, 5.4m fixed depth), just exposed separately so
    the layout engine can constrain SHAPE, not only area.
  - **`floor_layout._slice_reserving_garage()`** (new, replaces the plain `_slice()` call at both of
    `_layout_floor_core()`'s call sites) carves the garage out FIRST as a real vertical strip - exact
    real width for the requested car count, spanning the box's FULL height - before the general weighted
    algorithm runs on whatever rooms remain in the narrower leftover box. This guarantees garage's WIDTH
    is always exactly correct; its DEPTH ends up being the box's full available height, which is usually
    MORE than the bare 5.4m minimum - a deliberate trade-off (documented in the function's own
    docstring): a real, usable width beats exact minimality for garage specifically. Every other room's
    own sizing/redistribution logic (min/max caps, suite pairing, etc.) is completely unaffected - this
    only changes garage's own final shape, not how much AREA the rest of the zone had available (that
    was already correctly reduced by garage's real min-area reservation, unchanged from before).
  - **A real, honest side effect, not silently hidden**: removing garage from the general algorithm
    narrows the box available to the REST of the front zone, which can shift how THOSE rooms' own
    aspect ratios come out - visually confirmed in a real render where "Entry" ended up an unusably thin
    ~1.3ft-deep sliver as a result. This is the SAME pre-existing "area guaranteed, shape isn't"
    limitation now surfacing for a different room, not a new category of bug - not fixed in this pass
    (the user's explicit ask was scoped to garage specifically; this is flagged for a future pass, not
    silently ignored).
  - **Verification**: rendered the exact previously-buggy room program and confirmed the garage's width
    is now exactly 8.9ft (matching `garage_dimensions(1, "ft")`), up from the buggy 6.1ft - both
    numerically (`tests/test_floor_layout.py`'s new tests: real width regardless of surrounding rooms,
    a 2-car garage is wider than 1-car, exact-tiling still holds with the carve-out) and visually
    (re-rendered and inspected the PNG). 469/469 passing (4 new).
  - **A SECOND, separate garage bug found the same day, on review from another AI's suggestion (verified
    independently before applying, not blindly trusted)**: even with the real-width fix above, the car
    icon itself could still fail to render - `blueprint_svg.py`'s `_draw_furniture()` gates ALL furniture
    symbols behind `_FURNITURE_MIN_BOX_W`/`_H` (70x60 PIXELS), and on a plot with a long enough side
    (>~99ft), a garage's real, CORRECTLY SIZED width (8.9ft) scales down under that pixel threshold even
    though its real-world size is fine - silently drawing an empty garage room with no car symbol.
    Confirmed live: a 150x100ft plot scales the garage's 8.9ft width to 46px, comfortably under the old
    70px gate. Fix: `_GARAGE_MIN_BOX_W`/`_H` (40x40px), a garage-specific lower threshold used only when
    `"garage" in name` - safe because `_furnish_garage()`'s car symbol is drawn purely proportionally
    (`car_w = w*0.6, car_h = h*0.7`, no fixed-pixel offsets that could break at a smaller box), unlike
    fixed-size fixtures (a bed's pillows, a toilet's real-world-proportioned rectangle) the general
    threshold protects. `name = rect["name"].lower()` moved up in the function so the gate can check room
    type before deciding which threshold applies. Verified both by re-rendering the exact 150x100ft
    scenario (car icon now visible) and with new tests (`tests/test_blueprint_svg.py`) confirming the
    lower threshold is genuinely garage-specific - a non-garage room at the identical box size stays
    suppressed, and a garage below even ITS OWN lower threshold still gets suppressed too (the fix lowers
    the bar, it doesn't remove it). 472/472 passing (3 new).
  - **A THIRD, separate garage bug found the same day, real user report with a screenshot: "in the prompt
    i didnt tell it to generate a garage but it did and when it is adding a garage its taking too much
    space, which is cramping up every room and text."** Root cause: `ROOM_LAYOUT_PROMPT_TEMPLATE`
    (`app/providers/gemini.py`) explicitly leaves adding a garage up to Gemini's OWN judgement ("the
    ground floor should hold... and - if it fits the requirements - a garage..."), not gated on the user
    having actually asked for one - a probabilistic decision, not a real signal. Combined with the v16
    width fix above, `_slice_reserving_garage()` gives ANY room merely NAMED "Garage" the real,
    full-box-HEIGHT carve-out treatment regardless of whether `garage_cars` (the deterministic, real
    "did the user ask for this" signal) was ever set - so a Gemini-invented garage on a 100ft-deep plot
    came out as an 8.9x60.9ft strip (540 sq ft) running the full depth of the front zone, squeezing every
    other public room and its label into a fraction of the space. Confirmed by literally reproducing the
    reported screenshot's exact room mix and rendering it - the reproduction matched (see verification
    below). Fix (`app/pipeline/generate_house.py`, right after `garage_cars` is resolved): whenever
    `garage_cars` is falsy (the user's own requirements text has no "garage" mention at all - see
    `house_requirements.mentions_garage()`), every room on every floor that `classify_room_category()`
    would call "garage" is stripped out of `room_layout` before it ever reaches `layout_floor()` - same
    "deterministic parsing overrides a probabilistic LLM decision" pattern already used to GUARANTEE a
    garage that WAS requested (the injection block right below this), just its mirror image for the
    unrequested case. Only Gemini's OWN invented garage is affected - a real user-requested garage still
    goes through the existing injection + real-width-carve-out path unchanged.
    **Verification**: reproduced the exact reported room mix (5 private-zone rooms + garage/entry/living/
    dining/kitchen on a 100x100ft plot) via a scratch render - before the fix, garage came out as the same
    disproportionate full-height strip described above; after the fix, no garage room exists at all and
    the freed area goes back to Living Room/Dining/Kitchen via the normal weighted redistribution (Living
    Room grew from 3209 to 3485 sq ft). New regression test
    (`test_run_house_pipeline_strips_an_unrequested_garage_gemini_invented` in
    `tests/test_house_pipeline.py`) uses a `FakeProvider` subclass whose `generate_room_layout()` already
    returns a self-invented Garage room, with no "garage" mention anywhere in the pipeline's prompt text -
    asserts the persisted `room_layout_json` has no Garage room at all. 473/473 passing (1 new).
  - **A separate question asked alongside the garage report: why does the AI "Concept Layout" card
    sometimes have a dark/gray background instead of light?** Initially diagnosed as a pure vendor/notebook
    concern outside this repo's control (see below) - then, on the user asking whether it could be
    constrained to one color, investigated further and found to be genuinely fixable HERE, via
    post-processing, not the notebook.
    - **Root cause**: the model has no fixed random seed (no `seed` field exists anywhere in the confirmed
      `kaggle_autocad.py` request contract), so nothing pins its output to one visual style run to run.
      Comparing multiple REAL saved samples in `scripts/output/` (not synthetic guesses) showed this isn't
      unbounded randomness - it's a flip between near-opposite color POLARITIES of the same drawing: a
      light background with dark line work (the desired look, mean luminance ~140-170/255 across samples)
      versus a literal "blueprint" - white line work on a dark blue-gray background (mean ~105/255,
      `scripts/output/latest_kaggle_floor1.png` - this is what the user's dark screenshot actually was).
      A third style (a mid-gray "rendered slab" look with a light margin, mean ~140) is real variance too
      but is NOT simply an inversion of the light style.
    - **Fix**: `app/providers/kaggle_autocad.py`'s `_normalize_dark_background()` (new) computes the
      returned image's average luminance and, when it falls below `_DARK_BACKGROUND_MEAN_THRESHOLD` (128),
      inverts the image (`PIL.ImageOps.invert`) before compositing room labels. Deliberately
      ONE-DIRECTIONAL - only ever lightens a dark image, never touches an already-light one - so the
      unrelated mid-gray "rendered slab" variant (well above the threshold) is left alone rather than risk
      a blind heuristic making it worse. Verified by inverting the actual saved dark sample and inspecting
      the result (not just reasoned about) - it recovers a light parchment-style drawing close to the
      desired look, confirmed visually before writing any code.
    - **Still true, unchanged from the earlier diagnosis**: the model's ACTUAL rendering choices (which
      polarity, contrast, whether walls are poché-filled, prompt wording) are entirely the friend's Kaggle
      notebook's own domain, not this repo's - this fix doesn't change what the model generates, it
      corrects the result afterward, deterministically, in code we control. If the model starts producing
      a style this heuristic doesn't handle (a truly novel polarity, not just light-vs-dark), that would
      need a new case here, not a notebook change.
    - **Tests**: `tests/test_kaggle_autocad.py` gained `test_normalize_dark_background_inverts_a_dark_image`,
      `test_normalize_dark_background_leaves_a_light_image_untouched`,
      `test_normalize_dark_background_leaves_a_mid_gray_image_untouched` (unit tests against synthetic
      solid-color images), and `test_generate_floor_plan_normalizes_a_dark_polarity_response` (end-to-end,
      confirms the fix is actually wired into `generate_floor_plan()`'s return path, not just defined).
      477/477 passing (4 new).
    - This card stays labeled "Concept Layout - not a precise blueprint" regardless of polarity - the
      deterministic Pillow-drawn blueprint next to it (light linen background, solid poché walls, real
      dimensions) remains the accurate, authoritative one; the AI card is a supplementary visual only.

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
   `.env`, R2 still viable). SQLite stores **metadata + storage keys only**, never image blobs.
   **Real key structure** (as of the 2026-08-20 per-feature-folder reorg, see below):
   `users/{namespace}/roomRedesign/input/{project_id}/...` /
   `users/{namespace}/roomRedesign/output/{project_id}/...` for Room Redesign, and
   `users/{namespace}/buildAHouse/input/{house_project_id}/...` /
   `users/{namespace}/buildAHouse/output/{house_project_id}/...` for Build a House
   (`app/main.py`, `app/pipeline/generate.py`, `app/pipeline/generate_house.py`) - `local.input/...`/
   `local.output/...` (no `users/` prefix at all) is only the PRE-AUTH FALLBACK when `username` is
   `None` (`key_prefix = ... if username else "local.output"` - never actually hit in the real
   authenticated app, since every generator endpoint requires a logged-in user; kept only so
   `run_pipeline`/`run_house_pipeline` don't hard-crash if ever called without one, e.g. in older tests).
   **Per-user-then-per-feature folders (`users/{namespace}/roomRedesign/...` /
   `.../buildAHouse/...`), not per-feature-then-per-user** - a deliberate choice: it keeps everything one
   user ever generated, across both tools, under a single browsable S3 prefix (`users/{namespace}/`) -
   easier to audit/delete one user's data or just browse the bucket by person - rather than splitting the
   same user's files across two disconnected top-level trees. This was **additive**: existing projects'
   already-stored keys (from before this reorg) are untouched and keep resolving correctly, since every
   `Project`/`HouseProject` row stores its own exact keys at creation time and nothing reconstructs them
   later - only NEW generations after the reorg land under the new `roomRedesign`/`buildAHouse` segment.
   Both features also now write an input **`metadata.json`** next to the upload (`interior_style`/
   `color_palette`/`additional_instructions`/`city`/`room_dimensions` for Room Redesign;
   `dimensions`/`house_inputs` for Build a House) - best-effort, wrapped in try/except, never blocks
   project creation - so a user's full input history is browsable directly from S3, not just via the DB.
   Build a House previously wrote NO metadata JSON to S3 at all (only Room Redesign did) - this closed
   that parity gap. Neither feature writes a RESULT-side JSON to S3 (materials/room_layout/etc. stay
   DB-only, in `materials_json`/`room_layout_json`/`meta_json`) - only the input-side mirror exists.
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
  `floor_plan` slot. Auth (`POST /api/auth/{signup,login}` at the time this reconciliation was written) is
  now entirely Clerk's own hosted UI, not a backend contract this codebase defines — see "Authentication
  (Clerk)" below. There are exactly **two generator tools** (Room Redesign, Build a House), a landing tab,
  and login/signup — no third tool, no other pages.
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
  success simulation and the color-swap "Access Granted" fake success state; the dead Google/Apple
  social-login buttons (no OAuth backend existed at the time); the "Encrypted with AES-256" badge (a false
  technical claim); the placeholder text in Full Name/Email/Password (removed per user request — the
  mockup's fake example values like "Mies van der Rohe" read as pre-filled data, not a hint).
  **Superseded**: this bullet originally described a real hand-built username/password + bcrypt system
  that replaced the mockup's fakes (including a since-removed visible Username/Role form pair, auto-derived
  usernames, etc.) — that entire system, along with the Google OAuth flow eventually built alongside it,
  has since been replaced wholesale by Clerk. See "Authentication (Clerk)" below for the current, real
  system; nothing about the stripped Stitch-mockup fakes changes as a result, only what replaced them.
  **Real bug fixed at the time (kept for the CSS lesson, independent of the auth-system swap)**: Chrome/Edge
  force a light yellow/white autofill background via an internal
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

## Authentication (Clerk)

**Auth is now entirely owned by Clerk (https://clerk.com)** — signup, login, Google sign-in, session
issuance, and the account UI itself. This replaced an earlier hand-built system (bcrypt password hashing +
Starlette session cookies + a hand-written Google OAuth flow) entirely, not incrementally — see "Why this
replaced the old system" below for the real incident that drove it.

- **Frontend** (`static/clerk-init.js`, shared by `index.html`/`login.html`/`signup.html`): loads Clerk's
  JS SDK via plain `<script>` tags from Clerk's own CDN — no npm/bundler, same convention as
  Tailwind/GSAP elsewhere in this project. **Two scripts are required, in order**: `@clerk/ui@1/dist/
  ui.browser.js` THEN `@clerk/clerk-js@6/dist/clerk.browser.js` — a real bug hit while wiring this up:
  loading only `clerk-js` (the quickstart's more prominent script tag) throws `"Clerk was not loaded with
  Ui components"` the moment `mountSignIn()`/`mountSignUp()` is called, confirmed live (widgets silently
  failed to render, console showed the exact error) before the `@clerk/ui` script was added. `Clerk.load()`
  is called with `{ ui: { ClerkUI: window.__internal_ClerkUICtor } }` — the value `ui.browser.js` sets on
  `window` for `clerk-js` to pick up. Exposes a single `clerkReady` promise every page awaits before
  touching `window.Clerk`. The **publishable key** (`pk_test_...`) is hardcoded directly in this file — safe
  to expose, it's meant to be public (unlike `CLERK_SECRET_KEY`, backend-only, in `.env`). The **Frontend
  API domain** Clerk's script URLs need is decoded from the publishable key itself
  (`base64(frontendApiDomain + "$")` is the middle segment of a `pk_test_.../pk_live_...` key) rather than
  hardcoded a second time — switching Clerk instances only ever means updating `CLERK_PUBLISHABLE_KEY`.
- **`login.html`/`signup.html`**: the old hand-written forms + `/api/auth/login|signup` fetch calls +
  client-side username-derivation are gone entirely. Each page now just mounts Clerk's own pre-built
  widget into a `<div>` (`Clerk.mountSignIn(...)` / `Clerk.mountSignUp(...)`) with
  `fallbackRedirectUrl: "/"` and an `appearance: { variables: { colorPrimary: "#7A4712" } }` override to
  match this project's brand accent — everything else about Clerk's widget (email/password fields, the
  "Continue with Google" button, validation, error states) is Clerk's own UI, not this codebase's. No
  username field anywhere anymore — see below for why.
- **`app.js`**: `applyAuthUI(user)` drives the nav/mobile-sidebar logged-in-vs-guest state directly off
  `Clerk.user` (available client-side once `clerkReady` resolves — `user.fullName`, `user.username`,
  `user.primaryEmailAddress.emailAddress`), **not** a round-trip to this backend — there's no `/api/auth/me`
  equivalent anymore, Clerk already has this loaded client-side. Kept live via `Clerk.addListener()` so
  logging out updates the UI immediately, no page reload needed. `authFetch(path, options)` attaches a
  fresh `Authorization: Bearer <token>` header (`await Clerk.session.getToken()`, which Clerk caches in
  memory and only re-issues over the network once actually near expiry) to every authenticated API call —
  this is the **entire** auth mechanism reaching the backend now, no cookies/`credentials: "include"`
  involved at all. Logging out calls `Clerk.signOut()` directly, no backend endpoint involved.
- **Backend** (`app/auth.py`): `get_current_user(request)` reads the `Authorization: Bearer <token>`
  header, verifies it via `clerk_backend_api.security.verify_token(token, VerifyTokenOptions(secret_key=
  settings.clerk_secret_key))`, and returns an `AuthUser(id=payload["sub"])` — just the Clerk user id
  (e.g. `"user_2abc..."`), nothing else. `require_user()` 401s if that's `None`. There is **no local
  `User` table anymore** — Clerk's session JWT doesn't carry email/name by default, and nothing
  server-side needs them (the frontend already has that from `Clerk.user` directly), so there was nothing
  left to store locally. `Project.user_id`/`HouseProject.user_id` are now plain `Optional[str]` columns
  holding the Clerk user id string directly — **not a local FK anymore** (there's no table left to
  reference). An existing Postgres (Neon) database from before this migration needed
  `app/db.py::_drop_legacy_user_table()` (runs once on every non-SQLite startup, idempotent) to drop the
  old FK constraints and the orphaned `user` table — without it, Postgres would reject every new
  project/house-project insert with a foreign-key violation, since a Clerk user id will never exist as a
  row in that now-unmanaged table.
- **Per-user S3 namespacing unchanged in shape, different value source**: storage keys are still
  `users/{id}/input/{project_id}/...` / `users/{id}/output/{project_id}/...` — `{id}` is now the Clerk
  user id (`user.id` in `app/main.py`) instead of the old hand-picked `username`. Clerk ids are
  alphanumeric + underscore, already a safe S3 path segment with **no separate charset validation
  needed** — the old `app.auth.validate_username`/`USERNAME_PATTERN` machinery is gone, since there's no
  user-chosen username left to validate at all.
- **Human-readable label appended to the S3 prefix**: `app/main.py::_storage_namespace(user_id,
  display_name)` turns `{id}` above into `{id}_{sanitized_display_name}` (e.g.
  `user_2abc123_jane_doe`) — added because opaque Clerk ids alone made browsing the bucket to find a
  specific person's files require cross-referencing the Clerk dashboard every time. `display_name` comes
  from `POST /api/projects`/`/api/house-projects`'s new `display_name` Form field, sent by `app.js`'s
  `currentUserDisplayName()` (reads `Clerk.user.fullName`/`username`/email client-side — no extra network
  call, already loaded). **Deliberately not re-verified server-side** (only sanitized, never trusted for
  auth/ownership — that's still the exact Clerk `user_id`, compared elsewhere in this file) since it's
  only ever used for a folder label. **The Clerk id stays the stable, authoritative prefix** with the
  name appended, not the reverse — so a later display-name change on Clerk's side only affects the
  *next* upload's folder name, never orphans or breaks access to previously-created projects (each
  `Project`/`HouseProject` row stores its own exact storage keys at creation time, never reconstructs
  them from the user id later). Applies identically whether the session came from Clerk's email/password
  flow or its Google sign-in — both produce the same kind of Clerk user object client-side, no
  special-casing needed. Falls back to the bare id (old behavior) if no display name is available yet
  (e.g. a brand-new profile with nothing set).
- **Generator gating unchanged**: `create_project`/`create_house_project` still require
  `Depends(require_user)` and set `project.user_id = user.id`; `get_project`/`get_house_project` still
  return **404 (not 403)** for both "doesn't exist" and "exists but isn't yours", for the same
  id-enumeration reason as before.
- **Google sign-in**: fully owned by Clerk now — enabled/configured entirely inside the Clerk Dashboard
  (Google as a social connection), not in this codebase. `app/google_oauth.py` (the old hand-written
  three-request Authorization Code flow) is **deleted**, not dormant.
- **Why this replaced the old system**: real incidents, not a preference swap. (1) Signups vanished
  overnight because SQLite lived on Render's ephemeral filesystem and was wiped on every redeploy — fixed
  first by migrating to hosted Postgres (see "Provider split"-adjacent `resolved_database_url` in
  `app/config.py`), but that alone didn't explain everything. (2) Even after the DB fix, the nav UI never
  showed a logged-in state on Safari/iOS — traced to Safari/WebKit's ITP silently dropping the session
  cookie because it was cross-site (Vercel frontend, Render backend, different domains), confirmed via a
  live side-by-side test: identical request/response, Chromium stored the cookie and logged in correctly,
  WebKit discarded it outright. A Vercel-proxy workaround (`vercel.json`'s `/api/:path*` rewrite, making
  requests same-origin) fixed the cookie problem but the user then chose to migrate to Clerk entirely
  rather than keep maintaining a hand-rolled auth stack — Clerk's Bearer-token pattern sidesteps
  cross-site cookie blocking structurally (a Bearer header isn't subject to `SameSite`/third-party-cookie
  rules at all), on top of removing password-hashing/session-security/OAuth-flow code this project no
  longer has to own or debug.
- **Tests** (`tests/conftest.py`): `_fake_clerk_verification` (autouse) monkeypatches
  `app.auth.verify_token` with a deterministic fake that accepts only `"test|<clerk_user_id>"` Bearer
  tokens and rejects everything else — Clerk is never called for real in tests, same convention as every
  other provider (see "Testing convention" below). `login_as(client, user_id=None)` attaches that fake
  token to a `TestClient`'s headers (persists across requests on that client) and returns the user id —
  the direct replacement for the old `_signup_and_login()` helper, aliased to it in `test_api.py`/
  `test_house_api.py` so all existing call sites needed zero changes. `tests/test_auth.py` covers
  `get_current_user`/`require_user` directly (missing header, non-Bearer header, invalid token, valid
  token) plus a couple of integration checks against the real app (401 without/with a bad token, 200 with
  a valid one). `tests/test_google_oauth.py` is deleted — there's no local Google OAuth code left to test.

## Honest, real-signal-driven progress bar

Previously (`static/app.js`'s `createSimulatedFill()`) the progress bar was a **hardcoded timer**: +0.6%
every 150ms up to a `CAP = 92`, reached in ~23 seconds regardless of whether the actual generation took 1
minute or 5 — then it sat frozen at 92% for however much longer the real work took, fully decoupled from
reality. A real, reported bug. The stage *stepper* right next to it was never affected — it already derived
its state from real poll-response fields (`deriveStageIndex`/`deriveHouseStageIndex`) — only the bar itself
was fake.

- **Replaced with `createSignalFill()`**, anchored to the SAME real signals the stepper already reads.
  Each poll, `computeRoomProgressTarget(data)`/`computeHouseProgressTarget(data)` compute two numbers from
  real response fields (room: `room_description` presence, each tier image presence, `materials_status`;
  house: `plot_description`, `blueprint_status`, `images.render` presence) — `confirmed` (the percent
  actually justified by data that has arrived) and `ceiling` (a soft cap just below the next expected
  milestone). Both are monotonic (`Math.max` against the previous value) — a later poll can never move the
  bar backward.
  - **No hardcoded time assumption anywhere** — `createSignalFill()`'s `requestAnimationFrame` loop only
    ever eases `current` toward `Math.max(confirmed, ceiling)`, decelerating as it approaches (so it's
    never visibly frozen between the 3s polls) but never overtaking a milestone that hasn't actually been
    confirmed by real data yet. A 1-minute and a 5-minute generation both pace correctly, since the bar's
    motion is driven entirely by which poll responses have arrived, never by elapsed wall-clock time.
  - Room milestone weights sum to 92% at "materials settled" (mirroring the old 92% cap's role, but now
    reached only when genuinely justified, not by a timer), leaving the final jump to 100% for the real
    `done` response. House: 92% at `images.render` present.
  - `finish()` is still the ONLY path to 100%, called solely from the real completion callback
    (`finishProgress`/`finishHouseProgress`) — unchanged from before. `start()`/`finish()` method names are
    also unchanged, so `startProgressMessages()`/`finishProgress()` (room) and
    `startHouseProgress()`/`finishHouseProgress()` (house) needed zero call-site changes — only
    `applyPollUpdate()`/`applyHousePollUpdate()` gained one `setProgress(confirmed, ceiling)` call each,
    computed fresh every poll.

### Real bug fixed (2026-09-03): refresh mid-generation left the progress bar stuck at 0% forever

The reconnect mechanism below (`resumeActiveGeneration()`) was already resuming polling correctly, but
the dispatch call that invokes it (`if (localStorage.getItem(ACTIVE_GENERATION_KEY)) { resumeActive
Generation(); } else { restorePendingGeneration(); }`) lived near the TOP of `static/app.js` (right after
both functions were defined), which ran BEFORE several `const`s those functions depend on were
initialized further down the file — specifically `roomProgressFill`/`houseProgressFill`
(`createSignalFill(...)`, declared ~120 lines later). Accessing a `const` before its initializing line
throws a real JS temporal-dead-zone `ReferenceError`. Since `resumeActiveGeneration()` is `async` and was
called without `await`/`.catch()`, that error surfaced as a silently unhandled promise rejection — the
function had ALREADY switched to the progress tab and shown the progress card (that part runs before the
crash point) but never reached `startProgressMessages()`'s `roomProgressFill.start()` successfully, and
never started polling. Net effect: a real, reported bug — refresh during generation left the page frozen
on the progress screen at 0%, unrecoverable without manually clearing `localStorage`.
**Fix**: moved the entire resume/restore dispatch block to the literal end of `static/app.js` (after
every module-level `const`/`let`/function it touches), and replaced the bare unawaited calls with
`.catch()` handlers that log the error and fall back to a clean state (`clearActiveGeneration()` for the
resume path) — so a future bug in either path degrades to the upload screen instead of a stuck page.
Covered by manual testing (refresh mid-generation on both tabs); no automated test exists for this
specific ordering bug since it's a page-load/module-evaluation-order issue, not something the existing
`TestClient`-based test suite exercises.

### Real bug fixed (2026-09-04): History dates displayed in the wrong timezone

`Project.created_at`/`HouseProject.created_at` are always written with `datetime.now(timezone.utc)`
(`app/models.py`'s `_now()`), but **SQLite silently drops the tzinfo on round-trip** - confirmed live by
reading a row straight back from the DB: `datetime.datetime(2026, 9, 4, 6, 18, 18, ...)`, `tzinfo=None`.
A plain `project.created_at.isoformat()` on that naive value produced an ambiguous string with no
`Z`/offset suffix (e.g. `"2026-09-04T06:18:18.074068"`). `static/app.js`'s `new Date(iso)` then
misinterpreted that string as the **browser's own local time** instead of UTC, before `formatHistoryDate()`
converted "as if UTC" to Asia/Karachi for display - compounding into a wrong displayed time (the History
modal's dates were off by whatever the browser's own UTC offset happened to be, not by a clean, obvious
amount). **Fix**: `app/main.py`'s new `_utc_isoformat(dt)` helper reattaches `timezone.utc` to a naive
datetime before formatting (safe - every stored value IS UTC by construction, this just makes that
unambiguous once serialized), used at both `created_at=` call sites (`_project_to_response()`/
`_house_project_to_response()`). Frontend side: `static/app.js`'s `formatHistoryDate()` now also always
shows a real time-of-day (not just the date - same-day generations were previously indistinguishable),
fixed to `timeZone: "Asia/Karachi"` (real GMT+5, no DST, confirmed correct - this app "isn't going
international" so a single fixed timezone beats the browser's own). Room Redesign and Build a House
**share the exact same `formatHistoryDate()` function** (`renderHistoryRoomCard()`/
`renderHistoryHouseCard()` both call it) - fixing it once fixed both, no per-tab duplication needed.
Regression-guarded: `tests/test_api.py`/`tests/test_house_api.py` now assert
`datetime.fromisoformat(body[0]["created_at"]).tzinfo is not None` - a naive round-trip through SQLite
regressing this fix would fail these tests immediately, without needing to inspect exact clock values.

## Resilient loading (reconnect on reload/navigation) + Cancel generating…

Generation was already a server-side FastAPI `BackgroundTask` (`app/main.py`'s `create_project`/
`create_house_project` → `background_tasks.add_task`) that always runs to completion regardless of the
client — but the live progress VIEW lived only in an in-memory JS variable (the project id passed into
`pollProject()`/`pollHouseProject()`), so a reload or navigating away (e.g. to sign up) orphaned it: the
generation kept running server-side, but the user landed back on a blank upload screen with no indication
anything was happening, recoverable only via History once it finished. Two real problems fixed together:
the view not surviving navigation, and no way to actually stop/discard a generation the user no longer
wants.

- **Reconnect**: `static/app.js` persists `{tab, id}` to `localStorage` under `interior-gen:active-generation`
  (`saveActiveGeneration()`/`clearActiveGeneration()`) the moment a generation is successfully created,
  cleared the moment it reaches any terminal state (`done`/`failed`/`cancelled`) or the user explicitly
  cancels it — deliberately `localStorage`, not the pre-existing `sessionStorage`-based
  `savePendingGeneration()` (a SEPARATE, one-shot 401-login-redirect handoff for UNSUBMITTED form fields,
  untouched by this feature — the two mechanisms solve different problems and don't share a key).
  `resumeActiveGeneration()` runs at page load (alongside the existing `restorePendingGeneration()` — a
  real in-flight generation takes priority if both keys somehow exist) and instantly switches to the right
  tab, shows the progress card, and resumes polling from where it left off — the poll response's real data
  (materials_status, blueprint_status, image presence) naturally reconstructs the correct stepper/stage
  state, no separate "resume state" needed.
- **Cancel-and-discard, not just "stop watching"**: a real **"Cancel generating…"** button on both progress
  cards. Best-effort — a paid image/GPU call already in flight when clicked still finishes; its result is
  simply discarded rather than shown or saved, matching the plainly-communicated limit of what a server-side
  BackgroundTask can do (no true mid-request kill). Backend: `POST /api/projects/{id}/cancel` and
  `POST /api/house-projects/{id}/cancel` (`app/main.py`) flip `status` to a new `"cancelled"` value —
  idempotent, only actually changes `queued`/`running` rows, owner-scoped with the same 404-not-403
  treatment as every other project endpoint. The pipelines (`run_pipeline()`/`run_house_pipeline()`) poll
  for this via `_is_cancelled(session, project)` (`app/pipeline/generate.py`/`generate_house.py`) at stage
  boundaries — room: before image generation starts, and again right after it finishes (before materials
  join/`done`); house: before the blueprint step, and again right before the paid render call (the most
  valuable checkpoint, since it's the costly step). **`_is_cancelled()` MUST call `session.refresh(project)`,
  not read the in-memory object** — the cancel request lands via a completely separate request/session, so
  the long-lived pipeline session's own identity-map cache would otherwise never see it. On a hit, the
  pipeline returns immediately without overwriting `status` back to `done`/`failed`. `list_projects`/
  `list_house_projects` exclude `status == "cancelled"` rows so a discarded run never reappears in History.
  No S3 cleanup — a cancelled project is simply never rendered again, and its (if any) partial objects are
  harmless orphans.
- **Frontend poll-loop safety**: `roomPollCancelled`/`housePollCancelled` + `activeRoomProjectId`/
  `activeHouseProjectId` module-level flags let a poll already scheduled via `setTimeout()` before a Cancel
  click recognize it's stale and stop rescheduling itself, instead of racing the UI back into "progress"
  after the user already left it. `pollProject()`/`pollHouseProject()` also handle a `"cancelled"` status
  arriving mid-poll (e.g. resumed after reload, discovering the generation was cancelled from elsewhere) by
  quietly returning to the upload screen.
- **Test note**: `tests/test_pipeline.py`'s/`tests/test_house_pipeline.py`'s cancellation tests simulate a
  concurrent cancel request by opening a genuinely SEPARATE `Session(engine)` from inside a fake provider
  method (mirroring what the real cancel endpoint does) - one room test needed a `StaticPool`-backed test
  engine specifically because it does this from a WORKER THREAD (the per-tier `ThreadPoolExecutor`), and
  plain SQLite in-memory pooling gives each thread its own separate `:memory:` database, silently "losing"
  the table. Real production code never opens a session from a worker thread (see `run_pipeline()`'s own
  comment on that) — this is purely a test-simulation need. Also: `tests/test_api.py`/`tests/test_house_api.py`
  create rows directly via `app.db.engine` (no `TestClient` POST reaches a `queued`/`running` state
  observably — `BackgroundTasks` run synchronously within `TestClient`'s request, so a project is already
  `done` by the time the create response returns) — and use random `uuid4`-suffixed ids, since **this
  project's test suite has no DB isolation for `test_api.py`/`test_house_api.py`** (they hit the real
  configured `DATABASE_URL`, normally the dev `data/app.db` file) — a fixed literal id would collide with
  a leftover row from the same test's own previous run.

## Model attribution ("Generated with our model" / "Generated with OpenAI")

After a generation completes, the results screen (both Room Redesign and Build a House) shows a short line
naming which backend actually produced the images — **explicit user requirement: never mention "fallback"
or imply one path is a backup for another**, even when the OpenAI reliability fallback (see "Provider
split" above) is exactly what ran.

- **The only place that knows which provider actually produced any bytes is inside `HybridProvider`**
  (`app/providers/hybrid.py`) — its `generate_image`/`generate_images_batch`/`generate_house_render` are
  the sole call sites where the success-vs-fallback branch is decided. `_PROVIDER_LABELS` maps class name →
  a plain two-value label: `KaggleImageProvider`/`ModalImageProvider` → `"our model"` (both self-hosted —
  the distinction that matters to the user is self-hosted vs. paid third-party API, not which self-hosted
  platform happened to be configured), `OpenAIImageProvider` → `"OpenAI"`. Recorded via
  `_record_tier_label()` (room, per-tier) or inline after a successful/fallback batch call, into
  `self._tier_provider_labels: dict[str, str]` (keyed by tier) and `self._house_provider_label: str | None`
  — guarded by a lock since the 3 room tiers run on separate threads
  (`app/pipeline/generate.py`'s `ThreadPoolExecutor`).
- **`get_image_model_label()`** returns `"our model"` if every recorded tier came from the self-hosted
  backend, `"OpenAI"` if every tier came from OpenAI, `"our model + OpenAI"` if tiers genuinely diverged
  (only possible on the per-tier, non-batch path, since each tier's fallback is independent — see
  `HybridProvider`'s class docstring's "RUNTIME FALLBACK" section), or `None` before any tier completes.
  **`get_house_render_model_label()`** is always a single value (no `"+"` case) since house rendering has
  no runtime fallback.
- **Pipeline**: `run_pipeline()`/`run_house_pipeline()` call these via
  `getattr(provider, "get_image_model_label", lambda: None)()` (same optional-method pattern already used
  for `supports_batch()`) — so a test `FakeProvider` that doesn't implement the method simply gets `None`,
  no crash. The label is stored on a new nullable column (`Project.image_model` / `HouseProject.render_model`,
  additive migration in `app/db.py`) AND inside the existing `meta_json` blob, and exposed on
  `ProjectStatusResponse.image_model` / `HouseProjectStatusResponse.render_model`.
- **Frontend**: `renderResults()`/`renderHouseResults()` (`static/app.js`) show `"Generated using {label}"`
  in a small line under the room/plot description (`#image-model-note` / `#house-image-model-note` in
  `static/index.html`) whenever the field is present; hidden otherwise (e.g. old projects generated before
  this feature, where the column is `None`). Wording is "using" (2026-09-03, matching the exact requested
  copy) — was "with" before. `renderResults()`'s `displayModelLabel()` collapses the rare
  `"our model + OpenAI"` combined room label down to a single `"our model"` before display — the user only
  ever sees one clean claim, never the per-tier split (`get_house_render_model_label()` never has a `"+"`
  case at all, so `renderHouseResults()` doesn't need the same collapsing).

## Custom 404 page (2026-09-03)

Previously an unknown route (`/anything`) returned FastAPI's bare default `{"detail":"Not Found"}` JSON —
no styling, generic. `static/404.html` is a real page built from `index.html`'s ACTUAL current head/header/
footer markup (Tailwind CDN + the same inline `tailwind.config` token block, Fraunces+Poppins,
`bg-background`/`primary`/`night` tokens) — not from `terms.html`/`privacy.html`, which CLAUDE.md's own
"Frontend" section already flags as a stale, unretouched theme; matching those would have propagated the
same staleness into a brand-new page. `app/main.py`'s `custom_404_handler()` (a
`StarletteHTTPException` handler registered via `@app.exception_handler`) serves it for any 404 whose path
does NOT start with `/api/`, `/static/`, or `/media/` — API callers still get FastAPI's normal JSON 404
(a frontend `fetch()` checking `res.ok`/parsing JSON must never receive an HTML body), and static/media
asset misses stay plain 404s too (a missing image shouldn't return a full HTML page). Covered by
`tests/test_api.py`'s `test_unknown_page_serves_styled_404`/`test_unknown_api_route_still_returns_json_404`.

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
  new self-hosted infrastructure. Current prompt version is `v11.3` (`PROMPT_VERSION` in `prompts.py`) - see
  the Tier/prompt seam section above for the v7/v8/v9 history, and prompts.py's own module docstring for
  v11/v11.3.
- **v11.3 (2026-08-20) fixed a real, user-reported "Industrial style / Earthy palette doesn't look right"
  complaint** - two concrete, findable-in-text bugs (`app/pipeline/prompts.py`), not a rendering fluke:
  (1) `TIER_CEILING["premium"]` said "warm gold-lit trim" - a literal COLOR word inside a Budget Tier
  sentence, violating this module's own "colors live only in `COLOR_PROFILE`" rule, so it fought every
  non-gold palette (Earthy included) at the premium tier specifically; fixed by describing only the
  ceiling's structure/light-quality, no color. (2) The generic tier vocabulary
  (`TIER_MATERIAL_QUALITY`/`TIER_CEILING`/`TIER_FLOORING`) assumes a "premium = polished marble, gold-cove
  ceiling, travertine/walnut flooring" aesthetic that's the opposite of Industrial's raw-materials identity
  (concrete, exposed metal, brick) - Budget Tier comes right after Interior Style in PROMPT PRIORITY, so it
  was actively overriding the style the user chose. Fixed with `INDUSTRIAL_TIER_MATERIAL_QUALITY`/
  `_CEILING`/`_FLOORING` overrides, wired into `build_prompt()`, `build_tier_spec()` (materials pricing -
  so priced items match what's actually rendered), and `build_kaggle_prompt()` (the currently-active Kaggle
  path) whenever `style == "Industrial"` - same quality ladder, materials congruent with the style instead
  of fighting it. Deliberately scoped to ONLY Industrial, not a general per-style system - see
  prompts.py's module docstring for why the other 8 styles didn't need this.
  **Verification status: NOT YET confirmed against a real render** - the fix was validated via the
  composed-prompt text (printed and reviewed - confirmed no more marble/gold/travertine/walnut mentions for
  Industrial, correct earthy-tone reinforcement) and the automated test suite
  (`tests/test_prompts.py`'s `test_tier_ceiling_never_mentions_colors`/
  `test_industrial_premium_does_not_use_generic_luxury_materials`/etc.), but NOT against an actual paid
  generation - no OpenAI credits were available at the time this shipped. Per this project's own repeated
  lesson (documented throughout this Tier/prompt seam section), prompt-only reasoning has been wrong before
  real renders more than once - **a real Industrial + Earthy generation should be run and visually
  inspected the next time credits are available**, before treating this as fully verified.
- Gemini text quota (room description) is free/separate from image gen; image generation is paid
  (OpenAI). Localhost-only deployment is intentional for Phase 1.
- `GEMINI_IMAGE_MODEL` env var / Gemini image path is dormant, not deleted — kept for a possible future
  billing-enabled fallback.

## Future plans

`future-plans/` (project root) holds parked/deferred plans - work that was scoped and intentionally set
aside rather than dropped, usually to keep a change focused (e.g. "get the core output right before
touching styling"). Check there before assuming an idea was never considered.

- `future-plans/build-a-house-theme-parity.md` — porting Room Redesign's visual polish (image hero, status
  chip, concept-preview shimmer tiles, results hierarchy) onto the Build a House tab. Deferred so the
  house render/blueprint quality work (see "Build a House feature" above) could ship first.
- `future-plans/feasibility-checker.md` — an "is this possible?" checker for Build a House (e.g. warn +
  suggest fixes when requested extras can't fit the stated plot, like a basketball court on a tiny lot).
  Recommended approach: deterministic area math (Python, authoritative) + Gemini reasoning/phrasing over
  those real numbers - same "real data, LLM never guesses the hard numbers" pattern as materials pricing.
  Not started - two open design decisions (advisory vs. hard gate; runs at input time vs. after generation)
  recorded in the file, to confirm with the user before building.
- `future-plans/concept-layout-controlnet-conditioning.md` — make the friend-hosted AI "Concept Layout"
  card ACCURATE (not just decorative) by feeding this repo's real computed wall geometry
  (`floor_layout.py`) into the model's ControlNet as the conditioning image, instead of the empty
  rectangle it currently uses - so it traces the real plan instead of inventing one. Then composite our
  own accurate room labels onto the (deliberately text-free) AI output. A TWO-SIDED change (friend's
  Kaggle notebook + this repo), phased; the doc splits notebook-side vs repo-side work explicitly so a
  future session can generate the paste-ready notebook code on request. Not started - needs friend
  coordination; the deterministic system stays the source of truth + only real `.dxf` regardless.

## Git identity

This repo pushes to the **professional** GitHub account (`mhuzaifahahmed`) via the SSH alias remote
`git@github-mhuzaifahahmed:mhuzaifahahmed/interior-gen.git`, with per-repo local identity
`mhuzaifahahmed <mhuzaifah282@gmail.com>`. Never use `git config --global` here. See the machine-wide
`~/.claude/CLAUDE.md` for the full multi-account routing convention.
