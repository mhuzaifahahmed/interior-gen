from app.providers.base import Provider
from app.providers.cloudflare import CloudflareImageProvider
from app.providers.gemini import GeminiProvider


class HybridProvider(Provider):
    """Room description via Gemini (still free, separate quota from image gen).
    Image generation via Cloudflare Workers AI (free tier) since Google removed
    free-tier Gemini image generation in Dec 2025. See app/providers/cloudflare.py.
    """

    def __init__(self) -> None:
        self._gemini = GeminiProvider()
        self._cloudflare = CloudflareImageProvider()

    def describe_room(self, image_bytes: bytes) -> str:
        return self._gemini.describe_room(image_bytes)

    def generate_image(self, image_bytes: bytes, prompt: str, negative_prompt: str = "") -> bytes:
        return self._cloudflare.generate_image(image_bytes, prompt, negative_prompt)
