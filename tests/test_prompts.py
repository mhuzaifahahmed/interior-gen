from app.pipeline.prompts import (
    PRESERVE_STRUCTURE,
    TIER_SPECS,
    build_negative_prompt,
    build_prompt,
)


def test_all_tiers_present():
    assert set(TIER_SPECS.keys()) == {"economical", "mid", "premium"}


def test_tier_specs_are_mutually_distinct():
    seen = set()
    for tier, spec in TIER_SPECS.items():
        fingerprint = tuple(spec.values())
        assert fingerprint not in seen, f"tier {tier} duplicates another tier's spec"
        seen.add(fingerprint)


def test_build_prompt_contains_preserve_structure():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert PRESERVE_STRUCTURE in prompt


def test_build_prompt_includes_room_description_when_given():
    prompt = build_prompt("premium", room_description="A rectangular room with two windows.")
    assert "A rectangular room with two windows." in prompt


def test_build_prompt_unknown_tier_raises():
    try:
        build_prompt("luxury-plus")
    except ValueError:
        return
    assert False, "expected ValueError for unknown tier"


def test_budget_and_mid_exclude_chandelier_via_negative_prompt():
    assert "chandelier" in build_negative_prompt("economical")
    assert "chandelier" in build_negative_prompt("mid")


def test_premium_does_not_exclude_chandelier():
    assert "chandelier" not in build_negative_prompt("premium")


def test_positive_prompt_never_mentions_chandelier():
    # Regression guard: chandelier exclusion must live in the negative prompt only.
    # Diffusion models don't reliably obey "no chandelier" stated in the positive
    # prompt, so it must never be (re-)added there for budget/mid.
    for tier in ("economical", "mid"):
        assert "chandelier" not in build_prompt(tier)


def test_color_is_frontloaded_in_paint_field_not_only_palette():
    # Regression guard for the "everything looks the same color" bug: the tier's
    # distinguishing color must appear in `paint` (survives 77-token truncation),
    # not only in the separate, truncatable `palette` field.
    assert "sage" in TIER_SPECS["economical"]["paint"].lower()
    assert "beige" in TIER_SPECS["mid"]["paint"].lower()
    assert "marble" in TIER_SPECS["premium"]["paint"].lower()
