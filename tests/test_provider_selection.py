from app.providers import get_provider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIImageProvider


def test_get_provider_uses_openai_for_house_renders():
    # "Build a House" rendering always stays on OpenAI regardless of
    # IMAGE_PROVIDER (see HybridProvider's docstring) - unlike room-redesign
    # image generation (_room_image_provider), which is environment-dependent
    # by design once IMAGE_PROVIDER=kaggle is set, so it's not asserted here.
    provider = get_provider()
    assert isinstance(provider._house_image_provider, OpenAIImageProvider)


def test_get_provider_uses_gemini_for_text():
    provider = get_provider()
    assert isinstance(provider._gemini, GeminiProvider)
