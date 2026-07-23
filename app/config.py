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

    storage_backend: str = "local"
    local_storage_dir: str = "data/storage"

    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    database_url: str = "sqlite:///data/app.db"


settings = Settings()
