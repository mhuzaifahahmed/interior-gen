import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.providers import idealhouse
from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.kaggle import KaggleImageProvider
from app.providers.modal_provider import ModalImageProvider
from app.providers.openai import OpenAIImageProvider

logger = logging.getLogger(__name__)


def _default_room_image_provider(fallback_provider):
    # IMAGE_PROVIDER selects the room-redesign image backend: "modal"
    # (default, self-hosted - see modal_provider.py), "kaggle" (the old
    # Kaggle-notebook setup, kept dormant not deleted), or "openai" (falls
    # through to fallback_provider, always OpenAI). A toggle, not a hard
    # swap - reverting to OpenAI is one .env line. "Build a House" rendering
    # is a SEPARATE toggle (house_image_provider) - see HybridProvider's
    # docstring for why they're independent.
    if settings.image_provider == "modal":
        return ModalImageProvider()
    if settings.image_provider == "kaggle":
        return KaggleImageProvider()
    return fallback_provider


def _default_house_image_provider(openai_provider):
    # house_image_provider selects the "Build a House" render backend -
    # independent of image_provider above (room-redesign), following this
    # codebase's established room-vs-house settings-isolation pattern. See
    # app/config.py's house_image_provider comment for the default choice.
    # "kaggle" uses a SEPARATE Kaggle account/tunnel from room-redesign's
    # (settings.kaggle_house_api_url) - added in advance of a real trained
    # house model being ready; see KaggleImageProvider.generate_house_render()'s
    # docstring for the assumed (not yet confirmed) request contract.
    if settings.house_image_provider == "modal":
        return ModalImageProvider()
    if settings.house_image_provider == "kaggle":
        return KaggleImageProvider()
    return openai_provider


class HybridProvider(Provider):
    """Room description + tier-notes analysis via Gemini (still free, separate
    quota from image gen). Image generation via injectable image backends -
    OpenAI's gpt-image-1 unless a different one is passed in (mainly for
    tests) or a toggle setting swaps it. Google removed free-tier Gemini
    image generation in Dec 2025, which is why image gen isn't just Gemini too.

    THREE separate concerns, three separate attributes - deliberately not
    conflated, since house_image_provider becoming independently toggleable
    (Modal or OpenAI, see app/config.py) means "the house render backend"
    and "the guaranteed-reliable fallback for a failed room provider" are no
    longer always the same thing, and must not be treated as if they were:
    - _openai: ALWAYS a real (or injected, for tests) OpenAI-shaped provider.
      This is the universal, always-available reliability fallback for room-
      redesign failures - see RUNTIME FALLBACK below. Never swapped by any
      toggle, so a failing experimental backend always has something solid
      to land on.
    - _house_image_provider: resolved from house_image_provider ("modal",
      "kaggle", or "openai") via _default_house_image_provider() - used ONLY
      for generate_house_render(), never as a fallback target for room-redesign.
    - _room_image_provider: resolved from image_provider ("modal", "kaggle",
      or "openai") via _default_room_image_provider() - used for
      generate_image()/generate_images_batch() (room-redesign).

    RUNTIME FALLBACK: generate_image() automatically retries via _openai if
    _room_image_provider raises for any reason - a dead Kaggle tunnel, a
    Modal error, a timeout, a malformed response, anything. This matters
    specifically because both Kaggle and Modal room backends are
    experimental/self-hosted (Kaggle's Cloudflare tunnel has gone offline
    mid-testing in the past) - without this, one dead backend fails the
    whole generation instead of degrading to the paid-but-reliable one. Each
    of the 3 tiers' generate_image() calls falls back independently (see
    app/pipeline/generate.py's per-tier ThreadPoolExecutor), so a transient
    failure on only one tier doesn't drag the other two down with it. No
    fallback loop when IMAGE_PROVIDER=openai (the default toggle value for
    this specific setting is "modal", but explicitly setting it to "openai"
    still works this way) - in that case _room_image_provider IS _openai
    (the same object), so there's nothing further to fall back to and a
    failure just raises directly.

    floor_plan_provider defaults to the app.providers.idealhouse MODULE itself
    (not an instance - its generate_floor_plan is a plain function, same style
    as serpapi.py) so a real vendor can be wired in later by only changing that
    one file, or by injecting a different object here (e.g. in tests).
    """

    def __init__(
        self, image_provider=None, room_image_provider=None, floor_plan_provider=None
    ) -> None:
        self._gemini = GeminiProvider()
        self._openai = image_provider or OpenAIImageProvider()
        self._house_image_provider = _default_house_image_provider(self._openai)
        self._room_image_provider = room_image_provider or _default_room_image_provider(self._openai)
        self._floor_plan_provider = floor_plan_provider or idealhouse

    def describe_room(self, image_bytes: bytes) -> str:
        return self._gemini.describe_room(image_bytes)

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return self._gemini.generate_tier_notes(image_bytes)

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        try:
            return self._room_image_provider.generate_image(image_bytes, prompt, tier)
        except Exception:
            if self._room_image_provider is self._openai:
                raise  # already OpenAI (the fallback itself) - nothing left to try
            logger.exception(
                "room image provider %s failed for tier %s - falling back to OpenAI "
                "for this generation",
                type(self._room_image_provider).__name__,
                tier,
            )
            return self._openai.generate_image(image_bytes, prompt, tier)

    def supports_batch(self) -> bool:
        return self._room_image_provider.supports_batch()

    def generate_images_batch(
        self, image_bytes: bytes, tier_prompts: dict[str, str]
    ) -> dict[str, bytes]:
        try:
            return self._room_image_provider.generate_images_batch(image_bytes, tier_prompts)
        except Exception:
            if self._room_image_provider is self._openai:
                raise  # already OpenAI (the fallback itself) - nothing left to try
            logger.exception(
                "room image provider %s batch generation failed - falling back to OpenAI "
                "(per-tier concurrent, not batched)",
                type(self._room_image_provider).__name__,
            )
            with ThreadPoolExecutor(max_workers=len(tier_prompts)) as pool:
                futures = {
                    tier: pool.submit(self._openai.generate_image, image_bytes, prompt, tier)
                    for tier, prompt in tier_prompts.items()
                }
                return {tier: future.result() for tier, future in futures.items()}

    def generate_materials(
        self,
        tier: str,
        tier_spec: dict[str, str],
        room_description: str | None,
        city: str,
        api_key: str | None = None,
        room_area_sqft: float | None = None,
        wall_area_sqft: float | None = None,
    ) -> dict:
        return self._gemini.generate_materials(
            tier, tier_spec, room_description, city, api_key, room_area_sqft, wall_area_sqft
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
        self,
        dimensions: dict,
        prompt: str,
        plot_description: str | None = None,
        floor_count: int | None = None,
    ) -> dict:
        return self._gemini.generate_room_layout(dimensions, prompt, plot_description, floor_count)
