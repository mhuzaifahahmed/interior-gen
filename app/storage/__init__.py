from app.config import settings
from app.storage.base import Storage
from app.storage.local import LocalStorage


def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        from app.storage.s3 import S3Storage

        return S3Storage()
    return LocalStorage()
