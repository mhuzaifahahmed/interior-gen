from app.pipeline.floor_layout import _zone_key, layout_floor


def _area(rect):
    return rect["w"] * rect["h"]


def test_layout_floor_returns_empty_for_no_rooms():
    assert layout_floor([], {"length": 40, "width": 60, "unit": "ft"}) == []


def test_layout_floor_single_room_fills_entire_plot():
    result = layout_floor([{"name": "Studio", "area": 1}], {"length": 40, "width": 60, "unit": "ft"})
    assert result == [{"name": "Studio", "x": 0.0, "y": 0.0, "w": 40.0, "h": 60.0}]


def test_layout_floor_rectangles_tile_the_plot_exactly():
    rooms = [
        {"name": "Living Room", "area": 3},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Bathroom", "area": 1},
    ]
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)

    # May include one extra "Hallway" rect (this room mix has 3 private-zone
    # rooms - Bedroom 1/2 + Bathroom - meeting MIN_ROOMS_FOR_CORRIDOR, see
    # floor_layout.py's real-circulation-corridor docstring) - the real
    # guarantee is still that every named room from the input is present and
    # everything together tiles the plot exactly, not a 1:1 rect count.
    assert len(rects) in (len(rooms), len(rooms) + 1)
    rect_names = {r["name"] for r in rects}
    assert {r["name"] for r in rooms} <= rect_names
    total_area = sum(_area(r) for r in rects)
    assert abs(total_area - 40 * 60) < 1e-6

    # No rectangle extends outside the plot bounds.
    for r in rects:
        assert r["x"] >= -1e-9
        assert r["y"] >= -1e-9
        assert r["x"] + r["w"] <= 40 + 1e-6
        assert r["y"] + r["h"] <= 60 + 1e-6


def test_layout_floor_areas_are_roughly_proportional_to_weights():
    # Two rooms, one weighted 3x the other - its rectangle should end up
    # bigger, though no longer EXACTLY 3x: both "Big Room"/"Small Room" are
    # unrecognized names (classify to the "default" category), so they get
    # an EQUAL guaranteed minimum area each - only the area remaining after
    # both minimums are reserved is split 3:1, which compresses the final
    # ratio below 3 (see floor_layout.py's MINIMUM-AREA GUARANTEE docstring
    # section - this is the intended, not accidental, behavior).
    rooms = [{"name": "Big Room", "area": 3}, {"name": "Small Room", "area": 1}]
    rects = layout_floor(rooms, {"length": 40, "width": 40, "unit": "ft"})

    big = next(r for r in rects if r["name"] == "Big Room")
    small = next(r for r in rects if r["name"] == "Small Room")
    ratio = _area(big) / _area(small)
    assert 1 < ratio < 3


def test_layout_floor_handles_many_rooms_without_error():
    rooms = [{"name": f"Room {i}", "area": i + 1} for i in range(12)]
    rects = layout_floor(rooms, {"length": 100, "width": 80, "unit": "ft"})
    assert len(rects) == 12
    total_area = sum(_area(r) for r in rects)
    assert abs(total_area - 100 * 80) < 1e-6


def test_layout_floor_defaults_missing_dimensions_to_one():
    result = layout_floor([{"name": "Room", "area": 1}], {})
    assert result == [{"name": "Room", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]


def _touch(a, b, tol=1e-6) -> bool:
    """True if rectangles a and b share any edge segment (adjacent)."""
    x_overlap = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    y_overlap = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    vertical_touch = abs((a["x"] + a["w"]) - b["x"]) < tol or abs((b["x"] + b["w"]) - a["x"]) < tol
    horizontal_touch = abs((a["y"] + a["h"]) - b["y"]) < tol or abs((b["y"] + b["h"]) - a["y"]) < tol
    return (vertical_touch and y_overlap > tol) or (horizontal_touch and x_overlap > tol)


def test_layout_floor_groups_public_rooms_together_not_scattered():
    # Real regression guard for the "living room ends up dead-center, mixed
    # in with a bedroom" complaint - a living room positioned between two
    # bedrooms in the input list (worst case for the old pure-weight-split
    # algorithm) must now end up in the SAME contiguous public-zone region
    # as the other public rooms, not sandwiched among private ones.
    rooms = [
        {"name": "Bedroom 1", "area": 2},
        {"name": "Living Room", "area": 3},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Kitchen", "area": 1},
        {"name": "Bathroom", "area": 1},
        {"name": "Garage", "area": 2},
    ]
    dimensions = {"length": 60, "width": 40, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)
    by_name = {r["name"]: r for r in rects}

    # Living Room must touch at least one other public-zone room (Kitchen or
    # Garage) - i.e. it landed in the public cluster, not isolated among
    # private rooms.
    living = by_name["Living Room"]
    assert _touch(living, by_name["Kitchen"]) or _touch(living, by_name["Garage"])


def test_layout_floor_public_rooms_form_one_contiguous_block():
    # All public-zone rooms should end up spatially connected to each other
    # (each touches at least one other public room, forming a single
    # cluster) - not scattered across the plot individually.
    rooms = [
        {"name": "Garage", "area": 2},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Living Room", "area": 2},
        {"name": "Bathroom 1", "area": 1},
        {"name": "Kitchen", "area": 1.5},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Dining Room", "area": 1.5},
    ]
    dimensions = {"length": 50, "width": 40, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)

    public_names = {"Garage", "Living Room", "Kitchen", "Dining Room"}
    public_rects = [r for r in rects if r["name"] in public_names]

    # Union-find style connectivity check: every public rect must be
    # reachable from every other public rect via touch() edges.
    reachable = {public_rects[0]["name"]}
    changed = True
    while changed:
        changed = False
        for r in public_rects:
            if r["name"] in reachable:
                continue
            if any(_touch(r, other) for other in public_rects if other["name"] in reachable):
                reachable.add(r["name"])
                changed = True

    assert reachable == public_names


def test_layout_floor_guarantees_minimum_area_even_when_gemini_gives_a_tiny_weight():
    # Real regression guard for the bug the min-area guarantee fixes: a
    # bedroom given a tiny relative weight next to a much bigger living room
    # must NOT shrink below its real-world usable minimum (see
    # room_specs.ROOM_SIZE_SPECS_M["bedroom"] - 2.7m x 2.7m, well over 60 sq
    # ft even before unit conversion slack).
    rooms = [{"name": "Living Room", "area": 20}, {"name": "Bedroom", "area": 0.1}]
    rects = layout_floor(rooms, {"length": 40, "width": 40, "unit": "ft"})
    bedroom = next(r for r in rects if r["name"] == "Bedroom")
    assert _area(bedroom) > 60


def test_layout_floor_garage_scales_with_car_count():
    # A 2-car garage must reserve more area than a 1-car garage, all else
    # equal - the minimum-area guarantee should scale with garage_cars.
    rooms = [{"name": "Garage", "area": 1}, {"name": "Living Room", "area": 1}]
    dimensions = {"length": 60, "width": 60, "unit": "ft"}

    one_car = layout_floor(rooms, dimensions, garage_cars=1)
    two_car = layout_floor(rooms, dimensions, garage_cars=2)

    garage_1 = next(r for r in one_car if r["name"] == "Garage")
    garage_2 = next(r for r in two_car if r["name"] == "Garage")
    assert _area(garage_2) > _area(garage_1)


def test_zone_key_ranks_circulation_between_public_and_private():
    # The direct, always-true guarantee: a staircase's zone rank sits
    # strictly between a public room's and a private room's - this is what
    # actually drives the stable sort; the geometric test below is a
    # concrete regression case for one specific room mix, not a general
    # proof (the recursive slice-and-dice split doesn't guarantee every
    # circulation room touches both neighboring zones for every possible
    # weight combination - see floor_layout.py's own docstring on this).
    assert _zone_key("Living Room") < _zone_key("Staircase") < _zone_key("Bedroom")
    assert _zone_key("Kitchen") < _zone_key("Stairs") < _zone_key("Bathroom")


def test_layout_floor_places_staircase_between_public_and_private_zones():
    # A concrete regression case (not a general proof - see the direct
    # _zone_key test above for the actual guarantee) confirming the
    # circulation zone (2026-08-29) sits between public and private in the
    # sort order for this specific room mix: a staircase given first in the
    # input list still ends up adjacent to both a public room and a private
    # room, not isolated on one side.
    rooms = [
        {"name": "Staircase", "area": 0.5},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Living Room", "area": 2},
        {"name": "Kitchen", "area": 1},
    ]
    dimensions = {"length": 50, "width": 40, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)
    by_name = {r["name"]: r for r in rects}

    staircase = by_name["Staircase"]
    touches_public = _touch(staircase, by_name["Living Room"]) or _touch(staircase, by_name["Kitchen"])
    touches_private = _touch(staircase, by_name["Bedroom 1"]) or _touch(staircase, by_name["Bedroom 2"])
    assert touches_public
    assert touches_private


def test_layout_floor_preserves_relative_order_within_a_zone():
    # A bedroom immediately followed by its own ensuite bathroom in the
    # input list should stay in that same relative order after the
    # public/private stable sort - only the coarse zone grouping is
    # enforced, not a full reordering within a zone.
    rooms = [
        {"name": "Living Room", "area": 3},
        {"name": "Master Bedroom", "area": 2},
        {"name": "Master Bathroom", "area": 1},
        {"name": "Kitchen", "area": 1},
    ]
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)

    # Both private-zone rooms (Master Bedroom, Master Bathroom) should still
    # be adjacent to each other, same as they were adjacent in the input.
    by_name = {r["name"]: r for r in rects}
    assert _touch(by_name["Master Bedroom"], by_name["Master Bathroom"])


def _private_room_mix():
    return [
        {"name": "Living Room", "area": 3},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Bathroom 1", "area": 1},
    ]


def test_layout_floor_creates_hallway_when_private_zone_has_enough_rooms():
    # 3 private-zone rooms (Bedroom 1, Bedroom 2, Bathroom 1) meets
    # MIN_ROOMS_FOR_CORRIDOR - a real "Hallway" rect should appear.
    rects = layout_floor(_private_room_mix(), {"length": 40, "width": 60, "unit": "ft"})
    assert any(r["name"] == "Hallway" for r in rects)


def test_layout_floor_hallway_touches_every_private_room():
    rects = layout_floor(_private_room_mix(), {"length": 40, "width": 60, "unit": "ft"})
    by_name = {r["name"]: r for r in rects}
    hallway = by_name["Hallway"]
    for name in ("Bedroom 1", "Bedroom 2", "Bathroom 1"):
        assert _touch(hallway, by_name[name]), f"{name} does not touch the hallway"


def test_layout_floor_hallway_borders_the_front_zone():
    # The hallway must be reachable FROM the public/circulation zone, not
    # just present somewhere inside the private zone - it should touch at
    # least one public-zone room.
    rects = layout_floor(_private_room_mix(), {"length": 40, "width": 60, "unit": "ft"})
    by_name = {r["name"]: r for r in rects}
    hallway = by_name["Hallway"]
    assert _touch(hallway, by_name["Living Room"]) or _touch(hallway, by_name["Kitchen"])


def test_layout_floor_no_hallway_with_fewer_than_threshold_private_rooms():
    # Only 2 private-zone rooms (Bedroom, Bathroom) - below
    # MIN_ROOMS_FOR_CORRIDOR - falls back to the original plain-adjacency
    # behavior, no Hallway rect at all.
    rooms = [
        {"name": "Living Room", "area": 3},
        {"name": "Bedroom", "area": 2},
        {"name": "Bathroom", "area": 1},
    ]
    rects = layout_floor(rooms, {"length": 40, "width": 60, "unit": "ft"})
    assert not any(r["name"] == "Hallway" for r in rects)


def test_layout_floor_falls_back_when_no_room_for_a_corridor():
    # Enough rooms to WANT a corridor, but the plot is too small/thin for the
    # private zone to have any depth left over after reserving the hallway
    # width - must fall back to plain adjacency rather than producing
    # degenerate/negative-sized room rects.
    rooms = [
        {"name": "Living Room", "area": 1},
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bathroom", "area": 1},
    ]
    rects = layout_floor(rooms, {"length": 10, "width": 8, "unit": "ft"})
    assert not any(r["name"] == "Hallway" for r in rects)
    for r in rects:
        assert r["w"] > 0 and r["h"] > 0


def test_layout_floor_preserves_order_within_a_corridor_row():
    # Same "master bedroom next to its own ensuite bathroom" guarantee as
    # test_layout_floor_preserves_relative_order_within_a_zone, but with
    # enough private rooms to actually trigger the corridor this time -
    # sequential packing along the hallway should keep them adjacent to each
    # other too, not just each individually adjacent to the hallway.
    rooms = [
        {"name": "Living Room", "area": 3},
        {"name": "Kitchen", "area": 1},
        {"name": "Master Bedroom", "area": 2},
        {"name": "Master Bathroom", "area": 1},
        {"name": "Bedroom 2", "area": 2},
    ]
    rects = layout_floor(rooms, {"length": 40, "width": 60, "unit": "ft"})
    by_name = {r["name"]: r for r in rects}
    assert "Hallway" in by_name
    assert _touch(by_name["Master Bedroom"], by_name["Master Bathroom"])


def test_layout_floor_rectangles_tile_the_plot_exactly_with_a_corridor():
    # The extra Hallway rect must still result in EXACT tiling, no gaps/
    # overlaps from floating-point drift in _pack_row()'s sequential packing.
    rects = layout_floor(_private_room_mix(), {"length": 45, "width": 55, "unit": "ft"})
    total_area = sum(_area(r) for r in rects)
    assert abs(total_area - 45 * 55) < 1e-6
