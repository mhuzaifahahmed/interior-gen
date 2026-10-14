"""Unit tests for app/pipeline/door_graph.py (Phase 4a pure refactor).

The functions were moved verbatim out of blueprint_svg.py; these tests pin
their behaviour directly and check that build_door_graph() reproduces the door
set the PNG renderer actually draws.
"""
from app.pipeline import blueprint_svg
from app.pipeline import door_graph as dg

DIMS = {"length": 40.0, "width": 30.0, "unit": "ft"}


def R(name, x, y, w, h, **extra):
    return {"name": name, "x": x, "y": y, "w": w, "h": h, **extra}


def test_blueprint_svg_reexports_the_moved_private_names():
    assert blueprint_svg._shared_edge is dg.shared_edge
    assert blueprint_svg._shared_edge_between_rooms is dg.shared_edge_between_rooms
    assert blueprint_svg._compute_hallway_adjacent is dg.compute_hallway_adjacent
    assert blueprint_svg._should_suppress_direct_door is dg.should_suppress_direct_door
    assert blueprint_svg._should_suppress_garage_direct_door is dg.should_suppress_garage_direct_door
    assert blueprint_svg._front_door_opening is dg.front_door_opening
    assert blueprint_svg._room_door_walls is dg.room_door_walls


def test_shared_edge_vertical_horizontal_and_none():
    a, b = R("A", 0, 0, 10, 10), R("B", 10, 2, 10, 5)
    assert dg.shared_edge(a, b) == {"orientation": "vertical", "pos": 10, "start": 2, "end": 7}
    c = R("C", 0, 10, 4, 5)
    assert dg.shared_edge(a, c) == {"orientation": "horizontal", "pos": 10, "start": 0, "end": 4}
    assert dg.shared_edge(a, R("D", 10, 10, 5, 5)) is None  # corner touch only
    assert dg.shared_edge(a, R("E", 30, 30, 5, 5)) is None


def test_shared_edge_between_rooms_is_parts_aware_and_picks_longest():
    l_room = R("Living Room", 0, 0, 10, 10, parts=[
        {"x": 0, "y": 0, "w": 10, "h": 4}, {"x": 0, "y": 4, "w": 4, "h": 6},
    ])
    other = R("Kitchen", 10, 0, 5, 10)
    edge = dg.shared_edge_between_rooms(l_room, other)
    # only the 10x4 top part touches x=10 -> y 0..4
    assert edge == {"orientation": "vertical", "pos": 10, "start": 0, "end": 4}
    # bbox-only edge would span the whole 0..10
    assert dg.shared_edge(l_room, other)["end"] == 10


def test_hallway_suppresses_bedroom_to_bedroom_but_not_hallway_doors():
    hall = R("Hallway", 0, 0, 20, 3)
    b1, b2 = R("Bedroom 1", 0, 3, 10, 8), R("Bedroom 2", 10, 3, 10, 8)
    rects = [hall, b1, b2]
    adj = dg.compute_hallway_adjacent(rects)
    assert id(b1) in adj and id(b2) in adj and id(hall) not in adj
    assert dg.should_suppress_direct_door(b1, b2, adj) is True
    assert dg.should_suppress_direct_door(hall, b1, adj) is False


def test_far_band_room_keeps_its_door():
    hall = R("Hallway", 0, 0, 10, 3)
    near, far = R("Bedroom 1", 0, 3, 10, 5), R("Bedroom 2", 0, 8, 10, 5)
    adj = dg.compute_hallway_adjacent([hall, near, far])
    assert id(far) not in adj
    assert dg.should_suppress_direct_door(near, far, adj) is False


def test_ensuite_rule():
    bed = R("Master Bedroom", 0, 0, 10, 10, suite=1)
    bath = R("Master Bathroom", 10, 0, 5, 10, suite=1)
    other_bath = R("Bathroom 2", 10, 10, 5, 5, suite=2)
    hall = R("Hallway", 0, 10, 10, 3)
    adj = dg.compute_hallway_adjacent([bed, bath, other_bath, hall])
    assert dg.should_suppress_direct_door(bed, bath, adj | {id(bath)}) is False  # same suite keeps
    assert dg.should_suppress_direct_door(bed, other_bath, frozenset({id(bed), id(other_bath)})) is True
    assert dg.should_suppress_direct_door(hall, bath) is True  # ensuite never opens to hallway
    assert dg.should_suppress_direct_door(hall, R("Bathroom 3", 0, 13, 4, 4)) is False  # common bath does


def test_garage_suppression():
    g = R("Garage", 0, 0, 9, 17)
    assert dg.should_suppress_garage_direct_door(g, R("Living Room", 9, 0, 10, 10)) is True
    assert dg.should_suppress_garage_direct_door(g, R("Kitchen", 9, 0, 10, 10)) is True
    assert dg.should_suppress_garage_direct_door(g, R("Entry", 9, 0, 5, 10)) is False
    assert dg.should_suppress_garage_direct_door(R("Bedroom 1", 0, 0, 5, 5), R("Living Room", 5, 0, 5, 5)) is False


def test_front_door_opening_per_facing_and_none():
    rects = [R("Entry", 0, 0, 10, 10), R("Living Room", 10, 0, 30, 30), R("Kitchen", 0, 10, 10, 20)]
    fp = (0.0, 0.0, 40.0, 30.0)
    assert dg.front_door_opening(rects, None, fp) is None
    assert dg.front_door_opening(rects, "up", fp) is None
    n = dg.front_door_opening(rects, "north", fp)
    assert n == {"orientation": "horizontal", "pos": 0.0, "start": 0, "end": 10}  # foyer preferred
    s = dg.front_door_opening(rects, "South", fp)
    assert s["orientation"] == "horizontal" and s["pos"] == 30.0
    w = dg.front_door_opening(rects, "west", fp)
    assert w["orientation"] == "vertical" and w["pos"] == 0.0
    e = dg.front_door_opening(rects, "east", fp)
    assert e["orientation"] == "vertical" and e["pos"] == 40.0 and (e["start"], e["end"]) == (0, 30)


def test_room_door_walls():
    room = R("Bedroom 1", 0, 0, 10, 10)
    edges = [
        {"orientation": "vertical", "pos": 10, "start": 2, "end": 5},
        {"orientation": "horizontal", "pos": 0, "start": 1, "end": 3},
        {"orientation": "horizontal", "pos": 0, "start": 20, "end": 25},  # unrelated span
    ]
    assert dg.room_door_walls(room, edges) == {"right", "top"}


def _layouts():
    hall_plan = [
        R("Hallway", 0, 12, 40, 3),
        R("Bedroom 1", 0, 15, 14, 15, suite=1), R("Master Bathroom", 14, 15, 6, 15, suite=1),
        R("Bedroom 2", 20, 15, 20, 15),
        R("Entry", 0, 0, 8, 12), R("Living Room", 8, 0, 20, 12), R("Garage", 28, 0, 12, 12),
    ]
    entry_plan = [
        R("Garage", 0, 0, 9, 17), R("Entry", 9, 0, 6, 17), R("Living Room", 15, 0, 25, 17),
        R("Kitchen", 0, 17, 20, 13), R("Dining Room", 20, 17, 20, 13),
    ]
    no_hall = [R("Bedroom 1", 0, 0, 20, 30), R("Bedroom 2", 20, 0, 20, 30)]
    return [(hall_plan, "south"), (entry_plan, "north"), (no_hall, "east")]


def test_build_door_graph_matches_the_doors_the_png_renderer_draws(monkeypatch):
    for rects, facing in _layouts():
        drawn = []
        monkeypatch.setattr(blueprint_svg, "_draw_door", lambda draw, edge, *a, **k: drawn.append(edge))
        fronts = []
        monkeypatch.setattr(blueprint_svg, "_draw_front_door", lambda draw, edge, *a, **k: fronts.append(edge))
        blueprint_svg.render_floor_blueprint(1, rects, DIMS, 1, facing)
        graph = dg.build_door_graph(rects, DIMS, facing, 1)
        assert drawn == [e for _a, _b, e in graph.interior]
        assert fronts == ([graph.front] if graph.front else [])
        assert graph.edges == drawn + fronts


def test_build_door_graph_front_door_is_ground_floor_only():
    rects, facing = _layouts()[1]
    assert dg.build_door_graph(rects, DIMS, facing, 1).front is not None
    assert dg.build_door_graph(rects, DIMS, facing, 2).front is None
    assert dg.build_door_graph(rects, DIMS, facing, 1, include_front_door=False).front is None


def test_build_door_graph_flags_preserve_per_caller_behaviour():
    rects, _ = _layouts()[1]  # Garage next to Living/Entry with a foyer present
    with_garage = dg.build_door_graph(rects, DIMS, "north", 1)
    without = dg.build_door_graph(rects, DIMS, "north", 1, suppress_garage_doors=False)

    def pair(g, n1, n2):
        return [p for p in g.pairs if {p[0]["name"], p[1]["name"]} == {n1, n2}]

    # Garage|Entry shared wall: never suppressed. Garage|Kitchen: suppressed unless flag off.
    assert pair(with_garage, "Garage", "Entry")[0][3] is False
    assert pair(with_garage, "Garage", "Kitchen")[0][3] is True
    assert pair(without, "Garage", "Kitchen")[0][3] is False
    # same pair list either way (DXF still draws the wall)
    assert len(with_garage.pairs) == len(without.pairs)


# ---- Step 3: front-door priority list, orphaned-ensuite soft violation ----

def test_front_door_tier_priority_and_exclusions():
    assert dg.front_door_tier("Entry") == 0 and dg.front_door_tier("Foyer") == 0
    assert dg.front_door_tier("Living Room") == 1 and dg.front_door_tier("Drawing Room") == 1
    for n in ("Lounge", "Family Lounge", "Family Room", "Sitting Area", "Hallway", "Hall"):
        assert dg.front_door_tier(n) == 2, n
    assert dg.front_door_tier("Dining Room") == 3
    for n in ("Kitchen", "Laundry", "Utility", "Store", "Storage", "Garage", "Staircase", "Study",
              "Bedroom 1", "Master Bathroom", "Powder Room", "Verandah"):
        assert dg.front_door_tier(n) is None, n


def test_front_door_follows_the_priority_not_the_span():
    fp = (0.0, 0.0, 40.0, 30.0)
    # Kitchen has the longest span on the north edge but is never the entrance.
    rects = [R("Kitchen", 0, 0, 25, 10), R("Dining Room", 25, 0, 5, 10), R("Lounge", 30, 0, 10, 10),
             R("Bedroom 1", 0, 10, 40, 20)]
    room, _edge = dg.front_door_room(rects, "north", fp)
    assert room["name"] == "Lounge"
    rects[2]["name"] = "Living Room"
    assert dg.front_door_room(rects, "north", fp)[0]["name"] == "Living Room"


def test_front_door_missing_when_only_excluded_rooms_touch_the_edge():
    fp = (0.0, 0.0, 40.0, 30.0)
    rects = [R("Kitchen", 0, 0, 20, 10), R("Bedroom 1", 20, 0, 20, 10), R("Living Room", 0, 10, 40, 20)]
    assert dg.front_door_room(rects, "north", fp) == (None, None)
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert rep["entry_missing"] and rep["front_door_room"] is None


def test_orphaned_ensuite_is_a_soft_planner_violation():
    from app.pipeline.layout_metrics import measure_floor
    from app.pipeline.layout_quality import ORPHANED_ENSUITE_PENALTY, score_floor_layout, violation_penalty

    rects = [
        R("Hallway", 0, 12, 40, 3),
        R("Master Bedroom", 0, 15, 20, 15, suite=0), R("Bedroom 2", 20, 15, 20, 15),
        R("Master Bathroom", 0, 0, 8, 12, suite=0), R("Living Room", 8, 0, 32, 12),
    ]
    assert measure_floor(rects, DIMS, facing="north")["orphaned_ensuites"] == ["Master Bathroom"]
    off = score_floor_layout(rects, DIMS)
    assert not any(v.kind == "orphaned_ensuite" for v in off.violations)  # flag-off scoring unchanged
    on = score_floor_layout(rects, DIMS, planner_checks=True, facing="north")
    hits = [v for v in on.violations if v.kind == "orphaned_ensuite"]
    assert len(hits) == 1 and not hits[0].hard and violation_penalty(hits[0]) == ORPHANED_ENSUITE_PENALTY
    assert ORPHANED_ENSUITE_PENALTY < 60.0  # below every gated / capped hard penalty


def test_front_door_policy_is_data_in_planning_rules(monkeypatch):
    """The tier order and the exclusion list live in planning_rules; changing
    the data changes the behaviour (door_graph hard-codes neither)."""
    from app.pipeline import planning_rules as pr

    rects = [
        {"name": "Living Room", "x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0},
        {"name": "Dining Room", "x": 10.0, "y": 0.0, "w": 14.0, "h": 10.0},
    ]
    fp = (0.0, 0.0, 24.0, 10.0)
    room, _edge = dg.front_door_room(rects, "north", fp)
    assert room["name"] == "Living Room"  # default: living outranks dining

    # Reverse the tiers: dining first -> the entrance moves to the Dining Room.
    monkeypatch.setattr(pr, "FRONT_DOOR_PRIORITY_TIERS", tuple(reversed(pr.FRONT_DOOR_PRIORITY_TIERS)))
    assert dg.front_door_tier("Dining Room") == 0
    room, _edge = dg.front_door_room(rects, "north", fp)
    assert room["name"] == "Dining Room"
    monkeypatch.undo()

    # Exclude a category: living is no longer eligible -> dining takes the door.
    monkeypatch.setattr(pr, "FRONT_DOOR_EXCLUDED_CATEGORIES", pr.FRONT_DOOR_EXCLUDED_CATEGORIES + ("living",))
    assert dg.front_door_tier("Living Room") is None
    room, _edge = dg.front_door_room(rects, "north", fp)
    assert room["name"] == "Dining Room"
    monkeypatch.undo()

    # Excluding nothing makes a Kitchen eligible only if a tier accepts its
    # category - none does, so it stays out (exclusion list is belt-and-braces).
    monkeypatch.setattr(pr, "FRONT_DOOR_EXCLUDED_CATEGORIES", ())
    assert dg.front_door_tier("Kitchen") is None
    monkeypatch.setattr(pr, "FRONT_DOOR_PRIORITY_TIERS",
                        pr.FRONT_DOOR_PRIORITY_TIERS + (pr.FrontDoorTier("kitchen", ("kitchen",)),))
    assert dg.front_door_tier("Kitchen") == len(pr.FRONT_DOOR_PRIORITY_TIERS) - 1
