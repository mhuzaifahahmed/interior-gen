import time

import pytest
from sqlmodel import SQLModel, create_engine

import app.dynamic_settings as dynamic_settings_module
from app.dynamic_settings import (
    KAGGLE_API_URL_KEY,
    KAGGLE_HOUSE_API_URL_KEY,
    get_all_effective_urls,
    get_effective_url,
    invalidate_cache,
    set_effective_url,
)


@pytest.fixture(autouse=True)
def _isolated_engine(monkeypatch):
    """A fresh in-memory engine per test, plus a cache reset - the module-level
    cache and DB must both start clean so tests don't leak state into each
    other (a real risk given the cache is process-global by design)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(dynamic_settings_module, "engine", engine)
    invalidate_cache()
    yield
    invalidate_cache()


def test_get_effective_url_falls_back_to_env_when_nothing_is_set():
    assert get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com") == (
        "https://env-default.example.com"
    )


def test_set_effective_url_overrides_the_env_fallback():
    set_effective_url(KAGGLE_API_URL_KEY, "https://new-tunnel.trycloudflare.com")
    assert get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com") == (
        "https://new-tunnel.trycloudflare.com"
    )


def test_set_effective_url_with_empty_string_clears_the_override():
    set_effective_url(KAGGLE_API_URL_KEY, "https://new-tunnel.trycloudflare.com")
    set_effective_url(KAGGLE_API_URL_KEY, "")
    assert get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com") == (
        "https://env-default.example.com"
    )


def test_set_effective_url_strips_whitespace():
    set_effective_url(KAGGLE_API_URL_KEY, "  https://new-tunnel.trycloudflare.com  ")
    assert get_effective_url(KAGGLE_API_URL_KEY, "fallback") == "https://new-tunnel.trycloudflare.com"


def test_different_keys_are_independent():
    set_effective_url(KAGGLE_API_URL_KEY, "https://room.example.com")
    assert get_effective_url(KAGGLE_HOUSE_API_URL_KEY, "https://house-env.example.com") == (
        "https://house-env.example.com"
    )


def test_set_effective_url_invalidates_the_cache_immediately():
    # A stale cached value must never linger after an explicit write - the
    # whole point of this feature is fast turnaround, not waiting out the TTL.
    get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com")  # warms the cache with ""
    set_effective_url(KAGGLE_API_URL_KEY, "https://new-tunnel.trycloudflare.com")
    assert get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com") == (
        "https://new-tunnel.trycloudflare.com"
    )


def test_get_effective_url_caches_within_ttl(monkeypatch):
    # Simulate a DB failure AFTER the first successful read - if the cache
    # weren't working, the second call would also hit the (now broken) DB and
    # fall back to the raw fallback instead of the cached real value.
    set_effective_url(KAGGLE_API_URL_KEY, "https://cached-value.example.com")
    first = get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com")
    assert first == "https://cached-value.example.com"

    monkeypatch.setattr(dynamic_settings_module, "engine", None)  # would break a real DB call
    second = get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com")
    assert second == "https://cached-value.example.com"


def test_get_effective_url_fails_open_on_db_error(monkeypatch):
    monkeypatch.setattr(dynamic_settings_module, "engine", None)  # Session(None) raises
    assert get_effective_url(KAGGLE_API_URL_KEY, "https://env-default.example.com") == (
        "https://env-default.example.com"
    )


def test_get_all_effective_urls_reports_source():
    set_effective_url(KAGGLE_API_URL_KEY, "https://overridden.example.com")
    result = get_all_effective_urls(
        {
            KAGGLE_API_URL_KEY: "https://room-env.example.com",
            KAGGLE_HOUSE_API_URL_KEY: "https://house-env.example.com",
        }
    )
    assert result[KAGGLE_API_URL_KEY] == {"value": "https://overridden.example.com", "source": "database"}
    assert result[KAGGLE_HOUSE_API_URL_KEY] == {"value": "https://house-env.example.com", "source": "env"}
