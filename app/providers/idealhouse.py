import logging

logger = logging.getLogger(__name__)

# FLOOR-PLAN VENDOR: NOT YET WIRED IN. This module is a deliberate placeholder,
# not a bug - see the "Build a House" plan for the full research trail. Two
# vendors were evaluated:
#
# - ModelsLab's Floor Planning API (https://docs.modelslab.com/interior-api/
#   floor-planning) - ruled out entirely. Its input is an EXISTING INTERIOR
#   ROOM PHOTO (SD1.5-style img2img with strength/guidance_scale params), takes
#   NO dimension parameters, and doesn't accept a plot/land photo at all. Wrong
#   tool for this feature regardless of price.
# - ideal.house's Floor Plan API (https://ideal.house/api/docs/floor-plan-api)
#   - real and working, but (a) NO free tier - credits-only, $39 minimum
#   purchase (1,000 credits @ $0.039/credit), ~$0.39/generation at its cheap
#   "Base" tier (10 credits), ~$0.78 at "Pro" (20 credits) - a real recurring
#   cost with no trial; and (b) CANNOT honor exact dimensions or irregular plot
#   shapes - it only accepts bedrooms/bathrooms/a coarse gross-area RANGE
#   (e.g. "100-120m²")/extras/a text prompt, plus an OPTIONAL REFERENCE
#   FLOOR-PLAN image (not a land photo). There is no way to feed in an exact
#   length x width or a corner-point boundary as a real geometric constraint -
#   at best that data would reach the generator as loose descriptive text.
#
# User decision: hold off on any floor-plan vendor integration for now. This
# stub keeps the Provider interface (generate_floor_plan) real and exercised -
# HybridProvider composes this module in as the default floor_plan_provider -
# so wiring in a real vendor later is a change to ONLY this file (the
# create-task/poll-result HTTP calls to ideal.house's API), with zero changes
# needed to the pipeline, API, or frontend.
#
# ideal.house's actual request shape, recorded here so implementing it later
# doesn't require re-research:
#   Create: POST https://api.ideal.house/api/v1/floorPlan/generate
#     headers: {"APIKEY": <key>, "Content-Type": "application/json"}
#     body: {"bedrooms": int, "bathrooms": int, "kitchenType": "open"|"closed",
#            "grossArea": "<range string>", "extras": "<comma-separated>",
#            "prompt": str, "refImageUrl": str (optional), "modelType": "Base"|"Pro"}
#     response: {"code": 0, "message": "success", "data": <taskId>}
#   Poll: GET https://api.ideal.house/api/v1/floorPlan/result?taskId=<id>
#     headers: {"APIKEY": <key>}
#     response: {"code": 0, "data": {"status": "Success"|"Processing"|
#                "Unprocessed"|"Failed", "percentage": int,
#                "output": {"resultUrl": str, "width": int, "height": int}}}
#     (async - poll every 3-5s until status is terminal)


def generate_floor_plan(
    plot_description: str | None, dimensions: dict, prompt: str, room_layout: dict | None = None
) -> list[bytes] | None:
    """Always returns None - no floor-plan vendor is configured yet (see module
    docstring above). Matches Provider.generate_floor_plan's best-effort
    contract: None means "not available," which the pipeline treats as an
    expected, non-fatal state, not a crash. room_layout is accepted for
    interface compatibility (see base.py's docstring) but unused - this stub
    has no real conditioning to build.
    """
    logger.info("floor-plan vendor not configured; skipping floor plan generation")
    return None
