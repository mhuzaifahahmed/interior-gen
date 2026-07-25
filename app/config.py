from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_text_model: str = "gemini-2.5-flash"

    # Image generation moved to Cloudflare Workers AI (free tier) after Google
    # discontinued free-tier Gemini image generation in Dec 2025. Gemini is still
    # used for the (separate-quota, still-free) room-description text call.
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_image_model: str = "@cf/runwayml/stable-diffusion-v1-5-img2img"

    # Experimental alternate image backend (OpenAI's official Images API) - see
    # app/providers/openai.py. Only used if image_provider="openai" below; Cloudflare
    # remains the code default (this default is overridden to "openai" in .env).
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

    # Which image backend HybridProvider uses - "cloudflare" (default, free) or
    # "openai" (paid, see above). Text (Gemini) is unaffected either way.
    image_provider: str = "cloudflare"

    storage_backend: str = "local"
    local_storage_dir: str = "data/storage"

    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    database_url: str = "sqlite:///data/app.db"


settings = Settings()
