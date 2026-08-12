from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

    # Optional second SerpApi key/account - app/providers/serpapi.py
    # automatically switches to this one the moment the first key's monthly
    # quota runs out mid-generation (detected from SerpApi's own "run out of
    # searches"/401/429 response), instead of that and every subsequent
    # item's search failing for the rest of the month. Blank means no
    # fallback key - behavior is unchanged (single-key, same as before).
    serpapi_api_key_2: str = ""

    @property
    def serpapi_api_keys(self) -> list[str]:
        return [k for k in (self.serpapi_api_key, self.serpapi_api_key_2) if k]

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

    # "Build a House" render fidelity - kept separate from
    # openai_image_input_fidelity above (which the room-redesign Economical
    # tier also reads) so raising the house render's fidelity doesn't silently
    # raise Economical's cost/fidelity too.
    openai_house_input_fidelity: str = "high"

    # "Build a House" render quality - also kept separate from
    # openai_image_quality (the room tiers' "low"). This feature only produces
    # 1-2 renders per generation (vs 3 tiers), so the cost of "high" is small
    # and worth paying for a genuinely good picture, per the user's explicit
    # "results aren't good enough" feedback - unlike input_fidelity, this
    # never silently raises room-redesign's cost since it's read by nobody
    # else.
    openai_house_image_quality: str = "high"

    # Room-redesign image backend selector: "openai" (default, gpt-image-1) or
    # "kaggle" (a user's own fine-tuned model - see app/providers/kaggle.py).
    # A toggle, not a hard swap, specifically so a dropped Kaggle tunnel can be
    # reverted to OpenAI by changing one .env line, no code edit needed. Only
    # affects room-redesign generate_image() - "Build a House" renders always
    # stay on OpenAI (see HybridProvider), since a fine-tuned interior-redesign
    # model wasn't trained for exterior/plot renders.
    image_provider: str = "openai"

    # KaggleImageProvider's endpoint - a Cloudflare quick-tunnel URL pointing at
    # a live Kaggle notebook session running the user's own fine-tuned SD-style
    # img2img model. Only used when image_provider == "kaggle". Ephemeral by
    # nature (dies whenever the notebook session ends) - expect to update this
    # each time the notebook is restarted.
    kaggle_api_url: str = ""

    # Set ONLY when the frontend is hosted on a different domain from this
    # backend (e.g. static/ deployed to Vercel, this FastAPI app deployed to
    # Render) - blank (the default) means same-origin, which is how local dev
    # and a single-Render-service deploy (this app serving its own static/
    # via app.mount("/static", ...) - see index()) both already work, with
    # zero extra config. When set, app/main.py adds CORS for exactly this
    # origin as a defensive fallback for any direct (non-proxied) API call.
    # The real fix for cross-site auth is vercel.json's /api/:path* proxy
    # (makes browser requests same-origin) plus Clerk's Bearer-token auth
    # (app/auth.py) - neither depends on cookies/CORS the way the old
    # session-cookie system did, so this setting matters much less than it
    # used to.
    frontend_origin: str = ""

    storage_backend: str = "local"
    local_storage_dir: str = "data/storage"

    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"

    # Plain sqlite:///data/app.db by default (dev) - point this at a real
    # hosted Postgres (Neon/Supabase/Render Postgres) connection string in
    # production instead, since a plain SQLite file on Render's free tier
    # lives on an EPHEMERAL filesystem and is wiped on every redeploy/restart
    # (a real incident: signups vanished overnight because of this). See
    # resolved_database_url below for two real Neon-specific connection
    # string quirks this normalizes automatically.
    database_url: str = "sqlite:///data/app.db"

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url
        if not url or url.startswith("sqlite"):
            return url

        # SQLAlchemy 2.x rejects the bare "postgres://" scheme some providers
        # (including Neon, historically) still hand out - only "postgresql://"
        # resolves to a known dialect.
        if url.startswith("postgres://"):
            url = "postgresql://" + url.removeprefix("postgres://")

        # Neon's copy-paste connection string can include channel_binding=require
        # - a libpq/SCRAM feature the psycopg2-binary wheel's bundled libpq
        # doesn't recognize, which fails the connection outright ("invalid
        # connection option 'channel_binding'") - a real, live-hit error, not
        # theoretical. sslmode=require (also in Neon's string) already forces
        # an encrypted connection, so dropping channel_binding is safe.
        parts = urlsplit(url)
        if "channel_binding" in dict(parse_qsl(parts.query)):
            query = urlencode(
                [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
            )
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

        return url

    # Clerk (https://clerk.com) - the entire auth system (signup, login,
    # Google sign-in, session issuance) as of the Clerk migration. Replaced
    # the old bcrypt password hashing + Starlette session cookies + hand-
    # written Google OAuth flow entirely - see CLAUDE.md's "Authentication"
    # section. publishable_key is used only by the frontend (safe to expose,
    # it's meant to be public); secret_key is backend-only, used by
    # app/auth.py's verify_token() call to verify the session JWT a
    # Clerk-authenticated frontend sends as `Authorization: Bearer <token>`.
    clerk_publishable_key: str = ""
    clerk_secret_key: str = ""


settings = Settings()
