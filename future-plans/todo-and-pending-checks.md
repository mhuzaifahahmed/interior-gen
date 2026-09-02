# Active TODO / pending checks (2026-08-31)

Living checklist - update/remove items as they're done, don't let this go stale. Distinct from the other
future-plans files (which are point-in-time design docs) - this one is a working list.

## Pending: waiting on the user to report back

- [ ] **Build a House generation time** - user will run a real generation, measure actual wall-clock time
  (expects ~5 min), and report back with a screenshot. Remove this item once reported; convert the result
  into a real timing investigation below. Note: a 2026-09-01 attempt at this got derailed by a zombie
  process (see below) and then a real Kaggle session outage - not yet a clean measurement.
- [ ] **Room redesign generation time** - same - user will run a real generation and report the actual
  time it takes. Remove this item once reported.

## Resolved: 2026-09-01 stuck-generation incident (two separate real causes, both fixed)

- [x] **Zombie process holding a SQLite lock**: a `python.exe serve-local.py` process (PID 2808) had been
  running since 8/28 - three days - bound to port 8000 and, critically, almost certainly holding a lock on
  `data/app.db`. New generations' background-task commits blocked on that lock forever with no error (GET
  polls still returned 200 since reads weren't blocked). `serve-local.py` doesn't even exist in the repo
  anymore. Killed the process; confirmed only the real `uvicorn --reload` process (PID 30144) was left on
  port 8000 afterward. **No code change for this one** - just a leftover local process. If "stuck forever,
  no error, but polling still returns 200" happens again: check `netstat -ano | grep :8000` for more than
  one PID before assuming it's a code bug.
- [x] **Real Kaggle session-offline detection built (2026-09-01)** - the user's follow-up report ("session
  was stopped on kaggle") turned out to be a second, separate real issue (the Concept Layout notebook's
  session had actually stopped), and exposed a real gap: neither best-effort nor non-best-effort Kaggle
  calls told the user WHY something didn't work when the session was down - just a silent degrade or a raw
  httpx traceback. Built `app/providers/session_errors.py` - classifies connection-shaped failures
  (`httpx.TransportError`, Cloudflare tunnel-down status codes) as `KaggleSessionUnavailableError` with an
  actionable message, wired into `kaggle_autocad.py` (new `floor_plan_status="unavailable"` +
  `floor_plan_error`, distinct from silent `"not_configured"`) and `kaggle.py` (room-redesign methods +
  `generate_house_render`). See CLAUDE.md's "Kaggle session-offline detection" entry for the full writeup.
  419/419 passing.

## Planned: room redesign speed (blocked on the timing report above)

- [ ] Once the real time is reported, use `app/pipeline/generate.py`'s EXISTING `PipelineTimer`
  (`app/pipeline/timing.py`) - it already logs a per-stage breakdown
  (`describe_room`/`generate_tier_notes`/`generate_images_batch`/`materials.join`/etc.) via
  `timer.log_summary()`. Pull that log line from a real run instead of guessing where the time goes.
- [ ] Candidate levers once the bottleneck stage is known (don't apply blindly - confirm which one
  actually matters first): Kaggle session/tunnel not being "warm" (no keep-alive, unlike Modal - real,
  known tradeoff, see CLAUDE.md); the materials-pricing stage's up-to-100s budget
  (`MATERIALS_TIMEOUT_SECONDS`); running `describe_room`/`generate_tier_notes` sequentially before image
  generation starts instead of overlapping them.
- Explicitly NOT re-litigating Kaggle vs. Modal - already decided (2026-08-31 user reconfirmed): Modal's
  cold-start/cooldown behavior "was eating all of our extra time" and wasn't worth its advantages. Staying
  on Kaggle (`IMAGE_PROVIDER=kaggle`).

## Planned: Build a House speed (blocked on the timing report above)

- [x] **DONE 2026-08-31, SUPERSEDED 2026-09-01**: v10 ran the AI Concept Layout call and the exterior
  render call CONCURRENTLY. Real live measurement showed this alone wasn't enough - Concept Layout is
  ~2min/floor and `HOUSE_RENDER_ENABLED=false` in dev right now, so there was nothing to overlap with.
- [x] **DONE 2026-09-01 (v11)**: `generate_floor_plan()` (Concept Layout) is now fully DECOUPLED from the
  house project's `done` status - runs on a fire-and-forget daemon thread, never joined. The project
  completes as soon as blueprint/DXF/render are ready; the Concept Layout card fills in later via the
  existing `floor_plan_status` poll field. This is the real fix for "2 min per floor is too much" - it
  doesn't make the Kaggle call faster, but it stops the whole project from waiting on it. See CLAUDE.md's
  `v11` entry for the full writeup (detached-thread design, the StaticPool-vs-file-DB test lesson, the
  accepted cancellation tradeoff). 418/418 passing, verified stable across repeated full-suite runs.
- [x] **Live-measured 2026-09-01**: the Kaggle Concept Layout call itself is **~2 minutes per floor**
  (`scripts/test_autocad_kaggle_labels.py` against a real tunnel, single floor/6 rooms). Floors are
  generated sequentially on the notebook's own side (one GPU, one `generation_lock`) - a multi-floor
  request takes proportionally longer, not a fixed cost.
- [ ] `app/pipeline/generate_house.py` still has NO per-stage timing instrumentation (confirmed by grep,
  2026-08-31) - unlike `generate.py`. Now less urgent for Concept Layout specifically (its real cost is
  already known: ~2min/floor, decoupled from `done` either way), but still worth adding for
  analyze_plot/blueprint+feasibility/render if further Build-a-House speed work is wanted.
- [ ] The DXF export itself is NOT the bottleneck (confirmed - pure Python/ezdxf, no model call,
  milliseconds) - don't waste time there.
- [ ] If the ~2min/floor Concept Layout time itself needs to come down (not just get out of the critical
  path, which v11 already did): the real levers are on the NOTEBOOK side - `num_inference_steps` (currently
  35) is the most direct one, or lower resolution. Not attempted - the decoupling made this less urgent
  since it's no longer blocking anything.

## Planned: make the Kaggle Concept Layout model genuinely better (tuning, not training - see above)

**Real visual critique done 2026-08-31** (fresh look at `scripts/output/autocad_with_labels_floor1.png` +
`autocad_conditioning_floor1.jpg`, side by side against our own deterministic blueprint): the AI card has
fallen BEHIND our own renderer's visual quality (used to be the reverse, which is the whole reason it was
integrated) - washed-out low-contrast gray instead of real black-on-white, still thin double-line walls
(no poché), furniture reads as vague blobs not objects, visible noisy/mottled background texture.

- [ ] Push the prompt harder for contrast: explicit "pure white background, solid black bold walls, high
  contrast crisp vector line art" + add "gray, washed out, hazy, sketchy, low contrast, faded" to the
  negative prompt.
- [ ] Explicitly prompt for "thick solid filled black walls" to nudge toward a poché look matching our own
  renderer's v6 upgrade.
- [ ] Try raising `guidance_scale` above its current 7.0 - the model isn't adhering strongly enough to the
  stated style right now.
- [ ] Swap Canny ControlNet for MLSD (better suited to rectilinear architecture, may also reduce the
  noisy/hazy background) - Phase 3 from `future-plans/concept-layout-controlnet-conditioning.md`.
- [ ] Consider DROPPING furniture from the prompt entirely - it's where the AI output looks worst, and our
  own renderer already draws furniture well; let the AI card just be walls/rooms in a nicer visual style,
  nothing more.
- [ ] `num_inference_steps` fine-tuning; a floor-plan-specific LoRA IF a commercially-clean one is found
  (check license before use - same RPLAN-style trap already documented in CLAUDE.md).
- [ ] Re-verify after any notebook change with `scripts/test_autocad_kaggle_labels.py` (real end-to-end
  test against the live tunnel, no OpenAI cost) before considering it done - same discipline as the
  ControlNet-conditioning work, which was live-verified, not just reasoned about.

**Status (2026-08-31): notebook redeployed, live-verified, real partial improvement.** The 4 safe tuning
changes were pasted in and confirmed live via `scripts/test_autocad_kaggle_labels.py` against the new
tunnel URL. Result: walls are genuinely darker/bolder now (contrast push worked) - but 3 things still
need attention:
- [ ] Still NO poché (solid-filled) walls - contrast improved but walls are still line-drawn, not filled.
  Try pushing the prompt even harder ("walls filled solid black, thick poche band") or accept this may be
  a harder ask for SDXL than contrast alone.
- [ ] NEW vignette/dark-corner artifact appeared (likely a `guidance_scale` 9.0 side effect) - try adding
  "vignette, dark corners, vignetting" to the negative prompt.
- [ ] Small illegible scribble-text is now more visible near room centers (may have existed before,
  hidden by low contrast) - matches this project's own already-documented diffusion limitation (text
  exclusion reduces but doesn't eliminate stray text attempts). Not a new problem, just newly visible.

MLSD swap still NOT attempted (needs a different ControlNet checkpoint + pipeline class, wants dedicated
live testing) - next real lever if the above 3 tweaks don't close the gap.

**Real root-cause finding (2026-08-31): the v2 negative prompt exceeded CLIP's 77-token limit.**
Estimated ~93 tokens (word+comma count heuristic) - meaning the tail end, including the exact gray/
washed-out/contrast terms meant to fix the original complaint, was very likely silently truncated and
never reached the model. This is the real explanation for the partial-only improvement + new vignette
artifact. Fixed in the v3 draft: negative prompt REORDERED BY PRIORITY (truncation cuts the end, so
highest-impact terms go first: text/labels > gray/contrast/vignette > furniture > generic 3d/photoreal
exclusions) and trimmed of redundant synonyms, estimated ~58 tokens now, real margin restored. Also
trimmed the positive prompt (was safe but had redundant padding eating headroom needed for room
details/notes). Notebook now also PRINTS the exact prompt/negative_prompt + word count on every
generation, so future tuning can watch the actual budget instead of guessing.

**LIVE-VERIFIED 2026-09-01** against a new tunnel (`fleece-trusted-gras-jump.trycloudflare.com`,
`scripts/test_autocad_kaggle_labels.py`, output re-saved to
`scripts/output/autocad_with_labels_floor1.png`) - the token-budget fix produced a real, visible
improvement, not just a theoretical one:
- [x] Contrast/washed-out gray - FIXED. Walls are now crisp bright white against a solid gray field,
  no more hazy/gray-on-gray look.
- [x] Vignette/dark corners - GONE in this run, background is even across the whole image.
- [x] Geometry still traces our real layout correctly (6 rooms, correct adjacency/proportions), our
  own composited labels land correctly in every room.
- [ ] Poché (solid-filled) walls - STILL NOT achieved, still thin double-line. Suspect this may be a
  harder ask than prompting can fix for a ControlNet/Canny-conditioned SDXL model specifically (a
  filled black band fights the line-based edge-map conditioning signal itself) - if revisited, this is
  the one to test an MLSD swap against rather than more prompt tuning.
- [ ] Small illegible scribble-text near room centers - reduced vs. before, not eliminated. Matches
  the already-documented diffusion text-limitation, not a regression.

## Planned: remaining "intelligence" gaps in the deterministic layout engine

Full detail already recorded in `future-plans/house-layout-spec-checklist.md` - not duplicated here, just
flagged as active work items the user wants to come back to:
- [ ] #17 remainder - plumbing-zone vertical alignment (bathrooms/kitchens stacking between floors) -
  not attempted at all yet.
- [ ] #13 - a REAL per-room exterior-wall/window guarantee (currently just the existing placement
  heuristic, not an enforced constraint) - this is what "room ventilation intelligence" maps to.
- [ ] #20/#21 - a real post-generation validator + candidate-layout scoring, instead of one deterministic
  pass with no regenerate-if-invalid loop.
