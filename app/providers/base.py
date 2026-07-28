from abc import ABC, abstractmethod


class Provider(ABC):
    """Model-provider seam. Swapping the underlying model/vendor should only
    ever require a new implementation of this interface, never pipeline changes.
    """

    @abstractmethod
    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        """Edit/redecorate the given room photo per the prompt. Returns PNG bytes.

        prompt is a complete natural-language edit instruction (see
        app/pipeline/prompts.py's build_prompt()) - exclusions like "no chandelier"
        are already stated directly in it, since the active backend is an
        instruction-following editor, not raw diffusion.

        tier (economical/mid/premium) is optional, best-effort context for
        implementations that vary backend-specific behavior per tier (e.g.
        OpenAIImageProvider raising input_fidelity for tiers that need to stay
        more tightly anchored to the input - see its module docstring). Not every
        implementation needs to use it.
        """
        ...

    @abstractmethod
    def describe_room(self, image_bytes: bytes) -> str:
        """Return a short structural description (walls/windows/layout/camera) of the room."""
        ...

    @abstractmethod
    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        """Analyze the room photo and return a short, room-specific renovation
        instruction per tier (keys: economical/mid/premium), e.g. noting visible
        damage that a given tier's prompt should account for. Return {} (or omit
        keys) if analysis isn't available/fails - callers must treat this as
        best-effort and degrade gracefully, same as describe_room.
        """
        ...

    @abstractmethod
    def generate_materials(
        self,
        tier: str,
        tier_spec: dict[str, str],
        room_description: str | None,
        city: str,
        api_key: str | None = None,
    ) -> dict:
        """Return an itemized materials/furniture list with local pricing for one
        tier, localized to `city`. Shape: {"items": [{"name", "spec", "price",
        "currency", "source_url", "is_estimate"}], "total", "currency"}.

        Must NEVER leave a price blank - if a real price/link can't be found for
        an item, it still appears with a labeled estimate. This is a harder
        guarantee than describe_room/generate_tier_notes's "best effort, degrade
        to {}" - implementations must synthesize a fallback list (still
        never-empty) rather than returning nothing, since a bare/empty result
        renders as broken UI, not as "feature unavailable."

        api_key optionally selects which underlying API key/client to use (see
        Settings.gemini_materials_api_keys) - implementations that support
        concurrent per-tier calls on separate quotas use this; others may
        ignore it.
        """
        ...
