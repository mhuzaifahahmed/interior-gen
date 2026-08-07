import logging

from app.config import settings
from app.providers import idealhouse
from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.kaggle import KaggleImageProvider
from app.providers.openai import OpenAIImageProvider

logger = logging.getLogger(__name__)


def _default_room_image_provider(house_image_provider):
    # IMAGE_PROVIDER=kaggle in .env swaps ONLY room-redesign image generation
    # to a user's own fine-tuned model (see app/providers/kaggle.py) - a
    # toggle, not a hard swap, so a dropped Kaggle tunnel can be reverted to
    # OpenAI by editing one .env line, no code change needed. "Build a House"
    # rendering is untouched by this setting on purpose (see HybridProvider's
    # docstring).
    if settings.image_provider == "kaggle":
        return KaggleImageProvider()
    return house_image_provider


class HybridProvider(Provider):
    """Room description + tier-notes analysis via Gemini (still free, separate
    quota from image gen). Image generation via injectable image backends -
    OpenAI's gpt-image-1 by default for both room-redesign and "Build a
    House" renders, unless a different one is passed in (mainly for tests).
    Google removed free-tier Gemini image generation in Dec 2025, which is
    why image gen isn't just Gemini too.

    Room-redesign image generation (generate_image) and "Build a House"
    rendering (generate_house_render) are DELIBERATELY separate provider
    instances (_room_image_provider vs _house_image_provider), not one shared
    _image_provider - added when IMAGE_PROVIDER=kaggle first let room-redesign
    swap to a user's own fine-tuned model. That model was trained on interior
    redesign, not exterior/plot renders, so "Build a House" always keeps using
    OpenAI regardless of IMAGE_PROVIDER; only generate_image() reads the toggle.

    RUNTIME FALLBACK: generate_image() automatically retries via OpenAI
    (_house_image_provider, which is always OpenAI regardless of the toggle)
    if _room_image_provider raises for any reason - a dead Kaggle tunnel,
    a timeout, a malformed response, anything. This matters specifically
    because the Kaggle model is an ephemeral, dev-hosted endpoint (a
    Cloudflare quick tunnel tied to a live notebook session) that can and has
    gone offline mid-testing - without this, one dead tunnel fails the whole
    generation instead of degrading to the paid-but-reliable backend. Each of
    the 3 tiers' generate_image() calls falls back independently (see
    app/pipeline/generate.py's per-tier ThreadPoolExecutor), so a transient
    failure on only one tier doesn't drag the other two down with it. No
    fallback loop when IMAGE_PROVIDER=openai (the default) - in that case
    _room_image_provider IS _house_image_provider, so there's nothing further
    to fall back to and a failure just raises directly, exactly as before this
    existed.

    floor_plan_provider defaults to the app.providers.idealhouse MODULE itself
    (not an instance - its generate_floor_plan is a plain function, same style
    as serpapi.py) so a real vendor can be wired in later by only changing that
    one file, or by injecting a different object here (e.g. in tests).
    """

    def __init__(
        self, image_provider=None, room_image_provider=None, floor_plan_provider=None
    ) -> None:
        self._gemini = GeminiProvider()
        self._house_image_provider = image_provider or OpenAIImageProvider()
        self._room_image_provider = room_image_provider or _default_room_image_provider(
            self._house_image_provider
        )
        self._floor_plan_provider = floor_plan_provider or idealhouse

    def describe_room(self, image_bytes: bytes) -> str:
        return self._gemini.describe_room(image_bytes)

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return self._gemini.generate_tier_notes(image_bytes)

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        try:
            return self._room_image_provider.generate_image(image_bytes, prompt, tier)
        except Exception:
            if self._room_image_provider is self._house_image_provider:
                raise  # already OpenAI (the fallback itself) - nothing left to try
            logger.exception(
                "room image provider %s failed for tier %s - falling back to OpenAI "
                "for this generation",
                type(self._room_image_provider).__name__,
                tier,
            )
            return self._house_image_provider.generate_image(image_bytes, prompt, tier)

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
        return self._house_image_provider.generate_house_render(image_bytes, prompt)

    def generate_room_layout(
        self, dimensions: dict, prompt: str, plot_description: str | None = None
    ) -> dict:
        return self._gemini.generate_room_layout(dimensions, prompt, plot_description)
