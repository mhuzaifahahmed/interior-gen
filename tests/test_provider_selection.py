from app.providers import get_provider
from app.providers.cloudflare import CloudflareImageProvider
from app.providers.openai import OpenAIImageProvider


def test_default_provider_uses_cloudflare_image_backend(monkeypatch):
    import app.providers as providers_module

    monkeypatch.setattr(providers_module.settings, "image_provider", "cloudflare")
    provider = get_provider()
    assert isinstance(provider._image_provider, CloudflareImageProvider)


def test_image_provider_openai_switches_image_backend(monkeypatch):
    import app.providers as providers_module

    monkeypatch.setattr(providers_module.settings, "image_provider", "openai")
    provider = get_provider()
    assert isinstance(provider._image_provider, OpenAIImageProvider)


def test_text_provider_is_gemini_regardless_of_image_provider(monkeypatch):
    import app.providers as providers_module
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(providers_module.settings, "image_provider", "openai")
    provider = get_provider()
    assert isinstance(provider._gemini, GeminiProvider)
