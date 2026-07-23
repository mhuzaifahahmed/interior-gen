from pathlib import Path

from app.config import settings
from app.storage.base import Storage


class LocalStorage(Storage):
    """Filesystem-backed storage. Served via the /media static mount in app.main."""

    def __init__(self) -> None:
        self.root = Path(settings.local_storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"invalid storage key: {key}")
        return path

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def url(self, key: str) -> str:
        return f"/media/{key}"
