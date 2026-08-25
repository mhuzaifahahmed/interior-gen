from app.pipeline.floor_layout import layout_floor


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

    assert len(rects) == len(rooms)
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
    # roughly 3x the area (allowing for the two-way recursive split, which is
    # exact for exactly 2 rooms).
    rooms = [{"name": "Big Room", "area": 3}, {"name": "Small Room", "area": 1}]
    rects = layout_floor(rooms, {"length": 40, "width": 40, "unit": "ft"})

    big = next(r for r in rects if r["name"] == "Big Room")
    small = next(r for r in rects if r["name"] == "Small Room")
    assert abs(_area(big) / _area(small) - 3) < 1e-6


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
