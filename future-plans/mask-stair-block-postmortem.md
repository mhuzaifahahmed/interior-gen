# Fixed stair block: Step 5.5 retry postmortem (both attempts reverted; STAIR_FIXED_BLOCK stays False)

Context: `house_planner.STAIR_FIXED_BLOCK` (Phase 4b, 44132c5) pins the staircase to a fixed block
(1.2 m x (2.7 m run + 0.3 m landing), same cell position on every floor, beside the planner spine, nothing
grows it). It was off because every variant regressed 3-12 gate floors. Step 5.5 retried it on top of
Steps 5.2 (smaller-footprint candidates) and 5.3 (last-resort fill routes toward a Terrace/Verandah sink).
Planner flag stays off by default; flag-off is not affected by any of this.

The planner already counts the block (with its landing) as the staircase's target/min/max
(`_allocate_floor(stair_area=...)`), so the footprint/targets account for it; what neither attempt can
fix is the GEOMETRIC pocket the block leaves next to the spine.

## Attempt 1 - block alone (front end of the spine, right side), sink routing from 5.3

* Stairs: every multi-floor floor 40-54 sq ft (1.1-1.5x min; was 158 / 161 / 572 on 50x50_2f f2,
  60x80_3f south/north f2, 150x200 f2). Target <= 2.0x met everywhere.
* Gate (planner on): **11 failures** - narrow on 60x80_3f south/north/east/west floor 1; oversized
  Sitting Area on 60x80_3f south/north floor 3 (the area the stair used to soak goes to the lounge);
  150x150_dense f1 hard 8 -> 27 in the engine count (gate: new narrow/door_too_narrow, also fails the
  Phase-1 clearance test); orphaned Master Bathroom on 150x150_dense f2; front door moves Entry -> Living
  Room on 150x200 f1 (front_door_fallback); 50x50_2f f1 worse.
* Where the area went instead: the Hallway (150x150_dense f1 hallway 831 sq ft, 150x200 f2 736, 60x80_3f
  f2 442) - the pocket bordered only by the block and the spine has no room neighbour, so the existing
  Bug-A rule hands it to the spine. 5.3's sink routing never applies (no room neighbour to route from).
* Improvements it did make: 150x200 f1 9 -> 1, f2 2 -> 1; 60x80_3f south/north f2 2 -> 0; 50x50_2f f2 3 -> 0.

## Attempt 2 - block at the BACK end of the spine (chosen because attempt 1's failures were mostly
## ground-floor narrow rooms around the block at the public-band boundary)

* Stairs 40-52 sq ft everywhere (<= 1.5x min).
* Gate: **14 failures** - narrow on 60x80_3f floors 1 (all four), floor 3 east/west; narrow bedroom
  (Bedroom 4) on 60x80_3f south/north f3; bedroom aspect on 150x150_dense f1 and 150x200 f1;
  pass-through on 60x80_3f east/west f2; orphaned ensuite 150x150_dense f2; 50x50_2f f1 4 -> 12 (engine).
  The hallway dump moves to floor 3 (60x80_3f f3 hallway 581-650 sq ft).
* Engine hard total on the 18 changed floors 63 -> 56 (better overall) - but per-floor no-worsening is the
  acceptance rule, and 14 floors got worse.

## Why it keeps failing

The block removes the stair as the dumping ground but the engine has no other sensible receiver for the
pocket it leaves: rooms grow in whole strips and stall (Step 5.1: 50% of final violations start at
growth), so the pocket is either spine-only (-> hallway) or taken by the nearest habitable room, which
then becomes a strip (narrow) or oversized. Fixing this needs the pocket to be absorbed by design - e.g. a
landing/hall cell set planned around the block, or width-aware growth that does not stall - not another
placement of the same block.
