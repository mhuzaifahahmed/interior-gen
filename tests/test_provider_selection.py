from app.providers import get_provider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIImageProvider


def test_get_provider_always_builds_a_real_openai_fallback():
    # _openai is the universal, always-real reliability fallback for a
    # failing room-redesign backend - unlike _house_image_provider and
    # _room_image_provider, which are BOTH environment-dependent by design
    # (house_image_provider/image_provider toggles - see HybridProvider's
    # docstring), _openai is never swapped by any toggle.
    provider = get_provider()
    assert isinstance(provider._openai, OpenAIImageProvider)


def test_get_provider_uses_gemini_for_text():
    provider = get_provider()
    assert isinstance(provider._gemini, GeminiProvider)
