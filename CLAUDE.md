# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

B2B AI interior-design tool. A user uploads a room photo; the backend returns **3 visually distinct
redesigns** — **Economical / Mid / Premium** — that preserve the real room structure (walls, windows,
doors, layout, camera angle). Phase 1 (current) is the backend pipeline + a bare-bones localhost frontend
that reliably produces and saves those 3 tiers. Materials-list + city-pricing is **explicitly deferred**
(spec preserved in the plan, §9) — do not build it unless asked.

Full plan: `C:\Users\User\.claude\plans\act-as-senior-technical-purrfect-globe.md`.

Hard constraint: **free tiers only.** Provider setup (see "Provider split" below) reflects a real
mid-build policy change and is not the original design — read it before touching `app/providers/`.

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
- **Image generation**: `app/providers/cloudflare.py` — **Cloudflare Workers AI**,
  `@cf/runwayml/stable-diffusion-v1-5-img2img` (free tier: 10,000 neurons/day, no card). This is
  **SD1.5 img2img**, not Nano Banana's instruction-based editing — structure preservation is controlled by
  a **per-tier `strength`** (`STRENGTH_BY_TIER` in `prompts.py`, not one shared constant). Tuning these
  values is the main lever if outputs drift too far or too little. **Counterintuitive, hard-won lesson**:
  premium is *lower* (0.45) than economical/mid (0.65/0.6), not higher, despite having the biggest material
  change. A real generation showed economical and premium at the same 0.65 - economical preserved structure
  fine, but premium hallucinated an entirely different room (no window, wrong shape), because its luxury
  vocabulary (marble/brass/chandelier) combined with that much freedom let SD1.5 fully reinterpret the scene
  toward a generic "luxury vanity" archetype instead of redecorating the actual input. Premium also carries
  a `structure_reminder` field (empty for the other tiers) that `build_prompt()` inserts right before the
  heaviest material tokens - a repeated structure-preservation anchor placed where the luxury vocabulary's
  pull is strongest. Don't raise premium's strength back up without re-verifying against a real generation.
  - **Alternate image providers**: selectable via `IMAGE_PROVIDER` (`cloudflare` default/free, or
    `openai`) in `get_provider()` (`app/providers/__init__.py`), which injects the chosen image backend
    into `HybridProvider(image_provider=...)` - text (Gemini) is unaffected either way. **OpenAI**
    (`app/providers/openai.py`, `OpenAIImageProvider`) is real and wired in - paid, `gpt-image-2`, not
    `gpt-image-1` which retires Oct 2026. Architecturally different from Cloudflare's SD1.5: it's an
    instruction-following edit model (like Nano Banana), not raw diffusion, so `negative_prompt` is
    folded into the positive prompt text (expected to actually work here, unlike SD1.5) and `strength`
    has no equivalent (accepted for interface compatibility, ignored).
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
    Two abandoned investigations, kept on record: Pixazo (Flux Schnell img2img) - their API gateway
    Cloudflare-bot-blocks server-side requests (403 even with a valid key), unusable for backend
    integration. NVIDIA NIM's Qwen-Image-Edit - `NVIDIA_API_KEY` placeholder exists but no provider code;
    its hosted invocation shape was never confirmed (image-edit models aren't in the standard `/v1/models`
    catalog, likely needs NVCF function-ID-based invocation instead of a friendly model name - the exact
    request shape lives on a JS-rendered `build.nvidia.com` page automated fetching couldn't scrape).
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

## Architecture (big picture)

Upload → FastAPI stores the original + creates a `Project` row (SQLite) → a **background job** runs the
3-tier pipeline → each tier calls the provider's image generation with a distinct prompt → all 4 images
land in object storage → the frontend **polls** `GET /api/projects/{id}` until `status=done` and renders
them.

Three deliberate seams keep the free/solo build swappable — respect them when adding code:

1. **Provider seam** (`app/providers/`): all model calls go through the `Provider` interface
   (`generate_image`, `describe_room`, `generate_tier_notes`). See "Provider split" above for the current
   hybrid wiring. Swapping a model/vendor should only ever mean touching this directory.
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

   **v6 prompt format: natural-language edit instructions, not SD1.5 keyword soup.** `build_prompt()`
   composes `TIER_SPECS` into a paragraph of imperatives ("Paint the walls with...", "Do not include...")
   because the active image backend is now OpenAI `gpt-image-1` (`IMAGE_PROVIDER=openai`) — an
   instruction-following editor (like Nano Banana), not raw diffusion. This replaced the old
   comma-separated descriptor format after a **real failure**: that format read to an instruction model
   as a generation spec for a target image, not an edit instruction for the input photo, so a real 3-tier
   generation came back with Premium/Mid as entirely different rooms (only Economical, with the weakest
   vocabulary, loosely resembled the input) — same "luxury vocabulary overpowers structure" failure shape
   as SD1.5, worse here since there's no strength dial to compensate.

   **Accepted, deliberate tradeoff**: this format is used for ALL providers now, including the free
   Cloudflare/SD1.5 fallback (declined building a second per-provider renderer to keep one code path) —
   which reopens two SD1.5-specific dangers the old format was built to avoid: CLIP's 77-token truncation
   (these paragraphs are well over that; trailing content silently drops on Cloudflare, not OpenAI) and
   diffusion's unreliable negation handling (partially mitigated - `build_negative_prompt()` /
   `NEGATIVE_ADDITIONS` / `STRENGTH_BY_TIER` are all still kept and still used for Cloudflare's dedicated
   `negative_prompt` param; the tier-exclusion sentence in `build_prompt()` is *additional* positive-prompt
   text, not a replacement). If Cloudflare quality matters again, the fix is a second keyword-style
   renderer selected per-provider, not implemented.

   `tier_note` and `user_notes` (optional 3rd/4th args to `build_prompt()`) are inserted right after the
   structural lock, ahead of the tier's own generic content, same priority reasoning as before just in
   instruction-sentence form now. `structure_reminder` (a premium-only SD1.5-era patch) is unused by v6 —
   the always-present structural lock covers what it used to patch — but the field stays in `TIER_SPECS`
   rather than being deleted.

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

Async: FastAPI `BackgroundTasks` + client polling (no real queue yet — hardening-phase item). Each
Project's `meta_json` carries `PROMPT_VERSION` so outputs are reproducible/defensible.

Layout: `app/{main,config,db,models,schemas}.py`, `app/pipeline/` (prompts + orchestration),
`app/providers/` (base + gemini + cloudflare + hybrid), `app/storage/` (base + local + s3), `static/`
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
not the UI chrome. The progress screen cycles (with a fade transition, `.is-changing` in `style.css`)
through `PROGRESS_MESSAGES` in `app.js` every 4.5s. **Revised decision, worth knowing if this reads
inconsistently elsewhere in this doc's git history**: an earlier version kept these deliberately vague/
generic on principle (the backend doesn't report per-tier progress, so specific claims like "now
designing Premium..." would be fabricated telemetry). That was explicitly overturned - messages are now
specific and varied ("Generating your Premium image…", "Fetching real-time price data…") including some
that aren't literally true (no live pricing/internet lookups happen), because a ~1 minute wait feels more
engaging with plausible specific-sounding status than with honest-but-vague filler. If revisiting this
tension later, that's the tradeoff being made, not an oversight. The landing screen carries a nav bar
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

All provider calls in tests are **mocked** — no real Gemini or Cloudflare network calls in the test suite
(quotas are limited/finite and must never be burned by CI). Pattern: monkeypatch the module-level client
call (see `tests/test_cloudflare_provider.py`, `tests/test_pipeline.py` for the FakeProvider pattern).
Prompt tests assert the three tier specs are mutually distinct and each carries the preserve-structure
instruction.

## Known limitations to keep in mind (not bugs to "fix" silently)

- SD1.5 img2img (Cloudflare fallback only, not the active backend) preserves structure via the `strength`
  parameter, not a hard geometric lock — expect more drift than gpt-image-1's instruction-following if
  that path is ever used. ControlNet+depth is the eventual hardening upgrade if this proves insufficient.
  Current prompt version is `v7` (`PROMPT_VERSION` in `prompts.py`) - see the Tier/prompt seam section
  above for the full v6/v7 history.
- Free-tier caps (Cloudflare: 10,000 neurons/day; Gemini text: separate free quota) and localhost-only
  deployment are intentional for Phase 1.
- `GEMINI_IMAGE_MODEL` env var / Gemini image path is dormant, not deleted — kept for a possible future
  billing-enabled fallback.

## Git identity

This repo pushes to the **professional** GitHub account (`mhuzaifahahmed`) via the SSH alias remote
`git@github-mhuzaifahahmed:mhuzaifahahmed/interior-gen.git`, with per-repo local identity
`mhuzaifahahmed <mhuzaifah282@gmail.com>`. Never use `git config --global` here. See the machine-wide
`~/.claude/CLAUDE.md` for the full multi-account routing convention.
