from app.pipeline.feasibility import check_feasibility, min_area_for_room_count, room_count_requirements_table


def test_check_feasibility_returns_feasible_for_a_generous_plot():
    rooms = [
        {"name": "Living Room", "area": 2},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 1.5},
        {"name": "Bathroom", "area": 0.5},
    ]
    result = check_feasibility(rooms, {"length": 60, "width": 60, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "feasible"
    assert result["explanation"] is None


def test_check_feasibility_returns_not_feasible_for_a_tiny_plot_with_many_rooms():
    rooms = [
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bedroom 3", "area": 1},
        {"name": "Bathroom 1", "area": 1},
        {"name": "Bathroom 2", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Living Room", "area": 1},
        {"name": "Garage", "area": 1},
    ]
    result = check_feasibility(rooms, {"length": 15, "width": 15, "unit": "ft"}, total_floors=2)
    assert result["verdict"] == "not_feasible"
    assert result["explanation"] is not None
    assert "sq ft" in result["explanation"]


def test_check_feasibility_returns_tight_near_the_boundary():
    rooms = [
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bathroom", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Living Room", "area": 1},
    ]
    # Derive required_area from a generously-sized plot first (still returned
    # even when feasible), then construct a plot whose area sits inside the
    # "tight" band (required_area, required_area / 0.85] - avoids hardcoding
    # a plot size that would silently go stale if the room-size constants
    # ever change.
    width = 30
    probe = check_feasibility(rooms, {"length": 1000, "width": width, "unit": "ft"}, total_floors=1)
    required_area = probe["required_area"]
    target_available = required_area * 1.05
    length = target_available / width

    result = check_feasibility(rooms, {"length": length, "width": width, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "tight"
    assert result["explanation"] is not None


def test_check_feasibility_accounts_for_garage_car_count():
    rooms = [{"name": "Garage", "area": 1}, {"name": "Living Room", "area": 1}]
    one_car = check_feasibility(rooms, {"length": 20, "width": 20, "unit": "ft"}, total_floors=1, garage_cars=1)
    three_car = check_feasibility(rooms, {"length": 20, "width": 20, "unit": "ft"}, total_floors=1, garage_cars=3)
    assert three_car["required_area"] > one_car["required_area"]


def test_check_feasibility_adds_staircase_overhead_for_multi_floor():
    rooms = [{"name": "Bedroom", "area": 1}]
    single_floor = check_feasibility(rooms, {"length": 30, "width": 30, "unit": "ft"}, total_floors=1)
    multi_floor = check_feasibility(rooms, {"length": 30, "width": 30, "unit": "ft"}, total_floors=2)
    assert multi_floor["required_area"] > single_floor["required_area"]


def test_check_feasibility_handles_no_rooms():
    result = check_feasibility([], {"length": 40, "width": 60, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "feasible"


# ---- min_area_for_room_count() / room_count_requirements_table() - real- ----
# ---- time "does N bedrooms fit my plot" guidance for the Build a House  ----
# ---- form (2026-09-30, explicit user request).                         ----


def test_min_area_for_room_count_increases_with_more_bedrooms():
    zero = min_area_for_room_count("bedroom", 0, 1, total_floors=1, unit="ft")
    five = min_area_for_room_count("bedroom", 5, 1, total_floors=1, unit="ft")
    assert five > zero


def test_min_area_for_room_count_increases_with_more_bathrooms():
    zero = min_area_for_room_count("bathroom", 0, 1, total_floors=1, unit="ft")
    five = min_area_for_room_count("bathroom", 5, 1, total_floors=1, unit="ft")
    assert five > zero


def test_min_area_for_room_count_includes_staircase_overhead_for_multi_floor():
    single = min_area_for_room_count("bedroom", 2, 1, total_floors=1, unit="ft")
    multi = min_area_for_room_count("bedroom", 2, 1, total_floors=2, unit="ft")
    assert multi > single


def test_min_area_for_room_count_agrees_with_check_feasibility():
    # The real guarantee: this must never disagree with the actual hard
    # gate about what "fits" means - both use the identical underlying
    # math, so a plot sized exactly at min_area_for_room_count()'s own
    # number must come back "feasible" or "tight", never "not_feasible".
    required = min_area_for_room_count("bedroom", 3, 1, total_floors=2, unit="ft")
    side = required**0.5
    rooms = [
        {"name": "Living Room", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Dining Room", "area": 1},
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bedroom 3", "area": 1},
        {"name": "Bathroom 1", "area": 1},
        {"name": "Staircase", "area": 1},
    ]
    result = check_feasibility(rooms, {"length": side, "width": side, "unit": "ft"}, total_floors=2)
    assert result["verdict"] in ("feasible", "tight")


def test_room_count_requirements_table_covers_the_full_range():
    table = room_count_requirements_table(total_floors=1, unit="ft")
    assert [row["count"] for row in table["bedrooms"]] == list(range(16))
    assert [row["count"] for row in table["bathrooms"]] == list(range(11))
    for row in table["bedrooms"] + table["bathrooms"]:
        assert row["min_area"] > 0
        assert row["min_side"] > 0


def test_room_count_requirements_table_min_side_is_sqrt_of_min_area():
    table = room_count_requirements_table(total_floors=1, unit="ft")
    row = table["bedrooms"][5]
    assert abs(row["min_side"] - row["min_area"] ** 0.5) < 0.1
