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


# ---- Item 4: dual-threshold scorecard ----
from app.pipeline.mask_scorecard import format_dual_scorecard, is_master_ensuite, score_floor  # noqa: E402

_BIG = {"length": 300.0, "width": 300.0, "unit": "ft"}


def test_is_master_ensuite_by_name_or_suite_tag():
    master_bed = {"name": "Master Bedroom", "suite": 1}
    tagged = {"name": "Bathroom 2", "suite": 1}
    other = {"name": "Bathroom 3", "suite": 2}
    rects = [master_bed, tagged, other, {"name": "Bedroom 2", "suite": 2}]
    assert is_master_ensuite({"name": "Master Bathroom"}, rects)
    assert is_master_ensuite({"name": "Ensuite Bath"}, rects)
    assert is_master_ensuite(tagged, rects)
    assert not is_master_ensuite(other, rects)
    assert not is_master_ensuite(master_bed, rects)


def test_raised_bath_cap_applies_only_to_the_master_ensuite_in_tier_mode():
    # 95 sq ft bathrooms on an XL (estate) plot: estate cap 100, base cap 80.
    rects = [
        {"name": "Master Bathroom", "x": 100, "y": 100, "w": 9.5, "h": 10},
        {"name": "Bathroom 2", "x": 120, "y": 100, "w": 9.5, "h": 10},
    ]
    floors = [[{"name": "Master Bathroom"}, {"name": "Bathroom 2"}]]
    tier = score_floor(rects, _BIG, "south", floors, 1, planner=True)
    assert tier["sizes"]["big_baths"] == ["Bathroom 2=95"]
    original = score_floor(rects, _BIG, "south", floors, 1, planner=True, thresholds="original")
    assert sorted(original["sizes"]["big_baths"]) == ["Bathroom 2=95", "Master Bathroom=95"]


def test_original_thresholds_drop_tier_maxima_but_keep_remainder_rooms():
    rects = [{"name": "Living Room", "x": 0, "y": 0, "w": 25, "h": 25},
             {"name": "Terrace", "x": 25, "y": 0, "w": 30, "h": 30}]
    floors = [[{"name": "Living Room"}]]
    over = {"Living Room": 700.0, "Terrace": 5000.0}
    tier = score_floor(rects, _BIG, "south", floors, 1, max_overrides=over, remainder_names=["Terrace"], planner=True)
    orig = score_floor(rects, _BIG, "south", floors, 1, max_overrides=over, remainder_names=["Terrace"],
                       planner=True, thresholds="original")
    assert "Living Room" not in tier["sizes"]["oversized"]
    assert "Living Room" in orig["sizes"]["oversized"]  # 625 > 1.1 x standard max
    assert "Terrace" not in orig["sizes"]["oversized"]


def test_dual_scorecard_prints_both_columns_and_totals():
    rects = [{"name": "Master Bathroom", "x": 100, "y": 100, "w": 9.5, "h": 10}]
    floors = [[{"name": "Master Bathroom"}]]
    kw = {"max_overrides": {"Master Bathroom": 120.0}, "remainder_names": [], "planner": True}
    a = {"x/floor1": score_floor(rects, _BIG, "south", floors, 1, thresholds="original", **kw)}
    b = {"x/floor1": score_floor(rects, _BIG, "south", floors, 1, **kw)}
    text = format_dual_scorecard(a, b, "planner_on")
    assert "(A) original" in text and "(B) tier-aware" in text
    assert "legacy 6 checks" in text and "/6" in text and "/9" in text and "fully clean floors" in text
    sizes = next(line for line in text.splitlines() if line.startswith("3. No oversized rooms"))
    assert sizes.split()[-2:] == ["0/1", "1/1"]
