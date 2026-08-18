from app.pipeline.house_prompts import HOUSE_NEGATIVE_PROMPT, USER_PROMPT_MAX_CHARS, build_house_prompt


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
