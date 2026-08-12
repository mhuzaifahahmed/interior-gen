"""Prompt builder for the "Build a House" feature's exterior/interior concept
render - mirrors app/pipeline/prompts.py's build_prompt() role and format
lesson: a coherent natural-language EDIT instruction paragraph for gpt-image-1
(an instruction-following editor), not comma-separated keyword soup - the same
real failure (a keyword-soup format reading as a generation spec instead of an
edit instruction) that drove that format for room-redesign applies here too.

v3 adds: an explicit floor-count hard constraint (the render must show exactly
the number of stories the layout/user actually specifies, not a guess), richer
photoreal/architectural vocabulary for both branches, and HOUSE_NEGATIVE_PROMPT
- a "Do not include" exclusion sentence grounded in real, researched failure
modes of AI-generated architecture renders (warped/misaligned windows, broken
roofline geometry, impossible/floating structure, wrong story count, smeared
materials, duplicated buildings/openings, inconsistent context, stray text/
watermarks). Same reasoning as prompts.py's NEGATIVE_ADDITIONS: instruction-
following models are built to follow exclusions stated in plain positive-
prompt language, so this is a sentence, not a separate API parameter.
"""

USER_PROMPT_MAX_CHARS = 200

HOUSE_NEGATIVE_PROMPT = (
    "Do not include, and actively avoid: bent, wavy, or warped window frames; "
    "misaligned or floating windows; broken or geometrically impossible roof "
    "edges; twisted or melted railings; bent window mullions; stairs that lead "
    "nowhere; any non-straight structural lines; floating building parts or "
    "structures that defy gravity or perspective; a different number of "
    "stories than specified; extra or duplicated floors; extra or duplicated "
    "houses or buildings in the frame; duplicated doors or windows; smeared "
    "wood grain; muddy or streaky glazing; stretched stone textures; obviously "
    "repeating or tiled texture seams; inconsistently removed, added, or "
    "distorted neighboring buildings and trees; and any text, labels, "
    "watermarks, dimension numbers, or a cartoonish/fake-CGI look."
)


def build_house_prompt(
    dimensions: dict,
    prompt: str | None = None,
    plot_description: str | None = None,
    room_layout: dict | None = None,
    using_blueprint_image: bool = False,
) -> str:
    """Compose a natural-language edit instruction for generate_house_render().

    Sentence order: edit framing (incl. the hard floor-count constraint) ->
    plot_description (if given) -> dimensions context -> room_layout
    floor/room summary (if given) -> the user's free-text prompt (if given) ->
    HOUSE_NEGATIVE_PROMPT - structural facts before free text, exclusions last
    so they land right before the model generates, same ordering principle as
    the rest of this function and as room-redesign's build_prompt().

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
    floor_count = _resolve_floor_count(room_layout, prompt)

    if using_blueprint_image:
        opening = (
            "Edit this image (a computed room-layout blueprint for the ground floor, drawn to "
            "scale from the plot's real dimensions) to create a photorealistic 3D architectural "
            "concept visualization of this exact layout - a furnished, fully rendered building, "
            "not a flat top-down blueprint or orthographic drawing. Use realistic materials, "
            "natural lighting, and a three-quarter or isometric exterior view (or interior, if "
            "the prompt requests it). This is a concept visualization, not a precise blueprint."
        )
    else:
        opening = (
            "Edit this photograph of a real building plot/piece of land to place a "
            "photorealistic, fully-built house on this exact plot, keeping the real ground, "
            "boundaries, and surroundings visible. Use an eye-level three-quarter exterior view "
            "(or interior, if the prompt requests it), natural daylight, realistic materials and "
            "landscaping, and high detail. This is a concept visualization, not a precise blueprint."
        )
    sentences = [opening]

    if floor_count:
        story_word = "story" if floor_count == 1 else "stories"
        sentences.append(
            f"The building must have exactly {floor_count} {story_word} - no more, no fewer."
        )

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

    sentences.append(HOUSE_NEGATIVE_PROMPT)

    return " ".join(sentences)


def _resolve_floor_count(room_layout: dict | None, prompt: str | None) -> int | None:
    """Only asserts a hard floor-count constraint when there's real basis for
    one: a computed room_layout (the normal pipeline path), or the user's own
    prompt explicitly naming a count. Deliberately does NOT default to "1
    story" when neither is known - stating a guessed constraint would be
    worse than stating none."""
    if room_layout and room_layout.get("floors"):
        return len(room_layout["floors"])
    if prompt:
        from app.providers.gemini import _explicit_floor_count

        return _explicit_floor_count(prompt)
    return None


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
