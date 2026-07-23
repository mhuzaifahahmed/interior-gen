from io import BytesIO

from google import genai
from PIL import Image

from app.config import settings
from app.providers.base import Provider


class GeminiProvider(Provider):
    def __init__(self) -> None:
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    def generate_image(self, image_bytes: bytes, prompt: str, negative_prompt: str = "") -> bytes:
        # Gemini's instruction-based image editing has no separate negative-prompt
        # channel; exclusions would need to be folded into `prompt` itself if this
        # path is ever reactivated (see CLAUDE.md - currently dormant/unused).
        image = Image.open(BytesIO(image_bytes))
        response = self.client.models.generate_content(
            model=settings.gemini_image_model,
            contents=[prompt, image],
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data

        raise RuntimeError("Gemini response contained no image data")

    def describe_room(self, image_bytes: bytes) -> str:
        image = Image.open(BytesIO(image_bytes))
        prompt = (
            "In one short phrase (under 15 words, comma-separated, no full sentences), "
            "describe this room's fixed structure only: window/door positions and room "
            "shape. Do not mention furniture, decor, or colors. This will be prepended "
            "to an SD1.5 prompt with a strict 77-token budget, so be extremely terse."
        )
        response = self.client.models.generate_content(
            model=settings.gemini_text_model,
            contents=[prompt, image],
        )
        return (response.text or "").strip()
