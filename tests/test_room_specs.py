from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    INTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    door_width_for_wall,
    garage_dimensions,
    garage_min_area_sqm,
    max_area_for_room,
    min_area_for_room,
    min_depth_for_room,
    min_width_for_room,
    preferred_area_for_room,
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
    # cramped to actually fit a washer, dryer, sink, and storage. 2026-10's
    # Measurements Model refinement re-grounded every room's minimum in
    # published residential standards (~35 sqft for a laundry/utility room,
    # a real, researched floor - see room_specs.py's size chart) rather than
    # a loose relative comparison to another room type, which is no longer a
    # meaningful "not cramped" bar now that every category has its own
    # independently-researched minimum.
    assert min_area_for_room("Utility Room", "ft") >= 30.0


def test_unrecognized_room_has_a_finite_max_area_not_unbounded():
    # Real, live-reproduced bug (2026-09-29): an unrecognized room name used
    # to have NO cap at all (math.inf), letting it absorb nearly all of
    # layout_floor()'s weight-based redistribution once every bounded room
    # nearby hit its own cap - ballooning to roughly the size of the whole
    # floor. A finite cap bounds that. "Hallway" is no longer the right
    # example room (2026-10's taxonomy refinement added "hallway" as a real,
    # recognized - and deliberately PINNED, like garage/staircase -
    # category, since a corridor's width is a real functional requirement,
    # not a preference) - use a genuinely unrecognized/invented name instead.
    import math

    unrecognized_max = max_area_for_room("Zxqvy Chamber", "ft")
    assert math.isfinite(unrecognized_max)
    # Still generous relative to its own minimum, not clamped to nothing.
    assert unrecognized_max > min_area_for_room("Zxqvy Chamber", "ft")


def test_a_room_merely_containing_master_in_an_unrelated_context_is_unaffected():
    # "master" only triggers the bump when the room is ALSO classified as a
    # bedroom - a non-bedroom room happening to include the word shouldn't
    # get bedroom-shaped sizing at all.
    assert min_area_for_room("Master Suite Closet", "ft") == min_area_for_room("Closet", "ft")


# ---- 2026-10 "Measurements Model" refinement: taxonomy/synonyms/walls/doors ----


def test_classify_room_category_resolves_regional_and_western_synonyms():
    # Common keyword-list matches (tier 1).
    assert classify_room_category("Dirty Kitchen") == "dirty_kitchen"
    assert classify_room_category("Powder Room") == "powder"
    assert classify_room_category("Half Bath") == "powder"
    assert classify_room_category("TV Lounge") == "family"
    assert classify_room_category("Home Theater") == "family"
    assert classify_room_category("Puja Room") == "prayer"
    assert classify_room_category("Walk-in Closet") == "closet"
    assert classify_room_category("Mudroom") == "laundry"
    # Regional synonym-dict matches (tier 2, phrases not in the primary list).
    assert classify_room_category("Sehan") == "courtyard"
    assert classify_room_category("Baithak") == "living"
    assert classify_room_category("Mumty") == "staircase"
    assert classify_room_category("Servant Quarters") == "bedroom"


def test_classify_room_category_never_raises_on_an_invented_name():
    # A genuinely invented room name must still resolve to SOME sensible
    # category (never an error, never "unsupported") - either a real
    # leisure category via a loose keyword match, or the generic fallback.
    assert classify_room_category("Cigar Lounge") in ("family", "living", "default")
    assert classify_room_category("Totally Invented Xyzzy Room") == "default"


def test_generic_fallback_room_has_a_finite_sane_size():
    area = min_area_for_room("Zorp Room", "ft")
    assert 0 < area < 100  # a real, bounded footprint, not zero or absurd
    assert max_area_for_room("Zorp Room", "ft") < 300


def test_door_width_bathroom_is_narrower_than_standard_and_front():
    bath_bed = door_width_for_wall("bathroom", "bedroom", "ft")
    standard = door_width_for_wall("bedroom", "living", "ft")
    closet = door_width_for_wall("closet", "bedroom", "ft")
    assert closet < bath_bed < standard


def test_door_width_for_wall_uses_the_smaller_of_the_two_rooms():
    # A door between two categories always uses the MORE private/smaller
    # room's own base width, never the larger one.
    assert door_width_for_wall("bathroom", "living", "ft") == door_width_for_wall("bathroom", "bedroom", "ft")


def test_exterior_wall_is_thicker_than_interior_partition():
    assert EXTERIOR_WALL_THICKNESS_M > INTERIOR_WALL_THICKNESS_M


def test_min_width_and_min_depth_are_real_positive_governing_dimensions():
    assert min_width_for_room("Foyer", "ft") > 0
    assert min_depth_for_room("Foyer", "ft") > 0
    # The foyer's real minimum depth directly addresses the "1-foot
    # entrance" bug - it must be a genuinely usable entry depth, not a
    # sliver.
    assert min_depth_for_room("Foyer", "ft") >= 4.0


def test_preferred_area_is_at_least_the_minimum_and_at_most_the_absolute_max():
    for name in ("Bedroom 2", "Living Room", "Kitchen", "Dining Room"):
        min_a = min_area_for_room(name, "ft")
        pref_a = preferred_area_for_room(name, "ft")
        max_a = max_area_for_room(name, "ft")
        assert min_a <= pref_a <= max_a


def test_living_room_has_a_real_absolute_ceiling_not_unbounded():
    # Direct regression for the real, reported "living room needs to be
    # shortened... how is it practical" / ~9,451 sqft ballooning bugs - the
    # cap is now a real, finite area regardless of multiplier math.
    assert max_area_for_room("Living Room", "ft") <= 410.0  # ~400 sqft + float slack


def test_family_leisure_category_has_a_tighter_cap_than_living():
    # The new "family" category (den/media/game/gym/bar...) intentionally
    # caps lower than "living" - it's a secondary leisure space, not the
    # dominant social room.
    assert max_area_for_room("Media Room", "ft") < max_area_for_room("Living Room", "ft")


