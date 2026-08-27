from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./local.db'
    redis_url: str = 'redis://localhost:6379/0'
    app_encryption_key: str = ''
    openai_api_key: str = ''
    openai_model: str = 'gpt-5'
    elevenlabs_api_key: str = ''
    elevenlabs_voice_id: str = ''
    pexels_api_key: str = ''
    runwayml_api_secret: str = ''
    google_client_id: str = ''
    google_client_secret: str = ''
    google_redirect_uri: str = ''
    youtube_default_privacy: str = 'private'

settings = Settings()
