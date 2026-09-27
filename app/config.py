from typing import Literal

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
    studio_fresh_plan_openai_model: str = 'gpt-6-astra'
    abacus_api_key: str = ''
    # Only the fresh editorial refinement opts in; research and independent
    # media/voice review keep their existing provider contracts.
    studio_abacus_editorial_enabled: bool = False
    studio_abacus_editorial_model: str = 'claude-haiku-4-5-20251001'
    # Existing subscription RouteLLM only; explicit durable commissioning required.
    studio_abacus_included_production: bool = False
    # Owner-authorized setup only. Separate durable scene receipts preserve
    # unknown historical cash; deactivate when the final budget is selected.
    studio_commissioning_video_generation: bool = False
    studio_commissioning_reasoning: bool = False
    studio_series_multiple_attempts_enabled: bool = False
    # Fixed audio model draws existing Abacus credits; separate explicit policy.
    studio_abacus_prepaid_audio: bool = False
    # Explicit synchronous retained-Capital review only; no automatic funding,
    # production routing, fixed-model fallback or additional cash permission.
    studio_abacus_router_retained_review_enabled: bool = False
    studio_abacus_router_retained_audio_review_enabled: bool = False
    # Blank keeps existing planning-provider routing. Explicit abacus selects
    # only the bounded native Haiku picture critic and requires spending setup.
    studio_visual_qc_provider: str = ''
    studio_visual_qc_openai_model: str = 'gpt-6-astra'
    # Staged rollout: enable only after explicit ledger initialization. Missing
    # context, policy or a reviewed provider quote then blocks paid requests.
    studio_spend_enforcement: bool = False
    studio_spend_policy_json: str = ''
    # Operator-verified account tariff; an empty value never means free TTS.
    studio_elevenlabs_pricing_evidence_json: str = ''
    studio_elevenlabs_native_credits: bool = False
    # Staged until actual delivery/review and the spending policy are commissioned.
    studio_longform_delivery_enabled: bool = False
    # Persisted per-service owner preference; does not rewrite historical jobs.
    studio_shorts_policy_json: str = ''
    studio_production_short_paid_create_cap: int = Field(
        default=2, strict=True, ge=2, le=6,
    )
    # Passive cost meter price overrides, e.g. {"elevenlabs": {"*": {"character": 0.0003}}}.
    cost_meter_prices_json: str = ''
    gemini_critic_enabled: bool = False
    gemini_api_key: str = ''
    gemini_model: str = 'gemini-3.1-pro-preview'
    elevenlabs_api_key: str = ''
    elevenlabs_voice_id: str = ''
    pexels_api_key: str = ''
    runwayml_api_secret: str = ''
    fal_key: str = ''
    studio_video_provider: Literal['auto', 'legacy', 'fal'] = 'auto'
    studio_fal_video_model: Literal['auto', 'veo_lite', 'seedance_pro', 'seedance_fast'] = 'auto'
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
