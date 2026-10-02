from app.pipeline.house_requirements import (
    DEFAULT_FRONT_YARD_DEPTH_M,
    extra_rooms_from_floor_text,
    mentions_front_yard,
    mentions_garage,
    mentions_utility,
    parse_extra_rooms_by_floor,
    parse_front_yard_depth,
    parse_garage_cars,
)
from app.pipeline.room_specs import to_plot_unit


def test_mentions_garage_true_when_present():
    assert mentions_garage("2 floors, 3 bedrooms. Extras: garage, modern style")


def test_mentions_garage_false_when_absent():
    assert not mentions_garage("2 floors, 3 bedrooms. Extras: modern style")
    assert not mentions_garage("")
    assert not mentions_garage(None)


def test_mentions_utility_true_for_utility_or_laundry():
    assert mentions_utility("2 floors, 3 bedrooms. Extras: utility room, modern style")
    assert mentions_utility("Extras: laundry room")
    assert mentions_utility("Extras: a laundry area near the kitchen")


def test_mentions_utility_false_when_absent():
    assert not mentions_utility("2 floors, 3 bedrooms. Extras: modern style")
    assert not mentions_utility("")
    assert not mentions_utility(None)


def test_parse_garage_cars_defaults_to_one():
    assert parse_garage_cars("Extras: garage") == 1


def test_parse_garage_cars_extracts_explicit_count():
    assert parse_garage_cars("Extras: 2 car garage") == 2
    assert parse_garage_cars("Extras: 3-car garage") == 3
    assert parse_garage_cars("Extras: garage for 4 cars") == 4


def test_mentions_front_yard_true_when_present():
    assert mentions_front_yard("Extras: front yard, garage")
    assert mentions_front_yard("Extras: front-yard")


def test_mentions_front_yard_false_when_absent():
    assert not mentions_front_yard("Extras: garage, modern style")


def test_parse_front_yard_depth_returns_none_when_not_mentioned():
    assert parse_front_yard_depth("Extras: garage", "ft") is None


def test_parse_front_yard_depth_uses_default_when_unspecified():
    result = parse_front_yard_depth("Extras: front yard", "m")
    assert abs(result - DEFAULT_FRONT_YARD_DEPTH_M) < 1e-6


def test_parse_front_yard_depth_converts_default_to_feet():
    result = parse_front_yard_depth("Extras: front yard", "ft")
    assert abs(result - to_plot_unit(DEFAULT_FRONT_YARD_DEPTH_M, "ft")) < 1e-6


def test_parse_front_yard_depth_extracts_explicit_value_in_feet():
    result = parse_front_yard_depth("Extras: 15ft front yard", "ft")
    assert abs(result - 15.0) < 1e-6


def test_parse_front_yard_depth_extracts_explicit_value_with_unit_word():
    result = parse_front_yard_depth("Extras: front yard 5 meters", "m")
    assert abs(result - 5.0) < 1e-6


# ---- parse_extra_rooms_by_floor() - real, confirmed user complaint: "you ----
# ---- are hardcoding everything... make it not hallucinate when I tell it ----
# ---- to add a kitchen on 2nd floor or a dining room on 3rd floor" (2026-09-30) ----


def test_parse_extra_rooms_by_floor_finds_kitchen_on_an_upper_floor():
    result = parse_extra_rooms_by_floor("Extras: kitchen on the 2nd floor")
    assert ("kitchen", "Kitchen", 2) in result


def test_parse_extra_rooms_by_floor_finds_dining_room_with_floor_n_phrasing():
    result = parse_extra_rooms_by_floor("Extras: add a dining room on floor 3")
    assert ("dining", "Dining Room", 3) in result


def test_parse_extra_rooms_by_floor_finds_living_room_synonym_lounge():
    result = parse_extra_rooms_by_floor("Extras: put a lounge on floor 2")
    assert ("living", "Living Room", 2) in result


def test_parse_extra_rooms_by_floor_finds_multiple_requests():
    result = parse_extra_rooms_by_floor("Extras: kitchen on floor 2, dining room on floor 3")
    assert ("kitchen", "Kitchen", 2) in result
    assert ("dining", "Dining Room", 3) in result


def test_parse_extra_rooms_by_floor_ignores_a_room_keyword_with_no_nearby_floor_reference():
    # A plain "kitchen" mention with no floor number anywhere nearby is left
    # to Gemini's own judgement, same as before this function existed - not
    # every room mention is a per-floor placement request.
    result = parse_extra_rooms_by_floor("Extras: modern kitchen, open layout")
    assert result == []


def test_parse_extra_rooms_by_floor_does_not_false_positive_on_floor_count_phrasing():
    # "2 floors. Floor 1: 2 bedrooms..." (the composed requirements text's
    # own standard phrasing) must never be mistaken for an extra-room
    # request - bedroom/bathroom are deliberately NOT in the eligible
    # category set (already fully owned by the per-floor count system).
    text = "2 floors. Floor 1: 2 bedrooms, 2 bathrooms. Floor 2: 2 bedrooms, 2 bathrooms."
    assert parse_extra_rooms_by_floor(text) == []


def test_parse_extra_rooms_by_floor_returns_empty_for_empty_text():
    assert parse_extra_rooms_by_floor("") == []
    assert parse_extra_rooms_by_floor(None) == []


def test_parse_extra_rooms_by_floor_deduplicates_and_sorts():
    text = "Extras: kitchen on floor 2, a kitchen on the 2nd floor too, study on floor 1"
    result = parse_extra_rooms_by_floor(text)
    assert result.count(("kitchen", "Kitchen", 2)) == 1
    assert result[0][2] <= result[-1][2]  # sorted by floor number


# ---- extra_rooms_from_floor_text() - Part 5 per-floor extras boxes ----


def test_extra_rooms_from_floor_text_maps_known_floor_box_text_to_tuples():
    # index 0 = floor 1, index 1 = floor 2, index 2 = floor 3 - no floor
    # number needs to appear IN the text itself, unlike parse_extra_rooms_by_floor.
    result = extra_rooms_from_floor_text(["kitchen and dining room", "", "dining room"])
    assert ("kitchen", "Kitchen", 1) in result
    assert ("dining", "Dining Room", 1) in result
    assert ("dining", "Dining Room", 3) in result
    assert not any(r[2] == 2 for r in result)  # the blank floor-2 box contributes nothing


def test_extra_rooms_from_floor_text_resolves_regional_and_leisure_categories():
    result = extra_rooms_from_floor_text(["dirty kitchen", "prayer room and a gym"])
    assert ("dirty_kitchen", "Dirty Kitchen", 1) in result
    assert ("prayer", "Prayer Room", 2) in result
    assert ("family", "Family Room", 2) in result


def test_extra_rooms_from_floor_text_handles_none_and_empty_list():
    assert extra_rooms_from_floor_text(None) == []
    assert extra_rooms_from_floor_text([]) == []
    assert extra_rooms_from_floor_text(["", "", ""]) == []


def test_extra_rooms_from_floor_text_ignores_bedroom_and_bathroom_mentions():
    # Bedroom/bathroom counts are already fully owned by the per-floor COUNT
    # system (floor_bedrooms/floor_bathrooms) - the extras box must not
    # double-handle them.
    result = extra_rooms_from_floor_text(["a cozy bedroom with ensuite bathroom"])
    assert result == []
