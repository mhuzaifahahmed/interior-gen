from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Reserved for text/description calls only (describe_room, generate_tier_notes,
    # and the dormant Gemini image-gen path) - NOT used for materials/pricing, which
    # has its own dedicated keys below. Keeping these separate means the room-
    # description call (which runs on every project, city or not) never competes
    # for quota with the materials feature.
    gemini_api_key: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"
    # gemini-2.5-flash returns a 404 ("no longer available to new users") on
    # accounts created after Google restricted it - a real, live-tested failure,
    # not theoretical. gemini-3.5-flash confirmed working via a real API call on
    # this project's accounts; gemini-flash-latest also works if that's preferred.
    gemini_text_model: str = "gemini-3.5-flash"

    # Materials/pricing feature: one DEDICATED Gemini key per tier (not shared with
    # gemini_api_key above), so each tier's grounded search runs on its own
    # rate-limit quota with zero contention between tiers or with the text calls.
    # Degrades gracefully per-tier - a tier with no dedicated key falls back to
    # gemini_api_key (see gemini_materials_api_keys property below).
    gemini_materials_api_key_economical: str = ""
    gemini_materials_api_key_mid: str = ""
    gemini_materials_api_key_premium: str = ""

    @property
    def gemini_materials_api_keys(self) -> dict[str, str]:
        return {
            "economical": self.gemini_materials_api_key_economical or self.gemini_api_key,
            "mid": self.gemini_materials_api_key_mid or self.gemini_api_key,
            "premium": self.gemini_materials_api_key_premium or self.gemini_api_key,
        }

    # SerpApi (https://serpapi.com) - real Google search results for materials
    # pricing, used in place of Gemini's own Google Search grounding tool. Real,
    # live-tested reason: grounding hits a 429 RESOURCE_EXHAUSTED wall on every
    # Gemini key/project tried here (even brand-new ones), and Google's own
    # developer forum has multiple reports of billing NOT fixing this (a platform
    # bug, not user error) - see app/providers/gemini.py's generate_materials()
    # module docstring. One shared key is enough (unlike the Gemini materials
    # keys above) - SerpApi's rate limit isn't per-tier, there's no reason to
    # split it three ways. Free plan: 250 searches/month, no card required.
    serpapi_api_key: str = ""

    # Image generation: OpenAI's official Images API (app/providers/openai.py) -
    # the sole image backend. Google discontinued free-tier Gemini image generation
    # in Dec 2025 (Gemini is still used for the separate-quota, still-free
    # room-description text call), and the earlier Cloudflare Workers AI/SD1.5
    # fallback was removed - its keyword-soup prompt format and diffusion-era
    # negative_prompt/strength machinery were confusing the OpenAI integration
    # with dead paths that no longer applied to it.
    #
    # COST WARNING (learned from a real test call, not docs): `quality` alone does
    # NOT control cost the way it looks like it should. `input_fidelity` is a
    # SEPARATE param that also drives cost heavily and defaults to "high" if
    # omitted - a real smoke test with only quality="low" set cost $0.10/image
    # (~20x the ~$0.005 "low quality" figure from pricing articles, which describe
    # generation cost, not edit cost). Both must be set explicitly to actually get
    # the cheap tier. input_fidelity also isn't PURELY a cost knob - OpenAI
    # describes it as controlling "fidelity to the original input image(s)", i.e.
    # structure preservation. If renders drift structurally, try "high" here
    # before touching anything else, and expect cost to jump accordingly.
    #
    # MODEL: gpt-image-1, not gpt-image-2, despite gpt-image-1 retiring Oct 2026 -
    # gpt-image-2 rejects input_fidelity outright (400 error, "does not support the
    # 'input_fidelity' parameter"), so it can't do a cheap-input-processing tier at
    # all. Deliberate choice given openai.py always sends input_fidelity.
    openai_api_key: str = ""
    openai_image_model: str = "gpt-image-1"
    openai_image_quality: str = "low"
    openai_image_input_fidelity: str = "low"

    storage_backend: str = "local"
    local_storage_dir: str = "data/storage"

    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    database_url: str = "sqlite:///data/app.db"


settings = Settings()
