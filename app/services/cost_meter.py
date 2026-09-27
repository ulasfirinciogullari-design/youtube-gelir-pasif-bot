"""Passive cost meter: record what every paid provider call cost, as it happens.

This module only observes. It runs after a paid request has already returned,
never blocks, retries or changes a request, and swallows every error of its
own. It is independent of the spend-enforcement ledger and works while that
guard is off.

Amounts are estimates from public list prices (``PRICES`` below, overridable
with ``COST_METER_PRICES_JSON``). Token-billed calls use the provider's own
usage figures when the response reports them. A call whose price is unknown
is recorded with ``priced=False`` so gaps stay visible instead of looking free.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from urllib.parse import urlsplit

EVENTS_KEY = 'cost_meter:events'
DAY_PREFIX = 'cost_meter:day:'
MONTH_PREFIX = 'cost_meter:month:'
JOB_PREFIX = 'cost_meter:job:'
MAX_EVENTS = 5000
_TTL_DAYS = 120

# USD list prices. Tokens are per million; seconds/characters/minutes are per unit.
PRICES = {
    'openai': {
        'gpt-6-astra': {'input': 10.0, 'cached_input': 1.0, 'output': 50.0},
        'gpt-5': {'input': 1.25, 'cached_input': 0.125, 'output': 10.0},
        'gpt-4.1-mini': {'input': 0.4, 'cached_input': 0.1, 'output': 1.6, 'web_search_call': 0.01},
    },
    'gemini': {
        'gemini-3.1-pro-preview': {'input': 2.0, 'output': 12.0},
        'gemini-3.7-flash': {'input': 0.75, 'output': 3.75},
        'veo-3.1-lite-generate-preview': {'second': 0.05},
        'veo-3.1-fast-generate-preview': {'second': 0.10},
        'veo-3.1-generate-preview': {'second': 0.40},
    },
    'abacus': {
        'claude-haiku-4-5-20251001': {'input': 1.0, 'output': 5.0},
        '*': {'input': 3.0, 'output': 15.0},
    },
    'runway': {
        'gen4.5': {'second': 0.12},
        'seedance2_fast': {'second': 0.29},
    },
    'fal': {
        'fal-ai/veo3.1/lite': {'second': 0.05},
        'fal-ai/bytedance/seedance/v1.5/pro/text-to-video': {'second': 0.052},
        'fal-ai/bytedance/seedance/v1/pro/fast/text-to-video': {'second': 0.045},
    },
    # Plan-dependent; set the account's real rate with COST_METER_PRICES_JSON.
    'elevenlabs': {'*': {'character': 0.00018}},
}

PROVIDER_LABELS = {
    'openai': 'OpenAI', 'gemini': 'Google Gemini', 'abacus': 'Abacus',
    'runway': 'Runway', 'fal': 'Fal', 'elevenlabs': 'ElevenLabs', 'kie': 'Kie',
}


def _prices() -> dict:
    try:
        from app.config import settings
        raw = getattr(settings, 'cost_meter_prices_json', '') or ''
        if raw.strip():
            override = json.loads(raw)
            merged = {provider: dict(models) for provider, models in PRICES.items()}
            for provider, models in override.items():
                merged.setdefault(provider, {}).update(models)
            return merged
    except Exception:
        pass
    return PRICES


def _rate(provider: str, model: str) -> dict | None:
    table = _prices().get(provider, {})
    return table.get(model) or table.get('*')


def _tokens_cost(rate: dict, input_tokens: int, output_tokens: int, cached: int = 0) -> Decimal:
    fresh = max(0, input_tokens - cached)
    total = (Decimal(fresh) * Decimal(str(rate.get('input', 0)))
             + Decimal(cached) * Decimal(str(rate.get('cached_input', rate.get('input', 0))))
             + Decimal(output_tokens) * Decimal(str(rate.get('output', 0))))
    return total / 1_000_000


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def estimate_openai_response(request: dict, response) -> dict:
    model = str(request.get('model') or _get(response, 'model') or '')
    usage = _get(response, 'usage')
    rate = _rate('openai', model)
    if usage is None or rate is None:
        return {'provider': 'openai', 'model': model, 'operation': 'responses', 'usd': 0.0, 'priced': False}
    details = _get(usage, 'input_tokens_details') or {}
    input_tokens = int(_get(usage, 'input_tokens', 0) or 0)
    output_tokens = int(_get(usage, 'output_tokens', 0) or 0)
    cached = int(_get(details, 'cached_tokens', 0) or 0)
    usd = _tokens_cost(rate, input_tokens, output_tokens, cached)
    searches = sum(1 for item in (_get(response, 'output') or [])
                   if _get(item, 'type') == 'web_search_call')
    usd += Decimal(searches) * Decimal(str(rate.get('web_search_call', 0)))
    return {'provider': 'openai', 'model': model, 'operation': 'responses', 'usd': float(usd),
            'priced': True, 'units': f'{input_tokens} girdi + {output_tokens} çıktı token'}


def estimate_runway(request: dict) -> dict:
    model = str(request.get('model') or '')
    seconds = int(request.get('duration') or 0)
    rate = _rate('runway', model)
    if rate is None or not seconds:
        return {'provider': 'runway', 'model': model, 'operation': 'text_to_video', 'usd': 0.0, 'priced': False}
    return {'provider': 'runway', 'model': model, 'operation': 'text_to_video',
            'usd': float(Decimal(str(rate['second'])) * seconds), 'priced': True,
            'units': f'{seconds} sn video'}


def _json_body(response) -> dict | None:
    try:
        if 'json' not in str(response.headers.get('content-type', '')):
            return None
        body = response.json()
        return body if isinstance(body, dict) else None
    except Exception:
        return None


def estimate_http(url: str, request_kwargs: dict, response) -> dict | None:
    """Price a paid HTTP POST; None means the URL is not a known paid provider."""
    parsed = urlsplit(url)
    host, path = parsed.hostname or '', parsed.path
    body = request_kwargs.get('json') if isinstance(request_kwargs.get('json'), dict) else {}
    if host == 'api.elevenlabs.io':
        characters = len(str(body.get('text') or ''))
        rate = _rate('elevenlabs', str(body.get('model_id') or ''))
        model = str(body.get('model_id') or 'tts')
        if not characters or rate is None:
            return {'provider': 'elevenlabs', 'model': model, 'operation': path, 'usd': 0.0, 'priced': False}
        return {'provider': 'elevenlabs', 'model': model, 'operation': path,
                'usd': float(Decimal(str(rate['character'])) * characters), 'priced': True,
                'units': f'{characters} karakter'}
    if host == 'queue.fal.run':
        model = path.lstrip('/')
        rate = _rate('fal', model)
        seconds = body.get('duration')
        try:
            seconds = int(str(seconds).rstrip('s'))
        except Exception:
            seconds = 0
        if rate is None or not seconds:
            return {'provider': 'fal', 'model': model, 'operation': 'video', 'usd': 0.0, 'priced': False}
        return {'provider': 'fal', 'model': model, 'operation': 'video',
                'usd': float(Decimal(str(rate['second'])) * seconds), 'priced': True,
                'units': f'{seconds} sn video'}
    if host == 'generativelanguage.googleapis.com':
        match = re.fullmatch(r'/v1beta/models/([A-Za-z0-9._-]+):(generateContent|predictLongRunning)', path)
        if not match:
            return None
        model, operation = match[1], match[2]
        rate = _rate('gemini', model)
        if operation == 'predictLongRunning':
            seconds = int((body.get('parameters') or {}).get('durationSeconds') or 0)
            if rate is None or 'second' not in rate or not seconds:
                return {'provider': 'gemini', 'model': model, 'operation': 'video', 'usd': 0.0, 'priced': False}
            return {'provider': 'gemini', 'model': model, 'operation': 'video',
                    'usd': float(Decimal(str(rate['second'])) * seconds), 'priced': True,
                    'units': f'{seconds} sn video'}
        usage = (_json_body(response) or {}).get('usageMetadata') or {}
        if rate is None or not usage:
            return {'provider': 'gemini', 'model': model, 'operation': 'text', 'usd': 0.0, 'priced': False}
        input_tokens = int(usage.get('promptTokenCount') or 0)
        output_tokens = int(usage.get('candidatesTokenCount') or 0) + int(usage.get('thoughtsTokenCount') or 0)
        return {'provider': 'gemini', 'model': model, 'operation': 'text',
                'usd': float(_tokens_cost(rate, input_tokens, output_tokens)), 'priced': True,
                'units': f'{input_tokens} girdi + {output_tokens} çıktı token'}
    if host == 'routellm.abacus.ai':
        model = str(body.get('model') or '')
        usage = (_json_body(response) or {}).get('usage') or {}
        rate = _rate('abacus', model)
        if rate is None or not usage:
            return {'provider': 'abacus', 'model': model, 'operation': 'messages', 'usd': 0.0, 'priced': False}
        input_tokens = int(usage.get('input_tokens') or 0)
        output_tokens = int(usage.get('output_tokens') or 0)
        return {'provider': 'abacus', 'model': model, 'operation': 'messages',
                'usd': float(_tokens_cost(rate, input_tokens, output_tokens)), 'priced': True,
                'units': f'{input_tokens} girdi + {output_tokens} çıktı token'}
    if host == 'api.kie.ai':
        return {'provider': 'kie', 'model': str(body.get('model') or ''), 'operation': path,
                'usd': 0.0, 'priced': False}
    return None


def _client():
    import redis
    from app.config import settings
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_timeout=0.5, socket_connect_timeout=0.5)


def _task_context() -> tuple[str | None, int | None]:
    try:
        from app.services.production_spend_runtime import _SCENE, _TASK_ID
        scene = _SCENE.get()
        return _TASK_ID.get(), scene.get('scene_index') if isinstance(scene, dict) else None
    except Exception:
        return None, None


def record(entry: dict, *, client=None, now: datetime | None = None) -> None:
    """Store one priced call. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        task_id, scene = _task_context()
        event = {**entry, 'at': now.isoformat(timespec='seconds'), 'task_id': task_id}
        if scene is not None:
            event['scene'] = scene
        usd = float(entry.get('usd') or 0.0)
        client = client or _client()
        day_key = DAY_PREFIX + now.strftime('%Y-%m-%d')
        month_key = MONTH_PREFIX + now.strftime('%Y-%m')
        provider = str(entry.get('provider') or 'unknown')
        with client.pipeline(transaction=False) as pipe:
            pipe.lpush(EVENTS_KEY, json.dumps(event, ensure_ascii=False))
            pipe.ltrim(EVENTS_KEY, 0, MAX_EVENTS - 1)
            for key in (day_key, month_key):
                pipe.hincrbyfloat(key, 'total', usd)
                pipe.hincrbyfloat(key, 'provider:' + provider, usd)
                pipe.hincrby(key, 'calls', 1)
                if not entry.get('priced'):
                    pipe.hincrby(key, 'unpriced_calls', 1)
                pipe.expire(key, _TTL_DAYS * 86400)
            if task_id:
                job_key = JOB_PREFIX + task_id
                pipe.hincrbyfloat(job_key, 'total', usd)
                pipe.hincrbyfloat(job_key, 'provider:' + provider, usd)
                pipe.hincrby(job_key, 'calls', 1)
                pipe.hsetnx(job_key, 'first_at', event['at'])
                pipe.hset(job_key, 'last_at', event['at'])
                pipe.expire(job_key, _TTL_DAYS * 86400)
            pipe.execute()
    except Exception:
        pass


def observe_http(url, request_kwargs, response) -> None:
    try:
        entry = estimate_http(str(url), request_kwargs or {}, response)
        if entry is not None:
            record(entry)
    except Exception:
        pass


def observe_openai(request, response) -> None:
    try:
        record(estimate_openai_response(request or {}, response))
    except Exception:
        pass


def observe_runway(request) -> None:
    try:
        record(estimate_runway(request or {}))
    except Exception:
        pass


def _hash_numbers(raw: dict) -> dict:
    result = {'total': 0.0, 'calls': 0, 'unpriced_calls': 0, 'providers': {}}
    for key, value in (raw or {}).items():
        if key.startswith('provider:'):
            result['providers'][key[len('provider:'):]] = round(float(value), 4)
        elif key in ('total',):
            result['total'] = round(float(value), 4)
        elif key in ('calls', 'unpriced_calls'):
            result[key] = int(value)
        else:
            result[key] = value
    return result


def summary(*, client=None, now: datetime | None = None, days: int = 14, events: int = 100) -> dict:
    """Everything the cost page shows, read-only."""
    now = now or datetime.now(timezone.utc)
    client = client or _client()
    from datetime import timedelta
    day_list = [(now - timedelta(days=offset)).strftime('%Y-%m-%d') for offset in range(days)]
    with client.pipeline(transaction=False) as pipe:
        for day in day_list:
            pipe.hgetall(DAY_PREFIX + day)
        pipe.hgetall(MONTH_PREFIX + now.strftime('%Y-%m'))
        pipe.lrange(EVENTS_KEY, 0, max(0, events - 1))
        raw = pipe.execute()
    daily = [{'day': day, **_hash_numbers(values)} for day, values in zip(day_list, raw[:days])]
    month = _hash_numbers(raw[days])
    recent = []
    for item in raw[days + 1]:
        try:
            recent.append(json.loads(item))
        except Exception:
            continue
    task_ids = list(dict.fromkeys(e['task_id'] for e in recent if e.get('task_id')))[:30]
    jobs = []
    if task_ids:
        with client.pipeline(transaction=False) as pipe:
            for task_id in task_ids:
                pipe.hgetall(JOB_PREFIX + task_id)
            job_raw = pipe.execute()
        for task_id, values in zip(task_ids, job_raw):
            jobs.append({'task_id': task_id, **_hash_numbers(values)})
    elapsed_days = now.day
    projected = month['total'] / elapsed_days * 30 if elapsed_days else 0.0
    return {'today': daily[0], 'daily': daily, 'month': month, 'recent': recent,
            'jobs': jobs, 'projected_month': round(projected, 2),
            'checked_at': now.isoformat(timespec='seconds')}
