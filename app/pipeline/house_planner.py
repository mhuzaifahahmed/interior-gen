"""Rules-driven house planner (mask planner, Phase 2). Pure - no I/O, no
model call, deterministic.

Turns plot + room program into a sensible BUILDING PLAN that the grid-mask
engine (floor_layout_mask.py) then only SHAPES rooms inside:

  - one footprint for every floor (governed by the ground floor; walls stack),
    sized by plot band (planning_rules.PLOT_BANDS) and clamped by what the
    room program can actually use (program_min .. program_ceiling) and by
    the envelope left after the band's minimum setbacks;
  - yard depths (front/rear/side), starting at each side's band minimum and
    sharing the remaining slack in proportion to those minimums (a garage
    forces front >= GARAGE_MIN_FRONT_SETBACK_M);
  - per floor, a TARGET area for every room plus any planner-ADDED rooms
    (bonus rooms from BONUS_ROOM_PRIORITY, an upper-floor "Terrace" for the
    remainder) - each added room dict carries planner_added=True and an
    added_reason, so it can be persisted and (Phase 5) visibly marked.

Allocation per floor (usable = footprint - circulation): every room's
minimum -> raise toward preferred -> toward target -> add bonus rooms in
priority order -> raise toward max -> upper floors: remainder becomes a
Terrace. HIERARCHY_RULES are applied to the result. Bedroom and bathroom
counts are never changed.

Behind Settings.house_planner_enabled (default False) - nothing calls this
unless the flag is on.
"""

import math
from dataclasses import dataclass, field

from app.pipeline import planning_rules as rules
from app.pipeline.floor_layout import _zone_key
from app.pipeline.room_specs import (
    staircase_dimensions,
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    max_area_for_room,
    min_area_for_room,
    preferred_area_for_room,
    target_area_for_room,
    to_plot_unit,
)

ADDED_REASON_SPARE = "spare floor area"
ADDED_REASON_TERRACE = "spare upper-floor area"
ADDED_REASON_PORCH = "covered open area (spare ground-floor area)"

# The footprint is never sized to make EVERY room sit exactly at its max:
# grid packing is never 100% efficient, and with zero slack any packing gap
# can only be dumped into a room past its max. The ceiling used for sizing is
# this fraction of the program ceiling, leaving the engine real headroom.
CEILING_PACKING_FRACTION = 0.85

# Footprint aspect bound (cross:depth), "bounded at about 1:2".
_MAX_ASPECT = 2.0

# XS-band minimum setbacks (ft) the planner shrinks toward when the program
# does not fit inside the band's own envelope.
_XS_BAND = rules.PLOT_BANDS[0]

_LIVING = "living"
_DINING = "dining"
_KITCHEN = "kitchen"


def _sqft_to_unit(sqft: float, unit: str) -> float:
    if (unit or "ft").strip().lower().startswith("f"):
        return sqft
    return sqft / (to_plot_unit(1.0, "ft") ** 2)


@dataclass
class FloorPlan:
    floor_number: int
    footprint_length: float  # along plot x
    footprint_width: float  # along plot y
    yards: dict  # gross distances from the plot edge: top/bottom/left/right (+ front/rear/side)
    room_targets: dict = field(default_factory=dict)  # name -> target area (unit^2)
    room_max: dict = field(default_factory=dict)  # name -> max area override (unit^2)
    added_rooms: list = field(default_factory=list)
    usable_area: float = 0.0
    circulation_area: float = 0.0
    unallocated_area: float = 0.0
    # Names of the floor's remainder-sink rooms (upper-floor Terrace, ground-
    # floor Verandah): sized to the leftover area, so they are reported
    # separately and never counted as "oversized".
    remainder_rooms: list = field(default_factory=list)
    # Diagnostic (set by floor_layout_mask after layout): which candidate
    # tier won - "rectified" / "reference_reconnect" / "reference_raw".
    layout_path: str = ""
    # Phase 4b step B: the staircase's FIXED block {"x","y","w","h"} in the
    # engine's CORE footprint coordinates (front edge at y=0; east/west
    # facings transposed), identical on every floor; None = no fixed stair.
    stair_block: dict | None = None
    # Diagnostic (set by floor_layout_mask after layout): fraction of the
    # floor's grid cells the bounded fill had to place with no neighbour
    # headroom (its last resort). layout_building's footprint-scale trigger.
    fill_overflow_fraction: float = 0.0


@dataclass
class BuildingPlan:
    unit: str
    footprint_length: float
    footprint_width: float
    yards: dict
    band: str
    net_plot_area: float
    footprint_area: float
    program_min: float
    program_ceiling: float
    envelope_area: float
    infeasible: bool
    floors: list = field(default_factory=list)
    tier: str = "standard"
    porch_area: float = 0.0  # ground-floor Verandah share of footprint_area
    built_area_cap: float | None = None  # estate cap on footprint x floors (L/XL)
    total_floors: int = 1
    # Phase 4b step B: see FloorPlan.stair_block. Sized here (stair_block_
    # size), POSITIONED by floor_layout_mask.layout_building next to the
    # spine it will reserve (the spine/band geometry lives in the engine).
    stair_block: dict | None = None
    stair_area: float = 0.0
    # Step 5.2: fraction of the planned footprint area actually used (1.0 =
    # as planned; < 1.0 = a smaller footprint candidate chosen by the engine).
    footprint_scale: float = 1.0

    def for_floor(self, floor_number: int) -> FloorPlan:
        for fp in self.floors:
            if fp.floor_number == floor_number:
                return fp
        return self.floors[0]


# ---------------------------------------------------------------- program


def _private_count(rooms: list[dict]) -> int:
    return sum(1 for r in rooms if _zone_key(str(r.get("name") or "")) == 2)


def _eligible_bonus_rooms(
    rooms: list[dict], floor_number: int, exclude: set | None = None, tier: str = "standard",
) -> list[dict]:
    """Bonus-room entries (planning_rules.BONUS_ROOM_PRIORITY_BY_TIER[tier])
    eligible on this floor that the program does not already have (by
    category; walk-in closet: one per master suite). Returns
    [{"name","category"}] in priority order. Never a bedroom or bathroom.
    Standard tier = exactly the Phase 2/3 list (one room per entry)."""
    names = {str(r.get("name") or "").strip().lower() for r in rooms}
    cats = [classify_room_category(str(r.get("name") or "")) for r in rooms]
    masters = sum(
        1 for r in rooms
        if "master" in str(r.get("name") or "").lower()
        and classify_room_category(str(r.get("name") or "")) == "bedroom"
    )
    min_target = (
        rules.BONUS_ROOM_MIN_TARGET_SQM if tier == "standard"
        else rules.BONUS_ROOM_MIN_TARGET_SQM_BY_TIER.get(tier, rules.BONUS_ROOM_MIN_TARGET_SQM)
    )
    out = []
    added_by_cat: dict[str, int] = {}
    for entry in rules.BONUS_ROOM_PRIORITY_BY_TIER.get(tier, rules.BONUS_ROOM_PRIORITY):
        floors = entry["floors"]
        if floors == "ground" and floor_number != 1:
            continue
        if floors == "upper" and floor_number == 1:
            continue
        allowed = masters if entry["per_suite"] else entry["max_count"]
        existing = cats.count(entry["category"])
        if tier != "standard":
            existing += added_by_cat.get(entry["category"], 0)
        if entry["name"].lower() in names or existing >= allowed or entry["name"] in (exclude or ()):
            continue
        if rules.tier_target_area(entry["name"], "m", tier=tier) < min_target:
            continue
        count = 1 if tier == "standard" else allowed - existing
        for k in range(count):
            name = entry["name"] if k == 0 else f"{entry['name']} {k + 1}"
            if name.lower() in names or name in (exclude or ()):
                continue
            out.append({"name": name, "category": entry["category"]})
            added_by_cat[entry["category"]] = added_by_cat.get(entry["category"], 0) + 1
    return out


def _max_area(name: str, unit: str, garage_cars, tier: str) -> float:
    if tier == "standard":
        return max_area_for_room(name, unit, garage_cars)
    return rules.tier_max_area(name, unit, garage_cars, tier)


def program_bounds(
    rooms: list[dict], unit: str, garage_cars: int | None = None, floor_number: int = 1,
    include_bonus: bool = True, exclude_bonus: set | None = None, tier: str = "standard",
) -> tuple[float, float]:
    """(program_min, program_ceiling) for one floor's room program, in the
    plot unit squared. program_min = sum of minimums x (1 + circulation);
    program_ceiling = sum of (tier) max areas of the requested rooms plus
    every eligible bonus room, x (1 + circulation)."""
    circ = rules.circulation_fraction(_private_count(rooms))
    names = [str(r.get("name") or "") for r in rooms]
    pmin = sum(min_area_for_room(n, unit, garage_cars) for n in names)
    pmax = sum(max(_max_area(n, unit, garage_cars, tier), min_area_for_room(n, unit, garage_cars)) for n in names)
    if include_bonus:
        pmax += sum(
            _max_area(b["name"], unit, garage_cars, tier)
            for b in _eligible_bonus_rooms(rooms, floor_number, exclude_bonus, tier)
        )
    return pmin * (1 + circ), pmax * (1 + circ)


def built_area_cap(floors_rooms: list[list[dict]], band_name: str, unit: str) -> float | None:
    """Estate cap (option 4): on L/XL bands, total built area (footprint x
    floors) <= bedrooms x BUILT_SQFT_PER_BEDROOM_CAP, bedrooms counted from
    the requested program (never changed). None when it does not apply."""
    if band_name not in rules.BUILT_AREA_CAP_BANDS:
        return None
    bedrooms = sum(
        1 for rooms in floors_rooms for r in rooms
        if classify_room_category(str(r.get("name") or "")) == "bedroom"
    )
    if bedrooms <= 0:
        return None
    return _sqft_to_unit(bedrooms * rules.BUILT_SQFT_PER_BEDROOM_CAP, unit)


# ---------------------------------------------------------------- footprint


def _setbacks(band: rules.PlotBand, unit: str, garage_cars, min_front: float | None, exterior: float) -> dict:
    s = rules.band_setbacks(band, unit)
    front = s["front"]
    if garage_cars:
        front = max(front, to_plot_unit(rules.GARAGE_MIN_FRONT_SETBACK_M, unit))
    if min_front:
        front = max(front, float(min_front))
    return {
        "front": max(front, exterior),
        "rear": max(s["rear"], exterior),
        "side": max(s["side"], exterior),
    }


def _envelope(depth_len, cross_len, sb) -> tuple[float, float]:
    return max(depth_len - sb["front"] - sb["rear"], 0.0), max(cross_len - 2 * sb["side"], 0.0)


def _shape(area: float, env_depth: float, env_cross: float) -> tuple[float, float]:
    """(fp_depth, fp_cross) with the given area, aspect following the
    envelope (bounded 1:2), never exceeding the envelope."""
    if area <= 0 or env_depth <= 0 or env_cross <= 0:
        return env_depth, env_cross
    aspect = min(max(env_cross / env_depth, 1 / _MAX_ASPECT), _MAX_ASPECT)
    depth = math.sqrt(area / aspect)
    cross = area / depth
    if cross > env_cross:
        cross = env_cross
        depth = min(area / cross, env_depth)
    if depth > env_depth:
        depth = env_depth
        cross = min(area / depth, env_cross)
    return depth, cross


def _yards(depth_len, cross_len, fp_depth, fp_cross, sb) -> tuple[float, float, float]:
    """(front, rear, side) gross yard depths. Each starts at its band
    minimum; slack is shared in proportion to those minimums."""
    side = max((cross_len - fp_cross) / 2, 0.0)
    slack = max(depth_len - fp_depth, 0.0)
    fmin, rmin = sb["front"], sb["rear"]
    if fmin + rmin <= 0:
        return slack / 2, slack / 2, side
    extra = slack - fmin - rmin
    if extra >= 0:
        return fmin + extra * fmin / (fmin + rmin), rmin + extra * rmin / (fmin + rmin), side
    scale = slack / (fmin + rmin)
    return fmin * scale, rmin * scale, side


def _plot_yards(facing: str, front: float, rear: float, side: float) -> dict:
    if facing == "south":
        d = {"top": rear, "bottom": front, "left": side, "right": side}
    elif facing == "east":
        d = {"top": side, "bottom": side, "left": rear, "right": front}
    elif facing == "west":
        d = {"top": side, "bottom": side, "left": front, "right": rear}
    else:
        d = {"top": front, "bottom": rear, "left": side, "right": side}
    d.update({"front": front, "rear": rear, "side": side})
    return d


# ---------------------------------------------------------------- allocation


def _raise(alloc: list[float], goal: list[float], rem: float) -> float:
    """Raise alloc toward goal, proportionally to each headroom, using up to
    `rem`. Returns what is left of rem."""
    if rem <= 0:
        return rem
    head = [max(g - a, 0.0) for a, g in zip(alloc, goal)]
    total = sum(head)
    if total <= 0:
        return rem
    if rem >= total:
        for i, h in enumerate(head):
            alloc[i] += h
        return rem - total
    for i, h in enumerate(head):
        alloc[i] += rem * h / total
    return 0.0


def _largest_index(entries, alloc, category):
    idx = [i for i, e in enumerate(entries) if e["category"] == category]
    return max(idx, key=lambda i: alloc[i]) if idx else None


def _apply_hierarchy(entries, alloc, maxs, floor_area: float, unit: str) -> float:
    """Applies HIERARCHY_RULES in place; returns area freed that could not be
    re-absorbed by another room (to be handled by the caller)."""
    freed = 0.0
    li = _largest_index(entries, alloc, _LIVING)
    di = _largest_index(entries, alloc, _DINING)
    ki = _largest_index(entries, alloc, _KITCHEN)
    ratio = rules.HIERARCHY_RULES["living_over_dining_min_ratio"]

    if di is not None:
        cap_waived = floor_area >= _sqft_to_unit(rules.HIERARCHY_RULES["dining_cap_waived_floor_sqft"], unit)
        if not cap_waived:
            cap = max(_sqft_to_unit(rules.HIERARCHY_RULES["dining_cap_sqft"], unit), entries[di]["min"])
            if alloc[di] > cap:
                freed += alloc[di] - cap
                alloc[di] = cap
    if li is not None and di is not None and alloc[li] < alloc[di] * ratio:
        total = alloc[li] + alloc[di]
        new_d = max(total / (1 + ratio), entries[di]["min"])
        new_l = min(total - new_d, maxs[li])
        freed += total - new_d - new_l
        alloc[di], alloc[li] = new_d, new_l
        if alloc[li] < alloc[di] * ratio:  # living capped: pull dining down further
            new_d = max(alloc[li] / ratio, entries[di]["min"])
            freed += alloc[di] - new_d
            alloc[di] = new_d
    if rules.HIERARCHY_RULES.get("kitchen_le_living") and li is not None and ki is not None and alloc[ki] > alloc[li]:
        new_k = max(alloc[li], entries[ki]["min"])
        freed += alloc[ki] - new_k
        alloc[ki] = new_k

    if freed > 0:
        # Re-absorb into rooms other than living/dining/kitchen, up to max.
        goal = [
            alloc[i] if entries[i]["category"] in (_LIVING, _DINING, _KITCHEN) else maxs[i]
            for i in range(len(entries))
        ]
        if li is not None:  # living may still grow to its max
            goal[li] = maxs[li]
        freed = _raise(alloc, goal, freed)
        # Re-check: living growth never violates the ratio direction.
    return freed


# Phase 4b step B: the staircase is a FIXED block - the run (room_specs
# staircase_dimensions: width x depth) plus a landing allowance at the top
# of the run - reserved once and identical on every floor. Nothing ever
# grows it (bounded fill, rebalance, rectify, steal all skip it), so it can
# never become the 158-572 sq ft sink it used to be.
STAIR_LANDING_ALLOWANCE_M = 0.3
# Kill switch for the fixed stair block (house_planner sizing + engine
# placement read the same flag).
STAIR_FIXED_BLOCK = False


def stair_block_size(unit: str) -> tuple[float, float]:
    """(width, depth) of the fixed staircase block in the plot unit."""
    w, d = staircase_dimensions(unit)
    return w, d + to_plot_unit(STAIR_LANDING_ALLOWANCE_M, unit)


def _entry(name: str, unit: str, garage_cars, tier: str = "standard") -> dict:
    mn = min_area_for_room(name, unit, garage_cars)
    pref = max(preferred_area_for_room(name, unit, garage_cars), mn)
    if tier == "standard":
        mx = max(max_area_for_room(name, unit, garage_cars), pref)
        tgt = min(max(target_area_for_room(name, unit, garage_cars), pref), mx)
    else:
        mx = max(rules.tier_max_area(name, unit, garage_cars, tier), pref)
        tgt = min(max(rules.tier_target_area(name, unit, garage_cars, tier), pref), mx)
    return {"name": name, "category": classify_room_category(name), "min": mn, "pref": pref, "target": tgt, "max": mx}


def _allocate_floor(
    rooms: list[dict], floor_number: int, total_floors: int, footprint_area: float, unit: str,
    garage_cars, add_bonus: bool, exclude_bonus: set | None = None, tier: str = "standard",
    porch_area: float = 0.0, stair_area: float = 0.0,
) -> FloorPlan:
    names = [str(r.get("name") or "") for r in rooms]
    entries = [_entry(n, unit, garage_cars, tier) for n in names]
    if stair_area > 0:
        # Phase 4b step B: the staircase is pinned to its fixed block.
        for e in entries:
            if e["category"] == "staircase":
                e["min"] = e["pref"] = e["target"] = e["max"] = stair_area
    circ = rules.circulation_fraction(_private_count(rooms))
    # The ground-floor Verandah (porch_area) is open area, not enclosed:
    # circulation and room allocation work on the enclosed part only.
    enclosed_area = footprint_area - porch_area
    circulation_area = enclosed_area * circ
    usable = enclosed_area - circulation_area

    alloc = [e["min"] for e in entries]
    rem = usable - sum(alloc)
    rem = _raise(alloc, [e["pref"] for e in entries], rem)
    rem = _raise(alloc, [e["target"] for e in entries], rem)

    added: list[dict] = []
    if add_bonus:
        for b in _eligible_bonus_rooms(rooms, floor_number, exclude_bonus, tier):
            e = _entry(b["name"], unit, garage_cars, tier)
            # Only when the spare area gives the bonus room its full TARGET
            # size - a bonus room squeezed in at its bare minimum is clutter
            # (a sliver closet/pantry), not a use of spare area.
            if rem >= e["target"]:
                entries.append(e)
                alloc.append(e["target"])
                rem -= e["target"]
                added.append({"name": b["name"], "planner_added": True, "added_reason": ADDED_REASON_SPARE})
        rem = _raise(alloc, [e["target"] for e in entries], rem)

    maxs = [e["max"] for e in entries]
    rem = _raise(alloc, maxs, rem)
    rem += _apply_hierarchy(entries, alloc, maxs, footprint_area, unit)

    room_max: dict[str, float] = {}
    remainder: list[str] = []
    if tier != "standard":
        # Tier maxima reach the engine (max_cells), the bounded fill, the
        # rebalance, the scorer's `oversized` check and the metrics/scorecard
        # through this override map. Standard tier: room_specs' own numbers.
        for e in entries:
            room_max[e["name"]] = e["max"]
    if stair_area > 0:
        for e in entries:
            if e["category"] == "staircase":
                room_max[e["name"]] = stair_area
    has_porch_room = any(e["category"] == "porch" for e in entries)
    if floor_number == 1 and porch_area > 0 and not has_porch_room:
        porch = rules.GROUND_FLOOR_EXCESS_FALLBACK
        size = porch_area + max(rem, 0.0)
        entries.append({"name": porch, "category": classify_room_category(porch), "min": size,
                        "pref": size, "target": size, "max": size})
        alloc.append(size)
        # Remainder sink, like the Terrace above it.
        room_max[porch] = footprint_area
        remainder.append(porch)
        added.append({"name": porch, "planner_added": True, "added_reason": ADDED_REASON_PORCH,
                      "open_area": True})
        rem = 0.0
    elif floor_number == 1 and porch_area > 0:
        rem += porch_area  # program already has its own porch; nothing reserved
    if floor_number > 1 and rem > 0:
        terrace = rules.UPPER_FLOOR_EXCESS_FALLBACK
        if terrace.lower() not in {n.lower() for n in names} and rem >= min_area_for_room(terrace, unit):
            entries.append({"name": terrace, "category": classify_room_category(terrace), "min": rem,
                            "pref": rem, "target": rem, "max": rem})
            alloc.append(rem)
            # The Terrace is the floor's remainder sink: its max is the whole
            # floor, so any packing residue lands here, never in a bedroom or
            # the staircase. (Reported as such by the scorecard.)
            room_max[terrace] = footprint_area
            remainder.append(terrace)
            terrace_dict = {"name": terrace, "planner_added": True, "added_reason": ADDED_REASON_TERRACE}
            if tier != "standard":
                terrace_dict["open_area"] = True
            added.append(terrace_dict)
            rem = 0.0

    targets = {}
    for e, a in zip(entries, alloc):
        targets[e["name"]] = a
    return FloorPlan(
        floor_number=floor_number,
        footprint_length=0.0,
        footprint_width=0.0,
        yards={},
        room_targets=targets,
        room_max=room_max,
        added_rooms=added,
        usable_area=usable,
        circulation_area=circulation_area,
        unallocated_area=max(rem, 0.0),
        remainder_rooms=remainder,
    )


# ---------------------------------------------------------------- entry point


def plan_building(
    floors_rooms: list[list[dict]],
    plot_dims: dict,
    facing: str | None = None,
    garage_cars: int | None = None,
    total_floors: int | None = None,
    min_front_setback: float | None = None,
    add_bonus: bool = True,
    exclude_bonus: dict | None = None,
    footprint_scale: float = 1.0,
) -> BuildingPlan:
    """See module docstring. `plot_dims` is the GROSS plot ({"length",
    "width","unit"}); yards are gross distances from the plot edge to the
    footprint (they already include the exterior wall band). `min_front_
    setback` (plot unit) folds in a user-requested front yard.
    `exclude_bonus` ({floor_number: {names}}) re-plans without bonus rooms
    the engine could not fit (see floor_layout_mask.layout_building), so the
    footprint shrinks instead of the spare area overflowing a room."""
    unit = plot_dims.get("unit") or "ft"
    length = float(plot_dims.get("length") or 1)
    width = float(plot_dims.get("width") or 1)
    facing = (facing or "north").strip().lower()
    if facing not in ("north", "south", "east", "west"):
        facing = "north"
    floors_rooms = floors_rooms or [[]]
    total_floors = total_floors or len(floors_rooms)

    exterior = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    net_area = max(length - 2 * exterior, 0.0) * max(width - 2 * exterior, 0.0)
    band = rules.plot_band(net_area, unit)
    tier = rules.size_tier(band.name)
    adjust = rules.floor_count_coverage_adjust(total_floors)

    depth_len, cross_len = (width, length) if facing in ("north", "south") else (length, width)

    bounds = [
        program_bounds(rooms, unit, garage_cars, i + 1, include_bonus=add_bonus,
                       exclude_bonus=(exclude_bonus or {}).get(i + 1), tier=tier)
        for i, rooms in enumerate(floors_rooms)
    ]
    program_min = max(b[0] for b in bounds)
    program_ceiling = max(bounds[0][1], program_min)

    sb = _setbacks(band, unit, garage_cars, min_front_setback, exterior)
    env_depth, env_cross = _envelope(depth_len, cross_len, sb)
    infeasible = False
    if program_min > env_depth * env_cross:
        sb = _setbacks(_XS_BAND, unit, garage_cars, min_front_setback, exterior)
        env_depth, env_cross = _envelope(depth_len, cross_len, sb)
        if program_min > env_depth * env_cross:
            sb = _setbacks(_XS_BAND, unit, None, None, exterior)
            env_depth, env_cross = _envelope(depth_len, cross_len, sb)
            infeasible = program_min > env_depth * env_cross
    envelope_area = env_depth * env_cross

    target = band.target_cov * adjust * net_area
    cap_total = built_area_cap(floors_rooms, band.name, unit)
    cap_fp = cap_total / total_floors if cap_total else None
    upper = min(program_ceiling * CEILING_PACKING_FRACTION, envelope_area)
    if cap_fp is not None:
        # The cap never pushes the house below what the program needs.
        upper = min(upper, max(cap_fp, program_min))
    area = min(max(target, program_min), upper)
    if footprint_scale != 1.0:
        # Step 5.2: a smaller footprint candidate (layout_building's scale
        # variants) - never below what the program needs.
        area = max(area * footprint_scale, program_min)
    porch_area = 0.0
    if tier in rules.PORCH_TIERS and target > area:
        # Ceiling-limited big plot: a ground-floor Verandah (open, covered)
        # takes part of the slack - at most PORCH_MAX_FRACTION of the
        # enclosed footprint and the tier's porch max, never past the
        # envelope or the built-area cap.
        porch_name = rules.GROUND_FLOOR_EXCESS_FALLBACK
        room_cap = rules.tier_max_area(porch_name, unit, None, tier)
        porch_area = min(target - area, room_cap, rules.PORCH_MAX_FRACTION * area, envelope_area - area)
        if cap_fp is not None:
            porch_area = min(porch_area, cap_fp - area)
        # A verandah squeezed below its PREFERRED size is clutter (measured:
        # a 69 sq ft one on the sparse 150x150 case pinched the bathroom).
        if porch_area < preferred_area_for_room(porch_name, unit):
            porch_area = 0.0
        area += porch_area
    fp_depth, fp_cross = _shape(area, env_depth, env_cross)
    shaped = fp_depth * fp_cross
    if porch_area > 0:
        porch_area = max(porch_area - max(area - shaped, 0.0), 0.0)
    area = shaped
    front, rear, side = _yards(depth_len, cross_len, fp_depth, fp_cross, sb)
    yards = _plot_yards(facing, front, rear, side)
    if facing in ("north", "south"):
        fp_length, fp_width = fp_cross, fp_depth
    else:
        fp_length, fp_width = fp_depth, fp_cross

    stair_area = 0.0
    if STAIR_FIXED_BLOCK and total_floors > 1 and any(
        classify_room_category(str(r.get("name") or "")) == "staircase" for rooms in floors_rooms for r in rooms
    ):
        sw, sd = stair_block_size(unit)
        stair_area = sw * sd
    plan = BuildingPlan(
        unit=unit, footprint_length=fp_length, footprint_width=fp_width, yards=yards, band=band.name,
        net_plot_area=net_area, footprint_area=area, program_min=program_min,
        program_ceiling=program_ceiling, envelope_area=envelope_area, infeasible=infeasible,
        tier=tier, porch_area=porch_area, built_area_cap=cap_total, total_floors=total_floors,
        stair_area=stair_area, footprint_scale=footprint_scale,
    )
    for i, rooms in enumerate(floors_rooms):
        fp = _allocate_floor(rooms, i + 1, total_floors, area, unit, garage_cars, add_bonus,
                             (exclude_bonus or {}).get(i + 1), tier,
                             porch_area if i == 0 else 0.0, stair_area)
        fp.footprint_length, fp.footprint_width, fp.yards = fp_length, fp_width, yards
        plan.floors.append(fp)
    return plan


# ---------------------------------------------------------------- persistence


def plan_to_dict(plan: BuildingPlan) -> dict:
    """JSON-safe snapshot persisted in room_layout["planner"], so every later
    consumer of the same room_layout (the AI Concept Layout conditioning
    image) lays rooms out inside the SAME footprint/yards/targets as the
    blueprint instead of re-planning on its own."""
    return {
        "footprint_length": plan.footprint_length,
        "footprint_width": plan.footprint_width,
        "yards": dict(plan.yards),
        "band": plan.band,
        "tier": plan.tier,
        "porch_area": plan.porch_area,
        "built_area_cap": plan.built_area_cap,
        "stair_block": dict(plan.stair_block) if plan.stair_block else None,
        "floors": [
            {
                "floor_number": f.floor_number,
                "room_targets": dict(f.room_targets),
                "room_max": dict(f.room_max),
                "added_rooms": [dict(a) for a in f.added_rooms],
                "remainder_rooms": list(f.remainder_rooms),
                "layout_path": f.layout_path,
            }
            for f in plan.floors
        ],
    }


def floor_plan_from_dict(data: dict | None, floor_number: int) -> FloorPlan | None:
    """Inverse of plan_to_dict for one floor; None when absent/malformed."""
    try:
        floor = next(f for f in data["floors"] if f["floor_number"] == floor_number)
        return FloorPlan(
            floor_number=floor_number,
            footprint_length=float(data["footprint_length"]),
            footprint_width=float(data["footprint_width"]),
            yards=dict(data["yards"]),
            room_targets=dict(floor.get("room_targets") or {}),
            room_max=dict(floor.get("room_max") or {}),
            added_rooms=[dict(a) for a in floor.get("added_rooms") or []],
            remainder_rooms=list(floor.get("remainder_rooms") or []),
            layout_path=str(floor.get("layout_path") or ""),
            stair_block=dict(data["stair_block"]) if data.get("stair_block") else None,
        )
    except (KeyError, TypeError, ValueError, StopIteration):
        return None
