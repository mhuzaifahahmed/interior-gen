from app.pipeline.prompts import (
    PRESERVE_STRUCTURE,
    ROOM_TYPE_COMMON_SENSE,
    TIER_SPECS,
    USER_NOTES_MAX_CHARS,
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


def test_positive_prompt_states_chandelier_exclusion_for_budget_and_mid():
    # Instruction-following models are built to follow exclusions stated directly
    # in the prompt, so the tier exclusion is a positive-prompt "Do not include: ..."
    # sentence. Premium has no exclusions, so it should never mention chandelier.
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


def test_build_prompt_includes_tier_note_when_given():
    prompt = build_prompt("economical", tier_note="repaint over visible water stains")
    assert "repaint over visible water stains" in prompt


def test_build_prompt_omits_tier_note_when_not_given():
    prompt = build_prompt("economical")
    # sanity: no stray "None" or empty-note artifact leaks into the prompt
    assert "None" not in prompt


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


# ---- instruction-format invariants ----


def test_build_prompt_reads_as_an_edit_instruction_not_a_generation_spec():
    # The prompt must frame itself as editing the input photo, not describing a
    # target image to generate from scratch - the root cause of a real failure
    # where gpt-image-1 rendered entirely different rooms for Premium/Mid instead
    # of redecorating the actual uploaded photo.
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
    # States the damage-repair intent positively in the prompt itself
    # (instruction models respond better to a direct command than negation) -
    # every tier's render should look renovated, never like the input's condition.
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "Repair and clean" in prompt
        assert "move-in ready" in prompt


def test_build_prompt_is_natural_language_sentences_not_comma_keywords():
    # Real sentences (period-terminated), not one long comma-joined descriptor list.
    prompt = build_prompt("mid")
    assert prompt.count(". ") > 5


# ---- room-type common sense ----


def test_build_prompt_always_includes_room_type_common_sense():
    # Regression guard for a real failure: the image model was turning hallways
    # into bedrooms and adding furniture that doesn't belong in the space. This
    # instruction is generic (not hardcoded to "hall"/"bedroom" specifically) and
    # must be present for every tier regardless of room_description being given.
    for tier in TIER_SPECS:
        assert ROOM_TYPE_COMMON_SENSE in build_prompt(tier)


def test_room_type_common_sense_is_not_hardcoded_to_a_fixed_room_list():
    # The instruction must lean on the model's own judgement about the specific
    # room in the photo, not enumerate an exhaustive fixed list of room types -
    # the user explicitly asked that this not be trained/limited to just
    # "hall" and "bedroom".
    assert "common sense" in ROOM_TYPE_COMMON_SENSE.lower()


def test_preserve_structure_explicitly_locks_room_depth():
    # Regression guard for a real failure: a hallway's depth changed (became
    # shorter/longer) between tiers, effectively changing the room's architecture.
    assert "depth" in PRESERVE_STRUCTURE.lower()


# ---- structure_reminder reactivation ----


def test_only_economical_has_no_structure_reminder_in_the_spec():
    # Economical is the only tier that never touches the ceiling plane (its
    # ceiling field is explicitly "no false ceiling") - mid and premium both
    # rework it (false ceiling / designer cove ceiling respectively), which is
    # the kind of depth-carrying-surface change that needs the reminder.
    assert TIER_SPECS["economical"]["structure_reminder"] == ""
    assert TIER_SPECS["mid"]["structure_reminder"] != ""
    assert TIER_SPECS["premium"]["structure_reminder"] != ""


def test_build_prompt_reactivates_structure_reminder_for_tiers_that_have_one():
    # Regression guard: PRESERVE_STRUCTURE alone (stated once, near the top) wasn't
    # enough to stop vivid tier vocabulary/ceiling rework from pulling gpt-image-1
    # toward a hallucinated generic room. structure_reminder is restated for any
    # tier that defines one (mid and premium) - economical has none, since it never
    # reworks the ceiling.
    for tier in ("mid", "premium"):
        prompt = build_prompt(tier)
        assert TIER_SPECS[tier]["structure_reminder"] in prompt

    assert TIER_SPECS["economical"]["structure_reminder"] == ""
    assert "Even with these material upgrades" not in build_prompt("economical")


def test_structure_reminder_appears_after_the_tiers_material_instructions():
    # Placement matters, not just presence: it must land AFTER the marble/brass/
    # lighting sentences so it counteracts that vocabulary's pull, not get buried
    # before it where PRESERVE_STRUCTURE already sits.
    prompt = build_prompt("premium")
    assert prompt.index(TIER_SPECS["premium"]["materials"]) < prompt.index(
        TIER_SPECS["premium"]["structure_reminder"]
    )
