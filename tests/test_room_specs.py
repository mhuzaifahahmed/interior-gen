from app.pipeline.room_specs import (
    classify_room_category,
    garage_dimensions,
    garage_min_area_sqm,
    max_area_for_room,
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


def test_garage_dimensions_fit_a_real_car_with_clearance():
    # 2026-09-19, real user report: the garage was "impractical" - too tight
    # for an actual car. A real single garage needs meaningfully more than a
    # car's own ~6ft width once door-opening/walk-around clearance is
    # included - not asserting an exact number (would overfit to today's
    # constant), just that it now comfortably clears a car's own width.
    width, depth = garage_dimensions(1, "ft")
    assert width > 9.0  # a car alone is ~6ft wide; real clearance needs more
    assert depth > 18.0


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


def test_master_bedroom_has_a_larger_minimum_than_a_regular_bedroom():
    # Real user request (2026-09-19): a master bedroom should actually feel
    # bigger than a standard bedroom, not identically sized.
    regular = min_area_for_room("Bedroom 2", "ft")
    master = min_area_for_room("Master Bedroom", "ft")
    assert master > regular


def test_master_bedroom_has_a_larger_maximum_than_a_regular_bedroom():
    regular = max_area_for_room("Bedroom 2", "ft")
    master = max_area_for_room("Master Bedroom", "ft")
    assert master > regular


def test_master_bedroom_still_classifies_as_a_plain_bedroom():
    # Deliberately NOT a new category - every other consumer (zoning, suite
    # pairing, feasibility, furniture dispatch) must keep treating master
    # and regular bedrooms identically; only the size numbers differ.
    assert classify_room_category("Master Bedroom") == "bedroom"


def test_utility_room_has_a_real_usable_minimum_size():
    # 2026-09-19: a real user report that the utility/laundry room was too
    # cramped (~40 sq ft) to actually fit a washer, dryer, sink, and storage.
    # Not asserting an exact number (that would overfit to today's constant)
    # - just that it's now comfortably above a small bathroom's own minimum,
    # a stable relative bar for "not cramped".
    assert min_area_for_room("Utility Room", "ft") > min_area_for_room("Bathroom", "ft")


def test_a_room_merely_containing_master_in_an_unrelated_context_is_unaffected():
    # "master" only triggers the bump when the room is ALSO classified as a
    # bedroom - a non-bedroom room happening to include the word shouldn't
    # get bedroom-shaped sizing at all.
    assert min_area_for_room("Master Suite Closet", "ft") == min_area_for_room("Closet", "ft")
