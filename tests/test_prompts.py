from app.pipeline.prompts import (
    PRESERVE_STRUCTURE,
    STRENGTH_BY_TIER,
    TIER_SPECS,
    UNIVERSAL_DAMAGE_NEGATIVE,
    USER_NOTES_MAX_CHARS,
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


def test_positive_prompt_states_chandelier_exclusion_for_budget_and_mid():
    # v6 inversion: instruction-following models (unlike SD1.5/diffusion) are built
    # to follow exclusions stated directly in the prompt, so the tier exclusion is
    # now ALSO a positive-prompt "Do not include: ..." sentence (on top of, not
    # instead of, the negative_prompt channel below, which Cloudflare/SD1.5 still
    # uses). Premium has no exclusions, so it should still never mention chandelier.
    for tier in ("economical", "mid"):
        prompt = build_prompt(tier)
        assert "chandelier" in prompt
        assert "Do not include" in prompt
    assert "chandelier" not in build_prompt("premium")


def test_tier_paint_field_names_the_distinguishing_color():
    # Sanity check that each tier's `paint` field actually names its distinguishing
    # color/material - this field is the first thing build_prompt() states about the
    # tier's look (right after the structural lock), so it anchors the tier's
    # identity regardless of prompt format.
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
    # re-verifying against a real generation first. Only meaningful for the
    # Cloudflare/SD1.5 fallback path (OpenAI ignores strength entirely).
    assert get_strength("premium") < get_strength("economical")


def test_only_premium_has_a_structure_reminder_in_the_spec():
    # This field still exists in TIER_SPECS (kept for reference/possible future
    # use) but v6's build_prompt() no longer reads it - see test below.
    assert TIER_SPECS["economical"]["structure_reminder"] == ""
    assert TIER_SPECS["mid"]["structure_reminder"] == ""
    assert TIER_SPECS["premium"]["structure_reminder"] != ""


def test_build_prompt_does_not_use_the_legacy_structure_reminder_field():
    # v6: the universal PRESERVE_STRUCTURE instruction (always present) replaced
    # this SD1.5-era per-tier patch. Confirms it's genuinely unused now, not just
    # coincidentally absent.
    prompt = build_prompt("premium")
    assert TIER_SPECS["premium"]["structure_reminder"] not in prompt


def test_build_prompt_includes_user_notes_when_given():
    prompt = build_prompt("mid", user_notes="modern, blue accents")
    assert "modern, blue accents" in prompt


def test_build_prompt_omits_user_notes_when_not_given():
    prompt = build_prompt("mid")
    assert "None" not in prompt


def test_build_prompt_truncates_overlong_user_notes():
    # Defensive re-truncation matters here specifically because this reaches
    # build_prompt() from the public API too (see app/main.py) - a client could
    # send arbitrary-length text bypassing the frontend's <input maxlength>.
    overlong = "x" * (USER_NOTES_MAX_CHARS + 50)
    prompt = build_prompt("mid", user_notes=overlong)
    assert "x" * (USER_NOTES_MAX_CHARS + 1) not in prompt
    assert "x" * USER_NOTES_MAX_CHARS in prompt


def test_build_prompt_strips_whitespace_from_user_notes():
    prompt = build_prompt("mid", user_notes="   cozy farmhouse   ")
    assert "cozy farmhouse" in prompt
    assert "  cozy" not in prompt


def test_user_notes_appear_before_tier_paint_field():
    # user_notes should be high-priority (ahead of the tier's own generic paint
    # description) so an explicit user request can actually steer the render,
    # not just get appended after the tier's defaults have already been stated.
    prompt = build_prompt("mid", user_notes="scandinavian style")
    assert prompt.index("scandinavian style") < prompt.index(TIER_SPECS["mid"]["paint"])


# ---- v6 instruction-format invariants ----


def test_build_prompt_reads_as_an_edit_instruction_not_a_generation_spec():
    # This is the whole point of v6: the prompt must frame itself as editing the
    # input photo, not describing a target image to generate from scratch - the
    # root cause of a real failure where gpt-image-1 rendered entirely different
    # rooms for Premium/Mid instead of redecorating the actual uploaded photo.
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "Edit this photograph" in prompt
        assert "same physical room" in prompt


def test_build_prompt_includes_each_tiers_key_materials():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert TIER_SPECS[tier]["materials"] in prompt
        assert TIER_SPECS[tier]["flooring"] in prompt


def test_build_prompt_includes_positive_damage_repair_instruction():
    # v6: states UNIVERSAL_DAMAGE_NEGATIVE's intent positively in the prompt itself
    # (instruction models respond better to a direct command than negation) -
    # every tier's render should look renovated, never like the input's condition.
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "Repair and clean" in prompt
        assert "move-in ready" in prompt


def test_build_prompt_is_natural_language_sentences_not_comma_keywords():
    # Distinguishes v6 from the old SD1.5 keyword-soup format: real sentences
    # (period-terminated), not one long comma-joined descriptor list.
    prompt = build_prompt("mid")
    assert prompt.count(". ") > 5
