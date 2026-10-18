"""Phase 7: layout_engine.layout_building's per-floor layout_path record and
fallback chain. The mask engine itself is stubbed (the chain is dispatcher
logic); the gate matrix covers the real engine."""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.pipeline import layout_engine

pytestmark = pytest.mark.fast

PLOT = {"length": 60, "width": 80, "unit": "ft"}


def _plan(n=1):
    return SimpleNamespace(floors=[SimpleNamespace(chain_path="mask") for _ in range(n)])


def _rooms():
    return [[{"name": "Living Room"}]]


@pytest.fixture
def planner_on(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)


def test_flag_off_is_a_pure_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", False)
    sentinel = (_plan(), [([], [], [])])
    monkeypatch.setattr(layout_engine.floor_layout_mask, "layout_building", lambda *a, **k: sentinel)
    monkeypatch.setattr(layout_engine, "floor_hard_total", lambda *a, **k: pytest.fail("must not score"))
    out = layout_engine.layout_building(_rooms(), PLOT, "north", None, 1)
    assert out[0] is sentinel[0] and out[1] is sentinel[1]
    assert out[0].floors[0].chain_path == "mask"


def test_clean_floor_is_mask_and_hard_floor_is_best_effort(planner_on, monkeypatch):
    plan = _plan(2)
    results = [([], [{"n": 1}], []), ([], [{"n": 2}], [])]
    monkeypatch.setattr(layout_engine.floor_layout_mask, "layout_building", lambda *a, **k: (plan, results))
    monkeypatch.setattr(layout_engine, "floor_hard_total",
                        lambda rects, dims, fn, total, cars: 0 if fn == 1 else 3)
    monkeypatch.setattr(layout_engine, "ALT_SHAPE_MAX_TRIES", 0)
    monkeypatch.setattr(layout_engine, "RECT_FALLBACK_ENABLED", False)
    layout_engine.layout_building([[{"name": "a"}], [{"name": "b"}]], PLOT, "north", None, 2)
    assert [f.chain_path for f in plan.floors] == ["mask", "mask_best_effort"]


# ---- 7.2 / 7.3: the fallback chain, one test per branch ----


def _stub_plan(n_floors=1, length=40.0, width=30.0, facing_scale=1.0):
    floors = [SimpleNamespace(chain_path="mask") for _ in range(n_floors)]
    return SimpleNamespace(footprint_length=length, footprint_width=width, footprint_scale=facing_scale, floors=floors)


def _results(marker, n=1):
    """Each floor's rects carry a fake `hard` count the stubbed scorer reads."""
    return [([{"name": "used"}], [{"name": "r", "hard": marker, "src": "mask"}], []) for _ in range(n)]


@pytest.fixture
def chain(planner_on, monkeypatch):
    """Stub every engine call; hard totals come from rects[0]["hard"]."""
    monkeypatch.setattr(layout_engine, "floor_hard_total", lambda rects, d, fn, total, cars: rects[0]["hard"])
    monkeypatch.setattr(layout_engine, "_chain_vector", lambda *a, **k: (0,))
    state = SimpleNamespace(base=(_stub_plan(), _results(3)), alt={}, rect_hard=None, rect_calls=0, alt_calls=[])
    monkeypatch.setattr(layout_engine.floor_layout_mask, "layout_building", lambda *a, **k: state.base)

    def fake_alt(floors_rooms, dims, facing, cars, total, front, replans, scale, aspect):
        state.alt_calls.append(aspect)
        idx = len(state.alt_calls)
        return state.alt.get(idx, (_stub_plan(), _results(3)))

    monkeypatch.setattr(layout_engine.floor_layout_mask, "_layout_building_at", fake_alt)

    def fake_rect(used, dims, cars, facing):
        state.rect_calls += 1
        return [{"name": "r", "hard": state.rect_hard, "src": "rect"}], []

    monkeypatch.setattr(layout_engine, "_rect_floor", fake_rect)
    monkeypatch.setattr(layout_engine, "EXTRA_TIME_FACTOR", None)
    state.rect_matches = True
    monkeypatch.setattr(layout_engine, "_rect_matches_plan", lambda plan, rects: state.rect_matches)
    return state


def _run():
    return layout_engine.layout_building([[{"name": "Living Room"}]], PLOT, "north", None, 1)


def test_alt_shape_wins_when_strictly_fewer_hard(chain):
    chain.alt[2] = (_stub_plan(length=30.0, width=40.0), _results(1))  # second try is better
    chain.rect_hard = 5  # rect must not even be needed
    plan, results = _run()
    assert plan.floors[0].chain_path == "mask_alt_shape:2"
    assert results[0][1][0]["src"] == "mask"
    assert chain.rect_calls == 1  # the floor still has 1 hard, so rect is tried, but loses (5 > 1)


def test_alt_shape_not_taken_when_not_strictly_better(chain):
    chain.rect_hard = 9
    plan, _results_out = _run()
    assert len(chain.alt_calls) <= layout_engine.ALT_SHAPE_MAX_TRIES
    assert plan.floors[0].chain_path == "mask_best_effort"


def test_alt_shape_tries_are_bounded(chain, monkeypatch):
    monkeypatch.setattr(layout_engine, "ALT_SHAPE_MAX_TRIES", 1)
    chain.rect_hard = 9
    _run()
    assert len(chain.alt_calls) == 1


def test_rect_wins_only_when_strictly_fewer(chain):
    chain.rect_hard = 1  # fewer than the mask's 3
    plan, results = _run()
    assert plan.floors[0].chain_path == "rect_fallback"
    assert results[0][1][0]["src"] == "rect"


def test_rect_equal_hard_is_not_used(chain):
    chain.rect_hard = 3
    plan, results = _run()
    assert plan.floors[0].chain_path == "mask_best_effort"
    assert results[0][1][0]["src"] == "mask"


def test_clean_building_runs_no_retries(chain):
    chain.base = (_stub_plan(), _results(0))
    plan, _r = _run()
    assert plan.floors[0].chain_path == "mask"
    assert chain.alt_calls == [] and chain.rect_calls == 0


def test_flag_off_runs_no_chain(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", False)
    base = (_stub_plan(), _results(3))
    monkeypatch.setattr(layout_engine.floor_layout_mask, "layout_building", lambda *a, **k: base)
    monkeypatch.setattr(layout_engine, "_rect_floor", lambda *a, **k: pytest.fail("rect must not run"))
    monkeypatch.setattr(layout_engine.floor_layout_mask, "_layout_building_at",
                        lambda *a, **k: pytest.fail("alt shape must not run"))
    out = _run()
    assert out[0] is base[0] and out[1] is base[1]


def test_time_budget_stops_alt_shape_tries(chain, monkeypatch):
    monkeypatch.setattr(layout_engine, "EXTRA_TIME_FACTOR", 0.0)
    chain.rect_hard = 9
    _run()
    assert chain.alt_calls == []


def test_alt_shape_aspects_are_distinct_and_bounded():
    aspects = layout_engine.alt_shape_aspects(1.33)
    assert 1 <= len(aspects) <= 3
    assert all(0.5 <= a <= 2.0 for a in aspects)
    assert all(abs(a - 1.33) / 1.33 > 0.08 for a in aspects)
    assert layout_engine.alt_shape_aspects(1.0)[0] != 1.0  # square: only elongated shapes are new


def test_rect_fallback_plan_routes_the_floor_to_rect(planner_on, monkeypatch):
    monkeypatch.setattr(settings, "house_layout_engine", "mask")
    called = []
    monkeypatch.setattr(layout_engine.floor_layout, "layout_floor_with_warnings",
                        lambda *a, **k: called.append("rect") or ([], []))
    monkeypatch.setattr(layout_engine.floor_layout_mask, "layout_floor_with_warnings",
                        lambda *a, **k: called.append("mask") or ([], []))
    layout_engine.layout_floor_with_warnings([{"name": "x"}], PLOT, None, "north", SimpleNamespace(chain_path="rect_fallback"))
    layout_engine.layout_floor_with_warnings([{"name": "x"}], PLOT, None, "north", SimpleNamespace(chain_path="mask"))
    assert called == ["rect", "mask"]


def test_planner_shape_aspect_changes_the_footprint_and_default_is_unchanged():
    from app.pipeline.house_planner import plan_building

    rooms = [[{"name": "Living Room"}, {"name": "Kitchen"}, {"name": "Bedroom 1"}, {"name": "Bathroom 1"}]]
    plot = {"length": 80, "width": 80, "unit": "ft"}
    base = plan_building(rooms, plot, "south", None, 1)
    same = plan_building(rooms, plot, "south", None, 1, shape_aspect=None)
    tall = plan_building(rooms, plot, "south", None, 1, shape_aspect=0.5)
    assert (base.footprint_length, base.footprint_width) == (same.footprint_length, same.footprint_width)
    assert tall.footprint_length < base.footprint_length
    assert tall.footprint_length * tall.footprint_width == pytest.approx(base.footprint_length * base.footprint_width, rel=0.02)


def test_rect_not_used_when_its_footprint_does_not_match_the_plan(chain):
    chain.rect_hard = 0
    chain.rect_matches = False
    plan, results = _run()
    assert plan.floors[0].chain_path == "mask_best_effort"
    assert results[0][1][0]["src"] == "mask"


def test_rect_not_used_when_it_is_worse_on_another_gate_metric(chain, monkeypatch):
    chain.rect_hard = 0
    monkeypatch.setattr(
        layout_engine, "_chain_vector",
        lambda plan, results, i, *a: (0, 2) if results[i][1][0]["src"] == "rect" else (0, 1),
    )
    plan, results = _run()
    assert plan.floors[0].chain_path == "mask_best_effort"
