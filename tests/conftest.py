import pytest

from app.providers import analysis_cache


@pytest.fixture(autouse=True)
def _reset_analysis_cache():
    """The Gemini analysis cache (app/providers/analysis_cache.py) is
    deliberately module/process-global, not per-provider-instance - see its
    module docstring for why. That means it persists across tests in the
    same pytest process unless reset, which silently breaks any test that
    reuses the same fixed sample image bytes another test already cached a
    result for (a real failure this fixture fixes - two GeminiProvider
    tests using the same tiny sample PNG were seeing each other's cached
    results). Autouse so no test has to remember to do this itself.
    """
    analysis_cache.clear()
    yield
    analysis_cache.clear()
