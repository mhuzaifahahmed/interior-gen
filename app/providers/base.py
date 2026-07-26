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
