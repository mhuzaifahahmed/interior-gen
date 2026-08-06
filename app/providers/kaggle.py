import base64
import logging
import threading

import httpx

from app.config import settings
from app.pipeline.prompts import (
    KAGGLE_PROMPT_MAX_WORDS,
    build_kaggle_negative_prompt,
    build_kaggle_prompt,
    extract_style_and_palette,
)

logger = logging.getLogger(__name__)

# One real test generation against this exact endpoint took ~78s - generous
# headroom over that for GPU cold-starts/queueing on a shared Kaggle session.
REQUEST_TIMEOUT_SECONDS = 240

# Real, live-observed failure (not theoretical): the room-redesign pipeline
# generates all 3 tiers concurrently (app/pipeline/generate.py's
# ThreadPoolExecutor(max_workers=3)), which is fine for OpenAI's API but a
# real Kaggle notebook's model instance is NOT thread-safe under that same
# load - a live 3-concurrent-request test reproduced "Expected all tensors to
# be on the same device, but got bias is on cpu, different from other tensors
# on cuda:0" on 2 of the 3 requests (the model's weights getting split across
# CPU/GPU mid-inference). That's a bug in the notebook's own request handling,
# not something fixable from this repo - so this MODULE-level lock (shared
# across every KaggleImageProvider instance, since HybridProvider/get_provider
# constructs a fresh one per request) serializes the actual HTTP calls to
# Kaggle instead: the pipeline still starts all 3 tiers concurrently
# (unchanged), only the outbound request to this one shared GPU is queued.
# Trade-off: total generation time becomes roughly additive across tiers
# (~3x a single call) instead of overlapping, same as this project's own
# documented pre-concurrency-fix baseline for OpenAI.
_request_lock = threading.Lock()


def _generate_url(base_url: str) -> str:
    # KAGGLE_API_URL has been observed set BOTH ways in practice (a bare
    # tunnel root, and a full URL with /generate already appended) - accept
    # either rather than silently producing a broken .../generate/generate
    # double-append.
    trimmed = base_url.rstrip("/")
    return trimmed if trimmed.endswith("/generate") else f"{trimmed}/generate"


class KaggleImageProvider:
    """Image-to-image editing via a user's own fine-tuned model, hosted in a
    Kaggle notebook and exposed through a Cloudflare quick tunnel
    (settings.kaggle_api_url) - an experimental alternate to
    OpenAIImageProvider, selected via IMAGE_PROVIDER=kaggle in .env (see
    HybridProvider/_default_room_image_provider in hybrid.py). Room-redesign
    only - "Build a House" renders always stay on OpenAI regardless of this
    setting (see HybridProvider's docstring for why).

    UNLIKE OpenAIImageProvider (gpt-image-1, an instruction-following editor),
    this is classic Stable-Diffusion-style img2img - the /generate endpoint
    accepts negative_prompt/num_inference_steps/guidance_scale, params that
    don't exist anywhere else in this codebase (deliberately removed along
    with the old Cloudflare/SD1.5 provider - see CLAUDE.md's "Provider split"
    section).

    PROMPT LENGTH: the model behind this endpoint uses a classic CLIP text
    encoder with a hard ~77-token limit, unlike gpt-image-1 (no meaningful
    limit) - the full build_prompt() text this provider receives as `prompt`
    (~550-650 words) would just get silently truncated by CLIP partway
    through a sentence. Per the user (this Kaggle model was trained
    specifically on layout/geometry preservation, making build_prompt()'s
    geometry-lock sentences redundant token spend for it): generate_image()
    does NOT send that full prompt through - it's shortened via
    app/pipeline/prompts.py's build_kaggle_prompt()/build_kaggle_negative_prompt()
    first (see that module's "KAGGLE MODEL PROMPT" section for the full
    reasoning). build_prompt() itself is completely untouched by this - the
    shortening happens here, only for this provider, on the already-assembled
    text it's handed.

    Real, live-tested contract (read from this project's own FastAPI
    /openapi.json, not assumed): POST {kaggle_api_url}/generate, JSON body
    {"image_base64": <bare base64, no data URI prefix>, "prompt": str,
    "negative_prompt"?: str}. Response: {"status": "success",
    "generated_image_base64": <bare base64 PNG>}. Confirmed via a real
    end-to-end test call (input + output both real images, not mocked).

    Ephemeral, dev-only endpoint - a Cloudflare quick tunnel tied to a live
    Kaggle notebook session, so expect it to go offline whenever that
    notebook stops. Flip IMAGE_PROVIDER back to "openai" in .env to revert
    instantly if so, no code change needed.
    """

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        short_prompt, negative_prompt = _prepare_kaggle_prompt(prompt, tier)

        image_b64 = base64.b64encode(image_bytes).decode()
        payload = {"image_base64": image_b64, "prompt": short_prompt}
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        # Serialized - see _request_lock's module-level comment for the real
        # concurrent-request failure this guards against.
        with _request_lock:
            response = httpx.post(
                _generate_url(settings.kaggle_api_url),
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        response.raise_for_status()

        data = response.json()
        image_b64_out = data.get("generated_image_base64")
        if not image_b64_out:
            logger.error("unexpected Kaggle image response shape: keys=%s", list(data.keys()))
            raise RuntimeError(f"unexpected Kaggle image response shape: {data}")
        return base64.b64decode(image_b64_out)


def _prepare_kaggle_prompt(full_prompt: str, tier: str | None) -> tuple[str, str | None]:
    """Shortens build_prompt()'s full output for this model's ~77-token CLIP
    limit - see KaggleImageProvider's docstring. build_prompt() itself is
    never modified; this only transforms the text on the way OUT to Kaggle.

    Normal path: tier is known (always is, in practice - the room-redesign
    pipeline always passes one) and app.pipeline.prompts.extract_style_and_palette()
    can find the style/palette that full_prompt was built with (reliable -
    see that function's docstring for why), so build_kaggle_prompt()/
    build_kaggle_negative_prompt() compose a real short keyword prompt from
    scratch using the SAME underlying style/tier/palette data build_prompt()
    used, not a text-truncation of its output.

    Fallback path: if tier is missing or style/palette can't be determined
    (e.g. called with a prompt that didn't come from build_prompt() at all,
    such as in an isolated test), falls back to naive word-truncation of
    full_prompt to KAGGLE_PROMPT_MAX_WORDS - cruder (may cut off mid-thought
    since it doesn't know sentence priority), but still respects the token
    budget and never crashes the request over a best-effort convenience path.
    """
    style, palette = extract_style_and_palette(full_prompt)
    if tier and style and palette:
        return build_kaggle_prompt(tier, style, palette), build_kaggle_negative_prompt(tier)

    logger.warning(
        "could not derive tier/style/palette from prompt for Kaggle shortening "
        "(tier=%r, style=%r, palette=%r) - falling back to naive word-truncation",
        tier,
        style,
        palette,
    )
    words = full_prompt.split()
    return " ".join(words[:KAGGLE_PROMPT_MAX_WORDS]), None
