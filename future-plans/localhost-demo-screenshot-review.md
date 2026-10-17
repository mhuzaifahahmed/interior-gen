# Review of the localhost demo screenshot (Build a House, 2026-10-17)

Read-only review at HEAD `25fb4ea` (branch `Measuremens-model-Major-Refinement`). No tracked code was changed.
Scratch scripts and PNGs: session scratchpad `review/` (not committed).

## Summary for the user (non-technical)

- **The plan in the screenshot is not presentable as a "floor plan".** Several bedrooms are split into
  scattered pieces (one bedroom is made of 14 pieces on both sides of the hallway), the zig-zag
  "staircase-shaped" walls inside Bedroom 1 are a stray sliver of *Bedroom 4*, three bedrooms can only be
  reached by walking through Bedroom 3, and there is a 14 sq ft "Hallway" closet next to a 36 sq ft stair.
- **It is not a one-off glitch.** I fed the same request (100 x 150 ft plot, 2 floors, south-facing,
  6 bedrooms + 4 bathrooms per floor) through the real pipeline and got **the same picture** - same rooms,
  same areas, same walls. The engine is deterministic; this is what today's default does for a dense
  program. Asking for fewer rooms (3 bed / 2 bath) is still messy on this engine setting.
- **Turning on the "planner" setting is a visible improvement but not a cure.** Public rooms become
  sensible (Living 343 sq ft, Dining 244, Kitchen 219), half the bedrooms become clean rectangles - but some
  bedrooms still wrap around others, one bedroom becomes a 3 ft wide corridor, the stair can only be
  reached through a bedroom, and a generation takes 14-51 s instead of 0.4 s. Results vary a lot with small
  changes in the room list (which Gemini decides), so it is a lottery live.
- **The packer prototype (separate branch, not wired into the app) produces a genuinely good plan for this
  exact request** - every room a clean rectangle, a single straight corridor, stairs stacked on both
  floors. But it cannot be switched on today: it is not connected to the pipeline, its last commit is a
  WIP, and it adds rooms nobody asked for (Library, Media Room, Gym).
- **Best plan for today's demo:** turn the planner on locally, and demo Build a House with a moderate
  request that you generate and check *before* the meeting (e.g. 40 x 60 ft, 1 floor, 3 bedrooms,
  2 bathrooms - the planner output for that case looks like a real plan). Show it from History rather than
  generating live. Do not demo 6+ bedrooms per floor on a big plot. If asked, say the layout engine is an
  in-progress concept generator and a new engine that produces clean rectangular rooms already exists as a
  prototype (show `scripts/output/quality/demo/compare_*.png` or the packer renders) - see section 4A.

---

## 1. Reproduction (exact)

`review/repro.py` runs the real `run_house_pipeline` with a fake provider (no Gemini/Kaggle/OpenAI), temp
SQLite, in-memory storage, and a spy on `render_floor_blueprint` to capture rects. Program returned by the
fake "Gemini": floor 1 = Foyer, Living Room, Dining Room, Kitchen, Hallway, Bedroom 1-6, Bathroom 1-4;
floor 2 = Hallway, Bedroom 1-6, Bathroom 1-4. The pipeline itself injects Staircase (both floors) and a
Sitting Area (floor 2). Plot `{"length": 100, "width": 150, "unit": "ft"}`, facing south, floor_bedrooms
[6,6], floor_bathrooms [4,4], `house_layout_engine=mask`, planner OFF.

Result: floor 1 is **visually identical to the screenshot** (every label, area and wall matches:
Bedroom 3 390, Bedroom 1 301, Bedrooms 2/4/5/6 100, Bathrooms 40/40/40/56, Staircase 36, small Hallway 14,
main Hallway 7.0 x 42.7 ft / 299, Living 179, Dining 191, Kitchen 11.2 x 11.4 / 127, Foyer 5.6 x 7.1 / 40).
So the screenshot request was 4 bathrooms per floor (not 3), and Gemini's list contained its own "Hallway".
Plot inferred as 100 ft wide x 150 ft deep (house drawn at ~15 px/ft in the screenshot; canvas width ~100 ft).
Footprint: 37.8 x 56.9 ft = 2,151 sq ft on a 15,000 sq ft plot (14%).

True room composition (from the rect `parts`, planner OFF, floor 1):

| room | label area | pieces | what that means |
|---|---|---|---|
| Bedroom 3 | 390 | 14 | pieces on BOTH sides of the main hallway (the unlabeled pocket right of Bathroom 3 and strips around Bathroom 2 are Bedroom 3); its labeled part is a 4.2 ft wide strip that acts as a second corridor |
| Bedroom 4 | 100 | 11 | bbox 28 x 36 ft; includes a 1.4 ft wide diagonal staircase-raster strip *inside Bedroom 1's area* (the zig-zag walls) plus scattered right-side pieces; its labeled pocket is ~24 sq ft |
| Bedroom 1 | 301 | 8 | wraps around Bathroom 2 and Bedroom 4's sliver |
| Bathroom 4 | 40 | 4 | 1.4 ft wide strips around the small hallway |
| Dining Room | 191 | 3 | wraps the kitchen (11 ft strip above it and a 1.4 ft strip between kitchen and the east wall) |
| Staircase | 36 | 2 | at the minimum size (1.2 x 2.7 m); floor 2's stair (34 sq ft, bbox 8.9 x 38.7) is at a different spot - stairs do not stack |
| Hallway (2nd) | 14 | 2 | Gemini's "Hallway" room, separate from the engine's own spine |

Grid is 1.4 ft per cell, which is why slivers are exactly 1.4 / 2.8 ft wide.

## 2. Defect list (as an architect would write it) and causes

Cause classes: growth / fill / repair / planner / program / renderer / door logic / scorer blind spot.

| # | location | defect | cause |
|---|---|---|---|
| 1 | Bedroom 1, centre-left | zig-zag "staircase" wall enclosure inside the bedroom - actually a disconnected 1.4 ft diagonal sliver of Bedroom 4 | **growth** (round-robin cell growth leaves diagonal raster fronts) + **no repair on planner-OFF path** (`floor_layout_mask.layout_floor_with_warnings` flag-off path has no rectify/reconnect, see `mask-step5-diagnostic.md` "flag-off has no repair step"); renderer draws every part faithfully |
| 2 | Bedroom 3 (390 sq ft) right side | irregular room wrapping Bedrooms 4/5/6 and the stair block; also owns pockets on the far side of the hallway (unlabeled room right of Bathroom 3) | **growth** + **leftover fill** (largest neighbour absorbs left-over cells; diagnostic: fill's last resort pushes rooms past max on 32/35 floors) |
| 3 | Bedrooms 4, 5, 6 | doors open into Bedroom 3, not a hallway - pass-through-a-bedroom circulation | consequence of 2; **scorer blind spot** - the planner-OFF score has no `pass_through_private` / `wrapped` / multi-part checks (those are planner-only, `layout_quality.shape_violations` is opt-in) |
| 4 | Bedrooms 2/4/5/6 at 100 sq ft vs Bedroom 3 390, Bedroom 1 301 | 4x size spread among equal bedrooms; no master | **growth/fill** (targets are equal; final size decided by growth race + fill), planner-OFF has no hierarchy rule |
| 5 | top-right | Hallway 14 sq ft, Bathroom 4 40 sq ft (strips), Staircase 36 sq ft crammed together; "HALLWAY" and "BATHROOM 4" labels clipped by walls | **program** (Gemini listed a "Hallway" room on top of the engine's own spine) + growth; **renderer** (labels placed in a part too small to hold them) |
| 6 | main hallway | 7 ft x 43 ft corridor running the full depth to the rear wall; a second de-facto corridor (Bedroom 3 strip) parallel to it | engine's fixed full-depth spine template (growth); excess width from fill |
| 7 | Kitchen | walled box inside the Dining Room; dining wraps it on two sides (incl. a 1.4 ft strip along the outside wall) | **fill** (dining absorbs leftover around kitchen) |
| 8 | Living Room (179) | living room smaller than two bedrooms in a 6-bed house; narrow (9.8 < 10.8 ft min width, engine's own hard warning) | growth; planner OFF has no living-room target hierarchy |
| 9 | Entrance sequence | Entrance -> 40 sq ft Foyer -> Living Room -> Dining -> Hallway; acceptable order, but the foyer is a closet-size box in the corner | growth (foyer pinned small) - minor |
| 10 | Bathrooms 2, 3 | doors to two different rooms / odd positions; Bathroom 3 sits next to an unlabeled Bedroom-3 pocket | door logic follows the broken room shapes (door logic itself is not the root cause) |
| 11 | Stairs | floor 1 stair top-right (5.6 x 7.1), floor 2 stair elsewhere, scattered parts | no cross-floor stair stacking in the mask engine (known gap #17) |
| 12 | Plot | 2,151 sq ft house on a 15,000 sq ft plot (14% coverage), big front yard | **planner/footprint sizing** (footprint sized from room minima + coverage rule, not from plot); product decision noted in STATUS |
| 13 | Feasibility banner | engine already marks this "tight" with 10-12 hard violations (narrow rooms, too-short shared walls) but still ships the plan | by design (downgrade to "tight", not block) |
| 14 | Labels | DINING ROOM text overlaps the table symbol; underline rule runs through KITCHEN/FOYER titles | **renderer** (cosmetic) |

Not checked / cannot tell from the image: whether the door arc at the top of the right strip is on the
exterior wall (exterior door) or an interior door.

## 3. Render comparison (same request, 100 x 150 ft, south, 2 floors, 6 bed + 4 bath per floor)

| | (a) live default: mask, planner OFF | (b) mask, planner ON | (c) packer prototype (`proto/explicit-rectangles`, 9269c85 WIP) |
|---|---|---|---|
| time (deterministic part only, no Gemini) | 0.4 s | 14.4-41 s (same input, two runs); 51 s for a variant | 19 s pack (+ render) |
| engine hard warnings f1 / f2 | 12 / 8 (narrow, door_too_narrow); wrapped/pass-through not checked | 15 / 9 (incl. wrapped x5, pass_through x2, bedroom 12:1 aspect, Sitting Area 671 sq ft oversized) | packer self-check ok on both floors; only soft notes (notches, 2.7:1 bedrooms, over-target) |
| rooms with >2 pieces f1 | 6 (Bedroom 3: 14, Bedroom 4: 11) | 4 (Bedroom 2: 7) | 0 (four bedrooms have one notch) |
| footprint | 37.8 x 56.9 = 2,151 | 44.7 x 63.8 = 2,848 | 48.2 x 68.9 = 3,320 |
| stairs stack across floors | no | no | **yes** (same position) |
| what disappears | - | stray zig-zag sliver, tiny 100 sq ft bedrooms, kitchen-in-dining box (now L-shaped instead); living 343 / dining 244 / kitchen 219; Bedrooms 5/6 clean 163-183 sq ft | everything in section 2 except #12 |
| what remains / new | everything in section 2 | Bedroom 3 is a 3.2 ft wide 38 ft long strip; Bedroom 2 wraps Bathroom 3; stair + small hallway reachable only through Bedroom 4; interior "Verandah (open)" strip in the middle of the house; floor 2: Bedroom 2 is a 3.2 x 40 ft corridor, 671 sq ft Sitting Area; planner adds Study / Verandah / Terrace ("+") | planner adds rooms not asked for (Media Room, Library, Study; floor 2 Gym, Library, Library 2); Verandah/Terrace drawn as walled rooms (branch predates the unwalled-open-area renderer); 556 sq ft Verandah/Sitting Area; galley kitchen 8.6 ft wide; foyer 33 sq ft |

Extra probes:
- Dropping Gemini's own "Hallway" from the program (planner OFF): the 14 sq ft pocket goes away, everything
  else stays as bad (Bedroom 3 370 sq ft in 11 pieces). Planner ON without it: fewer hard warnings
  (f2: 2) but the entrance then opens straight into the living room, the Foyer sits off the kitchen, and
  Bathroom 4 is 13.9 x 3.1 ft. Output is very sensitive to the exact room list.
- 3 bed / 2 bath per floor, same plot: planner OFF still 9 / 3 hard warnings (a 1.2 ft wide bathroom),
  planner ON 10 / 3 (incl. a 5-piece oversized Bedroom 1), 25 s. Fewer rooms alone does not fix it.
- Existing side-by-side `scripts/output/quality/demo/compare_40x60_typical_1f_south_floor1.png` (40 x 60,
  1 floor, 3 bedrooms): planner ON and packer both look like real plans; live default has the same
  zig-zag and wrapped bedrooms.

## 4. Prioritized change list

### (A) Safe for today's demo (no code change)

1. **Enable the planner locally**: add `HOUSE_PLANNER_ENABLED=true` to your local `.env` and restart uvicorn
   (`--reload` does not pick up `.env` changes). Fixes on this screenshot: #1, most of #4, #7 (partly),
   #8. Leaves #2/#3 in a smaller form, #5, #6, #11, #12. Cost: 14-50 s extra per Build-a-House
   generation on a big dense plot (fast on small plots), and adds "+" rooms (Study/Verandah/Terrace) the
   visitor did not ask for. Risk to other flows: none (Room Redesign untouched; the flag only changes
   the mask layout path). Revert = delete the line and restart. Do NOT set this on Render today.
2. **Pick the demo input in advance and generate it before the meeting**, then show it from History:
   e.g. 40 x 60 ft, 1 floor, 3 bedrooms, 2 bathrooms, south. Check the result by eye; if it looks wrong,
   regenerate (Gemini's room list changes run to run, so a second try can differ). Avoid 6+ bedrooms per
   floor and very large plots.
3. **What to say if someone asks about defects**: "The floor plan is a computed concept layout from the
   plot size and room counts, not an architect's drawing - the label on the drawing says so. The current
   engine grows rooms on a grid, which on dense programs can produce irregular rooms. We have a new engine
   that places every room as a clean rectangle along a corridor with stairs aligned between floors - here
   is its output for this same request [packer render / compare image]. It is in testing and will replace
   the current one." Do not claim it is live.

No safe one-line code fix for the core defects exists - all of #1-#4 come from the engine's growth/fill.

### (B) Medium (about a day)

| change | effect on this screenshot | risk | effort |
|---|---|---|---|
| Strip Gemini-provided "Hallway/Corridor" rooms before the mask engine (it builds its own spine) | removes #5's 14 sq ft hallway pocket only | low; probes show layouts shift elsewhere | 1 agent run incl. matrix gate |
| Reject/repair disconnected rooms on the planner-OFF path (run the planner path's rectify/reconnect, or add `wrapped`/multi-part checks to the flag-off score) | would hit #1-#3 | medium-high: the diagnostic shows 7 of 11 engine fixes were reverted for making other floors worse | 2-3 runs |
| Renderer: skip labels that do not fit their part; move area text off furniture | #5/#14 cosmetic | low | 1 run |
| Make the planner the committed default after a review of latency | same as A1 for all users | medium (latency 14-50 s on big plots) | 1 run + review |

### (C) Large

| change | effect | risk | effort |
|---|---|---|---|
| Finish and wire the packer behind a flag with per-floor fallback to mask (STATUS item 1-4) | removes #1-#11 on this request (observed) | packer is WIP; adds unrequested bonus rooms, bigger corridors, more windowless rooms (STATUS caveats); 19 s here | 3-5 agent runs + review, 1-2 days per STATUS |
| Stop the planner adding bonus rooms by default, or only when asked | removes Library/Gym/Media Room surprises | low-medium | 1 run (in the packer work) |
| Width-aware growth in the mask engine | the real fix for mask's narrow/wrapped rooms | high, the diagnostic's "next real lever" | multiple runs, uncertain |
| Footprint sizing vs plot size (#12) | bigger house on big plot | product decision | decision first |

**Single best recommendation for the demo:** A1 + A2 - planner on locally, a pre-generated moderate
request shown from History, and the packer/compare images ready to answer the inevitable question.
Nothing here can make a live 6-bedroom-per-floor request on a 100 x 150 plot presentable by tonight.

---

## 5. Addendum (2026-10-17): problems the user found themselves on a SECOND localhost run

Run analysed: project `2935009f` - 100 x 150 ft, **4 floors**, 3 bedrooms + 2 bathrooms per floor, facing
default, **planner ON** (`HOUSE_PLANNER_ENABLED=true` is already in the local `.env`). Re-run through the real
pipeline with the stored room lists (`review/repro4.py`, scratchpad, not committed): same picture as the screenshot.
Footprint ~36.4 x 52 ft. Floors 2, 3 and 4 come out identical.

None of these four were in sections 1-4 above (section 2 #5-#6 and #11 touch the stair/hallway topic only).

| # | Problem (user's words, checked) | Verified? | Cause | Fix status |
|---|---|---|---|---|
| U1 | Terrace sits in the middle of the house, no air path | **Yes.** Floors 2-4: Terrace is 7.8 x 24.7 ft at x 39.6-47.4, ringed by Bedroom 1, Bathroom 1/2, Hallway and Bedroom 2; the outer wall (x 31.8) belongs to Bedroom 1 / Bathroom 1. It is drawn as open, but nothing opens it. | The Terrace / Verandah is the planner's "remainder sink" room; the engine gives it no rule to touch an outer wall (seeded for size, not position), and the scorer has no check "open-air room must have an exterior edge". Floor 1's Verandah does touch the west wall, so it is luck, not a rule. | Not fixed. Proposed: seed sinks on an exterior boundary cell; add a hard `open_area_enclosed` scorer check; fall back to dropping the Terrace (spare area goes to a bounded room) if it cannot be placed. |
| U2 | Bedroom 3 too large - is there a max, why did it not apply? | **Yes.** Floors 2-4 Bedroom 3 = 574-617 sq ft; floor 1 Bedroom 1 = 308 sq ft. | A max exists (bedroom ~188 sq ft; the scorer reports `oversized 574 > 1.10 x max 206.9` as a HARD violation, 3 floors + floor 1). It is only a **warning**, not a rule the engine enforces: (a) the leftover-fill step can push rooms past `max_cells` as a last resort (earlier diagnostic: 32/35 floors); (b) upper floors have far more floor area (~1,900 sq ft) than the program needs (~900), and the only sink (Terrace, 193 sq ft here) cannot absorb it, so the rest lands in a bedroom; (c) Phase 7 (fallback when the best candidate still has hard violations) was never built, so the plan ships labelled "tight" instead of being retried or repaired. I confirmed (c) and the warning text; (a)/(b) come from the earlier diagnostic and were not re-traced here. | Not fixed. Proposed: bounded fill (rooms never exceed max; surplus goes to bonus rooms / sink / smaller footprint) and build the fallback chain. This is the same root as the "planner/fill" work in the main plan. |
| U3 | Stairs not aligned between floors | **Yes.** Floor 1 stair at x 61.7, y 39.2; floors 2-4 at x 51.3, y 69.1. | The mask engine has no cross-floor stair stacking (known gap #17), stair is placed per floor. **User decision: do NOT patch it from the top for the demo; fix at the root with the earlier plan (single stair position decided once for the building, then each floor built around it).** | Deliberately deferred. Root fix = planner chooses one stair rectangle for all floors and every floor treats it as a fixed block (the packer prototype already does this). |
| U4 | Beds right in front of doors; furniture in general | **Yes.** Floor 1 Bedroom 1: door on the west wall touches the bed head; floor 2 Bedroom 3: door on the west wall beside the bed. Beds always sit in the left-hand corner. | The bed rule (`blueprint_svg._furnish_bedroom`) only checks the TOP and BOTTOM walls; a door on the LEFT or RIGHT wall is explicitly ignored ("known, accepted simplification" in its docstring) - and since the bed is always anchored at the left edge, a left-wall door always collides. Other pieces use an all-or-nothing skip only for the walls they touch. No door-swing clearance zone is kept. | Not fixed. Proposed: pick the bed position by trying the 4 walls x 2 ends and keeping one whose footprint plus a door-swing clearance square is door-free; apply a clearance check to every piece (not only wall-hugging ones). Renderer-only change, safe for flag-off identity. |

### Other things visible in the same run (not raised by the user)
- Floor 2-4: Bathroom 2 is a 15.6 x 2.6 ft strip and Bedroom 1 is 7.8 ft wide - both hard `narrow` violations.
- Floor 1: Bedroom 2 <-> Hallway shared wall is 1.3 ft (no real door), Bedroom 1 has 3 parts, Entry/Foyer is 13 x 3.9 ft.
- Feasibility banner says "tight" - the hard violations above are the reason, but the plan is still shown.

### Priority if these are tackled
1. U4 (renderer only, low risk, visible in every bedroom).
2. U1 + U2 together with the bounded-fill and fallback work (same root: the planner/fill does not enforce what the scorer already detects).
3. U3 with the stair-stacking root fix, as the user decided.
