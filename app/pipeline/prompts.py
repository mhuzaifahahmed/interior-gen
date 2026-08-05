"""Prompt generation for the 3-tier room-redesign pipeline.

PROMPT FORMAT: natural-language EDIT INSTRUCTIONS. The image backend is OpenAI
gpt-image-1, an instruction-following editor (like Nano Banana), not raw diffusion -
build_prompt() composes structured design data into a coherent paragraph of
imperatives ("Paint the walls with...", "Do not include...") instead of a
comma-separated descriptor list.

Why this format (real failure, not theory): an earlier comma-separated keyword-soup
format read to the instruction model as a *generation spec for a target image*, not
an *edit instruction for the input photo* - a real 3-tier generation came back with
Premium/Mid as entirely different rooms (generic luxury interiors), only Economical
(weakest vocabulary) loosely resembling the input.

DESIGN-DIVERSITY SYSTEM (v10): a hierarchical concept model, not flat attribute
randomization. The earlier version (v9) only random.choice()'d small attributes
(paint color, flooring material, decor phrase) independently of each other within a
single fixed tier "look" - so every economical generation was still fundamentally
the same beige-paint-plus-ceramic-tile room with a different adjective, and GPT-Image
made minor edits instead of a genuinely different design. v10 first picks a whole
DesignConcept at random (e.g. "Japandi" vs "Transitional" vs "Warm Minimalism" for
mid), each with its own real furniture/material/lighting/decor vocabulary, and only
THEN randomizes within that concept's own boundaries. This is "random within
boundaries": the concept pool + each concept's option lists are the boundaries (real
renovation cost-ladder constraints per tier - see TIER_LIGHTING_TEMP/TIER_CEILING/
TIER_FLOORING/NEGATIVE_ADDITIONS below - are unchanged from before and still keep
economical away from marble/brass regardless of which concept is picked), and the
random concept + sub-choice selection is what actually varies per generation.

TIER_SPECS (below DESIGN_CONCEPTS) is kept as a STABLE, non-randomized summary dict
purely for app/providers/gemini.py's materials/pricing feature (generate_materials,
fallback_materials, _tier_line_items) - it is NOT used by build_prompt() and never
appears in an image-generation prompt. Materials pricing wants a consistent,
representative item description per tier for search terms, not per-generation
jitter, and gemini.py's consumers already expect plain strings (not lists) per
field - keeping this dict small and stable avoids re-plumbing that feature (out of
scope for this change) while still being a real, accurate description of that tier.
"""

import random
from dataclasses import dataclass, field

PROMPT_VERSION = "v10"


# ---------------------------------------------------------------------------
# Reusable per-concept structures (the "Optional" refactor requested: clean,
# typed building blocks instead of ad-hoc nested dicts).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FurnitureProfile:
    styles: list[str]  # e.g. "a low-profile oak-framed sofa with tapered wood legs"
    layouts: list[str]  # arrangement/circulation logic, not individual pieces


@dataclass(frozen=True)
class MaterialProfile:
    primary: list[str]  # dominant furniture/surface materials, e.g. "walnut"
    accents: list[str]  # hardware/trim accent materials, e.g. "brushed brass"


@dataclass(frozen=True)
class LightingProfile:
    fixtures: list[str]  # fixture TYPE/style; color temperature stays tier-level


@dataclass(frozen=True)
class DecorProfile:
    textiles: list[str]  # rugs, curtains, cushions, fabrics
    accessories: list[str]  # wall art, mirrors, plants, ceramics, objects


@dataclass(frozen=True)
class DesignConcept:
    name: str
    tier: str
    furniture: FurnitureProfile
    materials: MaterialProfile
    lighting: LightingProfile
    decor: DecorProfile
    color_philosophy: list[str]
    textures: list[str]
    # Feature walls: empty for every economical concept, on purpose - "no accent
    # wall, no wall mouldings" is a real cost-ladder rule (see NEGATIVE_ADDITIONS),
    # not a missing field.
    feature_wall: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tier-level constants: the real renovation cost-impact methodology (material
# quality ladder + lighting-temperature ladder) this project has always used.
# Unchanged in spirit from the pre-v10 TIER_SPECS - these are the "boundaries"
# every concept in a tier must stay inside, regardless of which concept or
# sub-choice random.choice() lands on.
# ---------------------------------------------------------------------------

TIER_LABELS = {
    "economical": "budget renovation",
    "mid": "mid-level renovation",
    "premium": "premium luxury renovation",
}

TIER_LIGHTING_TEMP = {
    "economical": "cool white practical lighting, 5000-6000K",
    "mid": "neutral warm lighting, 3500-4000K",
    "premium": "warm luxury lighting, 2700-3000K, layered recessed and cove fixtures",
}

TIER_CEILING = {
    "economical": "a plain flat white ceiling, no false ceiling, a simple ceiling fan",
    "mid": "a false ceiling with a warm cove lighting strip",
    "premium": "a designer multi-layer cove ceiling with warm gold-lit trim",
}

TIER_DENSITY = {
    "economical": "sparse furniture, about 80 percent of floor space left empty, uncluttered and clean",
    "mid": "a balanced furniture arrangement, tidy and comfortable, not crowded",
    "premium": "furniture arranged in curated symmetrical conversation zones, restrained, not overfilled",
}

# Restated AFTER the concept's material/lighting/decor instructions (see
# build_prompt()'s step 4b) - real, live-observed failure: vivid tier
# vocabulary (marble, brass, "designer ceiling") pulled gpt-image-1 toward a
# hallucinated generic luxury room even with the structural lock present
# earlier in the prompt. Empty for economical, which never reworks the
# ceiling plane and has shown no such drift.
TIER_STRUCTURE_REMINDER = {
    "economical": "",
    "mid": "still the same original room shape and window, do not enlarge or change the space",
    "premium": "still the same original room shape and window, do not enlarge or change the space",
}

# Flooring stays tier-level (cost-ladder driven), not concept-level: a
# "Contemporary" and "Cozy Minimal" economical room realistically use
# similarly cheap flooring regardless of aesthetic - the material COST scales
# with tier, not with style concept.
TIER_FLOORING = {
    "economical": [
        "ordinary matte ceramic tile flooring",
        "basic porcelain tile flooring",
        "entry-level vinyl flooring",
        "simple laminate flooring",
    ],
    "mid": [
        "quality wood-look laminate flooring",
        "engineered oak flooring",
        "high-quality vinyl plank flooring",
        "natural wood laminate flooring",
    ],
    "premium": [
        "premium marble flooring",
        "large-format natural stone flooring",
        "luxury travertine flooring",
        "high-end oak flooring",
        "premium walnut flooring",
    ],
}

# Tier-specific exclusions, rendered as a positive-prompt "Do not include: ..."
# sentence in build_prompt() - instruction-following models are built to follow
# exclusions stated directly in the instruction (unlike diffusion models, which
# need a separate negative-prompt channel; this codebase no longer has one).
# Unchanged from pre-v10: this is what actually enforces the cost ceiling
# (no chandelier/marble/gold-trim for economical or mid) regardless of which
# concept gets randomly picked.
NEGATIVE_ADDITIONS: dict[str, str] = {
    "economical": "chandelier, crystal chandelier, pendant light, gold trim, marble, luxury, ornate, wainscoting",
    "mid": "chandelier, crystal chandelier, gold trim, marble, ornate luxury details",
    "premium": "",
}


# ---------------------------------------------------------------------------
# DESIGN_CONCEPTS - the actual diversity engine. Each tier's concepts stay
# inside that tier's cost ceiling (economical concepts never mention marble/
# brass/velvet; premium concepts freely do) while giving GPT-Image a genuinely
# different furniture/material/lighting/decor vocabulary to work from each
# generation, not just a different adjective on the same room.
# ---------------------------------------------------------------------------

DESIGN_CONCEPTS: dict[str, list[DesignConcept]] = {
    "economical": [
        DesignConcept(
            name="Budget Scandinavian",
            tier="economical",
            furniture=FurnitureProfile(
                styles=[
                    "a simple light-oak-veneer sofa",
                    "a boxy light-wood-frame armchair",
                    "minimalist flat-pack-style shelving",
                    "a low-profile bed frame with light wood legs",
                ],
                layouts=[
                    "furniture kept minimal and pushed toward the walls to maximize open floor space",
                    "a single clear seating or resting zone, left uncluttered",
                ],
            ),
            materials=MaterialProfile(
                primary=["light pine", "birch veneer", "painted MDF"],
                accents=["matte black metal legs", "brushed aluminum hardware"],
            ),
            lighting=LightingProfile(
                fixtures=["a simple paper lantern shade", "a basic flush-mount ceiling light", "plain white track lighting"]
            ),
            decor=DecorProfile(
                textiles=["plain cotton curtains", "a simple flatweave rug", "basic linen-blend cushions"],
                accessories=["one or two potted plants", "a small round mirror", "simple framed prints"],
            ),
            color_philosophy=[
                "a soft neutral Scandinavian palette of white, light grey and warm wood",
                "a muted pastel accent against white walls",
            ],
            textures=["matte painted walls", "light wood grain", "plain woven cotton"],
        ),
        DesignConcept(
            name="Cozy Minimal",
            tier="economical",
            furniture=FurnitureProfile(
                styles=[
                    "a rounded-edge sofa in soft light fabric",
                    "a single accent chair in a soft neutral fabric",
                    "a simple low coffee table in light wood",
                ],
                layouts=[
                    "furniture arranged for one relaxed, comfortable focal seating area",
                    "an open, breathable layout with negative space left intentionally empty",
                ],
            ),
            materials=MaterialProfile(
                primary=["light ash", "painted MDF", "natural cotton"],
                accents=["matte black metal", "simple woven rattan"],
            ),
            lighting=LightingProfile(
                fixtures=["a single warm-toned floor lamp", "a plain dome pendant", "simple recessed downlights"]
            ),
            decor=DecorProfile(
                textiles=["a chunky knit throw", "plain woven curtains", "a soft low-pile rug"],
                accessories=["a stack of books as a side-table accent", "one large leafy plant", "a small ceramic vase"],
            ),
            color_philosophy=[
                "a warm neutral palette of oatmeal, sand and soft white",
                "a quiet monochrome palette with one soft accent tone",
            ],
            textures=["soft matte plaster walls", "natural woven fiber", "brushed cotton"],
        ),
        DesignConcept(
            name="Contemporary",
            tier="economical",
            furniture=FurnitureProfile(
                styles=[
                    "a clean-lined fabric sofa",
                    "a simple geometric accent chair",
                    "a slim modern console",
                ],
                layouts=[
                    "furniture arranged in a clear, functional layout facing the room's natural focal point",
                    "an efficient layout that keeps walkways open",
                ],
            ),
            materials=MaterialProfile(
                primary=["painted MDF", "laminate", "powder-coated metal"],
                accents=["matte black hardware", "brushed steel legs"],
            ),
            lighting=LightingProfile(
                fixtures=["a simple modern pendant", "a clean-lined flush ceiling light", "a slim floor lamp"]
            ),
            decor=DecorProfile(
                textiles=["solid-color curtains", "a simple geometric-pattern rug", "plain cushions in a single accent color"],
                accessories=["a single piece of abstract wall art", "a small mirror with a thin metal frame", "one or two plants in simple pots"],
            ),
            color_philosophy=[
                "a crisp neutral palette with one confident accent color",
                "cool contemporary greys and whites",
            ],
            textures=["smooth matte paint", "light laminate grain", "plain woven fabric"],
        ),
        DesignConcept(
            name="Urban Apartment",
            tier="economical",
            furniture=FurnitureProfile(
                styles=[
                    "a compact modular sofa",
                    "a slim-profile dining or work table",
                    "an industrial-look open shelving unit",
                ],
                layouts=[
                    "space-efficient furniture arrangement suited to a compact urban space",
                    "multi-purpose furniture placement that keeps the room feeling open",
                ],
            ),
            materials=MaterialProfile(
                primary=["painted MDF", "laminate wood-look", "matte-finish laminate"],
                accents=["matte black pipe-style shelving", "simple exposed metal legs"],
            ),
            lighting=LightingProfile(
                fixtures=["a simple Edison-style bulb fixture", "plain track lighting", "a basic drum-shade pendant"]
            ),
            decor=DecorProfile(
                textiles=["a simple woven rug", "plain linen-blend curtains", "solid-color cushions"],
                accessories=["a small gallery of simple framed prints", "one statement plant", "a compact round mirror"],
            ),
            color_philosophy=[
                "an urban neutral palette of warm grey, charcoal accents and white",
                "a raw, understated palette with one bold accent",
            ],
            textures=["matte painted plaster", "smooth laminate", "plain cotton weave"],
        ),
    ],
    "mid": [
        DesignConcept(
            name="Japandi",
            tier="mid",
            furniture=FurnitureProfile(
                styles=[
                    "a low-profile oak-framed sofa with tapered wood legs",
                    "a curved bouclé accent chair",
                    "a minimalist ash coffee table",
                    "a platform-style bed frame in natural wood",
                ],
                layouts=[
                    "furniture kept low-profile and slightly separated from the walls for an airy, open feel",
                    "an asymmetrical but balanced layout with one clear negative-space zone",
                ],
            ),
            materials=MaterialProfile(
                primary=["oak", "ash", "light walnut"],
                accents=["matte black steel", "natural rattan"],
            ),
            lighting=LightingProfile(
                fixtures=["a paper pendant lantern", "a slim linear recessed light", "a simple ceramic table lamp"]
            ),
            decor=DecorProfile(
                textiles=["linen curtains", "a jute or wool-blend rug", "cotton-linen cushions"],
                accessories=["ceramic vases in muted tones", "a single dried-branch arrangement", "a small handmade pottery bowl"],
            ),
            color_philosophy=[
                "muted earth tones with soft black accents",
                "a warm neutral palette of clay, sand and charcoal",
            ],
            textures=["natural linen weave", "raw oak grain", "matte ceramic finish"],
            feature_wall=["a warm wood slat feature wall"],
        ),
        DesignConcept(
            name="Organic Modern",
            tier="mid",
            furniture=FurnitureProfile(
                styles=[
                    "a curved bouclé sofa",
                    "a live-edge wood side table",
                    "a rounded rattan accent chair",
                ],
                layouts=[
                    "furniture arranged in soft, curved groupings rather than rigid lines",
                    "a layout that follows the room's natural light rather than the walls",
                ],
            ),
            materials=MaterialProfile(
                primary=["walnut", "light oak", "natural rattan"],
                accents=["brushed bronze", "matte terracotta"],
            ),
            lighting=LightingProfile(
                fixtures=["a sculptural ceramic table lamp", "a woven rattan pendant", "warm recessed cove lighting"]
            ),
            decor=DecorProfile(
                textiles=["a chunky wool rug", "linen curtains in a warm tone", "a bouclé-and-linen cushion mix"],
                accessories=["organic-shaped ceramic vessels", "a large sculptural plant", "an abstract textured wall hanging"],
            ),
            color_philosophy=[
                "a warm organic palette of terracotta, sand and sage",
                "earthy neutral tones with a soft green accent",
            ],
            textures=["raw plaster wall finish", "natural rattan weave", "soft bouclé fabric"],
            feature_wall=["a warm textured lime-wash feature wall"],
        ),
        DesignConcept(
            name="Transitional",
            tier="mid",
            furniture=FurnitureProfile(
                styles=[
                    "a tailored fabric sofa with a classic silhouette",
                    "a mix of one traditional wood armchair and one modern accent chair",
                    "a refined wood coffee table",
                ],
                layouts=[
                    "a balanced, symmetrical furniture arrangement anchored by a rug",
                    "furniture arranged in a traditional conversation grouping",
                ],
            ),
            materials=MaterialProfile(
                primary=["oak", "walnut", "painted wood millwork"],
                accents=["brushed nickel", "matte brass-toned hardware"],
            ),
            lighting=LightingProfile(
                fixtures=["a classic drum-shade pendant", "warm recessed cove lighting", "a pair of fabric-shade table lamps"]
            ),
            decor=DecorProfile(
                textiles=["tailored linen curtains", "a patterned wool-blend rug", "a mix of solid and subtly patterned cushions"],
                accessories=["a curated pair of framed art prints", "a classic ceramic table lamp", "a round wall mirror in a wood frame"],
            ),
            color_philosophy=[
                "a warm transitional palette of taupe, cream and soft navy",
                "a timeless neutral palette with one classic accent color",
            ],
            textures=["smooth painted millwork", "soft wool weave", "brushed wood grain"],
            feature_wall=["wall mouldings with a soft painted panel accent"],
        ),
        DesignConcept(
            name="Warm Minimalism",
            tier="mid",
            furniture=FurnitureProfile(
                styles=[
                    "a low-profile linen sofa",
                    "a single sculptural wood accent chair",
                    "a simple rounded-edge coffee table",
                ],
                layouts=[
                    "furniture kept to the essentials, with generous open floor space left visible",
                    "one clear, calm focal seating arrangement",
                ],
            ),
            materials=MaterialProfile(
                primary=["light oak", "ash", "painted wood panel"],
                accents=["matte black metal", "warm brushed bronze"],
            ),
            lighting=LightingProfile(
                fixtures=["a single sculptural floor lamp", "warm recessed cove lighting", "a simple ceramic pendant"]
            ),
            decor=DecorProfile(
                textiles=["natural linen curtains", "a soft wool-blend rug in a neutral tone", "plain linen cushions"],
                accessories=["one sculptural ceramic object", "a single large plant", "a thin-framed round mirror"],
            ),
            color_philosophy=[
                "a warm minimal palette of ivory, warm taupe and soft charcoal",
                "a quiet tonal palette with barely-there contrast",
            ],
            textures=["soft matte plaster", "natural linen weave", "warm wood grain"],
            feature_wall=["a subtly textured painted panel wall"],
        ),
        DesignConcept(
            name="Scandinavian",
            tier="mid",
            furniture=FurnitureProfile(
                styles=[
                    "a light-wood-framed sofa with soft grey upholstery",
                    "a simple wishbone-style accent chair",
                    "a round light-oak coffee table",
                ],
                layouts=[
                    "an airy, functional layout with furniture kept light and open",
                    "furniture arranged to maximize natural light across the room",
                ],
            ),
            materials=MaterialProfile(
                primary=["light oak", "birch", "painted wood"],
                accents=["matte black metal legs", "natural wool"],
            ),
            lighting=LightingProfile(
                fixtures=["a simple globe pendant", "warm recessed cove lighting", "a slim floor lamp with a linen shade"]
            ),
            decor=DecorProfile(
                textiles=["sheer linen curtains", "a soft wool rug in a light neutral tone", "a mix of textured cushions"],
                accessories=["a few well-placed plants", "simple framed graphic art", "a round mirror in a light wood frame"],
            ),
            color_philosophy=[
                "a classic Scandinavian palette of white, light wood and soft grey",
                "a soft Nordic palette with a single muted accent color",
            ],
            textures=["matte painted walls", "light wood grain", "soft wool weave"],
            feature_wall=["wall mouldings painted a soft muted tone"],
        ),
    ],
    "premium": [
        DesignConcept(
            name="Luxury Hotel",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "a tailored velvet sofa with brass-capped legs",
                    "a pair of curved lounge chairs in rich fabric",
                    "a polished stone-top coffee table",
                ],
                layouts=[
                    "furniture arranged in a curated symmetrical lounge-style grouping",
                    "a hospitality-inspired layout with a clear central conversation zone",
                ],
            ),
            materials=MaterialProfile(
                primary=["Calacatta marble", "dark walnut", "polished travertine"],
                accents=["brushed brass", "polished chrome"],
            ),
            lighting=LightingProfile(
                fixtures=["a sculptural crystal chandelier", "warm layered recessed and cove lighting", "a pair of brass table lamps"]
            ),
            decor=DecorProfile(
                textiles=["deep emerald velvet cushions", "a hand-knotted wool rug", "heavy silk-blend curtains"],
                accessories=["a curated sculptural art piece", "a large gilt-framed mirror", "a fresh floral arrangement"],
            ),
            color_philosophy=[
                "a rich jewel-toned palette against warm neutral walls",
                "a deep charcoal and gold-accented palette",
            ],
            textures=["polished marble veining", "a brushed brass finish", "plush velvet weave"],
            feature_wall=["a marble and dark wood panel wall with brass inlay"],
        ),
        DesignConcept(
            name="Modern Luxury",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "a sleek low-profile leather sofa",
                    "a sculptural modern accent chair",
                    "a polished stone waterfall-edge coffee table",
                ],
                layouts=[
                    "furniture arranged in clean, confident lines with generous negative space",
                    "a curated minimal layout that lets each piece stand out",
                ],
            ),
            materials=MaterialProfile(
                primary=["polished marble", "dark smoked oak", "brushed concrete-look stone"],
                accents=["matte black brass", "polished chrome"],
            ),
            lighting=LightingProfile(
                fixtures=["a sculptural geometric chandelier", "warm layered recessed lighting", "a minimalist brass floor lamp"]
            ),
            decor=DecorProfile(
                textiles=["a plush high-pile rug", "sheer floor-to-ceiling curtains", "a leather-and-bouclé cushion mix"],
                accessories=["a single large-scale abstract artwork", "a sculptural floor vase", "a sleek round mirror"],
            ),
            color_philosophy=[
                "a monochrome palette with a single bold accent",
                "a cool luxury palette of charcoal, white and brushed metal",
            ],
            textures=["a polished marble surface", "smoked wood grain", "a brushed metal finish"],
            feature_wall=["a polished stone feature wall with integrated lighting"],
        ),
        DesignConcept(
            name="Boutique Hotel",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "a curved velvet banquette-style sofa",
                    "an eclectic mix of one statement chair and one classic armchair",
                    "a brass-and-stone side table",
                ],
                layouts=[
                    "an intimate, layered furniture arrangement with distinct cozy zones",
                    "a boutique-style layout mixing lounge and display areas",
                ],
            ),
            materials=MaterialProfile(
                primary=["dark walnut", "veined marble", "warm brass"],
                accents=["aged brass", "smoked glass"],
            ),
            lighting=LightingProfile(
                fixtures=["a statement rattan or brass pendant", "warm layered cove and accent lighting", "a pair of eclectic table lamps"]
            ),
            decor=DecorProfile(
                textiles=["a richly patterned wool rug", "heavy jewel-toned curtains", "a velvet-and-linen cushion mix"],
                accessories=["curated vintage-inspired decor objects", "a gallery wall of framed art", "a large statement mirror"],
            ),
            color_philosophy=[
                "a moody jewel-toned palette with warm brass accents",
                "a rich layered palette of burgundy, forest green and gold",
            ],
            textures=["a veined marble surface", "aged brass patina", "plush velvet weave"],
            feature_wall=["a richly textured accent wall with brass detailing"],
        ),
        DesignConcept(
            name="Quiet Luxury",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "an understated linen-upholstered sofa with subtle tailoring",
                    "a single refined wood accent chair",
                    "a honed-stone coffee table",
                ],
                layouts=[
                    "furniture arranged with restrained, deliberate spacing",
                    "a calm, curated layout with no piece competing for attention",
                ],
            ),
            materials=MaterialProfile(
                primary=["honed marble", "light walnut", "natural travertine"],
                accents=["brushed brass", "matte bronze"],
            ),
            lighting=LightingProfile(
                fixtures=["a minimal sculptural chandelier", "soft layered recessed and cove lighting", "a single elegant floor lamp"]
            ),
            decor=DecorProfile(
                textiles=["a subtle high-quality wool rug", "natural linen curtains", "understated cashmere-blend cushions"],
                accessories=["one refined sculptural object", "a single large plant", "a thin-framed mirror in brushed brass"],
            ),
            color_philosophy=[
                "a muted quiet-luxury palette of warm stone, cream and soft taupe",
                "an understated tonal palette with almost no contrast",
            ],
            textures=["honed matte stone", "natural linen weave", "a brushed brass finish"],
            feature_wall=["a subtly textured stone-clad feature wall"],
        ),
        DesignConcept(
            name="Designer Penthouse",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "a sculptural Italian-style leather sofa",
                    "a designer accent chair with a sculptural frame",
                    "a sculptural marble-and-metal coffee table",
                ],
                layouts=[
                    "a bold, gallery-like furniture arrangement with dramatic negative space",
                    "furniture arranged to frame a single dramatic focal point",
                ],
            ),
            materials=MaterialProfile(
                primary=["book-matched marble", "dark smoked walnut", "polished terrazzo"],
                accents=["polished brass", "black chrome"],
            ),
            lighting=LightingProfile(
                fixtures=["a dramatic sculptural chandelier", "architectural cove and accent lighting", "a designer floor lamp with a sculptural silhouette"]
            ),
            decor=DecorProfile(
                textiles=["a bold abstract-pattern rug", "floor-to-ceiling sheer drapery", "a sculptural leather-and-silk cushion mix"],
                accessories=["a large-scale statement art piece", "a sculptural floor sculpture", "an oversized designer mirror"],
            ),
            color_philosophy=[
                "a bold high-contrast palette of black, cream and brushed metal",
                "a dramatic monochrome palette with one striking accent",
            ],
            textures=["book-matched marble veining", "a polished terrazzo surface", "smoked wood grain"],
            feature_wall=["a dramatic book-matched marble feature wall"],
        ),
        DesignConcept(
            name="Modern Classic",
            tier="premium",
            furniture=FurnitureProfile(
                styles=[
                    "a tailored roll-arm sofa in a refined fabric",
                    "a pair of classic wingback-inspired accent chairs",
                    "a polished wood coffee table with brass detailing",
                ],
                layouts=[
                    "a balanced, formal furniture arrangement anchored by a rug",
                    "furniture arranged in a timeless symmetrical grouping",
                ],
            ),
            materials=MaterialProfile(
                primary=["dark walnut", "polished marble", "painted wood millwork"],
                accents=["polished brass", "brushed bronze"],
            ),
            lighting=LightingProfile(
                fixtures=["a classic crystal or brass chandelier", "warm layered recessed and cove lighting", "a pair of elegant brass table lamps"]
            ),
            decor=DecorProfile(
                textiles=["a fine wool-silk blend rug", "tailored silk-blend curtains", "a velvet-and-linen cushion mix"],
                accessories=["a curated pair of classic framed artworks", "an elegant brass table lamp", "a gilt-framed mirror"],
            ),
            color_philosophy=[
                "a timeless palette of cream, navy and warm gold accents",
                "a classic neutral palette with rich brass warmth",
            ],
            textures=["a polished marble surface", "a brushed brass finish", "fine wool-silk weave"],
            feature_wall=["wall mouldings paired with a marble or wood panel inset"],
        ),
    ],
}


def _representative_tier_specs() -> dict[str, dict[str, str]]:
    """A STABLE (non-randomized) per-tier summary for app/providers/gemini.py's
    materials/pricing feature only - see the module docstring's TIER_SPECS
    paragraph. Derives from each tier's first DesignConcept so there's one
    source of truth for real material/style vocabulary instead of a second
    hand-maintained copy that could drift out of sync with DESIGN_CONCEPTS.
    """
    result: dict[str, dict[str, str]] = {}
    for tier, concepts in DESIGN_CONCEPTS.items():
        concept = concepts[0]
        materials_text = f"{concept.materials.primary[0]} with {concept.materials.accents[0]} accents"
        result[tier] = {
            "label": TIER_LABELS[tier],
            "lighting_temp": TIER_LIGHTING_TEMP[tier],
            "ceiling": TIER_CEILING[tier],
            "feature_wall": concept.feature_wall[0] if concept.feature_wall else "",
            "materials": materials_text,
            "density": TIER_DENSITY[tier],
            "structure_reminder": TIER_STRUCTURE_REMINDER[tier],
            "paint": concept.color_philosophy[0],
            "flooring": TIER_FLOORING[tier][0],
            "palette": concept.color_philosophy[0],
            "decor": concept.decor.accessories[0],
        }
    return result


# Consumed only by app/providers/gemini.py's materials pipeline - see module
# docstring. NOT used by build_prompt() below.
TIER_SPECS: dict[str, dict[str, str]] = _representative_tier_specs()


# An imperative structural-lock instruction - the single most important sentence
# in the whole prompt, always placed right after the edit framing, before any
# design content. This is the "Never Change" list stated as a direct command.
PRESERVE_STRUCTURE = (
    "Keep the exact room geometry, walls, doors, windows, columns or pillars, ceiling "
    "height, floor layout, and proportions unchanged. Do not add, remove, move, or "
    "resize any structural element, and do not change the camera angle or perspective. "
    "The room's depth and size must stay exactly as shown - do not make it look longer, "
    "shorter, deeper, wider, or more spacious than the original photo."
)

# A generic, room-type-agnostic instruction (not hardcoded to any specific list of
# room types - the model must use its own judgement about what's realistic for THIS
# specific space). Guards against a real failure: the image model was turning
# hallways into bedrooms and adding furniture that doesn't belong in the space,
# because room_description alone doesn't stop the model from defaulting to generic
# "make it look furnished" instincts.
ROOM_TYPE_COMMON_SENSE = (
    "Only add furniture and decor that would realistically belong in this specific "
    "type of space, based on common sense about what the photo actually shows - for "
    "example, do not add a bed or wardrobe to a hallway, entryway, or living room, "
    "and do not add dining or kitchen items to a bedroom. Do not change what kind of "
    "room or space this is."
)

# Nudges toward real variety across repeated generations of the same room. Can't
# literally know what a previous generation looked like (no new pipeline/DB state -
# out of scope for this change), so this leans on the model's own creative judgement
# plus the fact that DESIGN_CONCEPTS' random concept + sub-choice selection already
# makes structurally different output likely on its own.
DIVERSITY_INSTRUCTION = (
    "Design this as a completely fresh interior concept - avoid a generic or "
    "predictable look, and avoid repeating the same furniture style or arrangement "
    "you might use for a typical version of this room. Make deliberate, specific "
    "design choices, as a real interior designer would for a one-of-a-kind project."
)

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

    Hierarchical "random within boundaries" design selection (v10): first picks
    one DesignConcept at random from DESIGN_CONCEPTS[tier] (e.g. "Japandi" vs
    "Transitional" for mid), then randomizes furniture style/layout, materials,
    lighting fixture, textiles, accessories, color philosophy, and texture WITHIN
    that concept's own option lists - see DESIGN_CONCEPTS' module-docstring
    explanation of why this replaced flat independent attribute randomization.
    Flooring and lighting color-temperature stay tier-level (TIER_FLOORING/
    TIER_LIGHTING_TEMP) since they follow the real cost ladder, not aesthetic
    concept.

    Sentence order:
      1. Edit framing - this is a photo edit, not a fresh generation. Names the
         chosen concept so the model commits to one coherent creative direction.
      2. PRESERVE_STRUCTURE - the structural lock, always present, non-negotiable.
      2b. ROOM_TYPE_COMMON_SENSE - always present.
      3. room_description / tier_note / user_notes, if given (each optional).
      4. Concept-driven design instructions: furniture, layout, materials,
         flooring, lighting, ceiling, feature wall (if the tier allows one),
         color philosophy, texture, density, decor - in the same impact-per-
         dollar order the cost methodology uses (paint/color -> flooring ->
         lighting -> ceiling -> feature wall -> materials -> density -> decor).
      4b. TIER_STRUCTURE_REMINDER, if the tier has one (mid, premium) - restated
         AFTER the material/lighting instructions rather than only up in step 2,
         specifically to counteract vivid concept vocabulary (marble/brass/
         velvet/etc.) that's strong enough to pull the model toward a
         hallucinated generic room.
      5. DIVERSITY_INSTRUCTION - nudges toward a genuinely distinct result.
      6. Positive damage-repair instruction.
      7. Tier exclusions as a direct "Do not include" command (from
         NEGATIVE_ADDITIONS), only for tiers that have one (budget/mid; premium
         has none) - this is what actually enforces the cost ceiling regardless
         of which concept got randomly picked.

    tier_note is a short, room-specific instruction from Provider.generate_tier_notes()
    (e.g. "repaint over visible water stains on the ceiling"). user_notes is optional
    free text the user typed in (static/index.html's style-prompt input) - capped at
    USER_NOTES_MAX_CHARS and re-truncated here defensively (also enforced in
    app/main.py) since a client could call the API directly with arbitrary length text.
    """
    if tier not in DESIGN_CONCEPTS:
        raise ValueError(f"unknown tier: {tier}")

    concept = random.choice(DESIGN_CONCEPTS[tier])
    label = TIER_LABELS[tier]

    sentences = [
        f"Edit this photograph of a real room into a completely reimagined {label}, "
        f"designed in a {concept.name} interior design style - as if a professional "
        "interior designer redesigned this exact space from scratch. The output must "
        "be the same physical room, clearly recognizable.",
        PRESERVE_STRUCTURE,
        ROOM_TYPE_COMMON_SENSE,
        "Creatively redesign the interior: vary the furniture, furniture layout, "
        "lighting, rugs, curtains, wall art, mirrors, plants, decor, textures, "
        "fabrics, colors, accessories, feature walls, and ceiling details freely, "
        "while keeping the room's geometry, walls, doors, windows, and camera angle "
        "exactly as they are.",
    ]
    if room_description:
        sentences.append(room_description)
    if tier_note:
        sentences.append(tier_note)
    if user_notes:
        sentences.append(user_notes.strip()[:USER_NOTES_MAX_CHARS])

    furniture_style = random.choice(concept.furniture.styles)
    layout = random.choice(concept.furniture.layouts)
    primary_material = random.choice(concept.materials.primary)
    accent_material = random.choice(concept.materials.accents)
    fixture = random.choice(concept.lighting.fixtures)
    textile = random.choice(concept.decor.textiles)
    accessory = random.choice(concept.decor.accessories)
    color = random.choice(concept.color_philosophy)
    texture = random.choice(concept.textures)
    flooring = random.choice(TIER_FLOORING[tier])

    sentences += [
        f"Furnish the room with {furniture_style}, arranged with {layout}.",
        f"Use {primary_material} and {accent_material} as the primary materials throughout.",
        f"Install {flooring}.",
        f"Light the room with {fixture}, in {TIER_LIGHTING_TEMP[tier]}.",
        f"For the ceiling, use {TIER_CEILING[tier]}.",
    ]
    if concept.feature_wall:
        sentences.append(f"For the feature wall, use {random.choice(concept.feature_wall)}.")
    sentences += [
        f"The overall color philosophy should be {color}, with {texture} as a defining texture.",
        f"Furniture density: {TIER_DENSITY[tier]}.",
        f"Add this decor: {textile} and {accessory}.",
    ]

    structure_reminder = TIER_STRUCTURE_REMINDER[tier]
    if structure_reminder:
        sentences.append(f"Even with these design choices, this is {structure_reminder}.")

    sentences.append(DIVERSITY_INSTRUCTION)
    sentences.append(_DAMAGE_REPAIR_INSTRUCTION)

    exclusions = NEGATIVE_ADDITIONS[tier]
    if exclusions:
        sentences.append(f"Do not include: {exclusions}.")

    return " ".join(sentences)
