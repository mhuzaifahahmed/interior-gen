import base64

import httpx

from app.config import settings

# GPT-image models are instruction-following edit models (like Nano Banana),
# not raw noise-based diffusion like SD1.5 - so unlike Cloudflare's provider,
# there's no dedicated negative_prompt or strength API parameter here:
#   - negative_prompt is folded into the positive prompt as an explicit "Avoid:"
#     instruction. This is expected to actually work here, unlike the SD1.5 case
#     documented in app/pipeline/prompts.py's module docstring (diffusion models
#     don't reliably obey in-prompt negation) - instruction-following models are
#     built specifically to follow exclusions stated in plain language.
#   - strength has no equivalent in this API at all. Accepted (for Provider
#     interface compatibility, so this can be swapped in for Cloudflare without
#     touching the pipeline) but silently ignored.
#
# COST LESSON (found via a real test call, not docs): `quality` alone does NOT
# control cost the way it first appears to. There's a SEPARATE `input_fidelity`
# param ("high"/"low") governing how much the input image is processed - and it
# defaults to "high" if omitted. A real smoke test with only `quality: "low"` set
# cost $0.10/image, ~20x the ~$0.005 "low quality" figure quoted in pricing
# articles (which describe generation, not edit, cost) - almost certainly because
# input_fidelity was silently defaulting to the expensive tier the whole time.
# Both params are now set explicitly. IMPORTANT TRADEOFF: input_fidelity isn't
# purely a cost knob - OpenAI's own docs describe it as controlling "fidelity to
# the original input image(s)", i.e. structure preservation, the one property
# this whole product depends on. OPENAI_IMAGE_INPUT_FIDELITY defaults to "low"
# for cheap tuning per the user's request, but if renders drift structurally,
# try "high" first before touching anything else - and expect the cost to jump
# accordingly.
#
# MODEL CHOICE (also found by hitting a real 400 error): gpt-image-2 does NOT
# accept input_fidelity at all - "The model 'gpt-image-2' does not support the
# 'input_fidelity' parameter." Only gpt-image-1 (and presumably -mini/-1.5)
# support it. That's why OPENAI_IMAGE_MODEL is gpt-image-1 despite its Oct 2026
# deprecation - a deliberate choice (input_fidelity control matters more than
# avoiding a future migration) not an oversight. If gpt-image-2 is ever
# reconsidered, know upfront it can't do a cheap-input-processing tier at all.
IMAGES_EDITS_URL = "https://api.openai.com/v1/images/edits"


class OpenAIImageProvider:
    """Image-to-image editing via OpenAI's official Images API - an experimental
    alternate image backend, not the default (see IMAGE_PROVIDER in config.py).
    Model/quality/input_fidelity are all configurable - see the cost lesson above
    before changing input_fidelity casually.
    """

    def generate_image(
        self,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        strength: float | None = None,
    ) -> bytes:
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}. Avoid: {negative_prompt}."

        response = httpx.post(
            IMAGES_EDITS_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            data={
                "model": settings.openai_image_model,
                "prompt": full_prompt,
                "quality": settings.openai_image_quality,
                "input_fidelity": settings.openai_image_input_fidelity,
            },
            files={"image": ("room.png", image_bytes, "image/png")},
            timeout=120,
        )
        response.raise_for_status()

        data = response.json()
        items = data.get("data", [])
        if not items or "b64_json" not in items[0]:
            raise RuntimeError(f"unexpected OpenAI image response shape: {data}")
        return base64.b64decode(items[0]["b64_json"])
