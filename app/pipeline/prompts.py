"""Tier style specs driving visual differentiation between the 3 prototypes.

Encodes a real renovation cost-impact methodology, not arbitrary "budget/mid/premium"
adjectives: each tier follows a "material quality ladder" (paint -> mouldings/wood ->
marble+brass) and a "lighting temperature ladder" (cool 5000-6000K -> neutral warm
3500-4000K -> warm luxury 2700-3000K), matching how a real renovation prioritizes
spend (paint -> flooring -> lighting -> feature walls -> decor) and how luxury reads
visually (restraint + material quality, not clutter).

Each spec is a structured dict, not free text, because it is intentionally the seed
of the future materials/pricing database (deferred feature - see the plan's Long-Term
Plan section): these fields will later map to itemized, priceable line items.

PROMPT FORMAT (v6): natural-language EDIT INSTRUCTIONS, not SD1.5-style keyword soup.
The active image backend is now OpenAI gpt-image-1 (an instruction-following editor,
like Nano Banana), not raw diffusion - build_prompt() composes the same structured
TIER_SPECS into a coherent paragraph of imperatives ("Paint the walls with...", "Do
not include...") instead of a comma-separated descriptor list.

Why the switch (real failure, not theory): the old keyword-soup format read to an
instruction model as a *generation spec for a target image*, not an *edit instruction
for the input photo* - a real 3-tier generation through gpt-image-1 came back with
Premium/Mid as entirely different rooms (generic luxury interiors), only Economical
(weakest vocabulary) loosely resembling the input. Same failure shape as the SD1.5
"luxury vocabulary overpowers structure" problem, worse here.

ACCEPTED TRADEOFF: this format is used for ALL providers now, including the free
Cloudflare/SD1.5 fallback - a deliberate, known regression of that path (declined a
per-provider renderer to keep one code path). SD1.5-specific dangers this format
walks back into:
1. CLIP hard-truncates prompts at 77 tokens - these paragraphs are well over that,
   so on Cloudflare/SD1.5 specifically, trailing content (decor, tier exclusions)
   will silently get cut. Not a problem for OpenAI (large context).
2. Diffusion models don't reliably obey negation in the positive prompt ("do not
   include a chandelier" can still prime one). The tier exclusion sentence built
   from NEGATIVE_ADDITIONS is positive-prompt text now, on top of (not instead of)
   the existing build_negative_prompt() channel below - keep using the negative
   channel for Cloudflare; for OpenAI, negative_prompt gets folded into the prompt
   too (see app/providers/openai.py) since there's no dedicated field there either.
If Cloudflare quality matters again later, the fix is a second, keyword-style
renderer selected per-provider - not implemented (explicit user decision to replace
the format entirely rather than maintain two).
"""

PROMPT_VERSION = "v7"

TIER_SPECS: dict[str, dict[str, str]] = {
    "economical": {
        "label": "budget renovation",
        "paint": "plain flat cream and sage-green two-tone paint, cheap and utilitarian looking",
        "flooring": "ordinary matte grey ceramic tile flooring, plain and unpolished, no shine, no gloss",
        "lighting_temp": "cool white practical lighting, 5000-6000K",
        "feature_wall": "no accent wall, no wall mouldings, bare plain walls",
        "ceiling": "plain flat white ceiling, no false ceiling, a simple ceiling fan",
        "materials": "paint only, no premium materials, no wood paneling, no marble",
        "palette": "muted cream, sage green, and grey tones",
        "density": "sparse furniture, about 80 percent of floor space left empty, uncluttered and clean",
        "decor": "one or two potted plants, simple thin plain curtains, one or two plain framed prints",
        "structure_reminder": "",
    },
    "mid": {
        "label": "mid-level renovation",
        "paint": "warm beige walls with olive-green wainscoting panel on the lower half",
        "flooring": "warm wood-look laminate flooring",
        "lighting_temp": "neutral warm lighting, 3500-4000K",
        "feature_wall": "wall mouldings and a few framed art prints",
        "ceiling": "false ceiling with a warm cove lighting strip",
        "materials": "paint, wall mouldings, wainscoting, laminate wood flooring",
        "palette": "warm beige, olive green, and light wood tones",
        "density": "balanced furniture arrangement, tidy and comfortable, not crowded",
        "decor": "an area rug, framed art prints, a few potted plants",
        "structure_reminder": "",
    },
    "premium": {
        "label": "premium luxury renovation",
        "paint": "dark wood panel and marble feature wall with brass trim accents",
        "flooring": "polished Italian marble flooring, reflective and bright",
        "lighting_temp": "warm luxury lighting, 2700-3000K, like a luxury hotel lobby",
        "feature_wall": "marble and dark wood panel wall with brass inlay",
        "ceiling": "designer multi-layer cove ceiling with warm gold-lit trim",
        "materials": "real marble, brass trim, dark wood paneling",
        "palette": "rich dark wood tones with gold and brass metallic accents",
        "density": "furniture arranged in curated symmetrical conversation zones, restrained, not overfilled",
        "decor": "floor-to-ceiling heavy fabric curtains, framed art, symmetrical furniture placement",
        # Unused by v6's build_prompt() - the universal structural lock (PRESERVE_STRUCTURE,
        # always present) now covers what this was patching. Left in TIER_SPECS rather than
        # deleted: harmless, and documents the SD1.5-era failure this used to guard against
        # (see git history / CLAUDE.md) in case the keyword-style renderer is ever revived.
        "structure_reminder": "still the same original room shape and window, do not enlarge or change the space",
    },
}

# v6: an imperative structural-lock instruction (was a comma-fragment for SD1.5's
# keyword format) - this is the single most important sentence in the whole prompt,
# always placed right after the edit framing, before any tier content.
PRESERVE_STRUCTURE = (
    "Keep the exact walls, windows, doors, columns or pillars, ceiling shape and height, "
    "floor layout, and proportions unchanged. Do not add, remove, move, or resize any "
    "structural element, and do not change the camera angle or perspective. The room's "
    "depth and size must stay exactly as shown - do not make it look longer, shorter, "
    "deeper, wider, or more spacious than the original photo."
)

# v7: a generic, room-type-agnostic instruction (not hardcoded to any specific list
# of room types - the model must use its own judgement about what's realistic for
# THIS specific space). Added after a real failure: the image model was turning
# hallways into bedrooms and adding furniture that doesn't belong in the space,
# because room_description alone (even once it names the room type) doesn't stop
# the model from defaulting to generic "make it look furnished" instincts. This is
# a blanket rule that applies regardless of what type of room describe_room() found.
ROOM_TYPE_COMMON_SENSE = (
    "Only add furniture and decor that would realistically belong in this specific "
    "type of space, based on common sense about what the photo actually shows - for "
    "example, do not add a bed or wardrobe to a hallway, entryway, or living room, "
    "and do not add dining or kitchen items to a bedroom. Do not change what kind of "
    "room or space this is."
)

# Tier-specific exclusions. Used two ways in v6: (a) still passed through
# build_negative_prompt() below for Cloudflare/SD1.5's dedicated negative_prompt
# channel, and (b) also rendered as a positive-prompt "Do not include: ..." sentence
# in build_prompt() itself, since instruction-following models (unlike diffusion)
# are built to follow exclusions stated directly in the instruction.
NEGATIVE_ADDITIONS: dict[str, str] = {
    "economical": "chandelier, crystal chandelier, pendant light, gold trim, marble, luxury, ornate, wainscoting",
    "mid": "chandelier, crystal chandelier, gold trim, marble, ornate luxury details",
    "premium": "",
}

# Kept for build_negative_prompt() (Cloudflare's dedicated channel). Also restated
# positively inside build_prompt() itself now (see _DAMAGE_REPAIR_INSTRUCTION) - the
# instruction that directly fixes "economical tier barely transforms a damaged room",
# the original reason generate_tier_notes()/tier_note exists.
UNIVERSAL_DAMAGE_NEGATIVE = (
    "damaged, dirty, stained, cracked walls, cracked ceiling, mold, mildew, water "
    "damage, peeling paint, debris, rubble, dust, disrepair, abandoned, derelict"
)

_DAMAGE_REPAIR_INSTRUCTION = (
    "Repair and clean any damage, stains, cracks, mould, or debris visible in the "
    "original photo. The result must look fully renovated and move-in ready, never "
    "like the original's condition."
)

# img2img "strength" - only meaningful for Cloudflare/SD1.5 (diffusion noise-strength
# dial); OpenAI's instruction-following API has no equivalent and ignores it (see
# app/providers/openai.py). Kept for the Cloudflare fallback path. Counterintuitive,
# hard-won lesson from real SD1.5 testing, still relevant if that path is used:
# premium is LOWER (0.45) than economical/mid (0.65/0.6) despite the biggest material
# change - rich luxury vocabulary + high freedom let SD1.5 hallucinate a different
# room entirely; premium needs LESS freedom to stay anchored, not more.
STRENGTH_BY_TIER: dict[str, float] = {
    "economical": 0.65,
    "mid": 0.6,
    "premium": 0.45,
}


USER_NOTES_MAX_CHARS = 150


def build_prompt(
    tier: str,
    room_description: str | None = None,
    tier_note: str | None = None,
    user_notes: str | None = None,
) -> str:
    """Compose a natural-language EDIT instruction for the given tier (v6 - see
    module docstring for why this replaced the SD1.5 keyword-soup format).

    Sentence order:
      1. Edit framing - this is a photo edit, not a fresh generation.
      2. PRESERVE_STRUCTURE - the structural lock, always present, non-negotiable.
      2b. ROOM_TYPE_COMMON_SENSE - always present; stops the model from turning a
          hallway into a bedroom or adding furniture that doesn't belong in the
          space, without hardcoding a fixed list of room types.
      3. room_description / tier_note / user_notes, if given (each optional).
      4. Tier renovation instructions, derived from TIER_SPECS in the same
         impact-per-dollar order the methodology itself uses (paint -> flooring ->
         lighting -> ceiling -> feature wall -> materials/palette -> density -> decor).
      5. Positive damage-repair instruction (see _DAMAGE_REPAIR_INSTRUCTION) - states
         UNIVERSAL_DAMAGE_NEGATIVE's intent positively, since instruction models
         respond better to a direct command than negation.
      6. Tier exclusions as a direct "Do not include" command (from NEGATIVE_ADDITIONS),
         only for tiers that have one (budget/mid; premium has none).

    tier_note is a short, room-specific instruction from Provider.generate_tier_notes()
    (e.g. "repaint over visible water stains on the ceiling"). user_notes is optional
    free text the user typed in (static/index.html's style-prompt input) - capped at
    USER_NOTES_MAX_CHARS and re-truncated here defensively (also enforced in
    app/main.py) since a client could call the API directly with arbitrary length text.
    """
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown tier: {tier}")

    spec = TIER_SPECS[tier]
    sentences = [
        f"Edit this photograph of a real room to create a {spec['label']}. "
        "The output must be the same physical room, clearly recognizable - only redecorated.",
        PRESERVE_STRUCTURE,
        ROOM_TYPE_COMMON_SENSE,
    ]
    if room_description:
        sentences.append(room_description)
    if tier_note:
        sentences.append(tier_note)
    if user_notes:
        sentences.append(user_notes.strip()[:USER_NOTES_MAX_CHARS])

    sentences += [
        f"Paint the walls with {spec['paint']}.",
        f"Install {spec['flooring']}.",
        f"Light the room with {spec['lighting_temp']}.",
        f"For the ceiling, use {spec['ceiling']}.",
        f"For the feature wall, use {spec['feature_wall']}.",
        f"Use these materials throughout: {spec['materials']}.",
        f"The overall color palette should be {spec['palette']}.",
        f"Furniture density: {spec['density']}.",
        f"Add this decor: {spec['decor']}.",
    ]

    sentences.append(_DAMAGE_REPAIR_INSTRUCTION)

    exclusions = NEGATIVE_ADDITIONS[tier]
    if exclusions:
        sentences.append(f"Do not include: {exclusions}.")

    return " ".join(sentences)


def build_negative_prompt(tier: str) -> str:
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown tier: {tier}")
    addition = NEGATIVE_ADDITIONS[tier]
    if addition:
        return f"{UNIVERSAL_DAMAGE_NEGATIVE}, {addition}"
    return UNIVERSAL_DAMAGE_NEGATIVE


def get_strength(tier: str) -> float:
    if tier not in STRENGTH_BY_TIER:
        raise ValueError(f"unknown tier: {tier}")
    return STRENGTH_BY_TIER[tier]
