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
