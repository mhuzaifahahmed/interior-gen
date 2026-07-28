import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://serpapi.com/search"

# Each call is billed as 1 search regardless of how many results come back
# (SerpApi's 250/month free tier is metered per-call, not per-result), so
# asking for more results per search is a free improvement to real-item
# coverage now that each search targets one specific item (see gemini.py's
# generate_materials) - more headroom for that item's real price to actually
# appear somewhere in the returned results.
NUM_RESULTS = 12


def search(query: str, location: str | None = None) -> list[dict]:
    """Run one real Google search via SerpApi and return organic results as
    [{"title", "link", "snippet"}]. Raises on any failure (network, bad key,
    quota exhausted) - callers must catch this and fall back, same contract as
    every other best-effort external call in this codebase.

    location biases results toward that geographic area (SerpApi's `location`
    param, same field shown in their playground UI) - without it, results skew
    toward whatever global/US retailers rank highest for the query text alone,
    which is what caused a real observed bug: prices for a Karachi search came
    back in USD from Home Depot/Alibaba instead of local PKR listings.
    """
    params = {"engine": "google", "q": query, "api_key": settings.serpapi_api_key, "num": NUM_RESULTS}
    if location:
        params["location"] = location

    # Bumped from 20s after a real observed ReadTimeout on one item's search
    # during live testing - that one item's search failing is non-fatal (see
    # generate_materials's per-item try/except) but still worth padding to
    # cut down on losing real coverage to a slow-but-would-have-succeeded call.
    response = httpx.get(SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    error = data.get("search_metadata", {}).get("status")
    if error and error != "Success":
        raise RuntimeError(f"SerpApi search failed: {data.get('error', error)}")

    results = []
    for item in data.get("organic_results", [])[:NUM_RESULTS]:
        if not item.get("link"):
            continue
        results.append(
            {
                "title": item.get("title", ""),
                "link": item["link"],
                "snippet": item.get("snippet", ""),
            }
        )
    return results
