from abc import ABC, abstractmethod


class Provider(ABC):
    """Model-provider seam. Swapping the underlying model/vendor should only
    ever require a new implementation of this interface, never pipeline changes.
    """

    @abstractmethod
    def generate_image(self, image_bytes: bytes, prompt: str, negative_prompt: str = "") -> bytes:
        """Edit/redecorate the given room photo per the prompt. Returns PNG bytes.

        negative_prompt steers the model away from unwanted elements (e.g. a
        chandelier appearing in a budget-tier render). This is a distinct channel
        from the positive prompt because diffusion models are unreliable at
        obeying negation ("no chandelier") stated inside the positive prompt.
        """
        ...

    @abstractmethod
    def describe_room(self, image_bytes: bytes) -> str:
        """Return a short structural description (walls/windows/layout/camera) of the room."""
        ...
