# "Is this possible?" feasibility checker for Build a House

## Status

**DONE - built 2026-08-27, but as PURE DETERMINISTIC MATH, not the hybrid
math+Gemini design originally recommended below.** Driven by an external
architectural-generation spec the user supplied, which resolved both open
design decisions explicitly (see "Two open design decisions" below - now
answered, not open). `app/pipeline/feasibility.py`'s `check_feasibility()`
computes required area (real per-room-type minimums from
`app/pipeline/room_specs.py` + a circulation-overhead fraction + a staircase
allowance for multi-floor) vs. the available building footprint, classifies
`feasible`/`tight`/`not_feasible`, and is called from
`app/pipeline/generate_house.py` as a HARD GATE right before
`layout_floor()`/blueprint rendering - an infeasible request gets
`blueprint_status="infeasible"` + a real explanation
(`HouseProject.feasibility_json`, exposed via `HouseProjectStatusResponse.
feasibility`) instead of a silently-generated broken layout. The AI Concept
Layout stage is skipped too in that case (see generate_house.py's `v8`
comment). Frontend shows a banner (`static/app.js`'s
`renderHouseFeasibilityBanner()`) - red/blocking for `not_feasible`, a softer
informational tone for `tight`.

**Why pure math instead of the hybrid design originally recommended below**:
the user's later spec didn't ask for natural-language reasoning over
open-ended "extras" text (the original motivating case - basketball courts,
koi ponds, etc.) - it asked for feasibility over the STRUCTURED room program
(bedrooms/bathrooms/kitchens/garage) already going through
`generate_room_layout()`/`layout_floor()`, which already has real per-room
minimum sizes to check against deterministically. Gemini-synthesized
phrasing over those numbers (the hybrid idea) is still a reasonable upgrade
if free-text "extras" ever need their own feasibility check (e.g. "does a
basketball court fit"), but wasn't needed for what got built - see
`_room_size_specs`/`check_feasibility`'s plain f-string explanations, which
are accurate but more mechanical than a Gemini-phrased sentence would be.

## What this is

User request: if someone asks for requirements that can't realistically fit
the stated plot (e.g. a front yard + backyard + a basketball court on a small
plot), tell them it's not possible and suggest what to drop/change - instead
of silently generating something that ignores half the request.

## Recommended approach: hybrid (deterministic math + Gemini reasoning), NOT
pure-LLM and NOT pure-math

Three approaches were considered:

1. **Pure Gemini ("just ask the LLM if it fits")** - rejected. LLMs are
   unreliable at real area arithmetic ("does a basketball court fit in
   40x30ft?") - a feasibility checker that confidently gives WRONG verdicts
   is worse than no checker at all, actively destroys trust.
2. **Pure deterministic math (no AI)** - rejected as the sole approach.
   Accurate, but brittle: the "extras" field is free text (anything from
   "basketball court" to "koi pond" to "meditation garden") - a hardcoded
   area-lookup table can't cover arbitrary user phrasing, and can't produce
   natural-language suggestions.
3. **Hybrid (recommended)**: Python computes the authoritative numbers (plot
   area from length x width, buildable area x floor count, and a small known-
   footprint table for common extras like "basketball court" ~ 1000 sqft
   half-court), THEN Gemini is given those real numbers + the user's stated
   requirements and produces the verdict + natural-language suggestions.
   Gemini never invents the numbers, only reasons over them and phrases the
   response - the same "real data -> LLM synthesizes, LLM never guesses the
   hard numbers" pattern this codebase already uses for materials pricing
   (see CLAUDE.md's "Materials & pricing feature" section, specifically the
   SerpApi-search-then-Gemini-synthesizes design) and worth reusing directly
   rather than reinventing.

## Two open design decisions - NOW RESOLVED (2026-08-27, explicit user answer)

1. **Advisory warning vs. hard gate → HARD GATE, chosen over this doc's own
   "advisory" recommendation.** The user was asked directly and picked "block
   render, explain" - the blueprint/AI floor-plan stages never run for an
   infeasible request; only the exterior render still proceeds (a
   photoreal visualization of the plot itself isn't misleading the same way a
   blueprint of rooms that don't fit would be).
2. **When it runs → AFTER generation starts, not at input time.** Not the
   originally-recommended "check before generating" - it runs inside
   `run_house_pipeline()` right after `generate_room_layout()` returns (real
   room data is needed to check against, and that call is free/text-only
   anyway), BEFORE the paid render step. So a wasted OpenAI/Kaggle render
   call is still avoided when infeasible (`house_render_enabled`'s paid step
   only runs after the gate), just not via a separate pre-submit UI check -
   the two both prevent wasted PAID cost, this recommendation's exact
   original goal, just via a different mechanism (mid-pipeline gate vs.
   pre-submit check).

## Where this would plug in

- Reuses `dimensions` (`length`/`width`/`unit`) and the structured
  `house_inputs` (`floor_count`/`bedrooms`/`bathrooms`/`extras`) already
  collected by the "Plot Parameters" panel (`static/index.html`,
  `app/main.py`'s `create_house_project`) - no new inputs needed to start.
- A new best-effort Gemini call, same shape/cost profile as
  `analyze_plot()`/`generate_room_layout()` (free text quota, not the paid
  image path) - would live in `app/providers/gemini.py` alongside those.
- Frontend: a warning/suggestion panel shown after the structured inputs are
  filled in, before (or instead of, if a hard gate) the Generate button -
  exact placement TBD when this is picked up.

## Why deferred

Proposed in the same session the friend-hosted AutoCAD Kaggle model was
wired in (see CLAUDE.md's "Real DXF/AutoCAD-format export" entry) - parked
to keep that work scoped, not because of any technical blocker.
