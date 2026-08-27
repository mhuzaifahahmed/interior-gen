from app.pipeline.house_requirements import (
    DEFAULT_FRONT_YARD_DEPTH_M,
    mentions_front_yard,
    mentions_garage,
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
