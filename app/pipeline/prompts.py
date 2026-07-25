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

Prompt format is tuned for SD1.5 img2img (the current image backend, see
app/providers/cloudflare.py), which conditions on short descriptive phrases rather
than reasoning over natural-language instructions - so build_prompt() compresses the
tier spec into a comma-separated descriptive string, not paragraphs.

Two SD1.5-specific lessons baked in below (both found by testing real output):
1. CLIP hard-truncates prompts at 77 tokens, so token order = priority order, and
   the tier's DISTINGUISHING COLOR is folded directly into `paint` (priority #1)
   instead of a separate `palette` field, so it can never be truncated away - a
   generic "palette" field this far back was why early renders looked same-ish.
2. Diffusion models don't reliably obey negation in the positive prompt ("no
   chandelier" still primes a chandelier). Exclusions like "no chandelier" for
   budget/mid must go through the NEGATIVE prompt channel instead - see
   NEGATIVE_ADDITIONS and build_negative_prompt() below.
"""

PROMPT_VERSION = "v5"

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
        # Repeated structure anchor placed right before the heaviest luxury vocabulary
        # (marble/brass/chandelier-adjacent words) - a real failure was traced to this
        # word cluster overpowering PRESERVE_STRUCTURE and causing SD1.5 to hallucinate
        # a whole different room (a "luxury vanity nook") instead of redecorating the
        # actual input room. Repeating the anchor here reinforces it via CLIP
        # conditioning weight, right where it's needed most. Only premium needs this -
        # economical/mid don't carry the same luxury-vocabulary pull.
        "structure_reminder": "still the same original room shape and window, do not enlarge or change the space",
    },
}

PRESERVE_STRUCTURE = (
    "same room structure, same walls, same windows, same doors, same ceiling height, "
    "same camera angle and perspective as the original photo, unchanged room dimensions"
)

# Tier-specific NEGATIVE prompt additions (not positive-prompt phrasing - see module
# docstring point 2). Budget/mid must actively steer away from luxury fixtures that
# read as "real add-on costs"; premium has no such restriction.
NEGATIVE_ADDITIONS: dict[str, str] = {
    "economical": "chandelier, crystal chandelier, pendant light, gold trim, marble, luxury, ornate, wainscoting",
    "mid": "chandelier, crystal chandelier, gold trim, marble, ornate luxury details",
    "premium": "",
}

# Applies to every tier, positive prompt cannot reliably express this (same reason
# chandelier exclusion needs the negative channel): every render should look
# renovated/clean, never like the (possibly damaged/derelict) input photo's condition.
UNIVERSAL_DAMAGE_NEGATIVE = (
    "damaged, dirty, stained, cracked walls, cracked ceiling, mold, mildew, water "
    "damage, peeling paint, debris, rubble, dust, disrepair, abandoned, derelict"
)

# img2img "strength": 0.0 = unchanged input, 1.0 = ignores input entirely. Tiers need
# different values, not one shared constant - a badly damaged input photo needs MORE
# strength for the (visually minimal) economical tier to actually paint over the
# damage. Premium is deliberately LOWER, not higher, despite having the biggest
# material change: a real test showed economical (0.65) preserved structure fine,
# but premium at the SAME 0.65 hallucinated an entirely different room (no window,
# wrong shape) - the rich luxury vocabulary (marble/brass/chandelier) combined with
# that much freedom let SD1.5 fully reinterpret the scene toward a generic "luxury
# vanity" archetype instead of redecorating the actual input. Counterintuitive but
# real: premium needs LESS freedom to stay anchored, not more, precisely because its
# vocabulary has the strongest pull away from the input geometry.
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
    """Compose the SD1.5 prompt.

    SD1.5's CLIP text encoder hard-truncates at 77 tokens - anything past that is
    silently dropped. A full tier spec is well over that (~130-145 words), so token
    order here is priority order, not spec order: PRESERVE_STRUCTURE goes first
    (non-negotiable, must survive truncation), then fields in the same
    impact-per-dollar ranking the tier methodology itself uses (paint -> flooring ->
    lighting -> feature walls -> materials/palette -> decor last, since decor is the
    lowest-priority item and the one safest to lose if truncation happens).

    tier_note is an optional short, room-specific instruction (e.g. "repaint over
    visible water stains on the ceiling") produced by analyzing the actual uploaded
    photo per tier - see Provider.generate_tier_notes(). It is placed right before
    `paint` since it's usually a prerequisite/qualifier for that step, and is high
    enough priority to survive truncation alongside paint/flooring/lighting.

    user_notes is optional free text the user typed themselves (e.g. "modern, blue
    accents") - see the style-prompt input in static/index.html. Placed right after
    tier_note, ahead of the tier's own generic `paint` field, so an explicit user
    request can actually override/steer the tier's default look rather than being
    truncated away or drowned out. Capped at USER_NOTES_MAX_CHARS and defensively
    re-truncated here (not just in the frontend's `maxlength`) since this reaches
    build_prompt() from the API too, where a client could send arbitrary length text
    and eat the whole 77-token budget by itself.

    `structure_reminder` (per-tier, usually empty) is inserted right before
    `materials`/`palette` - i.e. right before the heaviest, most scene-defining
    vocabulary for that tier. It exists because a single PRESERVE_STRUCTURE mention
    wasn't enough to counteract premium's marble/brass/chandelier vocabulary in real
    testing; repeating the anchor closer to the words that threaten it reinforces it.
    """
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown tier: {tier}")

    spec = TIER_SPECS[tier]
    tokens = [
        f"{spec['label']} of the same room",
        PRESERVE_STRUCTURE,
    ]
    if room_description:
        tokens.append(room_description)
    if tier_note:
        tokens.append(tier_note)
    if user_notes:
        tokens.append(user_notes.strip()[:USER_NOTES_MAX_CHARS])
    tokens += [
        spec["paint"],
        spec["flooring"],
        spec["lighting_temp"],
        spec["ceiling"],
        spec["feature_wall"],
    ]
    if spec.get("structure_reminder"):
        tokens.append(spec["structure_reminder"])
    tokens += [
        spec["materials"],
        spec["palette"],
        spec["density"],
        spec["decor"],
    ]

    return ", ".join(tokens)


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
