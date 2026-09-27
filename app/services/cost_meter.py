"""Passive cost meter: record what every paid provider call cost, as it happens.

This module only observes. It runs after a paid request has already returned,
never blocks, retries or changes a request, and swallows every error of its
own. It is independent of the spend-enforcement ledger and works while that
guard is off.

Amounts are estimates from public list prices (``PRICES`` below, overridable
with ``COST_METER_PRICES_JSON``). Token-billed calls use the provider's own
usage figures when the response reports them. A call whose price is unknown
is recorded with ``priced=False`` so gaps stay visible instead of looking free.
Calls paid from a flat subscription are counted but add no dollars.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from urllib.parse import urlsplit

EVENTS_KEY = 'cost_meter:events'
DAY_PREFIX = 'cost_meter:day:'
MONTH_PREFIX = 'cost_meter:month:'
JOB_PREFIX = 'cost_meter:job:'
JOB_INDEX = 'cost_meter:jobs'
# Turkey stays on UTC+3 all year; days and months follow the owner's clock.
LOCAL_TZ = timezone(timedelta(hours=3))
MAX_EVENTS = 5000
MAX_INDEXED_JOBS = 2000
_TTL_DAYS = 120

# USD list prices. Tokens are per million; seconds/characters/credits are per unit.
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
    'runway': {
        'gen4.5': {'second': 0.12},
        'seedance2_fast': {'second': 0.29},
    },
    'fal': {
        'fal-ai/veo3.1/lite': {'second': 0.05},
        'fal-ai/bytedance/seedance/v1.5/pro/text-to-video': {'second': 0.052},
        'fal-ai/bytedance/seedance/v1/pro/fast/text-to-video': {'second': 0.045},
        'fal-ai/elevenlabs/tts/turbo-v2.5': {'character': 0.00005},
    },
    # Plan-dependent; set the account's real rate with COST_METER_PRICES_JSON.
    'elevenlabs': {'*': {'character': 0.00018}},
    # Kie bills credits (turbo 6, multilingual 12 per 1,000 characters).
    'kie': {
        'elevenlabs/text-to-speech-turbo-2-5': {'character_credits': 0.006, 'credit': 0.005},
        'elevenlabs/text-to-speech-multilingual-v2': {'character_credits': 0.012, 'credit': 0.005},
    },
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


def _unpriced(provider, model, operation):
    return {'provider': provider, 'model': model, 'operation': operation, 'usd': 0.0, 'priced': False}


def _per_second(provider, model, seconds, operation='video'):
    rate = _rate(provider, model)
    if rate is None or 'second' not in rate or not seconds:
        return _unpriced(provider, model, operation)
    return {'provider': provider, 'model': model, 'operation': operation,
            'usd': float(Decimal(str(rate['second'])) * seconds), 'priced': True,
            'units': f'{seconds} sn video'}


def _per_character(provider, model, text, operation='speech'):
    characters = len(str(text or ''))
    rate = _rate(provider, model)
    if not characters or rate is None:
        return _unpriced(provider, model, operation)
    if 'character_credits' in rate:
        credits = Decimal(str(rate['character_credits'])) * characters
        return {'provider': provider, 'model': model, 'operation': operation,
                'usd': float(credits * Decimal(str(rate.get('credit', 0)))), 'priced': 'credit' in rate,
                'units': f'{characters} karakter · {float(credits):.1f} kredi'}
    if 'character' not in rate:
        return _unpriced(provider, model, operation)
    return {'provider': provider, 'model': model, 'operation': operation,
            'usd': float(Decimal(str(rate['character'])) * characters), 'priced': True,
            'units': f'{characters} karakter'}


def _tokens(provider, model, operation, input_tokens, output_tokens, cached=0):
    rate = _rate(provider, model)
    if rate is None:
        return _unpriced(provider, model, operation)
    return {'provider': provider, 'model': model, 'operation': operation,
            'usd': float(_tokens_cost(rate, input_tokens, output_tokens, cached)), 'priced': True,
            'units': f'{input_tokens} girdi + {output_tokens} çıktı token'}


def estimate_openai_response(request: dict, response) -> dict:
    model = str(request.get('model') or _get(response, 'model') or '')
    usage = _get(response, 'usage')
    if usage is None:
        return _unpriced('openai', model, 'responses')
    details = _get(usage, 'input_tokens_details') or {}
    entry = _tokens('openai', model, 'responses', int(_get(usage, 'input_tokens', 0) or 0),
                    int(_get(usage, 'output_tokens', 0) or 0), int(_get(details, 'cached_tokens', 0) or 0))
    searches = sum(1 for item in (_get(response, 'output') or [])
                   if _get(item, 'type') == 'web_search_call')
    rate = _rate('openai', model)
    if searches and entry['priced'] and rate:
        entry['usd'] += float(Decimal(searches) * Decimal(str(rate.get('web_search_call', 0))))
        entry['units'] += f' · {searches} web araması'
    return entry


def estimate_runway(request: dict) -> dict:
    return _per_second('runway', str(request.get('model') or ''), int(request.get('duration') or 0),
                       'text_to_video')


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
        return _per_character('elevenlabs', str(body.get('model_id') or 'tts'), body.get('text'))
    if host == 'queue.fal.run':
        model = path.lstrip('/')
        if 'text' in body and 'duration' not in body:
            return _per_character('fal', model, body.get('text'))
        try:
            seconds = int(str(body.get('duration')).rstrip('s'))
        except Exception:
            seconds = 0
        return _per_second('fal', model, seconds)
    if host == 'api.kie.ai':
        text = (body.get('input') or {}).get('text') if isinstance(body.get('input'), dict) else None
        return _per_character('kie', str(body.get('model') or ''), text)
    if host == 'generativelanguage.googleapis.com':
        match = re.fullmatch(r'/v1beta/models/([A-Za-z0-9._-]+):(generateContent|predictLongRunning)', path)
        if not match:
            return None
        model, operation = match[1], match[2]
        if operation == 'predictLongRunning':
            return _per_second('gemini', model, int((body.get('parameters') or {}).get('durationSeconds') or 0))
        usage = (_json_body(response) or {}).get('usageMetadata') or {}
        if not usage:
            return _unpriced('gemini', model, 'text')
        return _tokens('gemini', model, 'text', int(usage.get('promptTokenCount') or 0),
                       int(usage.get('candidatesTokenCount') or 0) + int(usage.get('thoughtsTokenCount') or 0))
    if host == 'routellm.abacus.ai':
        usage = (_json_body(response) or {}).get('usage') or {}
        entry = {'provider': 'abacus', 'model': str(body.get('model') or ''), 'operation': 'messages',
                 'usd': 0.0, 'priced': True, 'subscription': True}
        if usage:
            entry['units'] = (f"{int(usage.get('input_tokens') or 0)} girdi + "
                              f"{int(usage.get('output_tokens') or 0)} çıktı token · abonelik")
        return entry
    return None


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        import redis
        from app.config import settings
        _CLIENT = redis.Redis.from_url(settings.redis_url, decode_responses=True,
                                       socket_timeout=0.5, socket_connect_timeout=0.5)
    return _CLIENT


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
        local = now.astimezone(LOCAL_TZ)
        day_key = DAY_PREFIX + local.strftime('%Y-%m-%d')
        month_key = MONTH_PREFIX + local.strftime('%Y-%m')
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
                pipe.hsetnx(key, 'first_at', event['at'])
                pipe.expire(key, _TTL_DAYS * 86400)
            if task_id:
                job_key = JOB_PREFIX + task_id
                pipe.hincrbyfloat(job_key, 'total', usd)
                pipe.hincrbyfloat(job_key, 'provider:' + provider, usd)
                pipe.hincrby(job_key, 'calls', 1)
                pipe.hsetnx(job_key, 'first_at', event['at'])
                pipe.hset(job_key, 'last_at', event['at'])
                pipe.expire(job_key, _TTL_DAYS * 86400)
                pipe.zadd(JOB_INDEX, {task_id: now.timestamp()})
                pipe.zremrangebyrank(JOB_INDEX, 0, -MAX_INDEXED_JOBS - 1)
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
        elif key == 'total':
            result['total'] = round(float(value), 4)
        elif key in ('calls', 'unpriced_calls'):
            result[key] = int(value)
        else:
            result[key] = value
    return result


def _root(task_id: str, jobs_by_id: dict) -> str:
    """Oldest ancestor known to Studio, so plan, render and repairs add up per video."""
    seen = set()
    current = task_id
    while current in jobs_by_id and current not in seen and len(seen) < 12:
        seen.add(current)
        parent = jobs_by_id[current].get('parent_id')
        if not isinstance(parent, str) or not parent:
            break
        current = parent
    return current


def _title(job: dict | None) -> str:
    spec = (job or {}).get('spec') or {}
    return str(spec.get('title') or spec.get('topic') or '')[:120]


def video_costs(job_costs: dict, jobs: list, videos: dict) -> list[dict]:
    """Group per-task costs into one row per video family and join YouTube views."""
    jobs_by_id = {job.get('task_id'): job for job in jobs if isinstance(job, dict) and job.get('task_id')}
    families: dict[str, dict] = {}
    for task_id, cost in job_costs.items():
        root = _root(task_id, jobs_by_id)
        family = families.setdefault(root, {'root_id': root, 'total': 0.0, 'calls': 0, 'providers': {},
                                            'tasks': [], 'last_at': ''})
        family['total'] += cost['total']
        family['calls'] += cost.get('calls', 0)
        family['tasks'].append(task_id)
        family['last_at'] = max(family['last_at'], str(cost.get('last_at') or ''))
        for provider, usd in cost['providers'].items():
            family['providers'][provider] = family['providers'].get(provider, 0.0) + usd
    for source_id, video in (videos or {}).items():
        if not isinstance(video, dict):
            continue
        root = _root(source_id, jobs_by_id)
        family = families.get(root)
        if family is None:
            continue
        views = video.get('view_count')
        if type(views) is int:
            family['views'] = family.get('views', 0) + views
        family['public'] = family.get('public') or video.get('privacy_status') == 'public'
        family['video_title'] = family.get('video_title') or video.get('title')
    rows = []
    for family in families.values():
        states = {str(jobs_by_id.get(task, {}).get('state') or '') for task in family['tasks']}
        if family.get('public'):
            status = 'published'
        elif states & {'PENDING', 'STARTED', 'PROGRESS', 'RETRY', 'AWAITING_APPROVAL', ''}:
            status = 'running'
        elif 'SUCCESS' in states:
            status = 'unpublished'  # Finished but not public (yet); not counted as waste.
        else:
            status = 'failed'
        views = family.get('views')
        rows.append({
            'root_id': family['root_id'], 'title': family.get('video_title')
            or _title(jobs_by_id.get(family['root_id'])) or family['root_id'][:8],
            'total': round(family['total'], 4), 'calls': family['calls'],
            'providers': {k: round(v, 4) for k, v in family['providers'].items()},
            'status': status, 'views': views, 'last_at': family['last_at'],
            'usd_per_1000_views': round(family['total'] / views * 1000, 2) if views else None,
        })
    rows.sort(key=lambda row: row['last_at'], reverse=True)
    return rows


def _projected_month(month: dict, local: datetime) -> float:
    """Extrapolate from when metering started this month, not from the 1st."""
    next_month = (local.replace(day=28) + timedelta(days=4)).replace(day=1)
    days_in_month = (next_month - timedelta(days=1)).day
    try:
        started = datetime.fromisoformat(str(month.get('first_at'))).astimezone(LOCAL_TZ)
    except (TypeError, ValueError):
        return 0.0
    elapsed = max((local - started).total_seconds() / 86400, 1.0)
    remaining_days = days_in_month - local.day + (1 - (local.hour * 60 + local.minute) / 1440)
    return month['total'] + month['total'] / elapsed * max(remaining_days, 0.0)


def _studio_jobs() -> list:
    try:
        from app.services.studio_state import list_jobs
        return list_jobs(500)
    except Exception:
        return []


def _youtube_videos(jobs: list) -> dict:
    try:
        from app.services.youtube_metrics import get_dashboard_metrics
        return get_dashboard_metrics(jobs).get('videos') or {}
    except Exception:
        return {}


def summary(*, client=None, now: datetime | None = None, days: int = 14, events: int = 100,
            jobs: list | None = None, videos: dict | None = None) -> dict:
    """Everything the cost page shows, read-only."""
    now = now or datetime.now(timezone.utc)
    client = client or _client()
    local = now.astimezone(LOCAL_TZ)
    day_list = [(local - timedelta(days=offset)).strftime('%Y-%m-%d') for offset in range(days)]
    with client.pipeline(transaction=False) as pipe:
        for day in day_list:
            pipe.hgetall(DAY_PREFIX + day)
        pipe.hgetall(MONTH_PREFIX + local.strftime('%Y-%m'))
        pipe.lrange(EVENTS_KEY, 0, max(0, events - 1))
        pipe.zrevrangebyscore(JOB_INDEX, '+inf', (now - timedelta(days=30)).timestamp(), start=0, num=400)
        raw = pipe.execute()
    daily = [{'day': day, **_hash_numbers(values)} for day, values in zip(day_list, raw[:days])]
    month = _hash_numbers(raw[days])
    recent = []
    for item in raw[days + 1]:
        try:
            recent.append(json.loads(item))
        except Exception:
            continue
    task_ids = list(raw[days + 2])
    job_costs = {}
    if task_ids:
        with client.pipeline(transaction=False) as pipe:
            for task_id in task_ids:
                pipe.hgetall(JOB_PREFIX + task_id)
            for task_id, values in zip(task_ids, pipe.execute()):
                if values:
                    job_costs[task_id] = _hash_numbers(values)
    jobs = _studio_jobs() if jobs is None else jobs
    videos = _youtube_videos(jobs) if videos is None else videos
    rows = video_costs(job_costs, jobs, videos)
    published = [row for row in rows if row['status'] == 'published']
    wasted = [row for row in rows if row['status'] == 'failed']
    total_views = sum(row['views'] or 0 for row in published)
    published_cost = sum(row['total'] for row in published)
    projected = _projected_month(month, local)
    return {
        'today': daily[0], 'daily': daily, 'month': month, 'recent': recent, 'videos': rows[:60],
        'projected_month': round(projected, 2),
        'per_published_video': round(published_cost / len(published), 2) if published else None,
        'usd_per_1000_views': round(published_cost / total_views * 1000, 2) if total_views else None,
        'wasted_total': round(sum(row['total'] for row in wasted), 2), 'wasted_count': len(wasted),
        'checked_at': now.isoformat(timespec='seconds'),
    }
