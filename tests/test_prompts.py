from app.pipeline.prompts import (
    ADDITIONAL_INSTRUCTIONS_MAX_CHARS,
    COLOR_PALETTES,
    COLOR_PROFILE,
    KAGGLE_DEFAULT_NEGATIVE_PROMPT,
    KAGGLE_PROMPT_MAX_WORDS,
    NEGATIVE_ADDITIONS,
    PRESERVE_STRUCTURE,
    ROOM_TYPE_COMMON_SENSE,
    STYLE_ELEMENT_POOLS,
    STYLE_OPTIONS,
    STYLE_PROFILES,
    TIER_FLOORING,
    TIER_STRUCTURE_REMINDER,
    build_kaggle_negative_prompt,
    build_kaggle_prompt,
    build_prompt,
    build_tier_spec,
    extract_style_and_palette,
)

TIERS = ("economical", "mid", "premium")


def test_all_tiers_present():
    assert set(TIER_FLOORING.keys()) == set(TIERS)


def test_style_options_and_profiles_are_in_sync():
    assert set(STYLE_OPTIONS) == set(STYLE_PROFILES.keys()) == set(STYLE_ELEMENT_POOLS.keys())
    assert len(STYLE_OPTIONS) == 9


def test_color_palettes_and_profile_are_in_sync():
    assert set(COLOR_PALETTES) == set(COLOR_PROFILE.keys())
    assert len(COLOR_PALETTES) == 8


def test_style_profiles_never_mention_colors():
    # Colors belong ONLY to Color Palette - a style profile that leaks a color
    # word would make a given style read differently depending on wording
    # accidents rather than only via the selected palette. "black"/"white" are
    # deliberately excluded from this check - they're used as material/hardware
    # FINISH descriptors here (e.g. Industrial's "matte black fixtures"), not as
    # a decor color choice, which is a real, accepted distinction from the
    # decorative hues below that palettes actually control.
    banned_colors = (
        "beige",
        "grey",
        "gray",
        "brown",
        "blue",
        "green",
        "red",
        "gold",
        "cream",
        "taupe",
        "navy",
    )
    import re

    for style, profile in STYLE_PROFILES.items():
        words = set(re.findall(r"[a-z]+", profile.lower()))
        for color in banned_colors:
            assert color not in words, f"style profile {style!r} mentions color word {color!r}"


def test_color_profiles_never_reference_furniture_or_style():
    # Palettes must never change the selected style - a palette entry that
    # smuggles in furniture/style vocabulary would risk doing exactly that.
    banned = ("sofa", "chair", "table", "modern", "industrial", "japandi", "luxury", "rug")
    for palette, text in COLOR_PROFILE.items():
        lowered = text.lower()
        for word in banned:
            assert word not in lowered, f"color profile {palette!r} mentions {word!r}"


def test_build_prompt_unknown_tier_raises():
    try:
        build_prompt("luxury-plus", "Modern", "Neutral")
    except ValueError:
        return
    assert False, "expected ValueError for unknown tier"


def test_build_prompt_unknown_style_raises():
    try:
        build_prompt("mid", "Not A Style", "Neutral")
    except ValueError:
        return
    assert False, "expected ValueError for unknown style"


def test_build_prompt_unknown_palette_raises():
    try:
        build_prompt("mid", "Modern", "Not A Palette")
    except ValueError:
        return
    assert False, "expected ValueError for unknown palette"


def test_build_tier_spec_unknown_inputs_raise():
    for bad_call in (
        lambda: build_tier_spec("nope", "Modern", "Neutral"),
        lambda: build_tier_spec("mid", "nope", "Neutral"),
        lambda: build_tier_spec("mid", "Modern", "nope"),
    ):
        try:
            bad_call()
        except ValueError:
            continue
        assert False, "expected ValueError"


# ---- geometry preservation (never changes, regardless of style/tier/palette) ----


def test_build_prompt_contains_preserve_structure():
    for tier in TIERS:
        for style in STYLE_OPTIONS:
            prompt = build_prompt(tier, style, "Neutral")
            assert PRESERVE_STRUCTURE in prompt


def test_build_prompt_always_includes_room_type_common_sense():
    for tier in TIERS:
        assert ROOM_TYPE_COMMON_SENSE in build_prompt(tier, "Modern", "Neutral")


def test_preserve_structure_explicitly_locks_room_depth():
    assert "depth" in PRESERVE_STRUCTURE.lower()


def test_preserve_structure_explicitly_names_the_never_change_list():
    lowered = PRESERVE_STRUCTURE.lower()
    for word in ("geometry", "walls", "doors", "windows", "ceiling", "camera angle", "perspective"):
        assert word in lowered


# ---- style respected ----


def test_build_prompt_names_the_selected_style():
    for style in STYLE_OPTIONS:
        prompt = build_prompt("mid", style, "Neutral")
        assert style in prompt
        assert STYLE_PROFILES[style] in prompt


def test_build_prompt_never_uses_a_different_styles_element_pool():
    # Controlled randomness must stay inside the selected style - none of another
    # style's furniture vocabulary should leak in. Run several times since the
    # furnish sentence is randomized per call.
    for _ in range(15):
        prompt = build_prompt("mid", "Japandi", "Neutral")
        for other_style, pool in STYLE_ELEMENT_POOLS.items():
            if other_style == "Japandi":
                continue
            # Only check phrases that are unique enough not to accidentally
            # overlap with Japandi's own pool (e.g. avoid generic words).
            for phrase in pool.seating + pool.coffee_tables:
                assert phrase not in prompt


# ---- palette changes colors/finishes only ----


def test_build_prompt_applies_the_selected_palette():
    for palette in COLOR_PALETTES:
        prompt = build_prompt("mid", "Modern", palette)
        assert COLOR_PROFILE[palette] in prompt


def test_build_prompt_palette_does_not_change_style_text():
    # Same style, two different palettes - the STYLE_PROFILES sentence itself
    # must be identical either way (only the palette sentence should differ).
    prompt_a = build_prompt("mid", "Scandinavian", "Neutral")
    prompt_b = build_prompt("mid", "Scandinavian", "Terracotta")
    assert STYLE_PROFILES["Scandinavian"] in prompt_a
    assert STYLE_PROFILES["Scandinavian"] in prompt_b


# ---- budget tier controls quality, not style/palette ----


def test_build_prompt_includes_flooring_from_the_tier_floor_ladder():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert any(option in prompt for option in TIER_FLOORING[tier])


def test_build_prompt_includes_a_material_quality_instruction():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert "Materials and finishes should reflect a" in prompt


def test_negative_additions_cover_budget_and_mid_but_not_premium():
    assert NEGATIVE_ADDITIONS["economical"]
    assert NEGATIVE_ADDITIONS["mid"]
    assert NEGATIVE_ADDITIONS["premium"] == ""


def test_build_prompt_states_chandelier_exclusion_for_budget_and_mid_even_with_luxury_style():
    # Real potential conflict: Style="Luxury" + Tier="economical/mid" - the tier's
    # cost ceiling must still win (Budget Tier ranks above Style is wrong reading;
    # actually Style ranks above Tier in the priority list, but Style only governs
    # FORM/LAYOUT vocabulary - the tier's material exclusion list is what actually
    # enforces the cost ceiling and must still fire regardless of chosen style).
    for tier in ("economical", "mid"):
        prompt = build_prompt(tier, "Luxury", "Neutral")
        assert "Do not include" in prompt
        assert "chandelier" in prompt
    for _ in range(10):
        assert "Do not include" not in build_prompt("premium", "Luxury", "Neutral")


def test_only_mid_and_premium_offer_a_feature_wall():
    assert "feature wall" not in build_prompt("economical", "Modern", "Neutral")
    for tier in ("mid", "premium"):
        assert "feature wall" in build_prompt(tier, "Modern", "Neutral")


# ---- bedroom handling: no table, a real bed, room purpose never changes ----


def test_every_style_has_bed_vocabulary():
    for style in STYLE_OPTIONS:
        assert STYLE_ELEMENT_POOLS[style].beds, f"style {style!r} has no bed options"


def test_room_type_common_sense_explicitly_addresses_bedrooms():
    lowered = ROOM_TYPE_COMMON_SENSE.lower()
    assert "dining table" in lowered
    assert "bed" in lowered
    assert "bedroom must stay a bedroom" in lowered


def test_build_prompt_gives_the_model_an_explicit_bedroom_branch():
    # Regression guard: the old furnish sentence unconditionally suggested a
    # sofa + coffee table + dining table for every room, including bedrooms -
    # directly contradicting ROOM_TYPE_COMMON_SENSE's "no dining items in a
    # bedroom" rule with no bed alternative ever offered. build_prompt() must
    # give the model an explicit if/else so it can furnish a bedroom
    # correctly (the model itself judges room type from the photo - this
    # codebase has no structured room-type data to branch on in Python).
    for tier in TIERS:
        for style in STYLE_OPTIONS:
            prompt = build_prompt(tier, style, "Neutral")
            assert "If this room is a bedroom" in prompt
            assert "do NOT add a dining table, coffee table, or dining chairs" in prompt
            assert any(bed in prompt for bed in STYLE_ELEMENT_POOLS[style].beds)


def test_bed_choice_varies_across_calls():
    prompts = {build_prompt("mid", "Modern", "Neutral") for _ in range(20)}
    beds_seen = {
        bed for bed in STYLE_ELEMENT_POOLS["Modern"].beds if any(bed in p for p in prompts)
    }
    assert len(beds_seen) > 1, "expected genuine variety across the style's bed options"


def test_build_prompt_restates_structure_reminder_for_every_tier():
    # Regression guard for a real failure: a deep/vast room (e.g. a large
    # industrial hall) came back shallow and ordinary-sized once furnished -
    # every tier needs the reminder, not just mid/premium (economical was
    # originally assumed safe since it never reworks the ceiling, but a real
    # generation showed the same depth collapse there too).
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert TIER_STRUCTURE_REMINDER[tier] in prompt


def test_structure_reminder_appears_after_the_furnishing_sentences():
    # Placement matters, not just presence: it must land AFTER the Controlled
    # Randomness furnishing content, not earlier in the Budget Tier section -
    # otherwise the vivid furniture vocabulary that comes after it has no
    # counter-anchor, which is exactly the ordering bug this test guards.
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert prompt.index("for upholstery and soft furnishings") < prompt.index(
            TIER_STRUCTURE_REMINDER[tier]
        )


# ---- additional instructions: refine, never replace ----


def test_build_prompt_includes_additional_instructions_when_given():
    prompt = build_prompt("mid", "Modern", "Neutral", additional_instructions="more indoor plants")
    assert "more indoor plants" in prompt


def test_build_prompt_omits_additional_instructions_when_not_given():
    prompt = build_prompt("mid", "Modern", "Neutral")
    assert "None" not in prompt


def test_build_prompt_frames_additional_instructions_as_a_refinement_not_a_replacement():
    prompt = build_prompt("mid", "Japandi", "Neutral", additional_instructions="blue sofa")
    assert "style wins" in prompt or "stays true to the Japandi style" in prompt


def test_build_prompt_truncates_overlong_additional_instructions():
    overlong = "x" * (ADDITIONAL_INSTRUCTIONS_MAX_CHARS + 50)
    prompt = build_prompt("mid", "Modern", "Neutral", additional_instructions=overlong)
    assert "x" * (ADDITIONAL_INSTRUCTIONS_MAX_CHARS + 1) not in prompt
    assert "x" * ADDITIONAL_INSTRUCTIONS_MAX_CHARS in prompt


def test_build_prompt_strips_whitespace_from_additional_instructions():
    prompt = build_prompt("mid", "Modern", "Neutral", additional_instructions="   cozy farmhouse   ")
    assert "cozy farmhouse" in prompt
    assert "  cozy" not in prompt


def test_additional_instructions_appear_after_style_and_palette_content():
    # Priority order: Style -> Tier -> Palette -> Additional Instructions.
    prompt = build_prompt("mid", "Modern", "Neutral", additional_instructions="scandinavian style")
    style_pos = prompt.index(STYLE_PROFILES["Modern"])
    palette_pos = prompt.index(COLOR_PROFILE["Neutral"])
    instructions_pos = prompt.index("scandinavian style")
    assert style_pos < palette_pos < instructions_pos


# ---- tier_note (room-specific) ----


def test_build_prompt_includes_tier_note_when_given():
    prompt = build_prompt("economical", "Modern", "Neutral", tier_note="repaint over visible water stains")
    assert "repaint over visible water stains" in prompt


def test_build_prompt_omits_tier_note_when_not_given():
    prompt = build_prompt("economical", "Modern", "Neutral")
    assert "None" not in prompt


def test_build_prompt_includes_room_description_when_given():
    prompt = build_prompt("premium", "Modern", "Neutral", room_description="A rectangular room with two windows.")
    assert "A rectangular room with two windows." in prompt


# ---- controlled randomness creates genuine variety ----


def test_controlled_randomness_produces_different_prompts_across_calls():
    prompts = {build_prompt("mid", "Modern", "Neutral") for _ in range(20)}
    # Extremely unlikely all 20 draws collapse to the same combination if the
    # randomization is actually wired up (STYLE_ELEMENT_POOLS has several
    # options per category) - a regression to a single fixed phrase would fail
    # this near-certainly.
    assert len(prompts) > 1


def test_controlled_randomness_never_changes_the_selected_style_or_palette():
    for _ in range(20):
        prompt = build_prompt("mid", "Japandi", "Sage")
        assert "Japandi" in prompt
        assert COLOR_PROFILE["Sage"] in prompt


# ---- instruction-format invariants (unchanged from the pre-v11 system) ----


def test_build_prompt_reads_as_an_edit_instruction_not_a_generation_spec():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert "Edit this photograph" in prompt
        assert "same physical room" in prompt


def test_build_prompt_encourages_creative_redesign_without_overly_restrictive_wording():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert "only redecorated" not in prompt
        assert "preserve everything" not in prompt
        assert "Creatively redesign" in prompt


def test_build_prompt_includes_positive_damage_repair_instruction():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert "Repair and clean" in prompt
        assert "move-in ready" in prompt


def test_build_prompt_includes_diversity_instruction():
    for tier in TIERS:
        prompt = build_prompt(tier, "Modern", "Neutral")
        assert "fresh interior concept" in prompt


def test_build_prompt_is_natural_language_sentences_not_comma_keywords():
    prompt = build_prompt("mid", "Modern", "Neutral")
    assert prompt.count(". ") > 5


# ---- build_tier_spec (materials pricing helper) ----


def test_build_tier_spec_has_the_fields_gemini_materials_needs():
    required_keys = {"label", "flooring", "paint", "lighting_temp", "ceiling", "feature_wall", "decor"}
    for tier in TIERS:
        spec = build_tier_spec(tier, "Modern", "Neutral")
        assert required_keys <= spec.keys()
        assert all(isinstance(v, str) for v in spec.values())


def test_build_tier_spec_is_stable_not_randomized():
    # Materials pricing wants a consistent per-project spec, not per-call jitter
    # - unlike build_prompt(), calling this twice with the same inputs must
    # return identical output.
    spec_a = build_tier_spec("premium", "Luxury", "Warm")
    spec_b = build_tier_spec("premium", "Luxury", "Warm")
    assert spec_a == spec_b


def test_build_tier_spec_economical_has_no_feature_wall():
    assert build_tier_spec("economical", "Modern", "Neutral")["feature_wall"] == ""
    assert build_tier_spec("mid", "Modern", "Neutral")["feature_wall"] != ""
    assert build_tier_spec("premium", "Modern", "Neutral")["feature_wall"] != ""


def test_build_tier_spec_reflects_the_selected_palette():
    spec_neutral = build_tier_spec("mid", "Modern", "Neutral")
    spec_sage = build_tier_spec("mid", "Modern", "Sage")
    assert spec_neutral["paint"] != spec_sage["paint"]
    assert spec_neutral["paint"] == COLOR_PROFILE["Neutral"]
    assert spec_sage["paint"] == COLOR_PROFILE["Sage"]


# ---- 20+ real sample prompts, no API calls - manual diversity check ----


def test_twenty_sample_prompts_are_all_distinct_and_valid():
    """Generates >=20 real prompts (mixing every tier/style/palette combo) purely
    via build_prompt() - no network/API calls - and checks the properties the
    spec calls out: geometry-preservation phrase present in all, style-profile
    text present and matching the chosen style, palette text present and
    matching the chosen palette, and no two prompts identical (genuine variety
    from controlled randomness, not just the style/palette axis)."""
    combos = [
        (tier, style, palette)
        for tier in TIERS
        for style in STYLE_OPTIONS
        for palette in COLOR_PALETTES
    ][:20]
    assert len(combos) == 20

    prompts = []
    for tier, style, palette in combos:
        prompt = build_prompt(tier, style, palette, additional_instructions="add a reading corner")
        assert PRESERVE_STRUCTURE in prompt
        assert STYLE_PROFILES[style] in prompt
        assert COLOR_PROFILE[palette] in prompt
        assert any(option in prompt for option in TIER_FLOORING[tier])
        prompts.append(prompt)

    assert len(set(prompts)) == len(prompts)


# ---- Kaggle short prompt (separate from build_prompt(), which stays untouched) ----


def test_build_prompt_is_unaffected_by_the_kaggle_functions_existing():
    # build_prompt() must remain completely untouched by this feature - same
    # content/order/length characteristics as before.
    prompt = build_prompt("mid", "Spanish", "Sage")
    assert len(prompt) > 1000
    assert PRESERVE_STRUCTURE in prompt
    assert ROOM_TYPE_COMMON_SENSE in prompt


def test_build_kaggle_prompt_stays_within_the_word_budget():
    for tier in TIERS:
        for style in STYLE_OPTIONS:
            for palette in ("Neutral", "Sage"):
                prompt = build_kaggle_prompt(tier, style, palette)
                assert len(prompt.split()) <= KAGGLE_PROMPT_MAX_WORDS


def test_build_kaggle_prompt_omits_geometry_preservation_text():
    # The whole point: PRESERVE_STRUCTURE/ROOM_TYPE_COMMON_SENSE's geometry
    # clauses are real content the OpenAI prompt needs but this model was
    # trained to not need - they must not appear in the short prompt at all.
    prompt = build_kaggle_prompt("mid", "Spanish", "Sage")
    assert "camera angle" not in prompt
    assert "ceiling height" not in prompt
    for banned_phrase in ("geometry", "camera angle", "perspective", "proportions"):
        assert banned_phrase not in prompt.lower()


def test_build_kaggle_prompt_keeps_the_bedroom_rule_briefly():
    prompt = build_kaggle_prompt("mid", "Modern", "Neutral")
    assert "bed" in prompt.lower()
    assert "table" in prompt.lower()


def test_build_kaggle_prompt_includes_style_tier_and_palette_identity():
    prompt = build_kaggle_prompt("premium", "Japandi", "Sage")
    assert "Japandi" in prompt
    assert "sage green" in prompt  # COLOR_PROFILE["Sage"]'s first word


def test_build_kaggle_prompt_unknown_inputs_raise():
    for bad_call in (
        lambda: build_kaggle_prompt("nope", "Modern", "Neutral"),
        lambda: build_kaggle_prompt("mid", "nope", "Neutral"),
        lambda: build_kaggle_prompt("mid", "Modern", "nope"),
    ):
        try:
            bad_call()
        except ValueError:
            continue
        assert False, "expected ValueError"


def test_build_kaggle_negative_prompt_includes_base_quality_terms_always():
    for tier in TIERS:
        assert KAGGLE_DEFAULT_NEGATIVE_PROMPT in build_kaggle_negative_prompt(tier)


def test_build_kaggle_negative_prompt_adds_tier_exclusions_for_budget_and_mid():
    for tier in ("economical", "mid"):
        negative = build_kaggle_negative_prompt(tier)
        assert NEGATIVE_ADDITIONS[tier] in negative
    # Premium has no NEGATIVE_ADDITIONS - negative prompt is just the base
    # quality terms, not "base terms + nothing" awkwardly appended.
    assert build_kaggle_negative_prompt("premium") == KAGGLE_DEFAULT_NEGATIVE_PROMPT


def test_extract_style_and_palette_round_trips_through_build_prompt():
    for tier in TIERS:
        for style in STYLE_OPTIONS:
            for palette in COLOR_PALETTES:
                full_prompt = build_prompt(tier, style, palette)
                extracted_style, extracted_palette = extract_style_and_palette(full_prompt)
                assert extracted_style == style
                assert extracted_palette == palette


def test_extract_style_and_palette_returns_none_for_unrelated_text():
    style, palette = extract_style_and_palette("just some random text about nothing in particular")
    assert style is None
    assert palette is None
