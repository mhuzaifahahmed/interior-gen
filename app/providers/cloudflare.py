import base64

import httpx

from app.config import settings

# Fallback strength if the caller doesn't pass one. Callers should normally pass a
# per-tier value from app/pipeline/prompts.py's STRENGTH_BY_TIER.
DEFAULT_STRENGTH = 0.6
NUM_STEPS = 20

# Tier-agnostic negative prompt: structure/quality guards that apply to every tier.
# Tier-specific exclusions (e.g. "chandelier" for budget/mid) are passed in by the
# caller via the negative_prompt param and appended - see app/pipeline/prompts.py.
BASE_NEGATIVE_PROMPT = (
    "different room layout, moved walls, moved windows, moved doors, changed "
    "camera angle, distorted architecture, blurry, low quality, deformed, "
    "cluttered, messy, overfilled with furniture"
)


class CloudflareImageProvider:
    """Image-to-image generation via Cloudflare Workers AI (free tier: 10,000
    neurons/day, no card required). Used for generate_image only - Gemini free
    tier no longer supports image generation as of Dec 2025.
    """

    def __init__(self) -> None:
        self._base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{settings.cloudflare_account_id}/ai/run/{settings.cloudflare_image_model}"
        )

    def generate_image(
        self,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        strength: float | None = None,
    ) -> bytes:
        combined_negative = BASE_NEGATIVE_PROMPT
        if negative_prompt:
            combined_negative = f"{combined_negative}, {negative_prompt}"

        payload = {
            "prompt": prompt,
            "negative_prompt": combined_negative,
            "image_b64": base64.b64encode(image_bytes).decode("ascii"),
            "strength": strength if strength is not None else DEFAULT_STRENGTH,
            "num_steps": NUM_STEPS,
        }
        response = httpx.post(
            self._base_url,
            headers={"Authorization": f"Bearer {settings.cloudflare_api_token}"},
            json=payload,
            timeout=120,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("image/"):
            return response.content

        # Some Workers AI responses wrap the image as base64 JSON instead of raw bytes.
        data = response.json()
        if not data.get("success", True):
            raise RuntimeError(f"Cloudflare Workers AI error: {data.get('errors')}")
        result = data.get("result", {})
        image_field = result.get("image") if isinstance(result, dict) else None
        if image_field:
            return base64.b64decode(image_field)

        raise RuntimeError(f"unexpected Cloudflare Workers AI response shape: {data}")
