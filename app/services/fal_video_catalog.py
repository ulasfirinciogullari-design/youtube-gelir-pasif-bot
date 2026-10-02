"""Reviewed silent video profiles shared by routing, requests and spending.

Sources (2026-09-23, re-checked unchanged 2026-10-01 and 2026-10-02):
https://fal.ai/models/<endpoint> and /api.
Only these exact request shapes are priced. Adding a model requires reviewing
its duration, audio, resolution, tariff and preservation contract together.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

from app.services.production_spend import SpendBlocked, SpendQuote, usd_micro
from app.services import channel_ids


PRICE_REVISION = 'fal-video-2026-10-01-v2'
VALID_FROM = datetime(2026, 9, 23, tzinfo=timezone.utc)
VALID_UNTIL = datetime(2026, 11, 1, tzinfo=timezone.utc)
ORIGIN = 'https://queue.fal.run'
MODELS = {
    'veo_lite': 'fal-ai/veo3.1/lite',
    'seedance_pro': 'fal-ai/bytedance/seedance/v1.5/pro/text-to-video',
    'seedance_fast': 'fal-ai/bytedance/seedance/v1/pro/fast/text-to-video',
}
PROVIDERS = {
    MODELS['veo_lite']: 'fal_veo_lite',
    MODELS['seedance_pro']: 'fal_seedance_15_pro',
    MODELS['seedance_fast']: 'fal_seedance_1_fast',
}


def _require(condition):
    if not condition:
        raise SpendBlocked('spend_fal_request_not_priced')


def primary_enabled(settings):
    """An installed key selects Fal in auto mode; legacy is an explicit rollback."""
    mode = getattr(settings, 'studio_video_provider', 'legacy')
    return mode == 'fal' or (mode == 'auto' and bool(str(getattr(settings, 'fal_key', '') or '').strip()))


COST_QUALITY_CHANNELS = frozenset(channel_ids.MANAGED)


def select_model(seconds, preference='auto', *, channel_id=None):
    """Use silent Seedance Pro for the owner's quality/cost trial.

    Every output still needs the same independent visual and motion QA.
    Veo and Fast remain explicit choices; no automatic second paid fallback.
    Existing accepted requests stay pinned by the durable native journal.
    """
    _require(type(seconds) is int and 2 <= seconds <= 10)
    _require(type(preference) is str and preference in {'auto', *MODELS})
    name = (('seedance_pro' if channel_id in COST_QUALITY_CHANNELS
             else 'veo_lite' if seconds <= 8 else 'seedance_fast')
            if preference == 'auto' else preference)
    model = MODELS[name]
    duration_for(model, seconds)
    return model


def duration_for(model, seconds):
    _require(type(model) is str and model in PROVIDERS)
    _require(type(seconds) is int and 2 <= seconds <= 10)
    if model == MODELS['veo_lite']:
        _require(seconds <= 8)
        return next(value for value in (4, 6, 8) if value >= seconds)
    return max(4, seconds) if model == MODELS['seedance_pro'] else seconds


def build_request(model, prompt, seconds, aspect_ratio):
    _require(type(prompt) is str and bool(prompt.strip())
             and len(prompt.encode('utf-16-le')) // 2 <= 1000)
    _require(type(aspect_ratio) is str and aspect_ratio in {'16:9', '9:16'})
    duration = duration_for(model, seconds)
    body = {'prompt': prompt, 'resolution': '720p', 'aspect_ratio': aspect_ratio,
            'duration': str(duration)}
    if model == MODELS['veo_lite']:
        body.update(duration=f'{duration}s', generate_audio=False, auto_fix=False)
    else:
        body['enable_safety_checker'] = True
        if model == MODELS['seedance_pro']:
            body['generate_audio'] = False
    return body


def quote_request(model, body):
    _require(type(model) is str and model in PROVIDERS and type(body) is dict)
    raw_duration = body.get('duration')
    _require(type(raw_duration) is str)
    duration = raw_duration.removesuffix('s') if model == MODELS['veo_lite'] else raw_duration
    _require(duration in {str(value) for value in range(2, 11)})
    seconds = int(duration)
    _require(body == build_request(model, body.get('prompt'), seconds, body.get('aspect_ratio')))
    # Equality alone would also admit 0 instead of False and 1 instead of True.
    for field in ('generate_audio', 'auto_fix', 'enable_safety_checker'):
        if field in body:
            _require(type(body[field]) is bool)
    now = datetime.now(timezone.utc)
    if not VALID_FROM <= now < VALID_UNTIL:
        raise SpendBlocked('spend_price_review_expired')
    if model == MODELS['veo_lite']:
        amount = Decimal('0.03') * seconds
    else:
        # 1280 x 720 (or portrait transpose), 24fps. Round the whole request
        # UP to a cent; the published token formula is an estimate, not an invoice.
        rate = Decimal('1.2') if model == MODELS['seedance_pro'] else Decimal('1')
        amount = (Decimal(1280 * 720 * 24 * seconds) / 1024 / 1_000_000 * rate
                  ).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    return SpendQuote('fal', model, usd_micro(amount), PRICE_REVISION)


def describe_request(model, body):
    quote = quote_request(model, body)
    return {'provider': 'fal', 'model': model,
            'duration_seconds': int(body['duration'].removesuffix('s')),
            'aspect_ratio': body['aspect_ratio'], 'resolution': '720p',
            'audio': False, 'sample_count': 1, 'price_revision': quote.price_revision}
