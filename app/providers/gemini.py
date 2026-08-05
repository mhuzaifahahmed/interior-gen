import json
import logging
import re
import time
import urllib.parse
from io import BytesIO

from google import genai
from google.genai import errors as genai_errors
from PIL import Image

from app.config import settings
from app.providers import serpapi
from app.providers.base import Provider

logger = logging.getLogger(__name__)

# Retry knobs for the materials-synthesis Gemini call specifically. Real,
# live-observed reason: gemini-3.5-flash intermittently returns a 503
# ("currently experiencing high demand") - a transient Google-side overload,
# not a code bug (hit twice during this project's own testing). Without a
# retry, one transient 503 discarded 6 already-successful, real SerpApi
# searches and collapsed the whole tier straight to the generic fallback.
MATERIALS_GEMINI_MAX_ATTEMPTS = 3
MATERIALS_GEMINI_RETRY_DELAY_SECONDS = 2

# Last-resort fallback model, tried once if every retry above still 503s.
# Real, live-observed reason (not hypothetical): during a live diagnostic call
# on 2026-08-05, gemini-3.5-flash 503'd on all 3 attempts within seconds
# (a genuine Google-side outage on that specific model), while this model
# answered instantly on the same key at the same moment - a different model
# means different underlying capacity/load, so it's a real hedge against a
# single-model outage, not just a retry of the same failing thing.
# gemini-2.5-flash is NOT viable here - confirmed 404 "no longer available to
# new users" on this project's keys (see gemini_text_model's own comment).
MATERIALS_GEMINI_FALLBACK_MODEL = "gemini-flash-latest"

# MATERIALS PRICING DESIGN (real, live-tested reason this isn't Gemini's own
# Google Search grounding tool): grounding hit a 429 RESOURCE_EXHAUSTED wall on
# every Gemini key/project tested here, including brand-new ones with the
# documented free monthly allowance untouched. Google's own AI developer forum
# has multiple current reports of billing NOT fixing this (a platform-side bug,
# not user error). Instead: real SerpApi Google searches (app/providers/
# serpapi.py) supply real snippets/links, which a PLAIN (non-grounded, free)
# Gemini call then synthesizes into structured pricing.
#
# PER-ITEM search, not one combined search per tier (real, live-observed
# reason): an earlier version fired ONE combined query per tier (e.g. "price of
# grey ceramic tile, cream paint, cool lighting in Karachi"). A single query
# only ever returns real matches for whichever term Google treats as the
# primary e-commerce category - almost always flooring/tile - so 6+ of a
# tier's other items (ceiling treatment, feature wall, decor...) got zero real
# search coverage no matter how the prompt was worded, and came back as pure
# LLM guesses. Fix: each tier's items are now a FIXED, deterministic list (see
# _tier_line_items) with ONE DEDICATED search per item - a real, individually-
# targeted attempt at a source for every single item, not just the one term
# that happens to rank well. Cost: 6 searches/tier * 3 tiers = 18/generation,
# ~13-14 generations/month on SerpApi's 250/month free tier (down from ~83) -
# an explicit, accepted tradeoff (the user chose real-per-item coverage over
# more generations/month after seeing most items come back as guesses).
MATERIALS_PROMPT_TEMPLATE = (
    "You are pricing renovation materials for a {tier_label} interior-renovation concept, "
    "for a buyer located in {city}.\n\n"
    "{area_block}"
    "Price EXACTLY these items - do not add, skip, merge, or rename any of them:\n\n"
    "{items_block}\n\n"
    "A real web search was run SEPARATELY for each item above, specifically for that item - "
    "its own results are shown right below its name. For each item: if ITS OWN results mention "
    "a price, use that real price as the STARTING point (see the quantity rule below before "
    "treating it as the item's final price), set is_estimate to false, and set source_url to the "
    "EXACT link of the result it came from - copy it character-for-character, NEVER invent, "
    "guess, or modify a URL, and NEVER use a link that was shown under a DIFFERENT item. If "
    "that item's own results don't mention a usable price, give your own best rough local "
    "estimate (still applying the quantity rule below), set is_estimate to true, and set "
    "source_url to null - do not omit the item and do not leave its price blank either way.\n\n"
    "IMPORTANT - quantity/multiplication rule: a price found for Flooring, Ceiling treatment, "
    "or Paint / wall finish is very often quoted as a PER-UNIT rate (e.g. \"$12/sqft\", \"Rs. "
    "1,100 per square foot\", \"$8 per sq ft\") describing the cost to cover ONE square foot, "
    "not the cost to do the whole room. Whenever a found (or estimated) price for one of these "
    "three items is a per-unit rate, you MUST multiply it by the room's floor area (given below, "
    "or your own reasonable estimate if none is given) to get that item's real total price for "
    "the whole room - never report a bare per-sqft rate as if it were already the item's total "
    "cost. Show the calculation in that item's spec field (e.g. \"Ceramic tile, Rs. 1,100/sqft x "
    "~180 sqft\"). Lighting, Feature wall, and Decor are normally priced per fixture/piece, not "
    "by area - price those as typically sold, with no area multiplication.\n\n"
    "IMPORTANT - currency: always express every price (and the total) in the LOCAL currency "
    "actually used in {city} (e.g. PKR for Pakistan, INR for India, USD for the United States) - "
    "never a different country's currency, even if a source result quotes one. If a real "
    "source's price is in a different currency, convert it to {city}'s local currency using your "
    "best knowledge of exchange rates and state the converted amount - this does NOT make it an "
    "estimate (is_estimate still reflects whether a real reference price was found, not whether a "
    "currency conversion was applied). Also compute a rough total across all items as a plain "
    "string, in that same local currency - this total must be the sum of each item's ALREADY-"
    "multiplied price, not a sum of any bare per-unit rates.\n\n"
    'Respond with ONLY raw JSON, no markdown fences, in this exact shape: '
    '{{"items": [{{"name": "...", "spec": "...", "price": "...", "currency": "...", '
    '"source_url": "https://... or null", "is_estimate": false}}], '
    '"total": "...", "currency": "..."}}'
)

# Room-list prompt for the "Build a House" free algorithmic blueprint step
# (see CLAUDE.md's "Build a House feature" section). Deliberately asks for
# AREA AS A RELATIVE WEIGHT, not literal square footage - the downstream
# layout algorithm (app/pipeline/floor_layout.py) always rescales these to
# exactly fill the plot's real dimensions, so it doesn't matter whether the
# model's absolute numbers are realistic, only their proportions to each other.
ROOM_LAYOUT_PROMPT_TEMPLATE = (
    "You are planning a room layout for a building on a plot with dimensions "
    "{dims_text}.\n\n"
    "{context_block}"
    "The user's requirements: {prompt_text}\n\n"
    "Determine how many floors this building should have (default to 1 floor if the "
    "requirements don't specify a number), and for each floor list the rooms it should "
    "contain with a relative area weight per room (bigger rooms get bigger numbers - the "
    "exact scale doesn't matter, only the proportions between rooms on the same floor).\n\n"
    'Respond with ONLY raw JSON, no markdown fences, in this exact shape: '
    '{{"floors": [{{"floor_number": 1, "rooms": [{{"name": "...", "area": 1}}]}}]}}'
)

TIER_NOTES_PROMPT = (
    "You are analyzing a room photo for a 3-tier renovation visualization tool. The "
    "three tiers are: ECONOMICAL (paint only, cheapest materials, minimal fixtures), "
    "MID-LEVEL (paint + mouldings/wainscoting + wood flooring + modest fixtures, no "
    "chandelier), and PREMIUM (marble, brass trim, chandelier, luxury materials).\n\n"
    "Look at this specific room's actual visible condition (damage, stains, cracks, "
    "clutter, disrepair, etc. if any). For EACH tier, write ONE short instruction "
    "(under 15 words, no full sentences, comma-separated phrase) telling an image "
    "model how THIS tier should specifically address what's visible in THIS room "
    "(e.g. what to paint over, repair, or clean up) consistent with that tier's "
    "budget level - an economical tier should look cheaply fixed, not lavishly "
    "restored.\n\n"
    'Respond with ONLY raw JSON, no markdown fences, in this exact shape: '
    '{"economical": "...", "mid": "...", "premium": "..."}'
)


class GeminiProvider(Provider):
    def __init__(self) -> None:
        self._client: genai.Client | None = None
        # Separate cache for non-default keys (materials/pricing concurrency - see
        # generate_materials) so each of the up-to-3 configured Gemini keys gets
        # its own genai.Client, letting concurrent per-tier calls actually land on
        # distinct rate-limit quotas instead of all funneling through self.client.
        self._clients_by_key: dict[str, genai.Client] = {}

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    def _client_for_key(self, api_key: str | None) -> genai.Client:
        if not api_key or api_key == settings.gemini_api_key:
            return self.client
        if api_key not in self._clients_by_key:
            self._clients_by_key[api_key] = genai.Client(api_key=api_key)
        return self._clients_by_key[api_key]

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        # tier is unused here - Gemini's instruction-based editing has no
        # per-tier fidelity/quality knob to vary (see Provider.generate_image).
        image = Image.open(BytesIO(image_bytes))
        response = self.client.models.generate_content(
            model=settings.gemini_image_model,
            contents=[prompt, image],
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data

        raise RuntimeError("Gemini response contained no image data")

    def describe_room(self, image_bytes: bytes) -> str:
        image = Image.open(BytesIO(image_bytes))
        prompt = (
            "In two short sentences, describe this room for an AI image-editing model. "
            "First: state what TYPE of space this actually is, based only on what's "
            "visibly there (e.g. hallway, corridor, entryway, bedroom, living room, "
            "kitchen, dining room, bathroom, office) - use your own judgement, don't "
            "default to a generic guess, and don't call it a 'room' if a more specific "
            "type is visible. Second: describe its fixed structure - window/door "
            "positions, room shape, and its approximate depth/proportions (e.g. 'a long "
            "narrow hallway extending several meters back' vs 'a compact, roughly "
            "square room'). Do not mention furniture, decor, or colors. Be concise."
        )
        response = self.client.models.generate_content(
            model=settings.gemini_text_model,
            contents=[prompt, image],
        )
        return (response.text or "").strip()

    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        """Real bug this exists to fix: materials pricing (generate_materials
        below) had no notion of the room's actual size, so a found per-sqft
        price (e.g. "PKR 1,100/sqft" for flooring) was passed straight through
        as if it were already the item's total cost, instead of being
        multiplied by the room's area - a flooring "total" that never actually
        accounted for how much flooring the room needs. This gives
        generate_materials a real (if rough) quantity to multiply by. Kept as
        its own tiny call rather than folded into describe_room() so a parsing
        failure here can't also break the room-structure description that
        feeds the image-generation prompt.
        """
        image = Image.open(BytesIO(image_bytes))
        prompt = (
            "Look at this room photo and estimate its approximate total floor area in "
            "square feet, reasoning from visible scale cues (door widths are typically "
            "~3 feet, standard ceiling height ~8-9 feet, furniture proportions, etc). "
            "Respond with ONLY a single number (the square footage), no units, no words, "
            "no explanation - e.g. \"180\"."
        )
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_text_model,
                contents=[prompt, image],
            )
            match = re.search(r"[\d,]+(?:\.\d+)?", response.text or "")
            if not match:
                return None
            area = float(match.group(0).replace(",", ""))
            return area if area > 0 else None
        except Exception:
            logger.exception("estimate_room_area failed; continuing without it")
            return None

    def analyze_plot(self, image_bytes: bytes, dimensions: dict) -> str | None:
        try:
            image = Image.open(BytesIO(image_bytes))
            dims_text = _format_dimensions(dimensions)
            prompt = (
                "You are analyzing a photo of a building plot/piece of land for an architectural "
                f"concept tool. The plot's stated dimensions are: {dims_text}. In 2-3 short "
                "sentences, describe: the plot's apparent orientation (e.g. which side faces a "
                "road/street, if visible), its approximate boundary shape (regular rectangle vs "
                "irregular), and any notable features visible in the photo (slope, existing "
                "structures, trees, adjacent buildings). Be concise and factual - do not invent "
                "details you cannot actually see in the photo."
            )
            response = self.client.models.generate_content(
                model=settings.gemini_text_model,
                contents=[prompt, image],
            )
            return (response.text or "").strip() or None
        except Exception:
            logger.exception("analyze_plot failed; continuing without it")
            return None

    def generate_floor_plan(
        self, plot_description: str | None, dimensions: dict, prompt: str
    ) -> bytes | None:
        # Gemini has no floor-plan-specific capability - this is the honest
        # "not available from this provider" case the best-effort contract
        # allows for (see Provider.generate_floor_plan's docstring). The real
        # floor-plan vendor slot is app/providers/idealhouse.py, composed in by
        # HybridProvider - not this class.
        return None

    def generate_house_render(self, image_bytes: bytes, prompt: str) -> bytes:
        # Reuses Gemini's own (dormant in the composition root, but real and
        # working) instruction-based image editing - same call shape as
        # generate_image() above, just under the house feature's own method
        # name so its parameters never get tangled with room-redesign's tier
        # semantics.
        return self.generate_image(image_bytes, prompt)

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        image = Image.open(BytesIO(image_bytes))
        response = self.client.models.generate_content(
            model=settings.gemini_text_model,
            contents=[TIER_NOTES_PROMPT, image],
        )
        return parse_tier_notes(response.text or "")

    def generate_materials(
        self,
        tier: str,
        tier_spec: dict[str, str],
        room_description: str | None,
        city: str,
        api_key: str | None = None,
        room_area_sqft: float | None = None,
    ) -> dict:
        line_items = _tier_line_items(tier_spec)

        # One dedicated SerpApi search per item (not one combined search) - see
        # the module docstring for why. A failed individual search isn't fatal -
        # that one item just can't be marked is_estimate=false, the rest of the
        # tier is unaffected.
        item_results: dict[str, list[dict]] = {}
        for name, spec in line_items:
            try:
                item_results[name] = serpapi.search(f"price of {spec} in {city}", location=city)
            except Exception:
                logger.exception("materials search failed for item %s, tier %s in %s", name, tier, city)
                item_results[name] = []

        # Real bug this fixes: without a known area, a found per-sqft price
        # (e.g. "$12/sqft" for flooring) was passed straight through as the
        # item's "total" price - never actually multiplied by how much floor
        # the room has. See estimate_room_area()'s docstring for the full story.
        if room_area_sqft:
            area_block = (
                f"This room's estimated floor area is approximately {room_area_sqft:.0f} square "
                "feet - use this for the quantity/multiplication rule below.\n\n"
            )
        else:
            area_block = (
                "No floor-area estimate is available for this room - for Flooring, Ceiling "
                "treatment, and Paint / wall finish, assume a typical room of this type and "
                "state your assumed square footage explicitly in that item's spec field before "
                "applying the quantity rule below.\n\n"
            )

        prompt = MATERIALS_PROMPT_TEMPLATE.format(
            tier_label=tier_spec.get("label", tier),
            city=city,
            area_block=area_block,
            items_block=_format_line_items_with_results(line_items, item_results),
        )

        try:
            client = self._client_for_key(api_key)
            response = _generate_content_with_retry(client, settings.gemini_text_model, [prompt])
            parsed = parse_materials(response.text or "")
            if parsed is not None:
                all_real_results = [r for results in item_results.values() for r in results]
                return _sanitize_source_urls(parsed, all_real_results)
        except Exception:
            logger.exception("generate_materials failed for tier %s in %s", tier, city)

        return fallback_materials(tier_spec, city)

    def generate_room_layout(
        self, dimensions: dict, prompt: str, plot_description: str | None = None
    ) -> dict:
        context_block = f"Context about the actual plot: {plot_description}\n\n" if plot_description else ""
        formatted_prompt = ROOM_LAYOUT_PROMPT_TEMPLATE.format(
            dims_text=_format_dimensions(dimensions),
            context_block=context_block,
            prompt_text=prompt.strip() if prompt else "no specific requirements given",
        )

        try:
            response = _generate_content_with_retry(self.client, settings.gemini_text_model, [formatted_prompt])
            parsed = parse_room_layout(response.text or "")
            if parsed is not None:
                return parsed
        except Exception:
            logger.exception("generate_room_layout failed for dimensions %s", dimensions)

        return fallback_room_layout(dimensions, prompt)


def _generate_content_with_retry(client: genai.Client, model: str, contents: list) -> object:
    """Retries the materials-synthesis Gemini call on genai_errors.ServerError
    (5xx - see MATERIALS_GEMINI_MAX_ATTEMPTS's comment for the real 503 this
    guards against). Deliberately narrow: only retries server-side errors,
    not e.g. auth/bad-request errors that would just fail identically again.

    If every attempt on `model` still 503s, makes one final attempt against
    MATERIALS_GEMINI_FALLBACK_MODEL - a different model has independent
    capacity/load, so it's a real hedge against a single-model outage (see
    that constant's docstring for the live incident that motivated this).
    """
    last_exc: Exception | None = None
    for attempt in range(1, MATERIALS_GEMINI_MAX_ATTEMPTS + 1):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except genai_errors.ServerError as exc:
            last_exc = exc
            logger.warning(
                "Gemini server error on materials call, attempt %d/%d: %s",
                attempt,
                MATERIALS_GEMINI_MAX_ATTEMPTS,
                exc,
            )
            if attempt < MATERIALS_GEMINI_MAX_ATTEMPTS:
                time.sleep(MATERIALS_GEMINI_RETRY_DELAY_SECONDS)

    if model != MATERIALS_GEMINI_FALLBACK_MODEL:
        try:
            logger.warning(
                "Gemini model %s exhausted retries, trying fallback model %s",
                model,
                MATERIALS_GEMINI_FALLBACK_MODEL,
            )
            return client.models.generate_content(model=MATERIALS_GEMINI_FALLBACK_MODEL, contents=contents)
        except Exception:
            logger.exception("Gemini fallback model %s also failed", MATERIALS_GEMINI_FALLBACK_MODEL)

    raise last_exc


def _format_dimensions(dimensions: dict) -> str:
    length = dimensions.get("length")
    width = dimensions.get("width")
    unit = dimensions.get("unit", "")
    if length and width:
        return f"{length} x {width} {unit}".strip()
    return "not specified"


def _tier_line_items(tier_spec: dict[str, str]) -> list[tuple[str, str]]:
    """The fixed, deterministic item list every tier prices - one dedicated
    search per item is what makes "a real source attempt for every item"
    achievable at all; letting the model freely itemize 5-9 items of its own
    choosing (the earlier design) meant most items got no dedicated search
    coverage. Shared by generate_materials() and fallback_materials() so both
    price the exact same items. Skips fields with no content for this tier
    (e.g. economical's blank feature_wall).
    """
    fields = [
        ("Flooring", tier_spec.get("flooring", "")),
        ("Paint / wall finish", tier_spec.get("paint", "")),
        ("Lighting", tier_spec.get("lighting_temp", "")),
        ("Ceiling treatment", tier_spec.get("ceiling", "")),
        ("Feature wall", tier_spec.get("feature_wall", "")),
        ("Decor", tier_spec.get("decor", "")),
    ]
    return [(name, spec) for name, spec in fields if spec]


def _format_line_items_with_results(line_items: list[tuple[str, str]], item_results: dict[str, list[dict]]) -> str:
    blocks = []
    for name, spec in line_items:
        results_text = _format_search_results(item_results.get(name, []))
        blocks.append(f"ITEM: {name} ({spec})\nSearch results for this item:\n{results_text}")
    return "\n\n".join(blocks)


def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "(no search results available for this item)"
    return "\n".join(
        f"{i}. Title: {r['title']}\n   Snippet: {r['snippet']}\n   Link: {r['link']}"
        for i, r in enumerate(results, start=1)
    )


def _sanitize_source_urls(materials: dict, real_results: list[dict]) -> dict:
    """Defense in depth on top of the prompt's "never invent/reuse a URL"
    instruction - prompt-following isn't 100% reliable, so strip any
    source_url the model returned that isn't a link we actually got from ANY
    of the real per-item searches. Downgrades that item to is_estimate=True
    instead of leaving a possibly-hallucinated link, same "never show
    something unverified as real" principle as everywhere else here.

    Checked against the union of all items' real links (not strictly the
    matching item's own links) - a stricter per-item check would also need to
    match the model's returned item `name` back to the exact fixed label we
    gave it, and a minor rename ("Flooring" -> "Flooring Material") would then
    incorrectly strip an otherwise-real link. Cross-item link reuse is a much
    smaller, lower-stakes mistake (still a real, resolving URL) than that
    false-negative failure mode.
    """
    real_links = {r["link"] for r in real_results}
    for item in materials["items"]:
        if item["source_url"] and item["source_url"] not in real_links:
            item["source_url"] = None
            item["is_estimate"] = True
    return materials


def _strip_json_fences(raw_text: str) -> str:
    """Models sometimes wrap JSON in markdown code fences even when told not to -
    strip them so json.loads() gets raw JSON either way.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def parse_tier_notes(raw_text: str) -> dict[str, str]:
    """Parse the tier-notes JSON response. Returns {} on any parse failure -
    callers must treat tier notes as best-effort, same as describe_room.
    """
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except (json.JSONDecodeError, ValueError):
        logger.warning("could not parse tier notes JSON: %r", raw_text[:200])
        return {}

    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ("economical", "mid", "premium") and isinstance(v, str)}


def parse_materials(raw_text: str) -> dict | None:
    """Parse the materials/pricing JSON response. Returns None (not {}) on
    failure - the caller (generate_materials) uses that as the signal to fall
    back to fallback_materials() instead, since a never-empty result is
    required here, unlike tier_notes/describe_room's "degrade to {}"."""
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except (json.JSONDecodeError, ValueError):
        logger.warning("could not parse materials JSON: %r", raw_text[:200])
        return None

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None

    items = []
    for raw_item in data["items"]:
        if not isinstance(raw_item, dict) or not raw_item.get("name"):
            continue
        price = raw_item.get("price")
        is_estimate = bool(raw_item.get("is_estimate", False))
        if not price:
            # Defensive fill - never leave a price blank even if this one item's
            # price came back missing despite the prompt's instruction not to.
            price = "Estimate unavailable"
            is_estimate = True
        items.append(
            {
                "name": str(raw_item["name"]),
                "spec": str(raw_item.get("spec", "")),
                "price": str(price),
                "currency": str(raw_item.get("currency", "")),
                "source_url": raw_item.get("source_url") or None,
                "is_estimate": is_estimate,
            }
        )

    if not items:
        return None

    total = data.get("total") or "See itemized estimates above"
    return {"items": items, "total": str(total), "currency": str(data.get("currency", ""))}


def parse_room_layout(raw_text: str) -> dict | None:
    """Parse the room-layout JSON response. Returns None (not {}) on failure -
    generate_room_layout's caller uses that as the signal to fall back to
    fallback_room_layout() instead, since a never-empty result is required
    here (the blueprint-drawing step needs at least one room per floor to
    draw anything), same category as parse_materials, not tier_notes/
    describe_room's "degrade to {}"."""
    try:
        data = json.loads(_strip_json_fences(raw_text))
    except (json.JSONDecodeError, ValueError):
        logger.warning("could not parse room layout JSON: %r", raw_text[:200])
        return None

    if not isinstance(data, dict) or not isinstance(data.get("floors"), list) or not data["floors"]:
        return None

    floors = []
    for raw_floor in data["floors"]:
        if not isinstance(raw_floor, dict) or not isinstance(raw_floor.get("rooms"), list):
            continue
        rooms = [
            {"name": str(raw_room["name"]), "area": float(raw_room.get("area") or 1)}
            for raw_room in raw_floor["rooms"]
            if isinstance(raw_room, dict) and raw_room.get("name")
        ]
        if not rooms:
            continue
        try:
            floor_number = int(raw_floor.get("floor_number") or len(floors) + 1)
        except (TypeError, ValueError):
            floor_number = len(floors) + 1
        floors.append({"floor_number": floor_number, "rooms": rooms})

    if not floors:
        return None

    return {"floors": floors}


def fallback_room_layout(dimensions: dict, prompt: str) -> dict:
    """Never-empty room-layout fallback for when the Gemini call itself fails
    entirely (network/auth/quota/timeout) or returns unparseable JSON. Guesses
    the floor count from a plain 'N floor(s)' pattern in the user's free-text
    prompt (defaulting to 1), and synthesizes the same fixed generic room list
    per floor with equal area weights - always produces something the
    blueprint step can draw, same never-empty guarantee as fallback_materials.
    """
    floor_count = _guess_floor_count(prompt)
    generic_rooms = [
        {"name": "Living Room", "area": 2},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 1.5},
        {"name": "Bedroom 2", "area": 1.5},
        {"name": "Bathroom", "area": 1},
    ]
    return {
        "floors": [{"floor_number": n, "rooms": generic_rooms} for n in range(1, floor_count + 1)]
    }


def _guess_floor_count(prompt: str) -> int:
    match = re.search(r"(\d+)\s*(?:floor|floors|storey|storeys|story|stories)", prompt or "", re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 10))
    return 1


def fallback_materials(tier_spec: dict[str, str], city: str) -> dict:
    """Never-empty materials fallback for when the Gemini call itself fails
    entirely (network/auth/quota/timeout - a harder failure than "couldn't
    find a price for one item", which generate_materials's prompt already
    handles by asking Gemini for a labeled estimate). Synthesizes the same
    fixed item list as generate_materials (_tier_line_items), each flagged as
    an estimate with a search-URL that's guaranteed to resolve (unlike a
    guessed product link), so the UI never renders a blank price or a dead link.
    """
    items = [
        {
            "name": name,
            "spec": spec,
            "price": "Estimate unavailable",
            "currency": "",
            "source_url": f"https://www.google.com/search?q={urllib.parse.quote(f'{name} {spec} price {city}')}",
            "is_estimate": True,
        }
        for name, spec in _tier_line_items(tier_spec)
    ]
    return {"items": items, "total": "Not available - see search links above", "currency": ""}
