from app.config import settings
from app.providers.base import Provider
from app.providers.hybrid import HybridProvider
from app.providers.openai import OpenAIImageProvider


def get_provider() -> Provider:
    if settings.image_provider == "openai":
        return HybridProvider(image_provider=OpenAIImageProvider())
    return HybridProvider()
