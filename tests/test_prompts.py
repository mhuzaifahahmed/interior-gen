from app.pipeline.prompts import (
    DESIGN_CONCEPTS,
    NEGATIVE_ADDITIONS,
    PRESERVE_STRUCTURE,
    ROOM_TYPE_COMMON_SENSE,
    TIER_FLOORING,
    TIER_SPECS,
    TIER_STRUCTURE_REMINDER,
    USER_NOTES_MAX_CHARS,
    build_prompt,
)


def test_all_tiers_present():
    assert set(TIER_SPECS.keys()) == {"economical", "mid", "premium"}
    assert set(DESIGN_CONCEPTS.keys()) == {"economical", "mid", "premium"}


def test_tier_specs_are_mutually_distinct():
    seen = set()
    for tier, spec in TIER_SPECS.items():
        fingerprint = tuple(spec.values())
        assert fingerprint not in seen, f"tier {tier} duplicates another tier's spec"
        seen.add(fingerprint)


def test_every_tier_has_multiple_design_concepts():
    # The whole point of the hierarchical system: real variety requires more
    # than one concept per tier to randomly choose between.
    for tier, concepts in DESIGN_CONCEPTS.items():
        assert len(concepts) >= 3, f"tier {tier} needs multiple concepts for real variety"


def test_design_concepts_within_a_tier_have_distinct_names():
    for tier, concepts in DESIGN_CONCEPTS.items():
        names = [c.name for c in concepts]
        assert len(names) == len(set(names)), f"tier {tier} has duplicate concept names"


def test_economical_concepts_never_mention_luxury_materials():
    # The real cost-ladder constraint: economical must stay under the cost
    # ceiling regardless of which concept gets randomly picked - "random within
    # boundaries", not "random anything".
    banned = ("marble", "brass", "velvet", "chandelier", "gold")
    for concept in DESIGN_CONCEPTS["economical"]:
        blob = " ".join(
            concept.materials.primary
            + concept.materials.accents
            + concept.lighting.fixtures
            + concept.decor.textiles
            + concept.decor.accessories
        ).lower()
        for word in banned:
            assert word not in blob, f"economical concept {concept.name!r} mentions {word!r}"


def test_only_mid_and_premium_concepts_offer_a_feature_wall():
    # "no accent wall, no wall mouldings" is a real cost-ladder rule, not a
    # missing field - every economical concept must have an empty feature_wall.
    for concept in DESIGN_CONCEPTS["economical"]:
        assert concept.feature_wall == []
    for tier in ("mid", "premium"):
        for concept in DESIGN_CONCEPTS[tier]:
            assert concept.feature_wall, f"{tier} concept {concept.name!r} has no feature wall option"


def test_build_prompt_contains_preserve_structure():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert PRESERVE_STRUCTURE in prompt


def test_build_prompt_names_the_chosen_concept():
    # The prompt should commit to one coherent design direction (Step 1 of the
    # hierarchical system) - whichever concept random.choice() landed on should
    # be named explicitly in the prompt, not left implicit.
    prompt = build_prompt("mid")
    chosen = next(c for c in DESIGN_CONCEPTS["mid"] if c.name in prompt)
    assert chosen.name in prompt


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
    # sentence. Premium legitimately has NO chandelier exclusion (some premium
    # concepts - Luxury Hotel, Designer Penthouse, Modern Classic - genuinely
    # include a chandelier as real lighting vocabulary), so the check for
    # premium is that it never emits a "Do not include" sentence at all, not
    # that the word never appears.
    for tier in ("economical", "mid"):
        prompt = build_prompt(tier)
        assert "chandelier" in prompt
        assert "Do not include" in prompt
    for _ in range(10):
        assert "Do not include" not in build_prompt("premium")


def test_negative_additions_cover_budget_and_mid_but_not_premium():
    assert NEGATIVE_ADDITIONS["economical"]
    assert NEGATIVE_ADDITIONS["mid"]
    assert NEGATIVE_ADDITIONS["premium"] == ""


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


def test_user_notes_appear_before_concept_design_content():
    # user_notes should be high-priority (ahead of the concept's own generic
    # design instructions) so an explicit user request can actually steer the
    # render, not just get appended after the concept's defaults are stated.
    prompt = build_prompt("mid", user_notes="scandinavian style")
    assert prompt.index("scandinavian style") < prompt.index("as the primary materials throughout")


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


def test_build_prompt_encourages_creative_redesign_without_overly_restrictive_wording():
    # "only redecorated" / "preserve everything" read as overly restrictive on
    # CONTENT (furniture/decor), not just geometry - the prompt must protect the
    # architecture while still explicitly inviting creative redesign of contents.
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "only redecorated" not in prompt
        assert "preserve everything" not in prompt
        assert "Creatively redesign" in prompt


def test_build_prompt_includes_flooring_from_the_tier_floor_ladder():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert any(option in prompt for option in TIER_FLOORING[tier])


def test_build_prompt_includes_a_material_instruction():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "as the primary materials throughout" in prompt


def test_build_prompt_includes_positive_damage_repair_instruction():
    # States the damage-repair intent positively in the prompt itself
    # (instruction models respond better to a direct command than negation) -
    # every tier's render should look renovated, never like the input's condition.
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "Repair and clean" in prompt
        assert "move-in ready" in prompt


def test_build_prompt_includes_diversity_instruction():
    for tier in TIER_SPECS:
        prompt = build_prompt(tier)
        assert "fresh interior concept" in prompt


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


def test_preserve_structure_explicitly_names_the_never_change_list():
    # Room geometry, walls, doors, windows, ceiling height, camera angle,
    # perspective - the exact "Preserve (Never Change)" list.
    lowered = PRESERVE_STRUCTURE.lower()
    for word in ("geometry", "walls", "doors", "windows", "ceiling", "camera angle", "perspective"):
        assert word in lowered


# ---- structure_reminder reactivation ----


def test_only_economical_has_no_structure_reminder():
    # Economical is the only tier that never touches the ceiling plane (its
    # ceiling stays "no false ceiling") - mid and premium both rework it
    # (false ceiling / designer cove ceiling respectively), which is the kind
    # of depth-carrying-surface change that needs the reminder.
    assert TIER_STRUCTURE_REMINDER["economical"] == ""
    assert TIER_STRUCTURE_REMINDER["mid"] != ""
    assert TIER_STRUCTURE_REMINDER["premium"] != ""


def test_build_prompt_reactivates_structure_reminder_for_tiers_that_have_one():
    # Regression guard: PRESERVE_STRUCTURE alone (stated once, near the top) wasn't
    # enough to stop vivid tier vocabulary/ceiling rework from pulling gpt-image-1
    # toward a hallucinated generic room. structure_reminder is restated for any
    # tier that defines one (mid and premium) - economical has none, since it never
    # reworks the ceiling.
    for tier in ("mid", "premium"):
        prompt = build_prompt(tier)
        assert TIER_STRUCTURE_REMINDER[tier] in prompt

    assert "Even with these design choices" not in build_prompt("economical")


def test_structure_reminder_appears_after_the_materials_instruction():
    # Placement matters, not just presence: it must land AFTER the materials
    # sentence so it counteracts vivid concept vocabulary, not get buried
    # before it where PRESERVE_STRUCTURE already sits.
    for tier in ("mid", "premium"):
        prompt = build_prompt(tier)
        assert prompt.index("as the primary materials throughout") < prompt.index(
            TIER_STRUCTURE_REMINDER[tier]
        )
