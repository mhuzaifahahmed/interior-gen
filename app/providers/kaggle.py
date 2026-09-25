import base64
import logging
import threading
import time

import httpx

from app.config import settings
from app.pipeline.prompts import (
    KAGGLE_PROMPT_MAX_WORDS,
    build_kaggle_negative_prompt,
    build_kaggle_prompt,
    extract_style_and_palette,
)
from app.providers.session_errors import classify_kaggle_failure

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


def _generate_batch_url(base_url: str) -> str:
    # Same bare-root-vs-full-URL ambiguity as _generate_url above, but for
    # the newer /generate_batch endpoint - if the base URL already ends in
    # /generate (the single-image endpoint), swap it out rather than
    # appending onto it.
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/generate_batch"):
        return trimmed
    if trimmed.endswith("/generate"):
        trimmed = trimmed[: -len("/generate")]
    return f"{trimmed}/generate_batch"


def _room_base(base_url: str) -> str:
    # Same bare-root-vs-full-URL ambiguity as _generate_url/_generate_batch_url
    # above - strip whichever known suffix (if any) got pasted onto the raw
    # tunnel root, so the health check below always probes the bare root
    # regardless of how KAGGLE_API_URL happens to be set.
    trimmed = base_url.rstrip("/")
    for suffix in ("/generate_batch", "/generate"):
        if trimmed.endswith(suffix):
            return trimmed[: -len(suffix)]
    return trimmed


# Lightweight "is anything listening" probe, not a real generation call - a
# slow/hanging response here already answers the question (effectively
# offline for a user waiting on it), so this stays short.
HEALTH_CHECK_TIMEOUT_SECONDS = 6


def check_connection() -> bool:
    """Best-effort liveness probe for the Kaggle room-redesign tunnel - used
    by GET /api/room-model-status (app/main.py) to tell the user upfront
    whether "Our Model" is actually reachable right now, before they submit
    a generation that would otherwise silently fall back to OpenAI (see
    HybridProvider's runtime fallback + get_image_model_label(), which only
    ever reveals this AFTER results land). Any real HTTP response (even an
    error status - the notebook has no route at "/") means the tunnel and
    the notebook's own FastAPI server are both up; only a connection-level
    failure (refused, timed out, DNS failure, Cloudflare edge error) means
    the session is actually offline - the same "session offline" failure
    shape session_errors.classify_kaggle_failure() distinguishes elsewhere,
    reused here via a plain try/except rather than importing that
    exception-classification helper for a boolean this simple.
    """
    if not settings.kaggle_api_url:
        return False
    try:
        httpx.get(_room_base(settings.kaggle_api_url), timeout=HEALTH_CHECK_TIMEOUT_SECONDS)
        return True
    except Exception:
        return False


def _generate_batch_status_url(base_url: str, job_id: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/generate_batch"):
        trimmed = trimmed[: -len("/generate_batch")]
    elif trimmed.endswith("/generate"):
        trimmed = trimmed[: -len("/generate")]
    return f"{trimmed}/generate_batch/status/{job_id}"


def _elevation_base(base_url: str) -> str:
    # KAGGLE_HOUSE_API_URL is a bare Cloudflare tunnel root in practice, but
    # tolerate a trailing /generate_elevation (or the room model's /generate)
    # having been pasted on by accident, same defensive normalization as the
    # room helpers above.
    trimmed = base_url.rstrip("/")
    for suffix in ("/generate_elevation", "/generate_batch", "/generate"):
        if trimmed.endswith(suffix):
            return trimmed[: -len(suffix)]
    return trimmed


def _generate_elevation_url(base_url: str) -> str:
    return f"{_elevation_base(base_url)}/generate_elevation"


def _generate_elevation_status_url(base_url: str, job_id: str) -> str:
    return f"{_elevation_base(base_url)}/generate_elevation/status/{job_id}"


# The job-submit and each status-poll call are both near-instant server-side
# (a dict write/lookup) - short timeout is enough; a slow response to either
# of these specifically would indicate the tunnel/notebook is actually down,
# not just busy generating.
BATCH_SUBMIT_TIMEOUT_SECONDS = 20
BATCH_POLL_TIMEOUT_SECONDS = 20
BATCH_POLL_INTERVAL_SECONDS = 3

# Generous ceiling on total wall-clock time spent polling for one batch job
# before giving up and raising - a real batch-of-3 at 1024x1024 took ~98s for
# denoising alone plus a possible OOM-triggered sequential fallback on top;
# 768x768 (see settings.kaggle_batch_resolution) should be meaningfully
# faster, but this stays generous rather than tight since going over this
# ceiling just raises (falls back to OpenAI via HybridProvider), it doesn't
# fail silently.
BATCH_POLL_MAX_SECONDS = 420


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
    "negative_prompt"?: str, "num_inference_steps"?: int (endpoint default
    30), "guidance_scale"?: float (endpoint default 7.5, not currently sent -
    only steps proved worth tuning after a real side-by-side comparison)}.
    Response: {"status": "success", "generated_image_base64": <bare base64
    PNG>}. Confirmed via a real end-to-end test call (input + output both
    real images, not mocked). num_inference_steps is always sent explicitly
    (settings.kaggle_num_inference_steps, default 20 - see config.py) rather
    than left to the endpoint's own default of 30, since a real timing+visual
    comparison showed 20 as the accepted speed/quality middle ground.

    Ephemeral, dev-only endpoint - a Cloudflare quick tunnel tied to a live
    Kaggle notebook session, so expect it to go offline whenever that
    notebook stops. Flip IMAGE_PROVIDER back to "openai" in .env to revert
    instantly if so, no code change needed.
    """

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        short_prompt, negative_prompt = _prepare_kaggle_prompt(prompt, tier)

        image_b64 = base64.b64encode(image_bytes).decode()
        payload = {
            "image_base64": image_b64,
            "prompt": short_prompt,
            "num_inference_steps": settings.kaggle_num_inference_steps,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        # Serialized - see _request_lock's module-level comment for the real
        # concurrent-request failure this guards against.
        try:
            with _request_lock:
                response = httpx.post(
                    _generate_url(settings.kaggle_api_url),
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            response.raise_for_status()
        except Exception as exc:
            # See app/providers/session_errors.py - raising this specific
            # type instead of the raw httpx error doesn't change behavior
            # (HybridProvider still catches it and falls back to OpenAI
            # exactly as before), it just makes the resulting log line
            # (logger.exception in hybrid.py) say plainly "session appears
            # offline" instead of a generic connection traceback.
            session_error = classify_kaggle_failure(exc, "room-redesign")
            if session_error:
                raise session_error from exc
            raise

        data = response.json()
        image_b64_out = data.get("generated_image_base64")
        if not image_b64_out:
            logger.error("unexpected Kaggle image response shape: keys=%s", list(data.keys()))
            raise RuntimeError(f"unexpected Kaggle image response shape: {data}")
        return base64.b64decode(image_b64_out)

    def supports_batch(self) -> bool:
        return True

    def generate_images_batch(
        self, image_bytes: bytes, tier_prompts: dict[str, str]
    ) -> dict[str, bytes]:
        """Generates all tiers in ONE GPU pass via the notebook's
        /generate_batch endpoint (added specifically to fix a real,
        live-observed problem: the room-redesign pipeline fires all 3 tiers
        concurrently, but a single Kaggle model instance is NOT thread-safe
        under simultaneous /generate calls - see _request_lock's docstring
        above for the exact crash this caused, and CLAUDE.md's "Kaggle Model
        Thread-Safety Crash" history). /generate_batch runs the diffusers
        pipeline once with LIST-valued prompt/image args instead of 3
        separate serialized calls, so tiers are no longer purely additive in
        wall-clock time - real GPU batching, not 3x request serialization.

        SUBMIT-THEN-POLL, not one blocking call - a real live test showed the
        free Cloudflare quick tunnel (trycloudflare.com) hard-kills any
        request still open past ~100s with a 524, independent of whether the
        GPU work itself succeeds (a batch-of-3 at 1024x1024 took ~98s for
        denoising ALONE). Blocking on one HTTP call for the whole job would
        always be at risk of losing a real, in-progress result to the
        tunnel's own timeout. Instead: POST /generate_batch registers a job
        and returns almost instantly ({"status": "started", "job_id": str}),
        then GET /generate_batch/status/{job_id} is polled every
        BATCH_POLL_INTERVAL_SECONDS - each poll is also near-instant (a dict
        lookup server-side) - until the job reports "done" (with
        "generated_images_base64") or "failed" (with "detail"). No single
        request in this flow is ever open long enough to risk the tunnel's
        timeout, regardless of how long generation actually takes.

        tier_prompts is {tier: full_build_prompt_output} for each of the 3
        tiers - each is independently shortened via _prepare_kaggle_prompt()
        exactly as generate_image() does for a single tier, preserving the
        existing CLIP-token-limit and per-tier negative-prompt behavior.

        Sent at settings.kaggle_batch_resolution (768, not the single-image
        endpoint's 1024) - a real batch-of-3 test OOM'd during VAE decode at
        1024x1024 even with attention/VAE slicing enabled; 768 cuts both
        compute and peak VRAM meaningfully.
        """
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
            "num_inference_steps": settings.kaggle_num_inference_steps,
            "resolution": settings.kaggle_batch_resolution,
        }

        # No _request_lock needed here - the notebook's own generation_lock
        # serializes GPU access server-side, and this submit call is
        # near-instant anyway (nothing left to serialize client-side).
        try:
            submit_response = httpx.post(
                _generate_batch_url(settings.kaggle_api_url),
                json=payload,
                timeout=BATCH_SUBMIT_TIMEOUT_SECONDS,
            )
            submit_response.raise_for_status()
        except Exception as exc:
            session_error = classify_kaggle_failure(exc, "room-redesign")
            if session_error:
                raise session_error from exc
            raise

        submit_data = submit_response.json()
        job_id = submit_data.get("job_id")
        if not job_id:
            logger.error("unexpected Kaggle batch submit response shape: %s", submit_data)
            raise RuntimeError(f"unexpected Kaggle batch submit response shape: {submit_data}")

        status_url = _generate_batch_status_url(settings.kaggle_api_url, job_id)
        deadline = time.monotonic() + BATCH_POLL_MAX_SECONDS

        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Kaggle batch job {job_id} did not complete within {BATCH_POLL_MAX_SECONDS}s"
                )

            time.sleep(BATCH_POLL_INTERVAL_SECONDS)

            try:
                poll_response = httpx.get(status_url, timeout=BATCH_POLL_TIMEOUT_SECONDS)
                poll_response.raise_for_status()
            except Exception as exc:
                session_error = classify_kaggle_failure(exc, "room-redesign")
                if session_error:
                    raise session_error from exc
                raise
            job = poll_response.json()
            status = job.get("status")

            if status == "done":
                images_b64_out = job.get("generated_images_base64")
                if not images_b64_out or len(images_b64_out) != len(tiers):
                    logger.error(
                        "unexpected Kaggle batch job result shape: keys=%s, count=%s (expected %s)",
                        list(job.keys()),
                        len(images_b64_out) if images_b64_out else 0,
                        len(tiers),
                    )
                    raise RuntimeError(f"unexpected Kaggle batch job result shape: {job}")
                return {tier: base64.b64decode(b64) for tier, b64 in zip(tiers, images_b64_out)}

            if status == "failed":
                raise RuntimeError(f"Kaggle batch job {job_id} failed: {job.get('detail')}")

            # status == "running" (or any other in-progress value) - keep polling.

    def generate_house_render(
        self,
        image_bytes: bytes | None,
        prompt: str,
        floor_count: int | None = None,
        wants_garage: bool | None = None,
        color: str | None = None,
        style: str | None = None,
    ) -> bytes:
        """"Build a House" front-elevation render, via a SEPARATE Kaggle
        notebook/tunnel from room-redesign's (settings.kaggle_house_api_url,
        its own account/model - see that setting's comment in config.py).

        REAL, CONFIRMED contract (read from the elevation notebook's own
        FastAPI code - kaggle_notebooks/elevation_server.py in this repo is a
        version-controlled reference copy of what gets pasted into Kaggle):
        this is a TEXT-TO-IMAGE model (RealVisXL_V4.0 SDXL), NOT img2img - it
        generates a photorealistic front elevation from scratch and takes NO
        input image at all, so image_bytes is accepted only for interface
        parity and deliberately IGNORED here (the plot photo is optional as of
        2026-09, and this backend never needed it - see
        HybridProvider.house_render_needs_photo()).

        SUBMIT-THEN-POLL, same reason as generate_images_batch() above: the
        free Cloudflare quick tunnel hard-kills any request open past ~100s
        with a 524, and a single 28-step RealVisXL render on a T4 (plus a
        possible cold model load) can exceed that. POST {base}/generate_elevation
        registers a job and returns almost instantly ({"status": "started",
        "job_id": str}); GET {base}/generate_elevation/status/{job_id} is then
        polled until "done" ("generated_image_base64") or "failed" ("detail").

        The APP sends a MINIMAL request (see build_house_elevation_prompt in
        house_prompts.py): a short `prompt` = the user's own exterior
        requirements text (NOT a long, detailed, error-prone paragraph - the
        notebook owns all the heavy camera-framing/photoreal/negative-prompt
        scaffolding + the per-floor-count aspect-ratio rules itself, exactly
        like room-redesign's build_kaggle_prompt() keeps the app-side prompt
        short and lets the model do the rest), plus `floors` as a STRUCTURED
        int (drives the notebook's story-count + aspect-ratio logic reliably,
        instead of hoping the model parses a count out of free text), plus the
        plot width for context. Empty prompt is fine - the notebook falls back
        to its own sensible default exterior features.

        NOT best-effort (unlike the room-redesign methods above): this
        exception propagates to house_project.error and is shown to the user
        verbatim via showHouseError(), so a friendly, actionable message here
        (via classify_kaggle_failure) matters. No _request_lock - separate
        account/notebook/GPU from room-redesign's.

        color (2026-09-18, v15) is the resolved COLOR_PROFILE words for the
        user's chosen exterior palette (house_prompts.color_palette_words()) -
        a STRUCTURED field, same treatment as `garage` below, not embedded in
        `prompt`. Real reason it's structured rather than more prompt text: an
        earlier version appended the palette as a trailing text clause onto
        `prompt`, but the notebook wraps that text inside its OWN prompt
        scaffolding, which hardcodes competing color/material vocabulary
        ("dark textured stone", etc.) with no dedicated slot for an appended
        clause - the palette could be drowned out. Sending it structured lets
        the notebook place it in a dedicated, high-priority position instead
        (see elevation_server.py's _build_prompt()). Omitted when falsy so an
        un-updated notebook keeps its old behavior (no color signal).

        style (2026-09-18, v16) is the resolved HOUSE_STYLE_PROFILES words
        for the user's chosen exterior architectural style
        (house_prompts.house_style_words()) - same structured-field
        treatment as color above, for the same reason: the notebook is
        hardcoded around "Modern Luxury Contemporary" in several places
        (DEFAULT_THEME/DEFAULT_FEATURES/_floor_rules), which would fight any
        other style the same way its hardcoded color vocabulary fought
        non-default palettes. Omitted when falsy so an un-updated notebook
        keeps its old modern-luxury-default behavior.
        """
        payload: dict = {"prompt": prompt or ""}
        if floor_count:
            payload["floors"] = floor_count
        # Structured garage signal - the notebook uses it to include a car
        # porch (True) or actively exclude a garage/carport/driveway via its
        # negative prompt (False). Only sent when we actually have a signal;
        # omitted when None so an un-updated notebook keeps its old behavior.
        if wants_garage is not None:
            payload["garage"] = bool(wants_garage)
        if color:
            payload["color"] = color
        if style:
            payload["style"] = style

        base_url = settings.kaggle_house_api_url
        try:
            submit_response = httpx.post(
                _generate_elevation_url(base_url),
                json=payload,
                timeout=BATCH_SUBMIT_TIMEOUT_SECONDS,
            )
            submit_response.raise_for_status()
        except Exception as exc:
            session_error = classify_kaggle_failure(exc, "house-render")
            if session_error:
                raise session_error from exc
            raise

        submit_data = submit_response.json()
        job_id = submit_data.get("job_id")
        if not job_id:
            logger.error("unexpected Kaggle elevation submit response shape: %s", submit_data)
            raise RuntimeError(f"unexpected Kaggle elevation submit response shape: {submit_data}")

        status_url = _generate_elevation_status_url(base_url, job_id)
        deadline = time.monotonic() + BATCH_POLL_MAX_SECONDS

        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Kaggle elevation job {job_id} did not complete within {BATCH_POLL_MAX_SECONDS}s"
                )

            time.sleep(BATCH_POLL_INTERVAL_SECONDS)

            try:
                poll_response = httpx.get(status_url, timeout=BATCH_POLL_TIMEOUT_SECONDS)
                poll_response.raise_for_status()
            except Exception as exc:
                session_error = classify_kaggle_failure(exc, "house-render")
                if session_error:
                    raise session_error from exc
                raise

            job = poll_response.json()
            status = job.get("status")

            if status == "done":
                image_b64_out = job.get("generated_image_base64")
                if not image_b64_out:
                    logger.error("unexpected Kaggle elevation job result shape: keys=%s", list(job.keys()))
                    raise RuntimeError(f"unexpected Kaggle elevation job result shape: {job}")
                return base64.b64decode(image_b64_out)

            if status == "failed":
                raise RuntimeError(f"Kaggle elevation job {job_id} failed: {job.get('detail')}")

            # status == "running"/"started" (or any other in-progress value) - keep polling.


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
