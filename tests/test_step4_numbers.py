"""Step 4 ("the numbers") unit tests: narrow-bedroom selection term, master
bedroom cap / tier scaling, small bonus-room minimums, dual-threshold
scorecard, upper-floor surplus sinks. Pure / synthetic inputs (fast tier)."""
from app.pipeline import floor_layout_mask as M
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.layout_quality import LayoutScore, Violation, narrow_bedroom_names
from app.pipeline.mask_scorecard import format_narrow_bedrooms

PLOT = {"length": 40.0, "width": 60.0, "unit": "ft"}


# ---- Item 1: narrow bedrooms ----

def test_narrow_bedroom_names_counts_hard_narrow_and_hard_aspect_bedrooms_once():
    vs = [
        Violation("Bedroom 2", "narrow", True, ""),
        Violation("Bedroom 2", "bedroom_aspect", True, ""),  # same room: counted once
        Violation("Master Bedroom", "bedroom_aspect", True, ""),
        Violation("Bedroom 3", "bedroom_aspect", False, ""),  # soft aspect (> 2.0): not counted
        Violation("Bathroom 1", "narrow", True, ""),  # not a bedroom
        Violation("Bedroom 4", "sliver_part", False, ""),
    ]
    assert narrow_bedroom_names(vs) == ["Bedroom 2", "Master Bedroom"]


def _r(name, x, y, w, h):
    return {"name": name, "x": x, "y": y, "w": w, "h": h}


def test_measure_floor_narrow_bedrooms_is_aspect_fail_or_hard_narrow():
    rects = [
        _r("Living Room", 0, 0, 40, 20),
        _r("Bedroom 1", 0, 20, 6, 20),       # 6 ft < 8.86 ft min width: hard narrow
        _r("Bedroom 2", 6, 20, 9.5, 30),     # wide enough, but 30/9.5 = 3.2 > 2.5
        _r("Bedroom 3", 15.5, 20, 12, 14),   # fine
        _r("Bathroom 1", 27.5, 20, 12.5, 14),
    ]
    m = measure_floor(rects, PLOT)
    assert m["narrow_bedrooms"] == ["Bedroom 1", "Bedroom 2"]
    assert "Bedroom 2" in m["bedroom_aspect_fail"]


def _cand(names, extra=()):
    vs = [Violation(n, "narrow", True, "") for n in names] + list(extra)
    return (([{"name": "Bedroom 1", "x": 0, "y": 0, "w": 10, "h": 10}], LayoutScore(score=-1000.0 * len(vs), violations=vs), {}), "rectified")


def test_final_pick_prefers_fewer_narrow_bedrooms_inside_the_safe_set(monkeypatch):
    # pick0 (best plain score) has 1 narrow bedroom + 1 narrow bathroom; the
    # second has the same ratchet vector (2 "other" hard) but only 0 narrow
    # bedrooms - it is safe and wins once the term is on.
    a = _cand(["Bedroom 2", "Bathroom 1"])
    b = _cand(["Bathroom 1", "Study"])
    monkeypatch.setattr(M, "NARROW_BEDROOM_FINAL_PICK", False)
    assert M._pick_circulation_safe([a, b], {}, 2) is a
    monkeypatch.setattr(M, "NARROW_BEDROOM_FINAL_PICK", True)
    assert M._pick_circulation_safe([a, b], {}, 2) is b


def test_final_pick_never_takes_an_unsafe_candidate_for_fewer_narrow_bedrooms(monkeypatch):
    # b has no narrow bedroom but one MORE hard violation than pick0: unsafe.
    a = _cand(["Bedroom 2"])
    b = _cand(["Bathroom 1", "Study"])
    monkeypatch.setattr(M, "NARROW_BEDROOM_FINAL_PICK", True)
    assert M._pick_circulation_safe([a, b], {}, 2) is a


def test_scorecard_narrow_bedrooms_line_lists_count_and_floors():
    res = {"a/floor1": {"metrics": {"narrow_bedrooms": ["Bedroom 2"]}},
           "b/floor1": {"metrics": {"narrow_bedrooms": []}},
           "c/floor2": {"metrics": {"narrow_bedrooms": ["Bedroom 3", "Master Bedroom"]}}}
    line = format_narrow_bedrooms(res)
    assert "3 rooms on 2/3 floors" in line
    assert "a/floor1=Bedroom 2" in line and "c/floor2=Bedroom 3,Master Bedroom" in line
