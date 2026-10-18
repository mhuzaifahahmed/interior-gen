# Why rooms end far above their max - and a bounded fill that can pass the gate (read-only diagnosis, 2026-10-18)

HEAD `72a0e62`, planner ON, mask engine. No tracked code was changed. Probes (scratchpad `review/`, not committed):
`fillprobe.py` (per-stage room sizes + cell grids of the CHOSEN candidate), `planprobe.py` (planner numbers),
`compartments.py` (capacity check on all 35 gate floors, 2 min with 4 workers).

## Plain-language summary

- **The 574 sq ft bedroom (100x150, floors 2-4) and the 462 vs 105 bedrooms (50x70, floor 3) are the same bug,
  and it is NOT the planner's numbers.** The corridor (spine) runs down the middle of the back half of the
  floor and cuts it into a left part and a right part. Rooms are placed (seeded) on one side or the other by
  position along the outer wall, without checking whether each side has the right amount of floor for the
  rooms put there. On both demo floors one side got too many rooms (they stay small: Bedroom 1 = 100,
  Bedroom 6 = 105, Terrace far below its target) and the other side got one bedroom and nothing else. The
  spare floor on that side has only that one bedroom next to it, so the fill gives it everything.
- **Nothing later can move the area back.** The "rebalance" step only passes area between rooms that share
  a wall; the corridor is not a room, so the two sides are cut off from each other, and the front band
  (the lounge) is not allowed to take part. The Terrace (the intended dump) is on the other side.
- **This is common, not a corner case:** on 20 of the 35 gate floors a corridor-bounded part of the floor
  has more cells than the rooms inside it can legally hold (sum of their maxes). 19 of those 20 are among
  the 28 floors where the fill had to overflow a room. The Sonnet Terrace cap failed for exactly this
  reason: capping the only room with spare capacity in a part of the floor leaves the bedrooms as the only
  place for the rest.
- **Why upper floors have so much spare area:** one footprint for all floors, sized for the GROUND floor's
  program; upper floors have fewer/smaller rooms (no living/dining/kitchen, and in the standard tier no
  bonus room is allowed upstairs at all). 100x150: 322 sq ft spare per upper floor; 50x70: 488 sq ft.
- **The fix is to make placement capacity-aware, not to cap more numbers:** (1) measure each part of the
  floor before growth; (2) put rooms (whole suites) and the Terrace on the side that can hold them; (3) move
  the corridor sideways when no split balances; (4) only then bound the fill's last resort; (5) build the
  Phase 7 fallback for whatever is left. Each step is a no-op on floors that already balance, which is what
  keeps the currently-good gate floors byte-identical.

## 1. Which steps push a room past max, in what order, and why each is allowed

### 100x150 ft, 4 floors, 3 bed / 2 bath, floor 2 (floors 3-4 identical)
Plan: footprint 36.4x52.1 = 1,898 sq ft; floor 2 usable 1,670 = targets Lounge 560, Bedroom 1-3 207 each
(= max), Bathrooms 66, Stair 35, **Terrace 322** (remainder). Grid 1,120 cells of 1.69 sq ft.

| room | min | target* | max | after grow | after fill | after rebalance | final |
|---|---|---|---|---|---|---|---|
| Bedroom 1 (left of spine) | 100 | 207 | 207 | **100** | 100 | 100 | 122 (`narrow` 7.8 ft) |
| Bedroom 2 (left) | 100 | 207 | 207 | **100** | 100 | 100 | 142 |
| Terrace (left) | 41 | 398 | 1,898 | **186** | 258 | 258 | 193 |
| Bedroom 3 (right, alone) | 100 | 207 | 207 | 224 | **574** | 574 | **574** (`oversized`) |

*target after `_fit_cells`. Cell grid after growth (row 0 = front): Lounge fills the 12-row front band; the
spine (cols 12-14) splits the back. Left part (12 cols x 28 rows = 568 sq ft) holds Bedroom 1, Bedroom 2,
Bathroom 1, Bathroom 2, Terrace (targets 944 sq ft). Right part (13 x 28 = 615 sq ft) holds Bedroom 3 and
the stair (max 243 sq ft). 249 cells (422 sq ft) are free after growth, all on the right.

Order of events:
1. **Growth** (`_grow_rooms`, L2296-2331: whole-strip extension, stalls when the next strip is not free)
   leaves the left rooms short and the right half empty. Bedroom 3 already sits at 224 > 207 (grid
   rounding of its fitted target).
2. **Fill** (`_fill_leftovers_bounded`): the right pocket's neighbours are Bedroom 3 (no headroom) and the
   spine. Step 1 "neighbour with headroom" (L2996-3005) finds nobody; step 2 "spine-aligned pocket ->
   hallway" (L3006-3011) does not apply; **last resort** (L3012-3043): `_nearest_to_sink` (L3072) finds no
   wall chain to the Terrace (adjacency counts room-room walls only, L3110-3129; the spine is not a room),
   so the "least relative overflow" pick is Bedroom 3 -> **+207 cells = +350 sq ft** in one block.
3. **Rebalance** (`_rebalance_over_max`, L3192): Bedroom 3's chain goes Bedroom 3 -> Staircase (over max)
   -> Lounge, but the Lounge is in the front band group (`group`, L3216-3219; check at L3239), so the
   search stops; `end is None`, nothing moves; "230 cells still over a max" is logged.
4. **Repair** (rectify/reconnect/circulation arms) keeps it: final 574.

Same pattern on floor 1: Bedroom 1 186 -> 247 (fill last resort, 84 cells) -> 308 final (+61 more in the
repair stage, path `circ_arms`). The repair stage can add over-max area too, but it is second-order.

### 50x70 ft, 3 floors, floor 3 (floor 2 identical)
Plan: footprint 35.7x44.6 = 1,594; usable 1,403 = Lounge 400, Bedroom 5/6 180 (= max), Bathrooms 60, Stair 35,
**Terrace 488**. After growth the LEFT part holds only Bedroom 5 + Bathroom 5 (420 cells = 525 sq ft vs
max 241); the RIGHT part holds Stair, Terrace, Bathroom 6, Bedroom 6. Bedroom 5: 139 (grow) -> fill gives
its 42 sq ft headroom, then the last resort gives the rest (226 cells) -> **462**. Bedroom 6 stays at 134,
rectify trims it to **105** (`narrow` 6.7 ft). Terrace grows to 244 of a 530 target, ends **331**, ringed by
rooms (U1). Exactly the user's screenshot numbers.

### Why each step is allowed to do this
- **The last resort exists to keep the tiling exact.** A leftover cell nobody owns is a coverage gap (hard,
  "must be 0" in the gate) or the old black hole (Bug A). The only other sink for an unaligned pocket is the
  hallway, and making every such pocket hallway was tried and reverted (11 gate floors regressed, comment
  L3021-3024). So when a pocket has no neighbour with headroom, giving it to a room is the only way the
  current design can close the grid. The over-max is "allowed" because no legal home exists in that part
  of the floor - the problem is upstream.
- **Growth stalls by design** (whole-strip rectangles; three cell-level alternatives were rejected,
  docstring L2231-2267). `_top_up_to_target` (L2380) would push short rooms toward target but is still
  **never called**, and it would not help here anyway: the short rooms and the free cells are on opposite
  sides of the spine.
- **The rebalance is bounded by walls and zones** (never crosses the band, never moves through the spine,
  undoes chains that make a room narrow, L3259-3271).
- **Hidden defeat of an existing guard:** the planner widens the spine only while the back zone has more
  cells than its rooms' combined max (L1234-1244). The Terrace's max is the whole footprint
  (`house_planner.py` L511, L495), so on any floor with a Terrace/Verandah the spine never widens - and the
  check is for the whole back zone, never per side.

### How common (all 35 gate floors, planner ON, chosen candidate)
A "compartment" = a connected part of the grid not crossed by the spine, inside one zone (front band or
back). Compartment cells > sum of the max of the rooms in it (a Terrace/Verandah counts as unbounded):
**20/35 floors**; fill overflow: 28/35; both: 19. Examples (sq ft, cells vs sum of max): 50x50_2f f1
544 vs 275, f2 489 vs 275; 60x80_3f_{south,north} f1 544 vs 275, {east,west} f1 567 vs 275; 150x150_dense
f1 726 vs 549, f2 1,380 vs 595; 150x200 f2 572 vs 35 (the stair alone - the famous 572 sq ft stair);
30x40_sparse 316 vs 180; 90x40_sparse 342 vs 180. Three floors have a compartment with NO room seeded in it
(60x80_3f s/n f2 161 sq ft, 150x200 f1 61+66) - a cross-link fragment only the fill can close.
Floors with zero excess: 30x40_small, all 4 40x60 facings, 40x60_garage, 50x50_typical, 60x80_dense,
90x40_typical, 150x150_typical/sparse, both 300x300, 60x80_3f_east/west f2.

## 2. Why upper floors have surplus, and why the planner does not shrink or add rooms

- **Footprint = ground floor's need.** `plan_building` (`house_planner.py` L580-602): `program_ceiling =
  bounds[0][1]` (ground floor only), area = min(max(band target, program_min), 0.85 x ceiling, envelope,
  estate cap). One footprint for all floors (walls stack). 100x150: ground ceiling 2,091 -> 1,778 enclosed +
  120 Verandah. 50x70: ground ceiling 1,875 (incl. Media Room) -> 1,594.
- **Upper programs are smaller.** Sum of maxes of the real upper rooms: 100x150 1,348 vs usable 1,670 ->
  **322 spare**; 50x70 915 vs 1,403 -> **488 spare**. `_allocate_floor` (L451-517) raises every room to max
  and puts the rest in a Terrace whose max is the whole footprint (L502-512).
- **No bonus rooms upstairs.** Standard tier: every `BONUS_ROOM_PRIORITY` entry is "ground", except
  Walk-in Closet (needs a room named "master") and Storage (target below `BONUS_ROOM_MIN_TARGET_SQM` 6.5 m^2)
  -> eligible list is empty on 50x70 floors 2-3. Spacious tier (100x150): the first plan DID add Library x2
  + Gym x2 upstairs (footprint 2,817), but the engine's bonus-drop loop (`floor_layout_mask` L844-858)
  dropped them (they came out as strips - same compartment problem), the re-plan excluded them
  (L1008-1011), and the footprint shrank only to the ground floor's ceiling (1,898).
- **Why not shrink:** shrinking to the upper floors' ceiling would starve the ground floor (its rooms would
  fall below target). The engine's own smaller-footprint search (L938-951) is switched off exactly on these
  floors: it is skipped when any floor's last-resort overflow exceeds 15% of its cells (both demo cases:
  18.5% and 17.7%), because overflow that size was (correctly) judged structural.
- Note: the 1.5x bedroom balance rule (`638d763`) cannot help: the planner's bedroom targets were already
  equal (207/207/207, 180/180); the imbalance is created by where the engine seeds them.

## 3. Design: bounded fill + fallback that can pass the gate

Principle: the fill can only be bounded if every part of the floor has a legal home for its cells BEFORE
growth. So the order is: measure -> assign -> (shift spine) -> sink on an outer wall -> bound the last
resort -> fallback chain. Every step is gated on "this floor has compartment excess"; floors with zero
excess (15 listed above) must stay byte-identical, which is the main protection for the gate.

Where surplus goes when every real room is at max, in order: (a) the floor's sink (Terrace/Verandah),
placed in the compartment that holds the excess, seeded on that compartment's outer wall; (b) the
corridor of that compartment, only if the pocket touches it (residue only); (c) a room up to the scorer's
own tolerance (1.10 x max, never past); (d) nothing fits -> the candidate is marked unbounded and loses;
(e) Phase 7 chain. Shrinking the footprint stays a building-level fallback, not the default, because the
ground floor needs the area. A Terrace cap or upstairs bonus rooms are product numbers to decide after
(a)-(e) work (change 7) - a cap without a second legal home is what broke the last attempt.

Footprint identical across floors: nothing below changes `plan_building`'s footprint; the spine offset is
per floor at first (it does not move walls), and can be made building-wide later together with the shared
stair rectangle (U3).

### Ordered changes (one at a time; each behind a module flag in the same style as the existing ones;
gate before/after; planner-off output byte-identical; render 50x70 f3 and 100x150 f2 and read the PNGs)

1. **Diagnostic only (no layout change).** Add a pure `_compartments(spine_cells, band, cols, rows)` and an
   informational metric `compartment_excess_sqft` per floor (report + scorecard, not ratcheted). Port the
   logic of scratchpad `compartments.py`. Done when the report shows the 20 floors above. Breaks nothing;
   check `scripts/mask_output_dump.py` before/after is byte-identical.
2. **Capacity-aware seeding (`_SEED_BY_CAPACITY`).** In `_grow_filled` after `_seed_rooms` (L1311): compute
   each seed's compartment; if any compartment has excess (> 10% of its smallest room's max, so 56x56 /
   60x80_typical, excess 16 and 10 sq ft, stay untouched), re-seed: choose an assignment of back-zone
   UNITS (bedroom + its ensuite together, lone rooms, the sink) to compartments that minimises total
   (cells - sum max)+ and (sum min - cells)+, the sink absorbing any excess where it sits; brute force is
   fine (<= 10 units, 2-4 compartments). Seed each unit on its compartment's part of the exterior boundary
   path; then `_fit_cells` per compartment instead of per zone (L1275-1280). Most likely to break:
   60x80_3f_* f1-f3 (suite contact, pass_through, orphaned ensuite - the Sonnet failure floors), 50x50_2f f2
   (4-part hallway), 150x150_dense_garage_2f f1 (unreachable) / f2 (narrow). Detect: gate test; excess -> 0
   on the changed floors; orphaned-ensuite and narrow-bedroom counts in the scorecard; renders.
3. **Sink on an outer wall (`_SINK_ON_EXTERIOR`).** The Terrace/Verandah seed is the exterior-boundary cell
   of its assigned compartment farthest from other seeds (not `last_cell`, L1965/L1998). Add a HARD scorer
   check `open_area_enclosed` (porch-category room with no footprint-edge segment >= 0.9 m), reusing the
   daylight edge test. Likely to break: the same 60x80_3f upper floors and 150x150_dense f2 (Sitting Area
   ballooned there). Detect: new check count 0; daylight not worse.
4. **Spine offset when no assignment balances (`_SPINE_OFFSET_SEARCH`).** `_reserve_spine` centres the
   spine (`cx0`, L1693); add an offset parameter and, only on floors still in excess after change 2, try
   offsets (|offset| <= 25% of cols) and keep the one with the smallest remaining excess. Targets
   30x40_sparse, 90x40_sparse, 150x200 f2 (stair alone in 572 sq ft), and the room-less cross-link
   compartments. Also make the spine-widening guard (L1234-1244) per compartment and exclude the sink's
   footprint-size max from `back_max`. Likely to break: 150x200 (tuned estate cap, documented as chaotic),
   50x50_2f. Detect: gate; stair_over_cap -> 0; hallway area vs plan circulation.
5. **Bound the last resort (`_FILL_BOUNDED_LAST_RESORT`).** Replace "whole pocket to one neighbour"
   (L3025-3043) by: sink in the same compartment via a chain that may pass the spine (treat spine cells as
   passable in `_nearest_to_sink`/rebalance adjacency for this purpose only); then neighbours up to
   ceil(1.10 x max); then hallway if the pocket touches the spine; else the old dump but counted as
   `stats["unbounded_cells"]` and a candidate penalty so best-of-N prefers bounded candidates. Coverage gap
   must stay 0 (the old dump remains the final guarantee). Likely to break: floors where the overflow was
   small and local (50x50_typical 46, 90x40_typical 74, 40x60 east/west 66, 60x80_dense 16 sq ft) - none of
   these have compartment excess, so the change must be a no-op there unless the pocket is > 1.10 x max.
   Detect: `oversized` -> 0 on every floor where `unbounded_cells` = 0.
6. **Phase 7 fallback chain** (`layout_building`, L872-916): if a floor still has `unbounded_cells` > 0 or
   hard violations: (a) re-run the footprint-scale search WITHOUT the 15% overflow guard (L935-951) - after
   2-5 the remaining overflow is no longer structural; (b) alternate footprint shapes at the same area
   (aspect swapped / squarer) via a new `plan_building` aspect override; (c) rect engine only if strictly
   fewer hard violations including daylight; (d) best-effort mask. Log `layout_path` per floor (INFO + 
   `meta_json` + report), keep the feasibility downgrade to "tight". Likely to break: runtime (cap
   attempts; 100x150 4-floor already ~1 min) and 150x200/300x300 (rect fallback footprint differs from the
   planner's yards - log it). Detect: gate; layout_path counts in the scorecard (extend `PATH_KINDS`).
7. **Product numbers (ask the user first):** Terrace cap (e.g. 100-150 sq ft or a % of floor), standard-tier
   upstairs bonus rooms (Study, Linen/Store, second sitting) seeded on an outer wall, and the small-plot
   yard rule. Only after 2-6, so capped surplus has legal homes. Likely to break: every upper floor with a
   Terrace (60x80_3f all facings f2-f3, 50x50_2f f2, 150x150_dense f2, 150x200 f2). Detect: gate +
   compartment excess must stay 0 after the cap.

Not covered by this plan and still needed for clean floors: `narrow` rooms born in growth (50% of final
hard violations, step-5 diagnostic). Change 2 removes the share caused by over-subscribed compartments (both
demo floors' narrow Bedroom 1 / Bedroom 6 are the short side of the same imbalance), but width-aware growth
remains a separate lever.

## 4. Opus list (`localhost-demo-screenshot-review.md`) - what this root work resolves

| item | resolved by this work? |
|---|---|
| U1 Terrace enclosed | **Yes** - change 3 (outer-wall seed + hard `open_area_enclosed`), relying on 2 for the right compartment |
| U2 oversized bedroom | **Yes** - changes 2, 4, 5 (cause traced above); 6 handles leftovers |
| U3 stairs not aligned | No - needs the shared stair rectangle (Phase 4b fixed block, now off). Change 4 is a prerequisite if the spine offset is later made building-wide |
| U4 bed in front of door | No - renderer (`c3eccab` partial; 3-door rooms still wrong) |
| U5 entrance / foyer disconnected | No - foyer-on-front-edge is a separate placement rule (`_FOYER_ON_FACING_EDGE` = False, L235, reverted after 11 regressions) |
| §2 #2 bedroom wraps others, #4 4x bedroom spread | **Yes** on the planner path (same fill mechanism) |
| §2 #3 pass-through bedrooms | Partly (fewer wrapped rooms; reachability rules unchanged) |
| §2 #6 second de-facto corridor strip, #7 kitchen boxed by dining | Partly (#6 is a fill strip; #7 is front-band growth shortfall, one compartment, not excess) |
| §2 #13 "tight" plan still shipped | **Yes** - change 6 retries before shipping |
| §2 #1 zig-zag sliver, #8 living hierarchy (flag-off), #5 Gemini hallway, #11 stairs, #12 coverage, #14 labels | No (flag-off path untouched; program, stair, product, renderer) |
| B: strip Gemini hallway; flag-off repair; label fitting | No |
| B: make planner the default | Enables it (Phase 6 decision), does not decide it |
| C: packer | Not needed if 2-6 pass the gate; stays the reference / fallback branch |
| C: stop unrequested bonus rooms | No - change 7 is the decision point |
| C: width-aware growth | Partly (only the narrow rooms caused by compartment imbalance) |
| C: footprint vs plot size | No (product decision) |

## Evidence files (scratchpad, not committed)
`review/fillprobe.py big4|m50x70` - per-stage tables and grids above (`out/probe_m50x70.txt`);
`review/planprobe.py` - planner ceilings and bonus lists; `review/compartments.py` -> `out/compartments.txt`
/ `.json` - the 35-floor capacity table.
