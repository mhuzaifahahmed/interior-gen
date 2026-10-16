# Step 5.1 diagnostic: where the mask engine's hard violations come from (read-only, HEAD 2313e28)

Script: `scripts/mask_step5_diagnostic.py` (module-attribute wrappers only, restored afterwards; no source
behaviour change). Full output: `scripts/output/quality/step5_diagnostic.txt` (+ `.json`). The refreshed
surplus ledger (`scripts/mask_surplus_diagnostic.py`, now with the per-candidate memo disabled so every
stage really runs - the memo from a6341d9 had silently made that script skip stages) is in
`scripts/output/quality/surplus_diagnostic.txt`.

Method, in one paragraph: every matrix floor (35) runs through the real engine twice. Pass A keeps the memo ON
and only times each engine function (exclusive seconds) - that is the real cpu split. Pass B turns the memo
OFF (a pure speedup cache; output identical) and snapshots the cell grid of every candidate after each stage;
for the CHOSEN candidate of each floor every snapshot is converted to rooms with the same decomposition the
final output uses and scored with the exact `score_floor_layout` call the engine makes. Sanity check: the
re-score of the last stage equals the engine's own warnings on **35/35** floors (both modes). Shape moves in
`_rectify_rooms`/`_reconnect_rooms` are detected by watching the `cells_by_room` list from inside
`ShapeCtx.info` (read-only), so each applied move and any suite contact it broke is recorded.

"Hard violation" below = a hard `Violation` from the engine scorer (includes `wrapped`, `bedroom_aspect`,
`stair_oversized` and circulation kinds, which the engine treats as score-only, but which the scorecard/gate
count). The scorecard histogram is reproduced separately (section 1).

## 1. Hard violations on the non-clean floors, and the top three blockers (scorecard, HEAD)

From `mask_quality_report.py --scorecard --planner both --no-render --jobs 4`
(saved: `scripts/output/quality/scorecard_step5_before.txt`). Planner ON: 33/35 floors not clean.

| type (planner ON) | class | violations | floors | newly clean if fixed alone |
|---|---|---|---|---|
| narrow | HARD | 29 | 22 | 3 |
| coverage | CHECK | 19 | 19 | 2 |
| oversized | HARD | 22 | 15 | 0 |
| too_many_parts | CHECK | 15 | 9 | 0 |
| bedroom_aspect | HARD | 7 | 7 | 0 |
| door_too_narrow | HARD | 5 | 5 | 0 |
| dining_over_cap / pass_through_private / pinned_over_1.5x / stair_oversized | | 4 each | 4 each | 0 |
| foyer_shallow / hierarchy_inverted | HARD | 1 each | 1 each | 0 |

Top 3 blockers (planner ON): **narrow** (+3: 30x40_small, 50x50_typical, 56x56_typical), **coverage** (+2:
150x150_sparse, 300x300_sparse), **oversized** (+0). Greedy: narrow -> 3 clean, +coverage -> 7, +oversized -> 10.

Planner OFF: 35/35 not clean; top 3 too_many_parts (84 / 34 floors), narrow (85 / 31), oversized (49 / 29),
each +0 alone; greedy reaches 1 clean floor after three types.

## 2. Area ledger per floor (planner ON)

Totals: footprint - targets - real hallway is small (net surplus, as found before), but **12,384 sq ft is
left unclaimed after growth** on the chosen candidates (35/35 floors), and the fill's LAST RESORT (no
neighbour with headroom -> the whole pocket goes to one neighbour past its max) placed **4,877 sq ft past a
room's max on 32/35 floors**. Final rooms still above their engine max (remainder rooms excluded): bathroom 19,
bedroom 19, living 5, kitchen 4, dining 2, foyer 1.

Selected floors (sq ft; full table in the output file):

| floor | footprint | sum targets | plan circ | real hall | stair plan -> actual | free after growth | last-resort overflow | biggest fill+rebal gain |
|---|---|---|---|---|---|---|---|---|
| 40x60_typical_1f_south | 1576 | 1387 | 189 | 120 | - | 300 | 185 | Living +156 |
| 50x50_2f/1 | 1087 | 1000 | 87 | 0 | 35 -> 39 | 439 | 260 | Bedroom 1 +340 |
| 50x50_2f/2 | 1087 | 957 | 130 | 272 | 35 -> **158** | 235 | 155 | **Staircase +119** |
| 60x80_dense_garage_1f | 2593 | 2204 | 389 | 242 | - | 501 | 138 | Living +216 |
| 60x80_3f_south/2 | 1087 | 957 | 130 | 282 | 35 -> **161** | 232 | 152 | **Staircase +126** |
| 150x150_typical_1f | 2234 | 1980 | 254 | 118 | - | 531 | 28 | Master Bedroom +296 |
| 150x150_dense_garage_2f/2 | 2906 | 2557 | 349 | 218 | 35 -> 40 | 940 | **757** | Sitting Area +757 |
| 150x200_2f/2 | 1950 | 1794 | 156 | 123 | 35 -> **572** | 533 | **533** | **Staircase +533** |
| 300x300_typical_1f | 2757 | 2445 | 312 | 145 | - | 789 | 25 | Master Bedroom +348 |

The three oversized stairs (and the 4th, 60x80_3f_north/2) get their excess from the fill (last resort or
headroom step), never from the plan target (35 sq ft each).

## 3. The repair step (rectify + reconnect): suite walls, wrapping, runtime

Chosen candidate, summed over 35 floors, after each stage (planner ON):

| stage | hard | rooms >2 parts | 2-part rooms | suite pairs in contact | rooms >1.1x max | narrow bedrooms |
|---|---|---|---|---|---|---|
| growth | 339 | 67 | 37 | 56/56 | 12 | 52 |
| bounded fill | 291 | 98 | 48 | 56/56 | 45 | 36 |
| rebalance | 283 | 105 | 62 | 56/56 | 26 | 35 |
| ensure | 283 | 105 | 62 | 56/56 | 26 | 35 |
| rectify (local search) | 128 | 22 | 72 | **53/56** | 22 | 11 |
| reconnect | 123 | 20 | 71 | **48/56** | 22 | 11 |
| arms (circ_arms only) | 117 | 20 | 72 | 47/56 | 22 | 11 |

(Growth-stage counts include `coverage_gap`/`unreachable` because cells are still unclaimed; those vanish
after the fill and are not counted as origins of final violations.)

* **Why suite walls are lost:** every bedroom/ensuite pair still touches after growth, fill and rebalance
  (56/56). 9 contacts are lost: rectify 3, reconnect 5, arms 1. **Reconnect is the main cause (5/9):** in
  each case the BEDROOM was disconnected after the local search (cost >= 50; 2-3 parts, e.g. 60x80_3f
  south/north floor 2 Master Bedroom, 60x80_3f east/west floor 2 Bedroom 2), `_reconnect_rooms` keeps the
  LARGEST piece and gives away the rest - and the piece it gives away is the one that held the wall with the
  ensuite (40-66 cells moved). It never considers which piece carries the suite contact. The other breaks are
  pair re-splits (3) and one fragment give, all on a donor that was wrapped or disconnected. On the final
  layouts 9 suite pairs have no contact (all listed in the output).
* **Does repair cause the WRAPPING?** No - the opposite. Rooms with >2 parts are created by growth (67) and
  the fill (+31) and rebalance (+7); rectify removes 83 of the 105 (-> 22) and reconnect 2 more. Rectify does
  turn some multi-part rooms into 2-part L shapes (62 -> 72). Of the 40 final `wrapped` violations, 20
  originate in growth, 18 in fill/rebalance, 2 in rectify.
* **Runtime (pass A, memo on, the real engine; matrix 340.6 s of single-process cpu):** `_rectify_rooms`
  **191.6 s = 56.2%**, `_rebalance_over_max` 52.1 s = 15.3%, `_grow_rooms` 30.4 s = 8.9%, scoring 23.9 s =
  7.0%, `_fill_leftovers_bounded` 17.9 s = 5.2%, `_cells_to_room` 2.8%, `_reconnect_rooms` 0.2%, other 3.1%.
  So the repair step is most of the runtime (56%, a bit below the earlier "65%" profile, which counted
  differently), but `_reconnect_rooms` itself is negligible - the cost is the rectify local search. Flag-off:
  4.1 s total, growth 47.5%.

## 4. Origin of every final hard violation (first stage from which it persists to the end)

Planner ON, 117 final hard violations on the chosen candidates:

| type | planner target | growth | fill | rebalance | rectify | reconnect | arms | total |
|---|---|---|---|---|---|---|---|---|
| wrapped | 0 | 20 | 17 | 1 | 2 | 0 | 0 | 40 |
| narrow | 0 | **29** | 0 | 0 | 0 | 0 | 0 | 29 |
| oversized | 0 | 6 | **20** | 0 | 0 | 0 | 0 | 26 |
| bedroom_aspect | 0 | 0 | 4 | 0 | 3 | 0 | 0 | 7 |
| door_too_narrow | 0 | 3 | 0 | 0 | 2 | 0 | 0 | 5 |
| pass_through_private | 0 | 1 | 1 | 0 | 2 | 0 | 0 | 4 |
| stair_oversized | 0 | 0 | **4** | 0 | 0 | 0 | 0 | 4 |
| foyer_shallow | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| hierarchy_inverted | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 |
| **total** | **0 (0%)** | **59 (50%)** | **47 (40%)** | 1 (1%) | 10 (9%) | 0 | 0 | 117 |

Grouped: planner targets 0%, **growth 50%**, **fill + rebalance 41%**, repair 9%, arms 0%.
Planner OFF (92 final): growth 86 (93%), fill 6 (7%) - flag-off has no repair step.

## What the data says about the fix order

1. **No final hard violation comes from a planner target** - the planner's numbers are not the problem
   (consistent with Step 4).
2. **Growth (50%)**: every final `narrow` (29) and half of `wrapped` start at growth and are never repaired.
   This is shape-of-growth (width-aware growth / pre-claimed compact blocks), not area.
3. **Fill (40%)**: 20 of 26 `oversized`, all 4 `stair_oversized`, 17 `wrapped`. The fill's last resort put
   4,877 sq ft past maximums on 32/35 floors. Bounding the fill by max + routing the remainder to a sink
   (Step 5.3) and shrinking footprints whose surplus has nowhere sensible to go (Step 5.2) target exactly
   this. `_top_up_to_target` (5.3c) targets the 12,384 sq ft left after growth.
4. **Repair (9%)** is a net reducer of violations (283 -> 123); its two real faults are suite contacts lost in
   `_reconnect_rooms` (keeps the largest piece, not the piece touching the ensuite) and its runtime (56%).
   A suite-aware choice of the kept piece in `_reconnect_rooms` is a cheap, targeted follow-up candidate
   (not in Step 5's list; noted for the user).

So the planned order (5.2 footprint scale, 5.3 bounded fill + sink + top-up, 5.4 compact blocks for small
rooms) attacks the fill origin first; the larger growth origin (`narrow`) is only touched by 5.4's compact
pre-claimed blocks (small rooms) - bedrooms/bathrooms narrow at growth are NOT addressed by any Step 5 item.

## Step 5.4 record: compact blocks, small-bonus minima, master cap, upper-floor sinks (ALL reverted)

Each change was gated on its own (planner-on gate, planner-only code; flag-off untouched).

| change | layouts changed | gate | why |
|---|---|---|---|
| (a) `_preclaim_compact` alone (compact >= min-width block, aspect <= 2:1, <= max, around the seed, for pantry/laundry/closet/storage) | 0 | n/a | On the matrix the only small service room is a Laundry whose real min width (6.9 ft) squared already exceeds its max (49 sq ft) after grid rounding, so no legal block exists; with a too-big block it became `oversized` (63 > 1.1 x 49) instead of `narrow`. |
| (a) + small-bonus minima (pantry 25 / laundry 40 / closet 25 sq ft) | 21 | **16 floors worse** | Pantry/Laundry come out compact (7.0x7.0, 6.7x5.3, 4.0x8.0), but adding them shifts every target on the floor and the bonus drop/re-plan loop: new `narrow`, `door_too_narrow`, `foyer_shallow`, `small_area` on 40x60 S/N, 50x50_2f, 60x80_typical, 60x80_3f (all orientations), oversized Sitting Area, narrow bedroom on 56x56. |
| (b) master <= 2.0x the floor's largest other bedroom (target + max + room_max override) | 13 | **8 floors worse** | The engine still decides the final size: 300x300_typical master 577 -> 539 vs others 107 (5x); 150x150_typical 437 -> 464. Neighbours get the freed area as strips: new `narrow` on 40x60 E/W/garage, 60x80_typical (+door_too_narrow), 60x80_dense, 150x150_typical; orphaned ensuite on 300x300; unreachable on 150x150_dense f1. |
| (c) upper-floor sinks Landing 70 / Upper Storage 40 before the Terrace | 6 | **6/6 changed floors worse** | Landing/Upper Storage grow as 1-4 ft strips (narrow, door_too_narrow) on 150x150_dense f2, 150x200 f2, 60x80_3f floor 3 (all orientations); the 150x200 stair stays 572 sq ft. |
| (c) + the compact pre-claim (incl. Landing) | 7 | **7 floors worse** | The blocks are claimed (71-91 cells) but the rest of the floor then fragments (60x80_3f south/north f3: Sitting Area 17 parts, hard 2 -> 12). |

Sparse/big plots (nothing kept, so unchanged from 5.3): 150x150_sparse footprint 457 sq ft / 2.0%,
300x300_sparse 514 / 0.6%, 150x150_typical 2022 / 9.0% (5.2's 0.9 scale), 300x300_typical 2757 / 3.1%,
150x200 1950 / 6.5%. Master vs other bedrooms (sq ft): 40x60 south 158 vs 292/177 (master is the SMALLEST);
60x80 dense 245 vs 216/180/189; 150x150 typical 437 vs 164/101; 150x200 f1 198 vs 200; 300x300 typical 577
vs 114/114.

Conclusion: area/sizing changes keep failing for the same reason the diagnostic showed - the
engine's growth decides shapes (50% of final violations start at growth), so any extra or resized room
becomes a strip somewhere. The next real lever is width-aware growth, not more planner numbers.

## Footprint-scale trigger (kept, 048e5ef) and suite-aware `_reconnect_rooms` (reverted)

**Trigger.** Per floor, `fill_overflow_fraction` = last-resort fill cells / grid cells. The three buildings
a smaller scale improves have the SMALLEST overflow (150x150_typical 1.3% / 28 sq ft, 50x50_typical 2.6% /
42, 60x80_typical 5.4% / 101); every building with a floor above 15% (30x40 sparse, 50x50 2f, the four 60x80
3f, 90x40 sparse, 150x150 dense, 150x200; worst floors 18-28%) never had a scale accepted, and the real dense
3-floor e2e requests reach 20.8%. So the search now runs only when no floor exceeds 15% = 1 - min scale
(the most area any candidate removes) - an UPPER bound, the opposite of "try smaller when leftover is
large" (any lower bound that keeps 150x150_typical triggers on nearly everything). Matrix output
byte-identical in both modes. Stop-at-first differs from try-all on 60x80_typical (0.95 / hard 2 vs
0.85 / hard 0), so try-all stays. Redundant-work pruning measured and not worth it: identical floor inputs
13/208 calls (8 s of 328 s) on the matrix, 2/36 in e2e; an early "floor is worse" abort can only skip floors
of the last replan attempt (zero on single-floor buildings and on every multi-floor building that still
triggers).

**Suite-aware reconnect (reverted, no source change).** Keeping the piece of a disconnected bedroom/ensuite
that touches its partner (longest shared wall) instead of the largest: orphaned ensuites 9 -> 5 (rooms;
8 -> 5 floors), but 5 floors worse - 50x50_2f f2 (hard 1->2, narrow bedrooms 1->2, path falls back to
reference_raw), 60x80_3f north/south f2 (hard 0->1, bedroom_aspect, narrow bedroom, pass_through 0->2;
suite_guard path no longer picked), 60x80_3f east/west f2 (hard 1->2, narrow bedrooms 1->2, orphan unchanged;
circ_stair no longer picked). Matrix totals: hard 30->35, narrow bedrooms 10->15, pass_through 4->8. Why:
logging every case where the rule changes the kept piece (all 35 floors, all candidates) shows the piece
holding the suite wall is ALWAYS a small strip - 6 to 100 sq ft, typically 1-5 ft deep (e.g. 60x80_3f
south f2 Bedroom 1 keeps 14 sq ft 14.1x1.0 ft of 147 cells) - and below the room's minimum area in every
case; all but one are also below a usable width. Restricting the rule to pieces that meet the minimum area,
or that keep a usable width, or both, changes no layout at all. So the 5 reconnect losses are not "the
wrong piece kept": the bedroom's real body is elsewhere and only a sliver touches the ensuite. The fix has
to keep the ensuite next to the bedroom's main body earlier (growth / rectify), not in reconnect.
suite_guard therefore stays (7 picks, unchanged).
