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

PROMPT FORMAT (v6+): natural-language EDIT INSTRUCTIONS. The image backend is OpenAI
gpt-image-1, an instruction-following editor (like Nano Banana), not raw diffusion -
build_prompt() composes the structured TIER_SPECS into a coherent paragraph of
imperatives ("Paint the walls with...", "Do not include...") instead of a
comma-separated descriptor list.

Why this format (real failure, not theory): an earlier comma-separated keyword-soup
format read to the instruction model as a *generation spec for a target image*, not
an *edit instruction for the input photo* - a real 3-tier generation came back with
Premium/Mid as entirely different rooms (generic luxury interiors), only Economical
(weakest vocabulary) loosely resembling the input.

An earlier version of this pipeline also supported Cloudflare Workers AI/SD1.5 as a
free fallback image backend, with its own negative_prompt/strength diffusion-specific
machinery. That path has been removed entirely (not just disabled) - it was a source
of confusion once OpenAI became the real backend: SD1.5-era concepts like a 77-token
CLIP limit, negation-unreliable diffusion, and a "strength" noise dial don't apply to
an instruction-following editor and had no reason to keep shaping this file's design.
"""

PROMPT_VERSION = "v9"

import random
TIER_SPECS = {

    "economical": {

        "label": "budget renovation",
        "lighting_temp": "cool white practical lighting, 5000-6000K",
        "ceiling": "plain flat white ceiling, no false ceiling, a simple ceiling fan",
        "feature_wall": "no accent wall, no wall mouldings, bare plain walls",
        "materials": "paint only, no premium materials, no wood paneling, no marble",
        "density": "sparse furniture, about 80 percent of floor space left empty, uncluttered and clean",
        "structure_reminder": "",

        "paint": [
            "plain flat cream paint",
            "soft warm white paint",
            "light beige paint",
            "light greige paint",
            "muted sage paint",
            "warm ivory paint",
            "off-white paint"
        ],

        "flooring": [
            "ordinary matte ceramic tile flooring",
            "basic porcelain tile flooring",
            "entry-level vinyl flooring",
            "simple laminate flooring"
        ],

        "palette": [
            "soft neutral colors",
            "light earth tones",
            "muted contemporary colors",
            "warm neutral palette",
            "cool neutral palette"
        ],

        "decor": [
            "one or two potted plants with simple curtains",
            "minimal framed artwork and simple curtains",
            "small indoor plants with basic decor",
            "simple contemporary accessories"
        ],

    },

    "mid": {

        "label": "mid-level renovation",
        "lighting_temp": "neutral warm lighting, 3500-4000K",
        "ceiling": "false ceiling with a warm cove lighting strip",
        "feature_wall": "wall mouldings and a few framed art prints",
        "materials": "paint, wall mouldings, wainscoting, laminate wood flooring",
        "density": "balanced furniture arrangement, tidy and comfortable, not crowded",
        # Mid's "false ceiling" instruction reworks the ceiling plane - the same
        # kind of depth-carrying surface change that required premium's reminder
        # below. A real generation showed mid's ceiling change flattening the
        # room's receding depth lines with no counter-anchor after the ceiling
        # instruction (economical stays empty because it never touches the
        # ceiling plane at all - "no false ceiling"). Same generic wording as
        # premium's, not reworded for any specific room type.
        "structure_reminder": "still the same original room shape and window, do not enlarge or change the space",

        "paint": [
            "warm beige walls",
            "soft greige walls",
            "light taupe walls",
            "warm ivory walls",
            "subtle mushroom-colored walls"
        ],

        "flooring": [
            "quality wood-look laminate flooring",
            "engineered oak flooring",
            "high-quality vinyl plank flooring",
            "natural wood laminate flooring"
        ],

        "palette": [
            "warm natural colors",
            "earth inspired colors",
            "soft Scandinavian palette",
            "Japandi inspired palette",
            "modern neutral palette"
        ],

        "decor": [
            "tasteful artwork, plants and textured rug",
            "minimal designer decor",
            "balanced contemporary decor",
            "soft modern accessories"
        ],

    },

    "premium": {

        "label": "premium luxury renovation",
        "lighting_temp": "warm luxury lighting, 2700-3000K, layered recessed and cove fixtures",
        "ceiling": "designer multi-layer cove ceiling with warm gold-lit trim",
        "feature_wall": "marble and dark wood panel wall with brass inlay",
        "materials": "real marble, brass trim, dark wood paneling",
        "density": "furniture arranged in curated symmetrical conversation zones, restrained, not overfilled",
        # Reactivated after a real observed failure: premium's vivid luxury vocabulary
        # (marble, brass, "designer ceiling") pulled gpt-image-1 toward a hallucinated
        # generic luxury lobby (different column layout, narrower room) even with the
        # universal PRESERVE_STRUCTURE present earlier in the prompt. Restating the
        # structural lock tier-specifically, AFTER the tempting vocabulary rather than
        # only before it, is what actually anchors it back to the input photo - see
        # build_prompt() step 4b. Empty for economical, which doesn't show this pull.
        "structure_reminder": "still the same original room shape and window, do not enlarge or change the space",

        "paint": [
            "architectural painted walls with premium finishes",
            "designer textured wall finishes",
            "luxury limewash walls",
            "high-end matte architectural walls"
        ],

        "flooring": [
            "premium marble flooring",
            "large-format natural stone flooring",
            "luxury travertine flooring",
            "high-end oak flooring",
            "premium walnut flooring"
        ],

        "palette": [
            "quiet luxury palette",
            "rich warm neutrals",
            "timeless natural colors",
            "organic luxury palette",
            "high-end contemporary colors"
        ],

        "decor": [
            "curated luxury decor with premium artwork",
            "minimal luxury accessories",
            "designer furniture styling",
            "high-end contemporary decor"
        ],

    }

}

# An imperative structural-lock instruction - the single most important sentence
# in the whole prompt, always placed right after the edit framing, before any
# tier content.
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

# Tier-specific exclusions, rendered as a positive-prompt "Do not include: ..."
# sentence in build_prompt() - instruction-following models are built to follow
# exclusions stated directly in the instruction (unlike diffusion models, which
# need a separate negative-prompt channel; this codebase no longer has one).
NEGATIVE_ADDITIONS: dict[str, str] = {
    "economical": "chandelier, crystal chandelier, pendant light, gold trim, marble, luxury, ornate, wainscoting",
    "mid": "chandelier, crystal chandelier, gold trim, marble, ornate luxury details",
    "premium": "",
}

_DAMAGE_REPAIR_INSTRUCTION = (
    "Repair and clean any damage, stains, cracks, mould, or debris visible in the "
    "original photo. The result must look fully renovated and move-in ready, never "
    "like the original's condition."
)


USER_NOTES_MAX_CHARS = 150


def build_prompt(
    tier: str,
    room_description: str | None = None,
    tier_note: str | None = None,
    user_notes: str | None = None,
) -> str:
    """Compose a natural-language EDIT instruction for the given tier.

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
      4b. structure_reminder, if the tier has one (currently premium and mid) - restated
         AFTER the material/lighting instructions rather than only up in step 2,
         specifically to counteract vivid tier vocabulary (marble/brass/etc.) that's
         strong enough to pull the model toward a hallucinated generic room.
      5. Positive damage-repair instruction (see _DAMAGE_REPAIR_INSTRUCTION) - a
         direct command, since instruction models respond better to that than
         negation.
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
        f"Paint the walls with {random.choice(TIER_SPECS[tier]['paint'])}.",
        f"Install {random.choice(TIER_SPECS[tier]['flooring'])}.",
        f"Light the room with {spec['lighting_temp']}.",
        f"For the ceiling, use {spec['ceiling']}.",
        f"For the feature wall, use {spec['feature_wall']}.",
        f"Use these materials throughout: {spec['materials']}.",
        f"The overall color palette should be {random.choice(TIER_SPECS[tier]['palette'])}.",
        f"Furniture density: {spec['density']}.",
        f"Add this decor: {random.choice(TIER_SPECS[tier]['decor'])}.",
    ]

    if spec["structure_reminder"]:
        sentences.append(f"Even with these material upgrades, this is {spec['structure_reminder']}.")

    sentences.append(_DAMAGE_REPAIR_INSTRUCTION)

    exclusions = NEGATIVE_ADDITIONS[tier]
    if exclusions:
        sentences.append(f"Do not include: {exclusions}.")

    return " ".join(sentences)
