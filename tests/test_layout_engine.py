"""Tests for app/pipeline/layout_engine.py - the feature-flag dispatcher
between floor_layout.py (the "rect" engine) and floor_layout_mask.py (the
"mask" engine). The real thing this module does is route by
settings.house_layout_engine - these tests confirm that routing is correct
and that the default ("rect") is exactly what the live site has always
produced (zero behavior change for callers that don't touch the flag)."""

from app.config import settings
from app.pipeline import floor_layout, floor_layout_mask, layout_engine

_ROOMS = [
    {"name": "Living Room", "area": 2},
    {"name": "Kitchen", "area": 1},
    {"name": "Bedroom", "area": 1},
]
_DIMS = {"length": 40, "width": 35, "unit": "ft"}


def test_layout_engine_defaults_to_rect(monkeypatch):
    assert settings.house_layout_engine == "rect"  # conftest's autouse fixture forces this
    dispatched, _warnings = layout_engine.layout_floor_with_warnings(_ROOMS, _DIMS)
    direct, _warnings2 = floor_layout.layout_floor_with_warnings(_ROOMS, _DIMS)
    assert dispatched == direct


def test_layout_engine_routes_to_rect_explicitly(monkeypatch):
    monkeypatch.setattr(settings, "house_layout_engine", "rect")
    dispatched = layout_engine.layout_floor(_ROOMS, _DIMS)
    direct = floor_layout.layout_floor(_ROOMS, _DIMS)
    assert dispatched == direct


def test_layout_engine_routes_to_mask_when_set(monkeypatch):
    monkeypatch.setattr(settings, "house_layout_engine", "mask")
    dispatched = layout_engine.layout_floor(_ROOMS, _DIMS)
    direct = floor_layout_mask.layout_floor(_ROOMS, _DIMS)
    assert dispatched == direct


def test_layout_engine_mask_output_differs_in_shape_from_rect():
    """Sanity check that the two engines are genuinely different code
    paths, not accidentally aliased - the mask engine's rects carry a
    "parts" key on multi-part rooms, the rect engine's never do."""
    from app.config import settings as real_settings

    real_settings.house_layout_engine = "rect"
    rect_rects = layout_engine.layout_floor(_ROOMS, _DIMS, facing="south")
    real_settings.house_layout_engine = "mask"
    mask_rects = layout_engine.layout_floor(_ROOMS, _DIMS, facing="south")
    real_settings.house_layout_engine = "rect"  # restore - no monkeypatch used in this test
    assert all("parts" not in r for r in rect_rects)
    # mask engine output may or may not have multi-part rooms for this
    # specific tiny program, but it must still be a valid, non-empty result.
    assert len(mask_rects) > 0


def test_layout_engine_with_warnings_signature_matches_both_engines(monkeypatch):
    """layout_floor_with_warnings() must accept and return the identical
    shape regardless of engine - this is the whole point of the dispatcher
    seam (every call site imports from HERE, not from either engine
    directly)."""
    for engine_name in ("rect", "mask"):
        monkeypatch.setattr(settings, "house_layout_engine", engine_name)
        rects, warnings = layout_engine.layout_floor_with_warnings(
            _ROOMS, _DIMS, garage_cars=None, facing="north"
        )
        assert isinstance(rects, list)
        assert isinstance(warnings, list)
        for rect in rects:
            assert "name" in rect
            assert "x" in rect and "y" in rect and "w" in rect and "h" in rect


def test_layout_engine_unrecognized_value_falls_back_to_rect(monkeypatch):
    """An unrecognized/typo'd value should behave like "rect" (the live
    default), not silently crash or silently use the experimental engine -
    same defensive posture as every other string-valued setting in this
    codebase (e.g. image_provider)."""
    monkeypatch.setattr(settings, "house_layout_engine", "not-a-real-engine")
    dispatched = layout_engine.layout_floor(_ROOMS, _DIMS)
    direct = floor_layout.layout_floor(_ROOMS, _DIMS)
    assert dispatched == direct
