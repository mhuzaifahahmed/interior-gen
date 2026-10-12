"""Shared quality-gate matrix for the mask engine (Phase 0). Each case:
id, plot, floors (list of room lists), garage_cars, facing."""


def _r(*rooms):
    return [{"name": n, "area": a} for n, a in rooms]


def _typical(garage=False):
    rooms = [("Entry", 1), ("Living Room", 3), ("Dining Room", 2), ("Kitchen", 2),
             ("Master Bedroom", 2), ("Master Bathroom", 1), ("Bedroom 2", 1.5),
             ("Bathroom 2", 1), ("Bedroom 3", 1.5)]
    if garage:
        rooms.insert(0, ("Garage", 1.5))
    return _r(*rooms)


STAIR = ("Staircase", 1)
SPARSE = _r(("Bedroom 1", 1), ("Bedroom 2", 1), ("Bathroom 1", 1))
SPARSE2 = _r(("Living Room", 2), ("Kitchen", 1), ("Bedroom 1", 1))
SMALL = _r(("Entry", 1), ("Living Room", 3), ("Kitchen", 1.5), ("Dining Room", 1.5),
           ("Bedroom 1", 2), ("Bedroom 2", 1), ("Bathroom 1", 1))
DENSE = _r(("Entry", 1), ("Garage", 1.5), ("Living Room", 3), ("Dining Room", 2), ("Kitchen", 2),
           ("Laundry", 1), ("Master Bedroom", 2), ("Master Bathroom", 1), ("Bedroom 2", 1.5),
           ("Bathroom 2", 1), ("Bedroom 3", 1.5), ("Bathroom 3", 1), ("Bedroom 4", 1.5),
           ("Study", 1))
DENSE_2F = DENSE + _r(STAIR)
GROUND_2F = _r(("Entry", 1), ("Living Room", 3), ("Dining Room", 2), ("Kitchen", 2),
               ("Bedroom 1", 1.5), ("Bathroom 1", 1), STAIR)
UPPER_2F = _r(("Master Bedroom", 2), ("Master Bathroom", 1), ("Bedroom 2", 1.5),
              ("Bathroom 2", 1), ("Sitting Area", 2), STAIR)
TOP_3F = _r(("Bedroom 4", 1.5), ("Bathroom 4", 1), ("Sitting Area", 2), STAIR)
GROUND_150x200 = _r(("Entry", 1), ("Garage", 1.5), ("Living Room", 3), ("Dining Room", 2),
                    ("Kitchen", 2), ("Master Bedroom", 2), ("Master Bathroom", 1),
                    ("Bedroom 1", 1.5), ("Bathroom 1", 1), STAIR)
UPPER_150x200 = _r(("Bedroom 2", 1.5), ("Bathroom 2", 1), ("Family Lounge", 2), STAIR)


def _case(case_id, length, width, floors, garage=None, facing="south"):
    return {
        "id": case_id,
        "plot": {"length": float(length), "width": float(width), "unit": "ft"},
        "floors": floors,
        "garage_cars": garage,
        "facing": facing,
    }


CASES = [
    _case("30x40_small_1f", 30, 40, [SMALL]),
    _case("30x40_sparse_1f", 30, 40, [SPARSE]),
    _case("40x60_typical_1f_south", 40, 60, [_typical()]),
    _case("40x60_typical_garage_1f", 40, 60, [_typical(True)], garage=1),
    _case("50x50_typical_1f", 50, 50, [_typical()]),
    _case("50x50_2f", 50, 50, [GROUND_2F, UPPER_2F]),
    _case("56x56_typical_1f", 56, 56, [_typical()]),
    _case("60x80_typical_1f", 60, 80, [_typical()]),
    _case("60x80_dense_garage_1f", 60, 80, [DENSE], garage=2),
    _case("60x80_3f_south", 60, 80, [GROUND_2F, UPPER_2F, TOP_3F]),
    _case("90x40_typical_1f", 90, 40, [_typical()]),
    _case("90x40_sparse_1f", 90, 40, [SPARSE2]),
    _case("150x150_typical_1f", 150, 150, [_typical()]),
    _case("150x150_sparse_1f", 150, 150, [SPARSE]),
    _case("150x150_dense_garage_2f", 150, 150, [DENSE_2F, UPPER_2F], garage=1),
    _case("150x200_2f_reported", 150, 200, [GROUND_150x200, UPPER_150x200], garage=1),
    _case("300x300_typical_1f", 300, 300, [_typical()]),
    _case("300x300_sparse_1f", 300, 300, [SPARSE]),
]
# All 4 facings on two representative cases (south variants are above).
for _f in ("north", "east", "west"):
    CASES.append(_case(f"40x60_typical_1f_{_f}", 40, 60, [_typical()], facing=_f))
    CASES.append(_case(f"60x80_3f_{_f}", 60, 80, [GROUND_2F, UPPER_2F, TOP_3F], facing=_f))


def iter_floors():
    """Yield (case, floor_number, rooms, key) for every floor layout."""
    for case in CASES:
        for i, rooms in enumerate(case["floors"], start=1):
            yield case, i, rooms, f"{case['id']}/floor{i}"
