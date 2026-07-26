import json
import logging
from io import BytesIO

from google import genai
from PIL import Image

from app.config import settings
from app.providers.base import Provider

logger = logging.getLogger(__name__)

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

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

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

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        image = Image.open(BytesIO(image_bytes))
        response = self.client.models.generate_content(
            model=settings.gemini_text_model,
            contents=[TIER_NOTES_PROMPT, image],
        )
        return parse_tier_notes(response.text or "")


def parse_tier_notes(raw_text: str) -> dict[str, str]:
    """Parse the tier-notes JSON response, tolerating markdown code fences that
    models sometimes add even when told not to. Returns {} on any parse failure -
    callers must treat tier notes as best-effort, same as describe_room.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("could not parse tier notes JSON: %r", raw_text[:200])
        return {}

    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ("economical", "mid", "premium") and isinstance(v, str)}
