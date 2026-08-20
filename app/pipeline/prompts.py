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

DESIGN-DIVERSITY SYSTEM (v11): "random within boundaries", driven by explicit user
choices instead of an internally-randomized design concept. v10 picked a whole
DesignConcept at random per generation (e.g. "Japandi" vs "Transitional") - that
produced real variety, but the user never actually chose the look they'd get, which
made results unpredictable and made the same room's "Mid" tier look like a
completely different style on every regeneration. v11 replaces the random concept
pick with two REQUIRED user choices - Interior Style and Color Palette (see
STYLE_OPTIONS/COLOR_PALETTES below) - so the *style* is deterministic and
user-controlled, while *within* that style, individual furniture pieces, fixtures,
textiles, and decor are still randomized every generation (see STYLE_ELEMENT_POOLS)
so two generations of the same room/style/palette are never identical. This is the
literal "random within boundaries" contract: the boundaries are the selected Style,
the selected Palette, and the selected Budget Tier's cost ceiling (TIER_FLOORING/
TIER_LIGHTING_TEMP/TIER_CEILING/NEGATIVE_ADDITIONS, unchanged in spirit from pre-v11)
- randomness never crosses any of those three boundaries.

PROMPT PRIORITY (nothing lower may override anything higher):
  1. Preserve Room Geometry  - structural lock, always first, non-negotiable.
  2. Interior Style          - STYLE_PROFILES[style], the design's core identity.
  3. Budget Tier             - material/finish quality ceiling (unchanged tier logic).
  4. Color Palette           - COLOR_PROFILE[palette], colors/finishes only.
  5. Additional Instructions - optional user free text, a refinement, never a
                                style replacement (style wins on conflict).
  6. Controlled Randomness   - per-generation furniture/decor/fixture variety,
                                drawn only from the selected style's own pools.

STYLE_PROFILES intentionally carry NO color words - colors live only in
COLOR_PROFILE, so a given style always reads the same regardless of which palette
is layered on top of it, and a given palette always means the same colors
regardless of which style it's paired with. This mirrors why TIER_FLOORING/
TIER_LIGHTING_TEMP/NEGATIVE_ADDITIONS stayed tier-level rather than per-style: the
combinatorial grid (9 styles x 8 palettes x 3 tiers = 216 combinations) only stays
maintainable if each axis only ever encodes its own concern.

v11.3: fixed a real, user-reported "Industrial style / Earthy palette doesn't look
right" complaint - two concrete, findable-in-text bugs, not a rendering fluke.
(1) TIER_CEILING["premium"] said "warm gold-lit trim" - a literal COLOR word
inside a Budget Tier sentence, directly violating this module's own stated
"colors live only in COLOR_PROFILE" rule above, so it fought every non-gold
palette (Earthy included) at the premium tier specifically. Fixed by describing
only the ceiling's structure/light-quality, no color. (2) The generic tier
vocabulary (TIER_MATERIAL_QUALITY/TIER_CEILING/TIER_FLOORING) assumes a
"premium = polished marble, gold-cove ceiling, travertine/walnut flooring"
aesthetic that's the OPPOSITE of Industrial's raw-materials identity
(STYLE_PROFILES["Industrial"]: concrete, exposed metal, brick) - Budget Tier
comes right after Interior Style in PROMPT PRIORITY, so it was actively
overriding the style the user chose. Fixed with INDUSTRIAL_TIER_MATERIAL_QUALITY/
_CEILING/_FLOORING - Industrial-specific overrides used by build_prompt(),
build_tier_spec() (materials pricing), AND build_kaggle_prompt() whenever
style == "Industrial", keeping the identical quality LADDER (premium still
reads as more expensive than mid) but expressed in materials congruent with
Industrial's own character (polished/sealed concrete, blackened/brushed steel,
exposed structural elements) instead of vocabulary that reads as a different
style. Deliberately scoped to ONLY Industrial, not a general per-style system -
it's the one style whose identity is fundamentally incompatible with the
generic tier reading; the other 8 styles' "premium = refined/polished" doesn't
contradict their own identity the way it does here.
"""

import random
import re
from dataclasses import dataclass

PROMPT_VERSION = "v11.3"


# ---------------------------------------------------------------------------
# Interior Style + Color Palette - the two REQUIRED user-facing selections
# (static/index.html's "Style Parameters" panel). Order in these lists is the
# order options appear in the frontend dropdowns.
# ---------------------------------------------------------------------------

STYLE_OPTIONS: list[str] = [
    "Modern",
    "Minimalist",
    "Scandinavian",
    "Japandi",
    "Industrial",
    "Mediterranean",
    "Spanish",
    "Traditional",
    "Luxury",
]

COLOR_PALETTES: list[str] = [
    "Neutral",
    "Earthy",
    "Warm",
    "Cool",
    "Monochrome",
    "Terracotta",
    "Sage",
    "Black & White",
]

# Lightweight anchors, NOT a teaching pass - gpt-image-1 already understands what
# "Japandi" or "Industrial" mean. These just pin the model toward this project's
# preferred interpretation of each style (form/line/layout language only). Colors
# are deliberately absent - they belong exclusively to COLOR_PROFILE below.
STYLE_PROFILES: dict[str, str] = {
    "Modern": (
        "Clean lines, sleek furniture, uncluttered spaces, refined finishes, "
        "simple forms, contemporary materials."
    ),
    "Minimalist": (
        "Minimal furniture, open spaces, restrained decoration, functional design, "
        "clean geometry, subtle textures."
    ),
    "Scandinavian": (
        "Light wood finishes, functional furniture, cozy textiles, natural "
        "materials, soft lighting, comfortable interiors."
    ),
    "Japandi": (
        "Natural wood, handcrafted ceramics, linen fabrics, organic textures, "
        "minimalist furniture, soft indirect lighting."
    ),
    "Industrial": (
        "Concrete finishes, exposed metal, brick textures, matte black fixtures, "
        "open shelving, raw materials."
    ),
    "Mediterranean": (
        "Natural stone, textured plaster, rustic wood, handcrafted details, "
        "elegant arches, timeless craftsmanship."
    ),
    "Spanish": (
        "Decorative tiles, rustic wood, textured plaster, handcrafted details, "
        "arches, traditional craftsmanship."
    ),
    "Traditional": (
        "Classic furniture, detailed woodwork, timeless finishes, layered decor, "
        "elegant craftsmanship."
    ),
    "Luxury": (
        "Premium materials, elegant furniture, refined detailing, designer "
        "lighting, marble accents, sophisticated finishes."
    ),
}

# Color direction only - never furniture, never room type, never quality level.
# Applied to walls, upholstery, curtains, rugs, accessories, accent colors,
# flooring tones (when appropriate), and decorative objects (see build_prompt()'s
# Color Palette section) - never the selected Style itself.
COLOR_PROFILE: dict[str, str] = {
    "Neutral": "warm whites, soft beige, ivory, light greige, and muted taupe",
    "Earthy": "clay, terracotta, olive green, warm brown, sand, stone, and natural wood tones",
    "Warm": "cream, caramel, warm oak, honey, soft bronze, and golden beige",
    "Cool": "cool gray, charcoal, slate, muted blue, and soft graphite",
    "Monochrome": "white, gray, charcoal, and black with subtle tonal variation",
    "Terracotta": "terracotta, burnt clay, warm rust, muted orange accents, and earthy browns",
    "Sage": "sage green, muted olive, warm gray, soft cream, and natural stone",
    "Black & White": "black and white with crisp contrast and restrained accents",
}


# ---------------------------------------------------------------------------
# Budget Tier - unchanged cost-ladder logic from the pre-v11 system (this is
# the "Do not change the existing tier logic" requirement). Controls material/
# furniture/finish quality and decorative richness, independent of Style/Palette.
# ---------------------------------------------------------------------------

TIER_LABELS = {
    "economical": "budget renovation",
    "mid": "mid-level renovation",
    "premium": "premium luxury renovation",
}

TIER_MATERIAL_QUALITY = {
    "economical": "affordable, durable materials with simple, honest finishes - no premium or luxury materials",
    "mid": "good-quality materials with refined, well-made finishes",
    "premium": "premium, high-end materials with luxurious, meticulously detailed finishes",
}

TIER_LIGHTING_TEMP = {
    "economical": "cool white practical lighting, 5000-6000K",
    "mid": "neutral warm lighting, 3500-4000K",
    "premium": "warm luxury lighting, 2700-3000K, layered recessed and cove fixtures",
}

# Real bug fixed here: "warm gold-lit trim" injected a literal COLOR word
# (gold) into a Budget Tier sentence - this module's own stated design
# principle (see the module docstring's "STYLE_PROFILES intentionally carry
# NO color words - colors live only in COLOR_PROFILE" rule, which this
# dict silently violated) means every palette's color instructions have to
# fight that "gold" mention at the premium tier specifically, regardless of
# which palette is chosen - a real, findable contributor to a user-reported
# "Earthy doesn't look right" complaint (Earthy's clay/terracotta/olive
# palette has nothing to do with gold). Fixed by describing the ceiling's
# STRUCTURE/TREATMENT only (multi-layer cove, warm-toned trim as a light
# quality, not a hue) and leaving the actual color entirely to COLOR_PROFILE.
TIER_CEILING = {
    "economical": "a plain flat white ceiling, no false ceiling, a simple ceiling fan",
    "mid": "a false ceiling with a warm cove lighting strip",
    "premium": "a designer multi-layer cove ceiling with warm, richly-lit trim",
}

TIER_DENSITY = {
    "economical": "sparse furniture, about 80 percent of floor space left empty, uncluttered and clean",
    "mid": "a balanced furniture arrangement, tidy and comfortable, not crowded",
    "premium": "furniture arranged in curated symmetrical conversation zones, restrained, not overfilled",
}

# Whether this tier can afford a decorative feature/accent wall at all - a real
# cost-ladder rule, not a style choice: economical never gets one regardless of
# which Style is selected.
TIER_FEATURE_WALL_AVAILABLE = {
    "economical": False,
    "mid": True,
    "premium": True,
}

# Restated AFTER ALL vivid design vocabulary - specifically after the Controlled
# Randomness furnishing sentences at the very end of build_prompt(), not earlier
# in the Budget Tier section - real, live-observed failure (both pre-v11, where
# this originally lived in the pipeline, and again after the v11 rewrite
# temporarily regressed the ordering): vivid style/tier/furniture vocabulary
# (marble, brass, "designer ceiling", a whole furnished-room description) pulled
# gpt-image-1 toward a hallucinated generic room even with the structural lock
# present earlier in the prompt - a deep/vast room (e.g. a large industrial hall)
# came back shallow and ordinary-sized once furnished with everyday living-room
# items. Originally only mid/premium got this (reasoned as "only tiers that rework
# the ceiling plane need it"), but that reasoning didn't hold - a real generation
# showed the SAME depth collapse on economical too, so every tier now gets a
# reminder; economical's is phrased around depth/proportions generically instead
# of reusing the ceiling-focused mid/premium wording verbatim.
TIER_STRUCTURE_REMINDER = {
    "economical": (
        "still the exact same original room - the same depth, scale, and proportions, "
        "not a smaller, shallower, or more compact version of the space"
    ),
    "mid": "still the same original room shape and window, do not enlarge or change the space",
    "premium": "still the same original room shape and window, do not enlarge or change the space",
}

# Flooring stays tier-level (cost-ladder driven), not style-level: any style at
# the economical tier realistically uses similarly cheap flooring - the material
# COST scales with tier, not with aesthetic.
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

# Real user report: Industrial (a style whose entire identity - see
# STYLE_PROFILES/STYLE_ELEMENT_POOLS above - is concrete, exposed metal,
# brick, raw materials) "doesn't look right", especially at the premium
# tier. Root cause: TIER_MATERIAL_QUALITY/TIER_CEILING/TIER_FLOORING above
# assume a generic "premium = polished marble, gold-lit cove ceiling,
# travertine/walnut flooring" aesthetic - the opposite of Industrial's raw
# identity - so the Budget Tier section (which comes right after Style in
# PROMPT PRIORITY) actively fights the style the user chose. These
# Industrial-specific overrides keep the EXACT SAME quality ladder
# (premium still reads as more expensive/refined than mid, mid more than
# economical) but express it in materials congruent with Industrial's own
# character - polished/sealed concrete, blackened or brushed steel, exposed
# structural elements - instead of vocabulary that reads as a different
# style entirely. Deliberately scoped to ONLY Industrial (not a general
# per-style system for all 9 styles) since that's the one style whose
# identity is fundamentally incompatible with the generic tier vocabulary -
# the other 8 styles' generic "premium" reading (refined/polished
# materials) doesn't contradict their own identity the way it does here.
INDUSTRIAL_TIER_MATERIAL_QUALITY = {
    "economical": (
        "affordable, durable raw-industrial materials with simple, honest finishes - "
        "painted metal, basic sealed concrete - no premium or luxury materials"
    ),
    "mid": "good-quality industrial materials with refined raw finishes - sealed concrete, brushed steel, reclaimed wood",
    "premium": (
        "premium industrial materials with meticulously finished raw surfaces - polished concrete, "
        "blackened steel, sealed reclaimed wood - luxurious in quality and craftsmanship, not in ornament"
    ),
}

INDUSTRIAL_TIER_CEILING = {
    "economical": "a plain flat ceiling with visible conduit, a simple ceiling fan",
    "mid": "an exposed structural ceiling with visible ductwork and track lighting",
    "premium": (
        "an intentionally exposed structural ceiling with blackened steel beams and visible ductwork, "
        "softly uplit with designer track or pendant fixtures"
    ),
}

INDUSTRIAL_TIER_FLOORING = {
    "economical": [
        "sealed basic concrete flooring",
        "affordable polished-concrete-look vinyl flooring",
    ],
    "mid": [
        "sealed concrete flooring",
        "large-format concrete-look tile flooring",
    ],
    "premium": [
        "premium polished concrete flooring",
        "large-format honed concrete-look porcelain flooring",
        "burnished concrete flooring with a high-end sealed finish",
    ],
}

# Tier-specific exclusions, rendered as a positive-prompt "Do not include: ..."
# sentence in build_prompt() - instruction-following models are built to follow
# exclusions stated directly in the instruction (unlike diffusion models, which
# need a separate negative-prompt channel; this codebase no longer has one). This
# is what actually enforces the cost ceiling even when a user pairs an expensive-
# reading Style (e.g. "Luxury") with the economical tier - Style still governs
# FORM/LAYOUT language, but the tier's "Do not include" sentence is placed right
# after the Style section specifically to override any conflicting material
# vocabulary before the rest of the prompt continues.
NEGATIVE_ADDITIONS: dict[str, str] = {
    "economical": "chandelier, crystal chandelier, pendant light, gold trim, marble, luxury, ornate, wainscoting",
    "mid": "chandelier, crystal chandelier, gold trim, marble, ornate luxury details",
    "premium": "",
}


# ---------------------------------------------------------------------------
# Controlled Randomness - per-style option pools for the things this project's
# spec explicitly calls out as safe to randomize (furniture pieces, tables,
# chairs, rugs, artwork, mirrors, plants, lighting fixtures, fabrics, decorative
# accessories). Deliberately colorless (color comes only from COLOR_PROFILE) and
# deliberately silent on quality/material grade (that comes only from
# TIER_MATERIAL_QUALITY/TIER_FLOORING) - each pool entry is pure FORM/TYPE
# vocabulary for its style, so randomizing within a pool can never accidentally
# cross the Style or Budget Tier boundary.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleElementPool:
    seating: list[str]  # sofas / lounge seating
    coffee_tables: list[str]
    dining_tables: list[str]
    chairs: list[str]  # accent/dining chairs
    beds: list[str]  # used ONLY when the room is a bedroom - see build_prompt()
    rugs: list[str]
    artwork: list[str]
    mirrors: list[str]
    plants: list[str]
    lighting: list[str]  # fixture TYPE; color temperature stays tier-level
    fabrics: list[str]  # upholstery/textile material variations
    accessories: list[str]  # decorative objects


STYLE_ELEMENT_POOLS: dict[str, StyleElementPool] = {
    "Modern": StyleElementPool(
        seating=["a low-profile sofa with tapered metal legs", "a streamlined sectional with clean, straight lines"],
        coffee_tables=["a sculptural glass-and-metal coffee table", "a low rectangular coffee table with a lacquered top"],
        dining_tables=["a streamlined dining table with a lacquered finish", "a rectangular dining table with slim metal legs"],
        chairs=["slim-armed accent chairs with a molded shell design", "dining chairs with a simple cantilevered frame"],
        beds=["a low-profile platform bed with a sleek upholstered headboard", "a minimalist bed frame with clean geometric lines and a wide flat headboard"],
        rugs=["a low-pile rug with a subtle geometric weave", "a flatweave rug with clean linear patterning"],
        artwork=["a large abstract canvas in a slim frame", "a set of minimalist line-art prints"],
        mirrors=["a frameless floating mirror", "a slim metal-framed rectangular mirror"],
        plants=["a single sculptural potted plant in a cylindrical planter", "a small cluster of potted succulents"],
        lighting=["a sleek linear pendant fixture", "recessed track lighting with slim fixtures", "a minimalist sculptural floor lamp"],
        fabrics=["a smooth performance-weave upholstery", "a matte bouclé upholstery"],
        accessories=["a set of sculptural ceramic objects", "a minimalist geometric vase"],
    ),
    "Minimalist": StyleElementPool(
        seating=["a low, armless sofa in a single solid form", "a compact sofa with no visible ornamentation"],
        coffee_tables=["a simple slab-style coffee table", "a low cube-shaped side table"],
        dining_tables=["an unadorned rectangular dining table", "a simple round dining table on a single pedestal"],
        chairs=["backless stools in a single material", "simple armless dining chairs with no pattern"],
        beds=["a simple platform bed with no headboard, just a low frame", "an unadorned low bed frame in a single solid material"],
        rugs=["a plain low-pile rug in a single tone", "a simple flatweave rug with no pattern"],
        artwork=["a single small framed print", "one understated abstract piece"],
        mirrors=["a simple unframed mirror", "a thin rectangular mirror with no ornamentation"],
        plants=["one small potted plant in a plain ceramic pot", "a single minimal cactus in a simple planter"],
        lighting=["a single unobtrusive pendant", "a plain flush-mount ceiling light", "a slim architectural floor lamp"],
        fabrics=["a plain matte cotton upholstery", "a smooth solid-weave fabric"],
        accessories=["a single ceramic bowl", "one small sculptural object, kept minimal"],
    ),
    "Scandinavian": StyleElementPool(
        seating=["a light-wood-framed sofa with soft upholstery", "a simple sofa with slim tapered wooden legs"],
        coffee_tables=["a round light-oak coffee table", "a simple pale-wood coffee table with turned legs"],
        dining_tables=["a pale birch dining table", "a simple light-wood dining table with rounded edges"],
        chairs=["a simple wishbone-style dining chair set", "light-wood chairs with a woven cord seat"],
        beds=["a light-wood bed frame with a simple slatted headboard", "a pale birch platform bed with rounded edges"],
        rugs=["a soft wool rug with a light woven texture", "a simple flatweave rug in a natural fiber"],
        artwork=["a set of simple framed graphic prints", "a single botanical print in a light wood frame"],
        mirrors=["a round mirror in a light wood frame", "an oval mirror with a slim pale frame"],
        plants=["a few well-placed potted plants in simple ceramic pots", "a tall leafy plant in a woven basket planter"],
        lighting=["a simple globe pendant", "a slim floor lamp with a fabric shade", "a woven paper lantern fixture"],
        fabrics=["a soft brushed wool upholstery", "a natural linen-cotton blend"],
        accessories=["a collection of simple handmade ceramics", "a woven basket used as a decorative accent"],
    ),
    "Japandi": StyleElementPool(
        seating=["a low-profile oak-framed sofa with tapered wood legs", "a curved bouclé accent sofa"],
        coffee_tables=["a minimalist ash coffee table", "a low rounded-edge wood coffee table"],
        dining_tables=["a platform-style dining table in natural wood", "a simple oak dining table with rounded corners"],
        chairs=["a curved bouclé accent chair", "simple wood-framed dining chairs with a woven seat"],
        beds=["a low platform bed in natural oak with a slim wood headboard", "a minimalist tatami-style low bed frame in light wood"],
        rugs=["a jute or wool-blend rug with a natural weave", "a low-pile rug in a raw fiber texture"],
        artwork=["a single dried-branch arrangement as a wall accent", "a minimalist ink-wash style print"],
        mirrors=["a round mirror in a thin natural wood frame", "a simple unlacquered wood-framed mirror"],
        plants=["a single sculptural plant in a handmade ceramic planter", "a small bonsai-style potted plant"],
        lighting=["a paper pendant lantern", "a slim linear recessed light", "a simple ceramic table lamp"],
        fabrics=["a raw linen upholstery", "a nubby natural-fiber weave"],
        accessories=["handmade pottery bowls in muted tones", "a small collection of natural stone objects"],
    ),
    "Industrial": StyleElementPool(
        seating=["a low leather sofa with exposed metal framing", "a boxy sofa with visible rivet detailing"],
        coffee_tables=["a reclaimed-wood-and-metal coffee table", "a low coffee table on a raw steel-pipe frame"],
        dining_tables=["a metal-framed dining table with a raw wood top", "a dining table with a concrete-look top and metal legs"],
        chairs=["metal-framed stools with a worn leather seat", "dining chairs with a bent-metal frame"],
        beds=["a metal-framed bed with a raw iron headboard", "a bed frame with exposed metal piping and a reclaimed-wood headboard"],
        rugs=["a flatweave rug in a raw natural fiber", "a low-pile rug with a distressed pattern"],
        artwork=["a large-scale black-and-white photograph", "a metal-framed graphic print"],
        mirrors=["a round mirror in a matte black metal frame", "a mirror with an exposed rivet-style frame"],
        plants=["a tall plant in a raw metal planter", "a potted plant in a concrete-look pot"],
        lighting=["an Edison-style bulb pendant", "a matte black pipe-frame floor lamp", "a caged wall sconce"],
        fabrics=["a worn-look leather upholstery", "a heavy canvas weave"],
        accessories=["reclaimed wood decorative crates", "a set of matte metal decorative objects"],
    ),
    "Mediterranean": StyleElementPool(
        seating=["a rustic wood-framed sofa with woven detailing", "a low sofa with a linen-blend upholstery over a carved wood frame"],
        coffee_tables=["a hand-carved wood coffee table", "a low coffee table with a mosaic-tiled top"],
        dining_tables=["a rustic wood dining table with turned legs", "a dining table with a hand-finished wood top"],
        chairs=["wrought-iron-framed dining chairs with woven seats", "rustic wood dining chairs with a rush-woven seat"],
        beds=["a rustic wood bed frame with a hand-carved headboard", "a wrought-iron bed frame with a scrolled headboard"],
        rugs=["a hand-woven textured rug", "a flatweave rug with a traditional motif"],
        artwork=["a hand-painted ceramic wall plate display", "a framed botanical or coastal-inspired print"],
        mirrors=["a mirror with an arched wrought-iron frame", "a mirror framed in hand-carved wood"],
        plants=["a potted olive- or citrus-style plant in a terracotta pot", "trailing greenery in a glazed ceramic planter"],
        lighting=["a wrought-iron lantern-style pendant", "a hand-blown glass table lamp", "a wall sconce with a wrought-iron frame"],
        fabrics=["a woven natural-fiber upholstery", "a textured linen blend with hand-loomed detailing"],
        accessories=["hand-painted decorative tile accents", "a collection of glazed ceramic vessels"],
    ),
    "Spanish": StyleElementPool(
        seating=["a rustic wood-framed sofa with turned wood legs", "a low sofa with leather-and-linen upholstery"],
        coffee_tables=["a hand-carved wood coffee table", "a low coffee table with a wrought-iron base"],
        dining_tables=["a rustic wood dining table with wrought-iron accents", "a dark-wood dining table with hand-carved legs"],
        chairs=["high-back wood dining chairs with leather seats", "dining chairs with a hand-carved wood frame"],
        beds=["a dark-wood bed frame with a hand-carved headboard", "a wrought-iron bed frame with an ornate scrolled headboard"],
        rugs=["a hand-woven rug with a traditional pattern", "a textured flatweave rug"],
        artwork=["a decorative tile mosaic wall accent", "a framed piece with a traditional motif"],
        mirrors=["a mirror framed in hand-carved dark wood", "a mirror with an ornate wrought-iron surround"],
        plants=["a potted plant in a hand-painted terracotta pot", "trailing greenery in a glazed talavera-style planter"],
        lighting=["a wrought-iron chandelier-style pendant", "a hand-forged wall sconce", "a ceramic table lamp with a hand-painted base"],
        fabrics=["a textured woven upholstery", "a heavy embroidered fabric accent"],
        accessories=["hand-painted decorative tiles as accents", "a collection of glazed terracotta pottery"],
    ),
    "Traditional": StyleElementPool(
        seating=["a tailored roll-arm sofa with a classic silhouette", "a skirted sofa with a formal, tailored shape"],
        coffee_tables=["a carved wood coffee table", "a coffee table with turned legs and a wood-veneer top"],
        dining_tables=["a formal wood dining table with detailed legs", "a dining table with a traditional pedestal base"],
        chairs=["a set of classic wingback-inspired dining chairs", "dining chairs with a carved wood frame and upholstered seat"],
        beds=["a tailored upholstered bed with a tall button-tufted headboard", "a classic wood sleigh bed frame"],
        rugs=["a patterned wool-blend rug", "a rug with a classic border motif"],
        artwork=["a curated pair of classic framed artworks", "a formal gallery-style art arrangement"],
        mirrors=["a mirror in a detailed carved wood frame", "an oval mirror with a classic moulded frame"],
        plants=["a formal potted plant in a classic urn-style planter", "a well-manicured potted arrangement"],
        lighting=["a classic drum-shade pendant", "a pair of fabric-shade table lamps", "a traditional multi-arm ceiling fixture"],
        fabrics=["a fine tailored upholstery fabric", "a subtly patterned woven fabric"],
        accessories=["a curated collection of classic decorative objects", "a formal display of framed keepsakes"],
    ),
    "Luxury": StyleElementPool(
        seating=["a tailored velvet sofa with sculptural legs", "a low sofa upholstered in a rich fabric with a sculptural frame"],
        coffee_tables=["a polished stone-top coffee table", "a coffee table with a sculptural metal base and glass top"],
        dining_tables=["a designer dining table with a sculptural base", "a dining table with a polished stone top"],
        chairs=["a set of upholstered dining chairs with metal detailing", "dining chairs with a sculptural frame and plush upholstery"],
        beds=["a tailored upholstered bed with a tall channel-tufted velvet headboard", "a designer bed frame with a sculptural upholstered headboard"],
        rugs=["a hand-knotted wool rug", "a plush high-pile designer rug"],
        artwork=["a curated large-scale statement art piece", "a gallery-quality framed artwork"],
        mirrors=["a large gilt-framed mirror", "a sculptural designer mirror"],
        plants=["a large statement plant in a designer planter", "a curated fresh floral arrangement"],
        lighting=["a sculptural crystal chandelier", "a pair of designer table lamps", "layered architectural cove lighting"],
        fabrics=["a plush velvet upholstery", "a fine silk-blend fabric"],
        accessories=["a curated sculptural art object", "a collection of designer decorative accessories"],
    ),
}


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
    "and do not add dining or kitchen items to a bedroom. If this room is a bedroom, "
    "do not add a dining table, coffee table, or dining chairs under any circumstance - "
    "instead make sure it includes a proper, well-made bed appropriate to the "
    "renovation tier, with real bedding, and keep the room functioning as a bedroom. "
    "Do not change what kind of room or space this is - a bedroom must stay a bedroom, "
    "a living room must stay a living room, and so on."
)

# Nudges toward real variety across repeated generations of the same room/style.
# Can't literally know what a previous generation looked like (no new pipeline/DB
# state - out of scope for this change), so this leans on the model's own creative
# judgement plus the fact that the Controlled Randomness section already makes a
# structurally different result likely on its own.
DIVERSITY_INSTRUCTION = (
    "Design this as a completely fresh interior concept within the given style - "
    "avoid a generic or predictable look, and avoid repeating the same furniture "
    "arrangement you might use for a typical version of this room. Make deliberate, "
    "specific design choices, as a real interior designer would for a one-of-a-kind "
    "project."
)

_DAMAGE_REPAIR_INSTRUCTION = (
    "Repair and clean any damage, stains, cracks, mould, or debris visible in the "
    "original photo. The result must look fully renovated and move-in ready, never "
    "like the original's condition."
)


ADDITIONAL_INSTRUCTIONS_MAX_CHARS = 150
# Kept as an alias - app/main.py and existing callers import this name.
USER_NOTES_MAX_CHARS = ADDITIONAL_INSTRUCTIONS_MAX_CHARS


def build_prompt(
    tier: str,
    style: str,
    palette: str,
    room_description: str | None = None,
    tier_note: str | None = None,
    additional_instructions: str | None = None,
) -> str:
    """Compose a natural-language EDIT instruction for the given tier/style/palette.

    "Random within boundaries": style and palette are REQUIRED, explicit user
    choices (not randomized) - they're the design's fixed identity for this
    generation. Only the individual furniture pieces/fixtures/textiles/decor
    (STYLE_ELEMENT_POOLS[style]) are randomized per call, via random.choice() on
    each category, so two generations of the same room/style/palette/tier still
    produce genuinely different (but always on-style) results. Flooring and
    lighting color-temperature stay tier-level (TIER_FLOORING/TIER_LIGHTING_TEMP)
    since they follow the real cost ladder, not aesthetic style.

    Sentence order follows the PROMPT PRIORITY documented in this module's
    docstring: Geometry -> Style -> Tier -> Palette -> Additional Instructions ->
    Controlled Randomness. Nothing lower in that order may override anything
    higher - concretely, the Tier section's "Do not include" exclusion sentence is
    placed immediately after the Style section so it can override any conflicting
    material vocabulary a "premium-reading" style (e.g. Luxury) might introduce
    before the economical/mid cost ceiling is stated.

    tier_note is a short, room-specific instruction from Provider.generate_tier_notes()
    (e.g. "repaint over visible water stains on the ceiling"). additional_instructions
    is optional free text the user typed in (static/index.html's "Additional
    Instructions" textarea) - capped at ADDITIONAL_INSTRUCTIONS_MAX_CHARS and
    re-truncated here defensively (also enforced in app/main.py) since a client
    could call the API directly with arbitrary length text. It is framed as a
    refinement of the chosen style, not a replacement - if it conflicts with the
    style, the style wins (see the sentence wording below).
    """
    if tier not in TIER_LABELS:
        raise ValueError(f"unknown tier: {tier}")
    if style not in STYLE_PROFILES:
        raise ValueError(f"unknown style: {style}")
    if palette not in COLOR_PROFILE:
        raise ValueError(f"unknown palette: {palette}")

    label = TIER_LABELS[tier]
    pool = STYLE_ELEMENT_POOLS[style]

    # ---- 1. Preserve Room Geometry ----
    sentences = [
        f"Edit this photograph of a real room into a completely reimagined {label}, "
        f"designed in a {style} interior design style - as if a professional "
        "interior designer redesigned this exact space from scratch. The output must "
        "be the same physical room, clearly recognizable.",
        PRESERVE_STRUCTURE,
        ROOM_TYPE_COMMON_SENSE,
    ]
    if room_description:
        sentences.append(room_description)

    # ---- 2. Interior Style ----
    sentences.append(f"Design direction for the {style} style: {STYLE_PROFILES[style]}")
    sentences.append(
        "Creatively redesign the interior: vary the furniture, furniture layout, "
        "lighting, rugs, curtains, wall art, mirrors, plants, decor, textures, "
        "fabrics, and accessories freely within this style, while keeping the "
        "room's geometry, walls, doors, windows, and camera angle exactly as they are."
    )

    # ---- 3. Budget Tier ----
    # Industrial gets its own tier vocabulary (material quality/ceiling/
    # flooring) - see INDUSTRIAL_TIER_*'s module-level comment for why: the
    # generic tier vocabulary assumes a polished/luxury aesthetic that
    # directly fights Industrial's raw-materials identity.
    if style == "Industrial":
        material_quality = INDUSTRIAL_TIER_MATERIAL_QUALITY[tier]
        ceiling = INDUSTRIAL_TIER_CEILING[tier]
        flooring_options = INDUSTRIAL_TIER_FLOORING[tier]
    else:
        material_quality = TIER_MATERIAL_QUALITY[tier]
        ceiling = TIER_CEILING[tier]
        flooring_options = TIER_FLOORING[tier]

    flooring = random.choice(flooring_options)
    sentences.append(f"Materials and finishes should reflect a {label}: {material_quality}.")
    sentences.append(f"Install {flooring}.")
    sentences.append(f"Use {TIER_LIGHTING_TEMP[tier]} throughout the space.")
    sentences.append(f"For the ceiling, use {ceiling}.")
    if TIER_FEATURE_WALL_AVAILABLE[tier]:
        sentences.append(f"Include a feature wall styled to match the {style} design direction.")
    sentences.append(f"Furniture density: {TIER_DENSITY[tier]}.")
    exclusions = NEGATIVE_ADDITIONS[tier]
    if exclusions:
        sentences.append(f"Do not include: {exclusions}.")

    # ---- 4. Color Palette ----
    sentences.append(
        f"Color palette: use {COLOR_PROFILE[palette]} across the walls, furniture "
        "upholstery, curtains, rugs, accessories, accent colors, and flooring tones "
        "where appropriate, without changing the selected style above."
    )

    if tier_note:
        sentences.append(tier_note)

    # ---- 5. Additional Instructions (optional refinement, style always wins) ----
    if additional_instructions:
        cleaned = additional_instructions.strip()[:ADDITIONAL_INSTRUCTIONS_MAX_CHARS]
        if cleaned:
            sentences.append(
                f"Additional refinement from the user (apply it only in a way that stays "
                f"true to the {style} style above; if it ever conflicts with the style, "
                f"the style wins and the instruction should be adapted, not followed "
                f"literally): {cleaned}"
            )

    # ---- 6. Controlled Randomness ----
    seating = random.choice(pool.seating)
    coffee_table = random.choice(pool.coffee_tables)
    dining_table = random.choice(pool.dining_tables)
    chair = random.choice(pool.chairs)
    bed = random.choice(pool.beds)
    rug = random.choice(pool.rugs)
    artwork = random.choice(pool.artwork)
    mirror = random.choice(pool.mirrors)
    plant = random.choice(pool.plants)
    fixture = random.choice(pool.lighting)
    fabric = random.choice(pool.fabrics)
    accessory = random.choice(pool.accessories)

    # Explicitly branched on room type (the image model judges this from the
    # photo itself, same as ROOM_TYPE_COMMON_SENSE above) rather than always
    # suggesting a sofa/coffee-table/dining-table combo - that combo makes no
    # sense in a bedroom and previously contradicted ROOM_TYPE_COMMON_SENSE's
    # "do not add dining items to a bedroom" instruction with no bed
    # alternative offered in its place.
    sentences.append(
        f"If this room is a bedroom, furnish it with {bed}, along with appropriate "
        "nightstands and bedroom textiles, and do NOT add a dining table, coffee "
        f"table, or dining chairs. Otherwise, furnish the room with {seating} and "
        f"{coffee_table}, plus, if the space calls for it, {dining_table} with {chair}."
    )
    sentences.append(
        f"Light the room with {fixture}, and add {rug} on the floor, {artwork} as wall "
        f"decor, {mirror}, {plant}, and {accessory} as decorative accents."
    )
    sentences.append(f"Use {fabric} for upholstery and soft furnishings.")

    # Restated here, AFTER every piece of vivid style/material/furniture
    # vocabulary in the prompt (not earlier, in the Budget Tier section) - see
    # TIER_STRUCTURE_REMINDER's module-level comment for the real drift failure
    # this ordering fixes.
    sentences.append(f"Even with all of these design choices, this is {TIER_STRUCTURE_REMINDER[tier]}.")

    sentences.append(DIVERSITY_INSTRUCTION)
    sentences.append(_DAMAGE_REPAIR_INSTRUCTION)

    return " ".join(sentences)


def build_tier_spec(tier: str, style: str, palette: str) -> dict[str, str]:
    """A STABLE (non-randomized) per-(tier, style, palette) summary consumed only
    by app/providers/gemini.py's materials/pricing feature (generate_materials,
    fallback_materials, _tier_line_items) - NOT used by build_prompt() and never
    appears in an image-generation prompt. Materials pricing wants a consistent,
    representative item description for SerpApi search terms, not per-call
    jitter, so this deliberately picks the FIRST option from each relevant pool
    rather than random.choice() - unlike build_prompt(), which is intentionally
    randomized every call.

    Because style/palette are now real per-project user choices (not an
    internally-randomized concept), this also makes materials pricing more
    accurate than the pre-v11 system: the priced items now reflect the design
    the user actually asked for and will actually see rendered.
    """
    if tier not in TIER_LABELS:
        raise ValueError(f"unknown tier: {tier}")
    if style not in STYLE_PROFILES:
        raise ValueError(f"unknown style: {style}")
    if palette not in COLOR_PROFILE:
        raise ValueError(f"unknown palette: {palette}")

    pool = STYLE_ELEMENT_POOLS[style]
    palette_text = COLOR_PROFILE[palette]

    # Same Industrial override as build_prompt() - see INDUSTRIAL_TIER_*'s
    # module-level comment. Materials pricing must describe what's actually
    # rendered (marble pricing for a room the image model was told to render
    # in polished concrete would be actively wrong), not just what a generic
    # tier would normally use.
    if style == "Industrial":
        ceiling = INDUSTRIAL_TIER_CEILING[tier]
        materials_quality = INDUSTRIAL_TIER_MATERIAL_QUALITY[tier]
        flooring = INDUSTRIAL_TIER_FLOORING[tier][0]
    else:
        ceiling = TIER_CEILING[tier]
        materials_quality = TIER_MATERIAL_QUALITY[tier]
        flooring = TIER_FLOORING[tier][0]

    return {
        "label": f"{TIER_LABELS[tier]}, {style} style",
        "lighting_temp": TIER_LIGHTING_TEMP[tier],
        "ceiling": ceiling,
        "feature_wall": (
            f"a {style} feature wall in {palette_text}" if TIER_FEATURE_WALL_AVAILABLE[tier] else ""
        ),
        "materials": f"{materials_quality}, {style} style",
        "density": TIER_DENSITY[tier],
        "structure_reminder": TIER_STRUCTURE_REMINDER[tier],
        "paint": palette_text,
        "flooring": flooring,
        "palette": palette_text,
        "decor": pool.accessories[0],
    }


# ---------------------------------------------------------------------------
# KAGGLE MODEL PROMPT - a completely SEPARATE, SHORT prompt for the user's own
# fine-tuned image model (app/providers/kaggle.py), built alongside build_prompt()
# above WITHOUT modifying it in any way. build_prompt() (~3500-4000 characters,
# ~550-650 words) is written for gpt-image-1, which has no meaningful prompt
# length limit - the Kaggle model is a classic CLIP-conditioned model with a
# hard ~77-TOKEN limit on its prompt (and, separately, on its negative_prompt -
# see KaggleImageProvider). Sending it build_prompt()'s full output would just
# get silently truncated by CLIP partway through a sentence, losing whatever
# came after token 77 - almost certainly including the palette/furniture
# content near the end.
#
# Per the user (this model was trained specifically on layout/geometry
# preservation): PRESERVE_STRUCTURE / ROOM_TYPE_COMMON_SENSE's geometry
# clauses / TIER_STRUCTURE_REMINDER are deliberately OMITTED here entirely -
# redundant token spend on something the model already does on its own. Token
# budget instead goes to what the model can't infer from the photo alone:
# style, tier quality, palette, and a few concrete furniture pieces. The one
# exception is the bedroom/no-table rule (see ROOM_TYPE_COMMON_SENSE's own
# comment for the real failure that motivated it) - kept as a short 5-word
# hint since it's a content-correctness rule, not a geometry-preservation one.
#
# Format is short comma-separated keyword phrases, not natural-language
# sentences - unlike gpt-image-1 (an instruction-following editor, where that
# format was a proven real failure - see this module's top docstring), a
# classic CLIP text encoder is trained on exactly this kind of caption, so
# keyword phrasing is the CORRECT format for this different model, not a
# regression back to the old mistake.
#
# WORD-count budgeted, not TOKEN-count budgeted - no CLIP tokenizer is
# installed in this project (would mean pulling in `transformers`, a large
# dependency, just for this one estimate). ~45 words is a conservative proxy
# for staying under ~77 CLIP BPE tokens (English text runs roughly 1.3-1.5
# tokens/word, and multi-syllable design terms like "upholstery" or
# "wainscoting" often split into 2+ subword tokens) - if real generations
# still show truncation artifacts, tighten KAGGLE_PROMPT_MAX_WORDS further.
# ---------------------------------------------------------------------------

KAGGLE_PROMPT_MAX_WORDS = 45

# Short paraphrase of TIER_MATERIAL_QUALITY above, purely for word-budget
# reasons - the full sentence ("affordable, durable materials with simple,
# honest finishes - no premium or luxury materials") is itself already
# several times longer than this whole function's total word budget.
TIER_QUALITY_TAG = {
    "economical": "affordable simple finishes",
    "mid": "good quality refined finishes",
    "premium": "premium luxury finishes",
}

# Matches the server's own stated default negative_prompt (read from this
# project's own live /openapi.json - see app/providers/kaggle.py's module
# docstring) - restated explicitly here (not left to the server default)
# because build_kaggle_negative_prompt() below needs to ADD tier-specific
# exclusions on top of it for economical/mid, and sending our own
# negative_prompt value REPLACES the server's default rather than merging
# with it.
KAGGLE_DEFAULT_NEGATIVE_PROMPT = "ugly, low quality, distorted, blurry, bad architecture"


def _short_style_keywords(style: str, segments: int = 3) -> str:
    """First few comma-separated phrases from STYLE_PROFILES[style]. That text
    is already short keyword-style phrasing (e.g. "Clean lines, sleek
    furniture, uncluttered spaces, ...") - this truncates it rather than
    hand-maintaining a second, separate style vocabulary that could drift out
    of sync with STYLE_PROFILES over time.
    """
    parts = [p.strip().rstrip(".") for p in STYLE_PROFILES[style].split(",")]
    return ", ".join(parts[:segments])


def build_kaggle_prompt(tier: str, style: str, palette: str) -> str:
    """SHORT, keyword-style prompt for KaggleImageProvider - see this
    section's module-level comment above for the full "why" (77-token CLIP
    limit, no geometry-preservation text, keyword phrasing). Completely
    independent of build_prompt() - does not call it, parse it, or share any
    mutable state with it.

    Same "random within boundaries" contract: style/palette/tier are fixed
    inputs, one furniture piece is randomized from the chosen style's own
    STYLE_ELEMENT_POOLS (only one, not every category, to fit the word
    budget) - so repeated calls with the same inputs still vary slightly.

    Builds a priority-ordered list of phrases (most important first) and
    drops from the END if the result is still over KAGGLE_PROMPT_MAX_WORDS -
    so if anything has to be cut to fit, it's the lowest-priority content
    (the closing quality tag), never the style/tier/palette identity.
    """
    if tier not in TIER_LABELS:
        raise ValueError(f"unknown tier: {tier}")
    if style not in STYLE_PROFILES:
        raise ValueError(f"unknown style: {style}")
    if palette not in COLOR_PROFILE:
        raise ValueError(f"unknown palette: {palette}")

    pool = STYLE_ELEMENT_POOLS[style]
    furniture = random.choice(pool.seating)
    # Same Industrial override as build_prompt()/build_tier_spec() - see
    # INDUSTRIAL_TIER_*'s module-level comment. "premium marble flooring"
    # for an Industrial room fights the style's raw-materials identity here
    # too, in this completely independent prompt path.
    flooring_options = INDUSTRIAL_TIER_FLOORING[tier] if style == "Industrial" else TIER_FLOORING[tier]

    parts = [
        f"{style} style {TIER_LABELS[tier]} interior",
        _short_style_keywords(style),
        TIER_QUALITY_TAG[tier],
        COLOR_PROFILE[palette],
        random.choice(flooring_options),
        furniture,
        "bedroom: use a bed, never a table",
        "clean renovated interior, photorealistic",
    ]

    prompt = ", ".join(parts)
    while len(prompt.split()) > KAGGLE_PROMPT_MAX_WORDS and len(parts) > 1:
        parts.pop()
        prompt = ", ".join(parts)
    return prompt


def build_kaggle_negative_prompt(tier: str) -> str:
    """Short negative-prompt equivalent of build_prompt()'s "Do not include"
    exclusion sentence (NEGATIVE_ADDITIONS) - kept OUT of build_kaggle_prompt()'s
    already-tight positive-prompt word budget entirely, since the Kaggle
    model's API exposes a genuinely separate negative_prompt field (unlike
    gpt-image-1, which has no negative-prompt channel at all - see this
    module's top docstring). CLIP-style models tokenize prompt and
    negative_prompt separately, each with their own ~77-token budget, so
    exclusions belong here, not crammed into the positive prompt.
    """
    if tier not in TIER_LABELS:
        raise ValueError(f"unknown tier: {tier}")
    exclusions = NEGATIVE_ADDITIONS[tier]
    if not exclusions:
        return KAGGLE_DEFAULT_NEGATIVE_PROMPT
    return f"{KAGGLE_DEFAULT_NEGATIVE_PROMPT}, {exclusions}"


_STYLE_FROM_PROMPT_RE = re.compile(r"designed in an? (.+?) interior design style")


def extract_style_and_palette(prompt: str) -> tuple[str | None, str | None]:
    """Best-effort reverse-lookup of which Style/Palette a FULL build_prompt()
    output was built with, WITHOUT build_prompt() needing to expose them
    separately and WITHOUT changing the Provider.generate_image(image_bytes,
    prompt, tier) interface (which only ever receives the already-assembled
    prompt string, not the original style/palette arguments) - see
    KaggleImageProvider.generate_image(), the only caller.

    Reliable, not a guess: style is stated literally and unambiguously in
    build_prompt()'s edit-framing sentence ("designed in a {style} interior
    design style") - extracted via regex and validated against STYLE_OPTIONS.
    Palette has no name written anywhere in the prompt (only its color WORDS
    do, via COLOR_PROFILE[palette]), so this checks which COLOR_PROFILE value
    is present verbatim in the text instead - exact, not fuzzy, since that
    text is inserted unmodified by build_prompt().

    Returns (None, None) (or a partial pair) if either can't be determined -
    callers must treat this as best-effort, same contract as this module's
    other "may not have real data" extractors, and fall back to something
    else rather than raising.
    """
    style_match = _STYLE_FROM_PROMPT_RE.search(prompt)
    style = style_match.group(1) if style_match else None
    if style not in STYLE_PROFILES:
        style = None

    palette = None
    for key, text in COLOR_PROFILE.items():
        if text in prompt:
            palette = key
            break

    return style, palette
