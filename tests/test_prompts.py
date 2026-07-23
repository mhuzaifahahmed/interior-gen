from app.pipeline.prompts import (
    PRESERVE_STRUCTURE,
    STRENGTH_BY_TIER,
    TIER_SPECS,
    UNIVERSAL_DAMAGE_NEGATIVE,
    build_negative_prompt,
    build_prompt,
    get_strength,
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


def test_negative_prompt_always_excludes_damage_regardless_of_tier():
    for tier in TIER_SPECS:
        assert UNIVERSAL_DAMAGE_NEGATIVE in build_negative_prompt(tier)


def test_build_prompt_includes_tier_note_when_given():
    prompt = build_prompt("economical", tier_note="repaint over visible water stains")
    assert "repaint over visible water stains" in prompt


def test_build_prompt_omits_tier_note_when_not_given():
    prompt = build_prompt("economical")
    # sanity: no stray "None" or empty-note artifact leaks into the prompt
    assert "None" not in prompt


def test_all_tiers_have_a_strength_value():
    assert set(STRENGTH_BY_TIER.keys()) == {"economical", "mid", "premium"}
    for tier in STRENGTH_BY_TIER:
        assert 0.0 < get_strength(tier) <= 1.0


def test_get_strength_unknown_tier_raises():
    try:
        get_strength("luxury-plus")
    except ValueError:
        return
    assert False, "expected ValueError for unknown tier"


def test_premium_strength_is_lower_than_economical_despite_bigger_material_change():
    # Regression guard for a real observed failure: economical and premium once
    # shared the same (higher) strength, and premium hallucinated an entirely
    # different room (no window, wrong shape) while economical preserved structure
    # fine at that same value. Premium's luxury vocabulary needs LESS freedom, not
    # more, to stay anchored to the input - don't raise this back up without
    # re-verifying against a real generation first.
    assert get_strength("premium") < get_strength("economical")


def test_only_premium_has_a_structure_reminder():
    assert TIER_SPECS["economical"]["structure_reminder"] == ""
    assert TIER_SPECS["mid"]["structure_reminder"] == ""
    assert TIER_SPECS["premium"]["structure_reminder"] != ""


def test_build_prompt_includes_premium_structure_reminder():
    prompt = build_prompt("premium")
    assert TIER_SPECS["premium"]["structure_reminder"] in prompt


def test_build_prompt_omits_structure_reminder_for_tiers_without_one():
    for tier in ("economical", "mid"):
        prompt = build_prompt(tier)
        # the double comma that would result from appending an empty token
        assert ", , " not in prompt
