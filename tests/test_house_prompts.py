from app.pipeline.house_prompts import (
    HOUSE_NEGATIVE_PROMPT,
    HOUSE_STYLE_PROFILES,
    USER_PROMPT_MAX_CHARS,
    build_house_elevation_prompt,
    build_house_prompt,
    color_palette_words,
    house_style_words,
)


def test_build_house_elevation_prompt_is_minimal_not_an_edit_paragraph():
    # The Kaggle text-to-image elevation model owns its own scaffolding - the
    # app-side prompt must stay SHORT (just the user's own text), never the long
    # "Edit this photograph..." build_house_prompt paragraph.
    result = build_house_elevation_prompt("modern car porch, glass balcony")
    assert result == "modern car porch, glass balcony"
    assert "Edit this photograph" not in result
    assert HOUSE_NEGATIVE_PROMPT not in result


def test_build_house_elevation_prompt_empty_when_no_user_text():
    # Empty is fully supported - the notebook falls back to its own default
    # exterior features.
    assert build_house_elevation_prompt(None) == ""
    assert build_house_elevation_prompt("") == ""
    assert build_house_elevation_prompt("   ") == ""


def test_build_house_elevation_prompt_truncates_overlong_text():
    overlong = "a" * (USER_PROMPT_MAX_CHARS + 100)
    assert len(build_house_elevation_prompt(overlong)) == USER_PROMPT_MAX_CHARS


def test_build_house_prompt_reads_as_an_edit_instruction():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "Edit this photograph" in prompt
    assert "concept" in prompt.lower()


def test_build_house_prompt_labels_it_a_concept_not_a_blueprint():
    # Matches the explicit requirement: no image-gen API guarantees dimensional
    # accuracy, so the render must never be framed as a precise blueprint.
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "not a precise blueprint" in prompt


def test_build_house_prompt_includes_dimensions_when_given():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "40 x 60 ft" in prompt


def test_build_house_prompt_omits_dimensions_when_not_given():
    prompt = build_house_prompt({})
    assert "dimensions" not in prompt.lower()


def test_build_house_prompt_includes_plot_description_when_given():
    prompt = build_house_prompt({}, plot_description="A rectangular plot facing north.")
    assert "A rectangular plot facing north." in prompt


def test_build_house_prompt_includes_user_prompt_when_given():
    prompt = build_house_prompt({}, prompt="2 floors, 3 bedrooms, modern style")
    assert "2 floors, 3 bedrooms, modern style" in prompt


def test_build_house_prompt_omits_user_prompt_when_not_given():
    prompt = build_house_prompt({})
    assert "None" not in prompt


def test_build_house_prompt_truncates_overlong_user_prompt():
    overlong = "x" * (USER_PROMPT_MAX_CHARS + 50)
    prompt = build_house_prompt({}, prompt=overlong)
    assert "x" * (USER_PROMPT_MAX_CHARS + 1) not in prompt
    assert "x" * USER_PROMPT_MAX_CHARS in prompt


def test_build_house_prompt_always_includes_the_negative_prompt():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert HOUSE_NEGATIVE_PROMPT in prompt


def test_build_house_prompt_negative_prompt_names_researched_failure_modes():
    assert "warped" in HOUSE_NEGATIVE_PROMPT.lower()
    assert "story" in HOUSE_NEGATIVE_PROMPT.lower() or "stories" in HOUSE_NEGATIVE_PROMPT.lower()
    assert "duplicated" in HOUSE_NEGATIVE_PROMPT.lower()
    assert "watermark" in HOUSE_NEGATIVE_PROMPT.lower()


def test_build_house_prompt_states_exact_floor_count_from_room_layout():
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 1", "area": 1}]},
        ]
    }
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"}, room_layout=room_layout)
    assert "exactly 2 stories" in prompt


def test_build_house_prompt_states_exact_floor_count_from_explicit_user_prompt():
    prompt = build_house_prompt({}, prompt="I want 3 floors, modern style")
    assert "exactly 3 stories" in prompt


def test_build_house_prompt_omits_floor_count_constraint_when_unknown():
    # HOUSE_NEGATIVE_PROMPT always mentions "stories" generically (as an
    # exclusion), so check for the specific hard-constraint phrase instead
    # of the bare word.
    prompt = build_house_prompt({}, prompt="a modern house, no floor count mentioned")
    assert "must have exactly" not in prompt


def test_build_house_prompt_always_edits_the_plot_photo():
    # v4 removed the second, blueprint-sourced 3D render entirely - this
    # function now only ever composes the photo-edit prompt (no
    # using_blueprint_image branch/param anymore).
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "Edit this photograph of a real building plot" in prompt


def test_build_house_prompt_includes_color_palette_when_given():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"}, color_palette="Sage")
    assert "sage green" in prompt.lower()
    assert "exterior color palette" in prompt.lower()


def test_build_house_prompt_omits_color_palette_when_not_given():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "color palette" not in prompt.lower()


def test_build_house_prompt_ignores_an_unrecognized_color_palette():
    # Optional/cosmetic input - an unrecognized key must never raise, it
    # just degrades to "no color instruction" (same as color_palette=None).
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"}, color_palette="Not A Real Palette")
    assert "color palette" not in prompt.lower()


def test_build_house_elevation_prompt_never_embeds_color_text():
    # v15: color is sent to the elevation model as a STRUCTURED `color` field
    # (see generate_house.py's render step / Provider.generate_house_render),
    # never embedded in this minimal prompt string anymore - a trailing text
    # clause was too weak against the notebook's own hardcoded color
    # vocabulary. build_house_elevation_prompt() takes no color_palette param
    # at all now.
    result = build_house_elevation_prompt("modern car porch")
    assert result == "modern car porch"
    assert "color" not in result.lower()


def test_color_palette_words_returns_the_real_color_profile_text():
    assert color_palette_words("Earthy") == "clay, terracotta, olive green, warm brown, sand, stone, and natural wood tones"
    assert "cool gray" in color_palette_words("Cool")


def test_color_palette_words_returns_none_for_blank_or_unrecognized():
    assert color_palette_words(None) is None
    assert color_palette_words("") is None
    assert color_palette_words("Not A Real Palette") is None


def test_house_style_words_returns_the_real_house_style_profile_text():
    assert house_style_words("Mediterranean") == HOUSE_STYLE_PROFILES["Mediterranean"]
    assert "architecture" in house_style_words("Industrial")


def test_house_style_words_returns_none_for_blank_or_unrecognized():
    assert house_style_words(None) is None
    assert house_style_words("") is None
    assert house_style_words("Not A Real Style") is None


def test_house_style_profiles_never_contain_color_words():
    # Deliberately colorless - color is its own structured `color`
    # field/COLOR_PROFILE, never mixed into the style vocabulary. Word-
    # boundary matching (not bare substring) so e.g. "warehouse-inspired"
    # doesn't false-positive on "red".
    import re

    banned = ("red", "blue", "green", "yellow", "black", "white", "gold", "beige")
    for style, words in HOUSE_STYLE_PROFILES.items():
        lowered = words.lower()
        for word in banned:
            assert not re.search(rf"\b{word}\b", lowered), (
                f"{style!r} descriptor unexpectedly mentions color word {word!r}"
            )


def test_build_house_prompt_includes_architectural_style_when_given():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"}, architectural_style="Mediterranean")
    assert "mediterranean villa architecture" in prompt.lower()
    assert "architectural style" in prompt.lower()


def test_build_house_prompt_omits_architectural_style_when_not_given():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"})
    assert "architectural style" not in prompt.lower()


def test_build_house_prompt_ignores_an_unrecognized_architectural_style():
    prompt = build_house_prompt({"length": 40, "width": 60, "unit": "ft"}, architectural_style="Not A Real Style")
    assert "architectural style" not in prompt.lower()


def test_build_house_prompt_states_style_before_color():
    prompt = build_house_prompt(
        {"length": 40, "width": 60, "unit": "ft"}, color_palette="Sage", architectural_style="Industrial"
    )
    style_pos = prompt.lower().index("architectural style")
    color_pos = prompt.lower().index("color palette")
    assert style_pos < color_pos


def test_build_house_elevation_prompt_never_embeds_style_text():
    # v16: architectural style is sent to the elevation model as a
    # STRUCTURED `style` field (see generate_house.py's render step /
    # Provider.generate_house_render), never embedded in this minimal
    # prompt string - build_house_elevation_prompt() takes no style param.
    result = build_house_elevation_prompt("modern car porch")
    assert result == "modern car porch"
    assert "style" not in result.lower()
