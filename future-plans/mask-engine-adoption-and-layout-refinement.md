# Mask-engine adoption + layout practicality refinement — execution plan

**Status:** APPROVED, not started. Author: architecture review (2026-10-09).
**For:** the models implementing this, one task at a time.
**Decision already made by the user:** adopt the **`mask`** layout engine as the shipping
engine and fix the architectural-practicality problems around it. This is locked — do
not re-litigate rect-vs-mask.

### Priority & scope update (2026-10-09, user directive)

- **PRIMARY FOCUS = "our model"** — the deterministic layout engine (`mask`) + its blueprint PNG/DXF
  output. The **AI "Concept Layout"** vendor (Kaggle SDXL+ControlNet, `kaggle_autocad.py` /
  `conditioning_image.py`) is **DEPRIORITIZED** — do NOT spend effort on it in this plan. The
  conditioning-image yard-parity task is **dropped**. The real `.dxf` export is OURS (not the AI
  vendor) and stays in scope.
- **AUTO SITE-PLANNING is a promoted, first-class feature:** on a genuinely large residential plot the
  model must AUTOMATICALLY leave and render real yard — driveway, front/rear yards, side setbacks —
  rather than let the house sprawl to fill the lot. "This is going to be used for HOUSES", so a big plot
  must look like a house on a property, not a building edge-to-edge. See the expanded Phase C.

---

## 0. Why this exists (read fully before touching anything)

A live 150×150 ft, 2-floor, ~10-room generation produced a floor plan with seven
real, architect-confirmed problems:

1. **House uses ~40% of the plot; the rest renders as blank drafting sheet** — no
   driveway, garden, or setbacks. (A *rendering* gap, not a layout bug.)
2. **Bowling-alley rooms** — a 62.6×25.6 ft master bedroom, 28×12 ft secondary bedrooms.
3. **Inverted social hierarchy** — a 1,469 sq ft dining room next to a 400 sq ft living room.
4. **Dining dwarfs living; rooms balloon past their real-world caps.**
5. **Master suite stranded in a central band** with the hallway running past it — no privacy.
6. **A 78-ft single tunnel hallway** — 769 sq ft of pure circulation.
7. **Kitchen and dining ~100 ft apart; utility/laundry stranded** away from the kitchen.
   Garage is 1-car, buried, with no driveway / road access.

### The key fact that shapes this whole plan

There are **two layout engines** already in the codebase, behind a feature flag:

- **`rect`** — `app/pipeline/floor_layout.py` — rectangular slice-and-dice. **This is what
  produced the bad map** and is the current default (`settings.house_layout_engine = "rect"`,
  `app/config.py` ~line 318).
- **`mask`** — `app/pipeline/floor_layout_mask.py` — grid region-growing engine, **purpose-built
  to fix exactly these problems**: it guarantees window-wall egress *by construction*, reserves a
  compact central circulation spine (not an edge tunnel), grows compact near-square rooms (and L/U
  shapes when needed), clamps the house footprint to `min(target, sum(max_area))` so rooms can't
  balloon, and spreads bedrooms across multiple exterior walls.

Both are routed through a single dispatcher, `app/pipeline/layout_engine.py`
(`layout_floor_with_warnings` / `layout_floor`), selected by `settings.house_layout_engine`
(`"rect"` | `"mask"`). **Every real call site already imports from the dispatcher**
(`generate_house.py`, `feasibility.py`'s geometry probe, `kaggle_autocad.py`'s conditioning image),
so **flipping the engine is a one-line default change** — no call-site edits.

**Therefore most of the fix is: finish-verify-and-flip to `mask`, then add yard/site rendering
(which neither engine does) plus a few targeted `mask`-engine gaps.** You are NOT rewriting an
engine from scratch.

### How `mask` maps onto the 7 problems

| # | Problem | Fixed by | Where |
|---|---------|----------|-------|
| 2,9 | Bowling-alley rooms | `mask` (compact region growth, bedrooms spread across 3 walls) | engine, structural |
| 3,4 | Dining > Living, ballooning | `mask`'s `_mask_footprint_dimensions` clamps footprint to `sum(max_area)`; `ABSOLUTE_MAX_AREA_SQM` caps hold | engine + `room_specs.py` |
| 6 | 78-ft tunnel hallway | `mask`'s compact centered cross-spine | engine, structural |
| 5 | Master in the middle | `mask` seeds bedrooms on the exterior perimeter (partial) + Phase D master-corner rule | engine + Phase D |
| 1 | Blank plot / no yard | **Phase C** yard rendering (engine-independent; and `mask` makes the footprint *smaller*, so this matters MORE) | renderers |
| 7 | Kitchen↔dining split; utility stranded | **Phase D** adjacency + utility-near-kitchen | engine + pipeline |
| 8 | Garage: 1-car, buried, no driveway | **Phase C** driveway render + **Phase D** garage shape/road-edge (a `mask` known gap) | renderers + engine |

---

## 0.5 Input handling — GROUND TRUTH (verified against the code 2026-10-09)

Before any task assumes *where garage/kitchen/dining/utility come from*, here is the REAL flow
(verified in `app/main.py::create_house_project`, `app/pipeline/house_requirements.py`,
`app/pipeline/generate_house.py::run_house_pipeline`). **Do not contradict this.**

- **Garage is NOT a structured field.** The user types it in the free-text **`extras`** box (folded
  into `HouseProject.prompt` by `_compose_house_requirements()`). `run_house_pipeline` resolves
  `garage_cars = parse_garage_cars(text) if mentions_garage(text) else None` (`"2 car garage"` /
  `"garage for 2"` → 2, else 1); strips any Gemini-invented garage if unmentioned; injects
  `Garage` + `Entry` on the **ground floor only** when requested. → Phase D4's 2-car change belongs in
  `generate_house.py`'s garage_cars resolution, as written.

- **Per-floor rooms (kitchen/dining/utility/etc. on specific floors) — TWO paths + ONE real gap:**
  - *Path 1* — shared `extras` box with an explicit floor number: `parse_extra_rooms_by_floor(text)`
    catches `"kitchen on floor 2"`, `"dining room on 3rd floor"` (needs a DIGIT in the same clause).
    Vocabulary = `_EXTRA_ROOM_CATEGORIES`: kitchen, dining, living, study, laundry(=utility), closet,
    dirty_kitchen, pantry, prayer, family, powder, storage. (NOT bedroom/bathroom = the count system;
    NOT garage/staircase/foyer = their own handling.)
  - *Path 2* — "Per floor" mode's per-floor text boxes (`floor_extras[i]` → floor i+1):
    `extra_rooms_from_floor_text()` — direct keyword scan per box, no floor number needed.
  - Both union into `extra_rooms` → `_ensure_requested_rooms_by_floor()` GUARANTEES each
    (category, floor) room exists on that floor.
  - **⚠️ REAL GAP:** "kitchen/dining/utility on **every** floor" / "each floor" typed in the SHARED box
    is **NOT parsed** (`_FLOOR_REF_RE` needs a digit; "every" has none). It falls through to
    ground-floor-only guarantees (`_ensure_ground_floor_public_rooms` = Living/Kitchen/Dining GROUND
    ONLY; utility ground-only; upper floors get only a `_ensure_upper_floor_lounge` "Sitting Area").
    **So an "every floor" kitchen/dining/utility request is silently honored on the ground floor only
    unless the user used Path 1 or Path 2.** This is a candidate fix, flagged for the user (see below) —
    it is NOT one of the original 7 layout problems and is out of scope unless the user confirms.

- **Utility/laundry:** `mentions_utility(text)` (True if "utility"/"laundry" anywhere) → guaranteed on
  the **ground floor only**; stripped from all floors if unmentioned. Per-floor utility needs Path 1/2.

**Consequence for this plan:** Phase D placement rules must operate **per-floor** — "put utility next
to THIS floor's kitchen", "kitchen adjacent to dining on whatever floor each is on" — and must NOT
assume these rooms live only on the ground floor. A kitchen/dining/utility CAN legitimately exist on an
upper floor (via the per-floor request paths). Any task that hardcodes "ground floor only" for these is
wrong.

---

## 1. GOLDEN RULES for every task below

1. **Do NOT modify `app/pipeline/floor_layout.py` (the `rect` engine) to change its geometry.**
   It stays the frozen, proven fallback. You may *read* its helpers; `mask` already reuses several
   (`_anchor_offsets`, `_arrange_suites`, `_sort_key`, `_zone_key`, `_footprint_target_area`,
   `footprint_bbox`, `room_parts`). Shared pure helpers (e.g. in `room_specs.py`) MAY be edited, but
   re-check that the change doesn't regress `rect` (run the full suite).
2. **Develop and verify under `HOUSE_LAYOUT_ENGINE=mask` in your local, gitignored `.env`.** Do
   **not** flip the committed default (`app/config.py`) until **Phase E**, the last coding phase, in
   its own commit — this matches the project's stated rollout discipline.
3. **VISUAL VERIFICATION IS MANDATORY, not optional.** This project's own hard-won rule (stated
   repeatedly in `CLAUDE.md` and `floor_layout_mask.py`'s docstring): *automated tests alone do NOT
   catch layout bugs.* Every rejected approach in the mask engine's own history passed all numeric
   checks (zero holes, zero overlaps, zero hard violations) yet rendered visibly broken. For any task
   touching geometry or rendering, you MUST render the real PNG and look at it before claiming done.
   See §2 for the render recipe.
4. **One task = one focused change + its tests + a visual check.** Do not batch unrelated tasks.
5. **Run the full suite after each task:** `./.venv/Scripts/python.exe -m pytest -q` (or `pytest -q`).
   Report the pass count. The suite was 834 tests at the start of this plan.
6. **Never `git push` or commit without the user explicitly asking.** Never change `app/config.py`
   `house_layout_engine` default except in the dedicated Phase E task.
7. **Items marked `⚠️ CONFIRM WITH USER` must STOP and ask before implementing** — they are product
   decisions, not engineering ones.
8. If a task's acceptance check fails or reality contradicts this plan, **STOP and report what you
   found** rather than improvising a different change.

---

## 1.5 RESOLVED DECISIONS — the lower model does NOT stop to ask the user for these

The user delegated these product calls ("make the entire plan... for HOUSES", 2026-10-09). They are
DECIDED. Implement as stated. All are named, easily-tuned constants so the user can override later, but
do NOT block execution on them:

1. **C0 site-planning numbers (CHOSEN):** `AUTO_YARD_PLOT_THRESHOLD_M = 12.0` (~40 ft on the shorter
   plot side — below this, no forced yard), `MAX_PLOT_COVERAGE = 0.60` (house covers at most 60% of a
   large plot), `MIN_FRONT_SETBACK_M = 5.5` (~18 ft — fits a driveway + parked car). Ship these.
2. **Garage car count = exactly what the user typed** (`parse_garage_cars`): "garage" → 1, "2 car
   garage" → 2. **NO auto-bump to 2 on large plots** — the user supplies the count in `extras`; honor it
   verbatim.
3. **Upper floors (floor_number > 1):** render the same plot outline + open space, but **NO driveway and
   NO garden hatch** (site features are ground-floor only); the footprint is centered on that outline.
4. **Laundry/utility placement = adjacency-only** (seed next to the SAME floor's kitchen). **Do NOT
   re-zone `laundry`** into `_PUBLIC_ZONE_KEYWORDS` (that touches the frozen `rect` engine). If
   adjacency-only won't render cleanly, STOP and report — never re-zone as a workaround.
5. **AI Concept Layout = deferred**, not in this plan (see "Deferred follow-up" at the end).
6. **"every/each floor" room requests = Phase G** (below) — a real, in-scope fix, since the user
   explicitly wants kitchen/dining/utility on every floor.

Nothing above is an open gate. The ONLY legitimate reason to pause and ask is if the Phase F rendered
matrix makes the C0 numbers look visibly wrong — then surface it, do not silently re-tune.

---

## 2. The render-and-inspect recipe (you will use this constantly)

Scratchpad dir (use for all throwaway scripts/outputs):
`C:\Users\User\AppData\Local\Temp\claude\c--Users-User-interior-gen\<session>\scratchpad`

Reference examples already in the repo: `scripts/test_blueprint_dxf.py`,
`scripts/test_autocad_kaggle_labels.py`.

Write a scratch script like this (adapt per task) and open the PNG it writes:

```python
import os
os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"   # force the engine under test
from app.pipeline.layout_engine import layout_floor_with_warnings
from app.pipeline.blueprint_svg import render_floor_blueprint

# The EXACT reported case — 150x150 ft, south-facing, ground floor, ~10 rooms:
dimensions = {"length": 150.0, "width": 150.0, "unit": "ft"}
rooms = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
rects, warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars=1, facing="south")
png = render_floor_blueprint(1, rects, dimensions, 2, "south")   # (floor_no, rects, dims, total_floors, facing)
open("scratchpad/mask_floor1.png", "wb").write(png)
print("hard violations:", [ (v.room, v.detail) for v in warnings if v.hard ])
```

Then **read the PNG with the Read tool and actually look at it.** Checklist every time:
near-square rooms (no bowling alleys), kitchen touching dining, living ≥ dining, master on an
exterior corner, a visible compact hallway (not a full-width tunnel), a drawn front entrance, the
yard rendered (after Phase C), zero hard violations printed.

---

## PHASE A — Baseline: render `mask` on the real cases BEFORE changing anything

**Goal:** know exactly what `mask` fixes on its own vs. what still needs Phases C/D, so no effort is
spent fixing problems the engine already solved.

**A1. Render the mask engine, as-is, across the baseline matrix.** No code changes. Using the §2
recipe, render and visually inspect PNGs for:
- The exact reported case: 150×150 ft, south, 2-floor, the ~10-room ground floor above, AND its
  upper floor (bedrooms + staircase + sitting area).
- A medium case: 50×60 ft, south, 1-floor, 3 bed / 2 bath + living/kitchen/dining/entry.
- A small/tight case: 30×40 ft, south, 1-floor, 2 bed / 1 bath + living/kitchen/dining.
- A large wide case: 90×40 ft, south, 1-floor.
- One per facing (north/east/west) on the 50×60 case, to confirm the facing transform.

**A2. Write a short findings note** to `future-plans/mask-adoption-phase-a-findings.md`: for each of
the 7 problems, is it ✅ already fixed by `mask`, 🟡 partially, or ❌ still broken — with the specific
PNG evidence. **Report this to the user before proceeding to Phase B.** This note re-scopes Phases
C/D if `mask` already solved more (or less) than predicted.

**Acceptance:** PNGs rendered and inspected; findings note written; nothing in `app/` changed.

---

## PHASE B — Make the test suite green under `mask` BEFORE flipping anything

**Goal:** flipping the default in Phase E must not surprise-break the suite. Establish green-under-mask
now, while it's cheap to triage.

**B1. Audit which tests exercise the dispatcher vs. the rect engine directly.**
- Tests that import from `app.pipeline.floor_layout` directly (`tests/test_floor_layout.py`) test
  `rect` regardless of the flag — leave them; `rect` is unchanged.
- Tests that go through `app.pipeline.layout_engine`, `generate_house.py`, `feasibility.py`, or
  `kaggle_autocad.py` (`tests/test_house_pipeline.py`, `tests/test_house_api.py`,
  `tests/test_feasibility.py`, `tests/test_kaggle_autocad.py`, `tests/test_api.py` for the
  room-requirements table) will switch to `mask` when the flag flips. These are the ones to audit.
- Run the suite once with `HOUSE_LAYOUT_ENGINE=mask` set in the environment and record every failure.

**B2. Add a conftest fixture to pin the engine per-test.** In `tests/conftest.py`, mirror the
existing autouse fixtures `_default_house_render_enabled` / `_default_autocad_generation_enabled`
(they force a known default regardless of the developer's local `.env`). Add
`_default_house_layout_engine` forcing `settings.house_layout_engine = "rect"` by default, so the
*existing* dispatcher-based tests keep asserting against the engine they were written for and don't
break the instant Phase E flips the product default. Then, for tests that SHOULD assert mask
behavior, let them opt in (e.g. a `monkeypatch.setattr(settings, "house_layout_engine", "mask")` or a
parametrization). **This decouples "what the suite asserts" from "what ships", which is what lets
Phase E be a safe one-liner.**

⚠️ **Design note for B2:** Confirm the exact mechanism `feasibility.room_count_requirements_table`
uses — it is `@functools.lru_cache`’d and calls the dispatcher, so toggling the engine mid-process
can serve a stale cached table. The fixture (or the opt-in tests) must `room_count_requirements_table.cache_clear()`
after changing the flag. Verify this is handled or tests will be flaky.

**B3. Triage each real failure from B1** into: (a) a test that hard-codes rect-specific geometry and
should be pinned to rect via B2 (most cases), or (b) a genuine behavior the product now wants from
mask and whose expectation should be updated. For any (b) case, **render and visually confirm the new
behavior is actually correct before changing the assertion** — never update an assertion just to make
it pass.

**Acceptance:** full suite green with the committed default still `"rect"`; full suite ALSO green with
`HOUSE_LAYOUT_ENGINE=mask` forced in the environment (modulo tests intentionally pinned to rect).
Report both pass counts.

---

## PHASE C — Auto site-planning: footprint coverage + yard/driveway rendering (fixes #1, visible half of #8)

**Context:** The renderers ALREADY draw the house inside `footprint_bbox(rects)` within the full plot
(`render_floor_blueprint`, `app/pipeline/blueprint_svg.py` ~line 178, computes
`fp = footprint_bbox(rects) or (0,0,length,width)`; there's already a `_draw_property_line` at ~405).
So the blank area around the house IS the yard — it's just rendered as empty grid. `mask` shrinks the
footprint *more* than `rect`, so without this, large plots look *more* broken, not less. This phase is
**engine-independent** and is the single biggest perceived-quality win.

**C0. Size-triggered site planning in the footprint sizer (mask engine) — the "auto driveway + yards
above X by X" behavior.** Today the footprint only shrinks when the *room program* is smaller than the
plot (`_mask_footprint_dimensions` → `footprint = min(target·margin, sum(max_area), plot)`). That means
a large plot with a large room program can still sprawl edge-to-edge with no yard. For a HOUSES product
that's wrong — a big lot should always read as a house on a property. Add, in
`app/pipeline/floor_layout_mask.py::_mask_footprint_dimensions` (mask-only; do NOT touch
`rect`'s `_footprint_dimensions`, Golden Rule 1):
- **Plot-coverage cap:** when the plot is larger than a threshold, additionally cap the footprint to at
  most `MAX_PLOT_COVERAGE` of the plot area, so a front + rear + side yard is always guaranteed, even
  when the room program could have filled the lot. Define named constants `AUTO_YARD_PLOT_THRESHOLD_M`
  and `MAX_PLOT_COVERAGE` (values DECIDED in §1.5).
- **Minimum front setback:** when a garage/driveway will be drawn, ensure the footprint's facing edge
  sits at least `MIN_FRONT_SETBACK_M` back from the plot's facing edge so a driveway + a parked car
  actually fit. (`_anchor_offsets` front-flushes, so this must be applied as a real front offset here,
  not left to centering.) (value DECIDED in §1.5).
- **Strict no-op on small/right-sized plots:** a compact urban lot whose coverage is already below the
  cap still fills edge-to-edge exactly as today. The feature only ever *adds* yard, never removes usable
  building area a small plot genuinely needs.
- Because the result flows through `footprint_bbox(rects)`, the yard RENDERING below (C1/C2) picks this
  up automatically — C0 decides *how much* yard exists, C1/C2 draw it.

**Values (DECIDED — see §1.5, do not ask):** `AUTO_YARD_PLOT_THRESHOLD_M = 12.0` (~40 ft, shorter plot
side), `MAX_PLOT_COVERAGE = 0.60`, `MIN_FRONT_SETBACK_M = 5.5` (~18 ft). Keep all three as named,
easily-tuned constants. Ship these values; the user adjusts after seeing the Phase F matrix only if they
look wrong.

**C1. Draw labeled outdoor zones in the leftover plot area (PNG).** In
`app/pipeline/blueprint_svg.py`, add a `_draw_yard(...)` helper called from `render_floor_blueprint`
*after* the grid/property line but *before* the house walls, using `footprint_bbox` (house rect) vs.
the full plot rect, and `facing` to know which edge is the road:
- The band between the footprint's facing edge and the plot's facing edge = **front zone**: label it
  and reserve the driveway (see C2). The opposite band = **REAR GARDEN** (light hatch / "GARDEN"
  label). Side bands = **side setback** (thin, labeled or left plain).
- Use subtle, clearly-outdoor styling (light hatch or stipple + a small label), visually distinct
  from interior rooms. Do NOT fill it like a room or a wall.
- **Ground floor only** gets the full yard treatment (driveway + garden hatch + setback labels). Upper
  floors (`floor_number > 1`) render the same plot outline with the footprint centered on it and the
  surrounding area as plain open space — **no driveway, no garden hatch** (§1.5 decision 3).

**C2. Draw a driveway apron from the road edge to the garage (PNG).** Only when a room classifies as
`garage` (`room_specs.classify_room_category`). Find the garage rect, drop a driveway rectangle from
the garage's facing-edge side out to the plot's facing (road) boundary, styled as paving. This makes
garage road-access *visible* (problem #8's visible half). If the garage is not adjacent to the facing
edge, the driveway can't be drawn cleanly — in that case skip it and note it; Phase D4 makes the
garage reach the road edge.

**C3. Mirror the yard/driveway into the DXF export (our model's output).**
- `app/pipeline/blueprint_dxf.py` (`render_floor_blueprint_dxf`): add the yard boundary + driveway as
  real line geometry on new layers (e.g. `YARD`, `DRIVEWAY`) — DXF consumers expect the site, not just
  the building. Keep it line-only (no hatch fill), consistent with that module's line-only doctrine.
- `app/pipeline/conditioning_image.py` (the AI Concept Layout) — **OUT OF SCOPE / DROPPED** per the
  2026-10-09 priority directive. Do not touch it in this plan. The AI vendor is deprioritized; the yard
  belongs to our own PNG + DXF output only.

**C4. Verify positioning reads correctly.** Render the 150×150 south case: house should sit toward the
road (facing) edge with a large rear garden, driveway to the garage, symmetric side setbacks. If it
reads wrong (e.g. house floating dead-center with no road relationship), the `_anchor_offsets` logic
(reused by both engines) front-flushes along the facing axis and centers the cross axis — confirm that
matches "house at front, garden behind." If not, STOP and report; changing anchoring affects both
engines.

**Tests:** add `tests/test_blueprint_svg.py` cases asserting (a) a yard region is drawn when the
footprint is smaller than the plot and NOT drawn when the footprint fills the plot (no-op on a
right-sized plot), (b) a driveway is drawn iff a garage room exists. Add a `tests/test_blueprint_dxf.py`
case asserting the yard/driveway layers appear and `doc.audit()` still returns 0 errors. **Render +
inspect every change.**

**Acceptance:** 150×150 case renders with a real, labeled yard + driveway; a right-sized plot renders
unchanged (yard code is a strict no-op when footprint == plot); DXF audits clean; tests pass.

---

## PHASE D — Targeted `mask`-engine gaps (adjacency, garage shape/access, master corner)

These are the things `mask` does NOT yet guarantee. Re-scope against the Phase A findings — skip any
sub-task Phase A proved already-good. **All geometry work here is in
`app/pipeline/floor_layout_mask.py`.**

**D1. Kitchen ↔ Dining adjacency (problem #7a).** `mask` seeds public rooms spread along the front
row in `_sort_key` order (`_seed_rooms`); `_PUBLIC_ORDER_RANKS` (in `floor_layout.py`, reused) already
places dining immediately before kitchen, so they *should* seed adjacent. Verify in a render. If they
still come out separated, strengthen seeding so dining and kitchen seed in neighboring front-row cells
(e.g. seed kitchen adjacent to the already-placed dining seed rather than by independent proportional
spacing). Keep it deterministic. **Verify by render** (kitchen cell must border a dining cell).

**D2. Utility/laundry next to the kitchen (problem #7b).** Today `laundry` is zoned private
(`_zone_key` → 2), so it seeds at the back with bedrooms. Architecturally it belongs beside the
kitchen — **on whatever floor each actually appears** (see §0.5: utility/kitchen can be requested on
upper floors via the per-floor paths; do NOT assume ground-floor only). The rule is "seed `laundry`
adjacent to the kitchen seed **on the same floor**", applied per-floor. Options, in order of
preference:
- Make `laundry`/`pantry`/`dirty_kitchen` seed adjacent to that floor's kitchen seed (a "wet cluster"),
  without reclassifying their zone (minimal blast radius). Verify by render on a floor that has both.
- If adjacency-only won't render cleanly, **STOP and report** — do NOT re-zone `laundry` into
  `floor_layout._PUBLIC_ZONE_KEYWORDS` as a workaround (§1.5 decision 4: re-zoning is off the table — it
  touches the frozen `rect` engine and other consumers).

**D3. Master suite on a far, quiet exterior corner (problem #5).** `mask` already seeds bedrooms on
the exterior perimeter via `_exterior_boundary_path`. Enhance `_seed_rooms` so the master bedroom
(name contains "master", classifies as `bedroom`) seeds at the corner of the boundary path FARTHEST
from the entrance (front edge), with its ensuite bathroom (same `suite` id) seeded adjacent to it —
the suite-pairing anchor logic already exists; just bias the master's own seed to the far corner.
Verify by render: master on a back corner, ensuite attached, not fronting the entrance.

**D4. Garage: real shape + road-edge access + 2-car default (problem #8).**
- **Shape/access:** `mask`'s own "Known simplifications" note says garage/staircase get no explicit
  width×depth carve-out — they grow like any room. A garage must (a) be wide enough for its cars
  (`room_specs.garage_dimensions(cars, unit)`) and (b) touch the facing (road) edge so the Phase-C
  driveway can reach it. Seed the garage ON the front/facing boundary and bias its growth to reach its
  real `garage_dimensions` width before other front-row rooms claim that frontage. Verify by render:
  garage on the road edge, roughly its real proportions, driveway connects.
- **Car count = as-typed (DECIDED, §1.5 decision 2):** honor exactly what `parse_garage_cars` returns
  ("garage" → 1, "2 car garage" → 2). **No large-plot auto-2-car logic** — do not add it. No change to
  `garage_cars` resolution in `generate_house.py` is needed.

**D5. (Optional, only if Phase A flagged it) circulation compactness.** If the `mask` cross-spine still
reads as too much corridor on some case, consider capping spine width or dropping the cross-link for
smaller programs (knobs already exist: `_CANDIDATE_SPINE_WIDTH_DELTAS`, `_CANDIDATE_CROSS_LINK`,
`_MIN_ROOMS_FOR_CROSS_LINK`). Verify by render. Skip if Phase A showed circulation is already fine.

**Tests:** add `tests/test_floor_layout_mask.py` cases for each implemented sub-task (kitchen-dining
adjacency by shared-edge; laundry adjacent to kitchen; master on a non-front exterior corner with its
ensuite adjacent; garage touches the facing edge with ≥ its real min width). These assert on real
geometry (coordinates/shared edges), not just presence. **Render + inspect each.**

**Acceptance:** each implemented sub-task verified by render AND test; the 150×150 case now shows
kitchen↔dining adjacent, utility by the kitchen, master on a back corner with ensuite, garage on the
road edge with a driveway; full suite green.

---

## PHASE E — Flip the default to `mask` (its own commit, last)

**Only after Phases B–D are complete, the full matrix is visually verified, and the suite is green both
ways.**

**E1.** In `app/config.py`, change `house_layout_engine: str = "rect"` → `"mask"`. Update that
setting's comment to note the flip date and that `rect` remains available as a one-line rollback
(`HOUSE_LAYOUT_ENGINE=rect`).

**E2.** Confirm `tests/conftest.py`'s `_default_house_layout_engine` fixture (B2) keeps the suite
deterministic regardless of this default — i.e. the committed default no longer silently decides what
tests assert. Run the full suite; it must stay green with the new default.

**E3.** Update `CLAUDE.md`'s `house_layout_engine` / Build-a-House documentation to state `mask` is now
the shipping engine, what it fixes, and that `rect` is the dormant rollback. Add a dated "mask adoption"
entry summarizing Phases A–E (matching the project's detailed-changelog style).

**Acceptance:** default is `mask`; suite green; `CLAUDE.md` updated; rollback is a documented one-liner.

---

## PHASE F — Final verification matrix + regression lock

**F1. Render and visually inspect the full matrix** under the now-default `mask` engine:

| Plot | Floors | Facing | Program | Must show |
|------|--------|--------|---------|-----------|
| 150×150 | 2 | south | the exact reported program | yard+driveway, no bowling alleys, living≥dining, kitchen↔dining, master back corner, compact hall |
| 50×60 | 1 | south | 3 bed/2 bath + LKD+entry | right-sized footprint (little/no yard), clean rooms |
| 30×40 | 1 | south | 2 bed/1 bath + LKD | tight but zero hard violations, real egress |
| 90×40 | 1 | south | 4 bed/2 bath | wide-plot proportions, no slivers |
| 60×80 | 3 | north/east/west | 2 bed+bath/floor | staircase per floor, facing correct, entrance only on ground floor |
| 150×150 | 1 | south | SPARSE: 3 bedrooms only | footprint clamped (no one room ballooning), rest is yard |

For each: zero hard violations printed, and the visual checklist from §2 passes. Save the PNGs to
the scratchpad and reference them in the report.

**F2. Lock regressions.** Ensure every bug fixed in Phases C/D has a test that would fail if it
regressed (geometry-based assertions, per the project's doctrine). Full suite green.

**F3. Final report to the user:** for each of the 7 original problems, before/after PNG evidence and a
one-line status. List anything still honestly imperfect (e.g. cross-floor vertical stacking of
stair/hallway is a known, separately-tracked limitation in both engines — do NOT claim it's fixed).
Do not commit or push unless the user asks.

---

## PHASE G — "every/each floor" room requests (independent; addresses a stated user requirement)

**Why:** the user explicitly wants "kitchen / dining / utility on every floor", but §0.5 shows that
phrasing typed in the shared `extras` box is NOT parsed today (`_FLOOR_REF_RE` needs a digit; "every"
has none), so it is silently honored on the ground floor only. This phase makes the model honor it.
Engine-independent; no dependency on Phases A–F.

**G1.** In `app/pipeline/house_requirements.py::parse_extra_rooms_by_floor`, handle a clause that
contains a room keyword from `_EXTRA_ROOM_CATEGORIES` plus **"every floor" / "each floor" / "all
floors" / "both floors"** (no digit) by emitting an "all floors" request. That function does NOT know
the floor count, so return a sentinel floor number (e.g. `0`, or a dedicated marker) for these, and
EXPAND the sentinel to the real floor numbers in `generate_house.py::run_house_pipeline` where
`total_floors` is known — right where `extra_rooms` is assembled — so `_ensure_requested_rooms_by_floor`
then guarantees the room on each floor. Keep it deterministic; leave the existing numbered-floor
behavior ("kitchen on floor 2") completely unchanged.

**G2.** Make the SAME "every floor" guarantee cover **utility/laundry**: today `mentions_utility` only
injects a utility room on the ground floor. When the user says "utility on every floor", it must appear
on each floor — route it through the expanded `extra_rooms` path (laundry is already in
`_EXTRA_ROOM_CATEGORIES`), not the ground-floor-only `mentions_utility` injection.

**Tests:** `tests/test_house_requirements.py` — "kitchen on every floor" (no digit) → an all-floors
request after expansion; existing numbered-floor tests unchanged. `tests/test_house_pipeline.py` — a
3-floor project with "dining room on every floor" in extras ends up with a dining room on all 3 floors
in the persisted `room_layout_json` (use a FakeProvider whose response omits them, proving the
deterministic guarantee — mirror the existing `_ensure_requested_rooms_by_floor` tests).

**Acceptance:** "kitchen/dining/utility on every floor" typed in the shared extras box yields those
rooms on every floor; numbered requests and the no-request path are unchanged; full suite green.

---

## Deferred follow-up — Concept Layout parity (explicitly NOT in this plan)

The AI "Concept Layout" card (`kaggle_autocad.py` + `conditioning_image.py`, the friend-hosted Kaggle
SDXL+ControlNet vendor) is deprioritized (2026-10-09). It is NOT touched by this plan. Recorded so it
isn't forgotten:
- **It keeps working after the mask flip** — `conditioning_image.render_conditioning_edge_map` already
  reads the same `footprint_bbox` + rects the blueprint does, so it will automatically trace the new
  `mask` geometry. It simply won't show the yard/driveway (we deliberately did not add those to the
  conditioning image) and may diverge cosmetically from the blueprint — acceptable, since the card is
  already labeled "Concept Layout — not a precise blueprint."
- **A future pass** (only if the user wants it) would: add the yard/driveway outline to the conditioning
  edge map, re-tune the friend's Kaggle notebook to match, and re-verify end-to-end against a live
  tunnel. That needs the external notebook + a running session — which is exactly why it is separated
  from this deterministic-only plan.

---

## Honest scope boundaries (do NOT silently attempt these)

- **Cross-floor pixel-exact vertical alignment** of staircase/hallway/plumbing — a genuinely harder
  problem, out of scope for both engines; tracked separately. Don't claim it's solved.
- **A true double-loaded corridor / full adjacency-graph solver** — the `mask` cross-spine is a
  deliberate approximation. Out of scope unless Phase A/D proves it insufficient AND the user approves.
- **Non-rectangular plots / corner-point input** — not in scope; the pipeline only has length×width.
- **Any `rect`-engine geometry change** — forbidden (Golden Rule 1).
- **All earlier "confirm with user" decisions are now RESOLVED in §1.5** (C0 numbers, garage car count,
  upper-floor yard, laundry adjacency-only, AI vendor deferred) — do not re-open them. The "every/each
  floor" request is now an in-scope fix (Phase G), not an open question. (The "yard in the AI
  conditioning image" question is withdrawn — AI vendor out of scope.)

## Suggested commit sequencing (only when the user asks to commit)

1. Phase B (test harness / conftest fixture) — infra, no behavior change.
2. Phase C (C0 footprint coverage + yard/driveway rendering) — engine-independent, shippable on its own.
3. Phase G ("every/each floor" parsing) — independent, small; can land any time.
4. Phase D sub-tasks — one commit each.
5. Phase E (flip default) — its own commit, as the project requires.
6. Phase F regression tests + `CLAUDE.md` — docs/tests.
