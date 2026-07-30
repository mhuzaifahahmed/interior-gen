from app.providers.idealhouse import generate_floor_plan


def test_generate_floor_plan_always_returns_none():
    # No floor-plan vendor is wired in yet (see the module's docstring for the
    # ModelsLab/ideal.house research trail) - this must stay a safe, expected
    # "not available" signal, not raise.
    assert generate_floor_plan("a rectangular plot", {"length": 40, "width": 60, "unit": "ft"}, "modern") is None


def test_generate_floor_plan_handles_missing_inputs():
    assert generate_floor_plan(None, {}, "") is None
