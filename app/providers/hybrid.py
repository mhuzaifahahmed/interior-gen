from app.providers import idealhouse
from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIImageProvider


class HybridProvider(Provider):
    """Room description + tier-notes analysis via Gemini (still free, separate
    quota from image gen). Image generation via an injectable image backend -
    OpenAI's gpt-image-1 (default) unless a different one is passed in (mainly
    for tests). Google removed free-tier Gemini image generation in Dec 2025,
    which is why image gen isn't just Gemini too.

    floor_plan_provider defaults to the app.providers.idealhouse MODULE itself
    (not an instance - its generate_floor_plan is a plain function, same style
    as serpapi.py) so a real vendor can be wired in later by only changing that
    one file, or by injecting a different object here (e.g. in tests).
    """

    def __init__(self, image_provider=None, floor_plan_provider=None) -> None:
        self._gemini = GeminiProvider()
        self._image_provider = image_provider or OpenAIImageProvider()
        self._floor_plan_provider = floor_plan_provider or idealhouse

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
        room_area_sqft: float | None = None,
    ) -> dict:
        return self._gemini.generate_materials(
            tier, tier_spec, room_description, city, api_key, room_area_sqft
        )

    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        return self._gemini.estimate_room_area(image_bytes)

    def analyze_plot(self, image_bytes: bytes, dimensions: dict) -> str | None:
        return self._gemini.analyze_plot(image_bytes, dimensions)

    def generate_floor_plan(
        self, plot_description: str | None, dimensions: dict, prompt: str
    ) -> bytes | None:
        return self._floor_plan_provider.generate_floor_plan(plot_description, dimensions, prompt)

    def generate_house_render(self, image_bytes: bytes, prompt: str) -> bytes:
        return self._image_provider.generate_house_render(image_bytes, prompt)

    def generate_room_layout(
        self, dimensions: dict, prompt: str, plot_description: str | None = None
    ) -> dict:
        return self._gemini.generate_room_layout(dimensions, prompt, plot_description)
