import app.providers.hybrid as hybrid_module
from app.providers.hybrid import HybridProvider


class FakeImageProvider:
    def __init__(self, name):
        self.name = name

    def generate_image(self, image_bytes, prompt, tier=None):
        return f"{self.name}:image".encode()

    def generate_house_render(self, image_bytes, prompt):
        return f"{self.name}:house".encode()


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
