from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./local.db'
    redis_url: str = 'redis://localhost:6379/0'
    app_encryption_key: str = ''
    openai_api_key: str = ''
    openai_model: str = 'gpt-5'
    studio_plan_provider: str = 'openai'
    studio_visual_qc_provider: str = ''
    studio_visual_qc_openai_model: str = 'gpt-6-astra'
    studio_production_short_paid_create_cap: int = Field(
        default=2, strict=True, ge=2, le=6,
    )
    gemini_critic_enabled: bool = False
    gemini_api_key: str = ''
    gemini_model: str = 'gemini-3.1-pro-preview'
    elevenlabs_api_key: str = ''
    elevenlabs_voice_id: str = ''
    pexels_api_key: str = ''
    runwayml_api_secret: str = ''
    fal_key: str = ''
    google_client_id: str = ''
    google_client_secret: str = ''
    google_redirect_uri: str = ''
    youtube_default_privacy: str = 'private'
    factory_api_token: str = ''

    # Railway Storage Bucket / S3-compatible credentials.
    bucket: str = ''
    access_key_id: str = ''
    secret_access_key: str = ''
    endpoint: str = ''
    region: str = 'auto'

    @field_validator('studio_production_short_paid_create_cap', mode='before')
    @classmethod
    def parse_production_short_paid_create_cap(cls, value: object) -> object:
        # Environment variables are strings; reject floats, booleans and
        # other coercions while accepting the bounded integer spelling.
        if isinstance(value, str) and value.strip() in {'2', '3', '4', '5', '6'}:
            return int(value.strip())
        return value


settings = Settings()

