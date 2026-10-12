import pytest

from app.pipeline import planning_rules as pr
from app.pipeline import room_specs as rs


@pytest.mark.parametrize("sqft,name", [
    (0, "XS"), (1499.9, "XS"), (1500, "S"), (2999, "S"), (3000, "M"),
    (7999, "M"), (8000, "L"), (24999, "L"), (25000, "XL"), (1e6, "XL"),
])
def test_plot_band_boundaries(sqft, name):
    assert pr.plot_band(sqft, "ft").name == name


def test_plot_band_metric_conversion():
    assert pr.plot_band(1000 / 10.7639, "m").name == "XS"
    assert pr.plot_band(2000 / 10.7639, "m").name == "S"


def test_band_setbacks_units():
    band = pr.plot_band(5000, "ft")
    assert pr.band_setbacks(band, "ft") == {"front": 12.0, "rear": 8.0, "side": 5.0}
    assert pr.band_setbacks(band, "m")["front"] == pytest.approx(12 / rs.METERS_TO_FEET)


def test_bands_monotonic():
    for a, b in zip(pr.PLOT_BANDS, pr.PLOT_BANDS[1:]):
        assert a.target_cov > b.target_cov and a.max_cov > b.max_cov
        assert a.front_ft < b.front_ft
    assert all(b.target_cov < b.max_cov for b in pr.PLOT_BANDS)


def test_floor_adjust_and_circulation():
    assert pr.floor_count_coverage_adjust(1) == 1.0
    assert pr.floor_count_coverage_adjust(5) == 0.85
    assert pr.circulation_fraction(2) == 0.08
    assert pr.circulation_fraction(3) == 0.12
    assert pr.circulation_fraction(5) == 0.12
    assert pr.circulation_fraction(6) == 0.15


def test_bonus_list_sanity():
    assert pr.GARAGE_MIN_FRONT_SETBACK_M == 5.5
    for item in pr.BONUS_ROOM_PRIORITY:
        assert item["floors"] in ("ground", "upper", "any")
        assert item["max_count"] >= 1
        assert rs.classify_room_category(item["name"]) == item["category"]
    assert pr.UPPER_FLOOR_EXCESS_FALLBACK == "Terrace"


def test_layout_quality_reexports_daylight_constants():
    from app.pipeline import layout_quality as lq

    assert lq.DAYLIGHT_MIN_EDGE_M == pr.DAYLIGHT_MIN_EDGE_M
    assert lq.DAYLIGHT_HARD_CATEGORIES == pr.DAYLIGHT_HARD_CATEGORIES


_CATEGORIES = [c for c, _ in rs._CATEGORY_KEYWORDS] + ["default"]
_REP_NAMES = {
    "powder": "powder room", "dirty_kitchen": "dirty kitchen", "family": "tv room",
    "living": "living room", "default": "zzz unknown", "foyer": "entry",
}


@pytest.mark.parametrize("unit", ["ft", "m"])
@pytest.mark.parametrize("category", _CATEGORIES + ["master"])
def test_min_preferred_target_max_chain(category, unit):
    name = "Master Bedroom" if category == "master" else _REP_NAMES.get(category, category)
    if category != "master":
        assert rs.classify_room_category(name) == category
    mn = rs.min_area_for_room(name, unit)
    pf = rs.preferred_area_for_room(name, unit)
    tg = rs.target_area_for_room(name, unit)
    mx = rs.max_area_for_room(name, unit)
    eps = 1e-9
    assert mn <= pf + eps, (category, unit, mn, pf)
    assert pf <= tg + eps, (category, unit, pf, tg)
    assert tg <= mx + eps, (category, unit, tg, mx)


@pytest.mark.parametrize("category", [c for c in _CATEGORIES if c != "garage"])
def test_target_area_sqm_matches_room_specs(category):
    name = _REP_NAMES.get(category, category)
    assert rs.target_area_for_room(name, "m") == pytest.approx(pr.target_area_sqm(category), rel=1e-6)


def test_master_preferred_not_below_min():
    assert rs.preferred_area_for_room("Master Bedroom", "ft") >= rs.min_area_for_room("Master Bedroom", "ft")


def test_foyer_max_not_below_preferred():
    assert rs.max_area_for_room("Entry", "ft") >= rs.preferred_area_for_room("Entry", "ft")
