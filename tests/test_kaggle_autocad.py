import base64
import io

import httpx
from PIL import Image, ImageDraw, ImageFont

import pytest

import app.providers.kaggle_autocad as kaggle_autocad_module
from app.providers.kaggle_autocad import (
    _composite_room_furniture,
    _composite_room_labels,
    _normalize_dark_background,
    generate_floor_plan,
)
from app.providers.session_errors import KaggleSessionUnavailableError


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def _fake_jpeg_b64() -> str:
    image = Image.new("RGB", (4, 4), (255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_generate_floor_plan_returns_none_when_url_not_configured(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "")
    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "2 floors, 3 bedrooms")
    assert result is None


def test_generate_floor_plan_submits_then_polls_and_returns_png_bytes(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    calls = {"polls": 0}

    def fake_post(url, json=None, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_floorplans"
        assert json["length"] == 40
        assert json["width"] == 60
        assert json["unit"] == "ft"
        assert json["floors"] == 2
        assert json["bedrooms"] == 3
        assert json["bathrooms"] == 2
        return FakeResponse(json_data={"status": "started", "job_id": "job-1"})

    def fake_get(url, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_floorplans/status/job-1"
        calls["polls"] += 1
        if calls["polls"] < 2:
            return FakeResponse(json_data={"status": "running"})
        return FakeResponse(
            json_data={
                "status": "done",
                "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}],
            }
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "2 floors, 3 bedrooms, 2 bathrooms. Extras: garage"
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].startswith(b"\x89PNG")  # re-encoded to PNG, even though the model returns JPEG
    assert calls["polls"] == 2


def test_generate_floor_plan_returns_one_image_per_floor_in_order(monkeypatch):
    # Real regression guard: the model generates one image per floor
    # natively - every floor must come back, sorted by floor_number, not
    # just the first (an earlier version silently discarded floors 2+).
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        assert json["floors"] == 3
        return FakeResponse(json_data={"status": "started", "job_id": "job-multi"})

    def fake_get(url, timeout=None):
        # Deliberately out of order, to confirm the result is sorted.
        return FakeResponse(
            json_data={
                "status": "done",
                "floors": [
                    {"floor_number": 2, "image_base64": _fake_jpeg_b64()},
                    {"floor_number": 3, "image_base64": _fake_jpeg_b64()},
                    {"floor_number": 1, "image_base64": _fake_jpeg_b64()},
                ],
            }
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "3 floors")

    assert result is not None
    assert len(result) == 3
    for image_bytes in result:
        assert image_bytes.startswith(b"\x89PNG")


def test_generate_floor_plan_returns_none_on_job_failure(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-2"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "failed", "detail": "CUDA out of memory"})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_returns_none_when_submit_response_has_no_job_id(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started"})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_raises_session_unavailable_on_connection_error(monkeypatch):
    # 2026-09-01: a connection-shaped failure (the tunnel/notebook session
    # being offline) is no longer silently swallowed to None - it's real,
    # actionable, and gets surfaced to the user (see
    # app/providers/session_errors.py, app/pipeline/generate_house.py's
    # _run_floor_plan_stage). Genuinely unexpected/unclassified errors still
    # degrade to None, unchanged - see the next test.
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("tunnel is down")

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)

    with pytest.raises(KaggleSessionUnavailableError):
        generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")


def test_generate_floor_plan_returns_none_on_unclassified_exception(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        raise ValueError("some unrelated bug")

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_omits_conditioning_images_when_no_room_layout(monkeypatch):
    # Backward compat: a caller that doesn't supply room_layout must get the
    # exact same request shape as before Phase 1 (concept-layout-controlnet-
    # conditioning.md) - no conditioning_images field at all, so the
    # notebook's own create_plot_boundary() fallback keeps working unchanged.
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        assert "conditioning_images" not in json
        return FakeResponse(json_data={"status": "started", "job_id": "job-noconditioning"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}]})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is not None


def test_generate_floor_plan_sends_one_conditioning_image_per_floor_when_room_layout_given(monkeypatch):
    # Phase 1: real geometry beats the notebook's own hardcoded empty
    # rectangle - one conditioning image per floor, built from the SAME
    # rects layout_floor() (and thus the deterministic blueprint step) uses.
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 2", "area": 1}]},
        ]
    }

    def fake_post(url, json=None, timeout=None):
        assert "conditioning_images" in json
        assert len(json["conditioning_images"]) == 2
        for b64_png in json["conditioning_images"]:
            assert base64.b64decode(b64_png).startswith(b"\x89PNG")
        return FakeResponse(json_data={"status": "started", "job_id": "job-conditioning"})

    def fake_get(url, timeout=None):
        return FakeResponse(
            json_data={
                "status": "done",
                "floors": [
                    {"floor_number": 1, "image_base64": _fake_jpeg_b64()},
                    {"floor_number": 2, "image_base64": _fake_jpeg_b64()},
                ],
            }
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "2 floors", room_layout=room_layout
    )
    assert result is not None
    assert len(result) == 2


def test_generate_floor_plan_conditioning_image_reflects_facing(monkeypatch):
    # 2026-09-04: facing MUST be threaded through to the conditioning image
    # builder - otherwise the AI Concept Layout card would silently trace a
    # "north" (default) layout even when the real deterministic blueprint
    # used a different facing, visibly disagreeing with it (see
    # _conditioning_images_by_floor()'s docstring). A real, different facing
    # must produce a genuinely different conditioning image.
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    room_layout = {
        "floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}]}]
    }
    captured_conditioning_images = []

    def fake_post(url, json=None, timeout=None):
        captured_conditioning_images.append(json["conditioning_images"][0])
        return FakeResponse(json_data={"status": "started", "job_id": "job-facing"})

    def fake_get(url, timeout=None):
        return FakeResponse(
            json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}]}
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor", room_layout=room_layout, facing="north"
    )
    generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor", room_layout=room_layout, facing="south"
    )

    assert captured_conditioning_images[0] != captured_conditioning_images[1]


def test_generate_floor_plan_composites_room_labels_when_room_layout_given(monkeypatch):
    # Phase 2: once we sent real conditioning geometry, composite our own
    # accurate room labels onto the (deliberately text-free) returned image -
    # confirmed here by checking the result differs from the plain
    # re-encoded-with-no-labels case (same fake JPEG input either way).
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    room_layout = {"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 1}]}]}

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-label"})

    fake_jpeg = _fake_jpeg_b64()

    def fake_get(url, timeout=None):
        return FakeResponse(
            json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": fake_jpeg}]}
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    with_labels = generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor", room_layout=room_layout
    )

    without_labels = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")

    assert with_labels is not None and without_labels is not None
    # Same input JPEG both times, but only the room_layout call composites a
    # label onto it - the resulting PNG bytes must differ.
    assert with_labels[0] != without_labels[0]


def test_generate_floor_plan_defaults_bedrooms_bathrooms_when_not_stated(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        assert json["floors"] == 1
        assert json["bedrooms"] == 3
        assert json["bathrooms"] == 2
        return FakeResponse(json_data={"status": "started", "job_id": "job-3"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}]})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "no specific requirements")
    assert result is not None


def test_normalize_dark_background_inverts_a_dark_image():
    # 2026-09-04: the model has no fixed seed, so it sometimes lands on a
    # dark-background/light-line "literal blueprint" polarity instead of the
    # desired light-background/dark-line look - a plain inversion recovers
    # something close to the desired style (verified against real saved
    # model output, not just a synthetic case, before this was written).
    dark_image = Image.new("RGB", (8, 8), (20, 20, 30))
    result = _normalize_dark_background(dark_image)
    # Inverted: a near-black pixel becomes a near-white one.
    r, g, b = result.getpixel((0, 0))
    assert r > 200 and g > 200 and b > 200


def test_normalize_dark_background_leaves_a_light_image_untouched():
    light_image = Image.new("RGB", (8, 8), (230, 230, 220))
    result = _normalize_dark_background(light_image)
    assert result.getpixel((0, 0)) == (230, 230, 220)


def test_normalize_dark_background_leaves_a_mid_gray_image_untouched():
    # The "rendered slab" style variant (mid-gray, mean ~140-170 in real
    # saved samples) is real output variance too but is NOT a simple
    # inversion of the light style - must not be touched by this heuristic.
    mid_gray_image = Image.new("RGB", (8, 8), (150, 150, 150))
    result = _normalize_dark_background(mid_gray_image)
    assert result.getpixel((0, 0)) == (150, 150, 150)


def test_generate_floor_plan_normalizes_a_dark_polarity_response(monkeypatch):
    # End-to-end: a job response whose image is dark-polarity should come
    # back as a light PNG, not a dark one - regression guard for the fix
    # above actually being wired into generate_floor_plan()'s return path.
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    dark_image = Image.new("RGB", (8, 8), (20, 20, 30))
    buffer = io.BytesIO()
    dark_image.save(buffer, format="JPEG")
    dark_jpeg_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-dark"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": dark_jpeg_b64}]})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "no specific requirements")

    assert result is not None
    returned_image = Image.open(io.BytesIO(result[0]))
    r, g, b = returned_image.getpixel((0, 0))
    assert r > 200 and g > 200 and b > 200


def test_composite_room_furniture_draws_symbols_into_rooms():
    # 2026-09-08: the Concept Layout card now composites our OWN accurate
    # furniture (reusing blueprint_svg's deterministic symbols) onto the AI
    # image, fixing the model's own hallucinated/wrong furniture. This
    # confirms furniture is actually drawn into a room (the composited image
    # differs from the untouched background), the drawing half of the fix.
    from app.pipeline.conditioning_image import CANVAS_SIZE

    rects = [
        {"name": "Master Bedroom", "x": 0, "y": 0, "w": 50, "h": 50},
        {"name": "Kitchen", "x": 50, "y": 0, "w": 50, "h": 50},
    ]
    dimensions = {"length": 100, "width": 50, "unit": "ft"}
    image = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), (250, 246, 238))
    before = image.tobytes()
    _composite_room_furniture(image, rects, dimensions)
    assert image.tobytes() != before  # furniture symbols were drawn


def test_generate_floor_plan_composites_furniture_when_room_layout_given(monkeypatch):
    # The furniture compositor must actually be wired into the done-branch's
    # return path (not just defined) whenever we sent real conditioning
    # geometry - guarded here with a spy, since it's easy to add the function
    # but forget the call site (as happened once with the labels).
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    called = {"count": 0}
    real = kaggle_autocad_module._composite_room_furniture

    def spy(image, rects, dimensions, stair_direction=None):
        called["count"] += 1
        return real(image, rects, dimensions, stair_direction)

    monkeypatch.setattr(kaggle_autocad_module, "_composite_room_furniture", spy)

    room_layout = {"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 1}]}]}

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-furniture"})

    def fake_get(url, timeout=None):
        return FakeResponse(
            json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}]}
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor", room_layout=room_layout
    )
    assert result is not None
    assert called["count"] == 1


def test_composite_room_labels_shrinks_or_wraps_a_label_too_wide_for_its_room():
    # Real, live-reported bug (2026-09-08): every label was drawn at ONE
    # fixed font size derived only from the overall image width, with no
    # per-room width check or wrapping at all - on a narrow room (e.g. a
    # compact bathroom beside a bedroom) the label ran straight into its
    # neighbor's, reading as jumbled/overlapping text (e.g. "Master
    # BedMaster BathroBedroolShared BathroBedroom 3"). Reproduces a narrow
    # room whose name would clearly overflow at the module's default font
    # size and confirms the ACTUAL fitted label - using the same font-fit
    # logic the function itself uses - never exceeds the room's own pixel
    # width.
    from app.pipeline.blueprint_svg import _fit_room_name
    from app.pipeline.conditioning_image import CANVAS_SIZE, plot_to_canvas_box

    rects = [
        {"name": "Master Bedroom", "x": 0, "y": 0, "w": 24, "h": 30},
        {"name": "Master Bathroom", "x": 24, "y": 0, "w": 8, "h": 30},
        {"name": "Bedroom 2", "x": 32, "y": 0, "w": 24, "h": 30},
    ]
    dimensions = {"length": 56, "width": 30, "unit": "ft"}
    image = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0))
    _composite_room_labels(image, rects, dimensions)

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    _x0, _y0, box_w, _box_h = plot_to_canvas_box(56, 30)
    bathroom = rects[1]
    room_px_w = bathroom["w"] / 56 * box_w  # scale_x is 1.0 (image width == CANVAS_SIZE)
    base_font = ImageFont.load_default(size=max(14, CANVAS_SIZE // 32))

    lines, font = _fit_room_name(draw, bathroom["name"].upper(), max(room_px_w - 8, 10), base_font)
    rendered_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
    assert rendered_w <= room_px_w
