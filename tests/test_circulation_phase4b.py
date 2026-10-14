"""Phase 4b: circulation door rules, reachability metric, scorer opt-in,
renderer parity and the planner-engine circulation repairs."""
import pytest

from app.config import settings
from app.pipeline import door_graph as dg
from app.pipeline import floor_layout_mask as M
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.layout_quality import score_floor_layout

DIMS = {"length": 40.0, "width": 30.0, "unit": "ft"}


def R(name, x, y, w, h, **extra):
    return {"name": name, "x": x, "y": y, "w": w, "h": h, **extra}


def _doors(rects, facing="north", floor=1):
    g = dg.build_door_graph(rects, DIMS, facing, floor)
    return {frozenset((a["name"], b["name"])) for a, b, _e in g.interior}, g


# ---------------------------------------------------------------- door rules


@pytest.mark.parametrize("public", ["Living Room", "Dining Room", "Kitchen", "Media Room"])
def test_common_bathroom_never_opens_into_a_public_room(public):
    rects = [R(public, 0, 0, 20, 30), R("Bathroom 2", 20, 0, 8, 10), R("Bedroom 2", 20, 10, 20, 20)]
    doors, _g = _doors(rects)
    assert frozenset((public, "Bathroom 2")) not in doors
    assert frozenset(("Bathroom 2", "Bedroom 2")) in doors  # no hallway: other doors unchanged


def test_powder_room_never_opens_into_living_but_may_open_to_foyer():
    rects = [R("Living Room", 0, 0, 20, 30), R("Powder Room", 20, 0, 6, 8), R("Entry", 20, 8, 6, 22)]
    doors, _g = _doors(rects)
    assert frozenset(("Living Room", "Powder Room")) not in doors
    assert frozenset(("Powder Room", "Entry")) in doors


def test_front_door_is_never_the_garage():
    # Only the garage touches the north (front) edge -> no front door at all.
    rects = [R("Garage", 0, 0, 40, 12), R("Entry", 0, 12, 10, 18), R("Living Room", 10, 12, 30, 18)]
    room, edge = dg.front_door_room(rects, "north", (0, 0, 40, 30))
    assert room is None and edge is None
    # A non-garage room on the edge takes it even though the garage is wider.
    rects = [R("Garage", 0, 0, 30, 12), R("Living Room", 30, 0, 10, 30), R("Entry", 0, 12, 30, 18)]
    room, _edge = dg.front_door_room(rects, "north", (0, 0, 40, 30))
    assert room["name"] == "Living Room"


def test_front_door_prefers_any_non_private_room_over_a_bathroom():
    rects = [R("Bathroom 1", 0, 0, 30, 10), R("Hallway", 30, 0, 10, 30), R("Bedroom 1", 0, 10, 30, 20)]
    room, _edge = dg.front_door_room(rects, "north", (0, 0, 40, 30))
    assert room["name"] == "Hallway"


def test_orphaned_ensuite_opens_onto_the_hallway():
    # Bathroom 1 is tagged to Bedroom 1's suite but shares no wall with it.
    rects = [
        R("Bedroom 1", 0, 0, 15, 30, suite=0), R("Hallway", 15, 0, 5, 30),
        R("Bathroom 1", 20, 0, 8, 10, suite=0), R("Bedroom 2", 20, 10, 20, 20, suite=1),
    ]
    assert dg.orphaned_ensuites(rects) == frozenset({id(rects[2])})
    doors, _g = _doors(rects)
    assert frozenset(("Bathroom 1", "Hallway")) in doors
    # A real ensuite (touching its bedroom) still never opens onto the hallway.
    rects[2] = R("Bathroom 1", 20, 0, 8, 10, suite=1)
    doors, _g = _doors(rects)
    assert frozenset(("Bathroom 1", "Hallway")) not in doors
    assert frozenset(("Bathroom 1", "Bedroom 2")) in doors


def test_dxf_now_applies_garage_door_suppression(monkeypatch):
    from app.pipeline import blueprint_dxf

    seen = {}
    real = blueprint_dxf.build_door_graph

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(blueprint_dxf, "build_door_graph", spy)
    rects = [R("Garage", 0, 0, 20, 30), R("Living Room", 20, 0, 20, 20), R("Entry", 20, 20, 20, 10)]
    blueprint_dxf.render_floor_blueprint_dxf(1, rects, DIMS, "south")
    assert "suppress_garage_doors" not in seen  # the default (True) applies
    g = real(rects, DIMS, "south", 1)
    assert all(not ({a["name"], b["name"]} == {"Garage", "Living Room"}) for a, b, _e in g.interior)


def test_kaggle_door_specs_use_the_parts_aware_edge(monkeypatch):
    from app.providers import kaggle_autocad

    seen = {}
    real = kaggle_autocad.build_door_graph

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(kaggle_autocad, "build_door_graph", spy)
    # An L-shaped room whose bbox touches B but whose parts only touch it on
    # a short stub: the parts-aware edge differs from the bbox edge.
    L = R("Living Room", 0, 0, 20, 30, parts=[{"x": 0, "y": 0, "w": 20, "h": 10}, {"x": 0, "y": 10, "w": 8, "h": 20}])
    B = R("Kitchen", 20, 0, 20, 30)
    kaggle_autocad._door_specs_for_floor([L, B, R("Dining Room", 8, 10, 12, 20)], DIMS, "north", 1)
    assert "parts_aware" not in seen
    assert dg.shared_edge_between_rooms(L, B) != dg.shared_edge(L, B)


# ---------------------------------------------------------------- reachability


def test_reachability_ensuite_through_own_bedroom_is_fine():
    rects = [
        R("Living Room", 0, 0, 40, 10), R("Hallway", 0, 10, 5, 20),
        R("Bedroom 1", 5, 10, 20, 20, suite=0), R("Bathroom 1", 25, 10, 15, 20, suite=0),
    ]
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert rep["root"] == "Living Room"
    assert rep["unreachable"] == [] and rep["pass_through_private"] == []


def test_reachability_bedroom_through_bedroom_is_pass_through():
    rects = [R("Living Room", 0, 0, 40, 10), R("Bedroom 1", 0, 10, 20, 20), R("Bedroom 2", 20, 10, 20, 20)]
    rects[0] = R("Living Room", 0, 0, 20, 10)
    rects.append(R("Bathroom 1", 20, 0, 20, 10))  # bath also blocks the front of Bedroom 2
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert "Bedroom 2" in rep["pass_through_private"]


def test_reachability_unreachable_room_and_porch_excluded():
    rects = [
        R("Living Room", 0, 0, 20, 30), R("Bathroom 1", 20, 0, 20, 15),  # bath<->living suppressed
        R("Verandah", 20, 15, 20, 15),
    ]
    # Verandah is reachable from living; bath borders only living (suppressed) and verandah.
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert "Verandah" not in rep["unreachable"] + rep["pass_through_private"]
    rects = [R("Living Room", 0, 0, 20, 30), R("Bathroom 1", 20, 0, 20, 30)]
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert rep["unreachable"] == ["Bathroom 1"]


def test_reachability_upper_floor_starts_at_the_staircase():
    rects = [
        R("Staircase", 0, 0, 5, 10), R("Hallway", 5, 0, 5, 30),
        R("Bedroom 3", 10, 0, 30, 15), R("Bedroom 4", 10, 15, 30, 15), R("Sitting Area", 0, 10, 5, 20),
    ]
    rep = dg.circulation_report(rects, DIMS, "north", 2)
    assert rep["root"] == "Staircase" and rep["root_how"] == "staircase"
    assert rep["unreachable"] == [] and rep["pass_through_private"] == []
    assert rep["entry_missing"] is False  # floor 2 has no front door by design


def test_entry_missing_when_only_the_garage_is_on_the_facing_edge():
    rects = [R("Garage", 0, 0, 40, 12), R("Entry", 0, 12, 10, 18), R("Living Room", 10, 12, 30, 18)]
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert rep["entry_missing"] is True and rep["entry_is_garage"] is False


def test_dead_end_hall_length():
    # The hall's right 20 ft only borders an ensuite (its hall door is
    # suppressed), so it serves no door: a 20 ft dead end.
    rects = [
        R("Living Room", 0, 0, 10, 30), R("Hallway", 10, 0, 30, 4),
        R("Bedroom 1", 10, 4, 10, 26, suite=0), R("Bathroom 1", 20, 4, 20, 26, suite=0),
    ]
    rep = dg.circulation_report(rects, DIMS, "north", 1)
    assert rep["dead_end_hall_len"] == pytest.approx(20.0)
    v = score_floor_layout(rects, DIMS, planner_checks=True, facing="north", floor_number=1).violations
    assert any(x.kind == "dead_end_hall" and not x.hard for x in v)


# ---------------------------------------------------------------- scorer / metrics opt-in


def test_scorer_circulation_is_opt_in():
    rects = [R("Living Room", 0, 0, 20, 30), R("Bathroom 1", 20, 0, 20, 30)]
    kinds = lambda sc: {v.kind for v in sc.violations}
    plain = score_floor_layout(rects, DIMS)
    no_facing = score_floor_layout(rects, DIMS, planner_checks=True)
    assert "unreachable" not in kinds(plain) | kinds(no_facing)
    on = score_floor_layout(rects, DIMS, planner_checks=True, facing="north", floor_number=1)
    assert any(v.kind == "unreachable" and v.hard and v.room == "Bathroom 1" for v in on.violations)
    # Without planner_checks the score is identical whatever `facing` says.
    assert score_floor_layout(rects, DIMS, facing="north").score == plain.score


def test_measure_floor_circulation_fields_need_facing():
    rects = [R("Living Room", 0, 0, 20, 30), R("Bathroom 1", 20, 0, 20, 30)]
    m0 = measure_floor(rects, DIMS)
    assert m0["unreachable"] is None and m0["entry_missing"] is None
    m1 = measure_floor(rects, DIMS, facing="north")
    assert m1["unreachable"] == ["Bathroom 1"] and m1["entry_is_garage"] == [] and m1["entry_missing"] == []
    assert {k: v for k, v in m0.items() if v is not None and k != "circulation_root"} == {
        k: v for k, v in m1.items() if k in m0 and m0[k] is not None
    }


# ---------------------------------------------------------------- engine (planner path)


def test_carve_access_arm_connects_a_room_to_the_spine():
    # 10x6 grid: spine column 4; Bedroom B (cols 0-1) sealed behind Bedroom A (cols 2-3).
    cols, rows = 10, 6
    owner = [[-1] * cols for _ in range(rows)]
    cells = [set(), set(), set()]
    for r in range(rows):
        for c in range(cols):
            i = -2 if c == 4 else (1 if c < 2 else 0 if c < 4 else 2)
            owner[r][c] = i
            if i >= 0:
                cells[i].add((c, r))
    targets = [
        {"name": "Bedroom A", "category": "bedroom", "suite": None, "min_area": 1.0},
        {"name": "Bedroom B", "category": "bedroom", "suite": None, "min_area": 1.0},
        {"name": "Study", "category": "study", "suite": None, "min_area": 1.0},
    ]
    stats = {}
    M._carve_access_arms(owner, cells, targets, ("Bedroom B",), [1, 1, 1], [2, 2, 2], 0, cols, rows,
                         1.0, 1.0, "m", stats)
    assert stats["arms"] == ["Bedroom B"] and stats["arm_cells"] > 0
    # Every cell still has exactly one owner (room or spine): exact tiling.
    assert all(owner[r][c] != -1 for r in range(rows) for c in range(cols))
    arm = [(c, r) for r in range(rows) for c in (2, 3) if owner[r][c] == -2]
    assert arm and all(owner[r][1] == 1 for _c, r in arm)


def test_planner_matrix_floors_have_no_garage_entrance_and_no_unreachable_rooms(monkeypatch):
    from tests.mask_quality_matrix import CASES

    monkeypatch.setattr(settings, "house_planner_enabled", True)
    for cid in ("60x80_dense_garage_1f", "150x200_2f_reported", "60x80_3f_south"):
        case = next(c for c in CASES if c["id"] == cid)
        _plan, results = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"],
                                           len(case["floors"]))
        for n, (_u, rects, _w) in enumerate(results, start=1):
            rep = dg.circulation_report(rects, case["plot"], case["facing"], n)
            assert rep["entry_is_garage"] is False and rep["entry_missing"] is False, cid
            assert rep["unreachable"] == [], (cid, n, rep)


# ---------------------------------------------------------------- step B: fixed stair block
# house_planner.STAIR_FIXED_BLOCK is OFF by default (see the Phase 4b report:
# turning it on regresses the planner quality gate on 7-12 floors). These
# tests pin the mechanism itself with the switch forced on.


def _stairs(results):
    out = []
    for _u, rects, _w in results:
        s = [r for r in rects if "stair" in r["name"].lower()]
        out.append(s[0] if s else None)
    return out


def test_fixed_stair_block_is_identical_on_every_floor_and_never_grows(monkeypatch):
    from app.pipeline import house_planner as HP
    from app.pipeline.layout_quality import stair_over_cap
    from tests.mask_quality_matrix import CASES

    monkeypatch.setattr(settings, "house_planner_enabled", True)
    monkeypatch.setattr(HP, "STAIR_FIXED_BLOCK", True)
    for cid in ("50x50_2f", "150x200_2f_reported", "60x80_3f_south"):
        case = next(c for c in CASES if c["id"] == cid)
        plan, results = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"],
                                           len(case["floors"]))
        assert plan.stair_block is not None
        stairs = _stairs(results)
        assert all(s is not None and "parts" not in s for s in stairs), cid
        boxes = {(round(s["x"], 6), round(s["y"], 6), round(s["w"], 6), round(s["h"], 6)) for s in stairs}
        assert len(boxes) == 1, (cid, boxes)  # same x/y/w/h on every floor
        assert all(abs(s["w"] * s["h"] - plan.stair_block["w"] * plan.stair_block["h"]) < 1e-6 for s in stairs)
        assert not any(stair_over_cap(s, "ft") for s in stairs), cid  # <= 2.0x its minimum
        for _u, rects, _w in results:  # still tiles exactly
            m = measure_floor(rects, case["plot"])
            assert m["coverage_gap_fraction"] <= 0.01 and m["overlap_area"] <= 1e-3


def test_single_floor_house_reserves_no_stair_block(monkeypatch):
    from app.pipeline import house_planner as HP
    from tests.mask_quality_matrix import CASES

    monkeypatch.setattr(settings, "house_planner_enabled", True)
    monkeypatch.setattr(HP, "STAIR_FIXED_BLOCK", True)
    case = next(c for c in CASES if c["id"] == "40x60_typical_1f_south")
    plan, _r = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], 1)
    assert plan.stair_block is None and plan.stair_area == 0.0


def test_verandah_excluded_from_circulation_failures_but_tiles(monkeypatch):
    from tests.mask_quality_matrix import CASES

    monkeypatch.setattr(settings, "house_planner_enabled", True)
    case = next(c for c in CASES if c["id"] == "150x150_dense_garage_2f")
    _plan, results = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], 2)
    rects = results[0][1]
    assert any(r["name"] == "Verandah" for r in rects)
    m = measure_floor(rects, case["plot"], facing=case["facing"], floor_number=1, total_floors=2)
    assert "Verandah" not in m["unreachable"] + m["pass_through_private"]
    assert m["coverage_gap_fraction"] <= 0.01 and m["overlap_area"] <= 1e-3
