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
    room_layout: dict | None = None,
    using_blueprint_image: bool = False,
) -> str:
    """Compose a natural-language edit instruction for generate_house_render().

    Sentence order: edit framing -> plot_description (if given) -> dimensions
    context -> room_layout floor/room summary (if given) -> the user's
    free-text prompt (if given) - structural facts before free text, same
    ordering principle as the rest of this function.

    room_layout, when given, is GeminiProvider.generate_room_layout()'s output
    (see app/pipeline/floor_layout.py + blueprint_svg.py for how it also
    becomes the actual reference image passed as image_bytes to
    generate_house_render() - only the ground floor's blueprint image is used
    as the visual reference, so this text summary is what carries upper-floor
    information to the model). Unrelated to the still-deferred, still-inert
    floor-plan VENDOR slot (app/providers/idealhouse.py).

    using_blueprint_image must match what the caller actually passes as
    generate_house_render()'s image_bytes: True if it's the ground floor's
    drawn blueprint, False if the blueprint step failed/was skipped and the
    caller fell back to the raw plot photo - the opening sentence describes
    whichever image is actually being edited, so it never contradicts what
    the model is looking at.
    """
    if using_blueprint_image:
        opening = (
            "Edit this image (a computed room-layout blueprint for the ground floor, drawn to "
            "scale from the plot's real dimensions) to create a photorealistic architectural "
            "concept visualization. The result should be a plausible exterior (or interior, if "
            "the prompt requests it) concept render for a building with this exact room layout "
            "on this plot - a concept visualization, not a precise blueprint."
        )
    else:
        opening = (
            "Edit this photograph of a real building plot/piece of land to create an "
            "architectural concept visualization. The result should be a plausible "
            "exterior (or interior, if the prompt requests it) concept render for a "
            "building on this plot - a concept visualization, not a precise blueprint."
        )
    sentences = [opening]

    if plot_description:
        sentences.append(plot_description)

    dims_text = _format_dimensions(dimensions)
    if dims_text:
        sentences.append(f"The plot's stated dimensions are {dims_text} - keep the design plausible for a plot this size.")

    layout_text = _format_room_layout(room_layout)
    if layout_text:
        sentences.append(layout_text)

    if prompt:
        sentences.append(prompt.strip()[:USER_PROMPT_MAX_CHARS])

    return " ".join(sentences)


def _format_room_layout(room_layout: dict | None) -> str | None:
    if not room_layout or not room_layout.get("floors"):
        return None

    floors = room_layout["floors"]
    floor_summaries = []
    for floor in floors:
        room_names = ", ".join(room["name"] for room in floor.get("rooms", []))
        if room_names:
            floor_summaries.append(f"floor {floor.get('floor_number')} ({room_names})")

    if not floor_summaries:
        return None

    return f"The building has {len(floors)} floor(s): " + "; ".join(floor_summaries) + "."


def _format_dimensions(dimensions: dict) -> str | None:
    length = dimensions.get("length")
    width = dimensions.get("width")
    unit = dimensions.get("unit", "")
    if length and width:
        return f"{length} x {width} {unit}".strip()
    return None
