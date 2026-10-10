# Phase A findings — baseline render of the `mask` engine, as-is

**Date:** 2026-10-10. **Scope:** read-only against `app/` — no engine code was touched. This is
the Phase A deliverable required by
`future-plans/mask-engine-adoption-and-layout-refinement.md` before any of Phases B–G start.

**Method:** `scripts/phase_a_render.py` (gitignored, kept in the repo for re-running) forces
`HOUSE_LAYOUT_ENGINE=mask` in-process and calls the real dispatcher
(`app.pipeline.layout_engine.layout_floor_with_warnings`) → `app.pipeline.blueprint_svg.
render_floor_blueprint`, i.e. the exact call path `generate_house.py` uses. 10 PNGs were
rendered and visually inspected (via the Read tool, not just "no exception raised") across the
full baseline matrix: the exact reported 150×150/2-floor case (both floors), 50×60/1-floor,
30×40/1-floor, 90×40/1-floor, and a north/east/west facing sweep on the 50×60 room list. All
PNGs are saved at `scripts/output/phase_a/*.png` (gitignored) plus a few cropped close-ups
(`*_crop.png`) referenced below. Numeric rect data (coordinates, hard violations) is in this
document's quotes and was also printed to stdout by the script — re-run it to reproduce.

**Headline: `mask` is NOT ready to ship as-is.** It fixes bedroom shape in the common/south-facing
case, which is real progress, but every one of the 10 renders had non-zero hard violations, public
rooms (Living/Dining/Hallway) are still bowling-alley slivers in every single case, and two new,
more severe bugs were found that are not among the original 7 problems: a literal unlabeled solid
black "hole" rendered inside the house on 4 of the 10 renders, and a master bedroom that collapses
to a ~1 ft sliver specifically under east/west facing. Both are described in detail below ("New
bugs found, not in the original 7").

---

## Per-problem verdict

### 1. House uses ~40% of the plot; rest renders as blank drafting sheet

**❌ Still broken — actually worse than `rect`, exactly as the plan predicted.** `mask` shrinks the
footprint more aggressively than `rect`. On the 150×150 ft case, the footprint bbox is roughly
42.6×42.6 ft (~1,815 sq ft) sitting in the lower-middle of a 22,500 sq ft plot — about **8%
coverage**, not ~40%. See `case1_150x150_south_floor1.png`: a tiny house floating in a sea of
empty grid with no yard/driveway treatment at all. This is squarely Phase C's job (not started;
out of scope for Phase A) and Phase C's framing ("mask makes the footprint smaller, so this
matters MORE") is confirmed correct by this render.

### 2. Bowling-alley rooms (62.6×25.6 ft master bedroom, 28×12 ft secondary bedrooms)

**🟡 Partially fixed — bedrooms mostly fixed, public rooms (Living/Dining) are NOT, and the fix is
inconsistent across facings.**

- **Bedrooms in the "typical" (south-facing) orientation came out compact/near-square**, a real
  improvement over `rect`'s reported bowling alleys:
  - Case 1 (150×150, south): Bedroom 2 10.7×9.6 ft (aspect 1.1), Master Bedroom 8.5×11.7 (aspect
    1.4), Bedroom 3 9.6×12.8 (aspect 1.3), Bathroom 2/Master Bathroom both 7.5×7.5 (square).
  - Case 2 (50×60, south): similar — Bedroom 2 10.4×9.6-ish, Bedroom 3 9.9×13.1, bathrooms square.
  - Case 3 (30×40): Bedroom 1 and Bedroom 2 both 10.4×11.5 (aspect 1.1).
- **But `Living Room` and `Dining Room` are bowling-alley slivers in every single case tested**,
  with no exception:
  - Case 1: Dining Room bbox 5.3×26.7 ft (aspect 5.0), Living Room's real carved shape is narrow
    enough that the renderer hid its area text entirely (the v17 "no label beats a cramped one"
    rule kicked in) — see the close-up crop `case1_dining_crop.png`, which shows Living Room and
    Dining Room as two parallel thin vertical strips, each barely wider than the furniture inside
    them, running most of the house's depth.
  - Case 2 (50×60, south): Dining Room 5.5×24.1 ft (aspect 4.4) — same sliver shape, see
    `case2_50x60_south.png`.
  - Case 3 (30×40): Living Room 6.6×18.3 (aspect 2.8), Dining Room 5.7×18.3 (aspect 3.2).
  - Case 4 (90×40 wide): Living Room's real rendered shape is 27.2×7.7 ft (aspect 3.5) — a sliver
    running the full width of the front of the house.
- **Bedroom 2 also came out as a bowling-alley sliver on the wide plot (case 4)**: bbox
  27.2×7.7 ft (aspect 3.5), spanning the full front width — the "compact near-square room growth"
  claim does not hold universally, it's case-dependent.
- **Master Bedroom specifically collapses to a near-zero sliver under east/west facing** on the
  identical case-2 room list that produced a normal 109 sq ft Master Bedroom under south/north
  facing: under east facing, Master Bedroom's bbox is 1.1×18.6 ft (20.4 sq ft, aspect **16.9**);
  under west facing it's the same shape mirrored. See `case5_50x60_east.png` /
  `case5_50x60_west.png` — the "MASTER BEDROOM" label is squeezed into a razor-thin vertical strip
  with no legible room behind it. This is a new, facing-dependent regression, not something the
  original 7 problems described.

**Verdict:** the headline "mask fixes bowling-alley rooms" claim is true only for bedrooms, only
in some orientations, and never for Living/Dining. Phase D (or a new phase) needs to target the
public zone specifically, not just verify it.

### 3. Inverted social hierarchy (a 1,469 sq ft dining room next to a 400 sq ft living room)

**🟡 Partially fixed — direction is correct on 3 of 4 cases, but still inverted on the small/tight
case.**

- Case 1 (150×150): Living Room's bbox area (774.5) >> Dining Room's (142.4) — correct direction.
- Case 2 (50×60): Living Room 166 sq ft > Dining Room 132 sq ft — correct direction, modest gap.
- Case 4 (90×40): Living Room 396 (labeled, right at the ~400 cap) vs Dining Room 125.8 sq ft —
  correct direction.
- **Case 3 (30×40 tight): INVERTED — Dining Room (175 sq ft) is bigger than Living Room
  (122 sq ft).** See `case3_30x40_south.png`, labels read "LIVING ROOM 6.6x18.3 ft (122 sq ft)"
  directly above "DINING ROOM 5.7x18.3 ft (175 sq ft)". This is the exact problem #3 failure mode,
  still present on a small/tight plot.

**Verdict:** not reliably fixed — case-dependent. A real fix needs living > dining to hold by
construction (e.g. a priority/weight bias), not just emerge from the current seeding order most of
the time.

### 4. Dining dwarfs living; rooms balloon past real-world caps

**🟡 Partially fixed — the absolute-area cap appears to be respected (no 1,469 sq ft dining rooms
were seen), but shape (not just area) is still badly broken for the same rooms.** Reported dining
areas across all 4 cases topped out at 175 sq ft (case 3) — nowhere near the original 1,469 sq ft
report. Living Room's area also stayed under or near its ~400 sq ft cap (396 on case 4). So the
*magnitude* of ballooning seems gone. But as described under #2, the *shape* these areas come in
(5–6 ft wide, 20–40 ft long slivers) is arguably just as architecturally broken as oversized
rooms were — a usable-space problem, not a numeric-cap problem. The `ABSOLUTE_MAX_AREA_SQM`
mechanism referenced in the plan's mapping table does appear to be holding; the bowling-alley
shape problem from #2 is the real remaining issue here, not raw area.

### 5. Master suite stranded in a central band, hallway running past it (no privacy)

**❌ Still broken, as expected pre-Phase-D.** On case 1 (150×150, south), Master Bedroom sits at
roughly the house's own front-middle area (y≈116–129, directly beside Garage/Entry, i.e. near the
entrance), while it's **Bedroom 2** that actually lands in the far, quiet back corner (y≈106–149,
the row farthest from Entry at y≈142). See `case1_floor1_crop.png` — "MASTER BEDROOM" and "MASTER
BATHROOM" sit directly below "GARAGE"/"ENTRY" near the house's own front edge, not tucked away in
a back corner. The plan's Phase D3 (seed the master on the boundary-path corner farthest from the
entrance) is squarely needed and nothing in the current `mask` seeding does this by accident.

### 6. A 78-ft single tunnel hallway (769 sq ft of pure circulation)

**❌ Still broken.** Every multi-bedroom case produced one long, full-depth (or near-full-span)
hallway, just repositioned to the house's own center rather than an edge — a real but small
improvement in *position*, none in *shape*:

- Case 1: Hallway 6.4×42.7 ft (273 sq ft, aspect 6.7) — spans almost the entire house depth.
- Case 2: Hallway 6.6×43.8 ft (**483 sq ft**, aspect 6.6) — bigger than the original report's
  769 sq ft is not reached, but the shape/aspect-ratio problem is identical in kind.
- Case 3 (30×40 tight): Hallway 3.8×38.5 ft (146 sq ft, aspect **10.1**) — proportionally the worst
  of the four, spanning essentially the full house depth on a tight plot.
- Case 4 (90×40 wide): Hallway ~6.0×26.3 ft (aspect 4.35).

The plan's claim that `mask` "reserves a compact central circulation spine (not an edge tunnel)"
is only half true: it IS central now (a real, visible improvement — see the render, the hallway no
longer hugs one wall), but it is not compact — it is a full-depth corridor in every case tested,
same architectural problem as the original report, just moved to the middle of the plan. Phase D5
("circulation compactness") is squarely in scope and necessary, not optional.

### 7a. Kitchen and dining ~100 ft apart

**✅ Looks fixed, by default, with caveats.** In every case where both exist, Kitchen and Dining
Room share a wall with a door between them:

- Case 1: Kitchen (x 86.7–96.3) borders Dining Room (x 81.4–86.7) along x=86.7, with a visible
  door swing connecting them — see `case1_dining_crop.png`.
- Case 2 (south): "DINING ROOM" and "KITCHEN" sit directly adjacent with a shared wall/door — see
  `case2_50x60_south.png`.
- Case 4: Dining Room and Kitchen are adjacent (x 48.0–57.1 vs 57.1–75.2).

**Caveat, not covered by Phase A's matrix**: Dining Room in case 1 is *also* directly adjacent to
Bedroom 3 (shares a wall + door), which is an odd kitchen/dining/bedroom circulation path — worth
a look during Phase D1 even though the core kitchen↔dining adjacency itself already works. Phase
D1's planned "verify, strengthen if needed" framing is correct — it's already mostly true, no
rework looks necessary here, just confirmation.

### 7b. Utility/laundry stranded away from the kitchen

**⚠️ Not evaluated — no room list in Phase A's matrix included a Laundry/Utility room.** The plan's
§2 code sample and the baseline matrix in §A1 of the plan do not call for a utility room in any of
the 5 required cases, so this was not tested. Phase D2 will need its own dedicated render (a floor
with both Kitchen and Laundry) before/after its change — flagging this explicitly so Phase D2 isn't
skipped under the assumption Phase A already covered it.

### 7c (garage half of problem 8). Garage: 1-car, buried, no driveway/road access

**🟡 Partially fixed.** On case 1 (south-facing), the Garage's own footprint-south edge (y≈149.2)
sits right at the footprint's own facing edge (the house's south wall, y≈149.3) — i.e. the garage
does reach the house's own road-facing wall, unlike the original report's "buried" garage. Shape:
16.0×21.3 ft (aspect 1.33) for 1 car — wider than strictly necessary but not a bowling-alley
sliver, and reasonably square. Car count honored exactly as typed (I passed `garage_cars=1`,
consistent with §1.5 decision 2 — no auto-bump observed, as expected since that logic isn't
present yet). **Still missing**: no driveway rendering exists at all yet (Phase C, not started) —
so even though the garage touches the house's own edge, there's no visual connection from there to
the plot's road boundary, and the huge yard gap (problem #1) means "touches the house wall" is not
the same as "visibly reachable from the road." This matches the plan's own framing (driveway
render = Phase C, shape/road-edge = Phase D4) — Phase A simply confirms the garage-placement half
already looks reasonable in the one case tested (south-facing, requested).

---

## Facing-transform sanity check (case 5, north/east/west on the 50×60 room list)

**✅ The public-zone-to-facing-edge transform itself is correct.** North puts Entry/Living/
Kitchen/Dining at the top (low-y) edge; east puts them at the right (high-x) edge; west puts them
at the left (low-x) edge — all confirmed visually (`case5_50x60_north.png`,
`_east.png`, `_west.png`) and match the documented north/south/east/west convention
(`floor_layout.py`'s docstring, reused by `mask`). Exact tiling (no gaps/overlaps outside the one
new bug below) held across all 4 facings.

**❌ But east/west facing specifically triggers the Master Bedroom collapse described under
problem #2** (1.1 ft wide sliver) and **the black-hole rendering bug described below**, in BOTH
east and west renders of the identical room list that rendered fine (if still imperfect) under
south. This means the facing axis-swap transform (`length`/`width` swap + mirror, per the plan's
own description of how mask reuses `_anchor_offsets`) is interacting badly with seeding/packing
for this specific room count — not just a south-only issue.

---

## New bugs found, NOT among the original 7 — must be investigated before Phase D, arguably before
## any further adoption work

### A. A literal unlabeled solid-black "hole" rendered inside the house footprint

On 4 of the 10 renders — `case2_50x60_south.png`, and all three of `case5_50x60_{north,east,
west}.png` (i.e. every facing variant of the 50×60/3-bed/2-bath/LKD/entry room list) — a large,
solid black rectangle renders **inside the house's own exterior wall boundary**, with no room
label, no furniture, no door, not accounted for by any of the 10 rects the engine returned. Close
inspection (`case2_blackbox_crop.png`) confirms it is not a wall, not a shadow, not an artifact of
image compression — it is a large filled black polygon sitting where the renderer's
"fill-the-whole-footprint-black-then-carve-out-each-room" technique (`blueprint_svg.py`'s poché
wall approach, see CLAUDE.md's v6 blueprint_svg entry) left an area **uncarved** — i.e. a region
inside the computed footprint bounding box that the mask engine never actually assigned to any
room. This is consistent with `mask` producing non-rectangular (L/U-shaped) room regions whose
bounding boxes can be larger than their actual filled area — the renderer trusts the footprint
bbox for the solid-black base fill, but only carves the real occupied cells back out, so any
genuinely unassigned cell within that bbox stays solid black forever.

This did **not** occur on case 1 (150×150, 11/7-room programs), case 3 (30×40, 6 rooms), or case 4
(90×40, 9 rooms) — so it is not universal, but it is 100% reproducible across every facing of the
specific case-2 room program (south/north/east/west all show it, just in different screen
positions as the geometry rotates), meaning it is a real, deterministic geometry bug tied to this
room count/program, not a one-off render glitch.

**This is a shipping blocker independent of the 7 original problems** — a floor plan with an
unexplained solid black block is a worse user-facing failure than any bowling-alley room, and
none of `layout_quality.score_floor_layout`'s hard-violation messages printed by the script
mentioned anything resembling "hole"/"uncovered area" for this case, meaning the scorer does not
currently detect this failure mode at all. Recommend treating this as a new, numbered Phase A
finding that blocks Phase E (flip) regardless of whether Phases C/D ship — a user must never see
this.

### B. Degenerate/near-vanishing rooms, independent of the facing-triggered Master Bedroom issue

Case 1's **upper floor** (`case1_150x150_south_floor2.png`) has a "Bedroom 5" that collapsed to a
near-zero region — bbox 59.5 sq ft but only **0.8 ft wide** (hard violation: `'0.8 < min width
8.9'`) — its label visually overlaps "BATHROOM 4"'s label in the render, and no real bedroom shape
is visible in that area of the PNG. This is a second, independent instance of the same failure
class as the Master Bedroom collapse (A room existing in the data/warnings but not really existing
as usable space in the render) — worth tracking as a single class of bug ("mask can produce
near-zero-width degenerate rooms") rather than two unrelated one-offs.

### C. Hard violations were non-zero on every single one of the 10 renders

None of the 10 Phase A renders came back with zero hard violations from
`layout_floor_with_warnings`. Every case had at least 3, and the worst (case 1 ground floor) had
5. This means the engine's own best-of-N candidate search (described in the plan as scoring and
keeping the least-bad candidate) is not currently landing on a zero-violation candidate for *any*
of the 5 baseline room programs — not just hard edge cases. This should temper how "already fixed"
Phases C/D treat the current `mask` baseline: there is no case in this matrix that mask currently
handles cleanly end-to-end.

---

## Summary table

| # | Problem | Verdict | Evidence |
|---|---------|---------|----------|
| 1 | Blank plot / no yard | ❌ still broken, worse than `rect` | case1 (~8% coverage) |
| 2 | Bowling-alley rooms | 🟡 bedrooms mostly fixed (south-facing only); Living/Dining always broken; Master Bedroom collapses under E/W facing | case1 crop, case2–5, case4 Bedroom 2 |
| 3 | Inverted social hierarchy | 🟡 fixed on 3/4 cases, still inverted on case 3 (30×40) | case3 |
| 4 | Dining ballooning past caps | 🟡 area caps hold; shape (slivers) still broken | case1–4 |
| 5 | Master suite stranded centrally | ❌ still broken (Bedroom 2 gets the quiet corner, not Master) | case1 crop |
| 6 | 78-ft tunnel hallway | ❌ still broken (now centered, still full-depth/not compact) | case1–4 |
| 7a | Kitchen↔dining split | ✅ looks fixed by default | case1, case2, case4 |
| 7b | Utility stranded | ⚠️ not tested (no utility room in matrix) | — |
| 7c | Garage buried, no driveway | 🟡 garage reaches footprint's own road edge; no driveway render at all yet (Phase C) | case1 |
| facing transform | — | ✅ correct public-zone-to-edge rotation | case5 ×3 |
| **NEW: black-hole bug** | not in original 7 | ❌ reproducible shipping blocker on 4/10 renders | case2, case5×3 |
| **NEW: degenerate rooms** | not in original 7 | ❌ Bedroom 5 (case1 floor2), Master Bedroom (case5 E/W) | case1 floor2, case5 E/W |
| **NEW: nonzero violations everywhere** | not in original 7 | ❌ all 10 renders had hard violations | all |

## Recommendation for re-scoping Phases C/D

- **Phase C (yard/site-planning) is fully necessary, unchanged scope** — problem #1 is untouched
  by `mask` on its own, confirmed worse than `rect`.
- **Phase D1 (kitchen↔dining) can likely be a quick verify-only task**, per the plan's own
  "verify first" framing — it already works in every case tested.
- **Phase D2 (utility adjacency) needs its own render matrix** — Phase A didn't exercise it at all.
- **Phase D3 (master corner) is fully necessary, unchanged scope** — confirmed still broken.
- **Phase D4 (garage shape/road-edge) is smaller than scoped** — the garage-reaches-edge part looks
  roughly correct already in the one case tested; the real remaining work is Phase C's driveway
  rendering, not the engine's garage placement.
- **Phase D5 (circulation compactness) should be promoted from "optional, only if Phase A flagged
  it" to required** — Phase A flagged it clearly and consistently (every case).
- **A new, un-numbered fix is needed before/alongside Phase D for the Living/Dining bowling-alley
  shape** — this isn't explicitly one of Phase D's five sub-tasks as written, and should be raised
  with the user before Phase D starts, since it's the single most visually damaging *unaddressed*
  problem after the two brand-new bugs below.
- **The black-hole rendering bug (new finding A) and the degenerate-room bug (new finding B) should
  be raised with the user before Phase D begins** — these were not anticipated by the plan and are
  arguably more urgent than several of the Phase D sub-tasks, since they are not cosmetic
  (bowling-alley) but structurally broken (unexplained black fill, rooms that are geometrically
  almost nonexistent). Recommend a dedicated investigation task be inserted before Phase D1,
  scoped to: (a) reproduce the black-hole bug deterministically (already done here — the 50×60/
  3bed-2bath/LKD/entry room list on any facing), (b) determine in `floor_layout_mask.py` whether
  this is a seeding/growth gap (some cells never claimed by any room) or a `footprint_bbox`/
  renderer mismatch (bbox larger than the union of actual regions), and (c) decide whether the fix
  belongs in the engine (grow to fill, or shrink the reported footprint to match actual coverage)
  or the renderer (don't blindly fill-then-carve across the full bbox).

Nothing in `app/` was modified to produce this report.

---

## Phase A2 addendum (2026-10-10) — both new bugs root-caused and fixed

**Scope of this addendum**: Phase A2 of the plan, scoped to exactly the two new bugs found above
("New bugs found, NOT among the original 7" — black hole, degenerate rooms) plus closing the
detection gap (finding C's "scorer doesn't notice this at all"). The original 7 problems' verdicts
above are UNCHANGED by this pass — Phase A2 was not scoped to touch bowling-alley Living/Dining,
the tunnel hallway, master-corner placement, etc.; those remain Phase D's job. Only
`app/pipeline/floor_layout_mask.py` and `app/pipeline/layout_quality.py` were modified —
`app/pipeline/floor_layout.py` (the `rect` engine) was never touched, confirmed by the full suite
staying green under the still-default `rect` engine (878/878, one pre-existing unrelated SQLite-
lock-contention flake under full-suite load, confirmed to pass cleanly in isolation and on a clean
rerun — the same already-documented flake class CLAUDE.md records elsewhere, not a regression from
this work).

### Bug A (black hole) — root cause, confirmed with direct instrumentation, not guessed

**Confirmed cause: engine-side gap, hypothesis 1 from the plan's A2.2, exactly as predicted.**
Direct instrumentation of `_grow_floor()` (counting `owner[r][c] == -1` cells before/after
`_fill_leftovers()`/`_ensure_minimum_presence()`) on the exact reproducing case (50×60/3-bed/2-bath/
LKD/entry, south facing, `cross_link=True` candidate) showed **168 cells still unclaimed** after both
passes were supposed to have resolved every leftover. Tracing one stuck cell's neighbors showed the
exact mechanism: `_fill_leftovers()`'s region-discovery loop recorded a neighbor into `adjacent_rooms`
only when `owner_val >= 0` (a real room) — a **spine cell** (`owner == -2`, reserved by
`_reserve_spine()`'s cross-link band) was neither `-1` (joins the same unclaimed region) nor `>= 0`
(counted as an adjacent room), so it was silently ignored as a neighbor entirely. The cross-link spine
is a plus-shape that can fully wall off one of its four quadrants if growth stalls there before
reaching every cell in it — the trapped pocket's only real borders were spine arms + the grid edge, so
`adjacent_rooms` came back empty and `if not adjacent_rooms: continue` left the region `-1` forever.
This 100% matches the reproduction: `cross_link=True` with a large (~square bbox) Hallway was
confirmed as the actual winning/selected candidate in the pre-fix run.

**Fix** (`app/pipeline/floor_layout_mask.py::_fill_leftovers`): a region whose only real neighbors are
spine cells is now absorbed INTO the spine (`owner = -2`, added to a (new) mutable `spine_cells` set
parameter) rather than left unclaimed — architecturally sound, since widening the corridor's own poché
into a dead-end pocket it already walls off is a normal outcome, not a hack. A second, narrower
last-resort branch (a region with NEITHER a real room NOR a spine neighbor — not expected to occur in
practice, kept only so no cell can ever stay `-1` under any room program) assigns it to whichever room
anywhere on the floor is furthest under its own target. `_grow_floor()` now recomputes the Hallway's
final rect from the real `owner` grid after `_fill_leftovers()` runs (not the original pre-fill
`spine_cells` snapshot), so the absorption is reflected in the actual rendered output.

### Bug B (degenerate rooms) — root cause, confirmed with direct instrumentation

**Not the plan's hypothesis 1 (seed/transpose) as originally worded, but a closely related dynamic
version of it — confirmed by instrumentation, not assumed.** A seed-time reachability probe (flood-fill
from each candidate seed, checking whether enough free cells are reachable) was implemented first and
tested — it did **not** change the output at all, because at seed-placement time almost the entire grid
is still empty (only a few other seeds are occupied), so a static reachability check at seeding time
cannot predict a trap. Direct cell-level tracing of the real failure (east-facing Master Bedroom,
identical room list) showed the true mechanism: Master Bedroom's seed row ended up sandwiched between
**two already-growing neighbors above it in the SAME row** (Master Bathroom claiming columns 0–6,
Living Room claiming columns 7–16 of the row directly above) and the **cross-link spine's horizontal
band directly below it** — by the time Master Bedroom's own turn came up in round-robin whole-strip
growth (`_grow_rooms()`), neighboring rooms with earlier array indices had already partially claimed the
one open direction in the *same* growth round, and whole-strip extension requires the **entire**
candidate strip to be free (a single already-claimed cell anywhere in the strip blocks the whole
direction). The room was left fully boxed on all four sides — zero free neighbor cells at all — so even
the existing free-cell arm fallback (`_grow_arm()`) found nothing to claim. This is a genuine **dynamic
race during round-robin growth**, not a static seeding defect — the plan's hypothesis 2 (arm fallback
claiming a thin trail) was the closer match once the real mechanism was traced, except the arm fallback
here claimed *nothing at all* (zero reachable free cells), not merely a badly-shaped trail.

**Fix, two parts**:
1. A reachability-aware seed-selection helper (`_best_reachable_boundary_seed()` /
   `_reachable_free_count()`) was added to `_seed_rooms()` anyway — kept because it's a real, if
   secondary, improvement (the common/already-working case resolves on its very first probe, so it's a
   no-op there) and because it's good defense-in-depth even though it didn't resolve the primary
   reproduction on its own.
2. **The fix that actually closed the gap**: a new absolute-last-resort "steal" pass in `_grow_rooms()`
   (`_steal_arm()`), run only for a room still under its own real minimum area after BOTH whole-strip
   growth AND the free-cell arm fallback have been exhausted. It BFS-reaches into an **already-claimed
   neighboring room's** cells (never a spine cell — circulation is never stealable), nearest first, but
   skips any cell whose current owner would drop below *its own* real minimum if it lost that cell — a
   donor is never pushed into a violation to fix this room's. This is one more rung on the EXISTING
   last-resort ladder (seed → whole-strip growth → free-cell arm → steal-from-slack-neighbor), not a
   growth/seeding redesign.

**A real, honestly-stated side effect found via re-rendering, not hidden**: because stealing cells can
leave the donor with an odd notch, a handful of OTHER rooms occasionally pick up a thin attached part
from this same mechanism (confirmed while writing the regression tests — e.g. a `Living Room` part came
back 1.1ft wide on the east-facing case, and a `Bedroom 4` part came back 1.5ft wide on the upper-floor
case). This is the SAME, already-documented, pre-existing M1 limitation `_fill_leftovers()`'s own
docstring already calls out ("a small number of rooms can still end up with a thin sliver part... a
known, honestly-stated M1 limitation") — not a new category of bug, and NOT what Bug B was (Bug B was a
room's *overall* area/shape collapsing to near-nothing, not an otherwise-fine room occasionally growing
one cosmetically thin extra part). The regression tests added below were scoped to the real Bug B
guarantee (the specific rooms' real total area, summed across parts, recovers to within 90% of their own
minimum) rather than a blanket "no room may ever have any thin part anywhere" rule, which would
effectively forbid this engine's own L/U-shaped-room feature.

### A2.6 — detection gap closed

`app/pipeline/layout_quality.py` gained `_coverage_violation()`, wired into `score_floor_layout()`: sums
every returned room's real area (parts-aware) and compares it against the candidate's own
`length × width`, flagging a HARD `coverage_gap` violation (with a 2% float-noise tolerance) whenever
they don't match. This directly closes the gap Phase A's finding C called out — the black-hole bug
previously produced zero hard violations because no individual *rect* was wrong, only a region neither
engine ever handed to anyone at all, which no existing per-room check could see. Verified with a
dedicated regression test that artificially shrinks one room's area (simulating an unclaimed region) and
confirms the new check fires.

### Re-render verification

All 10 Phase A baseline renders were regenerated (`scripts/phase_a_render.py`, unchanged) after the fix
and visually inspected via the Read tool (not just "hard-violation count went down"):
- **Zero black holes across all 10** — confirmed both by visual inspection of every PNG and by the new
  exact-area coverage check (`covered == footprint_area` within float tolerance) passing for every
  facing of the exact reproducing room list.
- **Zero collapsed/near-zero-width rooms across all 10** — Master Bedroom under east/west facing is now
  a real, visibly furnished room (confirmed in the re-rendered PNG, not just the printed numbers); the
  upper-floor "Bedroom 5" case similarly recovered to a real, usable shape.
- The pre-existing, OUT-OF-SCOPE bowling-alley Living/Dining/Hallway slivers from the original 7-problem
  verdicts above are still present exactly as before (case3's Living Room is still a narrow strip, for
  example) — Phase A2 was never scoped to fix those; they remain correctly tracked as Phase D's job.
- Hard-violation counts are NOT zero on every case (several cases still print 4–6 hard violations,
  mostly the pre-existing `narrow`/`sliver` kind tied to the still-open bowling-alley problem, plus a
  handful of the "a stolen cell created a thin part elsewhere" side effect described above) — this
  matches the plan's own A2.7 instruction to note, not necessarily fully fix, overall violation counts in
  this phase.

### New regression tests (`tests/test_floor_layout_mask.py`)

- `test_layout_floor_mask_bug_a_black_hole_full_coverage_south` — exact area-sum coverage check (not the
  file's existing sampling-based `_tiles_exactly()` helper, which was found to produce a false-positive
  "hole" at this exact geometry due to sampling-grid aliasing — confirmed by cross-checking with the
  exact arithmetic, which agreed there was no real gap; this is a real, useful caveat about that helper
  worth remembering for future geometry-sensitive tests) across all 4 facings of the exact reproducing
  room list.
- `test_layout_floor_mask_bug_b_master_bedroom_not_collapsed_east_west` / `..._upper_floor_bedroom_not_
  collapsed` — assert the specific rooms' real total area (summed across parts) recovers to within 90%
  of their own real minimum, for the exact reproducing room lists/facings.
- `test_score_floor_layout_flags_an_artificially_introduced_coverage_gap` — regression guard for the
  A2.6 detection-gap fix itself, not just the engine fix.

Full suite: **878/878 passing** (17 tests in `test_floor_layout_mask.py`, up from 13 before this pass).
