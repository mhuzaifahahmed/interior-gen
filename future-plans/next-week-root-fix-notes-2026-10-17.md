# Notes for next week's root fix (written 2026-10-17, from the user's localhost screenshots)

Written so it can be understood WITHOUT the screenshots. Read together with
`localhost-demo-screenshot-review.md` (sections 1-5, problems U1-U5) and `STATUS-2026-10-17.md`.

## Standing decision (user)
Do NOT patch these from the top (renderer tweaks, post-hoc room edits): every local fix moves space and creates
another defect. Fix them at the root next week: the planner/engine must enforce placement and size rules, the
scorer must check them. Nothing in this file was implemented. The only code change made on 2026-10-17 was the
bedroom bed mirroring (`c3eccab`, renderer only).

## The picture these notes come from
Localhost, Build a House, planner ON (`HOUSE_PLANNER_ENABLED=true`), plot **50 x 70 ft**, several floors; the
screenshot is **Floor 3**, drawn north-up, front (south) at the bottom. House footprint about 35.7 x 44.7 ft
(about 1,600 sq ft, roughly 46% of the 3,500 sq ft plot). Yards left around it (from the dimension chains): front
15.2 ft, rear 10.1 ft, each side 7.1 ft. The user said this output was "much more reliable" than earlier ones
(note: the only change since was the bed mirroring; the improvement is the planner being on and a smaller, simpler
request, not a new engine fix).

Floor 3 layout, left to right / top to bottom:
- West column: Bathroom 5 (7.8 x 7.8, 61 sq ft) in the top-left corner, then **Bedroom 5, 462 sq ft** filling
  the rest of the column (a tall room about 15 x 31 ft).
- Middle: a 4.5 ft wide Hallway running the full depth.
- East column: Bathroom 6 (52 sq ft) and the Staircase (7.8 x 4.5, 35 sq ft, "DN") at the top, then a
  **Terrace (open) "+", 15.6 x 21.2 ft = 331 sq ft**, then **Bedroom 6, 15.6 x 6.7 ft = 105 sq ft** at the bottom.
- South: Family Lounge, 35.7 x 11.2 ft = 398 sq ft, full width.

## Problem A - two "equal" bedrooms differ 4x (Bedroom 5 = 462, Bedroom 6 = 105)
- Both are ordinary bedrooms; the bedroom maximum in the rules is about 188 sq ft (an earlier run of the same
  engine printed `oversized 574 > 1.10 x max 206.9` for a bedroom; confirm the exact value in `room_specs.py` /
  `planning_rules.py`). Bedroom 5 is about 2.5x the max; Bedroom 6 is a 6.7 ft deep strip, i.e. a narrow room
  (likely also a hard `narrow` / `bedroom_aspect` warning).
- Why (same root as U2 in the review): the max is only a scorer warning. The engine's leftover-fill step may push
  a room past its max, and the planner has no step that spreads surplus area over several rooms evenly. The
  scorer already flags this as a hard violation but the plan is still shipped as "tight" (no fallback chain, Phase 7).
- Root fix: bounded fill (no room above its max; surplus goes to bonus rooms / a bounded sink / a smaller footprint)
  plus a balance rule for rooms of the same category (same-category rooms within a fixed ratio, e.g. 1.5x).

## Problem B - the Terrace is too big (331 sq ft)
- It is bigger than every bedroom and nearly as big as the Family Lounge. It is the planner's "remainder sink":
  upper floors get a Terrace to soak up whatever area is left after rooms reach their targets, so a floor with spare
  area gets a big open-air room. It is also enclosed (see review U1): ringed by Hallway, Bathroom 6, Bedroom 6.
- Wanted: a Terrace is a small amenity, not a dump for surplus. Needs a max (suggest a fixed cap, e.g. about
  100-150 sq ft or a % of the floor, to be decided with the user), it must touch an exterior wall, and the surplus
  must go elsewhere (bounded growth of real rooms, bonus rooms such as a study, or a smaller footprint).
- Interaction to remember: capping the Terrace alone pushes surplus into bedrooms (makes Problem A worse). Fix
  A and B together, in the planner's allocation step.

## Problem C - do not give every plot gardens and yards (user rule)
- Today the planner always reserves front / rear / side yards from the plot-band table (`planning_rules.py`:
  M band 12 / 8 / 5 ft front / rear / side minimum; here the real yards ended up larger: 15 / 10 / 7 ft) and the
  renderer draws them as hatched "front yard", "garden", "side yard" zones, even on a modest 50 x 70 plot. On
  small plots this takes area that rooms need, and it is what keeps the footprint small and room sizes cramped.
- User rule: on a small plot, skip the garden / yards so room sizes are NOT compromised. Room size takes priority
  over yards. Larger plots keep yards and the driveway.
- What needs deciding next week (do not guess): the exact "small plot" threshold (this 3,500 sq ft plot sits in
  the M band, 3,000-8,000 sq ft; the user's wording suggests it may count as small - ask), which yards go first
  (suggested order: garden / rear, then side yards, keep a minimal front setback only if a garage / driveway is
  requested), and whether the footprint may then grow to the plot minus minimum legal setbacks.
- Where it lives: `planning_rules.py` (PLOT_BANDS minimum setbacks and target coverage), `house_planner.py`
  (footprint area and yard offsets), `blueprint_svg.py` / `blueprint_dxf.py` (yard zone drawing, driveway). Gate
  baselines (`tests/data/mask_quality_baseline*.json`) will change; update them only with the reviewed diff.

## How to approach it (suggested order)
1. Decide the open numbers with the user: small-plot threshold, Terrace cap, same-category bedroom ratio.
2. Planner: yard policy by plot band, bounded allocation (no room above max, Terrace capped, balance rule).
3. Engine: seed the sink on an exterior wall; foyer on the front wall (U5); one stair rectangle shared by all
   floors (U3); bounded fill.
4. Scorer / gate: hard checks for room over max, same-category imbalance, open-air room without an exterior
   edge, front door not opening into the foyer, stairs not stacked. Run the 35-floor matrix before and after;
   never loosen thresholds; compare with the packer prototype (`proto/explicit-rectangles`) as the reference.
5. Re-render 50x70 (3 floors) and 100x150 (4 floors) and read the images, not only the numbers.
