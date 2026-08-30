from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./local.db'
    redis_url: str = 'redis://localhost:6379/0'
    app_encryption_key: str = ''
    openai_api_key: str = ''
    openai_model: str = 'gpt-5'
    gemini_critic_enabled: bool = False
    gemini_api_key: str = ''
    gemini_model: str = 'gemini-3.1-pro-preview'
    elevenlabs_api_key: str = ''
    elevenlabs_voice_id: str = ''
    pexels_api_key: str = ''
    runwayml_api_secret: str = ''
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


settings = Settings()

