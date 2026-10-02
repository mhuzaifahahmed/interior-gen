from app.pipeline.layout_quality import score_floor_layout


def _dims(length=40, width=60, unit="ft"):
    return {"length": length, "width": width, "unit": unit}


def test_clean_layout_has_no_hard_violations():
    rects = [
        {"name": "Living Room", "x": 0, "y": 0, "w": 20, "h": 30},
        {"name": "Bedroom 1", "x": 20, "y": 0, "w": 20, "h": 15},
        {"name": "Bathroom", "x": 20, "y": 15, "w": 20, "h": 15},
        {"name": "Kitchen", "x": 0, "y": 30, "w": 40, "h": 30},
    ]
    scored = score_floor_layout(rects, _dims())
    assert not scored.has_hard_violations


def test_sliver_room_is_flagged_as_soft_violation():
    # The real, reported 20.9x88.1ft bedroom sliver.
    rects = [
        {"name": "Bedroom 2", "x": 0, "y": 0, "w": 20.9, "h": 88.1},
        {"name": "Living Room", "x": 20.9, "y": 0, "w": 30, "h": 88.1},
    ]
    scored = score_floor_layout(rects, _dims(length=60, width=90))
    assert any(v.kind == "sliver" and not v.hard for v in scored.violations)


def test_undersized_room_is_a_hard_violation():
    rects = [{"name": "Bathroom", "x": 0, "y": 0, "w": 3, "h": 3}]  # way under real min
    scored = score_floor_layout(rects, _dims())
    assert scored.has_hard_violations
    assert any(v.kind == "small_area" for v in scored.hard_violations)


def test_shallow_foyer_is_a_hard_violation():
    # Direct regression for the real "1-foot entrance" bug.
    rects = [{"name": "Entry", "x": 0, "y": 0, "w": 10, "h": 1}]
    scored = score_floor_layout(rects, _dims())
    assert any(v.kind == "foyer_shallow" for v in scored.hard_violations)


def test_narrow_sliver_room_is_a_hard_violation_even_if_area_is_sufficient():
    # Large enough AREA, but far too narrow a width to be usable - a direct
    # regression for "area guaranteed, shape wasn't" bugs this scorer exists
    # to catch.
    rects = [{"name": "Bedroom 1", "x": 0, "y": 0, "w": 0.5, "h": 400}]
    scored = score_floor_layout(rects, _dims())
    assert any(v.kind == "narrow" for v in scored.hard_violations)


def test_door_wall_too_short_for_any_real_door_is_a_hard_violation():
    # A real (not corner-touch), but too-short-for-even-the-smallest-
    # practical-door shared wall - 1.0ft, below the ~1.77ft (0.9x0.6m) floor.
    rects = [
        {"name": "Bedroom 1", "x": 0, "y": 0, "w": 15, "h": 15},
        {"name": "Bathroom", "x": 15, "y": 0, "w": 15, "h": 1.0},
    ]
    scored = score_floor_layout(rects, _dims())
    assert any(v.kind == "door_too_narrow" for v in scored.hard_violations)


def test_hallway_and_staircase_are_exempt_from_the_sliver_penalty():
    rects = [{"name": "Hallway", "x": 0, "y": 0, "w": 3, "h": 30}, {"name": "Staircase", "x": 3, "y": 0, "w": 4, "h": 8}]
    scored = score_floor_layout(rects, _dims())
    assert not any(v.kind == "sliver" for v in scored.violations)


def test_degenerate_zero_size_room_is_a_hard_violation():
    rects = [{"name": "Bedroom 1", "x": 0, "y": 0, "w": 0, "h": 10}]
    scored = score_floor_layout(rects, _dims())
    assert scored.has_hard_violations


def test_score_is_lower_for_a_worse_layout():
    clean = [
        {"name": "Living Room", "x": 0, "y": 0, "w": 20, "h": 30},
        {"name": "Bedroom 1", "x": 20, "y": 0, "w": 20, "h": 30},
    ]
    bad = [
        {"name": "Living Room", "x": 0, "y": 0, "w": 39.9, "h": 59.9},
        {"name": "Bedroom 1", "x": 0, "y": 0, "w": 0.1, "h": 0.1},
    ]
    assert score_floor_layout(clean, _dims()).score > score_floor_layout(bad, _dims()).score
