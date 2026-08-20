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
    # With BOTH toggles explicitly set to "openai" - room and house rendering
    # use the same injected provider, matching pre-Kaggle/pre-Modal behavior.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "openai")
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
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "openai")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FakeImageProvider("kaggle"))
    house_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=house_provider)

    assert provider.generate_image(b"x", "prompt") == b"kaggle:image"
    assert provider.generate_house_render(b"x", "prompt") == b"openai:house"


def test_modal_toggle_only_swaps_room_image_generation(monkeypatch):
    # Same shape as the Kaggle toggle test above - IMAGE_PROVIDER=modal must
    # swap ONLY room-redesign, not house rendering.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "modal")
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "openai")
    monkeypatch.setattr(hybrid_module, "ModalImageProvider", lambda: FakeImageProvider("modal"))
    house_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=house_provider)

    assert provider.generate_image(b"x", "prompt") == b"modal:image"
    assert provider.generate_house_render(b"x", "prompt") == b"openai:house"


def test_house_image_provider_modal_toggle_only_swaps_house_rendering(monkeypatch):
    # house_image_provider=modal must swap ONLY generate_house_render - room
    # redesign stays on whatever image_provider resolves to (openai here).
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "modal")
    monkeypatch.setattr(hybrid_module, "ModalImageProvider", lambda: FakeImageProvider("modal"))
    room_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=room_provider)

    assert provider.generate_image(b"x", "prompt") == b"openai:image"
    assert provider.generate_house_render(b"x", "prompt") == b"modal:house"


def test_house_image_provider_kaggle_toggle_only_swaps_house_rendering(monkeypatch):
    # Same shape as the Modal house-toggle test above - house_image_provider=
    # kaggle must swap ONLY generate_house_render, using a SEPARATE Kaggle
    # account/notebook from room-redesign's (advance plumbing for a house
    # model not yet trained - see KaggleImageProvider.generate_house_render()).
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "kaggle")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FakeImageProvider("kaggle"))
    room_provider = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=room_provider)

    assert provider.generate_image(b"x", "prompt") == b"openai:image"
    assert provider.generate_house_render(b"x", "prompt") == b"kaggle:house"


def test_generate_image_falls_back_to_openai_not_to_the_house_provider_when_room_fails(monkeypatch):
    # Real regression guard: if house_image_provider is ALSO an experimental
    # backend (e.g. both set to "modal"), a failing room provider must still
    # fall back to the guaranteed-real OpenAI instance, not to whatever
    # (possibly also-failing) backend house rendering happens to be using.
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "modal")
    monkeypatch.setattr(hybrid_module, "KaggleImageProvider", lambda: FailingImageProvider("kaggle"))
    monkeypatch.setattr(hybrid_module, "ModalImageProvider", lambda: FailingImageProvider("modal-house"))
    real_openai = FakeImageProvider("openai")

    provider = HybridProvider(image_provider=real_openai)

    result = provider.generate_image(b"x", "prompt", tier="mid")

    assert result == b"openai:image"


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


# ---------------------------------------------------------------------------
# Provider attribution ("our model" vs "OpenAI") - app/providers/hybrid.py's
# get_image_model_label()/get_house_render_model_label(). Uses class-name-
# keyed fakes (registered into hybrid_module._PROVIDER_LABELS for the
# duration of each test) rather than the generic FakeImageProvider above,
# since the label lookup keys off type(provider).__name__.
# ---------------------------------------------------------------------------


class FakeOurModelProvider:
    def generate_image(self, image_bytes, prompt, tier=None):
        return f"ourmodel:{tier}".encode()

    def supports_batch(self):
        return False

    def generate_house_render(self, image_bytes, prompt):
        return b"ourmodel:house"


class FakeOpenAIProvider:
    def generate_image(self, image_bytes, prompt, tier=None):
        return f"openai:{tier}".encode()

    def supports_batch(self):
        return False

    def generate_house_render(self, image_bytes, prompt):
        return b"openai:house"


class FailingFakeOurModelProvider:
    def generate_image(self, image_bytes, prompt, tier=None):
        raise RuntimeError("our model unreachable")

    def supports_batch(self):
        return False


def _register_fake_labels(monkeypatch):
    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "FakeOurModelProvider", "our model")
    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "FakeOpenAIProvider", "OpenAI")
    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "FailingFakeOurModelProvider", "our model")


def test_label_for_maps_the_real_provider_classes():
    from app.providers.kaggle import KaggleImageProvider
    from app.providers.modal_provider import ModalImageProvider
    from app.providers.openai import OpenAIImageProvider

    assert hybrid_module._label_for(KaggleImageProvider()) == "our model"
    assert hybrid_module._label_for(ModalImageProvider()) == "our model"
    assert hybrid_module._label_for(OpenAIImageProvider()) == "OpenAI"


def test_get_image_model_label_is_none_before_any_tier_completes(monkeypatch):
    _register_fake_labels(monkeypatch)
    provider = HybridProvider(image_provider=FakeOpenAIProvider(), room_image_provider=FakeOurModelProvider())
    assert provider.get_image_model_label() is None


def test_get_image_model_label_our_model_when_room_provider_succeeds(monkeypatch):
    _register_fake_labels(monkeypatch)
    provider = HybridProvider(image_provider=FakeOpenAIProvider(), room_image_provider=FakeOurModelProvider())

    provider.generate_image(b"x", "prompt", tier="economical")
    provider.generate_image(b"x", "prompt", tier="mid")
    provider.generate_image(b"x", "prompt", tier="premium")

    assert provider.get_image_model_label() == "our model"


def test_get_image_model_label_openai_when_room_provider_falls_back(monkeypatch):
    # Real regression guard for the "no fallback wording" requirement: even
    # though this exercises the fallback code path, the label must be the
    # plain "OpenAI" string - never mention fallback/backup.
    _register_fake_labels(monkeypatch)
    provider = HybridProvider(
        image_provider=FakeOpenAIProvider(), room_image_provider=FailingFakeOurModelProvider()
    )

    provider.generate_image(b"x", "prompt", tier="economical")

    label = provider.get_image_model_label()
    assert label == "OpenAI"
    assert "fallback" not in label.lower()
    assert "backup" not in label.lower()


def test_get_image_model_label_mixed_when_tiers_diverge(monkeypatch):
    _register_fake_labels(monkeypatch)

    class SometimesFailingProvider:
        def generate_image(self, image_bytes, prompt, tier=None):
            if tier == "mid":
                raise RuntimeError("transient failure on mid only")
            return f"ourmodel:{tier}".encode()

        def supports_batch(self):
            return False

    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "SometimesFailingProvider", "our model")
    provider = HybridProvider(image_provider=FakeOpenAIProvider(), room_image_provider=SometimesFailingProvider())

    provider.generate_image(b"x", "prompt", tier="economical")
    provider.generate_image(b"x", "prompt", tier="mid")
    provider.generate_image(b"x", "prompt", tier="premium")

    assert provider.get_image_model_label() == "our model + OpenAI"


def test_get_image_model_label_uniform_openai_when_no_room_toggle(monkeypatch):
    monkeypatch.setattr(hybrid_module.settings, "image_provider", "openai")
    _register_fake_labels(monkeypatch)
    provider = HybridProvider(image_provider=FakeOpenAIProvider())

    provider.generate_image(b"x", "prompt", tier="economical")

    assert provider.get_image_model_label() == "OpenAI"


def test_get_house_render_model_label_our_model(monkeypatch):
    _register_fake_labels(monkeypatch)
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "modal")
    monkeypatch.setattr(hybrid_module, "ModalImageProvider", lambda: FakeOurModelProvider())
    provider = HybridProvider(image_provider=FakeOpenAIProvider())

    assert provider.get_house_render_model_label() is None
    provider.generate_house_render(b"x", "prompt")
    assert provider.get_house_render_model_label() == "our model"


def test_get_house_render_model_label_openai(monkeypatch):
    _register_fake_labels(monkeypatch)
    monkeypatch.setattr(hybrid_module.settings, "house_image_provider", "openai")
    provider = HybridProvider(image_provider=FakeOpenAIProvider())

    provider.generate_house_render(b"x", "prompt")
    assert provider.get_house_render_model_label() == "OpenAI"


def test_get_image_model_label_uniform_our_model_on_successful_batch(monkeypatch):
    _register_fake_labels(monkeypatch)

    class FakeBatchOurModelProvider:
        def generate_images_batch(self, image_bytes, tier_prompts):
            return {tier: f"ourmodel:{tier}".encode() for tier in tier_prompts}

    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "FakeBatchOurModelProvider", "our model")
    provider = HybridProvider(
        image_provider=FakeOpenAIProvider(), room_image_provider=FakeBatchOurModelProvider()
    )

    provider.generate_images_batch(b"x", {"economical": "p1", "mid": "p2", "premium": "p3"})

    assert provider.get_image_model_label() == "our model"


def test_get_image_model_label_uniform_openai_when_batch_fails(monkeypatch):
    # Real regression guard: a failed batch call falls back to OpenAI for
    # ALL tiers at once (all-or-nothing, unlike the per-tier path) - the
    # label must reflect that uniformly, never mentioning fallback/backup.
    _register_fake_labels(monkeypatch)

    class FailingBatchProvider:
        def generate_images_batch(self, image_bytes, tier_prompts):
            raise RuntimeError("batch job failed")

    monkeypatch.setitem(hybrid_module._PROVIDER_LABELS, "FailingBatchProvider", "our model")
    provider = HybridProvider(
        image_provider=FakeOpenAIProvider(), room_image_provider=FailingBatchProvider()
    )

    provider.generate_images_batch(b"x", {"economical": "p1", "mid": "p2", "premium": "p3"})

    label = provider.get_image_model_label()
    assert label == "OpenAI"
    assert "fallback" not in label.lower()
