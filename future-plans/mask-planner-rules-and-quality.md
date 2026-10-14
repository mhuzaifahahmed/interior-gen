# Plan: rules-driven house planner + mask engine quality gate (rev 2)

## Context

The grid-mask engine (`app/pipeline/floor_layout_mask.py`) is the live default. It was adopted so rooms can bend or wrap to reach an exterior wall, and that goal stays. Real renders still show seven problems:

| # | Symptom | Root cause found in code |
|---|---|---|
| 1 | House is the same ~1,100 sq ft on 56², 150², 300² plots. Capacity dropdown is stuck at 0/1. | `_mask_footprint_dimensions` (L305-355) sizes the footprint from the room list only: preferred sum × 1.755, capped at `max_absorbable`. Plot size only ever *shrinks* it (the 60% cap). The feasibility probe then fails because rooms are cramped. |
| 2 | Unbalanced yards on 40x60 (18.8 front / 1.5 rear / 6.9 side). | `_front_setback_offset` gives front = min(slack, 5.5 m) and puts all remaining slack in the rear. Sides are centred. Setbacks are not tied to plot size. |
| 3 | Living 179 < Dining 393; bathroom 192 sq ft; master bedroom wrapped into an L. | `_fill_leftovers` is explicitly **unbounded** by `max_cells`; its comment saying otherwise is stale. Whole leftover regions are dumped into one room. No hierarchy rule exists. Spec bugs: master's preferred area (120) is below its own minimum (135), and the foyer's preferred area (40) is above its max (38). |
| 4 | Bathroom opens off the living room; dead-end 4x20 hall; 4x3 "HALLWAY" inside a bathroom; stairs reachable only through a bedroom. | `_fill_leftovers` L1484 absorbs any spine-bordered pocket into the Hallway. Stairs are seeded in the back-right corner. Doors are decided only in the renderer, by name rules. The scorer has **no reachability check at all**. |
| 5 | Rooms wrap too freely. | Arm, steal and fill steps add parts with no limit. The scorer never looks at part count or notch size. |
| 6 | Black square on labels; label W×H doesn't match the area; 0.9/1.0/1.9 dimension slices; ENTRANCE label clipped. | `_draw_columns` puts a 15 px solid column at the part's centre, which is where the label sits. The label prints the *largest part's* W×H next to the *summed* area. The dimension chain uses every room's bbox edges on a ~1 ft grid. The driveway paints over "FRONT YARD", and ENTRANCE / zone labels are never width-checked. |
| 7 | Whack-a-mole. | Every test is pinned to `rect`. Mask quality has been judged by eye, with no regression ratchet. |

**Goal.** Decisions come from data, with no external AI:

- a **rules table** of numbers;
- a **planner** that turns plot + program into a sensible plan (footprint, setbacks, per-room target areas, added rooms);
- the mask engine, which only *shapes* rooms inside that plan;
- a **scorer and quality gate** that check the result and block regressions.

**Decided with the user:**

- **Extra area.** First grow rooms toward their target size (never past max), then add non-bedroom rooms from a priority list. Bedroom and bathroom counts never change.
- **Huge plots.** House size is capped per plot-size band and by the program.

**Rev 2:** incorporates the user's six review changes (marked **[R1]–[R6]**), with the adjustments discussed.

## Execution protocol

1. **Step 0:** save this plan as `future-plans/mask-planner-rules-and-quality.md`. It is the document smaller models follow phase by phase.
2. **Each phase:** assign to a subagent, choosing the model dynamically:
   - **Sonnet** by default (gate scaffolding, rules data, rendering fixes, docs);
   - **Opus** only for the planner and bounded fill, shape discipline, circulation, and fallback, announced before launch.

   Then independently verify:
   - review the diff and confirm its scope;
   - run full `pytest`;
   - run the gate report against the baseline;
   - open the contact-sheet PNGs myself.
3. Commit each phase with a specific label. **Ask before every push.**
4. Golden rules:
   - Never touch `app/pipeline/floor_layout.py` (rect).
   - Stop and report if a fix needs more than its phase scope.
   - Never loosen the gate to make a phase pass.

---

## Phase 0: Mask quality gate (anti-whack-a-mole)

- **New `app/pipeline/layout_metrics.py`** (pure). `measure_floor(rects, plot_dims, footprint=None) -> dict`.
- **Matrix.** One shared definition in `tests/mask_quality_matrix.py`:
  - plots 30×40, 40×60, 50×50, 56×56, 60×80, 90×40, 150×150, 150×200, 300×300;
  - 1 / 2 / 3 floors;
  - sparse, typical and dense programs;
  - garage on / off;
  - the 4 facings on 2 representative cases;
  - the exact user-reported cases.
- **Metrics and their gate direction [R1]:**

| Metric | Direction |
|---|---|
| hard violation count, coverage gap, overlaps | **must not worsen**; gaps/overlaps must be 0 |
| **daylight**: habitable rooms with no window-capable exterior edge | **must not worsen**; target 0 |
| unreachable / pass-through-private rooms (live from Phase 4) | **must not worsen** |
| rooms over 1.10 × max area; living < dining | **must not worsen** |
| coverage % | **stay within band** (band range from the rules table once Phase 1 lands; informational before that) |
| each room's area | **stay within [min, max]** |
| front, rear and side yard depths; parts per room; soft counts; hallway dead-end length | **informational** (printed and stored, never fails) |

  So intended changes, like a bigger footprint, can't trip the ratchet.
- **[R1] Daylight check.**
  - Habitable rooms: bedroom, living, kitchen are **hard**; dining, study and family are **soft**.
  - A room passes if **any of its parts** has an edge segment of at least 0.9 m on the **footprint's** outer boundary (not the plot box).
  - Add it to `layout_quality.score_floor_layout`, replacing today's soft `no_egress`, so the engine's best-of-N selection also obeys it.
- **`tests/test_mask_quality_gate.py`.**
  - Forces `house_layout_engine="mask"` explicitly.
  - Runs the matrix against the committed `tests/data/mask_quality_baseline.json`, applying the directions above.
  - The baseline is updated only via `scripts/mask_quality_report.py --update-baseline`, and the baseline diff goes in that phase's commit.
- **`scripts/mask_quality_report.py`.** Prints the metrics table. Writes per-case PNGs plus a contact sheet to the gitignored `scripts/output/quality/`. Reuses the plumbing from `scripts/phase_f_render.py`.
- The rect suite and the conftest rect pin stay unchanged.
- **Done when:** a baseline is committed that captures today's bad numbers (the "before").

## Phase 1: Rules table, spec fixes, single-floor stair

- **New `app/pipeline/planning_rules.py`** (pure data plus lookups; metres, converted via `room_specs.to_plot_unit`).

  `PLOT_BANDS`, keyed by **net** plot area. The numbers are starting estimates and can be tuned:

  | Band | Net area | Target cov | Max cov | Front / rear / side min (ft) |
  |---|---|---|---|---|
  | XS | < 1,500 sq ft | 0.80 | 0.85 | 5 / 3 / 0 |
  | S | 1,500–3,000 | 0.70 | 0.75 | 8 / 5 / 3 |
  | M | 3,000–8,000 | 0.60 | 0.65 | 12 / 8 / 5 |
  | L | 8,000–25,000 | 0.45 | 0.55 | 18 / 12 / 8 |
  | XL | > 25,000 | 0.35 | 0.45 | 25 / 20 / 15 |

  Other rules:
  - `GARAGE_MIN_FRONT_SETBACK_M` = 5.5, only when a garage is requested.
  - `FLOOR_COUNT_COVERAGE_ADJUST`.
  - `CIRCULATION_FRACTION_BY_PRIVATE_COUNT`.
  - Per-category **target** area, between preferred and max.
  - `HIERARCHY_RULES`: living ≥ dining × 1.15, dining capped unless the floor is large, kitchen ≤ living.
  - `SHAPE_RULES`: max 2 parts; the secondary part ≤ 20% of area and ≤ 35% of the primary's matching side.
  - `DAYLIGHT_RULES`: hard and soft categories, minimum window edge 0.9 m.
  - `BONUS_ROOM_PRIORITY`: family lounge, study, walk-in closet (per suite), pantry, laundry, powder room, storage. Each has a max count and a floor eligibility.
  - `UPPER_FLOOR_EXCESS_FALLBACK = "Terrace"`.
- **`room_specs.py`:**
  - Give master's preferred area its multiplier, so preferred ≥ min.
  - Raise foyer's max so it is ≥ preferred.
  - Add `target_area_for_room`.
  - Test that min ≤ preferred ≤ target ≤ max holds for every category.
- **[R2] Single-floor staircase.**
  - `generate_house.py` strips every staircase-category room when `total_floors == 1`. It only injects at L874 when floors > 1, but Gemini can name one.
  - `feasibility.py` never counts one for 1 floor.
  - Add a pipeline test and a gate metric.
- Re-run the gate; update the baseline only where it improved.

## Phase 2: Planner + program fill + bounded leftovers (one phase, behind a flag) [R3]

These ship together. A larger footprint without bounded fill would dump the surplus into one room, reproducing the 3× master bedroom. Bounded fill without planner allocation leaves surplus cells with nowhere to go, causing coverage gaps.

- **Flag.** `Settings.house_planner_enabled: bool = False`.
  - Off: the old `_mask_footprint_dimensions` path is byte-identical.
  - On: the planner path.
  - The gate runs **both** modes: a ratchet on the off-mode baseline, plus a second on-mode baseline.
  - The flag is flipped to `True` only in Phase 6, once the on-mode gate is clean.
- **New `app/pipeline/house_planner.py`** (pure). `plan_building(floors_rooms, plot_dims, facing, garage_cars, total_floors) -> BuildingPlan` returns:
  - footprint W/H;
  - yard offsets (front, rear, left, right);
  - per floor: `room_targets` and `added_rooms`.
- **Footprint area:** `clamp(band.target_cov × net_plot_area, program_min, min(program_ceiling, envelope_area))`.
  - `envelope` = plot minus the band's minimum setbacks.
  - `program_min` = Σ min areas × (1 + circulation).
  - `program_ceiling` = Σ max areas of requested rooms plus all eligible bonus rooms × (1 + circulation).
  - If `program_min` exceeds the envelope, shrink setbacks toward the XS minimums before declaring the plot infeasible.
- **Shape and yards.**
  - Footprint aspect follows the envelope, bounded at about 1:2.
  - Yards start at each side's band minimum, and the slack is shared in proportion to those minimums.
- **One footprint for all floors**, governed by the ground floor; walls stack.
- **Allocation per floor** (usable area = footprint − circulation − stair):
  1. every room's minimum;
  2. raise toward preferred, then toward target;
  3. add bonus rooms in priority order;
  4. raise toward max;
  5. upper floors only: the remainder becomes a Terrace.
- **Hierarchy.** Apply `HIERARCHY_RULES` to the targets. The sum of targets equals the usable area exactly.
- **[R6] Planner-added rooms are labelled clearly.**
  - Each added room dict carries `planner_added: True` and `added_reason` ("spare floor area"), persisted in `room_layout_json`.
  - Visible marking is in Phase 5.
- **Engine.**
  - `layout_floor_with_warnings` gets an optional `plan`. When it is `None` and the flag is on, it builds a single-floor plan itself, so feasibility and kaggle keep working.
  - `target_cells` comes from the plan's targets; `max_cells` is the rules max.
- **Bounded `_fill_leftovers`.**
  - A region is split across adjacent rooms, each only up to `max_cells`.
  - Rounding residue goes to the adjacent room with the most headroom, never into the Hallway unless the cells line up with the spine at full width.
  - Fix the stale comment.
- **Scorer:**
  - hard `oversized` (> 1.10 × max);
  - hard `hierarchy_inverted` (living < dining);
  - soft `over_target`.
- **Wiring.**
  - `layout_engine.py` passes `plan` to mask only.
  - `generate_house.py` builds one `BuildingPlan` before the per-floor loop (L928).
  - The front-yard trim (L792-813) is folded into the planner.
- **[R5] Coverage report.**
  - Print the resulting coverage % and "% of program ceiling used" for 150×200 and 300×300 (typical and sparse programs).
  - **Flag** anything under about 12% in the phase report (informational, not a gate failure), since a 3-bed house on an estate is meant to be capped.
- **Check (flag on):**
  - 40×60: balanced setbacks; living > dining; bathroom ≤ 80 sq ft; master within its cap.
  - Every room ≤ 1.10 × max.
  - The footprint grows with the plot band up to the program ceiling.

## Phase 3: Shape discipline (mostly one rectangle, at most a small notch)

- **New `_rectify_rooms`** in the mask engine, run after growth and before `_decompose`.
  - A room keeps its primary rectangle plus at most one secondary part that meets `SHAPE_RULES`. This keeps a small bend that reaches an exterior wall.
  - Other secondary parts are reassigned to adjacent rooms with headroom, preferring results that stay rectangular.
  - Exact tiling must hold: every reassigned cell gets an owner.
- Keep egress rooms seeded on the exterior, so the primary rectangle carries the window and daylight stays satisfied.
- **Scorer:**
  - hard `wrapped` (> 2 parts, or secondary > 35%);
  - soft `notch`.
- Widen best-of-N from 4 to about 8–12 (seed-order and band-depth variants).
- **Check:**
  - The L-wrapped master is gone.
  - Parts per room ≤ 2 everywhere.
  - Daylight has not regressed.

## Phase 4: Circulation (reachable from entry, no pass-through bedroom or bath)

- **New `app/pipeline/door_graph.py`** (pure), with door logic moved from `blueprint_svg.py`:
  - `_shared_edge_between_rooms`, `_compute_hallway_adjacent`, `_should_suppress_direct_door`, `_should_suppress_garage_direct_door`, `_front_door_opening`;
  - plus the rule that a common bathroom never opens into living, dining or kitchen.

  `build_door_graph(rects, plot_dims, facing, floor_number)` is used by the PNG renderer, the DXF renderer (this fixes its missing garage-door suppression), `kaggle_autocad._door_specs_for_floor`, and the scorer.
- **Regression guard:** PNG and DXF output for existing test fixtures must stay identical, except where the new bathroom rule or the garage-door parity changes it.
- **Scorer:** breadth-first search from the entry (foyer or front-door room; the staircase on upper floors):
  - hard `unreachable`;
  - hard `pass_through_private`;
  - soft `dead_end_hall` (an arm longer than 1.5 m serving no door).
- **Engine:**
  - Build a spine when there are ≥ 2 private rooms, connected to the foyer or public band.
  - Seed common bathrooms and the staircase adjacent to the spine (staircase at the public/private boundary).
  - Prune stubs that serve no room into their neighbours, within max.
- **Check:**
  - 0 unreachable and 0 pass-through rooms across the matrix.
  - The in-bathroom hallway and the stairs-through-bedroom case are gone.

## Phase 5: Rendering and labels (`blueprint_svg.py`, mirrored in `blueprint_dxf.py`)

- **Label text:**
  - A single-part room prints its true W×H plus area.
  - A multi-part room prints its area only.
  - The DXF matches.
- **Columns:** skip or shift any column that would intersect the measured label box; skip columns in hallways and bathrooms. Same in the DXF.
- **`_carve_rooms`:** fill the concave-corner bite.
- **Dimension chains:**
  - Use only edges of parts on that exterior wall, along the footprint, plus the yard segments.
  - Merge segments under 0.6 m.
  - Skip a label on collision.
- **Draw order and fitting:**
  - Draw yard and zone labels after the driveway.
  - Width-check the zone labels and "DRIVEWAY".
  - Clamp ENTRANCE inside the foyer part, or move it to the front yard.
- **[R6] Planner-added rooms:**
  - a small "+" after the room name;
  - a title-block footnote, "+ added by planner to use spare floor area", listing those rooms;
  - a matching DXF text note.
  - No per-label suffix; that would clutter narrow rooms.
- **Tests:**
  - no wall pixels inside a label box;
  - label text equals the true dimensions;
  - no dimension segment under 0.6 m;
  - the footnote lists the added rooms.
- **Check:** re-render the user's 40×60 and 150×200 cases and inspect them directly.

## Phase 6: Capacity table, end-to-end, flag flip

- **`feasibility._geometrically_placeable_area`:** call the engine with a plan whose footprint is forced to `test_area`, so the probe tests the program, not yard policy.
- **Gate assertion:** a 1-floor 50×50 or larger reports ≥ 3 bedrooms and ≥ 2 bathrooms per floor.
- **End to end:** run `run_house_pipeline` with a FakeProvider for the user's cases (40×60/1F, 150×200/2F, 56/150/300 squares) and inspect the PNGs.
- **Flip the flag:** set `house_planner_enabled` to `True` only if the on-mode gate is clean. That on-mode baseline then becomes the primary one.

## Phase 7: Fallback chain + docs [R4]

- **In `layout_engine.layout_floor_with_warnings`:** if the mask result's best candidate still has hard violations:
  1. Retry mask with alternate footprint shapes from the planner (swapped aspect, squarer, longer; same area).
  2. If every attempt is still hard-failing, run rect, and **use it only if** it has strictly fewer hard violations including daylight.
  3. Otherwise keep the best mask result.
- **Logging.** Record `layout_path` per floor: `mask` / `mask_alt_shape:<n>` / `rect_fallback` / `mask_best_effort`.
  - Log it at INFO.
  - Store it in `meta_json`.
  - Include it in the gate report.
  - Known caveat: rect sizes its own footprint, so a rect-fallback floor may not match the planner's yards. That makes it a last resort, visible in logs.
- **L/U footprints:** add them only if the gate's daylight metric shows rectangles still failing on large plots after steps 1–3. Otherwise record "not needed" with the evidence.
- **Docs:** add a CLAUDE.md v30 entry and mark status in the `future-plans/` file.

---

## Critical files

- New:
  - `app/pipeline/planning_rules.py`
  - `app/pipeline/house_planner.py`
  - `app/pipeline/layout_metrics.py`
  - `app/pipeline/door_graph.py`
  - `tests/test_mask_quality_gate.py`
  - `tests/mask_quality_matrix.py`
  - `tests/data/mask_quality_baseline*.json`
  - `scripts/mask_quality_report.py`
- Modified:
  - `app/pipeline/floor_layout_mask.py`
  - `app/pipeline/layout_quality.py`
  - `app/pipeline/room_specs.py`
  - `app/pipeline/layout_engine.py`
  - `app/pipeline/generate_house.py`
  - `app/pipeline/feasibility.py`
  - `app/pipeline/blueprint_svg.py`
  - `app/pipeline/blueprint_dxf.py`
  - `app/providers/kaggle_autocad.py`
  - `app/config.py` (flag)
- Untouched: `app/pipeline/floor_layout.py` (rect).

## Reuse

- `room_specs` (min, preferred and max area; `min_width_for_room`, `garage_dimensions`, `staircase_dimensions`, `classify_room_category`, `to_plot_unit`).
- `floor_layout._sort_key`, `_arrange_suites`, `room_parts`.
- The mask engine's `_decompose`, `_steal_arm`, `_grow_arm`, `_public_band_rows`, `_entrance_cell`.
- `layout_quality.score_floor_layout`.
- `blueprint_svg._largest_part_view`, `_free_intervals`.
- `scripts/phase_f_render.py` scaffolding.

## Verification (every phase, and at the end)

1. `pytest -q`: the full suite is green.
2. `pytest tests/test_mask_quality_gate.py`: passes per the metric directions, with the baseline diff in the commit.
3. `python scripts/mask_quality_report.py`: read the table and open the contact sheet plus the user cases myself.
4. End target across the matrix:
   - coverage within band;
   - balanced setbacks;
   - 0 hard violations (daylight included);
   - every room ≤ 1.10 × max;
   - living ≥ dining;
   - ≤ 2 parts per room;
   - 0 unreachable or pass-through rooms;
   - no single-floor stair;
   - correct labels and dimension chains;
   - added rooms marked;
   - realistic capacity dropdown;
   - `layout_path` logged.


## Running the tests (speed tiers)

The full suite had grown to ~30 min (two `tests/test_house_planner.py` tests alone were ~16 min because
they recomputed `layout_building()` for every floor of every matrix case). Tiers:

- **Per change (minutes):** `./.venv/Scripts/python.exe -m pytest -m fast -q`
  - ~10 representative gate cases in BOTH planner modes (30x40, 40x60 south + east, 50x50 2f, 60x80 dense
    garage, 60x80 3f, 90x40, 150x150 dense 2f, 150x200 2f, 300x300) plus the pure mask / planner / door
    graph / scorer / quality / room-spec / planning-rule unit files (list: `_FAST_MODULES` in
    `tests/conftest.py`). Same assertions as the full gate, so a worsened ratcheted metric still fails.
- **Whole gate:** `./.venv/Scripts/python.exe -m pytest tests/test_mask_quality_gate.py -q`
- **Before each commit:** `./.venv/Scripts/python.exe -m pytest -q` (full suite, never skipped).
- **Matrix report in parallel:** `python scripts/mask_quality_report.py --scorecard --planner both --no-render --jobs 4`
  (identical output to `--jobs 1`).
- **Proving a speedup is output-identical:** `python scripts/mask_output_dump.py OUT.json` in a clean
  `git worktree` of HEAD and in the working tree, then `cmp` the two files (rects + warnings for all matrix
  floors, planner off and on, plus the capacity table).

`tests/mask_quality_matrix.planner_building(case)` computes a case's `layout_building()` once per test
session; use it (rather than calling `layout_building` per floor) in any new matrix-wide test.
