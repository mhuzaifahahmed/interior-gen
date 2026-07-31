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

    # ---- "Build a House" feature (app/pipeline/generate_house.py) ----

    @abstractmethod
    def analyze_plot(self, image_bytes: bytes, dimensions: dict) -> str | None:
        """Analyze a plot/land photo + its stated dimensions and return a short
        description of orientation, boundary shape, and notable features.
        Best-effort, same contract as describe_room - degrade to None on any
        failure, never raise into the caller.
        """
        ...

    @abstractmethod
    def generate_floor_plan(
        self, plot_description: str | None, dimensions: dict, prompt: str
    ) -> bytes | None:
        """Generate a 2D floor plan (PNG bytes) respecting `dimensions` and
        `prompt` as closely as the underlying vendor allows. Best-effort: return
        None if no floor-plan vendor is configured/available, or if generation
        fails - the pipeline must treat "no floor plan" as an expected, non-fatal
        state (see app/providers/idealhouse.py), not a crash. Unlike
        generate_materials, there is deliberately NO never-empty fallback here -
        a fabricated floor-plan image would be actively misleading in a way a
        labeled price estimate isn't.
        """
        ...

    @abstractmethod
    def generate_house_render(self, image_bytes: bytes, prompt: str) -> bytes:
        """Edit/render an exterior or interior concept visualization from a plot
        photo (or floor plan, once a vendor is wired in) per the prompt. Returns
        PNG bytes. NOT best-effort - mirrors generate_image()'s treatment in the
        room-redesign pipeline: this is the core paid deliverable of the house
        feature, so a failure here should fail the whole house-project rather
        than degrade silently.
        """
        ...

    @abstractmethod
    def generate_room_layout(
        self, dimensions: dict, prompt: str, plot_description: str | None = None
    ) -> dict:
        """Return a structured room list per floor for the free algorithmic
        blueprint step (app/pipeline/floor_layout.py + blueprint_svg.py) - NOT
        the same thing as generate_floor_plan()/idealhouse.py, which remains
        the separate, still-inert slot reserved for a future real, paid
        floor-plan vendor.

        Shape: {"floors": [{"floor_number": int, "rooms": [{"name": str,
        "area": number}]}]}. "area" is a relative weight, not literal square
        footage - the layout algorithm rescales it to the real plot dimensions.

        Must NEVER leave a floor without at least one room and must NEVER
        raise - this is a harder guarantee than analyze_plot's "best effort,
        degrade to None", same never-empty category as generate_materials:
        implementations must synthesize a deterministic fallback layout
        (still never-empty) rather than returning nothing, since the
        blueprint-drawing step needs real rooms to draw.
        """
        ...
