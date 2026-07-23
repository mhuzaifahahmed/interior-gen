from abc import ABC, abstractmethod


class Storage(ABC):
    """Byte-level object storage for uploaded/generated images.

    Implementations must be swappable with no changes to calling code:
    local filesystem for dev, S3-compatible (Cloudflare R2 / AWS S3) for cloud.
    """

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        ...

    @abstractmethod
    def url(self, key: str) -> str:
        """Return a URL the frontend can load the object from directly."""
        ...
