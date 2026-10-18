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
    layout_engine.layout_building([[{"name": "a"}], [{"name": "b"}]], PLOT, "north", None, 2)
    assert [f.chain_path for f in plan.floors] == ["mask", "mask_best_effort"]
