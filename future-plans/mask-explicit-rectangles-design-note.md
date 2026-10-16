# Design note: "explicit rectangles + bend" engine (evaluation, NO production code)

Status: proposal / decision document, written at HEAD `dfe798a` (2026-10-16). Nothing here is built.
Measurement script: `scripts/rect_baseline_on_matrix.py` (read-only). Output:
`scripts/output/quality/rect_baseline_on_matrix.txt` (+ `.json`, gitignored).

## Summary for the decision maker (read this first)

1. **Question:** should the planner draw every room as an explicit rectangle, with the mask engine only
   "bending" rooms that lack a window, instead of today's seed -> grow -> fill -> repair pipeline?
2. **Where we are:** mask engine with the planner on gives **3 of 35** fully clean floors and 13 of 35
   floors with zero hard violations. Today's live default (planner off) gives **0 of 35** clean floors.
3. **What I measured:** the old rectangle engine (`floor_layout.py`), run on the same 35 floors and
   scored the same way, also gives **0 of 35** clean floors. Run as-is inside the planner's footprint
   it gets **4 of 35** zero-hard floors, against the mask engine's 13. It is fast (about 1 s for all 35
   floors, against 135 s on 6 cores for the mask engine).
4. **The rectangle engine fails in the predicted ways:** 25 bedrooms/living rooms/kitchens with no
   window (mask: 0), 85 narrow rooms (mask: 26), and 69 rooms longer than 2.8:1 (mask: 18).
   It does no worse on circulation.
5. **So an unchanged slice-and-dice packer would be a step backwards.** The proposal only makes sense
   with a different packer. That packer must give every room its minimum width and depth, and give
   every habitable room an outside wall, when the rooms are first placed.
6. **That packer is buildable.** The design is below: strip packing along the spine, with a
   "suite slot" (bedroom on the outside wall, ensuite on the spine side). Whether it reaches the
   clean-floor bar is unproven; my estimate is a 30–40% chance of reaching 12/35.
7. **Recommendation: build the cheap prototype first, on a separate branch `proto/explicit-rectangles`.**
   The packer comes first with no bending (about 2–3 agent runs, 1–2 calendar days). Strict stop
   numbers are in §4. The current engine stays in charge until the prototype beats it on those
   numbers.
8. **If the prototype fails its gate,** stay incremental with the cheap targeted fixes in §9. I expect
   them to reach roughly 5–8 clean floors (estimate), not 12.

---

## 1. Measured baseline: rect engine vs mask engine, same 35 floors, same scorer

Command: `python scripts/rect_baseline_on_matrix.py --jobs 6`.
Every mode is scored by `mask_scorecard.score_floor` (which calls `layout_metrics.measure_floor`), the
same machinery as `mask_quality_report.py --scorecard`. "Clean" means the scorecard's own `clean_all`:
all 8 scored checks plus circulation.

**The four modes:**

| Mode | What it is | Scored with |
|---|---|---|
| `rect_self` | `floor_layout.layout_floor_with_warnings`, exactly as production would run it: raw program, whole plot. Rect sizes its own footprint (`_footprint_dimensions`, front-flush, sides centred, no planner yards). | Standard thresholds (`planner=False`), like the flag-off report |
| `rect_in_plan` | Packer-only proxy. `house_planner.plan_building` (scale 1.0) supplies the footprint, yards, added rooms and targets. Rect tiles exactly that box: its own footprint shrink is disabled by an in-process monkeypatch, and the weights are each room's target minus its reserved minimum. | Planner thresholds, plan `room_max` overrides, remainder rooms excluded |
| `mask_off` | Mask engine, `house_planner_enabled=False` (the HEAD default) | Standard thresholds |
| `mask_on` | Mask engine with `layout_building`, including the Step 5.2 scale search | Planner thresholds |

**Results:**

| metric | rect_self | rect_in_plan | mask_off | mask_on |
|---|---|---|---|---|
| **fully clean floors (clean_all)** | **0/35** | **0/35** | **0/35** | **3/35** |
| zero-hard floors (hard 0 and no daylight) | 19/35 | 4/35 | 3/35 | 13/35 |
| check coverage | 12 | 35 | 13 | 16 |
| check yards | 26 | 35 | 23 | 35 |
| check sizes | 0 | 1 | 6 | 15 |
| check hard | 19 | 4 | 3 | 13 |
| check parts (<= 2) | 35 | 35 | 1 | 27 |
| check single-floor stair | 35 | 35 | 35 | 35 |
| check bedroom aspect | 15 | 30 | 23 | 29 |
| check stair cap | 19 | 23 | 35 | 31 |
| check circulation | 21 | 26 | 17 | 31 |
| all-9 floor-checks (/315) | 182 | 224 | 156 | 232 |
| hard violations (measure_floor) | 18 | 119 | 92 | 30 |
| hard: narrow | 16 | 85 | 85 | 26 |
| hard: door_too_narrow | 2 | 13 | 7 | 4 |
| hard: small_area | 0 | 15 | 0 | 0 |
| hard: degenerate | 0 | 5 | 0 | 0 |
| hard: foyer_shallow | 0 | 1 | 0 | 0 |
| **daylight HARD rooms / floors** | **12 / 7** | **25 / 17** | 2 / 2 | **0 / 0** |
| daylight SOFT rooms | 2 | 3 | 0 | 0 |
| oversized rooms (histogram; floors) | 164 (35) | 62 (33) | 49 (29) | 21 (14) |
| stair_oversized (floors) | 16 | 12 | 0 | 4 |
| bedroom aspect > 2.5 (rooms) | 27 | 7 | 13 | 6 |
| rooms with largest part > 2.8:1 ("bowling alley") | 54/227 | 69/267 | 21/227 | 18/230 |
| rooms 2.0–2.8:1 | 49 | 38 | 37 | 41 |
| unreachable rooms | 0 | 0 | 0 | 0 |
| pass-through private rooms | 34 | 25 | 41 | 4 |
| entry-missing floors | 3 | 3 | 0 | 0 |
| floors failing ONLY daylight | 0 | 0 | 0 | 0 |
| runtime, all 35 floors | 0.9 s (1 process) | 1.2 s (1 process) | 3.3 s wall (6 procs) | **135 s wall (6 procs)** |

**What is not comparable, said plainly:**

- **`rect_self` coverage and yards** reflect rect's own footprint policy, not the planner's. Rect
  shrinks the house only when a probe layout is clean, otherwise it fills the plot. It places the house
  front-flush (front yard = wall thickness only), so the yards check passes trivially on many floors.
  Its 164 "oversized" rooms are mostly because it fills a footprint that is too big for the program.
- **The "hard" counts here are `measure_floor` hard violations, which are scorer-only kinds.** The mask
  engine's own count also includes `wrapped`, `bedroom_aspect`, `stair_oversized` and circulation. That
  is why Step 5.1 reports 117 for mask_on and this table reports 30. The scorecard checks (parts,
  aspect, stair_cap, circulation) carry those kinds instead, so `clean_all` is identical either way.
- **`rect_in_plan` coverage 35/35 vs mask_on 16/35 is a footprint difference, not a packing
  difference.** `rect_in_plan` uses the scale-1.0 plan with every bonus room kept. mask_on's
  `layout_building` drops bonus rooms it cannot fit (for example the Media Room on all 60x80_3f
  floors), re-plans a smaller footprint, and lands under the band. Example: 60x80_3f is 28.7% in the
  plan and 23.7% after the drops. This matters for the proposal: **a packer that can fit the bonus
  rooms fixes most of the coverage CHECK failures for free.** Coverage is the #2 blocker (19 floors).
- **`rect_in_plan` is sensitive to the weight scheme.** Using raw targets as weights (no minimum
  subtracted) gives 0/35 zero-hard instead of 4/35 (narrow 79, small_area 12, degenerate 5).
- **The rect engine has no minimum-width or minimum-depth guarantee.** On the 80x20 ft footprint of
  90x40_typical, its fixed hallway is 9.8 ft deep and produces negative-height bedrooms (`degenerate`).
  It is a lower bound for "any rectangle packer", not a prediction for a well-designed one.

**What the data says:**

- Daylight is the rect engine's structural weakness, confirmed (25 hard daylight rooms vs 0). The mask
  engine's "seed on the exterior" design really is why its daylight is perfect.
- The only thing rect does clearly better is parts: it has no multi-part rooms by construction.
- No mode has a single floor that fails *only* on daylight. Rect's daylight failures always come with
  narrow/oversized failures on the same floor. So "pack, then bend for daylight" only works if the
  packer itself is much better than rect at widths and sizes.

---

## 2. Proposal

**Pipeline:** `house_planner.plan_building` (unchanged: footprint, yards, tiers, targets, ceilings,
added rooms) → new `house_packer.pack_building(plan, floors_rooms, facing, garage_cars)` → per-floor
**explicit rectangles** in CORE coordinates (front at low y, same convention as the mask engine) →
**bend phase** (bounded mask moves, only for rooms lacking a window edge) → existing
`_apply_plan_transform` → renderers.

### 2.1 Packer candidates considered

| Algorithm | Guarantees min width/depth? | Daylight by construction? | Verdict |
|---|---|---|---|
| Zoned slice-and-dice (today's `floor_layout._layout_floor_core` with min-dim clamps) | Only by post-clamping, and it cascades | No: interior cuts bury rooms (measured: 25 rooms) | Rejected. The measurement above is this option, minus the clamps. |
| Guillotine with shape constraints | Yes, per cut | Only if every cut tree keeps each habitable leaf on the boundary; the search explodes | Too big a search for a deterministic, sub-second engine |
| Constraint search over full templates (double-loaded / L / courtyard) | Yes | Yes | The right idea but too much for v1. Kept as the second template family (§2.2, T3). |
| **Strip packing along a spine (recommended)** | **Yes: strip depth ≥ max min-depth of its rooms; each slot width ≥ its room's min width, enforced before placement** | **Yes: every strip has one long side on the exterior wall and the other on the spine/hall** | **Recommended** |

**Why strip packing.** The data says 50% of final violations start in growth (all 29 `narrow`, as
measured in Step 5.1) and 41% start in fill (all `stair_oversized`, 20/26 `oversized`). Strip packing
removes both stages entirely. Width and depth are arithmetic on known strip dimensions, and there are
no free cells left to "fill", because every square foot is assigned to a named room or sink during the
packing arithmetic. It also mirrors what the mask engine already does successfully: a spine
(`_reserve_spine`), a public band (`_public_band_rows`) and suites (`floor_layout._arrange_suites`).
The difference is that each room becomes an explicit slot instead of a growing region.

### 2.2 The packer, concretely

**Per building (once):**

1. **Spine position.** Choose one spine (hallway) position in core coordinates for the whole building.
   It is a vertical (front-to-back) corridor `_planner_hallway_width_cells`-equivalent wide
   (1.2 m base), at an x chosen so the two side strips have depths in
   `[max(min_depth of strip rooms), 1.6 × that]`. When no x satisfies this (footprint too wide), switch
   to template **T2**: a horizontal cross-corridor with a rear strip and side wings. For a footprint
   whose cross dimension is too small for two strips plus a spine (for example 90x40's 80x20 ft), use
   template **T1 single-loaded**: one strip plus a spine against the opposite wall.
2. **Stair block.** Fixed, identical on every floor (see §5). Its cells are reserved before any room.
3. **Wet stack.** For each strip, the spine-side half of every slot is the "wet band". Bathrooms,
   ensuites, laundry and pantry go there, so they stack floor to floor by x-range (see §5).

**Per floor:**

1. **Public band (ground floor).** Depth `D_pub` = max of (each public room's min depth, the garage's
   `garage_dimensions` depth, Σ public targets / footprint width). Capped as today by
   `_MAX_PUBLIC_BAND_FRACTION`, and by the planner directly instead of by growth. The band is one
   front strip that touches the front wall, so every public room has daylight on the facing edge.
   - Slot order follows `floor_layout._PUBLIC_ORDER_RANKS`.
   - The garage takes its exact `garage_dimensions(cars)` width at a corner (the existing
     `_preclaim_garage` intent, now exact).
   - The foyer takes `min_width × min_depth` (from `min_width_for_room` / `min_depth_for_room`) at the
     entrance cell (`_entrance_cell`).
   - Every other room gets width `max(target / D_pub, min_width)`.
2. **Back strips.** Rooms go in order from `_arrange_suites` (suites kept as pairs).
   - **A plain room** gets a full-depth slot: width = `max(target / D_strip, min_width)`, depth =
     `D_strip`. It touches the exterior wall on its long side and the spine on the other.
   - **A suite slot** (bedroom + ensuite) is one column of width `W = max(bed_min_w, (bed_t + bath_t) / D_strip)`.
     - The ensuite is a rectangle at the spine-side corner, with width ≥ bath min width (4.9 ft) and
       depth `bath_t / bath_w`, which must be ≥ bath min depth (8.1 ft).
     - The bedroom is the rest of the column: an L whose primary rectangle spans exterior wall to
       spine, plus one notch. The notch must satisfy `layout_quality.secondary_part_ok`
       (≤ 20% of area, ≤ 35% of the primary's matching side).
     - If the notch fails that rule, use a plain rectangle stack instead: bedroom on the exterior side,
       bath + closet on the spine side, the closet absorbing the remainder of the spine-side band.
   - This puts every bedroom on an exterior wall by construction, keeps its ensuite on a shared wall
     (fixes the 9 lost suite contacts from §3 of the Step 5.1 diagnostic), and puts wet rooms on the
     spine side.
3. **Surplus by construction.** Before any coordinate is fixed, compute
   `S = footprint − stair block − real spine area − Σ targets`. It is exact arithmetic, not a pocket
   found after growth. Resolve it in this order:
   1. raise rooms toward their plan max (headroom = Σ(max − target));
   2. give the rest to the floor's named sink slot (§5);
   3. if `S` still exceeds the sink's own max, shrink the footprint: reuse the Step 5.2
      `footprint_scale` re-plan, which becomes deterministic because `S` is known up front;
   4. if `S < 0`, see §3 (infeasibility).
4. **Snapping.** Compute continuous slot coordinates, then snap them to the existing grid (`_grid`).
   - Width and depth round **up** for the min checks (`math.ceil(min / cell − 1e-9)`, the convention
     `_preclaim_garage` / the foyer code already use).
   - The last slot in each strip absorbs the rounding residue, and only if it stays ≤ its max. If it
     does not, the residue goes to the sink slot.

**Candidates.** A small, deterministic set per floor, scored by the existing
`layout_quality.score_floor_layout(..., planner_checks=True)` and selected with the existing
`_pick_circulation_safe` / `_gate_vector` safety net:

- spine x positions {centre, ±1 slot};
- template {T1, T2, T3 = L/courtyard on L/XL bands};
- strip assignment (which suites go left or right; master farthest from the entrance, mirroring
  Phase D3);
- sink at {rear, side end}.

That is ≤ 48 candidates. Each is pure arithmetic and scores in milliseconds.

### 2.3 Bend phase (only for daylight, bounded)

The packer's output becomes the initial `owner` / `cells_by_room` grid, built by rasterising the slots,
so the mask machinery operates on it unchanged. The only bend move is in §4.

### 2.4 Worked example: 60x80_dense_garage_1f (hand arithmetic, estimate)

**Plan:** footprint 49.0 × 52.9 ft (south facing, M band, standard tier), front yard 18.8 / rear 8.3 /
side 5.5, planned circulation 389 sq ft.

**Targets (sq ft):** Entry 38, Garage 400, Living 349, Dining 167, Kitchen 150, Laundry 48, Master 272,
3 bedrooms 161 each, 3 baths 58 each, Study 117.

**Public band:**

- Public targets sum to 1,104, so `D_pub` = 1104 / 49 = **22.5 ft**. This is ≥ the garage depth and
  ≥ living's min depth of 13.8.
- Garage 17.7 × 22.5 ≈ 400.
- Entry 3.9 × 9.8 at the door, with the rest of its column (3.9 × 12.7) given to the hall/coat closet.
- Living 349 / 22.5 = 15.5 wide (≥ 10.8). Dining 7.4 → **below its 8.9 min width**. Kitchen 6.7 →
  **below its 7.9 min width**.
- The band row needs widths 17.7 + 3.9 + 15.5 + 8.9 + 7.9 = 53.9 ft > 49. So **T-band infeasible at
  targets**. The packer applies the §3 ladder: move Kitchen into the back strip next to the spine head
  (allowed: kitchen is a public-zone room, but daylight-hard, so it takes an exterior slot at the strip
  front). Then the band is 17.7 + 3.9 + 15.5 + 8.9 = 46.0 ≤ 49, and the 3.0 ft residue goes to Living.

**Back zone:**

- Back depth = 30.4 ft. Vertical spine 3.9 ft at the centre gives two strips 22.5 ft deep (exterior
  side wall → spine).
- Left strip (length 30.4):
  - Master suite: column W = (272 + 58) / 22.5 = 14.7. Ensuite 4.9 × 11.8 at the spine side; master
    primary 9.8 × 22.5 plus a notch 4.9 × 10.7. The notch is 52 / 324 = 16% of area and 10.7 / 22.5 =
    48% of the side, so it **fails the 35% side rule**. Fall back to the stack: master 14.7 × 14.6
    (215) on the exterior + ensuite 4.9 × 7.9 + closet 9.8 × 7.9 on the spine side. **The master then
    misses its 272 target by 57.** The packer records this as `under_target` (soft) or gives it the
    closet (planner_added walk-in, already a BONUS_ROOM entry).
  - Suite 2: 219 / 22.5 = 9.7 wide.
  - Total 24.4 of 30.4 ft; Bedroom 3 needs ≥ 8.9 and only 6.0 remains. **Left strip overflows** →
    Bedroom 3 moves to the right strip.
- Right strip:
  - Kitchen 7.9 at the front: 150 / 22.5 = 6.7 → clamp to 7.9 × 19 (spine side 7.9 × 3.5 to the
    laundry wet band).
  - Bedroom 3 with Bath 3: 9.7. Bedroom 4: 8.9 (161 / 22.5 = 7.2, clamped to 8.9 × 18.1, with
    8.9 × 4.4 at the spine side to Study / Laundry).
  - Total 26.5 ≤ 30.4. The 3.9 ft residue goes to the Study slot (117 / 22.5 = 5.2 wide → **narrow**,
    so Study takes the remaining 8.9 × 13 and Laundry 4.9 × 9.8 in the wet band).

**Surplus:** footprint 2,593 − spine 119 − targets 2,198 = **+276 sq ft**. Headroom to max ≈ 150 goes to
rooms; about 126 is left with no sink on a standard-tier ground floor. So the §2.2 step 3 ladder shrinks
the footprint (≈ 0.95 scale) instead of dumping it into a bedroom.

**What this example shows:**

- On this dense program, **the packer has to make real trade-offs:** the kitchen leaves the band, the
  master is under target, and the footprint shrinks. Every trade-off is explicit and every room meets
  its minimum width and depth.
- Today mask_on on this floor fails on `bedroom_aspect, narrow, oversized, pass_through_private,
  too_many_parts`. Here, every room has an exterior edge except the wet band rooms (bath / laundry,
  which are not daylight-hard). Study is daylight-soft and has the exterior wall.
- The arithmetic is plausible but tight, which is why the prototype must measure it rather than
  assume it.

---

## 3. HOW IT PREVENTS NARROW ROOMS

**Mechanism.** Width and depth are inputs to packing, not outcomes of growth:

1. **Strip depth.** Every strip's depth `D` is chosen ≥ `max(min_depth_for_room(r))` over its rooms
   (`room_specs.min_depth_for_room`).
2. **Slot width.** A slot's width is `max(target / D, min_width_for_room(r))`, computed before any
   coordinate is placed.
3. **Suite slots.** Each sub-rectangle is checked against its own room's min width and depth (ensuite:
   ≥ 4.9 × 8.1 ft in this matrix).
4. **Snapping.** Min dimensions round up to whole cells.
5. **Self-check.** The packer asserts every room's largest part ≥ `min_width_for_room × _AREA_TOLERANCE`
   (the exact test `layout_quality.score_floor_layout` applies for `narrow`). A candidate that fails is
   discarded, never scored-and-kept.

So `narrow` cannot appear at packing time. All 29 current `narrow` violations originate in
`_grow_rooms` (Step 5.1 §4), a stage that no longer exists.

**Infeasibility handling.** A strip "overflows" when Σ(min widths) > strip length. The packer then
tries, in order and deterministically:

1. Move the last non-suite room to the other strip, or to the public band if it is a public-zone room.
2. Switch template: T1 ↔ T2, or spine ±1 slot.
3. Drop planner-added bonus rooms, lowest priority first. This is the existing
   `layout_building(exclude_bonus=...)` re-plan loop, reused as-is.
4. Grow the footprint toward `min(program_ceiling × CEILING_PACKING_FRACTION, envelope)`.
   - For this to happen, `house_planner.plan_building` gains an optional `footprint_scale > 1.0`,
     bounded by the envelope. Today only scales < 1 exist.
5. Fall back to the current mask engine for that building. It is recorded as
   `layout_path="packer_fallback_mask"`, counted, and gated as the **fallback rate**.

Never: shrink a room below its minimum, or emit a room that fails `narrow`.

**The bend phase may not re-create narrow rooms.** Every bend move (§4) is accepted only if both
rooms' largest part still has min(w, h) ≥ `min_width_for_room`. This is the existing
`_usable_width(cells, min_widths, i, cell_w, cell_h)` check already used by `_rebalance_over_max`, and
the existing `_rectify_rooms` "never loses a usable primary width it had" guarantee. The move must also
not raise the floor's `narrow` count in a rescore (`_gate_vector` comparison against the pre-bend
candidate). A bend that fails either test is not applied.

**Worked example (60x80_dense_garage_1f, §2.4).**

- Dining (7.4 ft at target) and Kitchen (6.7 ft) would have been narrow. The packer clamps Dining to
  8.9, and the band overflows, so Kitchen moves to the back strip at its 7.9 min width.
- Bedroom 4 (7.2 ft at target) is clamped to 8.9 × 18.1, and the excess depth becomes wet-band /
  study area.
- Study (5.2 ft) becomes 8.9 × 13.
- Today's mask_on output for this floor has a hard `narrow`; a 9.3 × 26.4 ft master was measured on it at
  44132c5 (`mask-surplus-and-path-findings.md` §1). In the packer the master is 14.7 × 14.6.

---

## 4. HOW ROOMS STILL GET WINDOWS

**At packing time.** Every habitable room (`DAYLIGHT_HARD_CATEGORIES` bedroom/living/kitchen,
`DAYLIGHT_SOFT_CATEGORIES` dining/study/family) gets a slot with one long side on an exterior strip
edge:

- the public band's front wall;
- the side or rear wall for back strips.

This is a slot-assignment rule, not luck. Wet rooms (bath, laundry, pantry, store, closet) and the
stair are assigned to spine-side, wet-band or interior positions, because they do not need daylight.
The slot rule is that the exterior edge length is ≥ `DAYLIGHT_MIN_EDGE_M` (0.9 m). For a full-depth
slot this is the slot width (≥ min width ≥ 1.2 m), so it holds automatically.

**The rooms that can still lack a window** are the cases the strip templates cannot avoid:

- a room squeezed to a strip end behind the stair block;
- the T2 template's front-row-of-back-zone rooms;
- a public room moved out of the band when it has no exterior strip-front slot left.

The measurement says this is not hypothetical: a packer without this slot rule (`rect_in_plan`) buries
25 habitable rooms.

**The allowed bend move (one kind only).** "Exterior arm swap" for room R with no ≥ 0.9 m exterior edge:

1. **Pick a neighbour N.** N must share a wall with R and own an exterior edge of ≥ 2 × 0.9 m (N keeps
   ≥ 0.9 m after the move).
2. **Take an arm from N.** Carve from N an arm k cells wide (k = `ceil(0.9 m / cell)`, plus 1 if R's
   min width needs it) running from the R/N wall to the exterior. Use the existing `_grow_arm` /
   `_steal_arm` geometry.
3. **Pay N back.** Give N back exactly the same number of R's cells, farthest from the arm. Use the
   existing `_give_back(r_cells, frag, donor_rest, k, cols, rows)`. Area is preserved for both rooms.
4. **Accept the move only if all of these hold:**
   - R becomes primary + at most one secondary part satisfying `secondary_part_ok`
     (`SHAPE_RULES`: ≤ 2 parts, secondary ≤ 20% area and ≤ 35% side), and N likewise;
   - both rooms stay within [min, max], with plan overrides via `_planner_targets`;
   - both keep `_usable_width`;
   - N keeps its own daylight edge;
   - the spine, stair block and garage are never donors or recipients (the `_rectify_rooms` `no_receive`
     set, extended to donors);
   - no front-band crossing (row_lo / row_hi as in `_rectify_rooms`);
   - the rescored `_gate_vector` (which includes `narrow`, `wrapped` via the score-only kinds, and
     circulation) is no worse than before, and R's `no_daylight` is gone.
5. **Bounds.** At most one bend per room, at most `n_rooms` bends per floor, and each bend is tried
   against at most 4 neighbours × 2 arm positions. Moves run in deterministic room order.

Everything else stays rectangular. There is no growth, no fill, no rebalance and no rectify.

**Worst case: no legal bend exists.**

1. Re-run the candidate set with the room forced to another strip / template / spine position. This
   counts against the ≤ 48 candidate budget.
2. If every candidate still has a habitable room without daylight, the floor keeps its best packer
   candidate **with a hard `no_daylight`**, visible in warnings and the scorecard. It is never hidden.
3. The per-floor safety net then compares that candidate against the current mask engine's result
   (`_gate_vector`, the same rule `_layout_with_plan` uses for its reference). It takes the mask result
   if the packer is worse on any ratcheted metric. This is counted in the fallback rate.

**Worked example: 150x200_2f_reported floor 1.**

- Footprint 38.9 × 50.2 ft (XL / estate, south). Program: garage, entry, living, dining, kitchen,
  master suite, bedroom 1 suite, stair.
- Public band `D_pub` = (50 + 246 + 425 + 180 + 179) / 38.9 = **27.8 ft**.
  - Garage 10.0+ wide × 24.6 deep.
  - Entry 3.9 × 12.8.
  - Living 15.3 (≥ 10.8).
  - Dining 6.5 → clamped to 8.9. The band now needs 10 + 3.9 + 15.3 + 8.9 = 38.1 of 38.9. Kitchen does
    not fit, so it goes to the back-strip front slot with an exterior side wall.
- Back depth 22.4 ft. Two strips of 17.5 ft plus a 3.9 ft spine; the stair block sits at the spine head.
- Master suite column (312 + 61) / 17.5 = 21.3 ft. Its strip is only 22.4 long, so the kitchen and the
  suite cannot share it → kitchen goes to the right strip with Bedroom 1's suite (225 / 17.5 = 12.9) +
  kitchen 179 / 17.5 = 10.2 → 23.1 > 22.4. **Overflow by 0.7 ft** → ladder step 4 (footprint +3%).
- After that, every habitable room touches the exterior. The kitchen has the right side wall; the master
  and bedroom 1 have the side walls.
- The bend phase has nothing to do here. Today mask_on on this floor fails `dining_over_cap,
  door_too_narrow, narrow, too_many_parts`.

---

## 5. HOW STAIRS AND MULTI-FLOOR ALIGNMENT WORK

**Shared footprint.** Already true today: one `BuildingPlan` footprint for all floors, governed by the
ground floor. The packer additionally shares two pieces of geometry across floors:

- **Spine x-range.** Chosen once per building (§2.2). Every floor's hallway occupies the same columns.
  - Ground floor: the spine runs from the public band's back edge to the rear.
  - Upper floors: the spine runs from the front wall, or from the stair landing, to the rear.
- **Stair block.** The existing `house_planner.stair_block_size` (1.2 m × (2.7 m run + 0.3 m landing)),
  positioned once by a packer version of `_place_stair_block`. It sits at the spine head, beside the
  spine, on the side chosen by the candidate.
  - Ground floor: it borders the public band's back edge and the spine.
  - Upper floors: it borders the spine and the landing.
  - Cells are identical on every floor and are reserved before any slot is placed.
  - Its area is pinned (min = target = max = block) through the existing
    `_allocate_floor(stair_area=...)` path. `STAIR_FIXED_BLOCK` becomes true **for the packer path
    only**; the mask path is unchanged.
- **Landing.** On upper floors, the spine cells directly in front of the block's top end form the
  landing. They are part of the Hallway room, so no separate room is needed and the landing is always
  ≥ the spine width.
- **Plumbing stack.** On each floor, wet-band slots are assigned left to right in strip order, with a
  tie-break that prefers a slot whose x-range overlaps a wet slot on the floor below. This is a soft
  preference.
  - Reported as **"stacked wet rooms %"** (new, informational, not ratcheted in v1).

**Where the surplus goes, by construction** (§2.2 step 3, a named sink and never the stair). The packer
computes the surplus `S` before placing anything. After headroom to max:

| Floor | Sink | Notes |
|---|---|---|
| Ground floor, porch tiers | **Verandah** | `rules.GROUND_FLOOR_EXCESS_FALLBACK`, already sized by `_allocate_floor`'s porch logic. Placed as a full-width front or side slot (open area). |
| Upper floors | **Terrace** | `rules.UPPER_FLOOR_EXCESS_FALLBACK`. Placed as a rear strip-end slot on the exterior (open area). On 60x80_3f floor 3 the plan already has a 483 sq ft Terrace. |
| Small residue (< sink min) | Spine-side closet / store slot, else the last room in the strip | Never above its max |

Anything still left triggers a footprint shrink (Step 5.2 scale variants, now chosen deterministically
from `S` instead of searched).

The stair is pinned and can never receive area. This is the property the Step 5.5 fixed block lacked
(postmortem: "the pocket the block leaves has no room neighbour"). In the packer there are no pockets,
because every cell is assigned during packing.

**Worked example: 60x80_3f_south (3 floors).**

- Footprint 33.1 × 39.7 on all floors (plan scale 1.0).
- Spine 3.9 ft at x ≈ 14.6–18.5. Stair block 3.9 × 9.8 to the right of the spine head.
  - Floor 1: at y = `D_pub`.
  - Floors 2–3: at the same y (the same cells, so the stair aligns exactly).
- Floor 2: Master suite + suite 2 + Sitting Area 399 + Terrace sink 99.
- Floor 3: Bedroom 4 suite + Sitting Area + Terrace 483. The Terrace is the rear exterior slot, which is
  where today's hallway/stair dump goes. Today the floor 2 stair is 161 sq ft (`stair_oversized`); in the
  packer it is fixed at 38–54 sq ft by construction.
- Baths 2 and 4 sit in the left strip's wet band above Bathroom 1's x-range, so the stacked wet rooms
  figure is 100% here if the candidate allows it.

**Honest limits:**

- Exact stacking of all baths is a preference, not a guarantee.
- The ground-floor spine starts behind the public band. So floor 1's hallway is shorter than the upper
  floors' hallway, by design.
- The T2/T3 templates need their own stair positions (rule: the stair stays at the spine/cross-corridor
  junction).

---

## 6. HOW IT WILL BE MEASURED ON THE SAME GATE AND RENDERS

### 6.1 Flag and branch

- **New flag:** `Settings.house_layout_packer: bool = False`, read only on the planner path
  (`house_planner_enabled=True`).
  - Off: everything is byte-identical, for both the planner-off and planner-on mask paths. Verify with
    `scripts/mask_output_dump.py` + `cmp`, the established proof method.
- **Rect engine:** `floor_layout.py` is untouched (golden rule) and stays reachable via
  `house_layout_engine="rect"`.
- **Branch:** **the prototype is built on a separate branch `proto/explicit-rectangles`** (per the
  user). The current engine on the working branch is **not replaced** until the prototype beats it on
  the numbers below. Merging is a separate decision.

### 6.2 Same gate

- **Same matrix:** the 35 floors (`tests/mask_quality_matrix.iter_floors`).
- **Same scoring:** `mask_scorecard.score_floor` (tier thresholds) +
  `format_violation_histogram` + `format_dual_scorecard`.
- **Same ratchets:** `tests/test_mask_quality_gate.py` planner-on ratchets against
  `tests/data/mask_quality_baseline_planner.json` (HEAD values), plus the suite_guard and
  narrow_bedrooms ratchets.
- **New scorecard lines:** `layout_path` kinds `packer`, `packer+bend`, `packer_fallback_mask`; bends
  per floor; fallback rate; stacked wet rooms %.

### 6.3 Acceptance numbers (packer flag on, vs mask_on at HEAD)

| metric | HEAD mask_on | acceptance |
|---|---|---|
| fully clean floors (clean_all) | 3/35 | **≥ 12/35** |
| zero-hard floors | 13/35 | ≥ 20/35 |
| hard `narrow` | 26 | **0** |
| `stair_oversized` / pinned > 1.5x | 4 / 4 | **0 / 0** |
| `oversized` | 21 | ≤ 5 |
| daylight hard rooms | 0 | **0** (must not regress) |
| circulation | 31/35 | **35/35** |
| unreachable / entry-missing / entry-is-garage | 0 / 0 / 0 | 0 / 0 / 0 |
| parts check | 27/35 | ≥ 33/35 |
| any ratcheted gate metric worse than HEAD on any floor | — | **none** (the per-floor safety net makes this structural; the gate checks it) |
| fallback to mask | — | ≤ 3/35 floors, all reported |
| latency, 3-floor dense 150x150 request (`scripts/e2e_timing.py`) | 21.8 s warm planner-on | **≤ 5 s warm** layout (packer + bend + safety-net comparison) |
| full matrix (`--jobs 6`) | 135 s wall | ≤ 60 s wall |

A note on latency: the safety net needs the mask result as a comparison. So until the packer has proven
itself, latency is *mask + packer*. The ≤ 5 s budget applies once the safety net is switched to "mask
only on packer hard failure". During the prototype, report both figures.

### 6.4 Render-review protocol

Render PNGs with `mask_quality_report.py`-style rendering for the packer mode (write to
`scripts/output/quality/packer/`). A human or reviewing agent opens each PNG below with the Read tool and
records pass/fail per item in the commit message.

| Floor (PNG) | What is checked |
|---|---|
| `40x60_typical_1f_south_floor1` | Balanced yards; foyer at the entrance with ENTRANCE inside it; living ≥ dining; every bedroom on an outside wall; ensuite shares a wall with its bedroom; no room under 8.9 ft wide (bed) / 4.9 ft (bath) in the label W×H |
| `60x80_dense_garage_1f_floor1` | 2-car garage really 2 cars wide at the front corner; garage reached via Entry, not Living; kitchen and dining adjacent; all 4 bedrooms on outside walls; laundry in the wet band off the hall (no pass-through via a bedroom, which is a known HEAD failure); no L wraps except legal notches |
| `150x150_typical_1f_floor1` | Footprint size sensible for the plot (coverage line); bonus rooms (Media / Study / Library / Gym) present and rectangular, not dropped; no ballooned master (HEAD: 437 vs 164 / 101) |
| `150x200_2f_reported_floor1` | Garage + entry + living + dining in the band; kitchen with an outside wall; stair block beside the spine head; dining ≤ 180 sq ft |
| `150x200_2f_reported_floor2` | **Same stair cells as floor 1 (overlay check: crop both PNGs at the block)**; stair ≤ 2.0x its min (HEAD: 572 sq ft); landing in front of the block; Family Lounge / Library / Gym sized to target; no hallway pocket |
| `300x300_typical_1f_floor1` | Estate-tier sizes; Verandah drawn as open area at the front; master within the tier cap (HEAD: 577 vs 114); sink is the Verandah, not a bedroom |
| `60x80_3f_south_floor1/2/3` (and the `_east` variant) | **Stair alignment across 3 floors** (same block position in all three); spine in the same columns; baths stacked (wet-room x-ranges overlap); floor 3 Terrace at the rear as the sink; no `narrow` on floor 1 (the Step 5.5 failure); for `_east`, the transpose places the stair consistently |

Plus the contact sheet for all 35 floors, scanned for black holes, slivers and labels overflowing their
rooms.

### 6.5 Go/no-go thresholds for the prototype

Measured with the existing scorer, planner thresholds, on the 35 floors.

**Prototype (a): packer only, no bend.**

Go to (b) only if **all** of these hold:

- hard `narrow` = 0, `small_area` = 0, `degenerate` = 0, `stair_oversized` = 0;
- the packer succeeds without falling back on ≥ 32/35 floors;
- **zero-hard floors excluding daylight ≥ 16/35**;
- daylight hard rooms ≤ 10, each with a neighbour that has a ≥ 1.8 m exterior edge (bendable).

**Stop** if any of these hold:

- zero-hard excluding daylight ≤ 13/35, i.e. no better than mask_on with daylight already free;
- daylight hard rooms > 25, i.e. no better than `rect_in_plan`, the measured lower bound;
- fully clean floors (counting daylight) < 6/35.

Rationale: `rect_in_plan` measured 4/35 zero-hard with 25 buried rooms, and mask_on measured 13/35. A
packer that cannot beat 13 *before* any bending will not beat it after.

**Prototype (b): packer + minimal daylight bend.**

- **Go to the full build** only if clean_all ≥ 10/35 *and* no ratcheted metric is worse than HEAD on
  more than 3 floors (fixable in the full build's safety net).
- **Stop** if clean_all < 8/35 or daylight hard rooms > 0 after bending.

---

## 7. Reuse / replace / must not break

**Reuse (unchanged or near-unchanged):**

- **Planning:** `planning_rules` (bands, SHAPE_RULES, DAYLIGHT_RULES, HIERARCHY_RULES, BONUS_ROOM_PRIORITY,
  sinks) and `house_planner.plan_building` (targets, tiers, ceilings, estate cap, porch/verandah, terrace,
  `stair_block_size`, `footprint_scale`, `exclude_bonus` re-plan via `layout_building`).
- **Room specs:** `room_specs` (min/preferred/target/max, `min_width_for_room`, `min_depth_for_room`,
  `garage_dimensions`, `staircase_dimensions`, `classify_room_category`).
- **Ordering:** `floor_layout._arrange_suites`, `_sort_key`, `_zone_key`, `_PUBLIC_ORDER_RANKS`.
- **Mask engine helpers:** `_grid`, `_cells_to_room`, `_decompose_planner`, `_give_back`, `_grow_arm` /
  `_steal_arm` geometry, `_usable_width`, `_ShapeCtx`, `_entrance_cell`, `_apply_plan_transform`,
  `_pick_circulation_safe`, `_gate_vector`, `_worst_over_ratio` (the candidate / safety-net pattern).
- **Scoring:** `layout_quality.score_floor_layout` (+ `secondary_part_ok`, `daylight_failures`,
  `circulation_violations`), `layout_metrics.measure_floor`, `mask_scorecard` (scorecard, histogram,
  dual), `door_graph` (doors, circulation report).
- **Renderers:** `blueprint_svg` / `blueprint_dxf` / `conditioning_image` (they consume rects / parts
  and are engine-agnostic).
- **Matrix and gate:** the 35-floor matrix, the gate tests, the baselines, and `mask_output_dump.py` for
  byte-identity proofs.

**Replace (on the packer path only):** seeding and growth (`_seed_rooms`, `_grow_rooms`,
`_preclaim_garage`, `_top_up_to_target`), fill (`_fill_leftovers_bounded`, `_nearest_to_sink`),
`_rebalance_over_max`, `_ensure_minimum_presence`, `_fit_cells` zone rescale, the spine-widening loop,
`_rectify_rooms` and `_reconnect_rooms`, and the `_PLANNER_CANDIDATES` grow variants.

**Made obsolete** on that path (still present for the mask path):

- workarounds: `suite_guard` / `_KEEP_SUITE_CONTACT`, `circ_arms` / `_carve_access_arms`, `circ_stair` /
  `_STAIR_AT_SPINE`;
- the Step 5.2 footprint-scale *search* (it becomes an arithmetic decision);
- `_STAIR_POCKET_TO_HALL`, `_SINK_BESIDE_STAIR`, `_FAR_REMAINDER_SEEDS`, `NARROW_BEDROOM_*`,
  `_FILL_LAST_RESORT_TO_SINK`.

**Must not break:**

- **Flag-off byte identity.** Both the planner-off mask path (the live default) and the planner-on mask
  path stay byte-identical with the packer flag off, checked by dump + `cmp`.
- **Rect engine.** It stays untouched as a fallback (`house_layout_engine="rect"`).
- **Shared callers.** `feasibility._geometrically_placeable_area` and `kaggle_autocad` conditioning go
  through `layout_engine`, so they inherit the flag. The capacity table cache
  (`room_count_requirements_table.cache_clear()` in the conftest fixture) must key on it too.
- **Tests.** No existing test changes meaning. New tests go in a new file (`tests/test_house_packer.py`)
  plus a parametrised packer mode in the gate.

---

## 8. Risks (honest)

1. **The rect engine's own failure modes come back.** The measured rect proxy buries 25 habitable rooms,
   has 85 narrow rooms and 69 bowling alleys.
   - The design answers each one by construction: exterior-slot rule, min-dim clamps, strip depth
     bounded by 1.6 × min depth.
   - But the clamps cascade into overflows, as both worked examples show (kitchen leaves the band,
     footprint +3%). If overflows are frequent, the ladder ends in mask fallback and the gain evaporates.
     **This is the main technical risk**, and it is exactly what prototype (a) measures.
2. **Bending re-creates growth's chaos.** Mitigated by one move kind, area-preserving, at most one per
   room, rescored against `_gate_vector`, no cascades. But Steps 4–5 showed this engine family is
   *sensitive*: 7 of 11 attempts regressed 6–16 floors through small shared changes. The packer is
   arithmetic, so it should be far less chaotic, but this is unproven.
3. **Infeasibility on dense programs.** 60x80_dense and 150x150_dense are the cases where min widths
   don't fit (hand arithmetic, §2.4). Growing the footprint is policy-sensitive: it can trip coverage.
   Dropping bonus rooms is fine.
4. **Wide or deep footprints.** For example 49 × 52.9 gives 22.5 ft strips, and a 9 ft bedroom 22.5 ft
   deep is 2.5:1, right on `BEDROOM_ASPECT_HARD`. The suite-slot stack (bedroom 14.6 deep plus a wet
   band) addresses this. A plain bedroom without an ensuite has no wet band to absorb depth, so it gets
   a closet. **This is the second-biggest unknown.**
5. **Schedule.** Past runs averaged about 1 hour of agent time each plus review and rate-limit stalls.
   The full build is 10–14 runs (§9), with real risk of 1.5× overrun.
6. **Latency.** HEAD planner-on is already about 4× slower than planner-off (16–22 s warm per building,
   135 s matrix wall). The packer itself should be under 1 s per building, but keeping mask as the
   safety-net comparison during the prototype costs mask's full time. Budget in §6.3.
7. **Migration / test churn.** New baselines for packer mode; the scorecard gains path kinds; the gate
   is parametrised. The existing planner-on baseline stays the reference until merge.

---

## 9. Phased plan, effort, recommendation

| Phase | Work | Agent runs (est.) | Calendar (est.) | Gate |
|---|---|---|---|---|
| P0 | Branch `proto/explicit-rectangles`; flag; `house_packer.py` skeleton; T1/T-vertical templates; public band; suite slot; snapping; scorecard path kinds | 1 | 0.5 day | flag-off byte identity |
| P1 = prototype (a) | Full packer: surplus ladder, infeasibility ladder, stair block, sinks; scored on the 35 floors, no bend | 1–2 | 1 day | §6.5 (a) go/stop |
| P2 = prototype (b) | Exterior-arm-swap bend + per-floor safety net vs mask | 1–2 | 1 day | §6.5 (b) go/stop |
| P3 | T2/T3 templates, plumbing stack preference, footprint-grow re-plan, fallback accounting | 2–3 | 2–4 days | acceptance trending |
| P4 | Gate integration (packer baseline, ratchets), render review §6.4, latency work, tests | 2–3 | 2–3 days | §6.3 acceptance |
| P5 | Docs; merge decision (user) | 1 | 0.5 day | user sign-off |
| **Total** | | **8–12 runs (prototype only: 2–4)** | **~1.5–3 weeks (prototype: 2–3 days)** | |

**Single biggest unknown:** whether rooms clamped to their minimum width and depth fit the planner's
footprints on the dense programs without cascading overflows, i.e. what the packer-only fallback rate
and the zero-hard-excluding-daylight count turn out to be. Prototype (a) measures exactly this.

**Alternative: stay incremental.** Cheap targeted fixes still on the table, with plausible gains (all
estimates):

| Fix | Plausible gain | Notes |
|---|---|---|
| Suite-aware `_reconnect_rooms` (keep the piece touching the ensuite) | ~0–1 clean floors | Fixes ≥ 5 of 9 lost suite contacts. Cheap. |
| Width-aware growth in `_grow_rooms` (a room may not stall below min width; grow by strips ≥ min width) | Up to +3 floors | Targets the 26 `narrow`. Histogram: narrow alone = +3 floors. Highest-value incremental item, but it touches the most sensitive stage; Steps 4–5 suggest a high regression risk. |
| Keep bonus rooms instead of dropping them | Up to +2–3 coverage floors | Coverage is a CHECK failing on 19 floors, mostly because bonus rooms are dropped and the footprint shrinks (see §1). Engine-dependent. |
| Rectify speedups | 0 clean floors | Latency only (56% of runtime). |

Greedy histogram ceiling (narrow + coverage + oversized together) is 11 floors. Realistically,
incremental work lands at **~5–8 clean floors**.

**My recommendation: do the prototype first.** Build P0–P1 on `proto/explicit-rectangles` (2–3 agent
runs). Apply the §6.5 stop rule mechanically. Do not commit to the full build until (a) and (b) pass.

The reasoning:

- The incremental path has run into the same wall eleven times. The planner's numbers are right and the
  growth stage overrides them; the remaining incremental lever (width-aware growth) attacks the most
  chaotic stage directly.
- The prototype is cheap, isolated, and decides the question with the same scorer.
- Its failure would still be informative: it would tell us whether the planner footprints themselves
  are infeasible at minimum dimensions.

I would **not** recommend building the full engine on faith. The measured rect proxy (0/35 clean, 4/35
zero-hard, 25 rooms without a window) shows that a naive "explicit rectangles" approach is clearly
worse than today's mask engine. Only a packer with the slot rules above has a chance, and that chance is
an estimate (30–40% of reaching 12/35), not a measurement.
