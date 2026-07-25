from app.providers.base import Provider
from app.providers.cloudflare import CloudflareImageProvider
from app.providers.gemini import GeminiProvider


class HybridProvider(Provider):
    """Room description + tier-notes analysis via Gemini (still free, separate
    quota from image gen). Image generation via an injectable image backend -
    Cloudflare Workers AI (free tier, default) unless a different one is passed
    in - see get_provider() in app/providers/__init__.py, which selects based on
    IMAGE_PROVIDER. Google removed free-tier Gemini image generation in Dec 2025,
    which is why image gen isn't just Gemini too.
    """

    def __init__(self, image_provider=None) -> None:
        self._gemini = GeminiProvider()
        self._image_provider = image_provider or CloudflareImageProvider()

    def describe_room(self, image_bytes: bytes) -> str:
        return self._gemini.describe_room(image_bytes)

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return self._gemini.generate_tier_notes(image_bytes)

    def generate_image(
        self,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        strength: float | None = None,
    ) -> bytes:
        return self._image_provider.generate_image(image_bytes, prompt, negative_prompt, strength)
