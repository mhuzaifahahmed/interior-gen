from app.pipeline.room_specs import (
    classify_room_category,
    garage_min_area_sqm,
    min_area_for_room,
    to_plot_unit,
)


def test_classify_room_category_recognizes_common_room_types():
    assert classify_room_category("Master Bedroom") == "bedroom"
    assert classify_room_category("Guest Bathroom") == "bathroom"
    assert classify_room_category("Kitchen") == "kitchen"
    assert classify_room_category("Family Lounge") == "living"
    assert classify_room_category("2-Car Garage") == "garage"


def test_classify_room_category_defaults_for_unrecognized_names():
    assert classify_room_category("Zorp Room") == "default"
    assert classify_room_category("") == "default"


def test_to_plot_unit_converts_meters_to_feet():
    feet = to_plot_unit(1.0, "ft")
    assert abs(feet - 3.280839895) < 1e-6


def test_to_plot_unit_leaves_meters_unchanged():
    assert to_plot_unit(2.5, "m") == 2.5


def test_min_area_for_room_is_positive_for_every_category():
    for name in ["Bedroom", "Bathroom", "Kitchen", "Living Room", "Garage", "Something Unrecognized"]:
        assert min_area_for_room(name, "ft") > 0


def test_garage_min_area_scales_with_car_count():
    one_car = garage_min_area_sqm(1, "ft")
    two_car = garage_min_area_sqm(2, "ft")
    assert two_car > one_car
    # Width scales linearly per car, depth stays fixed - so it should be
    # roughly double, not some arbitrary multiplier.
    assert abs(two_car / one_car - 2) < 0.05


def test_min_area_for_room_uses_garage_cars_when_given():
    default_cars = min_area_for_room("Garage", "ft")
    three_cars = min_area_for_room("Garage", "ft", cars=3)
    assert three_cars > default_cars
