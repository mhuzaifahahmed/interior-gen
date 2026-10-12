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
    to_plot_unit,
)

# Local re-implementation of floor_layout.room_parts() (not imported -
# floor_layout.py already imports score_floor_layout FROM this module, so
# importing floor_layout.py back here would create a circular import, same
# reason _touches_exterior()/_shared_edge_length() below are local
# re-implementations rather than importing blueprint_svg.py's versions).
# A room's own top-level x/y/w/h always stays the true bounding box of its
# parts (floor_layout_mask.py's own invariant) - this is the ONE place
# every area/aspect/exterior-touch check below reads a room's REAL
# occupied cells from, instead of its bbox, so an L/U room's notch is
# never mistaken for floor area it doesn't actually have.
def _room_parts(rect: dict) -> list[dict]:
    parts = rect.get("parts")
    if parts:
        return parts
    return [{"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]}]

# A room whose longer side is more than this many times its shorter side
# reads as a visibly unusable sliver (e.g. the real, reported 20.9x88.1ft
# bedroom) - a soft penalty, not a hard violation, since a somewhat elongated
# room can still be functional.
SLIVER_ASPECT_RATIO = 2.8

# "Realistic packing rules" (2026-10, Part 2 of the "practical layouts"
# refinement) - a real-world room proportion target of ~2:1, grounded in
# standard residential room-proportion conventions, SOFTER than and BELOW
# SLIVER_ASPECT_RATIO - a room between this and the sliver threshold is
# noticeably elongated (not yet a true sliver) and scores a smaller penalty,
# gently steering the best-of-N candidate loop toward squarer rooms WITHOUT
# ever hard-rejecting a tight-but-valid layout (a hard 2:1 cap would mark
# many genuinely fine, slightly-elongated programs infeasible - deliberately
# avoided, see this module's own "best-of-SCORED-CANDIDATES, not a
# guarantee" doctrine).
PROPORTION_TARGET_RATIO = 2.0
ELONGATED_PENALTY = 8.0

# Categories whose shape is INTENTIONALLY linear/elongated by design - never
# penalized for a high aspect ratio (sliver OR the softer elongated tier).
_LINEAR_CATEGORIES = ("hallway", "staircase")

# "Realistic packing rules" (2026-10, Part 2) - a bedroom/living room with NO
# edge on the floor's own exterior boundary has no possible window wall at
# all (a real egress/natural-light requirement, not just a style preference)
# - soft, since the best-of-N loop should prefer a windowed layout over a
# fully-interior one, but a tight program may have no alternative. Only
# bedroom/living are checked - every other category (bathroom, closet,
# hallway, garage...) has no real egress expectation in this codebase's own
# existing room taxonomy.
EGRESS_PENALTY = 15.0
_EGRESS_CATEGORIES = ("bedroom", "living")

# The smallest door that can still be called a real door (half the standard
# "closet/powder" base) - a wall too short for even this is a genuine hard
# violation (the room is too small to have a usable door into it at all),
# matching the plan's "0.6m minimum door" rule.
_MIN_PRACTICAL_DOOR_M = 0.6

HARD_VIOLATION_PENALTY = 1000.0
SLIVER_PENALTY = 25.0
IMBALANCE_PENALTY = 10.0
_EDGE_TOL = 1e-4

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


# Daylight rule (mask planner quality gate, Phase 0): a habitable room needs a
# window-capable stretch of the FOOTPRINT's outer boundary. HARD categories
# must have it, SOFT categories should.
DAYLIGHT_HARD_CATEGORIES = ("bedroom", "living", "kitchen")
DAYLIGHT_SOFT_CATEGORIES = ("dining", "study", "family")
DAYLIGHT_MIN_EDGE_M = 0.9


def footprint_edge_length(part: dict, footprint: dict, tol: float = 1e-3) -> float:
    """Longest stretch of `part`'s edges lying on a side of the footprint
    bbox ({"x0","y0","x1","y1"}). Shared by layout_metrics and the opt-in
    daylight check below."""
    px0, py0 = part["x"], part["y"]
    px1, py1 = px0 + part["w"], py0 + part["h"]
    best = 0.0
    if abs(px0 - footprint["x0"]) < tol or abs(px1 - footprint["x1"]) < tol:
        best = max(best, min(py1, footprint["y1"]) - max(py0, footprint["y0"]))
    if abs(py0 - footprint["y0"]) < tol or abs(py1 - footprint["y1"]) < tol:
        best = max(best, min(px1, footprint["x1"]) - max(px0, footprint["x0"]))
    return max(best, 0.0)


def room_daylight_edge(rect: dict, footprint: dict) -> float:
    """Best window-capable footprint-edge length across a room's parts."""
    return max(
        (footprint_edge_length(p, footprint) for p in _room_parts(rect) if p.get("w", 0) > 0 and p.get("h", 0) > 0),
        default=0.0,
    )


def daylight_failures(rects: list[dict], footprint: dict, unit: str) -> tuple[list[str], list[str]]:
    """(hard_fail_names, soft_fail_names) for the daylight rule."""
    need = to_plot_unit(DAYLIGHT_MIN_EDGE_M, unit)
    hard, soft = [], []
    for rect in rects:
        name = str(rect.get("name") or "")
        cat = classify_room_category(name)
        if cat in DAYLIGHT_HARD_CATEGORIES or cat in DAYLIGHT_SOFT_CATEGORIES:
            if room_daylight_edge(rect, footprint) < need - 1e-6:
                (hard if cat in DAYLIGHT_HARD_CATEGORIES else soft).append(name)
    return hard, soft


def score_floor_layout(
    rects: list[dict],
    dimensions: dict,
    garage_cars: int | None = None,
    daylight_footprint: dict | None = None,
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

        # Parts-aware (2026-10, grid-mask engine): a room is a UNION OF
        # RECTANGULAR PARTS now (see _room_parts() above), not always a
        # single rectangle - a plain-rectangle room's single part IS its
        # bbox, so every check below is numerically identical to before on
        # the rect engine's own output (byte-compatible), and only differs
        # for an L/U room, where the bbox would otherwise overstate its
        # real area and exaggerate its aspect ratio (an L's bbox always
        # includes its own empty notch).
        parts = _room_parts(rect)
        valid_parts = [p for p in parts if p.get("w", 0) > 0 and p.get("h", 0) > 0]
        if not valid_parts:
            violations.append(Violation(name, "degenerate", True, "zero or negative dimension"))
            continue

        area = sum(p["w"] * p["h"] for p in valid_parts)
        # Parts-aware "which part actually governs usability" (2026-10,
        # real bug found via the capacity-table feasibility probe: with
        # governing_width taken as the MINIMUM across every part, a room
        # with a perfectly usable, real-sized PRIMARY chunk still failed
        # this check - a hard violation - purely because the mask engine's
        # own documented steal/fill-leftover fallback mechanisms (see
        # floor_layout_mask.py's module docstring, "a known, honestly-
        # stated M1 limitation") had attached one small, thin SECONDARY
        # part to it. That one hard violation, multiplied across nearly
        # every room on nearly every program, made
        # feasibility._geometrically_placeable_area() unable to find a
        # single zero-hard-violation candidate at ANY margin (even 3.5x the
        # real required area) for even the smallest room count - the real
        # root cause behind "the Bedrooms/floor dropdown only ever offers
        # 1" on a real, very large plot. Since 2026-10's _carve_rooms() fix,
        # a thin secondary part already renders seamlessly merged into its
        # room (no visible defect) - so it no longer deserves to be judged
        # as harshly as a room whose ENTIRE shape is a sliver. The room's
        # LARGEST part (the same "primary chunk" already used by the foyer
        # check just below, and by blueprint_svg.py's furniture/label
        # placement) is now what the HARD check judges; a thin SECONDARY
        # part is downgraded to a soft "sliver_part" violation instead of
        # silently losing the signal entirely.
        largest_part = max(valid_parts, key=lambda p: p["w"] * p["h"])
        primary_width = min(largest_part["w"], largest_part["h"])
        governing_width = min(min(p["w"], p["h"]) for p in valid_parts)

        # HARD: too small in area for this room type at all.
        min_area = min_area_for_room(name, unit, garage_cars)
        if area < min_area * _AREA_TOLERANCE:
            violations.append(
                Violation(name, "small_area", True, f"{area:.1f} < min area {min_area:.1f}")
            )

        # HARD: the room's own LARGEST (primary) part is narrower than this
        # room type's real minimum usable width - catches a room whose
        # ENTIRE shape is a sliver (e.g. a very thin, very deep room), which
        # is what this check was always meant to catch.
        if category not in ("garage", "staircase", "hallway"):
            min_w = min_width_for_room(name, unit)
            if primary_width < min_w * _AREA_TOLERANCE:
                violations.append(
                    Violation(name, "narrow", True, f"{primary_width:.1f} < min width {min_w:.1f} (largest part)")
                )
            elif len(valid_parts) > 1 and governing_width < min_w * _AREA_TOLERANCE:
                # A genuinely usable primary chunk exists; a smaller
                # SECONDARY part (the engine's own accepted steal/fill-
                # leftover fallback) is thinner than real-world min width -
                # soft, not hard, since it renders invisibly merged into
                # the room and was never what this check was meant to block.
                violations.append(
                    Violation(
                        name, "sliver_part", False,
                        f"a secondary part is {governing_width:.1f} < min width {min_w:.1f}",
                    )
                )

        # HARD: the foyer/entry specifically must meet its real minimum
        # SHAPE - direct regression guard for the "1-foot entrance" bug.
        # 2026-10 bug fix: this used to compare governing_width (min(w,h))
        # against min_depth_for_room() alone - but a foyer's whole point is
        # an ASYMMETRIC shape (min_depth > min_width, see room_specs.py's
        # own foyer entry: narrow but deep, like a real entryway) - so that
        # comparison could NEVER pass for a foyer shaped exactly as
        # intended (its narrower side will always be < its own min_depth).
        # A correctly-shaped foyer was being flagged as a hard violation
        # unconditionally, which stayed invisible while this scorer only
        # ever picked the "least bad" candidate - but directly blocked
        # every real foyer once Part 2's honest-capacity geometry check
        # started REJECTING any count with a hard violation. Fixed to check
        # both dimensions against their own correct minimum, independent of
        # which way the rect happens to be oriented (the slicer doesn't
        # guarantee a foyer's "w" is literally its narrow side).
        if category == "foyer":
            min_w = min_width_for_room(name, unit)
            min_depth = min_depth_for_room(name, unit)
            # Judged on the LARGEST part (a multi-part foyer is unlikely,
            # but if one occurs, what matters is whether it has ONE real
            # entry-shaped nook, not its overstated bbox) - same "largest
            # part" convention blueprint_svg.py's furniture/label/column
            # placement uses for multi-part rooms.
            largest = max(valid_parts, key=lambda p: p["w"] * p["h"])
            narrow_dim, long_dim = min(largest["w"], largest["h"]), max(largest["w"], largest["h"])
            if narrow_dim < min_w * _AREA_TOLERANCE or long_dim < min_depth * _AREA_TOLERANCE:
                violations.append(
                    Violation(
                        name, "foyer_shallow", True,
                        f"{narrow_dim:.1f}x{long_dim:.1f} < {min_w:.1f}x{min_depth:.1f}",
                    )
                )

        # SOFT: a visible sliver (aspect ratio) OR merely elongated past the
        # real ~2:1 proportion target (2026-10, Part 2 "realistic packing
        # rules") - excluding categories meant to be linear (hallway/
        # staircase). The elongated tier is a strictly smaller penalty than
        # a true sliver, so the candidate loop prefers squarer rooms without
        # ever treating "a bit longer than 2:1" as being as bad as a real
        # sliver.
        if category not in _LINEAR_CATEGORIES:
            # Judged per PART, worst part wins - an L/U room's bbox aspect
            # ratio always overstates elongation (the bbox includes the
            # empty notch), so a room with a square main body and a short
            # connecting arm must not be judged by its bbox; its own
            # thinnest/most-elongated PART is the real signal (and a
            # genuinely too-thin part is already caught separately by the
            # HARD "narrow" check above, which is also now parts-aware).
            aspect = max(max(p["w"], p["h"]) / max(min(p["w"], p["h"]), 0.01) for p in valid_parts)
            if aspect > SLIVER_ASPECT_RATIO:
                violations.append(Violation(name, "sliver", False, f"aspect ratio {aspect:.1f}"))
            elif aspect > PROPORTION_TARGET_RATIO:
                violations.append(Violation(name, "elongated", False, f"aspect ratio {aspect:.1f}"))

        # SOFT: a bedroom/living room with no edge on the floor's own
        # exterior boundary has no possible window wall (2026-10, Part 2
        # "realistic packing rules" - a real egress/natural-light
        # expectation). `dimensions` here is the candidate's OWN box (see
        # floor_layout.layout_floor_with_warnings()'s best-of-N loop, which
        # always scores a candidate against the exact box it was built to
        # tile) - rects genuinely touch its 0/length/width edges directly,
        # no further wall-thickness offset needed at this layer.
        if daylight_footprint is None and category in _EGRESS_CATEGORIES and not any(_touches_exterior(p, dimensions) for p in valid_parts):
            violations.append(Violation(name, "no_egress", False, "no exterior wall for a window"))

        # SOFT: well under its own preferred size while other rooms on the
        # same floor sit comfortably at/above theirs - a crude but real
        # signal of imbalance (not a hard violation, since "below preferred"
        # is still a usable room, just not an ideal one).
        preferred = preferred_area_for_room(name, unit, garage_cars)
        if preferred > min_area and area < min_area + (preferred - min_area) * 0.3:
            violations.append(Violation(name, "below_preferred", False, f"{area:.1f} well under preferred {preferred:.1f}"))

    if daylight_footprint is not None:
        hard_names, soft_names = daylight_failures(rects, daylight_footprint, unit)
        for n in hard_names:
            violations.append(Violation(n, "no_daylight", True, "no window-capable footprint edge"))
        for n in soft_names:
            violations.append(Violation(n, "no_daylight", False, "no window-capable footprint edge"))

    violations.extend(_door_clearance_violations(rects, unit))
    violations.extend(_coverage_violation(rects, dimensions))

    score = 0.0
    _SOFT_PENALTY_BY_KIND = {
        "sliver": SLIVER_PENALTY,
        "elongated": ELONGATED_PENALTY,
        "no_egress": EGRESS_PENALTY,
    }
    for v in violations:
        score -= HARD_VIOLATION_PENALTY if v.hard else _SOFT_PENALTY_BY_KIND.get(v.kind, IMBALANCE_PENALTY)

    return LayoutScore(score=score, violations=violations)


_COVERAGE_TOLERANCE_FRACTION = 0.02


def _coverage_violation(rects: list[dict], dimensions: dict) -> list[Violation]:
    """HARD violation when the real occupied area across every returned
    room's own PARTS doesn't add up to (approximately) the floor's own
    length x width - a direct, permanent regression guard for the
    "black hole" bug fixed 2026-10 (Phase A2, floor_layout_mask.py's
    _fill_leftovers()): an unclaimed grid cell never appears as part of
    ANY room, so it was previously invisible to this scorer entirely (no
    rect was wrong, there was simply a cell missing from every rect) - a
    floor could render with a literal unexplained solid black hole and
    still score as if it had zero problems. This check doesn't care WHY
    coverage is short (a future regression in either engine would be
    caught the same way, not just this one historical cause) - it only
    verifies the floor's own arithmetic adds up, the same "does the real
    output explain 100% of the footprint" question a human inspecting the
    PNG would ask. A small tolerance absorbs ordinary floating-point
    accumulation across many small rect/part multiplications, not a real
    gap - not a loophole for an actual missing region, which is typically
    many cells (a visibly obvious fraction of the floor), not a rounding
    sliver."""
    length = float(dimensions.get("length") or 0)
    width = float(dimensions.get("width") or 0)
    floor_area = length * width
    if floor_area <= 0:
        return []
    covered = sum(
        p["w"] * p["h"]
        for rect in rects
        for p in _room_parts(rect)
        if p.get("w", 0) > 0 and p.get("h", 0) > 0
    )
    shortfall = floor_area - covered
    if shortfall > floor_area * _COVERAGE_TOLERANCE_FRACTION:
        return [
            Violation(
                "__floor__", "coverage_gap", True,
                f"{covered:.1f} covered vs {floor_area:.1f} floor area ({shortfall:.1f} unexplained)",
            )
        ]
    return []


def _touches_exterior(rect: dict, dimensions: dict) -> bool:
    """True when `rect` has at least one edge on `dimensions`'s own box
    boundary (0 or length/width) - i.e. a real exterior wall, where a window
    could actually go. Used by the egress check above; a local, tolerant
    comparison (same style as _shared_edge_length() below) rather than
    importing floor_layout.footprint_bbox() here, which would create a
    circular import (floor_layout.py imports FROM this module)."""
    length = float(dimensions.get("length") or 0)
    width = float(dimensions.get("width") or 0)
    x, y, w, h = rect.get("x", 0.0), rect.get("y", 0.0), rect.get("w", 0.0), rect.get("h", 0.0)
    return (
        abs(x) < _EDGE_TOL
        or abs(y) < _EDGE_TOL
        or abs((x + w) - length) < _EDGE_TOL
        or abs((y + h) - width) < _EDGE_TOL
    )


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
            # Parts-aware (2026-10): the LONGEST shared wall across any
            # pair of the two rooms' own parts, not the two rooms' bbox -
            # an L/U room's bbox can touch a neighbor's bbox along a span
            # neither room actually has real floor against (the notch),
            # which would wrongly demand door clearance against empty air.
            shared = max(
                (_shared_edge_length(pa, pb, tol) or 0.0)
                for pa in _room_parts(a)
                for pb in _room_parts(b)
            )
            if shared < 0.3:
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
