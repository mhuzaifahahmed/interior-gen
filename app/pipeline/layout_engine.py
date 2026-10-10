"""Single dispatcher between the two deterministic room-layout algorithms -
app/pipeline/floor_layout.py's original rectangle slice-and-dice engine
("rect") and app/pipeline/floor_layout_mask.py's grid-mask region-growing
engine ("mask", produces real L/U-shaped rooms, a real circulation spine,
and window-wall egress for every bedroom/living room BY CONSTRUCTION - see
that module's own docstring for the full rationale and the structural bugs
in the rect engine's banded private-zone packing that motivated it).

This is a pure feature-flag seam (app/config.py's `house_layout_engine`,
"rect" or "mask") - every caller that used to import layout_floor_with_
warnings()/layout_floor() directly from floor_layout.py now imports the
SAME two function names from HERE instead, with an IDENTICAL signature and
return shape either way, so routing is the only thing this module does.
The three real call sites (app/pipeline/generate_house.py's render step,
app/pipeline/feasibility.py's geometry-verified capacity probe, and
app/providers/kaggle_autocad.py's AI Concept Layout conditioning image) all
route through here - this guarantees all three ALWAYS agree on which
engine produced a given floor's geometry; feasibility.py's probe in
particular must use the exact engine that will actually render, or its
"does this fit" answer could silently disagree with the real output.

Default is "rect" - this is a rollout flag, not a replacement. The live
site keeps producing today's proven rectangular output until the mask
engine is visually verified across the full test matrix and this default
is deliberately flipped in its own commit (see the plan file's milestone
roadmap). Reverting after a flip is a one-line config change, not a code
rollback."""

from app.config import settings
from app.pipeline import floor_layout, floor_layout_mask


def layout_floor_with_warnings(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None
) -> tuple[list[dict], list]:
    """Routes to the configured engine's own layout_floor_with_warnings() -
    see floor_layout.layout_floor_with_warnings()'s docstring for the full
    parameter/return contract, identical on both engines."""
    engine = floor_layout_mask if settings.house_layout_engine == "mask" else floor_layout
    return engine.layout_floor_with_warnings(rooms, dimensions, garage_cars, facing)


def layout_floor(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None
) -> list[dict]:
    """Routes to the configured engine's own layout_floor() - plain rects/
    parts list, no warnings (see layout_floor_with_warnings() above)."""
    rects, _warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars, facing)
    return rects
