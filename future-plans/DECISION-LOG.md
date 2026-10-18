# Decision log: what we thought, what happened, what the user decided

Newest entries at the bottom. Add to this file every time a decision is made or a result comes in, so a
future session can follow the story without the screenshots. Nothing has ever been pushed (user rule:
never push without being asked).

## 2026-10-17 - demo day (localhost)
- User showed the app on localhost. Problems seen: tiny house on a big plot, capacity dropdown stuck at 1 bed / 1 bath
  (fixed, `25fb4ea`), bad layouts on a 100x150, 2-floor, 6-bedroom request.
- Opus reviewed the screenshot (`localhost-demo-screenshot-review.md`, sections 1-4): the live default engine
  (mask, planner off) cannot make presentable plans for dense programs; planner-on is better but unreliable;
  the packer prototype is good but not wired in. Advice: planner on locally for the demo, show a pre-checked
  moderate plan (40x60 ft, 1 floor, 3 bed / 2 bath) from History.
- User then found 4 more problems themselves (U1-U4 in section 5 of that file) plus U5 (entrance and foyer
  disconnected). U1 terrace enclosed, U2 oversized bedroom, U3 stairs not aligned, U4 beds in front of doors.
- Decision: do NOT patch from the top (each patch creates another defect). Only the bed/door fix was done
  (`c3eccab`, renderer only: bed mirrors right when the left wall has a door; still wrong in rooms with doors on 3 walls).
- User's later screenshot (50x70 ft, floor 3) added: Bedroom 5 462 sq ft vs Bedroom 6 105; Terrace too big
  (331 sq ft); skip gardens/yards on small plots so rooms are not squeezed. Written in
  `next-week-root-fix-notes-2026-10-17.md`.

## 2026-10-18 - trying the easy planner rules first
- Idea (user): keep the planner flag off in code, turn it on only for demos (already how it works: `house_planner_enabled`
  default False; the user's local `.env` has it on).
- We ran a baseline (35-floor matrix, planner on): clean floors 3/35, zero-hard 13/35
  (`scripts/output/quality/step0/baseline_planner_on.txt`, gitignored).
- A Sonnet agent tried three planner-level rules. Results:
  - Bedroom balance within 1.5x: committed (`638d763`) but changes nothing; the planner's targets were already balanced,
    so the 462 vs 105 gap comes from the ENGINE's leftover fill, not the planner.
  - Yards off on plots under 4000 sq ft: reverted (clean floors 3 -> 1, parts per room worse); the footprint is limited by
    the room program, not the yards.
  - Terrace cap 150 sq ft: reverted (surplus landed in bedrooms/ensuites and broke 3 floors).
- Nothing got worse: the two reverted changes were never committed; live output is unchanged.
- Lesson: the easy rules cannot work while the engine's fill step (`_fill_leftovers` in `floor_layout_mask.py`) can push
  rooms past their maximum (last resort, seen on 32/35 floors).

## 2026-10-18 - decision: finish the previous week's plan (the root fix)
- User decision: go back to last week's plan (`mask-planner-rules-and-quality.md`) and FINISH it, instead of more small
  fixes. Then look at what remains of Opus's list; some of it may be fixed by working on the root cause.
- What is left of last week's plan: bounded leftover fill (Phase 2, only partly bounded), Phase 6 (capacity dropdown
  retest, end-to-end run, flip planner default only when the user says), Phase 7 (fallback chain when a floor still has
  hard violations, docs / CLAUDE.md entry). Packer stays on its own branch as the fallback option.
- Working rules: Opus diagnoses, Sonnet implements with tight briefs; one change at a time; gate matrix before and after;
  flag-off output byte-identical; never push; never edit rect engine; do not edit `.env`; render and read images.
- Remaining open questions for the user: small-plot yard cutoff, Terrace cap, bedroom size ratio (numbers used so
  far were assumptions), and whether big plots should get bigger houses (product decision).

## 2026-10-18 - Opus diagnosis of the fill step (`fill-bounding-diagnosis-2026-10-18.md`, `69c5762`)
- Root cause of both oversized-bedroom cases (100x150 floors 2-4, 50x70 floor 3): the hallway (spine) splits the back of the
  floor into a left and a right part; rooms are seeded on one side or the other by position with no check that the side can
  hold them. One side gets too many rooms (they stay at minimum size), the other side is left with a big empty pocket, and the
  fill step's last resort dumps the whole pocket into the one bedroom there. Same cause explains the Terrace cap failure
  (it removed the only room with spare capacity on that side). On 20 of 35 gate floors some part has more cells than its
  rooms' combined max.
- Upper floors have spare area because one footprint (sized from the ground floor) is used for all floors; the smaller-footprint
  search is switched off above 15% overflow, which is exactly where these cases sit.
- Proposed 7 ordered changes: (1) diagnostic metric, (2) capacity-aware seeding, (3) Terrace on an outer wall + hard check,
  (4) move the spine / fix widening guard, (5) bounded last resort, (6) Phase 7 fallback chain, (7) product numbers
  (Terrace cap, upstairs bonus rooms, small-plot yards) only after 2-6. Would fix U1, U2, "tight plans still shipped"; would NOT
  fix U3 (stairs), U4 (bed drawing), U5 (foyer placement).

## 2026-10-18 - decision: order of work (user)
- First finish the previous plan from Phase 6 (safe form: capacity table, gate assertion, end-to-end renders; NOT the flag
  flip). Then run the consolidated list in section 6 of `localhost-demo-screenshot-review.md` (merges Opus list, U1-U5,
  the notes file, RF1-RF7 and Phase 7 into one order without collisions). Flag flip and docs come last.
