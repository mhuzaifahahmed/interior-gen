# House layout-engine spec checklist (2026-08-27 external spec, 37 numbered items)

Status against the numbered architectural spec you pasted on 2026-08-27 ("I want you to modify my
existing AI floor-plan generation system..."). Legend: ✅ Done · 🟡 Partial · ❌ Not done. Check this file
again any time to see what's actually implemented vs. still outstanding - update the status inline as
more of this gets built.

1. **Current system architecture** — ✅ Covered in the architecture report given before implementation.
2. **Critical architectural change (deterministic layout before SDXL, SDXL = visualization only)** — ✅
   Already true structurally; reinforced by the ControlNet-conditioning work (2026-08-27, same day) -
   SDXL never authors room positions, dimensions, or labels.
3. **Data flow (requirements → feasibility → placement → validation → doors/windows/stairs → furniture →
   CAD geometry → conditioning image → ControlNet+SDXL → final image)** — 🟡 Feasibility, placement,
   doors/windows/stairs symbols, furniture, CAD geometry, conditioning image, and ControlNet+SDXL all
   exist. **No real validation-and-regenerate loop** - a layout is computed once, deterministically, not
   validated then retried.
4. **No hardcoded house** — ✅ Frontend inputs are fully dynamic; Pydantic notebook defaults are fallback
   values only, not treated as architectural defaults anywhere in the pipeline.
5. **Inspect the frontend first** — ✅ Done before any implementation (architecture report section A).
6. **Input model (plot, floors, bedrooms, bathrooms, kitchens, garage, yard...)** — 🟡 Floors/bedrooms/
   bathrooms are structured dropdowns. **Kitchens-per-floor, garage, and front-yard are NOT separate
   structured fields** - explicit decision (your answer, 2026-08-27): parsed from the existing free-text
   `extras` field instead of adding more dropdowns.
7. **Dedicated layout-engine classes (HouseRequirements/Room/FloorPlan/LayoutEngine/LayoutValidator/
   GeometryRenderer)** — 🟡 Functionally equivalent modules exist (`floor_layout.py`, `room_specs.py`,
   `feasibility.py`, `blueprint_svg.py`) but kept as this project's existing pure-function style, not
   reorganized into those exact OOP classes - matches the spec's own "don't blindly create these classes
   if equivalent structures already exist" instruction.
8. **Room representation has real coordinates/dimensions, not just names** — ✅ Was already true before
   this pass (`layout_floor()`'s rects always carry real x/y/w/h).
9. **Layout generation logic (envelope → setbacks → footprint → reserve → circulation → shared → private →
   service → doors → windows → stairs → furniture → validate → optimize)** — 🟡 Envelope, front-yard
   setback, and minimum-area reservation are real now. **A real circulation-space step now exists**
   (2026-09-02) - the private zone gets a genuine reserved hallway corridor (`floor_layout.py`'s
   `_layout_private_zone()`/`_pack_row()`) when it has ≥3 rooms and enough depth to spare, with rooms
   opening onto it (`blueprint_svg.py` suppresses direct bedroom-to-bedroom doors in favor of hallway
   access) instead of the flat 20% area allowance being the only nod to circulation. **Still no
   validate-then-optimize loop** - single deterministic pass only; still no double-loaded (two-row)
   corridor, non-rectangular rooms, or a true adjacency-graph solver.
10. **Functional zoning (public/semi-public/private/service/circulation)** — 🟡 Public/private zoning
    already existed before this pass. **2026-09-04**: the public/private split is now genuinely
    front-to-back (`floor_layout._split_box_along_y()`), not just left/right on wide plots - see
    CLAUDE.md's v14 entry. **Semi-public/service/circulation still aren't distinct categories** -
    folded into the coarse public/private split (circulation/staircase is its own third zone rank, but
    not a full semi-public/service split).
11. **Garage (road-facing, real vehicle-clearance sizing)** — 🟡 Real minimum area scaled by car count is
    built, and (2026-09-04) a garage is now BUFFERED from living space by a real, auto-injected Entry
    room, with a direct Garage↔Living/Kitchen/Dining door suppressed - see CLAUDE.md's v14 entry.
    **2026-09-04 (v15): a real, optional North/South/East/West plot-facing input now exists**
    (`static/index.html`'s `#house-facing`, threaded through `layout_floor(..., facing=...)`), defaulting
    to South when the user doesn't choose one - the garage/public zone lands on whichever edge the
    resolved facing points to, not just always the low-y band. **Still not "verified road-facing"** - this
    is a USER-STATED orientation, not detected from the plot photo or any external source (e.g. GPS/map
    data), so it's only as accurate as what the user actually selects. See CLAUDE.md's v15 entry.
12. **Front yard (reserved before building, not squeezed in after)** — 🟡 Depth is reserved before any
    room is placed. **No pedestrian-path/vehicle-movement modeling** beyond the raw area reservation.
13. **Bedrooms (min area, exterior wall, window, door, furniture, clearance)** — 🟡 Minimum area is
    guaranteed, and (2026-09-04) a real MAXIMUM is now also enforced (`room_specs.ROOM_MAX_MULTIPLIER`),
    so a bedroom can no longer balloon OR shrink unboundedly. Exterior-wall/window placement is the
    pre-existing heuristic (unchanged this pass), not a new guarantee.
14. **Bathrooms (fixture geometry, no collisions)** — 🟡 Minimum (and now maximum) area only;
    fixture-collision handling (shower-only-with-room-to-spare) predates this pass, unchanged.
15. **Kitchens (counters/sink/stove/fridge, near dining)** — 🟡 Minimum/maximum area guaranteed.
    **2026-09-04**: kitchen and dining are now adjacent ranks in `_PUBLIC_ORDER_RANKS`, a real targeted
    fix (previously separated by living/study, not even list-adjacent) - still the same soft, list-order
    heuristic as the rest of this zoning scheme, not a hard geometric adjacency guarantee.
16. **Staircase (real footprint, straight/L/U choice, UP/DN)** — ✅ Done (2026-08-29). A real "Staircase"
    room is now injected into every floor's room list (`generate_house.py`), participates in the same
    minimum-area guarantee and feasibility check as any other room (`room_specs.py`'s "staircase"
    category), and `blueprint_svg.py` draws a straight or L-shaped run INSIDE that room based on its own
    real (post-layout) aspect ratio - not a fixed shape, not a guess. UP/DN was already correct. Visually
    verified (both straight and L-shaped renders). No U-shaped variant (only straight/L) - a smaller gap,
    not pursued since straight/L already covers the vast majority of real compact-stair footprints.
17. **Multi-floor coordination (wall/stair/plumbing alignment across floors)** — 🟡 Partial (2026-08-29).
    Exterior wall alignment was already exact (every floor renders the identical plot boundary at the
    identical canvas position). The staircase now gets a dedicated "circulation" zone rank between public
    and private (`floor_layout.py`), giving CONSISTENT relative placement + consistent guaranteed sizing
    across floors. **Pixel-exact interior staircase alignment is NOT achieved** - stated plainly in
    `floor_layout.py`'s own docstring: this project's rectangular slice-and-dice algorithm can't reserve
    an arbitrary interior rectangle at the exact same coordinates on every floor without a fundamentally
    different, non-rectangular-region layout algorithm. Plumbing-zone alignment (bathrooms/kitchens
    stacking vertically) is still not attempted at all.
18. **Room dimensions as min/preferred/max, not fixed** — 🟡 Minimum was already real. **2026-09-04**: a
    real MAXIMUM now also exists (`room_specs.ROOM_MAX_MULTIPLIER`/`max_area_for_room()`,
    `floor_layout._clamp_to_max_and_redistribute()`) - Gemini's relative weight still acts as the
    preference signal WITHIN that `[min, max]` band, and excess beyond a capped room's max is
    redistributed to other rooms (never to garage/staircase, which are pinned to exactly their
    functional minimum). See CLAUDE.md's v14 entry. **Still not "preferred" as a genuine third number**
    distinct from min/max - the weight-driven share within the band plays that role instead.
19. **Feasibility engine (FEASIBLE/TIGHT/NOT FEASIBLE, hard gate)** — ✅ This pass's core deliverable
    (`app/pipeline/feasibility.py`), wired as a hard gate per your explicit decision.
20. **Validation stage (overlaps, doors, windows, stairs connect floors, garage access, etc.)** — ❌ Not
    done beyond feasibility itself - no post-generation validator or regenerate loop.
21. **Candidate layouts + scoring** — ❌ Not done. Explicitly deferred (Phase 3).
22. **Conditioning image built from final computed geometry** — ✅ Done same day, prior turn
    (`app/pipeline/conditioning_image.py`), live-verified against the redeployed notebook.
23. **ControlNet follows that geometry** — ✅ Done + live-verified (`controlnet_conditioning_scale=0.95`
    when real geometry is sent).
24. **Prompt generation (no hardcoded room list when using real geometry)** — ✅ Done in the notebook
    update (`build_floor_prompt(..., using_real_geometry=True)` no longer invents rooms).
25. **Negative prompt (prevent 3D/perspective/broken walls/text)** — ✅ Already existed, unchanged.
26. **Labels/dimensions generated programmatically, not by SDXL** — ✅ Done (`_composite_room_labels()` in
    `kaggle_autocad.py`), live-verified - every label landed in its correct room.
27. **AutoCAD-style output (heavy exterior walls, dimension lines, symbols)** — ✅ Largely pre-existing
    (`blueprint_svg.py`'s poché walls, furniture, dimension strings, title block).
28. **Dimensions/labels from computed geometry, never invented by SDXL** — ✅ Same as #26, plus the
    pre-existing real `.dxf` export (`blueprint_dxf.py`).
29. **Layered rendering (CAD geometry / annotations / optional AI refinement)** — ✅ Conceptually in place:
    `blueprint_svg.py` = CAD + annotations (the source of truth), the AI "Concept Layout" card = a
    separate, clearly-labeled optional visual refinement layer.
30. **API response exposes structured room geometry, not just images** — 🟡 `room_layout_json` and
    `feasibility_json` are exposed. **Raw room rectangles (x/y/w/h per room) are NOT exposed via the API**
    - only the rendered PNG/DXF and the pre-layout room-weight list.
31. **Error handling (informative errors, not silent bad output)** — 🟡 Feasibility failures now return a
    real explanation instead of silent bad output. Other failure modes (invalid conditioning image, SDXL
    failure) already degraded gracefully via the pre-existing best-effort/fallback design.
32. **Inspect before coding** — ✅ Done.
33. **Architecture report before implementation** — ✅ Delivered (sections A-G) before any code changed.
34. **Do-not list (no hardcoded plot/floors/bedrooms/coordinates, no SDXL-authored labels/dimensions)** —
    ✅ Respected throughout.
35. **Test cases (4 described scenarios)** — 🟡 Automated pytest coverage exists for analogous scenarios
    (small plot + few rooms, garage car-count variation, a feasibility boundary case) but **not the exact
    4 manual scenarios as live end-to-end generations** through the actual notebook.
36. **Success criteria (13 listed)** — 🟡 Most are met structurally. The weakest: multi-floor structural
    coordination (#17) and layout variety/candidate scoring (#21) are still open.
37. **Most important principle (architecture = source of truth, SDXL = visualization only)** — ✅ This is
    the principle the whole pipeline already enforces, reinforced by everything built in this pass.

## What's most worth picking up next, if you want to keep going

**#9 gained a real circulation corridor (2026-09-02)** - see above for exactly what's still missing
(double-loaded/two-row corridor, non-rectangular rooms, no validate-then-optimize loop). **#16 is now
done, #17 is partial** (2026-08-29 - real staircase room + circulation zone; see above for
exactly what's still missing there: pixel-exact interior alignment and plumbing-zone stacking).
**2026-09-04: #10/#11/#15/#18 all moved from open gaps to solid 🟡 (see CLAUDE.md's v14 entry)** - true
front-to-back zoning, real min/max room proportions, kitchen-dining adjacency, and a garage buffered by a
real Entry room. In priority order, the real remaining gaps are now: **#20/21 (a real post-generation
validator + candidate scoring)**, **#13 (a real per-room exterior-wall/window guarantee, not just the
existing heuristic)**, non-rectangular/L-shaped rooms (would unlock more realistic room shapes than the
current pure rectangle slice-and-dice), and the remaining sliver of **#17 (plumbing-zone vertical
alignment)**. Everything else is either done or a smaller polish item.
