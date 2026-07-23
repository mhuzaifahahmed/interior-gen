from abc import ABC, abstractmethod


class Provider(ABC):
    """Model-provider seam. Swapping the underlying model/vendor should only
    ever require a new implementation of this interface, never pipeline changes.
    """

    @abstractmethod
    def generate_image(
        self,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: str = "",
        strength: float | None = None,
    ) -> bytes:
        """Edit/redecorate the given room photo per the prompt. Returns PNG bytes.

        negative_prompt steers the model away from unwanted elements (e.g. a
        chandelier appearing in a budget-tier render). This is a distinct channel
        from the positive prompt because diffusion models are unreliable at
        obeying negation ("no chandelier") stated inside the positive prompt.

        strength controls how much the output may diverge from the input image
        (0.0 = unchanged, 1.0 = ignores input); None means "use the implementation's
        default". Different tiers may need different strength - see
        app/pipeline/prompts.py's STRENGTH_BY_TIER.
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
