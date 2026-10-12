from app.pipeline.layout_quality import (
    daylight_failures,
    footprint_edge_length,
    score_floor_layout,
)

DIMS = {"length": 20.0, "width": 20.0, "unit": "ft"}
FP = {"x0": 0.0, "y0": 0.0, "x1": 20.0, "y1": 20.0}


def test_footprint_edge_length_on_and_off_boundary():
    assert footprint_edge_length({"x": 0, "y": 5, "w": 5, "h": 8}, FP) == 8
    assert footprint_edge_length({"x": 5, "y": 5, "w": 5, "h": 5}, FP) == 0


def test_daylight_failures_hard_and_soft():
    rects = [
        {"name": "Bedroom 1", "x": 5, "y": 5, "w": 10, "h": 10},
        {"name": "Dining Room", "x": 0, "y": 0, "w": 0.5, "h": 0.5},
        {"name": "Living Room", "x": 0, "y": 15, "w": 10, "h": 5},
    ]
    hard, soft = daylight_failures(rects, FP, "ft")
    assert hard == ["Bedroom 1"]
    assert soft == ["Dining Room"]


def test_score_daylight_is_opt_in():
    rects = [{"name": "Bedroom 1", "x": 5, "y": 5, "w": 10, "h": 10}]
    off = score_floor_layout(rects, DIMS)
    assert "no_daylight" not in {v.kind for v in off.violations}
    assert "no_egress" in {v.kind for v in off.violations}
    on = score_floor_layout(rects, DIMS, daylight_footprint=FP)
    kinds = {(v.kind, v.hard) for v in on.violations}
    assert ("no_daylight", True) in kinds
    assert "no_egress" not in {k for k, _ in kinds}
