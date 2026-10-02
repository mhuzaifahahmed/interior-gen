"""Deterministic, pure scoring of a generated floor layout's real-world
practicality - the "intelligence loop" half of the Measurements Model
refinement (2026-10). No I/O, no model call, nothing to mock in tests - same
testability doctrine as floor_layout.py, which this module is deliberately
kept independent of (floor_layout.py imports FROM here for its best-of-N
candidate selection, not the other way around - avoids a circular import).

Why this exists: previously floor_layout.py computed exactly ONE layout per
floor and shipped it regardless of how impractical the result was (a sliver
room, a foyer squeezed to nothing, a door that couldn't physically fit on a
short wall). This module scores a layout's real violations - HARD ones (a
room that's genuinely too small/narrow/disconnected to be usable) and SOFT
ones (slivers, size imbalance) - so floor_layout.layout_floor() can generate
a small, bounded set of candidate layouts (varying only safe,
already-parameterized knobs) and pick the best-scoring one, or honestly flag
a floor that has no good candidate at all instead of silently shipping a
cramped plan.

This is best-of-SCORED-CANDIDATES, not a mathematical guarantee of a valid
layout - a genuinely infeasible room program (see app/pipeline/feasibility.py,
which runs BEFORE layout_floor() is ever called) can still produce a floor
with real hard violations even in its best candidate; that's surfaced via
`quality_warnings`, not hidden.
"""

from dataclasses import dataclass, field

from app.pipeline.room_specs import (
    classify_room_category,
    door_width_for_wall,
    min_area_for_room,
    min_depth_for_room,
    min_width_for_room,
    preferred_area_for_room,
)

# A room whose longer side is more than this many times its shorter side
# reads as a visibly unusable sliver (e.g. the real, reported 20.9x88.1ft
# bedroom) - a soft penalty, not a hard violation, since a somewhat elongated
# room can still be functional.
SLIVER_ASPECT_RATIO = 2.8

# Categories whose shape is INTENTIONALLY linear/elongated by design - never
# penalized for a high aspect ratio.
_LINEAR_CATEGORIES = ("hallway", "staircase")

# The smallest door that can still be called a real door (half the standard
# "closet/powder" base) - a wall too short for even this is a genuine hard
# violation (the room is too small to have a usable door into it at all),
# matching the plan's "0.6m minimum door" rule.
_MIN_PRACTICAL_DOOR_M = 0.6

HARD_VIOLATION_PENALTY = 1000.0
SLIVER_PENALTY = 25.0
IMBALANCE_PENALTY = 10.0

# Real-world area tolerance for float/rounding noise in the min-area
# guarantee pass (floor_layout.py's own weighted redistribution can leave a
# room a hair under its exact target due to floating-point accumulation) -
# not a loophole, just avoids flagging a false positive for a sub-1%
# difference.
_AREA_TOLERANCE = 0.98


@dataclass
class Violation:
    room: str
    kind: str
    hard: bool
    detail: str


@dataclass
class LayoutScore:
    score: float
    violations: list[Violation] = field(default_factory=list)

    @property
    def has_hard_violations(self) -> bool:
        return any(v.hard for v in self.violations)

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.hard]


def score_floor_layout(
    rects: list[dict], dimensions: dict, garage_cars: int | None = None
) -> LayoutScore:
    """Scores one floor's already-computed rectangles (floor_layout.py's
    output shape - [{"name","x","y","w","h"}, ...]) for real-world
    practicality. Higher is better; 0.0 with no violations is a clean floor.
    Pure function of the rects + dimensions - never mutates its input."""
    unit = dimensions.get("unit") or "ft"
    violations: list[Violation] = []

    for rect in rects:
        name = str(rect.get("name") or "")
        if not name:
            continue
        category = classify_room_category(name)
        w, h = float(rect.get("w") or 0), float(rect.get("h") or 0)
        if w <= 0 or h <= 0:
            violations.append(Violation(name, "degenerate", True, "zero or negative dimension"))
            continue

        governing_width = min(w, h)
        area = w * h

        # HARD: too small in area for this room type at all.
        min_area = min_area_for_room(name, unit, garage_cars)
        if area < min_area * _AREA_TOLERANCE:
            violations.append(
                Violation(name, "small_area", True, f"{area:.1f} < min area {min_area:.1f}")
            )

        # HARD: narrower than this room type's real minimum usable width -
        # catches a sliver that happens to clear the area floor only because
        # it's very long (e.g. a very thin, very deep room).
        if category not in ("garage", "staircase", "hallway"):
            min_w = min_width_for_room(name, unit)
            if governing_width < min_w * _AREA_TOLERANCE:
                violations.append(
                    Violation(name, "narrow", True, f"{governing_width:.1f} < min width {min_w:.1f}")
                )

        # HARD: the foyer/entry specifically must meet its real minimum
        # depth - direct regression guard for the "1-foot entrance" bug.
        if category == "foyer":
            min_depth = min_depth_for_room(name, unit)
            if governing_width < min_depth * _AREA_TOLERANCE:
                violations.append(
                    Violation(name, "foyer_shallow", True, f"depth {governing_width:.1f} < {min_depth:.1f}")
                )

        # SOFT: a visible sliver (aspect ratio), excluding categories meant
        # to be linear (hallway/staircase).
        if category not in _LINEAR_CATEGORIES:
            aspect = max(w, h) / max(governing_width, 0.01)
            if aspect > SLIVER_ASPECT_RATIO:
                violations.append(Violation(name, "sliver", False, f"aspect ratio {aspect:.1f}"))

        # SOFT: well under its own preferred size while other rooms on the
        # same floor sit comfortably at/above theirs - a crude but real
        # signal of imbalance (not a hard violation, since "below preferred"
        # is still a usable room, just not an ideal one).
        preferred = preferred_area_for_room(name, unit, garage_cars)
        if preferred > min_area and area < min_area + (preferred - min_area) * 0.3:
            violations.append(Violation(name, "below_preferred", False, f"{area:.1f} well under preferred {preferred:.1f}"))

    violations.extend(_door_clearance_violations(rects, unit))

    score = 0.0
    for v in violations:
        score -= HARD_VIOLATION_PENALTY if v.hard else (SLIVER_PENALTY if v.kind == "sliver" else IMBALANCE_PENALTY)

    return LayoutScore(score=score, violations=violations)


def _door_clearance_violations(rects: list[dict], unit: str) -> list[Violation]:
    """HARD violation when two adjacent rooms share a wall too short to fit
    even the smallest practical door (0.6m) between them - the room(s)
    involved are too small to realistically connect to the rest of the
    house. Reuses the same adjacency detection blueprint_svg.py's door
    renderer already relies on (a local, tolerant re-implementation to avoid
    importing blueprint_svg.py here, which would create a circular
    import - blueprint_svg.py itself imports from floor_layout.py, which
    imports from this module)."""
    violations: list[Violation] = []
    tol = 1e-4
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            shared = _shared_edge_length(a, b, tol)
            if shared is None or shared < 0.3:
                continue
            cat_a, cat_b = classify_room_category(a.get("name") or ""), classify_room_category(b.get("name") or "")
            door_w = door_width_for_wall(cat_a, cat_b, unit)
            min_door = door_width_for_wall("closet", "closet", unit)  # the 0.6m floor, in-unit
            if shared < min(door_w, min_door) * 0.9:
                violations.append(
                    Violation(
                        f"{a.get('name')}<->{b.get('name')}", "door_too_narrow", True,
                        f"shared wall {shared:.1f} too short for a real door",
                    )
                )
    return violations


def _shared_edge_length(a: dict, b: dict, tol: float) -> float | None:
    if abs((a["x"] + a["w"]) - b["x"]) < tol or abs((b["x"] + b["w"]) - a["x"]) < tol:
        y_start, y_end = max(a["y"], b["y"]), min(a["y"] + a["h"], b["y"] + b["h"])
        if y_end > y_start:
            return y_end - y_start
    if abs((a["y"] + a["h"]) - b["y"]) < tol or abs((b["y"] + b["h"]) - a["y"]) < tol:
        x_start, x_end = max(a["x"], b["x"]), min(a["x"] + a["w"], b["x"] + b["w"])
        if x_end > x_start:
            return x_end - x_start
    return None
