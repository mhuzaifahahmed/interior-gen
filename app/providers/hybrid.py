from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIImageProvider


class HybridProvider(Provider):
    """Room description + tier-notes analysis via Gemini (still free, separate
    quota from image gen). Image generation via an injectable image backend -
    OpenAI's gpt-image-1 (default) unless a different one is passed in (mainly
    for tests). Google removed free-tier Gemini image generation in Dec 2025,
    which is why image gen isn't just Gemini too.
    """

    def __init__(self, image_provider=None) -> None:
        self._gemini = GeminiProvider()
        self._image_provider = image_provider or OpenAIImageProvider()

    def describe_room(self, image_bytes: bytes) -> str:
        return self._gemini.describe_room(image_bytes)

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return self._gemini.generate_tier_notes(image_bytes)

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        return self._image_provider.generate_image(image_bytes, prompt, tier)

    def generate_materials(
        self,
        tier: str,
        tier_spec: dict[str, str],
        room_description: str | None,
        city: str,
        api_key: str | None = None,
    ) -> dict:
        return self._gemini.generate_materials(tier, tier_spec, room_description, city, api_key)
