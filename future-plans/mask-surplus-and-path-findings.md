# Mask planner: surplus, engine-path and Phase 4b parity findings (read-only diagnostics, 2026-10-14)

All numbers come from code at **HEAD 44132c5**, run from a detached git worktree (the main working tree had another agent's uncommitted speed edits, so it was not used). No existing file was modified.

**Scripts (new):**

| Script | What it does |
|---|---|
| `scripts/mask_surplus_diagnostic.py` | Per-room area ledger through the real planner-ON path (`layout_building`). It wraps engine functions at runtime and restores them afterwards. Writes `scripts/output/quality/surplus_diagnostic.txt` (full per-floor tables) and `.json`. |
| `scripts/mask_pass_through_paths.py` | Lists the pass-through rooms with their BFS door paths. Writes `scripts/output/quality/pass_through_paths.txt`. |
| `scripts/phase4b_parity_snapshot.py` | Snapshots each checkout, then compares them and attributes each change to a rule. Writes `scripts/output/quality/parity/{pre4b,head}.json` and `parity_report.txt`. |

All areas below are in sq ft. Every plot in the matrix is in feet.

---

## 1. Verdict on the hypothesis: PARTLY CONFIRMS

> "The planner and the engine disagree on how much area each room gets, and the engine dumps the difference into whichever room is adjacent."

### The ledger

For every room on every floor, the area change is split into stages:

| Stage | What it measures |
|---|---|
| quant | Grid rounding of the plan target |
| fit | `_fit_cells` zone rescale |
| grow | `_grow_rooms` |
| fill | `_fill_leftovers_bounded` |
| rebal | Rebalancing |
| shape | Rectify / reconnect |
| arms | Corridor arms carved through rooms |

`surplus` = footprint − Σ plan targets − actual hallway. This equals Σ(actual − target) over all rooms.

### The "dumps into an adjacent room" half is TRUE, and it is the dominant mechanism

| Metric (baseline, 35 floors) | Value |
|---|---|
| Area moved by the bounded fill into rooms | **12,410 sq ft** (fill is the largest positive stage by 5x; next is fit at 2,180) |
| Floors where ≥ 50% of the absorbing room's gain came from fill | **33 / 35** |
| Absorbing room adjacent to the Hallway / to the stair | 31 / 10 of 35 |
| Absorbing room's category | bedroom 23, living 6, staircase 4, dining 1, porch 1 |
| Floors with stair > 2.0x its minimum | 4. **All 4 got 119–533 sq ft purely from fill**. Their plan target is 35 sq ft, and quant/fit/grow add ≈ 0. |

### The "planner and engine disagree on area" half is MOSTLY WRONG as the source

| Metric (baseline) | Value |
|---|---|
| Net disagreement Σ surplus | **+1,446 sq ft total**; median +59 per floor; median \|surplus\| 83; \|surplus\| ≤ 100 on 26/35 floors |
| Area left unclaimed after growth (then handed out by fill) | **12,410 sq ft** = **8.6x** the net disagreement; median 343 per floor |
| Floors where unclaimed-after-growth > \|surplus\| | **35 / 35** |
| Floors where the absorbing room gains more than the whole floor's surplus | **28 / 35** |
| Grid quantisation (Σ\|quant\| over all rooms) | **100 sq ft** in total. Negligible. Cell area is 0.3–4.0 sq ft. |

The decisive counter-example is the stair floors themselves:

| Floor | Surplus | Stair |
|---|---|---|
| 50x50_2f floor2 | **−141** | +123 (158 sq ft) |
| 60x80_3f_south / north floor2 | **−143** | +126 (161 sq ft) |
| 150x200 floor2 | **+33** | +538 (572 sq ft) |

These floors have **less** area than the plan, yet the stair still balloons. So the stair is not where the planner's surplus goes. It is where the engine's own **growth shortfall** goes.

### The real mechanism

1. `_fit_cells` rescales the targets so they exactly fill each zone.
2. `_grow_rooms` uses whole-strip rectangle growth. A room stalls as soon as any cell in its next strip is taken. Its arm and steal fallbacks only top up to the **minimum**, not the target.
3. Rooms therefore stop short. On average **355 sq ft per floor** is left as pockets.
4. `_fill_leftovers_bounded` gives each pocket to the room that borders it.
   - Its "headroom" step is bounded by max, but max is far above target.
   - Its last resort (`pool = [non-stair] … or candidates`) hands a pocket to the stair when the stair is the pocket's only room neighbour. The pocket's other sides are spine.
5. `_top_up_to_target` (`floor_layout_mask.py`, L2156 at HEAD 44132c5) is the function written to close exactly this gap. **It is defined but never called.**

### Secondary causes that are real planner/engine disagreements

**(i) Circulation estimate vs the real hallway.**

- The planner reserves 5,773 sq ft of circulation in total. The engine draws 4,327 sq ft of hallway.
- On 24/35 floors the hallway is smaller than the plan reserved.
- On 4 floors there is no hallway at all: ground floors with fewer than 3 private rooms get no spine. Each of those floors still reserves 87 sq ft.
- This difference is most of the +1,446 sq ft net surplus.

**(ii) Ground-floor zone split.**

- The public band is capped at `_MAX_PUBLIC_BAND_FRACTION = 0.5` of the rows.
- The planner's public targets need more than that. Example: 50x50_2f floor1 has a front band of 680 cells against 923 cells of public targets.
- Meanwhile the back zone gets 680 cells for rooms whose combined max is 344 cells. That is +281 sq ft that cannot fit within max.
- Result: Bedroom 1 goes to 440 sq ft against a 169 target (+271), and Living Room is −180.
- The same pattern appears on all 5 floors of this shape: 50x50_2f floor1 and 60x80_3f_{south, north, east, west} floor1. Bedroom 1 is +268 to +273 each time.
- The user's hypothesis does describe these 5 floors well, but at **zone** level, not room level.

### What the hypothesis does NOT explain

**Narrow master, 60x80_dense_garage floor1 (9.3 x 26.4 ft):**

| Step | Area |
|---|---|
| Plan target | 273 |
| Growth | −186 (stalled at 9.3 ft wide) |
| Fill | +103 |
| Rebal | +25 |
| Shape | +9 |
| Arm carved through it | −26 |
| **Final** | **245** (under target) |

The master is **not** an absorber, and nothing was dumped into it. It is narrow because the neighbours took its side strips first, so growth could only extend it in depth. The floor's absorber is Bedroom 2: +54, with +173 from fill, ending as 3 parts (19.9x4.0 + 11.9x10.6 + 1.3x7.9). This is the L-shaped bedroom.

**Big plot that won't grow:** unrelated to surplus. See section 3.

**Bottom line:** the problem is growth shortfall plus neighbour fill (engine). The planner contributes second-order errors: circulation over-estimate and the ground-floor band split.

## 2. Fixed stair block (STAIR_FIXED_BLOCK = True, forced at runtime)

- **Block size:** 1.2 m × (2.7 m run + 0.3 m landing) = **38.8 sq ft**. After snapping to whole cells it is 40–54 sq ft. The block was intact on 18/18 winning layouts.
- **Stairs:** none over 2.0x their minimum (baseline: 4).
- **Where the pocket goes instead:** into the Hallway, via `_STAIR_POCKET_TO_HALL`.
  - Fill into the hallway: **+2,746 sq ft** (baseline: 0).
  - Total hallway: **7,908 vs 4,327**.
  - 150x200 floor2: hallway **736 sq ft**, against 156 planned.
  - Floors with surplus below −200: 10 (baseline: 1).
- **The block relocates the dump, it does not remove it.**
- **Regressions:**
  - Hard violations: 125 → 128 (score-only kinds included).
  - 150x150_dense floor1: 8 → 27.
  - 50x50_2f floor1: 4 → 12 (narrow / wrapped / door_too_narrow).
- **Confound:** some footprints change because the bonus-room drop/replan differs. For example 50x50_2f is 1,087 sq ft in baseline but 1,317 with the block, because "Media Room" is kept. So per-floor comparisons are not pure stair effects.

## 3. Big-plot growth: what actually binds (baseline)

The footprint is `min(max(band target, program_min), ceiling × 0.85, envelope, per-bedroom cap)`.

| Case | Band | Band target | Ceiling × 0.85 | Per-bedroom cap / floor | Footprint | Coverage | **Binds** |
|---|---|---|---|---|---|---|---|
| 150x150_typical | L / spacious | 9,922 | 2,114 | 3,900 | 2,234 (incl. 120 porch) | 9.9% | ceiling × 0.85 |
| 150x150_sparse | L | 9,922 | 457 | 2,600 | 457 | 2.0% | ceiling × 0.85 |
| 150x150_dense_2f | L | 9,426 | 2,786 | 3,900 | 2,906 | 12.9% | ceiling × 0.85 |
| 150x200_2f | XL / estate | 9,800 | 4,034 | **1,950** | 1,950 | 6.5% | **per-bedroom cap** |
| 300x300_typical | XL | 31,184 | 2,596 | 3,900 | 2,757 | 3.1% | ceiling × 0.85 |
| 300x300_sparse | XL | 31,184 | 514 | 2,600 | 514 | 0.6% | ceiling × 0.85 |

- The band target is **3.2x to 60.7x** the footprint actually built, so it never binds on a big plot.
- The house size is the program's (tier) max sum × 0.85, or the 1,300 sq ft-per-bedroom cap on the 2-floor estate.
- To make big plots grow, change those numbers: the tier max table, `CEILING_PACKING_FRACTION`, or `BUILT_SQFT_PER_BEDROOM_CAP`. The engine is not the cause.

## 4. Task 2(a): the "engine path" line sums to 25, not 35

- **Location:** `app/pipeline/mask_scorecard.py`.
  - L245: `PATH_KINDS = ("rectified", "reference_reconnect", "reference_raw")`.
  - L341–346 count only those three kinds.
- **Cause:** Phase 4b added three new `layout_path` values in `floor_layout_mask._layout_with_plan` / `_circulation_variants`: `circ_stair`, `circ_arms` and `circ_arms+stair`. Floors whose winning candidate is one of those are silently dropped from the count.
- **Ruled out:** it is not early returns, the bonus re-plan, or missing diagnostics. `LAST_FLOOR_DIAGNOSTICS` and `plan.layout_path` agree on all 35 floors.

**The 10 missing floors, by the path they actually took:**

| Path | Floors |
|---|---|
| `circ_arms` (4) | 60x80_dense_garage_1f/floor1, 150x150_dense_garage_2f/floor1, 60x80_3f_east/floor1, 60x80_3f_west/floor1 |
| `circ_stair` (6) | 60x80_3f_south/floor1, 60x80_3f_north/floor1, 60x80_3f_east/floor2, 60x80_3f_east/floor3, 60x80_3f_west/floor2, 60x80_3f_west/floor3 |

The full count is rectified 24 + circ_stair 6 + circ_arms 4 + reference_raw 1 = 35.

These 10 floors are **exactly** the 10 planner-ON floors whose rects changed in Phase 4b (section 6).

**Recommended fix:**

1. Extend `PATH_KINDS` with `circ_stair`, `circ_arms` and `circ_arms+stair` (or count by `Counter(paths.values())`).
2. Print every kind.
3. Add an assertion such as `assert sum(counts.values()) == len(keys), f"unknown layout_path kinds: ..."` (or a test in `tests/test_mask_scorecard.py`), so a future new path kind cannot vanish silently.
4. Keep "fallback" = reference_reconnect + reference_raw. The circ_* paths are not fallbacks.

## 5. Task 2(b): the 6 rooms on 5 floors reachable only through a bedroom or bathroom (planner ON)

Unreachable rooms: 0. Common thread: **none of the 5 primary rooms shares a wall with the Hallway.** The door rules (`should_suppress_direct_door` plus `hallway_adjacent`) keep the direct private doors for a room without hallway contact, because that is its only way in. The cause is therefore placement or growth (seeded or grown away from the spine), not the door rules.

| Floor | Room | BFS door path from the entry | Why no better path exists |
|---|---|---|---|
| 40x60_typical_garage_1f / 1 | Bathroom 2 (suite 1, 48 sq ft) | Entry → Living → Dining → Master Bedroom → **Bathroom 2** | Its only walls are Master Bedroom (another suite) and Master Bathroom. It never touches its own Bedroom 2 or the Hallway. |
| 50x50_2f / 1 | Staircase (39) | Entry → Dining → Bedroom 1 → **Staircase** | The ground floor has no Hallway: 2 private rooms is below `_MIN_ROOMS_FOR_SPINE = 3`. The stair is seeded at the back and its only wall is Bedroom 1. |
| 60x80_dense_garage_1f / 1 | Laundry (47) | Living Room → Master Bedroom → **Laundry** | Its only walls are Master Bedroom (4.0 ft) and Bedroom 4 (11.9 ft). |
| 150x150_dense_garage_2f / 1 | Staircase (34, 2 parts) | Entry → Living → Hallway → Master Bedroom → Master Bathroom → **Staircase** | Its only walls are Master Bathroom (2.8 ft) and Bedroom 4 (4.3 ft). |
| 150x150_dense_garage_2f / 2 | Bedroom 2 (141) | Staircase → Sitting Area → Hallway → Master Bedroom → **Bedroom 2** | Its walls are Master Bedroom, Master Bathroom and Bathroom 2. It does not touch the Hallway. |
| same floor | Bathroom 2 (its ensuite) | … → Bedroom 2 → **Bathroom 2** | It does touch the Hallway (4.3 ft), but that door is suppressed by the ensuite-to-hall rule, which is correct. It only fails because its bedroom fails. This is a cascade, not an independent failure. |

## 6. Task 2(c): did Phase 4b change rect-engine or flag-off renders?

Method: detached worktrees of cf1d58d (before 4b) and 44132c5 (HEAD), run on the full matrix in three modes. Door changes are attributed by re-deriving door sets on the same rects with the OLD `door_graph.py` (loaded from the cf1d58d worktree) and the NEW one.

### Layouts

| Mode | Rects identical | Warnings identical |
|---|---|---|
| mask, planner OFF | **35 / 35** | 35 / 35 |
| rect | **35 / 35** | 35 / 35 |
| mask, planner ON | 25 / 35 | 21 / 35 |

The 10 changed planner-ON floors are exactly the circ_* floors in section 4, which is intended. **The rect layout did not change at all.**

### Renders, on floors with identical rects

**0 unexplained differences.**

| Mode | PNG | DXF | Concept Layout door specs | Conditioning image |
|---|---|---|---|---|
| mask OFF (**the live production default**) | 3 | 7 | **35** | 3 |
| rect | 11 | 15 | 11 | 0 |
| mask ON | 26 | 28 | 34 | 13 |

### Attribution by rule

| Rule | rect | mask OFF | mask ON (same-rect floors) | Intended? |
|---|---|---|---|---|
| Common bath never opens into living/dining/kitchen/family | **16 doors / 11 floors** | 0 | 11 | **Yes.** The plan lists it as an allowed change. In rect it is mostly upper-floor ensuite ↔ "Sitting Area" (category living) and ground-floor Bathroom 1 ↔ Kitchen. |
| DXF garage parity | 4 (Garage↔Kitchen) | 4 (Garage↔Living) | 3 | **Yes.** Allowed by the plan, and the DXF now matches the PNG. |
| Kaggle parts-aware edge | 0 (rect rooms are single rectangles) | **289 door-pair changes on all 35 floors** | 125 | **Intended** (the 4a TODO), but **not** on the plan's list of allowed changes. It fixes real bugs: the old bbox edges invented doors such as Living↔Master Bedroom and missed real ensuite doors such as Master Bathroom↔Master Bedroom. It changes the live Concept Layout overlay on every flag-off floor. |
| EXTRA: front door prefers a non-bedroom/bath room | 0 | 3 | 3 | **Needs sign-off.** It moves the entrance from a bedroom/bath to the Hallway on all 3 sparse floors, live default included. Not in the brief. |
| EXTRA: an orphaned ensuite keeps its hallway door | 0 | 0 | 9 | **Needs sign-off.** Planner ON only. |
| Garage-entrance rule | not triggered | not triggered | not triggered | The pre-4b front door was never in a garage on this matrix. |

### Reachability safety check

With the new door rules, unreachable rooms are **0** in rect and 0 in mask-OFF, versus 0 and 0 under the old door graph. No rule removed a room's only door.

## 7. Surprising findings

1. `_top_up_to_target` exists, its docstring describes exactly the dominant failure, and **it is never called**.
2. The live default (mask, planner OFF) has pass-through rooms on 18/35 floors, and rect on 11/35. The gate holds planner-ON at 0 only, so these are unmeasured.
3. 50x50_2f floor2: the "Hallway" is 272 sq ft in 4 parts, against 130 planned. The spine widening plus shape moves already make it a sink before the stair block makes it worse.
4. Every big-plot footprint is capped by the program ceiling, not the plot band. The band numbers are effectively dead on L/XL plots.

## 8. Recommended fix order (smallest first)

1. **Number / report (trivial):** add the circ_* kinds to `mask_scorecard.PATH_KINDS` and assert the sum. Decide whether the Kaggle parts-aware change and the two EXTRA rules are accepted for the flag-off default, and record that.
2. **Number (planner):**
   - Set the circulation fraction to 0 (or to the real 1.2 m spine area) when a floor will have no spine (fewer than 3 private rooms). This removes the phantom +87 sq ft on 4 floors.
   - Separately, the big-plot size is a product decision on `CEILING_PACKING_FRACTION`, `BUILT_SQFT_PER_BEDROOM_CAP` and the tier max table. The engine is not involved.
3. **Planner:** make allocation zone-aware. Size public vs back targets against the engine's actual band split (`_public_band_rows`, capped at 0.5), or let the planner set the band depth. This fixes the +268 to +273 Bedroom 1 overflow on 5 ground floors.
4. **Engine (last, but this is where most of the area is):**
   - Call `_top_up_to_target` after `_grow_rooms` on the planner path. Target: the 12,410 sq ft growth shortfall.
   - Make the fill's last resort prefer the floor's remainder sink (Terrace/Verandah), and never the stair or hallway.
   - Then re-measure the stair block. On its own it only moves the stair dump into the hallway (+2,746 sq ft).
   - Every step must be gated by the planner-ON ratchet. The narrow master needs width-aware growth, not area changes.
