from app.providers.base import Provider
from app.providers.hybrid import HybridProvider


def get_provider() -> Provider:
    return HybridProvider()
