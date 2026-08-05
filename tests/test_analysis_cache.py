from app.providers import analysis_cache


def test_miss_on_empty_cache():
    assert analysis_cache.get("describe_room", b"abc") is analysis_cache.MISS


def test_set_then_get_returns_the_value():
    analysis_cache.set("describe_room", b"abc", "a cozy room")
    assert analysis_cache.get("describe_room", b"abc") == "a cozy room"


def test_different_bytes_never_collide():
    analysis_cache.set("describe_room", b"photo-a", "room A")
    analysis_cache.set("describe_room", b"photo-b", "room B")
    assert analysis_cache.get("describe_room", b"photo-a") == "room A"
    assert analysis_cache.get("describe_room", b"photo-b") == "room B"


def test_different_kinds_for_the_same_bytes_never_collide():
    # describe_room and generate_tier_notes both key on the same image bytes
    # - the "kind" tag in the cache key must keep them from overwriting
    # each other's entries.
    analysis_cache.set("describe_room", b"same-photo", "a description")
    analysis_cache.set("generate_tier_notes", b"same-photo", {"economical": "note"})
    assert analysis_cache.get("describe_room", b"same-photo") == "a description"
    assert analysis_cache.get("generate_tier_notes", b"same-photo") == {"economical": "note"}


def test_a_real_none_result_is_a_cache_hit_not_a_miss():
    # None is a legitimate cached value (e.g. estimate_room_area found no
    # parseable number) - must be distinguishable from "never cached".
    analysis_cache.set("estimate_room_area", b"photo", None)
    result = analysis_cache.get("estimate_room_area", b"photo")
    assert result is None
    assert result is not analysis_cache.MISS


def test_lru_eviction_drops_the_least_recently_used_entry():
    for i in range(analysis_cache._MAXSIZE):
        analysis_cache.set("describe_room", str(i).encode(), i)

    # One more entry should evict the oldest (index 0), not something else.
    analysis_cache.set("describe_room", b"new-entry", "newest")

    assert analysis_cache.get("describe_room", b"0") is analysis_cache.MISS
    assert analysis_cache.get("describe_room", b"new-entry") == "newest"
    assert analysis_cache.get("describe_room", str(analysis_cache._MAXSIZE - 1).encode()) == analysis_cache._MAXSIZE - 1


def test_getting_an_entry_marks_it_recently_used():
    for i in range(analysis_cache._MAXSIZE):
        analysis_cache.set("describe_room", str(i).encode(), i)

    # Touch the oldest entry so it's no longer the least-recently-used one.
    analysis_cache.get("describe_room", b"0")
    analysis_cache.set("describe_room", b"new-entry", "newest")

    # Index 0 survives (just touched); index 1 (now the true LRU) is evicted.
    assert analysis_cache.get("describe_room", b"0") == 0
    assert analysis_cache.get("describe_room", b"1") is analysis_cache.MISS


def test_clear_empties_the_cache():
    analysis_cache.set("describe_room", b"abc", "value")
    analysis_cache.clear()
    assert analysis_cache.get("describe_room", b"abc") is analysis_cache.MISS
