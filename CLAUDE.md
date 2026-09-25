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

## Deployment: Vercel (frontend) + Render (backend) — two separate deploys, wired by a rewrite

The production site is **not one deploy** — it's two, stitched together at the HTTP level, and this has
caused several real incidents (below) worth understanding before touching either side.

- **Vercel** hosts the static frontend only (`static/`) — no Python runs there. `vercel.json` at the repo
  root does two things:
  ```json
  { "rewrites": [
      { "source": "/", "destination": "/static/index.html" },
      { "source": "/api/:path*", "destination": "https://interior-gen.onrender.com/api/:path*" }
  ]}
  ```
  `/` serves the real HTML/JS/CSS; any `/api/*` call from `app.js` is transparently forwarded to Render —
  the browser only ever talks to the Vercel domain, the proxy is invisible client-side. **Nothing else is
  rewritten** — routes like `/admin` are NOT proxied, so `/admin` on the Vercel domain just 404s (Vercel
  has no idea that route exists; it's only real on Render itself).
- **Render** (`https://interior-gen.onrender.com`) runs the actual FastAPI app (`app/main.py`), talks to
  the real Postgres (Neon) database, and makes every paid/self-hosted call (OpenAI, Kaggle, Gemini,
  SerpApi, S3). It also happens to serve `static/` and `/admin` itself — but when reached *through*
  Vercel, only `/api/*` traffic actually lands there.
- **`/admin` therefore only ever works at `interior-gen.onrender.com/admin` directly** — not on the
  Vercel domain. This has caused real user confusion (mistaking the Vercel homepage's nav dropdown for
  the actual admin panel) — always point at the Render URL for `/admin`.
- **Two separate environment-variable stores, real incidents from this**: Render's env vars are
  completely independent of local `.env` — this has caused at least three real production breakages
  found only by reading Render's live logs together with the user: `CLERK_SECRET_KEY` was never set on
  Render at all (`httpx.LocalProtocolError: Illegal header value b'Bearer '` on every authenticated call
  — fixed by pasting the local value into Render's dashboard); `ADMIN_USER_IDS` was likewise never set on
  Render (`/admin` returned "You don't have admin access" for the real admin's own account — same fix);
  and Postgres never got a schema migration that only ran conditionally for SQLite (see
  `_migrate_missing_columns()` in `app/db.py` — fixed by generalizing it to run on both engines). **Rule
  of thumb: any new secret/setting added to `.env.example` must be separately added to Render's dashboard
  before it does anything in production — adding it locally is not enough, and there is no automatic
  sync.** Build-a-House's own `KAGGLE_HOUSE_API_URL`/`KAGGLE_AUTOCAD_API_URL` hit this exact same gap —
  set locally, never mirrored to Render, silently leaving `floor_plan_status="not_configured"` (a
  deliberately silent state — see "Build a House feature" below) with zero visible error.
- **Auth survives the cross-domain split because it's Bearer-token-based, not cookie-based** — see
  "Authentication (Clerk)" below for why that migration happened; the short version is that a Bearer
  `Authorization` header isn't subject to `SameSite`/third-party-cookie rules, so it survives the
  Vercel→Render hop with zero special handling, unlike the old session-cookie system.
- **`FRONTEND_ORIGIN`** (an env var, set on **Render**, not Vercel) is the CORS complement to the rewrite
  — it enables CORS for exactly the Vercel domain, for any request that doesn't go through the Vercel
  proxy. Leave blank for local dev (same-origin, no CORS needed).

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

## Room-photo validation gate (2026-09-21)

Real, user-reported bug: uploading an image that wasn't actually a photo of a room (e.g. a flat
graphic/logo) still went straight through the full pipeline - `describe_room()` correctly recognized it
as a non-photographic graphic in its own output text, but nothing acted on that signal, so
`gpt-image-1`/Kaggle got handed an image with no real walls/floor/furniture to preserve and just
hallucinated 3 unrelated generic bedroom photos per tier instead of editing the input.

**Folded into `describe_room()`'s existing single Gemini call, not a separate validation call** - an
earlier version of this fix added a dedicated `Provider.validate_room_photo()` method/call; replaced
same-day with a simpler one-call design per explicit user request.

- **`UNWORKABLE_IMAGE_MARKER`** (`app/providers/gemini.py`, currently `"UNWORKABLE_IMAGE"`) - a single
  leading instruction is prepended to `describe_room()`'s existing prompt: if Gemini decides the image
  isn't something it can work with (not an actual photographed room interior - the prompt gives
  illustrative examples like drawing/logo/diagram/screenshot/document, but is not limited to those),
  respond with EXACTLY this marker and nothing else, instead of a description.
- **`run_pipeline()`** (`app/pipeline/generate.py`) checks `describe_room()`'s return value for the marker
  (substring check, right after the existing best-effort `describe_room` try/except, BEFORE
  `project.room_description` is ever set) - a match rejects the project (`status="failed"`) before
  `generate_tier_notes`/any paid image call runs. **Fail-open by construction, no separate error handling
  needed**: `describe_room()` raising already degrades `room_description` to `None` (its pre-existing
  best-effort contract, unchanged), and `None` never matches the marker check - so a transient Gemini
  failure behaves exactly as it did before this gate existed, same as a genuinely valid room photo.
- **Deliberately content-agnostic, per explicit user instruction**: the internal Gemini prompt may name
  illustrative example categories (that's just classification guidance), but the USER-FACING rejection
  message is a fixed, generic string - `"We couldn't detect a room in this photo. Please upload a clear
  photo of an actual room and try again."` - built without any reference to what the image actually
  contained, and the marker text itself is never written to `project.room_description` (the field the
  frontend renders verbatim under the results header - see the screenshot that reported this bug, where
  Gemini's own description text was shown directly to the user).
- Cached on a sha256 of the image bytes via the existing `describe_room` cache key (`analysis_cache`) -
  unchanged from before this feature, since it's the same call, not a new one.
- Scoped to Room Redesign only (not Build a House - that feature already has its own optional-photo/
  best-effort `analyze_plot` handling, a different shape of problem).

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
- **Room-redesign model-status note (2026-09), `GET /api/room-model-status` + `static/app.js`'s
  `applyRoomModelStatusNote()`** - shown on the Room Redesign tab, above the Generate button, warning the
  user upfront that a generation will actually run on OpenAI instead of "Our Model" if the Kaggle session
  is down, per the user's explicit request ("show this once the kaggle notebook tells us it gives the
  error of notebook being shut down" - i.e. reactive, not proactive). **First version was wrong, fixed the
  same day**: a `check_connection()` helper pinged the tunnel's bare root and reported "connected" on any
  HTTP response - a real, reported bug, since this came back `connected: false` while the notebook was
  genuinely up and generating real images (a plain `GET /` doesn't reliably reflect whether the model
  itself is usable - server load/routing/tunnel quirks unrelated to actual generation capability).
  Replaced with **real, tracked status** instead of a synthetic probe: `app/providers/kaggle.py`'s
  `_mark_room_kaggle_status(bool)`/`is_room_kaggle_connected()` (module-level state, thread-safe via a
  lock) are updated directly from `generate_image()`/`generate_images_batch()` - `True` on a real success,
  `False` only when the failure is classified as "session offline" by `session_errors.classify_kaggle_failure()`
  (the exact same classification `KaggleSessionUnavailableError` already uses elsewhere - see above), never
  for an ordinary/unrelated error (a plain 500, an unexpected response shape) misleadingly blaming the
  session for a different bug. Starts optimistic (`True`) so a fresh process with no traffic yet doesn't
  show a false warning. `/api/room-model-status` returns `{"configured", "connected"}` -
  `configured=False` whenever `settings.image_provider != "kaggle"` (the frontend treats that as "don't
  show this note at all", not "Kaggle is offline"). Frontend note uses this project's standard GSAP
  entrance convention (guarded no-GSAP/reduced-motion fallback, `gsap.set()`+`.to()`, never `.from()`+
  stagger) and is hidden entirely whenever the session is actually connected.
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
- **v17 (2026-09-04): room labels shrink and/or wrap onto two lines instead of overflowing a narrow
  room.** Real user report: "the text is cluttered" - `_draw_room_label()` (`blueprint_svg.py`) previously
  rendered every room name at one fixed font size (13px) regardless of the room's own pixel width, so a
  long name (e.g. "MASTER BATHROOM") on a narrow room visibly overflowed past that room's own walls into
  whatever was drawn next to it.
  - **Deliberately NOT a fixed real-world "X feet" threshold**, even though that's how the request was
    framed - measured directly (see below) and confirmed the same real room width needs shrinking on one
    plot but not another, because whether a label overflows depends on the room's rendered PIXEL width,
    which depends on the WHOLE PLOT's scale (`TARGET_PLOT_LONGEST_SIDE_PX / longest side`), not the room's
    real feet alone. Same lesson this project already learned once for the garage furniture-icon gate
    (`_GARAGE_MIN_BOX_W`/`_H`, see v16 above). Measured real-world equivalents across representative plot
    scales for context: on a 40ft-plot (scale ~19.5px/ft), shrinking starts around a 6-8ft room width; on a
    100ft-plot (~7.8px/ft), around 12-15ft; on a 200ft-plot (~3.9px/ft), around 20-30ft - roughly
    proportional to plot size, exactly as the pixel-based reasoning predicts, and exactly why a single fixed
    feet number would either over-shrink small plots or under-shrink large ones.
  - **`_fit_room_name(draw, name, max_width_px, base_font)`** (new): tries the label's normal 13px size
    first; if the name is too wide for the room's own pixel box (measured via the SAME `draw.textbbox()`
    call that will actually render it, not an estimate), shrinks 1px at a time down to
    `_NAME_MIN_FONT_SIZE` (9). If it still doesn't fit even at the minimum size, wraps onto two lines by
    splitting at whichever space keeps the two halves' character counts closest to balanced (e.g. "MASTER
    BATHROOM" -> "MASTER" / "BATHROOM"). A single-word name has no space to split on and is left as one
    line at the minimum size - rare in practice since real room names are short, and far less clutter than
    the original single-fixed-size behavior regardless.
  - **`_draw_room_label()`** rewritten to call this, draw however many lines came back, and skip the
    area-text line entirely (not cram it in) when a wrapped 2-line name would leave no real room for it -
    same "no label at all is better than a cramped one" philosophy this function already had for
    too-small rooms.
  - **Verification**: re-rendered the exact suite-pairing room program from earlier in this session (a
    100x100ft plot with narrow ~8.7ft-wide Master/2nd bathrooms) and visually confirmed "MASTER BATHROOM"/
    "BATHROOM 2" now wrap cleanly onto two lines at a smaller size, fully inside their own room's walls,
    no more spillover into "BEDROOM 2"'s text. New tests in `tests/test_blueprint_svg.py`
    (`_fit_room_name()` unit tests for the fits-already/shrinks/wraps/single-word-can't-wrap cases, plus an
    end-to-end test reproducing the exact reported room mix and asserting the rendered label width never
    exceeds the room's own pixel box). 482/482 passing (5 new).
- **v18 (2026-09-04): Build a House's Plot Parameters dropdowns (Unit/Floors/Bedrooms/Bathrooms/Facing)
  now use the same "morph" GSAP dropdown as Room Redesign**, per explicit user request ("apply same
  transition in build a house as in room redesign"). These 5 fields were plain native `<select>` elements
  - they predated `setupMorphDropdown()` (built for Room Redesign's Interior Style/Color Palette fields,
  see the gsap-transitions skill's "dropdown menus" convention) and were simply never migrated. Converted
  each to the identical button+listbox markup pattern (a hidden `<input>` keeping the SAME id/name the
  rest of `app.js`/the backend already reads, so no other code needed to change how these values are
  read/submitted) and wired via the SAME `setupMorphDropdown()` factory - no new animation code, purely
  reusing the existing recipe. Unit/Floors/Bedrooms/Bathrooms always have a real default (unlike Interior
  Style/Color Palette, which start blank) - each dropdown's `.setValue()` is called once right after setup
  so its button starts showing that default instead of the generic placeholder. Facing stays genuinely
  optional - its "Not sure - default to South" option has `data-value=""`, and `setValue()`'s existing
  `value || placeholder` fallback already displays that exact placeholder text for a blank value, so no
  new logic was needed to support it doubling as an explicit "clear back to no preference" choice. Facing's
  real option values are capitalized ("North"/"South"/...) rather than lowercase, matching
  `setupMorphDropdown()`'s existing "value IS the display text" assumption (no separate label mechanism
  needed) - safe because `generate_house.py` already lowercases + validates `facing` server-side
  regardless of the exact case it receives. The pending-generation restore path (`static/app.js`, the
  401-login-redirect handoff) was updated from raw `.value = ` assignment to each dropdown's own
  `.setValue()`, so a restored value shows correctly in the button label too, not just in the underlying
  hidden input. `tests/test_house_api.py`'s house facing/dimensions API round-trip tests are unaffected -
  they POST form data directly, never through this HTML.
- **v19 (2026-09-04): download buttons across both tools now actually download instead of opening the
  image/DXF file in the browser.** Real, live-reported bug: every `download-btn`/DXF-link anchor already
  had a `download="filename"` attribute, which SHOULD trigger a save-as - but per MDN, the `download`
  attribute is silently ignored whenever the target URL is cross-origin, and every image/DXF URL this app
  serves is a real AWS S3 URL (see the "Storage seam" section above) - a different origin from the page
  itself. So clicking "download" just navigated to/opened the raw S3 object instead of ever saving it.
  **Fix**: `static/app.js`'s new `triggerDownload(url, filename)` fetches the file itself, wraps the
  response in a `blob:` URL (blob URLs are always same-origin, so `download` works on them
  unconditionally regardless of where the original file lives), clicks a throwaway anchor pointing at
  THAT, then revokes it - falling back to the previous "open in a new tab" behavior only if the fetch
  itself genuinely fails (e.g. a network error), so a real failure still gives the user something rather
  than a dead button. `wireDownloadLink(anchorEl)` reads the `href`/`download` attributes already present
  in the rendered markup and swaps the anchor's click handler to call `triggerDownload()` with
  `preventDefault()` (stopping the native, broken cross-origin navigation) instead of letting the browser
  handle the click natively - applied to all 5 real call sites: Room Redesign's 3 tier images, Build a
  House's exterior render/blueprint PNG/Concept Layout PNG, and the blueprint's `.dxf` AutoCAD export link
  (which had the exact same cross-origin bug, just not yet reported).
- **v21 (2026-09-04): the garage no longer stretches the full depth of the front zone.** Real, direct
  follow-up user report after v16's real-width fix: the garage now had the right WIDTH, but was carved at
  the box's FULL HEIGHT (e.g. an 8.9x35.7ft strip - roughly double a real garage's ~17.7ft/5.4m depth),
  reading as an oddly tall slab "stuck on the side" and wasting the area below its real depth instead of
  giving it to another room.
  - **First attempt, tried and superseded within the same pass**: a simple two-row split (garage + ONE
    "partner" room sharing a shallow front row at the garage's real depth, everything else in a full-width
    row below). This fixed the garage's own shape but overcorrected - stretching the partner room (usually
    Entry, the real architectural pairing for a shallow front-of-house strip) to fill the ENTIRE remaining
    row width ballooned it to an absurd size (a ~900 sq ft foyer in one real test case).
  - **Final fix: a three-region split**, still simple axis-aligned rectangles throughout (`floor_layout.
    _slice_reserving_garage()`): ROW A (shallow, height = garage's real depth) holds the garage plus a
    partner room sized from its OWN already-computed target area (`weight / garage_depth`, not stretched
    to fill leftover space) - the foyer/entry room if one exists, else whichever room is first in line, a
    defensive fallback for a caller with no entry room (not the normal path, since `generate_house.py`
    always injects one alongside any requested garage). COLUMN C (tall, full box height) holds the NEXT
    room in line - typically Living Room, since `_PUBLIC_ORDER_RANKS` already puts it right after garage/
    entry - occupying whatever width remains beside row A, so it gets real prominence instead of that
    space going to waste. ROW B REMAINDER (below row A, same width) holds every other room, sliced
    normally. These three regions tile the box's full area with zero gaps and zero overlaps - required,
    not just tidy: `blueprint_svg.py`'s poché-wall renderer fills the WHOLE plot solid before carving each
    room's interior back out, so any region with no assigned room would render as an unexplained solid
    black block, not a harmless blank gap.
  - **Two-tier fallback for sparse room programs**: the three-region split only fires when there are enough
    rooms to populate every region (garage + partner + a real column-C room + at least one more for row
    B) - fewer falls back to the simpler two-row split (partner fills the whole row), still a real
    improvement over the original bug just without column C's extra polish; fewer still (or a box too
    shallow for a second row at all) falls back further to the original single-column full-height
    carve-out.
  - **A related bug found and fixed while visually verifying this**: with rooms now narrower in some
    layouts (e.g. Entry at 5.9ft wide), the area sub-line under a room's name (e.g. "5.9x17.7 ft (104 sq
    ft)") had NO width-fit check at all - unlike the name above it (see v17) - and ran straight into
    whatever was drawn next to it, visible as "GARAGE 8.9x17.7ft(157sqft)5.9x17.7ft(104sqft) ENTRY"
    overlapping text. Fixed in `blueprint_svg._draw_room_label()`: the area line is now measured against
    the same width budget as the name and simply dropped (not shrunk/wrapped further - it's a purely
    supplementary line, the real dimensions already appear in the plot's own dimension lines) when it
    doesn't fit, matching this function's existing "no label at all beats a cramped one" rule.
  - **Verification**: reproduced the exact reported scenario (a 60x50ft plot, garage+entry+living+dining+
    kitchen front zone) and visually confirmed each stage - garage now a correctly-proportioned 8.9x17.7ft
    box, Entry a modest ~104 sq ft foyer (not the ~900 sq ft regression), Living Room a large, prominent
    room getting the reclaimed space, no overlapping labels. Also rendered and inspected a no-Entry
    fallback case (Living Room becomes the row-A partner, Dining becomes column C) - no gaps, no
    disproportionate rooms. New tests in `tests/test_floor_layout.py` (garage depth no longer equals full
    box height, partner room stays bounded, three-region tiling has zero gaps/overlaps, sparse room
    programs fall back cleanly) and `tests/test_blueprint_svg.py` (area text hidden when it overflows a
    narrow room, still shown when it fits). 488/488 passing (7 new; one pre-existing, already-documented
    SQLite-lock-contention flake under full-suite load reconfirmed unrelated - passes in isolation and on
    a clean full-suite rerun).
- **v22 (2026-09): structured `wants_garage` flag fixes a garage appearing in elevation renders that
  never asked for one.** The friend-hosted Kaggle elevation notebook (`kaggle_notebooks/
  elevation_server.py`, SDXL+ControlNet, the same one behind `HOUSE_IMAGE_PROVIDER=kaggle`/
  `KAGGLE_HOUSE_API_URL`) was including a garage in the rendered exterior even for plots with none
  requested — root cause: its prompt always included generic garage vocabulary with no way to suppress
  it. Fixed the same way the "unrequested Gemini-invented garage" bug was fixed in the deterministic
  layout engine (see the v16 entry above) — a real, deterministic signal instead of leaving it to the
  model's own judgement. `ElevationRequest` gained a `garage: bool | None` field; `_build_prompt()`
  includes `GARAGE_FEATURE` (positive prompt language) when `garage=True` or `NO_GARAGE_NEGATIVE`
  (negative-prompt exclusion) when `garage=False`, `DEFAULT_FEATURES` made garage-neutral either way.
  Threaded through the whole provider seam as `wants_garage: bool | None = None` on
  `generate_house_render()` (`app/providers/base.py`/`hybrid.py`/`openai.py`/`modal_provider.py`/
  `gemini.py` — only `kaggle.py`'s implementation actually uses it, sent as `payload["garage"]` when not
  `None`; every other implementation accepts-and-ignores it for interface parity, same pattern as
  `preferred_backend`/`facing` elsewhere in this seam). `app/pipeline/generate_house.py`'s render step
  computes `wants_garage=mentions_garage(prompt or "")` (reusing `house_requirements.py`'s existing
  garage-detection regex — the same deterministic parser that already drives the layout engine's own
  garage injection) and passes it straight through.
  **Two separate notebook-side bugs fixed alongside this, unrelated to the garage logic itself**: (1)
  the tunnel URL wasn't reliably printing into the Kaggle cell's visible output — traced to the print
  happening on a background thread, and Jupyter/ipykernel doesn't reliably route prints from non-main
  threads into cell output; moved to the main thread with explicit `flush=True` and a loud banner. (2)
  `uvicorn.run(app, host="0.0.0.0", port=8000)` conflicted with Kaggle's already-running event loop even
  with `nest_asyncio` patched in — replaced with `uvicorn.Config` + `uvicorn.Server` +
  `asyncio.get_event_loop().run_until_complete(server.serve())`, the user's own live-tested fix.
- **v23 (2026-09-18): exterior color palette, closing a real gap - Build a House previously had NO color
  input at all.** Reuses Room Redesign's own `COLOR_PROFILE`/`COLOR_PALETTES` vocabulary
  (`app/pipeline/prompts.py` - "Neutral"/"Earthy"/"Warm"/"Cool"/"Monochrome"/"Terracotta"/"Sage"/
  "Black & White") rather than inventing a second, house-only palette system - one shared source of
  truth for what each palette name means. A new "Exterior color palette (optional)" morph dropdown sits
  in the Plot Parameters panel (`static/index.html`, between the Floors/Bedrooms/Bathrooms row and the
  Facing dropdown), submitted as `color_palette` Form data only when the user actually picks one
  (mirrors Facing's "optional, omitted when blank" treatment) - unlike Room Redesign's own Color Palette
  field, which is required and hard-validated (400 on an unrecognized value). Here it's deliberately
  soft: `create_house_project` (`app/main.py`) degrades any value not in `COLOR_PALETTES` to `None`
  rather than rejecting the request - a cosmetic input should never block a generation. Persisted in
  `HouseProject.house_inputs_json` (no new DB column - reuses the existing `house_inputs` dict alongside
  floor_count/bedrooms/bathrooms/extras/facing) and exposed via `HouseProjectStatusResponse.house_inputs`.
  **Frontend** (`static/app.js`): `houseColorPaletteDropdown` (`setupMorphDropdown`, same recipe as every
  other house-tab dropdown) reads/writes `#house-color-palette`; the submit handler only appends
  `color_palette` to the upload `FormData` when non-empty; the 401-login-redirect pending-generation
  handoff (`savePendingGeneration`/its restore path) carries `colorPalette` through. `resetToHouseUpload()`
  deliberately does NOT reset this dropdown back to blank, matching the existing (also-unreset)
  Unit/Floors/Bedrooms/Bathrooms/Facing dropdowns' behavior. None of this v23 frontend/persistence layer
  changed in v15 below - only HOW the color reaches the render model changed.
- **v15/v24 (2026-09-18, later the same day): color made a STRUCTURED signal for the Kaggle elevation
  model, not embedded prompt text - v23's mechanism for the TEXT-TO-IMAGE path was too weak.** Real
  user report: the palette wasn't visibly changing the exterior render. Root cause: v23's
  `build_house_elevation_prompt()` appended the palette's color words as a TRAILING TEXT FRAGMENT
  (`"Exterior color palette: ..."`) onto the minimal prompt the app sends to the Kaggle notebook
  (`kaggle_notebooks/elevation_server.py`) - but that notebook wraps whatever text it receives inside its
  OWN prompt scaffolding, which hardcodes competing color/material vocabulary ("smooth concrete, dark
  textured stone, wood textures") with no dedicated slot for an appended clause, so the chosen palette
  could be drowned out. **User's explicit instruction: make it work exactly like Room Redesign, and the
  notebook code changes too.** Room Redesign's actual mechanism (confirmed by re-tracing the whole flow):
  the app injects `COLOR_PROFILE[palette]`'s color words into a DEDICATED, never-cut priority slot inside
  `build_kaggle_prompt()` (`app/pipeline/prompts.py`) - the room notebook does no color logic of its own,
  it just consumes the app-composed string. The elevation notebook is architecturally different (it owns
  its own scaffolding and the app deliberately sends only minimal structured inputs like `floors`/
  `garage`), so the faithful mirror isn't more embedded text - it's a structured `color` field sent the
  same way `floors`/`garage` already are, with the NOTEBOOK weaving it into a dedicated, front-loaded
  position in ITS OWN prompt (the real equivalent of Room Redesign's "dedicated priority slot").
  - **`app/pipeline/house_prompts.py`**: `build_house_elevation_prompt()` reverted to take no
    `color_palette` param at all (back to just `prompt`) - color no longer rides in this text.
    `_color_palette_text()` renamed to the now-public **`color_palette_words(palette)`** (same
    `COLOR_PROFILE.get()` body, `None` for blank/unrecognized) since `generate_house.py`'s render step now
    calls it directly to resolve the structured kwarg. `build_house_prompt()` (the OpenAI/Modal EDIT-backend
    path) is **UNCHANGED** - it still inlines `color_palette_words()` into its long edit paragraph exactly
    as v23 built it, since those backends ignore the new structured `color` kwarg entirely and the inline
    sentence is still the only channel they read.
  - **Provider seam**: `Provider.generate_house_render()` gained a `color: str | None = None` param across
    `base.py`/`hybrid.py`/`kaggle.py`/`openai.py`/`gemini.py`/`modal_provider.py` - same "accepted by all,
    honored only by Kaggle" treatment already established for `wants_garage`. `kaggle.py`'s payload dict
    adds `payload["color"] = color` only when truthy (omitted otherwise, so an un-updated notebook keeps
    its old behavior - same convention as `garage`).
  - **`app/pipeline/generate_house.py`**'s render step resolves `color_words = color_palette_words(color_palette)`
    ONCE and passes it as `provider.generate_house_render(..., color=color_words, ...)` on the single call
    site (both backend branches share it; EDIT backends just ignore the kwarg). `HOUSE_PROMPT_VERSION`
    bumped `v14` → `v15`.
  - **`kaggle_notebooks/elevation_server.py`** (the two-sided half - pasted into the live Kaggle notebook
    and redeployed): `ElevationRequest` gained `color: str | None = None`; `_run_job` extracts it and
    passes it into `_build_prompt(theme, plot_width, floors, features, garage, color)`; `_build_prompt`
    inserts a `color_frag` (`"exterior color palette of {color}, facade finished in {color}, "`) right
    after the theme and BEFORE the free-text features - a dedicated, front-loaded slot, the real
    notebook-side equivalent of Room Redesign's never-cut priority slot. When `color` is given, the
    trailing hardcoded material clause also softens `"dark textured stone"` → `"textured stone"` so a
    light palette isn't fought by a baked-in "dark". No color terms added to the negative prompt (parity
    with Room Redesign, whose `build_kaggle_negative_prompt` carries no color either). Contract comment at
    the top of the file updated to document the new `color` field.
  - **Tests**: `tests/test_house_prompts.py` (elevation prompt never contains color text anymore;
    `color_palette_words()` unit tests), `tests/test_house_pipeline.py` (`FakeProvider` gained a
    `render_colors` list capturing the structured kwarg per call; asserts color reaches
    `generate_house_render` as a kwarg - never inside the elevation prompt text - and is `None` when no
    palette was chosen; the edit-path test still asserts inline color text, unchanged), `tests/
    test_house_api.py`/`test_hybrid_provider.py` (fake `generate_house_render` signatures updated to
    accept the new `color` kwarg), `tests/test_kaggle_provider.py` (new payload test mirroring the
    existing `wants_garage` one - `payload["color"]` present when given, omitted when `None`). 616/616
    passing (full suite).
- **v16/v25 (2026-09-18, later the same day): "Architectural style" added, same structured-field design as
  color - explicit user request: "i want style from room redesign too." Label chosen after asking the user
  directly (`AskUserQuestion`): **"Architectural style"**, not "Interior style" (Room Redesign's own
  wording) - this drives an exterior house render, so "Interior style" would be a misnomer. Kept
  **optional** (matching color_palette, not Room Redesign's required field) per the same question.
  - **NOT a reuse of `app/pipeline/prompts.py`'s `STYLE_PROFILES`, unlike color which reused
    `COLOR_PROFILE` verbatim.** That dict is interior furniture/decor language ("sleek furniture", "cozy
    textiles", "open shelving") - sending it to an EXTERIOR elevation model would actively mislead it.
    `app/pipeline/house_prompts.py` gained its own **`HOUSE_STYLE_PROFILES`** - the same 9
    `STYLE_OPTIONS` names, mapped to facade/massing/roofline/cladding language instead (e.g.
    `"Mediterranean": "Mediterranean villa architecture, stucco plaster walls, terracotta clay tile
    pitched roof, arched windows and doorways, wrought-iron balconies"`, `"Industrial": "industrial
    architecture, exposed concrete and brick facade, steel-framed windows, warehouse-inspired massing,
    utilitarian roofline"`) - deliberately **colorless** (no color words in any descriptor - color stays
    its own field/`COLOR_PROFILE`, never mixed in; `tests/test_house_prompts.py`'s
    `test_house_style_profiles_never_contain_color_words` guards this with a word-boundary regex check,
    not a naive substring check - a naive one false-positived on "warehouse-**inspi`red`**"). Public
    **`house_style_words(style)`** resolver, parallel to `color_palette_words()` (`None`/unrecognized ->
    `None`, never raises).
  - **The elevation notebook (`kaggle_notebooks/elevation_server.py`) needed real de-biasing, not just a
    new field** - a second real gap found while planning this (parallel to color's "dark textured stone"
    problem): it was hardcoded around "Modern Luxury Contemporary" in THREE places that would fight any
    non-modern style: `DEFAULT_THEME`, the modern-biased `DEFAULT_FEATURES` ("glass balcony, black
    aluminum window frames, teak louvers"), and `_floor_rules`'s floor descriptors ("**modern luxury**
    house", "**low profile flat roof**" - a real problem for e.g. a Mediterranean tile-roof house).
    `_floor_rules` was rewritten to be purely about STORY COUNT (e.g. `"strictly double-story G+1 house,
    exactly two vertical levels..."`), with all aesthetic assertions removed - the no-style default render
    is unaffected since `DEFAULT_THEME` still fills the style slot when nothing is chosen.
  - **`app/providers/generate_house_render()` gained a `style: str | None = None` param** across
    `base.py`/`hybrid.py`/`kaggle.py`/`openai.py`/`gemini.py`/`modal_provider.py` - identical "accepted by
    all, honored only by Kaggle" treatment as `color`/`wants_garage`. `kaggle.py`'s payload dict adds
    `payload["style"] = style` only when truthy.
  - **`app/pipeline/generate_house.py`**'s render step resolves `style_words = house_style_words(architectural_style)`
    once and passes it as `provider.generate_house_render(..., style=style_words, ...)` alongside the
    existing `color=color_words`. `build_house_prompt()` (EDIT-backend path) gained an `architectural_style`
    param and inlines a style sentence (`"The house is in a {words} architectural style."`) right after the
    floor-count constraint, BEFORE the color sentence - matching Room Redesign's own "Style, then Palette"
    ordering. `HOUSE_PROMPT_VERSION` bumped `v15` → `v16`.
  - **`kaggle_notebooks/elevation_server.py`** (redeployed by the user): `ElevationRequest` gained `style:
    str | None = None`; `_build_prompt()` gained a `style` param - when given, the app-sent descriptor
    **replaces** the `"{theme} style, "` fragment entirely (not appended alongside it), the dedicated
    front-loaded aesthetic slot; when absent, `theme` fills it exactly as before (backward-compatible).
    `_run_job` also skips the modern-biased `DEFAULT_FEATURES` fallback when a style is chosen but the
    user's own `prompt`/features text is empty (`features = ... or ("" if style else DEFAULT_FEATURES)`) -
    so e.g. a chosen Mediterranean style isn't fought by "glass balcony, black aluminum window frames"
    defaults; the style descriptor alone carries the look. A real edge case this surfaced and fixed: an
    empty-features + real-style combination previously left a dangling `", ."` right before "Direct
    eye-level..." in the assembled prompt - fixed by joining style/color/features into one `descriptor`
    string and `.rstrip(", ")` before appending the period.
  - **`app/main.py::create_house_project`** gained `architectural_style: str | None = Form(None)`,
    soft-degraded (`if architectural_style not in STYLE_OPTIONS: architectural_style = None` - optional, no
    400, same as `color_palette`), added to `house_inputs` (no new DB column), threaded to
    `run_house_pipeline`.
  - **Frontend**: `static/index.html` gained an "Architectural style (optional)" morph dropdown (the same
    9 style names, "No preference" default) positioned before the color-palette dropdown - matching Room
    Redesign's own Style-then-Palette ordering. `static/app.js`'s `houseArchStyleDropdown` follows the
    exact same wiring pattern as `houseColorPaletteDropdown` (submit only when non-empty, carried through
    the 401-login pending-generation save/restore as `architecturalStyle`, left unreset by
    `resetToHouseUpload()`).
  - **Tests**: `tests/test_house_prompts.py` (`house_style_words()` unit tests, colorless-descriptor
    guard, `build_house_prompt` style-sentence present/absent/ignored-when-unrecognized, style-before-color
    ordering, elevation prompt never contains style text), `tests/test_house_pipeline.py` (`FakeProvider`
    gained a `render_styles` capture list; asserts the resolved descriptor reaches
    `generate_house_render` as the structured `style=` kwarg - never inside the elevation prompt text -
    and is `None` when no style chosen; edit-path test asserts inline style text), `tests/test_house_api.py`
    (persists in `house_inputs`, degrades silently on an unrecognized value, existing exact-dict
    `house_inputs` assertions extended with the new `architectural_style` key), `tests/test_hybrid_provider.py`
    (fake signatures updated), `tests/test_kaggle_provider.py` (new payload test mirroring the `color` one -
    `payload["style"]` present when given, omitted when `None`). 630/630 passing (full suite).
- **v17 (2026-09-19): a real front entrance, drawn on the exact facing edge, across all three
  renderers - plus a hard rule that the bed can never block a door.** Real user report on a live
  screenshot: no main house entrance existed at all (only interior room-to-room doors), furniture
  (a bed/sofa) could land directly in front of a door, and the chosen N/S/E/W facing had no visible
  proof it was honored. Root cause confirmed by reading the code: `layout_floor()` already applies
  `facing` as a coordinate transform producing a real, deterministic front edge (north→`y=0`,
  south→`y=width`, east→`x=length`, west→`x=0`) - but that value was never passed to any of the three
  renderers, only to placement.
  - **`app/pipeline/blueprint_svg.py`'s new `_front_door_opening(rects, length, width, facing)`** picks
    the exterior edge matching `facing` and returns a door-opening edge-dict (same shape `_shared_edge()`
    returns) on whichever real room touches it - preferring a foyer/entry room, else any public-zone
    room, else the largest room on that edge. `render_floor_blueprint()` gained a `facing=None` param;
    a new `_draw_front_door()` cuts a real opening through the EXTERIOR poché band (not an interior
    partition) with the same leaf+arc symbol plus an "ENTRANCE" text mark - the literal visible proof
    facing was honored. `_room_door_walls(rect, door_edges)` computes which of a room's 4 walls
    (top/bottom/left/right) carry a real door (interior, post-suppression, plus the front door) -
    threaded into the furniture pass.
  - **Bed placement gained a HARD rule** (`_furnish_bedroom`): the bed must never anchor to a wall
    carrying a door. Bottom-anchored by default (unchanged geometry); if the bottom wall has a door, the
    WHOLE bed (pillows/blanket/nightstand/wardrobe) flips to the top wall via a straight vertical mirror,
    as long as top is itself door-free. If both bottom AND top have doors (a room with 3+ doors), falls
    back to the bottom anchor anyway - drawing no bed at all would be worse. Left/right-wall doors are a
    stated, accepted simplification (the bed's footprint is already padded off those walls).
  - **Every other wall-anchored fixture gets a practical keep-clear skip**, not per-shape relocation:
    `_WALL_ANCHORED_FURNITURE_WALLS` records the REAL walls each `_furnish_*` function's own geometry
    touches (kitchen: top+right; living: top+left; study/closet: top; laundry: top+left) - that room's
    whole furniture group is skipped when ANY of its own walls has a door. Bathroom/dining/garage/
    staircase are unaffected (already spread across corners or centered, not wall-hugging).
  - **`app/pipeline/blueprint_dxf.py`** and **`app/pipeline/conditioning_image.py`** both gained the same
    `facing=None` param, reusing `_front_door_opening()` directly (imported from `blueprint_svg.py`) so
    all three renderers - the PNG blueprint, the real `.dxf` AutoCAD export, and the AI Concept Layout's
    conditioning image - can never disagree about where the entrance is. The DXF cuts a real door gap
    (not a window) on the matching exterior wall segment; the conditioning image draws the plot boundary
    as 4 separate line segments instead of one closed rectangle, with a gap on the matching side, so the
    AI model traces the entrance too. `kaggle_autocad.py`'s existing conditioning-image call site was
    extended to pass `facing` through (it already threaded `facing` into `layout_floor()` for this exact
    reason - see the `v15` facing entry above).
  - **Two real geometry bugs found via visual inspection, not just unit tests** (this project's own
    repeated lesson for blueprint/furniture changes, applied again): rendering all 4 facings and actually
    looking at each PNG showed (1) the door swing arc/leaf, copied from `_draw_door()`'s fixed +x/+y
    convention, drew OUTSIDE the house entirely for south and east facings (that convention is only safe
    for an interior door, where both sides are real rooms) - fixed by making the leaf/arc direction
    genuinely inward-aware in both `_draw_front_door()` and the DXF's new `_draw_front_door_symbol()`
    (a geometric reflection of the original, verified point-for-point, not a re-derived shape); and (2) a
    west-facing entrance still visually overlapped the Living Room's sofa, because the furniture-skip
    check only ever looked at the "top" wall - living room furniture also touches "left" (the side-arm
    rectangle), which the initial fix missed. Both confirmed fixed by re-rendering and re-inspecting
    every facing plus a garage case, a small/tight plot, and a dense multi-suite stress case.
  - Tests: `tests/test_blueprint_svg.py` (front-door edge/facing table, entry-room preference, bed
    flip + fallback, per-type furniture skip, bathroom/dining unaffected - 16 new), `tests/
    test_blueprint_dxf.py` (front-door gap present/moves with facing/valid `doc.audit()` - 3 new),
    `tests/test_conditioning_image.py` (boundary gap present/moves with facing - 2 new),
    `tests/test_house_pipeline.py` (facing reaches both renderers, spy test - 1 new). 650/650 passing
    (full suite).
- **v18 (2026-09-19): master bedrooms are now genuinely bigger, not identically sized to a regular
  bedroom.** Real user request, direct follow-up to v17. `app/pipeline/room_specs.py` gained
  `MASTER_BEDROOM_MIN_MULTIPLIER` (1.35x) and `MASTER_BEDROOM_MAX_MULTIPLIER` (2.4x, applied on top of
  the already-bumped minimum - a real ceiling of 3.24x a standard bedroom's raw minimum, vs a regular
  bedroom's 1.8x), applied by `min_area_for_room()`/`max_area_for_room()` whenever a room's own name
  contains "master" AND it classifies as a bedroom. Deliberately NOT a new `classify_room_category()`
  category - every other consumer of that classifier (zoning, suite pairing, feasibility math, furniture
  dispatch) already treats master and regular bedrooms identically and correctly; a new category would
  risk disturbing all of that for a change that's purely about size. `_is_master_bedroom()` is the one
  new gate, checked only inside the two sizing functions. Flows through `layout_floor()`'s existing
  min/max-area machinery unchanged, so it reaches all three renderers (PNG, DXF, AI conditioning image)
  for free - no renderer-specific code needed. Visually verified (not just unit-tested, this project's
  own standing lesson for sizing/furniture changes): rendered a realistic 45x50ft house and confirmed
  the Master Bedroom (345 sq ft) came out visibly and proportionally larger than a same-weighted regular
  Bedroom 2 (192 sq ft), with correct suite pairing/door placement/bed furniture scaling intact. Tests:
  `tests/test_room_specs.py` (master min/max both exceed a regular bedroom's, still classifies as plain
  "bedroom", a non-bedroom room merely containing "master" in its name is unaffected - 4 new). 654/654
  passing (full suite).

## Subscription plans, quotas, admin panel, and payments

Free/Pro/Studio plan gating is **built and live** (previously tracked as a roadmap-only plan in
`future-plans/subscription-and-access-roadmap.md`, now real code — that file still holds the original
research/decision trail, e.g. why Stripe was ruled out, but the plan/quota system itself is no longer
aspirational). Real payment processing is **not** — see "Payments" below for the honest current state.

### Plans & quotas (`app/plans.py`, `app/models.py::UserPlan`)

- `UserPlan` — one row per Clerk user id, lazily created (`get_or_create_user_plan()`) the first time
  their plan is looked up (there's no signup hook that creates it eagerly outside the webhook — see
  below). Fields: `plan` (`"free"`/`"pro"`/`"studio"`), four rolling-window usage counters
  (`room_kaggle_used`/`room_openai_used`/`house_kaggle_used`/`house_openai_used`), `quota_window_start`,
  `lifetime_generations` (never reset by the window — a true all-time total for the admin panel),
  `email`/`display_name` (client-supplied labels, see `capture_identity()` below).
- **Rolling 30-day window per user** (`QUOTA_WINDOW_DAYS`), anchored to `quota_window_start` — not a
  shared calendar-month cutover. `roll_quota_window_if_needed()` lazily advances it in whole
  `QUOTA_WINDOW_DAYS` increments (not just `window_start = now`) so a long-inactive user doesn't land on
  an artificially extended window.
- **`PLAN_QUOTAS`** — the approved pricing table (`math.inf` for genuinely unlimited buckets), with real
  monthly prices and the cost basis they're derived from:

  | | **Free — PKR 0** | **Pro — PKR 2,499/mo** | **Studio — PKR 6,999/mo** |
  |---|---|---|---|
  | Model access | Kaggle/our model only | Kaggle + OpenAI toggle | Kaggle + OpenAI toggle |
  | Room generations | 5/mo (Kaggle only) | Kaggle 150/mo fair-use; OpenAI 30/mo | Kaggle **unlimited** fair-use; OpenAI 100/mo |
  | House generations | 3/mo (Kaggle only) | Kaggle 60/mo fair-use; OpenAI 10/mo | Kaggle **unlimited** fair-use; OpenAI 40/mo |
  | Free retries/generation | 0 | 2 free | 5 free |
  | Premium features | — | Model toggle, priority queue, full History | + commercial-use license, HD house renders, bulk/export, priority support |
  | Est. worst-case OpenAI cost to us | $0 | ~$4.9/mo | ~$17.6/mo |
  | Margin at max usage | n/a | positive (~$4) | positive (~$7) |

  **Cost basis these numbers are built on**: Kaggle/"our model" ≈ PKR 0 marginal cost (bounded only by
  a 30 GPU-hr/week/account quota — see "Provider split" above). OpenAI room generation (3 tiers,
  low/low fidelity) ≈ **$0.10/generation** (~PKR 28 at ≈280 PKR/USD). OpenAI house render (high/high
  fidelity) ≈ **$0.19/generation** (~PKR 53) — roughly 2× room's cost, which is why House's OpenAI
  quotas are consistently lower than Room's at every plan tier.

  **Studio is NOT unlimited on OpenAI** — only the self-hosted Kaggle bucket is `math.inf`; OpenAI stays
  metered on every plan since it has a real per-image dollar cost regardless of plan (~$0.10/room
  generation, ~$0.19/house render — see "Provider split" above for the input-fidelity cost trap this
  pricing is based on).

  **"Free retries/generation" is built, but narrower in scope than this row's name implies** — see
  "Materials-only pricing retry" further below. It only covers re-running the free-text/pricing lookup
  (`generate_materials()`) for one tier of an already-completed project, NOT regenerating the actual
  (paid) images. A full "retry this exact generation, including images" concept is still deferred (see
  `future-plans/subscription-and-access-roadmap.md`) — don't assume this row means image regeneration.
- **`consume_quota(session, user_id, kind, backend)`** — the single gate every generator endpoint calls
  before accepting a request. Raises `QuotaExceededError` (carries `plan`/`quota_key`/`limit`, with a
  `user_message()` the frontend shows verbatim) rather than a bare 403 — increments happen at REQUEST
  time, not on pipeline completion (every attempt counts, matching the Free tier's "every retry counts"
  spirit). `settings.unlimited_test_user_ids` (comma-separated Clerk ids, dev/testing-only, never set in
  production) bypasses the limit check while still tracking real usage.
- **`resolve_preferred_backend(plan, requested)`** — decides whether a generation request's
  `preferred_model` Form field (`"kaggle"`/`"openai"`) actually overrides the app's configured default.
  Still accepts `plan=None` (the pre-login-trial caller this was originally built for, before anonymous
  generation was removed — see below) as "always allowed to choose", but neither generator endpoint can
  actually reach it with `plan=None` anymore since both now require login first. Free is never honored
  (silently falls back to the app default, verified end-to-end by a test that a Free user's explicit
  `"openai"` request still succeeds against the Kaggle quota — if it had wrongly been honored, it would 403
  immediately since Free's OpenAI allowance is 0); Pro/Studio are honored and quota-checked against
  whichever bucket the chosen backend maps to. Threaded through `HybridProvider._resolve_room_provider()`/
  `_resolve_house_provider()` and only included in pipeline calls when non-`None`
  (`backend_kwargs = {"preferred_backend": ...} if preferred_backend else {}`) — a deliberate scope
  decision so a call with no explicit preference is byte-for-byte identical to before this feature
  existed, avoiding touching ~25+ duck-typed `FakeProvider` test doubles across the test suite.
- **No anonymous generation — removed 2026-09, at the user's explicit request ("no generation before
  logging in").** `create_project`/`create_house_project` originally didn't require login at all (a
  1-free-trial-per-browser cookie, `get_current_user` optional instead of `require_user`) — both now gate
  on `Depends(require_user)` like every other generator-adjacent endpoint, so an anonymous POST 401s
  immediately, before any storage/pipeline cost. The frontend needed **zero changes** to make this work:
  `static/app.js`'s existing 401 handler (`savePendingGeneration()` + redirect to `/static/login.html`,
  originally built for "trial already used") already does exactly the right thing for "never logged in at
  all" too, since both cases were always the same status code. The only real UI follow-up was hiding the
  Kaggle/OpenAI model-choice toggle for anonymous visitors (`applyRoomPlanUI()`/`applyHousePlanUI()`) —
  previously shown to let an anonymous visitor pick a backend for their one trial, now meaningless before
  login and hidden until Pro/Studio. `_owns_project()` stays anonymous-aware (`user_id=None` still matches
  an anonymous caller) purely for **legacy rows** created while the trial feature was live — not for any
  new project, which can no longer have a null `user_id`.
- **7-day Pro trial: explicitly decided AGAINST, not just deferred.** An earlier draft of the roadmap
  floated this; the user has since said no outright — don't build it, don't suggest it as a lever, it's
  off the table.
- **Retry-buffer feature (2/5 free retries before a retry counts as a new generation on Pro/Studio) is
  deferred, not dropped** — needs a "regenerate this exact project" concept (a parent/retry link between
  rows) that doesn't exist in the app at all yet.
- **Build a House has a known, deliberate quota gap**: `create_house_project` is NOT gated on quota at
  all yet. Reason: `settings.house_image_provider` defaults to `"openai"` (no trained Kaggle house model
  exists in production), so gating against the pricing table's assumed Kaggle bucket would 403 every
  Free user's first-ever house generation (their real OpenAI allowance is 0). Hardcoding the Kaggle
  bucket regardless of real backend was considered and rejected — it would let Free users consume real
  paid OpenAI calls at zero counted cost. Resolve once a real Kaggle house model exists, or the pricing
  table is revisited for an OpenAI-only house reality.

### Identity capture (`capture_identity()`) — how the backend ever learns a user's email

Clerk's session JWT carries only the user id (`AuthUser.id`) — never email or name (see "Authentication
(Clerk)" below). `plans.capture_identity(session, user_id, email, display_name)` is a best-effort upsert
of those two fields onto `UserPlan`, called from three places whenever the frontend happens to have them
client-side (from `Clerk.user`): `GET /api/plan`'s optional `email`/`name` query params (fires on every
authenticated page load), and both generator endpoints' `email`/`display_name` Form fields. **Client-
supplied, never re-verified server-side** — same trust posture already used for `display_name` in S3 key
namespacing (`_storage_namespace()`) — fine for an admin *label*, never used for auth/ownership decisions
(that's always the real Clerk id). Only writes when a value is given AND actually different from what's
stored, so a plain page load doesn't churn a write every time.

### Clerk webhook (`POST /api/webhooks/clerk`) — creates the row at signup, not just first login

Without this, a `UserPlan` row (and therefore admin-panel visibility) only appears the moment a user
makes their *first successful authenticated request* — not the instant they finish signing up. A user
who signs up (including via Google) and never actually lands on a logged-in page afterward would never
show up in `/admin` without this webhook. Verified via **Svix** (`svix` Python package,
`Webhook(secret).verify(raw_body, headers)` — requires the exact raw request bytes, not re-serialized
JSON, which is why the endpoint reads `await request.body()` before any parsing). On a valid
`user.created` event, extracts the primary email + a `first_name + last_name` (falling back to
`username`) display name and calls `capture_identity()`. Deliberately **not** gated by
`require_user`/`require_admin` — security is the Svix signature check alone, since Clerk itself is the
caller, not a logged-in user.

**Setup is two manual steps, both outside this repo, and both must be done for this to do anything**:
1. Clerk Dashboard → Webhooks → Add Endpoint → `https://interior-gen.onrender.com/api/webhooks/clerk`,
   subscribed to `user.created`. Clerk shows a "Signing Secret" (`whsec_...`) on creation.
2. Set `CLERK_WEBHOOK_SECRET=whsec_...` on **Render's** environment (not just local `.env` — see the
   "Deployment" section above for why that distinction matters). Until both are done, the endpoint 400s
   every delivery attempt (`clerk_webhook_secret` empty) — silently, with no user-visible symptom other
   than "the new signup isn't in `/admin` yet."

### Admin panel (`/admin`, `static/admin.html`)

- **Auth: a Clerk-user-id allowlist, not a shared password.** `settings.admin_user_ids` (comma-separated
  Clerk ids, `.env`'s `ADMIN_USER_IDS`) → `admin_user_id_set` property (exact copy of the existing
  `unlimited_test_user_ids` pattern) → `require_admin()` (`app/auth.py`, `Depends(require_user)` first,
  then 403 if not in the set). Log in normally with an allowlisted Clerk account and `/admin` just works
  for that account — must be set separately on Render (see "Deployment" above; this has already caused
  one real incident where the admin's own account was locked out because the var was only in local
  `.env`).
- **`GET /api/admin/users`** → one row per `UserPlan`: user id, email, display name, plan, real
  window-rolled usage/remaining (via the same `plan_status()` every user's own `/api/plan` call uses —
  one source of truth, not a separate admin-only calculation), lifetime total, window reset date.
- **`POST /api/admin/users/{id}/plan`** (body `{plan}`) → `set_plan()` — the real, human-operated way a
  paid plan gets applied today, since no payment webhook exists yet (see "Payments" below). Does NOT
  touch usage counters or the quota window — upgrading mid-window doesn't grant/reset an allowance.
- **`POST /api/admin/users/{id}/reset-usage`** → `reset_usage()` — zeroes the four rolling counters and
  restarts the window from now; deliberately does NOT touch `lifetime_generations`.
- `GET /admin` serves `static/admin.html` — a single self-contained page (same Tailwind CDN/theme tokens
  as `index.html`, uses `clerk-init.js` so `authFetch` carries the Bearer token) with a searchable table,
  per-row plan `<select>` + Save, and a Reset-usage button. Not linked from the public nav.
- **Real bug fixed (2026-09-17)**: `renderPlanUsage()`'s `planUsageRow()` (`static/app.js`) had an early-
  return path for the "Unlimited" case (Studio's Kaggle bucket) that never appended the OpenAI-remaining
  note at all — so a Studio user's nav dropdown just said "Unlimited" with **zero mention** that OpenAI
  is still capped at 100/40. Fixed by computing the OpenAI note once, before the branch, and including it
  in both the "Unlimited" and normal-bar return paths.
- **Recurring local-DB test pollution, same root cause each time**: `tests/test_api.py`/
  `test_house_api.py`/`test_plans.py` intentionally hit the real configured `DATABASE_URL` (documented,
  accepted — no DB isolation for these files), which is normally the dev `data/app.db` SQLite file. This
  has repeatedly left dozens-to-hundreds of junk `UserPlan` rows (`jane@example.com`,
  `admin@example.com`, blank-email random-UUID rows) in the local dev DB after running the suite — safe
  to `DELETE FROM userplan WHERE user_id != '<your real Clerk id>'` when this happens again; it only ever
  affects the local file, never production Postgres.

### Frontend: Pricing tab, model toggle, quota display, quota-exceeded popup

- **Standalone "PRICING" tab** (`TAB_ORDER`/`TAB_PANELS`/`TAB_BTNS` in `app.js`, same `switchTab()`
  machinery as Home/Room/House) — the Free/Pro/Studio comparison, reachable any time, not just after
  hitting a limit. Pro/Studio cards show "Current plan" (disabled) instead of "Upgrade" when `GET
  /api/plan` confirms that's the real plan.
- **Room Redesign has a Kaggle/OpenAI toggle** (`#room-model-toggle-wrap`), shown only when the choice is
  real: logged-in Pro/Studio. Hidden entirely for an anonymous visitor (no generation is possible before
  logging in at all — see "No anonymous generation" above) and for logged-in Free (showing a control that
  does nothing would be its own bug, since `resolve_preferred_backend()` already silently ignores their
  choice). **Build a House deliberately has no such toggle** — `HOUSE_IMAGE_PROVIDER` defaults to
  `"openai"` and no trained self-hosted house model exists, so both toggle options would silently do the
  same (paid) thing; a plain static note explains this instead ("Build a House renders currently use
  OpenAI — our self-hosted house model isn't live yet").
- **Nav account-menu usage bars** (`#nav-plan-usage`/`#mobile-plan-usage`, `renderPlanUsage()`) — plan
  name + two progress bars (Room, House) reading the `*_kaggle` bucket, with an inline "+N OpenAI left"
  note (or "OpenAI unlimited" if that bucket is ever raised to infinite, which it currently never is).
  Refreshed on Clerk sign-in/out, on switching to Room/Pricing tabs, and right after a generation is
  submitted.
- **Quota-exceeded popup** (`#quota-modal-overlay`, `openQuotaModal()`/`closeQuotaModal()` in `app.js`) —
  NOT a separate full-page state (an earlier design was; replaced). Opens ON TOP of the upload screen on
  a 403, so the user's already-filled-in form (photo, style notes, etc.) survives underneath. Shows the
  backend's exact `QuotaExceededError.user_message()`, a real "Your quota resets on {date}" box (from
  `GET /api/plan`'s `quota_window_reset_at`), an "Upgrade Now" button (jumps to Pricing) and an "OK, I'll
  wait" button. Same GSAP "morph" open/close recipe as the nav account menu (see "Motion / GSAP
  animation conventions" below), close button excluded from the child stagger for the same stacking-
  context reason documented on the JazzCash modal.

### Payments — Safepay sandbox checkout is now REAL and LIVE (2026-09-17)

**Superseded the "no real processor" state below.** A real Safepay **sandbox** checkout is wired end to
end: clicking "Pay with card" on the JazzCash modal (`#safepay-checkout-btn`, sits ABOVE the manual
JazzCash instructions, which stay as a permanent secondary option per explicit user request) calls
`POST /api/payments/safepay/checkout`, redirects the browser to Safepay's real hosted checkout page, and
a completed payment auto-upgrades the user's plan via a callback endpoint — **no admin panel click
needed**, closing the exact gap the user asked to close ("when the payment is gotten and everything is
verified the user automatically gets upgraded... without me having to access the admin panel"). This is
**sandbox only** — flipping `SAFEPAY_ENVIRONMENT=production` with real production keys (after the
NTN/CNIC Merchant Onboarding Form review) is the only step left for real money; nothing else changes.

- **Real processor research (kept from before, unchanged)**: Stripe ruled out entirely (no Pakistan
  support). Compared Safepay (chosen), PayFast Pakistan, JazzCash (weak as a subscription backbone), and
  PayPro — see the original comparison further below in git history/this file's prior revisions if ever
  revisiting that choice. **Business-registration reality check**: a sandbox account needs ZERO
  documents; only production requires an NTN (free, same-day via FBR's IRIS portal) + CNIC + bank
  account via the Merchant Onboarding Form (~48hr review).
- **Integration shape — REAL, LIVE-VERIFIED against this project's actual sandbox keys on 2026-09-17,
  not just docs.** Safepay's own public integration examples (a GitHub gist, an older docs site) describe
  an `order/v1/init` + hosted-checkout-redirect flow, but their **exact hosted-checkout URL had silently
  changed** — the documented `{base}/components?...&beacon=...` path now 301-redirects to Safepay's
  marketing homepage (`getsafepay.pk`), a dead/retired path. The real, current one was found by fetching
  the checkout SPA's own JS bundle directly (`sandbox.api.getsafepay.com/checkout/static/js/main.*.chunk.js`)
  and reading its actual React Router route table + query-param parsing code — confirmed live (200,
  real "Safepay Checkout" page, no redirect):
  `{base}/checkout/pay?env=sandbox&beacon=<tracker>&order_id=<id>&redirect_url=<url>&cancel_url=<url>`.
  `POST {base}/order/v1/init` (`{"client","amount","currency","environment"}` → `{"data":{"token",
  "state":"TRACKER_STARTED",...}}`) and `GET {base}/order/v1/{tracker}` (same shape back, used as a
  server-to-server "did this really get paid" double-check) were both hit live and confirmed to work
  exactly as documented.
  **Real incident, live-verified fix (2026-09-17, same day): the completion-detection guess was WRONG.**
  A real sandbox test-card payment was run end to end through the actual checkout page — Safepay never
  redirected the browser back afterward (clicking "Close" on their own "Transaction submitted" modal did
  nothing observable), so nothing on our side fired and `/admin` never showed the upgrade. Looked up that
  exact tracker directly via `GET /order/v1/{tracker}` and found the real payment HAD succeeded
  server-side (a fully populated `transaction` object: id/token/fees/net/reference/signature) — but its
  `state` was `"TRACKER_ENDED"`, not any of the guessed `COMPLETE`/`SUCCESS`/`PAID` variants. Had the
  redirect or webhook actually fired under the original code, it would have silently misclassified this
  genuinely successful payment as a FAILURE. Fixed by changing the completion signal entirely:
  `is_completed_order(order_details)` now checks `order_details["transaction"] is not None` instead of
  matching `state` against any string at all — a fresh `TRACKER_STARTED` tracker (right after
  `order/v1/init`, before any payment attempt) has `transaction: null`, so this is a strictly more
  reliable signal than guessing at state-string spelling, and sidesteps the fact that `"TRACKER_ENDED"`
  is itself an ambiguous terminal-state name that a cancelled/abandoned checkout could plausibly also
  reach. `fetch_order_state()` → `fetch_order_details()` (returns the full `data` object, not just
  `state`, since the transaction field is what's now checked). **Separately, the browser not redirecting
  back at all is still unexplained** (could be a Safepay sandbox UI quirk, could be something about how
  `window.open`'s new-tab context handles the eventual `window.location` navigation) — but this no longer
  matters as much as it first appeared to, since the independent server-to-server webhook path doesn't
  depend on the browser at all, and now uses this same corrected completion check (see below).
- **`app/payments.py`** (new, isolated module — mirrors `app/plans.py`'s own "a processor only ever needs
  to call `set_plan()`" isolation): `create_checkout_session()` (raises `SafepayError`, not best-effort —
  a broken checkout call means the user literally cannot pay), `verify_callback_signature()`
  (`HMAC-SHA256(tracker, secret_key)`, constant-time compare, exact scheme from Safepay's own public
  examples), `fetch_order_details()` + `is_completed_order()` (see the real incident above for why this
  checks `transaction` presence, not a `state` string).
- **`PaymentIntent` table** (`app/models.py`, brand-new — no migration entry needed, `create_all()`
  handles new tables): one row per checkout attempt, created BEFORE the browser ever reaches Safepay
  (`user_id`/`plan`/`amount_pkr`/`tracker`/`status`) — necessary because Safepay's redirect callback only
  carries its own `tracker`/`order_id`, not our user id/plan; this is the lookup that maps "this tracker
  paid" back to "flip THIS user to THIS plan."
- **`POST /api/payments/safepay/checkout`** (`app/main.py`, `Form: plan`, requires login): creates a
  `PaymentIntent`, calls `payments.create_checkout_session()`, returns `{"checkout_url"}` for the
  frontend to `window.location.href` to. 400 on an invalid plan, 502 wrapping a `SafepayError`.
- **`GET /api/payments/safepay/callback`** (public, no auth — Safepay redirects the real browser here):
  verifies the HMAC signature, then does the server-to-server `fetch_order_details()` double-check BEFORE
  ever calling `set_plan()` — the redirect signature alone only proves the tracker is genuinely Safepay's,
  not that the customer actually finished paying. Idempotent (a replayed/already-non-pending intent
  redirects to `?payment=error`, never double-upgrades). Always ends in a redirect to the frontend
  (`?payment=success|failed|cancelled|invalid|error`), never raw JSON — a real browser lands here
  mid-checkout-flow. Best-effort refreshes the user's S3 `account.json` on success, same convention as
  the admin panel's own plan-change endpoint.
- **`PLAN_PRICES_PKR`** (`app/plans.py`, `{PRO: 2499, STUDIO: 6999}`) — the single source of truth the
  checkout endpoint reads, matching the numbers already hardcoded in the pricing cards/JazzCash modal.
- **Frontend** (`static/app.js`/`index.html`): the Safepay button lives inside the existing JazzCash
  modal, tracked via `jazzcashModalPlan` (which plan the modal is currently showing) so it needs no
  separate plan-picker UI. A `#payment-status-toast` (same visual pattern as the existing cold-start
  toast) shows the outcome when the browser lands back on `/` with a real `?payment=` query param
  (`showPaymentStatusToast()`, end of `app.js` for the same hoisting-safety reason as the resume/restore
  dispatch) — strips the query param via `history.replaceState` so a refresh doesn't re-show it, and on
  success switches to the Pricing tab + refreshes the nav usage bars/pricing cards.
- **Config** (`app/config.py`): `SAFEPAY_CLIENT_ID`/`SAFEPAY_SECRET_KEY`/`SAFEPAY_ENVIRONMENT` (real
  sandbox keys already in this machine's `.env`, renamed from an earlier `SAFEPAY_SANDBOX_PUBLICKEY`/
  `_SECRETKEY` naming to match) and `PUBLIC_BACKEND_URL` (this backend's own real public URL, needed to
  build the `redirect_url` handed to Safepay — separate concern from `FRONTEND_ORIGIN`, which is about
  where the BROWSER eventually lands, not where Safepay calls back to).
- **Tests**: `tests/test_payments.py` (pure unit, httpx monkeypatched, no network — signature
  verification, checkout-session creation, state-completion parsing) and `tests/test_payments_api.py`
  (integration — checkout endpoint creates a pending intent and returns a URL, callback upgrades the plan
  only on a genuinely completed + correctly-signed payment, rejects forged signatures without even
  reaching the status check, is idempotent against a replayed callback; plus the webhook tests described
  below). 604/604 passing (full suite).
- **Manual JazzCash transfer stays a PERMANENT secondary option**, unchanged, per explicit user request —
  shown right below the new "Pay with card" button, not replaced by it.
- **Second confirmation path added same day: a real server-to-server webhook, independent of the
  browser.** Real gap in the redirect-only design above: if the customer closes the tab right after
  paying, the browser never reaches `GET /api/payments/safepay/callback`, and the plan would never
  upgrade even though the payment succeeded. Confirmed live via a real screenshot of this account's own
  Safepay dashboard that a genuine webhook system exists, separate from the checkout flow: **Dashboard →
  Payments 2.0 → Developer → Endpoints**, with its own "Webhook Shared Secret" (distinct from
  `SAFEPAY_SECRET_KEY`, which only verifies the redirect callback's tracker signature) —
  `SAFEPAY_WEBHOOK_SECRET` in `.env`. The user registered `https://interior-gen.onrender.com` as the
  endpoint before a receiver existed at any specific path there; the real receiver is now
  `POST /api/payments/safepay/webhook` — **re-point the dashboard's endpoint URL at that exact path**
  once this ships, the bare domain won't route anywhere real.
  - **What's confirmed vs. not, stated plainly** (0 real webhook deliveries have landed on this account
    yet, since no payment has gone through the full flow with the webhook URL correctly pointed at
    `/api/payments/safepay/webhook`): the SIGNATURE scheme (HMAC-SHA256 of the raw request body using the
    webhook secret) is confirmed from Safepay's own public docs, which reference an
    `X_SFPY_SIGNATURE`-style header — `app/payments.py`'s `WEBHOOK_SIGNATURE_HEADER_CANDIDATES` tries
    several real-world spellings of that header name since the exact one hasn't been observed live yet.
    The PAYLOAD shape uncertainty is mostly sidestepped rather than guessed further: `extract_tracker()`
    (renamed from `extract_tracker_and_state()` — see the real completion-detection incident documented
    above) only ever trusts the payload for the TRACKER id, tried against a couple of plausible shapes;
    the actual completion decision always comes from a fresh `fetch_order_details()`/`is_completed_order()`
    call (the same LIVE-VERIFIED path the redirect callback uses), never from whatever the webhook body
    itself claims. The handler still always logs the full raw payload (`logger.info` in
    `safepay_webhook()`) so the first real delivery's exact shape can be inspected if `extract_tracker()`
    ever needs tightening — but getting that shape wrong no longer risks a misclassified payment the way
    trusting a guessed `state` field did.
  - **Shares its upgrade logic with the redirect callback via `_finalize_payment_intent()`** — whichever
    of the two confirmation paths (browser redirect or server-to-server webhook) arrives first actually
    upgrades the plan; the other finds the `PaymentIntent` already non-`"pending"` and safely no-ops
    (`test_safepay_webhook_and_callback_are_mutually_idempotent` guards this explicitly — a plan must be
    granted exactly once per successful payment, never twice just because two notification channels both
    fired).
  - **No event-type filter was offered when the endpoint was registered** (confirmed by the user directly)
    — Safepay pushes every event type to this one URL, so the handler must tolerate/ignore event shapes
    it doesn't recognize (returns 200 + `{"status":"ignored"}` for those) rather than erroring on them,
    which would trigger pointless retries from Safepay's side.
  - Returns a real 400 ONLY on a bad/missing signature (a genuine security boundary once the secret is
    configured) — every other "couldn't use this payload" case (unparseable JSON, unknown tracker,
    already-processed intent) returns 200 so Safepay doesn't keep retrying a delivery this handler
    already knows it can't act on.

### Self-serve "Cancel plan" (2026-09-17)

Real user request, direct follow-up to the Safepay integration: no way existed for a user to downgrade
themselves back to Free without asking an admin. **Honest scope**: this Safepay integration is a
ONE-TIME payment per upgrade click (`app/payments.py`), not an auto-renewing subscription enrolled with
Safepay — there is no real recurring charge to cancel on their side. "Cancel plan" here means exactly
what the admin panel's plan-change endpoint already does: flip `UserPlan.plan` back to `"free"`
immediately.

- **`POST /api/plan/cancel`** (`app/main.py`, requires login): 400 if already on Free, otherwise
  `set_plan(session, user.id, FREE)` + the same best-effort `account.json` S3 refresh every other
  plan-change path does. Deliberately does NOT touch usage counters/quota window (same as every other
  `set_plan()` call) — a user who's already used more Room generations this window than Free allows just
  can't generate again until the window resets, same outcome as if they'd been on Free all along.
- **Frontend**: a "Cancel plan" text link (`.cancel-plan-btn`) sits below whichever Pro/Studio pricing
  card's CTA button is currently the user's actual plan (`applyPricingUI()` toggles it alongside the
  existing "Current plan" disabled-button logic) — hidden on every other card. Confirms via a plain
  `confirm()` dialog (honest about scope: "takes effect immediately", no mention of a "subscription"
  being cancelled, since there isn't one to cancel) before calling the endpoint, then refreshes both the
  Plans tab and the nav quota bar.
- **Tests**: `tests/test_payments_api.py` gained 3 cases (downgrades to Free, rejects when already Free,
  requires login).

### Materials-only pricing retry (2026-09-17)

Real motivation, directly from the user: a live demo where a Gemini key hiccup left only the Mid tier's
materials priced — Economical/Premium fell back to `"Estimate unavailable"` — and re-running the whole
(paid) image generation just to retry a text/pricing lookup felt wasteful. This lets a user re-run **just**
`generate_materials()` for one tier of an already-completed project, without touching images.

- **`Project.materials_retry_count`** (new column, additive migration in `app/db.py`) — a single counter
  shared across all 3 tiers on one project (not per-tier), so a user can't get 3x the retries by hitting
  each tier once.
- **`app/plans.py`'s `MATERIALS_RETRY_LIMITS`** (`{FREE: 0, PRO: 2, STUDIO: 5}`) — the first real
  instantiation of CLAUDE.md's long-standing "Free retries/generation" pricing-table row (previously
  documented as "planned, not built"), deliberately scoped narrower than the full "regenerate this exact
  project" concept the roadmap doc originally described (which would also re-run the paid image
  generation, and stays deferred — see `future-plans/subscription-and-access-roadmap.md`).
- **`POST /api/projects/{id}/materials/retry`** (`app/main.py`, `Form: tier`, requires login): validates
  the tier, checks `materials_retry_count` against the owner's plan limit (403 once exhausted, with a
  distinct message when the limit is 0 vs. genuinely used up), re-runs `provider.generate_materials()`
  for ONLY that tier using the **exact same pricing-quantity basis** the original run used —
  `room_area_sqft`/`wall_area_sqft` are now persisted into `meta_json` at generation time specifically so
  a retry never needs a fresh (extra-cost) `estimate_room_area()` Gemini vision call, and never silently
  retries with a materially different, unquantified basis either. Falls back to `fallback_materials()` on
  a second failure (same never-empty contract as the original pipeline), still counts as a used retry (an
  attempt is an attempt, matching the quota system's own "every attempt counts" philosophy).
- **`ProjectStatusResponse` gained `materials_retry_used`/`materials_retry_limit`** — 0/0 for an anonymous
  (no-login) project, since retries require an account. `_project_to_response()` now takes a `session`
  param to compute the owner's real plan-based limit (a small, indexed `UserPlan` lookup per call — this
  app's own established "small dataset, no pagination needed" philosophy already accepts this cost
  elsewhere, e.g. the admin panel).
- **Frontend** (`static/app.js`): the materials modal (`openMaterialsModal()`/`renderMaterialsModalBody()`)
  shows a "Retry pricing (N retries left)" button whenever a tier's materials look like they need one
  (`materialsNeedsRetry()` — any item `is_estimate`, or the fallback's exact `"Not available - see search
  links above"` total string) — an "Upgrade to unlock retries" note for Free-plan users (limit 0), a
  "you've used them all" note once exhausted, nothing at all when the tier's pricing looks fine. Works
  identically from both call sites (live results and the History modal) since both now pass through the
  project id, tier key, and the plan-based retry counts.
- **Tests**: 5 new cases in `tests/test_api.py` (retry updates only the requested tier, rejected on Free,
  rejects an invalid tier, enforces the plan limit across repeated calls, 404s for someone else's project).

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
   **Hit AGAIN, same lesson, different call site (2026-09-17)**: `tests/test_payments_api.py`'s
   Safepay callback/webhook "completed payment" tests reach `_finalize_payment_intent()`'s best-effort
   `account.json` write, which calls the real `get_storage()` too - 4 tests were written without mocking
   it, silently writing ~22 tiny `account.json` objects to the real bucket (under random
   `users/user_<uuid4-hex>/` prefixes - easy to tell apart from real accounts, which use Clerk's own
   mixed-case id format, not pure lowercase hex) across several full-suite runs before being caught. Fixed
   the same way (a local `FakeStorage` + `monkeypatch.setattr(main_module, "get_storage", ...)` on every
   affected test). **Left in the bucket, at the user's explicit choice** (not auto-deleted) - harmless,
   isolated, identifiable by the `user_[0-9a-f]{24}` id pattern if ever cleaned up later. Any FUTURE test
   that reaches a plan-upgrade code path (Safepay, admin plan-change, or anything else calling
   `_write_account_json`) needs this same mock - it's not exclusive to the original `create_project`-style
   call sites this note originally warned about.
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

**Not every element should get a GSAP entrance animation — a real one tried and reverted (2026-09-17).**
The pre-auth header pill (see `static/auth-header.js` below) briefly had a "cut to full" clip-path
entrance (starts clipped to a narrow center sliver, expands to full width) and the mobile hamburger icon
briefly morphed (`menu` → `close` glyph via rotate/scale/fade) instead of swapping instantly. Both were
removed the same day per explicit user feedback that they "looked cheap." **A real, separate reason
beyond taste**: the header entrance had a genuine latent failure mode — `gsap.set(pillEl, { opacity: 0,
... })` ran synchronously before the `.to()` reveal, so if GSAP's CDN script was ever slow/blocked (ad
blocker, flaky network), the pill — including the SIGN UP button — would stay invisible **forever**, with
no fallback trigger to un-stick it. This was flagged as the likely explanation for a real "the signup
button isn't visible" bug report. **Lesson: an entrance animation on a critical conversion element
(a signup CTA) needs a hard timeout/fallback if it's going to exist at all** — simplest fix used here was
just not animating it. Both pages' `<script>` tag for GSAP was removed entirely once this was reverted,
since neither page needs it for anything else.

**Shared pre-auth header** (`static/auth-header.js`) — the SINGLE source of truth for `login.html`/
`signup.html`'s nav, replacing two hand-copied `<header>` blocks that had drifted (one was missing the
PRICING link, one had stale hrefs). Injects one merged pill (Logo | Nav links | primary CTA) via
`outerHTML` into `<div id="auth-page-header"></div><script src="/static/auth-header.js"
data-cta-label="..." data-cta-href="..." data-cta-filled="..."></script>`. **Real mobile bug fixed
(2026-09-16)**: the wordmark + gaps + CTA pill could together exceed a narrow phone's width, pushing the
CTA (e.g. login page's filled "SIGN UP" button) partly/fully off-screen with no scroll affordance to
reach it. Fixed by hiding the "Interior‑Gen" wordmark text below the `sm` breakpoint (icon-only), and
`whitespace-nowrap`/`shrink-0` on the CTA so it can never wrap or get squeezed — verified via a real
390px-viewport Playwright screenshot, not just reasoned about (this project's own established lesson:
guess the fix, then actually screenshot it before calling it done).

**Main-site mobile nav sizing** (2026-09-16) — `index.html`'s two header pills (logo pill + tabs/account
pill) used their full desktop `h-16` height/padding/logo size even on a narrow phone, where only the logo
and hamburger are ever visible (everything else is `hidden md:flex`) — looked oversized relative to the
screen width. Shrunk below `md` only (logo `w-8→w-6`, pill `h-16→h-11`, hamburger button `w-10→w-8`,
tighter gaps/padding); desktop is completely untouched (`md:` variants restore every original value) —
confirmed via side-by-side screenshots at 375px and 1280px.

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
