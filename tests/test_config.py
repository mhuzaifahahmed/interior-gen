from app.config import Settings


def _settings(database_url: str) -> Settings:
    return Settings(database_url=database_url)


def test_sqlite_url_passed_through_unchanged():
    assert _settings("sqlite:///data/app.db").resolved_database_url == "sqlite:///data/app.db"


def test_bare_postgres_scheme_normalized_to_postgresql():
    url = "postgres://user:pass@host/db"
    assert _settings(url).resolved_database_url == "postgresql://user:pass@host/db"


def test_channel_binding_query_param_stripped():
    url = "postgresql://user:pass@host/db?sslmode=require&channel_binding=require"
    resolved = _settings(url).resolved_database_url
    assert "channel_binding" not in resolved
    assert "sslmode=require" in resolved
    assert resolved.startswith("postgresql://user:pass@host/db?")


def test_postgres_scheme_and_channel_binding_both_fixed_together():
    url = "postgres://user:pass@host/db?channel_binding=require&sslmode=require"
    resolved = _settings(url).resolved_database_url
    assert resolved.startswith("postgresql://")
    assert "channel_binding" not in resolved
    assert "sslmode=require" in resolved


def test_plain_postgresql_url_without_channel_binding_untouched():
    url = "postgresql://user:pass@host/db?sslmode=require"
    assert _settings(url).resolved_database_url == url
