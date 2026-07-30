"""Prompt builder for the "Build a House" feature's exterior/interior concept
render - mirrors app/pipeline/prompts.py's build_prompt() role and format
lesson: a coherent natural-language EDIT instruction paragraph for gpt-image-1
(an instruction-following editor), not comma-separated keyword soup - the same
real failure (a keyword-soup format reading as a generation spec instead of an
edit instruction) that drove that format for room-redesign applies here too.
"""

USER_PROMPT_MAX_CHARS = 200


def build_house_prompt(
    dimensions: dict,
    prompt: str | None = None,
    plot_description: str | None = None,
) -> str:
    """Compose a natural-language edit instruction for generate_house_render().

    Sentence order: edit framing -> plot_description (if given) -> dimensions
    context -> the user's free-text prompt (if given). No floor-plan input is
    referenced here - that step is deferred (see app/providers/idealhouse.py) -
    so this always edits the original plot photo directly.
    """
    sentences = [
        "Edit this photograph of a real building plot/piece of land to create an "
        "architectural concept visualization. The result should be a plausible "
        "exterior (or interior, if the prompt requests it) concept render for a "
        "building on this plot - a concept visualization, not a precise blueprint."
    ]

    if plot_description:
        sentences.append(plot_description)

    dims_text = _format_dimensions(dimensions)
    if dims_text:
        sentences.append(f"The plot's stated dimensions are {dims_text} - keep the design plausible for a plot this size.")

    if prompt:
        sentences.append(prompt.strip()[:USER_PROMPT_MAX_CHARS])

    return " ".join(sentences)


def _format_dimensions(dimensions: dict) -> str | None:
    length = dimensions.get("length")
    width = dimensions.get("width")
    unit = dimensions.get("unit", "")
    if length and width:
        return f"{length} x {width} {unit}".strip()
    return None
