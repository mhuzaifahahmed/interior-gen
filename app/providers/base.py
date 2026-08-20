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

    def supports_batch(self) -> bool:
        """True if generate_images_batch() does real batched GPU inference
        instead of the default sequential fallback. app/pipeline/generate.py
        checks this to decide whether to use a single batch call (Kaggle) or
        its existing per-tier ThreadPoolExecutor concurrency (OpenAI, which
        already parallelizes at the HTTP-request level and gets nothing extra
        from this method's default sequential loop).
        """
        return False

    def generate_images_batch(
        self, image_bytes: bytes, tier_prompts: dict[str, str]
    ) -> dict[str, bytes]:
        """Generate 3 tier images in one batch call (if supported) or sequentially
        (default). tier_prompts is {"economical": "...", "mid": "...", "premium": "..."}.

        Returns {"economical": bytes, "mid": bytes, "premium": bytes}.

        Default implementation calls generate_image() sequentially for each tier;
        Kaggle overrides this with a true /generate_batch endpoint call.
        """
        return {tier: self.generate_image(image_bytes, prompt, tier)
                for tier, prompt in tier_prompts.items()}

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
        room_area_sqft: float | None = None,
        wall_area_sqft: float | None = None,
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

        room_area_sqft is the best-effort estimate from estimate_room_area()
        below, OR a real user-supplied floor area (see app/main.py's
        _compute_room_dimensions() / app/pipeline/generate.py's run_pipeline())
        - when present, implementations should use it to turn a found
        PER-UNIT price (e.g. "$12/sqft" for flooring) into a real total for the
        whole room (price * area), not just copy the per-unit number through as
        if it were already the item's total cost. None means no estimate was
        available - implementations should fall back to their prior per-item
        guessing behavior in that case.

        wall_area_sqft is an independently-optional, user-supplied paintable
        wall area (only present when the user also gave a room height - see
        _compute_room_dimensions()) - when present, implementations should use
        it (instead of room_area_sqft) for Paint/wall-finish's own quantity
        rule specifically, since a room's wall area and floor area are
        different numbers. None means no such measurement was given - Paint
        falls back to using room_area_sqft (or its own guess) exactly as
        before this parameter existed.
        """
        ...

    @abstractmethod
    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        """Best-effort rough floor-area estimate (in square feet) for the room
        in the photo, used so generate_materials() can multiply a found
        per-square-foot price into a real total instead of leaving it as a
        bare unit price. Same contract as describe_room/generate_tier_notes -
        degrade to None on any failure, never raise into the caller.
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
        self,
        dimensions: dict,
        prompt: str,
        plot_description: str | None = None,
        floor_count: int | None = None,
    ) -> dict:
        """Return a structured room list per floor for the free algorithmic
        blueprint step (app/pipeline/floor_layout.py + blueprint_svg.py) - NOT
        the same thing as generate_floor_plan()/idealhouse.py, which remains
        the separate, still-inert slot reserved for a future real, paid
        floor-plan vendor.

        Shape: {"floors": [{"floor_number": int, "rooms": [{"name": str,
        "area": number}]}]}. "area" is a relative weight, not literal square
        footage - the layout algorithm rescales it to the real plot dimensions.

        floor_count, when given, is a REAL explicit value from the "Build a
        House" form's structured Floors dropdown (see app/main.py's
        create_house_project) and takes priority over any floor-count
        guess/regex-parse implementations might otherwise derive from the
        free-text prompt. None means no explicit value was given (the legacy
        free-text-only path) - implementations should fall back to their
        prior guessing behavior in that case.

        Must NEVER leave a floor without at least one room and must NEVER
        raise - this is a harder guarantee than analyze_plot's "best effort,
        degrade to None", same never-empty category as generate_materials:
        implementations must synthesize a deterministic fallback layout
        (still never-empty) rather than returning nothing, since the
        blueprint-drawing step needs real rooms to draw.
        """
        ...

    # NOTE: an AI-drawn "generate_cad_plan" method briefly existed here (an
    # image model redrawing the deterministic blueprint into a polished CAD
    # presentation) and was removed - a real generation showed the image
    # model hallucinating malformed dimension/area text ("18.4 x 522.59 ft")
    # when asked to render technical content. Never committed to git, so
    # cleanly removed rather than left dormant. The floor-plan image is
    # 100% deterministic again (app/pipeline/blueprint_svg.py, now with
    # furniture and staircase symbols) - see generate_house.py's
    # HOUSE_PROMPT_VERSION docstring for the full history.
