"""Door decisions, shared by every renderer (Phase 4a pure refactor).

Pure geometry, no I/O, no pixel drawing. These functions were moved VERBATIM
(bodies unchanged) out of blueprint_svg.py so the PNG blueprint, the DXF
export, the AI Concept Layout door overlay and (later) the scorer can all
reason about the same door set from one place. blueprint_svg.py re-exports
the old private names (`_shared_edge`, `_front_door_opening`, ...) so every
existing import keeps working.

`build_door_graph()` is the PNG renderer's door loop, extracted unchanged and
parameterised so each caller keeps its CURRENT behaviour exactly (see the
`parts_aware` / `suppress_garage_doors` flags).
"""

from itertools import combinations
from typing import NamedTuple

from app.pipeline.floor_layout import _zone_key, footprint_bbox as _footprint_bbox, room_parts
from app.pipeline.room_specs import classify_room_category

EDGE_TOL = 1e-4
MIN_SHARED_EDGE_UNITS = 0.3  # ignore corner-touching pairs with no real shared wall



def shared_edge(a: dict, b: dict) -> dict | None:
    """Returns the shared-wall segment between two adjacent room rects, or
    None if they don't share a meaningful wall. orientation "vertical" means
    the wall itself runs vertically (a's right edge touches b's left, or vice
    versa); "horizontal" means the wall runs horizontally."""
    if abs((a["x"] + a["w"]) - b["x"]) < EDGE_TOL or abs((b["x"] + b["w"]) - a["x"]) < EDGE_TOL:
        x = a["x"] + a["w"] if abs((a["x"] + a["w"]) - b["x"]) < EDGE_TOL else a["x"]
        y_start, y_end = max(a["y"], b["y"]), min(a["y"] + a["h"], b["y"] + b["h"])
        if y_end - y_start > MIN_SHARED_EDGE_UNITS:
            return {"orientation": "vertical", "pos": x, "start": y_start, "end": y_end}

    if abs((a["y"] + a["h"]) - b["y"]) < EDGE_TOL or abs((b["y"] + b["h"]) - a["y"]) < EDGE_TOL:
        y = a["y"] + a["h"] if abs((a["y"] + a["h"]) - b["y"]) < EDGE_TOL else a["y"]
        x_start, x_end = max(a["x"], b["x"]), min(a["x"] + a["w"], b["x"] + b["w"])
        if x_end - x_start > MIN_SHARED_EDGE_UNITS:
            return {"orientation": "horizontal", "pos": y, "start": x_start, "end": x_end}

    return None


def shared_edge_between_rooms(a: dict, b: dict) -> dict | None:
    """Parts-aware wrapper around shared_edge(): the LONGEST shared-wall
    segment across every (part of a, part of b) pair - one real door per
    connected room pair, never one per part-pair (an L/U room's own 2-3
    parts could each border the same neighbor, which must still only ever
    draw ONE door, same as a plain-rectangle room always has). A
    plain-rectangle room's single part is its own bbox, so this is
    byte-identical to a direct shared_edge(a, b) call whenever neither
    room has `parts`."""
    best: dict | None = None
    best_len = 0.0
    for pa in room_parts(a):
        for pb in room_parts(b):
            edge = shared_edge(pa, pb)
            if edge is None:
                continue
            length = edge["end"] - edge["start"]
            if length > best_len:
                best_len = length
                best = edge
    return best


def compute_hallway_adjacent(rects: list[dict]) -> frozenset[int]:
    """The id() of every rect that shares a real wall with the Hallway rect
    (2026-10, see floor_layout.py's "NO-BOWLING-ALLEY ROOMS" / banded
    packing). Computed once per floor and passed into
    should_suppress_direct_door() - see that function's `hallway_adjacent`
    parameter for why this matters: a private room packed into a far band
    (floor_layout._pack_rows_banded()) has NO direct hallway edge at all, so
    its doors to neighboring rooms must never be suppressed, or it would end
    up completely sealed with zero doors. Returns an empty frozenset (every
    lookup then misses, so suppression behaves as if hallway_adjacent were
    never passed) when there's no Hallway rect on this floor at all."""
    hallway_rect = next((r for r in rects if r.get("name") == "Hallway"), None)
    if hallway_rect is None:
        return frozenset()
    return frozenset(
        id(r) for r in rects if r is not hallway_rect and shared_edge_between_rooms(r, hallway_rect) is not None
    )


def orphaned_ensuites(rects: list[dict]) -> frozenset[int]:
    """Phase 4b: id() of every suite-tagged bathroom/powder room that shares
    NO wall with any non-bathroom room carrying its own suite tag. Such a
    room is an ensuite only on paper - it cannot be entered from its bedroom
    - so for door purposes it is a COMMON bathroom: it may open onto the
    hallway (should_suppress_direct_door's ensuite-to-hallway suppression
    no longer applies to it). Without this, the planner's upper floors
    produced doorless "ensuites" sitting across the corridor from their
    bedroom."""
    out = set()
    for r in rects:
        suite = r.get("suite")
        if suite is None or classify_room_category(r.get("name") or "") not in ("bathroom", "powder"):
            continue
        partners = [
            o for o in rects
            if o is not r and o.get("suite") == suite
            and classify_room_category(o.get("name") or "") not in ("bathroom", "powder")
        ]
        if not any(shared_edge_between_rooms(r, o) is not None for o in partners):
            out.add(id(r))
    return frozenset(out)


def should_suppress_direct_door(
    a: dict, b: dict, hallway_adjacent: frozenset[int] | None = None,
    orphan_ensuites: frozenset[int] | None = None,
) -> bool:
    """Real circulation corridor (see floor_layout.py's module docstring):
    when a Hallway rect exists on this floor, a direct door between two
    adjacent private-zone rooms is suppressed so they open onto the hallway
    instead (which the generic door loop above already draws for free, since
    the hallway shares a wall with every room it serves) - EXCEPT a real
    ensuite: a bathroom keeps its direct door to its OWN bedroom.

    Suite-aware pairing (2026-09-03, real user feedback that a "master
    washroom" ended up nowhere near the master bedroom): floor_layout.py's
    _arrange_suites() now tags each bedroom+bathroom pair with a shared
    "suite" id. A bathroom keeps a direct door ONLY to a room carrying the
    SAME suite id - never to an unrelated neighboring bedroom it happens to
    be packed next to, and never to a second bathroom. A standalone/common
    bathroom (no suite id) opens only onto the hallway. This replaced the
    old rule ("keep the door whenever either room is a bathroom"), which
    drew a wrong door from a bathroom into whatever bedroom happened to sit
    beside it once suites interleave bedrooms and bathrooms along the row.

    hallway_adjacent (2026-10, see compute_hallway_adjacent()): a private
    room with NO direct hallway edge (possible once floor_layout.py's
    "no bowling-alley rooms" banded packing puts a room in the far band -
    see floor_layout._pack_rows_banded()) has no OTHER way to reach the
    rest of the house except through a direct neighbor, so its doors are
    NEVER suppressed by this function regardless of the usual bedroom<->
    bedroom rule below - suppression only fires when BOTH rooms already
    have their own independent hallway access, so losing this one direct
    link can't orphan either side. None (the default) preserves the
    ORIGINAL behavior exactly (every private room is assumed hallway-
    adjacent, which was always true before banded packing existed).

    Only called when has_hallway is True - a private zone with no corridor
    (too few rooms, or not enough space) keeps the original "any shared edge
    gets a door" behavior unconditionally, since direct adjacency is its
    only way in."""
    a_hall = a["name"] == "Hallway"
    b_hall = b["name"] == "Hallway"
    if a_hall or b_hall:
        # An ENSUITE bathroom (a bathroom carrying a suite tag) is private to
        # its own bedroom - it must not also open straight onto the hallway.
        # A standalone/common bathroom (no suite tag) still does. Every other
        # hallway door is kept (that's how rooms reach the corridor).
        other = b if a_hall else a
        if classify_room_category(other["name"]) == "bathroom" and other.get("suite") is not None:
            # An "ensuite" that cannot reach its own bedroom is a common
            # bathroom in practice (orphaned_ensuites()) - it keeps its
            # hallway door. None (default) = the original rule exactly.
            return not (orphan_ensuites is not None and id(other) in orphan_ensuites)
        return False
    if _zone_key(a["name"]) != 2 or _zone_key(b["name"]) != 2:
        return False
    if hallway_adjacent is not None and (id(a) not in hallway_adjacent or id(b) not in hallway_adjacent):
        # At least one side has no other way to the hallway - keep this
        # door rather than risk sealing a far-band room off entirely.
        return False

    a_bath = classify_room_category(a["name"]) == "bathroom"
    b_bath = classify_room_category(b["name"]) == "bathroom"
    if not a_bath and not b_bath:
        return True  # two non-bathroom private rooms (e.g. bedroom<->bedroom) - via the hallway
    if a_bath and b_bath:
        return True  # two bathrooms should never interconnect

    # Exactly one is a bathroom: keep the ensuite door ONLY within the same
    # suite (both tagged, same id). No tag, or different tags, means these
    # two rooms aren't a real pair - suppress it.
    suite_a, suite_b = a.get("suite"), b.get("suite")
    if suite_a is not None and suite_a == suite_b:
        return False
    return True


WET_ROOM_CATEGORIES = ("bathroom", "powder")
BATHROOM_FORBIDDEN_NEIGHBORS = ("living", "dining", "kitchen", "family")


def should_suppress_bathroom_public_door(a: dict, b: dict) -> bool:
    """Phase 4b circulation rule, applied on EVERY floor (with or without a
    hallway): a bathroom / powder room never opens straight into a living,
    dining, kitchen or family room. It may open onto the hallway, an
    entry/foyer, or its own suite bedroom (those doors are unaffected). Only
    a pair carrying the SAME suite tag is exempt (an ensuite is never tagged
    to a living room in practice, but the rule is stated by suite, not by
    name). If this leaves a bathroom with no door at all, that is a LAYOUT
    problem - no substitute door is invented here; the scorer's
    reachability check (layout_quality) reports it as `unreachable`."""
    a_cat, b_cat = classify_room_category(a.get("name") or ""), classify_room_category(b.get("name") or "")
    if a_cat in WET_ROOM_CATEGORIES:
        other_cat, wet, other = b_cat, a, b
    elif b_cat in WET_ROOM_CATEGORIES:
        other_cat, wet, other = a_cat, b, a
    else:
        return False
    if other_cat not in BATHROOM_FORBIDDEN_NEIGHBORS:
        return False
    suite = wet.get("suite")
    return not (suite is not None and suite == other.get("suite"))


GARAGE_SUPPRESSED_NEIGHBORS = ("living", "kitchen", "dining")


def should_suppress_garage_direct_door(a: dict, b: dict) -> bool:
    """Garage buffered from living space (2026-09-04, real user critique:
    "the garage takes a very large and somewhat disruptive central portion
    of the plan" / "do not force garage circulation through the main living
    room"). When a real Entry/foyer room exists on this floor, a direct door
    between the Garage and Living/Kitchen/Dining is suppressed - garage
    traffic routes through the Entry room instead (the generic door loop
    already draws Garage<->Entry and Entry<->Living/Kitchen/Dining doors for
    free, same mechanism the Hallway rect already relies on for the private
    zone). No suppression when there's no Entry room on the floor - direct
    adjacency stays the only way in, same fallback philosophy as the
    hallway-corridor threshold in should_suppress_direct_door().

    Only called when has_entry is True (see the door-drawing loop above)."""
    a_cat, b_cat = classify_room_category(a["name"]), classify_room_category(b["name"])
    categories = {a_cat, b_cat}
    if "garage" not in categories:
        return False
    other_cat = b_cat if a_cat == "garage" else a_cat
    return other_cat in GARAGE_SUPPRESSED_NEIGHBORS


# ---- Front entrance ----
# Real gap fixed 2026-09-19 (user-reported): the door loop above only ever
# draws doors between two ADJACENT ROOMS - nothing cut an opening in the
# EXTERIOR plot boundary, so there was no main house entrance at all, and
# the chosen N/S/E/W facing had no visible proof it was honored (it only
# affected room placement, invisibly). layout_floor()'s own facing
# convention (see its docstring) is: north front = low-y (top), south =
# high-y (bottom), east = high-x (right), west = low-x (left) - the front
# door lands on whichever real room touches that edge.
FACING_ORDER = ("north", "south", "east", "west")


def front_door_opening(
    rects: list[dict], facing: str | None, footprint_bbox: tuple[float, float, float, float]
) -> dict | None:
    """Edge-only wrapper around front_door_room() (the historical API every
    renderer calls). See front_door_room() for the selection rules."""
    return front_door_room(rects, facing, footprint_bbox)[1]


def front_door_room(
    rects: list[dict], facing: str | None, footprint_bbox: tuple[float, float, float, float]
) -> tuple[dict | None, dict | None]:
    """Picks the exterior boundary edge matching `facing` and returns a
    door-opening edge-dict of the SAME shape shared_edge() returns
    ({orientation, pos, start, end}), spanning whichever real room's own
    span on that edge - so it can be drawn with the same door-drawing
    geometry already used for interior doors (see _draw_front_door()).

    Room selection prefers a real foyer/entry room if one touches the
    facing edge, else any public-zone room, else whichever room has the
    largest span on that edge (a defensive fallback - expected to rarely
    matter, since generate_house.py already injects an Entry room whenever
    a garage was requested, and the public zone is always placed on the
    front edge by layout_floor()'s own front-to-back zoning).

    `footprint_bbox` (2026-10, Part 1 "smart canvas sizing" - replaces the
    old `length`/`width`/`unit` params): (min_x, min_y, max_x, max_y) of the
    HOUSE's own exterior wall, from floor_layout.footprint_bbox() - not the
    bare plot's 0/length/width, since a real yard (an oversized plot shrunk
    to a realistic footprint) can leave the house's own exterior wall sitting
    well inside the plot. Numerically identical to the old "exterior inset
    from 0/length/width" logic whenever the footprint fills the plot (the
    normal case), so this is a behavior-preserving refactor there.

    Returns (None, None) when `facing` isn't a real value - matches this
    module's existing "no signal, no mark" restraint (e.g. `_furnish_*`'s
    silent skip for an unrecognized room name).

    Phase 4b rule: the entrance is NEVER the garage. Preference: a foyer/
    entry touching the facing edge, else a non-garage public room touching
    it, else any other non-garage room touching it. When only the garage
    touches the facing edge, returns (None, None) - no front door is drawn
    on that floor and the scorer reports `entry_missing`. (The garage's own
    vehicle door is not the entrance.)"""
    facing = (facing or "").strip().lower()
    if facing not in FACING_ORDER:
        return None, None

    fp_x0, fp_y0, fp_x1, fp_y1 = footprint_bbox

    if facing == "north":
        orientation, pos = "horizontal", fp_y0
        touches = lambda p: abs(p["y"] - pos) < EDGE_TOL
        span = lambda p: (p["x"], p["x"] + p["w"])
    elif facing == "south":
        orientation, pos = "horizontal", fp_y1
        touches = lambda p: abs((p["y"] + p["h"]) - pos) < EDGE_TOL
        span = lambda p: (p["x"], p["x"] + p["w"])
    elif facing == "west":
        orientation, pos = "vertical", fp_x0
        touches = lambda p: abs(p["x"] - pos) < EDGE_TOL
        span = lambda p: (p["y"], p["y"] + p["h"])
    else:  # east
        orientation, pos = "vertical", fp_x1
        touches = lambda p: abs((p["x"] + p["w"]) - pos) < EDGE_TOL
        span = lambda p: (p["y"], p["y"] + p["h"])

    # Parts-aware (2026-10, grid-mask engine): a room's own PART touching
    # the facing edge, not just its bbox - an L/U room's bbox can touch the
    # facing edge even when none of its real parts do (the empty notch),
    # which would wrongly offer a door opening onto space the room doesn't
    # actually occupy. A plain-rectangle room's single part is its bbox, so
    # this is byte-identical to before there.
    candidates = [
        (r, p) for r in rects for p in room_parts(r)
        if touches(p) and classify_room_category(r.get("name") or "") != "garage"
    ]
    if not candidates:
        return None, None

    foyers = [(r, p) for r, p in candidates if classify_room_category(r["name"]) == "foyer"]
    public = [(r, p) for r, p in candidates if _zone_key(r["name"]) == 0]
    # Phase 4b: the last-resort front door prefers any room that is not a
    # bedroom/bathroom/powder room (a hallway, a lounge, a study...) before
    # falling back to a private room - a front door straight into a
    # bathroom is never chosen while another room touches the facing edge.
    not_private = [
        (r, p) for r, p in candidates
        if classify_room_category(r["name"]) not in ("bedroom", "bathroom", "powder")
    ]
    pool = foyers or public or not_private or candidates
    room, part = max(pool, key=lambda rp: span(rp[1])[1] - span(rp[1])[0])

    start, end = span(part)
    return room, {"orientation": orientation, "pos": pos, "start": start, "end": end}


def room_door_walls(rect: dict, door_edges: list[dict]) -> set[str]:
    """Returns which of a room's four walls ("top"/"bottom"/"left"/"right")
    carry a real door opening - built from the SAME edge-dicts already used
    to draw doors (interior, post-suppression, plus the front entrance), so
    furniture placement (see _draw_furniture()) can avoid blocking a door
    without duplicating any door-suppression logic. The overlap check
    (edge start/end vs. this room's own span) guards against a coincidental
    same-position wall on an unrelated, non-adjacent room."""
    walls: set[str] = set()
    for edge in door_edges:
        if edge["orientation"] == "horizontal":
            room_start, room_end = rect["x"], rect["x"] + rect["w"]
            overlaps = edge["start"] < room_end - EDGE_TOL and edge["end"] > room_start + EDGE_TOL
            if not overlaps:
                continue
            if abs(edge["pos"] - rect["y"]) < EDGE_TOL:
                walls.add("top")
            if abs(edge["pos"] - (rect["y"] + rect["h"])) < EDGE_TOL:
                walls.add("bottom")
        else:
            room_start, room_end = rect["y"], rect["y"] + rect["h"]
            overlaps = edge["start"] < room_end - EDGE_TOL and edge["end"] > room_start + EDGE_TOL
            if not overlaps:
                continue
            if abs(edge["pos"] - rect["x"]) < EDGE_TOL:
                walls.add("left")
            if abs(edge["pos"] - (rect["x"] + rect["w"])) < EDGE_TOL:
                walls.add("right")
    return walls


class DoorGraph(NamedTuple):
    """pairs: every adjacent room pair as (a, b, edge, suppressed) - the
    DXF needs the suppressed ones too (it still draws their wall).
    front: the front-door opening edge (ground floor only) or None.
    front_room: the room the front door opens into (Phase 4b), or None."""

    pairs: list
    front: dict | None
    front_room: dict | None = None

    @property
    def interior(self) -> list:
        """[(a, b, edge)] for pairs that really get a door, in loop order."""
        return [(a, b, e) for a, b, e, s in self.pairs if not s]

    @property
    def edges(self) -> list:
        """Every door edge to draw: interior (post-suppression) + front."""
        return [e for _a, _b, e in self.interior] + ([self.front] if self.front else [])


def build_door_graph(
    rects: list[dict],
    plot_dims: dict | None = None,
    facing: str | None = None,
    floor_number: int = 1,
    *,
    parts_aware: bool = True,
    suppress_garage_doors: bool = True,
    include_front_door: bool = True,
) -> DoorGraph:
    """The door loop blueprint_svg.render_floor_blueprint() always used,
    extracted unchanged: hallway/entry gating, the `combinations` loop, the
    suppression calls and the ground-floor-only front door.

    Phase 4b: every caller (PNG, DXF, Concept Layout, scorer) now uses the
    defaults - parts-aware shared edges and garage-door suppression - so all
    renderers agree. The bathroom rule (should_suppress_bathroom_public_door)
    applies on every floor, with or without a hallway. parts_aware=False /
    suppress_garage_doors=False remain only as explicit opt-outs (unused by
    production callers). include_front_door=False skips the front-door
    lookup."""
    dims = plot_dims or {}
    length = float(dims.get("length") or 1)
    width = float(dims.get("width") or 1)
    has_hallway = any(r.get("name") == "Hallway" for r in rects)
    has_entry = any(classify_room_category(r.get("name") or "") == "foyer" for r in rects)
    hallway_adjacent = compute_hallway_adjacent(rects)
    orphans = orphaned_ensuites(rects) if has_hallway else frozenset()
    edge_fn = shared_edge_between_rooms if parts_aware else shared_edge
    pairs = []
    for a, b in combinations(rects, 2):
        edge = edge_fn(a, b)
        if edge is None:
            continue
        suppressed = False
        if has_hallway and should_suppress_direct_door(a, b, hallway_adjacent, orphans):
            suppressed = True
        elif suppress_garage_doors and has_entry and should_suppress_garage_direct_door(a, b):
            suppressed = True
        elif should_suppress_bathroom_public_door(a, b):
            suppressed = True
        pairs.append((a, b, edge, suppressed))

    front = front_room = None
    if include_front_door and floor_number == 1:
        fp = _footprint_bbox(rects) or (0.0, 0.0, length, width)
        front_room, front = front_door_room(rects, (facing or "").strip().lower(), fp)
    return DoorGraph(pairs, front, front_room)


# ---- Phase 4b: circulation (reachability from the entry) ----
# Rooms whose failures are NOT counted (they still tile and still carry
# doors): the planner's open remainder rooms (ground-floor Verandah, upper
# Terrace) and any other porch-category open area - rendering them unwalled
# is a later phase, so judging their "reachability" now would only measure
# an artifact of drawing them as walled rooms.
CIRCULATION_EXCLUDED_CATEGORIES = ("porch",)
PRIVATE_TRANSIT_CATEGORIES = ("bedroom", "bathroom", "powder")
DEAD_END_HALL_SOFT_M = 1.5


def _graph_root(rects: list[dict], graph: DoorGraph, floor_number: int, adj: list[set]) -> tuple[int | None, str]:
    """(index, how) of the BFS start node. Floor 1: the front-door room.
    Upper floors: the staircase room. Fallbacks (reported via `how`): the
    hallway, else the room with the most doors."""
    idx = {id(r): i for i, r in enumerate(rects)}
    if floor_number == 1 and graph.front_room is not None and id(graph.front_room) in idx:
        return idx[id(graph.front_room)], "front_door"
    if floor_number != 1:
        for i, r in enumerate(rects):
            if classify_room_category(r.get("name") or "") == "staircase":
                return i, "staircase"
    for i, r in enumerate(rects):
        if r.get("name") == "Hallway":
            return i, "hallway_fallback"
    if not rects:
        return None, "empty"
    return max(range(len(rects)), key=lambda i: (len(adj[i]), -i)), "most_connected_fallback"


def _project_on_part(e: dict, p: dict, horizontal: bool) -> tuple[float, float] | None:
    """Projection of a door/junction edge lying on part p's perimeter onto
    p's long axis (x when `horizontal`, else y); None if not on p."""
    x0, y0, x1, y1 = p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]
    if e["orientation"] == "horizontal":
        if not (abs(e["pos"] - y0) < EDGE_TOL or abs(e["pos"] - y1) < EDGE_TOL):
            return None
        s, t = max(e["start"], x0), min(e["end"], x1)
        if t <= s + EDGE_TOL:
            return None
        return (s, t) if horizontal else ((y0, y0) if abs(e["pos"] - y0) < EDGE_TOL else (y1, y1))
    if not (abs(e["pos"] - x0) < EDGE_TOL or abs(e["pos"] - x1) < EDGE_TOL):
        return None
    s, t = max(e["start"], y0), min(e["end"], y1)
    if t <= s + EDGE_TOL:
        return None
    return (s, t) if not horizontal else ((x0, x0) if abs(e["pos"] - x0) < EDGE_TOL else (x1, x1))


def _dead_end_hall_length(rects: list[dict], graph: DoorGraph) -> float:
    """Longest stretch at the end of a Hallway part (along its long axis)
    beyond the last door / hallway junction serving it, in plot units.
    0.0 when there is no hallway."""
    hall = next((r for r in rects if r.get("name") == "Hallway"), None)
    if hall is None:
        return 0.0
    edges = [e for a, b, e in graph.interior if a is hall or b is hall]
    if graph.front_room is hall and graph.front is not None:
        edges.append(graph.front)
    parts = room_parts(hall)
    worst = 0.0
    for i, p in enumerate(parts):
        horizontal = p["w"] >= p["h"]
        lo = p["x"] if horizontal else p["y"]
        hi = lo + (p["w"] if horizontal else p["h"])
        served: list[tuple[float, float]] = []
        boundary = list(edges)
        for j, q in enumerate(parts):
            if j != i:
                e = shared_edge(p, q)
                if e is not None:
                    boundary.append(e)
        for e in boundary:
            iv = _project_on_part(e, p, horizontal)
            if iv is not None:
                served.append(iv)
        if not served:
            worst = max(worst, hi - lo)
            continue
        first = min(s for s, _e in served)
        last = max(e for _s, e in served)
        worst = max(worst, first - lo, hi - last)
    return worst


def circulation_report(
    rects: list[dict], plot_dims: dict | None, facing: str | None, floor_number: int = 1
) -> dict:
    """Phase 4b reachability over the REAL door graph (build_door_graph, i.e.
    exactly the doors every renderer draws, plus the ground-floor front door).

    BFS starts at the ENTRY: floor 1 = the front-door room; upper floors =
    the staircase room (fallbacks: hallway, then the most-connected room -
    reported in `root_how`). The Hallway is ordinary circulation.

    - unreachable: rooms with no door path from the entry (HARD).
    - pass_through_private: rooms reachable ONLY through a bedroom/bathroom/
      powder room that is not their own suite partner. An ensuite bathroom
      (same suite tag) or a closet reached directly from a properly-reached
      bedroom is fine (HARD otherwise).
    - entry_is_garage / entry_missing (floor 1 only, HARD).
    - dead_end_hall_len: longest hallway stretch past its last door (plot
      units; SOFT above DEAD_END_HALL_SOFT_M).
    Porch-category rooms (Verandah/Terrace) are traversable but never counted
    as failures (see CIRCULATION_EXCLUDED_CATEGORIES)."""
    graph = build_door_graph(rects, plot_dims, facing, floor_number)
    n = len(rects)
    idx = {id(r): i for i, r in enumerate(rects)}
    adj: list[set] = [set() for _ in range(n)]
    for a, b, _e in graph.interior:
        ia, ib = idx[id(a)], idx[id(b)]
        adj[ia].add(ib)
        adj[ib].add(ia)
    cats = [classify_room_category(r.get("name") or "") for r in rects]
    names = [str(r.get("name") or "") for r in rects]
    root, how = _graph_root(rects, graph, floor_number, adj)

    entry_missing = floor_number == 1 and graph.front_room is None
    entry_is_garage = floor_number == 1 and graph.front_room is not None and cats[idx[id(graph.front_room)]] == "garage"

    counted = [i for i in range(n) if cats[i] not in CIRCULATION_EXCLUDED_CATEGORIES]
    if root is None:
        return {
            "root": None, "root_how": how, "unreachable": [], "pass_through_private": [],
            "entry_is_garage": entry_is_garage, "entry_missing": entry_missing, "dead_end_hall_len": 0.0,
        }

    full = {root}
    queue = [root]
    while queue:
        u = queue.pop()
        for v in adj[u]:
            if v not in full:
                full.add(v)
                queue.append(v)

    clean = {root}
    queue = [root]
    while queue:
        u = queue.pop()
        if u != root and cats[u] in PRIVATE_TRANSIT_CATEGORIES:
            continue  # reached, but never passed through
        for v in adj[u]:
            if v not in clean:
                clean.add(v)
                queue.append(v)
    ok = set(clean)
    for u in clean:
        if cats[u] != "bedroom":
            continue
        for v in adj[u]:
            if v in ok:
                continue
            same_suite = rects[u].get("suite") is not None and rects[u].get("suite") == rects[v].get("suite")
            if (cats[v] in ("bathroom", "powder") and same_suite) or cats[v] == "closet":
                ok.add(v)

    unreachable = [names[i] for i in counted if i not in full]
    pass_through = [names[i] for i in counted if i in full and i not in ok]
    return {
        "root": names[root],
        "root_how": how,
        "unreachable": unreachable,
        "pass_through_private": pass_through,
        "entry_is_garage": entry_is_garage,
        "entry_missing": entry_missing,
        "dead_end_hall_len": _dead_end_hall_length(rects, graph),
    }
