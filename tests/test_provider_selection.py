from app.providers import get_provider
from app.providers.gemini import GeminiProvider
from app.providers.openai import OpenAIImageProvider


def test_get_provider_uses_openai_image_backend():
    provider = get_provider()
    assert isinstance(provider._image_provider, OpenAIImageProvider)


def test_get_provider_uses_gemini_for_text():
    provider = get_provider()
    assert isinstance(provider._gemini, GeminiProvider)
