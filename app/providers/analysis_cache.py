import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# Module-level (not per-provider-instance) because get_provider() constructs a
# fresh HybridProvider/GeminiProvider per request (see app/providers/__init__.py)
# - an instance-level cache would never see a hit across two different requests.
# Bounded LRU, not an unbounded dict, so a long-running process can't leak
# memory over many uploads. Keyed on a sha256 of the image bytes (not the bytes
# themselves) so the cache doesn't hold references to full image payloads -
# only a 32-byte digest per entry.
_MAXSIZE = 64
_cache: "OrderedDict[tuple[str, str], Any]" = OrderedDict()
_lock = threading.Lock()

MISS = object()  # not None - a real analysis result can legitimately be None/empty


def _key(kind: str, image_bytes: bytes) -> tuple[str, str]:
    return (kind, hashlib.sha256(image_bytes).hexdigest())


def get(kind: str, image_bytes: bytes) -> Any:
    """Returns the cached result for (kind, image_bytes), or MISS if absent."""
    key = _key(kind, image_bytes)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    return MISS


def set(kind: str, image_bytes: bytes, value: Any) -> None:
    key = _key(kind, image_bytes)
    with _lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _MAXSIZE:
            _cache.popitem(last=False)


def clear() -> None:
    """Test-only escape hatch - the cache is module/process-global, so tests
    that assert on cache hit/miss behavior need to reset it between runs.
    """
    with _lock:
        _cache.clear()
