import pytest

import app.providers.hybrid as hybrid_module
from app.providers.hybrid import HybridProvider


class FakeImageProvider:
    def __init__(self, name):
        self.name = name

    def generate_image(self, image_bytes, prompt, tier=None):
        return f"{self.name}:image".encode()

    def generate_house_render(self, image_bytes, prompt):
        return f"{self.name}:house".encode()


class FailingImageProvider:
    """Always raises from generate_image() - simulates a dead Kaggle tunnel,
    a timeout, or any other real failure of the room image provider."""

    def __init__(self, name, exc=None):
        self.name = name
        self.exc = exc or RuntimeError(f"{name} is unreachable")
        self.calls = 0

    def generate_image(self, image_bytes, prompt, tier=None):
        self.calls += 1
        raise self.exc

    def generate_house_render(self, image_bytes, prompt):
        raise self.exc


def test_room_image_generation_defaults_to_the_injected_house_provider(monkeypatch):
    # Default settings (IMAGE_PROVIDER unset -> "openai") - both room and house
    # rendering use the same injected provider, matching pre-Kaggle behavior.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    fake = FakeImageProvider("shared")
    provider = HybridProvider(image_provider=fake)

    assert provider.generate_image(b"x", "prompt") == b"shared:image"
    assert provider.generate_house_render(b"x", "prompt") == b"shared:house"


def test_kaggle_toggle_only_swaps_room_image_generation(monkeypatch):
    # IMAGE_PROVIDER=kaggle must swap ONLY generate_image (room redesign) -
    # generate_house_render ("Build a House") always stays on whatever the
    # house image provider is, since a fine-tuned interior-redesign model
    # wasn't trained for exterior/plot renders.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FakeImageProvider("kaggle"))
    house_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=house_provider)

    assert provider.generate_image(b"x", "prompt") == b"kaggle:image"
    assert provider.generate_house_render(b"x", "prompt") == b"openai:house"


def test_explicit_room_image_provider_overrides_the_settings_toggle(monkeypatch):
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FakeImageProvider("kaggle"))
    explicit_room_provider = FakeImageProvider("explicit")

    provider = HybridProvider(
        image_provider=FakeImageProvider("openai"), room_image_provider=explicit_room_provider
    )

    assert provider.generate_image(b"x", "prompt") == b"explicit:image"


# ---- runtime fallback: a failing room image provider (e.g. a dead Kaggle ----
# ---- tunnel) must not fail the whole generation when OpenAI could serve it ----


def test_generate_image_falls_back_to_openai_when_room_provider_fails(monkeypatch):
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    failing_kaggle = FailingImageProvider("kaggle")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: failing_kaggle)
    house_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=house_provider)

    result = provider.generate_image(b"x", "prompt", tier="mid")

    assert result == b"openai:image"
    assert failing_kaggle.calls == 1


def test_generate_image_passes_the_original_prompt_unmodified_to_the_fallback(monkeypatch):
    # The prompt handed to generate_image() is already the full OpenAI-shaped
    # build_prompt() output regardless of which provider is active - the
    # fallback must pass it straight through, not try to reshape it.
    captured = {}

    class CapturingOpenAI(FakeImageProvider):
        def generate_image(self, image_bytes, prompt, tier=None):
            captured["prompt"] = prompt
            return super().generate_image(image_bytes, prompt, tier)

    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FailingImageProvider("kaggle"))

    provider = HybridProvider(image_provider=CapturingOpenAI("openai"))
    provider.generate_image(b"x", "a long real build_prompt() paragraph", tier="premium")

    assert captured["prompt"] == "a long real build_prompt() paragraph"


def test_generate_image_raises_directly_when_no_fallback_is_configured(monkeypatch):
    # IMAGE_PROVIDER=openai (the default) - room and house providers are the
    # SAME instance, so there's nothing left to fall back to. Must raise
    # directly, not loop or swallow the error.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    failing = FailingImageProvider("openai")

    provider = HybridProvider(image_provider=failing)

    with pytest.raises(RuntimeError, match="openai is unreachable"):
        provider.generate_image(b"x", "prompt")

    assert failing.calls == 1


def test_generate_image_raises_if_both_room_provider_and_fallback_fail(monkeypatch):
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(
        hybrid_module, "KaggleImageProvider", lambda: FailingImageProvider("kaggle", RuntimeError("kaggle down"))
    )
    failing_openai = FailingImageProvider("openai", RuntimeError("openai also down"))

    provider = HybridProvider(image_provider=failing_openai)

    with pytest.raises(RuntimeError, match="openai also down"):
        provider.generate_image(b"x", "prompt")


def test_each_tier_falls_back_independently(monkeypatch):
    # Simulates the real per-tier concurrency in app/pipeline/generate.py -
    # a transient failure on one tier's Kaggle call must not affect another
    # tier's call, which might genuinely succeed.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")

    class FlakyKaggle:
        def generate_image(self, image_bytes, prompt, tier=None):
            if tier == "mid":
                raise RuntimeError("transient failure on mid only")
            return f"kaggle:{tier}".encode()

    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FlakyKaggle())
    house_provider = FakeImageProvider("openai")
    provider = HybridProvider(image_provider=house_provider)

    assert provider.generate_image(b"x", "prompt", tier="economical") == b"kaggle:economical"
    assert provider.generate_image(b"x", "prompt", tier="mid") == b"openai:image"
    assert provider.generate_image(b"x", "prompt", tier="premium") == b"kaggle:premium"
