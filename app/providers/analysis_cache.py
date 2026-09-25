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


def size() -> int:
    """Real entry count - used by the admin "Clear analysis cache" endpoint
    (see app/main.py's admin_clear_analysis_cache()) to report how many
    entries were actually wiped, not just that the call succeeded."""
    with _lock:
        return len(_cache)


def clear() -> int:
    """Wipes every cached analysis (describe_room, estimate_room_area,
    generate_tier_notes) and returns how many entries were removed.

    Two real callers: (1) tests that assert on cache hit/miss behavior need
    to reset this module-global state between runs; (2) the admin panel's
    "Clear analysis cache" button (POST /api/admin/clear-analysis-cache) -
    a real operational need this cache created, not a hypothetical one: a
    WRONG describe_room() classification (e.g. Gemini once said a clearly
    non-room image was "workable") gets cached indefinitely (until process
    restart or LRU eviction at _MAXSIZE), so re-uploading that exact same
    file keeps getting served the same wrong verdict forever, even after a
    prompt fix ships - identical across every device/user that uploads
    byte-identical content, since the cache key is purely a hash of the
    image bytes. Previously the only fix was waiting for a full redeploy
    (which restarts the process); this lets an admin bust it instantly.
    """
    with _lock:
        removed = len(_cache)
        _cache.clear()
    return removed
