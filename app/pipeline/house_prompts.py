"""Prompt builder for the "Build a House" feature's exterior/interior concept
render (the photoreal render_key deliverable, edited from the real plot
photo) - mirrors app/pipeline/prompts.py's build_prompt() role and format
lesson: a coherent natural-language EDIT instruction paragraph for gpt-image-1
(an instruction-following editor), not comma-separated keyword soup - the same
real failure (a keyword-soup format reading as a generation spec instead of an
edit instruction) that drove that format for room-redesign applies here too.

v3 added: an explicit floor-count hard constraint (the render must show exactly
the number of stories the layout/user actually specifies, not a guess), richer
photoreal vocabulary, and HOUSE_NEGATIVE_PROMPT - a "Do not include" exclusion
sentence grounded in real, researched failure modes of AI-generated
architecture renders (warped/misaligned windows, broken roofline geometry,
impossible/floating structure, wrong story count, smeared materials,
duplicated buildings/openings, inconsistent context, stray text/watermarks).
Same reasoning as prompts.py's NEGATIVE_ADDITIONS: instruction-following
models are built to follow exclusions stated in plain positive-prompt
language, so this is a sentence, not a separate API parameter.

v4 REMOVED the second, blueprint-sourced "3D layout render" entirely (and with
it, this module's using_blueprint_image branch) - a deliberate scope cut so
"Build a House" produces exactly two deliverables: this photoreal exterior
render, and a professional 2D CAD floor-plan image per floor (see
app/pipeline/cad_prompts.py) instead of a 3D isometric visualization. This
module now only ever composes the photo-edit prompt.

v14 (2026-09-18): added an optional exterior color_palette param, reusing
app/pipeline/prompts.py's COLOR_PROFILE dict verbatim (same 8 keys the room-
redesign "Color palette" dropdown already offers - "Neutral"/"Earthy"/"Warm"/
"Cool"/"Monochrome"/"Terracotta"/"Sage"/"Black & White") instead of inventing
a separate house-only vocabulary. Real gap being closed: Build a House had no
way at all to steer the exterior's color scheme - every render was whatever
color the model defaulted to.

v15 (2026-09-18, same day): v14 embedded the palette's color words as a
TRAILING TEXT FRAGMENT appended onto build_house_elevation_prompt()'s output
("Exterior color palette: ..."). That's weak for the Kaggle TEXT-TO-IMAGE
elevation model specifically - the notebook (kaggle_notebooks/
elevation_server.py) wraps whatever text it receives inside its OWN prompt
scaffolding, which hardcodes competing color/material vocabulary ("dark
textured stone", etc.) with no dedicated slot for an appended clause, so the
chosen palette could be drowned out. Room Redesign's own Kaggle path never has
this problem because build_kaggle_prompt() (app/pipeline/prompts.py) gives
color words a dedicated, never-cut priority slot INSIDE the prompt it builds -
the room notebook does no color logic of its own, it just consumes the
app-composed string. The elevation notebook is architecturally different (it
owns its scaffolding, the app deliberately sends only minimal structured
inputs), so the faithful mirror isn't more text - it's a structured `color`
field, sent the same way `floors`/`garage` already are, with the NOTEBOOK
weaving it into a dedicated position in its own prompt (see
elevation_server.py's _build_prompt()). build_house_elevation_prompt() no
longer touches color at all - see generate_house.py's render step, which now
resolves color_palette_words() once and passes it as
Provider.generate_house_render()'s `color` kwarg instead. build_house_prompt()
(the OpenAI/Modal EDIT-backend path) is UNCHANGED by this - those backends
ignore the structured `color` kwarg entirely, so build_house_prompt() keeps
inlining the color sentence into its long edit paragraph exactly as before,
since that's still the only channel those backends read.

v16 (2026-09-18, same day): added an optional exterior architectural_style
signal, mirroring color's v15 design exactly - a structured `style` field
sent to Provider.generate_house_render(), never embedded in
build_house_elevation_prompt()'s text. NOT a reuse of app/pipeline/prompts.py's
STYLE_PROFILES though (unlike color, which reused COLOR_PROFILE verbatim) -
that dict is interior-specific ("sleek furniture", "cozy textiles", "open
shelving"), which would actively mislead an EXTERIOR elevation model. Instead
this module owns its own HOUSE_STYLE_PROFILES (below) - the same 9
STYLE_OPTIONS names, mapped to facade/massing/roof/cladding language instead
of furniture/interior language. house_style_words() is the public resolver,
parallel to color_palette_words(). build_house_prompt() (EDIT path) gained an
interior_style param and inlines a style sentence the same way it already
inlines color - those backends have no structured channel, so the sentence is
still the only way they see it. The elevation notebook
(kaggle_notebooks/elevation_server.py) needed a matching two-sided change:
it was hardcoded around "Modern Luxury Contemporary" in three places
(DEFAULT_THEME, the modern-biased DEFAULT_FEATURES, and _floor_rules's
"luxury modern"/"flat roof" floor descriptors) that would have fought any
non-modern style the same way "dark textured stone" fought light color
palettes - see that file's own comments for the de-biasing.
"""

USER_PROMPT_MAX_CHARS = 200

# Exterior/architectural style vocabulary - the same 9 names as
# app/pipeline/prompts.py's STYLE_OPTIONS (reused for a consistent dropdown
# vocabulary across both tools), but mapped to FACADE/MASSING/ROOF/CLADDING
# language instead of that module's interior furniture/decor language -
# see this module's v16 docstring note for why prompts.py's STYLE_PROFILES
# couldn't be reused directly. Deliberately colorless (color is its own
# structured `color` field/COLOR_PROFILE, resolved separately by
# color_palette_words()) - never mix color words into these.
HOUSE_STYLE_PROFILES: dict[str, str] = {
    "Modern": (
        "modern architecture, clean rectilinear massing, flat or low-slope roof, "
        "large glass windows, minimalist facade"
    ),
    "Minimalist": (
        "minimalist architecture, simple geometric forms, unadorned flat facade, "
        "flush windows, restrained materials with no ornamentation"
    ),
    "Scandinavian": (
        "Scandinavian architecture, light timber cladding, pitched gable roof, "
        "large windows for natural light, simple functional massing"
    ),
    "Japandi": (
        "Japandi architecture, natural timber and stone facade, low-pitched roof "
        "with deep overhanging eaves, understated minimal massing, strong "
        "indoor-outdoor connection"
    ),
    "Industrial": (
        "industrial architecture, exposed concrete and brick facade, "
        "steel-framed windows, warehouse-inspired massing, utilitarian roofline"
    ),
    "Mediterranean": (
        "Mediterranean villa architecture, stucco plaster walls, terracotta clay "
        "tile pitched roof, arched windows and doorways, wrought-iron balconies"
    ),
    "Spanish": (
        "Spanish colonial architecture, stucco walls, clay tile roof, arched "
        "entryways, wrought-iron details, courtyard-style massing"
    ),
    "Traditional": (
        "traditional architecture, classic symmetrical facade, pitched shingle "
        "roof, detailed cornices and trim, brick or stone cladding"
    ),
    "Luxury": (
        "luxury contemporary architecture, grand symmetrical proportions, "
        "premium stone and glass facade, statement double-height entrance"
    ),
}

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


def build_house_elevation_prompt(prompt: str | None = None) -> str:
    """Minimal exterior-features string for the Kaggle TEXT-TO-IMAGE elevation
    model (RealVisXL) - the counterpart to app/pipeline/prompts.py's
    build_kaggle_prompt() for room-redesign, and deliberately the OPPOSITE of
    build_house_prompt() below.

    The elevation notebook (kaggle_notebooks/elevation_server.py) owns ALL the
    heavy prompt scaffolding itself - camera framing, photoreal vocabulary, the
    per-floor-count aspect-ratio + negative-prompt rules - so the app only
    needs to hand it the user's own short requirements text and let the model
    do the rest. A long, detailed app-side paragraph (like build_house_prompt's
    output, meant for an instruction-following EDIT model) would just fight the
    notebook's own scaffolding and add error surface for a from-scratch
    diffusion model. Empty is a fully supported result - the notebook falls
    back to its own sensible default exterior features when it gets no extras.

    Story count is NOT included here - it's sent SEPARATELY as a structured
    `floors` int (see KaggleImageProvider.generate_house_render), far more
    reliable than embedding "2 floors" in free text for the model to parse.

    Color is ALSO not included here (as of v15) - it's sent separately as a
    structured `color` string (see generate_house.py's render step and
    Provider.generate_house_render's `color` kwarg), for the same reliability
    reason as floor count, and because a trailing text clause was too weak
    against the notebook's own hardcoded color/material vocabulary - see this
    module's docstring for the full story.
    """
    if not prompt:
        return ""
    return prompt.strip()[:USER_PROMPT_MAX_CHARS]


def build_house_prompt(
    dimensions: dict,
    prompt: str | None = None,
    plot_description: str | None = None,
    room_layout: dict | None = None,
    color_palette: str | None = None,
    architectural_style: str | None = None,
) -> str:
    """Compose a natural-language edit instruction for generate_house_render()
    - always edits the real plot photo (image_bytes passed by the caller is
    always the plot photo now that the blueprint-sourced 3D render is gone).

    Sentence order: edit framing (incl. the hard floor-count constraint) ->
    architectural_style (if given) -> color_palette (if given) -> plot_description
    (if given) -> dimensions context -> room_layout floor/room summary (if
    given) -> the user's free-text prompt (if given) -> HOUSE_NEGATIVE_PROMPT -
    structural facts before free text, exclusions last so they land right
    before the model generates, same ordering principle as the rest of this
    function and as room-redesign's build_prompt(). Style and color_palette
    sit right after the floor-count constraint (all hard structural/visual
    facts) rather than buried after the user's own free text, where they'd
    risk reading as secondary to whatever the user typed. Style is stated
    before color, matching the "what it is, then what color it is" ordering
    room-redesign's own build_prompt() uses for Style/Palette.

    architectural_style (v16 addition, 2026-09-18, same treatment as
    color_palette above) drives EXTERIOR architecture via
    house_style_words()/HOUSE_STYLE_PROFILES - NOT room-redesign's interior
    STYLE_PROFILES (wrong vocabulary for a building exterior - see this
    module's docstring).

    room_layout, when given, is GeminiProvider.generate_room_layout()'s
    output (see app/pipeline/floor_layout.py + blueprint_svg.py) - used here
    only as a text summary so the render's floor/room count stays consistent
    with what the CAD plans show. Unrelated to the still-deferred, still-inert
    floor-plan VENDOR slot (app/providers/idealhouse.py).
    """
    floor_count = _resolve_floor_count(room_layout, prompt)

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

    style_text = house_style_words(architectural_style)
    if style_text:
        sentences.append(f"The house is in a {style_text} architectural style.")

    palette_text = color_palette_words(color_palette)
    if palette_text:
        sentences.append(
            f"Use an exterior color palette of {palette_text} for the walls, trim, roof, "
            "and finishes."
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


def color_palette_words(color_palette: str | None) -> str | None:
    """Reuses app/pipeline/prompts.py's COLOR_PROFILE (the same "Neutral"/
    "Earthy"/"Warm"/etc. vocabulary the room-redesign palette dropdown
    offers) rather than inventing a house-only palette system. Imported
    locally (not at module level) to avoid a circular-import risk -
    app/pipeline/prompts.py has no reason to import from this module, but
    keeping the import scoped here matches _resolve_floor_count()'s existing
    local-import-of-app.providers.gemini precedent in this same file.
    Silently returns None for an unrecognized/blank key - this module never
    raises over an optional, cosmetic input.

    Public (not prefixed with _) - app/pipeline/generate_house.py's render
    step calls this directly to resolve the structured `color` kwarg it
    passes to Provider.generate_house_render() (see v15's docstring note
    above for why that's now a separate channel from either prompt string)."""
    if not color_palette:
        return None
    from app.pipeline.prompts import COLOR_PROFILE

    return COLOR_PROFILE.get(color_palette)


def house_style_words(architectural_style: str | None) -> str | None:
    """Resolves HOUSE_STYLE_PROFILES (above) - the EXTERIOR counterpart to
    color_palette_words(), same signature/contract shape (None in -> None
    out, unrecognized key -> None, never raises). Deliberately does NOT
    import app/pipeline/prompts.py's STYLE_PROFILES - that dict is interior
    furniture/decor language, wrong for an exterior elevation model. See
    this module's v16 docstring note."""
    if not architectural_style:
        return None
    return HOUSE_STYLE_PROFILES.get(architectural_style)


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
