import base64
import logging

import httpx

from app.config import settings
from app.pipeline.prompts import (
    KAGGLE_PROMPT_MAX_WORDS,
    build_kaggle_negative_prompt,
    build_kaggle_prompt,
    extract_style_and_palette,
)

logger = logging.getLogger(__name__)

# Modal deployments have no free-tier proxy timeout the way Kaggle's
# Cloudflare quick tunnel did (~100s, the thing that forced kaggle.py's
# submit-then-poll job pattern) - a plain blocking call is safe here.
# Generous headroom over real observed timings anyway (~45-95s for a single
# image, ~90-115s for a 3-tier batch, including any cold-start).
REQUEST_TIMEOUT_SECONDS = 300
BATCH_REQUEST_TIMEOUT_SECONDS = 400

# House renders can take longer on a cold container (first request after the
# container scales down re-downloads/loads the model) - generous ceiling.
HOUSE_REQUEST_TIMEOUT_SECONDS = 400

# Real, live-observed behavior (not theoretical): Modal's own web endpoints
# can respond with a 303 redirect carrying a "__modal_attempt_token" query
# param on a cold-start/retry - part of Modal's own request-tracking
# protocol, not an error. httpx does NOT follow redirects by default, so
# every call here must set follow_redirects=True or a legitimate cold-start
# response gets raised as an HTTPStatusError instead of completing normally.
FOLLOW_REDIRECTS = True


class ModalImageProvider:
    """Image-to-image editing via self-hosted models on Modal
    (https://modal.com) - the active room-redesign backend (image_provider
    setting) and, separately, an available "Build a House" render backend
    (house_image_provider setting). Replaces the earlier Kaggle-notebook +
    Cloudflare-tunnel setup for room-redesign - see app/config.py's
    image_provider comment for the full migration story (Kaggle's phone
    verification blocked hosting a second model; Modal's OAuth-only signup
    doesn't).

    Same underlying model stack as the old Kaggle notebook for room-redesign
    (RealVisXL_V5.0 + xinsir/controlnet-depth-sdxl-1.0, classic SD-style
    img2img with negative_prompt/num_inference_steps/guidance_scale - NOT an
    instruction-following editor like gpt-image-1, same PROMPT LENGTH caveat
    as KaggleImageProvider: build_prompt()'s full ~600-word output is
    shortened via _prepare_kaggle_prompt() before being sent, since this
    model's CLIP text encoder has the same ~77-token limit). House-render
    generation uses a separate Modal deployment (modal_house_generation.py)
    with its own model instance, prompt built by app/pipeline/house_prompts.py
    and passed through unmodified (no length-shortening - Build a House
    prompts are already capped at USER_PROMPT_MAX_CHARS upstream).

    Real, live-tested contracts (both deployments' /docs, confirmed via
    actual end-to-end test calls, not assumed):
    - POST {modal_room_redesign_url}, JSON {"image_base64", "prompt",
      "negative_prompt"?, "num_inference_steps"?, "guidance_scale"?,
      "control_scale"?} -> {"status": "success"|"error",
      "generated_image_base64"?, "detail"?}
    - POST {modal_room_redesign_batch_url}, JSON {"items": [{"image_base64",
      "prompt", "negative_prompt"?}, ...], "num_inference_steps"?,
      "guidance_scale"?, "control_scale"?, "resolution"?} -> {"status",
      "generated_images_base64"?}. Runs a real batched GPU pass (list-valued
      prompt/image args to the diffusers pipeline) with an automatic
      OOM-safe fallback to sequential per-image generation server-side if a
      batch-of-3 exceeds the T4's VRAM at the requested resolution - see
      modal_room_redesign.py's own comments for why 768x768 (not 1024) is
      the batch default.
    - POST {modal_house_url}, JSON {"image_base64", "prompt",
      "negative_prompt"?, "num_inference_steps"?, "guidance_scale"?,
      "control_scale"?} -> same {"status", "generated_image_base64"?} shape,
      1024x1024, no batching (Build a House only ever produces one render).

    Permanent URLs (unlike Kaggle's ephemeral tunnel) - `modal deploy` prints
    them once and they don't change on their own, so settings.modal_*_url
    should rarely if ever need updating after the first deploy.
    """

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        short_prompt, negative_prompt = _prepare_kaggle_prompt(prompt, tier)

        payload = {
            "image_base64": base64.b64encode(image_bytes).decode(),
            "prompt": short_prompt,
            "num_inference_steps": settings.modal_num_inference_steps,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        response = httpx.post(
            settings.modal_room_redesign_url,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=FOLLOW_REDIRECTS,
        )
        response.raise_for_status()
        return _decode_single(response.json())

    def supports_batch(self) -> bool:
        return True

    def generate_images_batch(
        self, image_bytes: bytes, tier_prompts: dict[str, str]
    ) -> dict[str, bytes]:
        image_b64 = base64.b64encode(image_bytes).decode()
        tiers = list(tier_prompts.keys())

        items = []
        for tier in tiers:
            short_prompt, negative_prompt = _prepare_kaggle_prompt(tier_prompts[tier], tier)
            item = {"image_base64": image_b64, "prompt": short_prompt}
            if negative_prompt:
                item["negative_prompt"] = negative_prompt
            items.append(item)

        payload = {
            "items": items,
            "num_inference_steps": settings.modal_num_inference_steps,
            "resolution": settings.modal_batch_resolution,
        }

        response = httpx.post(
            settings.modal_room_redesign_batch_url,
            json=payload,
            timeout=BATCH_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=FOLLOW_REDIRECTS,
        )
        response.raise_for_status()

        data = response.json()
        if data.get("status") != "success":
            logger.error("Modal batch generation failed: %s", data.get("detail"))
            raise RuntimeError(f"Modal batch generation failed: {data.get('detail')}")

        images_b64_out = data.get("generated_images_base64")
        if not images_b64_out or len(images_b64_out) != len(tiers):
            logger.error(
                "unexpected Modal batch response shape: keys=%s, count=%s (expected %s)",
                list(data.keys()),
                len(images_b64_out) if images_b64_out else 0,
                len(tiers),
            )
            raise RuntimeError(f"unexpected Modal batch response shape: {data}")

        return {tier: base64.b64decode(b64) for tier, b64 in zip(tiers, images_b64_out)}

    def generate_house_render(self, image_bytes: bytes, prompt: str) -> bytes:
        """"Build a House" exterior/interior concept render. Prompt is passed
        through as-is (unlike generate_image, no shortening) - house prompts
        are already length-capped upstream by house_prompts.py."""
        payload = {
            "image_base64": base64.b64encode(image_bytes).decode(),
            "prompt": prompt,
        }

        response = httpx.post(
            settings.modal_house_url,
            json=payload,
            timeout=HOUSE_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=FOLLOW_REDIRECTS,
        )
        response.raise_for_status()
        return _decode_single(response.json())


def _decode_single(data: dict) -> bytes:
    if data.get("status") != "success":
        logger.error("Modal image generation failed: %s", data.get("detail"))
        raise RuntimeError(f"Modal image generation failed: {data.get('detail')}")

    image_b64_out = data.get("generated_image_base64")
    if not image_b64_out:
        logger.error("unexpected Modal image response shape: keys=%s", list(data.keys()))
        raise RuntimeError(f"unexpected Modal image response shape: {data}")
    return base64.b64decode(image_b64_out)


def _prepare_kaggle_prompt(full_prompt: str, tier: str | None) -> tuple[str, str | None]:
    """Same CLIP ~77-token-limit shortening as kaggle.py's helper of the same
    name - this Modal deployment runs the identical classic-SD-style model,
    so the same prompt-length constraint and shortening approach applies.
    Kept as a local copy (not imported from kaggle.py) so this provider has
    zero dependency on the now-dormant Kaggle module."""
    style, palette = extract_style_and_palette(full_prompt)
    if tier and style and palette:
        return build_kaggle_prompt(tier, style, palette), build_kaggle_negative_prompt(tier)

    logger.warning(
        "could not derive tier/style/palette from prompt for Modal shortening "
        "(tier=%r, style=%r, palette=%r) - falling back to naive word-truncation",
        tier,
        style,
        palette,
    )
    words = full_prompt.split()
    return " ".join(words[:KAGGLE_PROMPT_MAX_WORDS]), None
