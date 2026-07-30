from app.pipeline.house_prompts import USER_PROMPT_MAX_CHARS, build_house_prompt


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
