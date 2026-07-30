import base64

import httpx

from app.config import settings

# GPT-image models are instruction-following edit models (like Nano Banana), not
# raw noise-based diffusion - there's no negative_prompt or strength API parameter
# here. Exclusions ("no chandelier") are stated directly in the positive prompt
# text by build_prompt() (app/pipeline/prompts.py) instead, since instruction-
# following models are built to follow exclusions stated in plain language.
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

# Tiers that must stay tightly anchored to the input photo, so they're worth the
# input_fidelity="high" cost jump above. Real observed failures, not theory:
# premium's vivid luxury vocabulary (marble, brass, "designer ceiling") pulled
# gpt-image-1 toward a hallucinated generic luxury room (wrong column layout,
# narrower space) at "low" fidelity, even with prompts.py's structural-lock
# text present. Mid was added after the SAME failure shape - camera angle/room
# geometry drifting - persisted even after mid already had both prompt-level
# protections (PRESERVE_STRUCTURE's explicit "do not change the camera angle
# or perspective" line, plus mid's own structure_reminder restated after its
# material instructions, added for an earlier depth-flattening issue). Since
# the text-level fix was already in place and drift continued, this confirms
# it's a fidelity/anchoring-strength issue, not a prompt-wording gap - the same
# conclusion premium's fix was based on. Deliberately per-tier, not global, so
# the extra cost is spent only where drift was actually observed - economical
# has shown no such drift and stays on the cheap default.
HIGH_FIDELITY_TIERS = {"premium", "mid"}


class OpenAIImageProvider:
    """Image-to-image editing via OpenAI's official Images API - the sole image
    generation backend. Model/quality/input_fidelity are all configurable - see
    the cost lesson above before changing input_fidelity casually.
    """

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        input_fidelity = "high" if tier in HIGH_FIDELITY_TIERS else settings.openai_image_input_fidelity
        return self._edit_image(image_bytes, prompt, input_fidelity)

    def generate_house_render(self, image_bytes: bytes, prompt: str) -> bytes:
        # Shares the exact request shape with generate_image() via _edit_image -
        # kept as its own public method (not just calling generate_image
        # directly) so the "Build a House" feature's parameters never get
        # tangled with room-redesign's tier/fidelity semantics. Uses its own
        # dedicated openai_house_input_fidelity setting (not
        # openai_image_input_fidelity, which the Economical tier also reads)
        # so raising this feature's fidelity doesn't silently raise
        # Economical's cost/fidelity too.
        return self._edit_image(image_bytes, prompt, settings.openai_house_input_fidelity)

    def _edit_image(self, image_bytes: bytes, prompt: str, input_fidelity: str) -> bytes:
        response = httpx.post(
            IMAGES_EDITS_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            data={
                "model": settings.openai_image_model,
                "prompt": prompt,
                "quality": settings.openai_image_quality,
                "input_fidelity": input_fidelity,
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
