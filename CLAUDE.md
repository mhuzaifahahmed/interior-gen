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
  a **per-tier `strength`** (`STRENGTH_BY_TIER` in `prompts.py`, not one shared constant: a badly damaged
  input photo needs more strength for the economical tier to actually paint over the damage, while
  mid/premium's larger material swaps also need enough headroom). Tuning those per-tier values is the main
  lever if outputs drift too far or too little.
- **Tier notes (room-specific prompt customization)**: `GeminiProvider.generate_tier_notes` analyzes the
  actual uploaded photo once and returns a short, tier-specific instruction per tier (e.g. "repaint over
  visible water stains" for economical) that `build_prompt()` inserts at high priority. This exists because
  a fully generic prompt under-transformed a badly damaged room for the economical tier (looked barely
  renovated) — see `TIER_NOTES_PROMPT` in `gemini.py`. Best-effort: on failure/parse error it degrades to
  `{}` and the pipeline continues without room-specific notes, same as `describe_room`.
- `get_provider()` (`app/providers/__init__.py`) returns `HybridProvider` — this is the composition root.
  `GeminiProvider.generate_image` still exists but is unused/dormant (would work again if billing is ever
  enabled on the Gemini project) — don't delete it without checking with the user first.

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
   (filesystem, dev default) or `s3.py` (boto3, S3-compatible → **Cloudflare R2** recommended, or AWS S3).
   SQLite stores **metadata + storage keys only**, never image blobs.
3. **Tier/prompt seam** (`app/pipeline/prompts.py`): tier differentiation is driven by a structured
   per-tier **style spec** dict plus a shared `PRESERVE_STRUCTURE` block. This dict is intentionally the
   seed of the future materials/pricing DB — keep it structured, not free-text. Encodes a real renovation
   cost-impact methodology (material quality ladder + lighting-temperature ladder, prioritized
   paint -> flooring -> lighting -> feature walls -> decor), not arbitrary tier adjectives.

   Two hard-won lessons from real testing, both still load-bearing — don't undo them:
   - **SD1.5's CLIP text encoder hard-truncates prompts at 77 tokens.** `build_prompt()` orders fields by
     priority (paint/color first, decor last) so the top-priority fields always survive truncation. The
     tier's *distinguishing color* lives inside the `paint` field itself (not a separate `palette` field
     alone) specifically so it can't be truncated away — an earlier version kept color only in `palette`
     and every tier rendered visually similar as a result.
   - **Diffusion models don't reliably obey negation in the positive prompt.** "no chandelier" written
     into `prompt` still primes a chandelier. Exclusions (no chandelier/marble/gold-trim for budget+mid,
     and a **universal** damage/disrepair exclusion applied to every tier so no render looks like the
     un-renovated input) must go through `build_negative_prompt(tier)` → the provider's `negative_prompt`
     param, never as "no X" text inside `build_prompt()`. `test_positive_prompt_never_mentions_chandelier`
     guards this.

   `tier_note` (optional 3rd arg to `build_prompt()`) carries the room-specific instruction from
   `generate_tier_notes` — see "Provider split" above. It's inserted right before `paint` (high priority,
   since it's usually a prerequisite qualifier for the paint step).

Async: FastAPI `BackgroundTasks` + client polling (no real queue yet — hardening-phase item). Each
Project's `meta_json` carries `PROMPT_VERSION` so outputs are reproducible/defensible.

Layout: `app/{main,config,db,models,schemas}.py`, `app/pipeline/` (prompts + orchestration),
`app/providers/` (base + gemini + cloudflare + hybrid), `app/storage/` (base + local + s3), `static/`
(vanilla HTML/JS — no build step), `data/` (sqlite + local storage, gitignored), `tests/`.

## Testing convention

All provider calls in tests are **mocked** — no real Gemini or Cloudflare network calls in the test suite
(quotas are limited/finite and must never be burned by CI). Pattern: monkeypatch the module-level client
call (see `tests/test_cloudflare_provider.py`, `tests/test_pipeline.py` for the FakeProvider pattern).
Prompt tests assert the three tier specs are mutually distinct and each carries the preserve-structure
instruction.

## Known limitations to keep in mind (not bugs to "fix" silently)

- SD1.5 img2img (current image backend) preserves structure via the `strength` parameter, not a hard
  geometric lock — expect more drift than the originally-planned Nano Banana approach, and expect less
  precise instruction-following than a natural-language-tuned model. ControlNet+depth is the eventual
  hardening upgrade if this proves insufficient.
- Free-tier caps (Cloudflare: 10,000 neurons/day; Gemini text: separate free quota) and localhost-only
  deployment are intentional for Phase 1.
- `GEMINI_IMAGE_MODEL` env var / Gemini image path is dormant, not deleted — kept for a possible future
  billing-enabled fallback.

## Git identity

This repo pushes to the **professional** GitHub account (`mhuzaifahahmed`) via the SSH alias remote
`git@github-mhuzaifahahmed:mhuzaifahahmed/interior-gen.git`, with per-repo local identity
`mhuzaifahahmed <mhuzaifah282@gmail.com>`. Never use `git config --global` here. See the machine-wide
`~/.claude/CLAUDE.md` for the full multi-account routing convention.
