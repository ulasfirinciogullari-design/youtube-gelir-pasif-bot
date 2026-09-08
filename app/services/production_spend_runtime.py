"""Connect paid provider submissions to the durable production spend ledger.

No credentials or request contents are persisted. Deterministic request hashes
are shared by a repair family: a timeout, SDK retry or new queue ID cannot
authorize an identical paid submission again. Unknown prices stay blocked.
The default rollout flag is OFF; this module never initializes live budgets.
"""
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from functools import wraps
import hashlib
import json
import math
import re

import redis

from app.config import settings
from app.services.production_spend import (
    LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy,
)


_TASK_ID = ContextVar('production_spend_task', default=None)
_JOB_PREFIX = 'youtube_studio:job:'
_CHANNEL_PREFIX = 'youtube_studio:oauth:channel:v3:'
_CHANNEL_INDEX = 'youtube_studio:oauth:channels:v3'
_JOB_ID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
_CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')


def enforcement_enabled():
    return getattr(settings, 'studio_spend_enforcement', False) is True


def spending_task(function):
    """Carry the real Celery task ID, never an identity from an API body."""
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        token = _TASK_ID.set(getattr(self.request, 'id', None))
        try:
            return function(self, *args, **kwargs)
        finally:
            _TASK_ID.reset(token)
    return wrapped


class SpendingThreadPoolExecutor(_ThreadPoolExecutor):
    """Each parallel narration/critic operation inherits its own context copy."""
    def submit(self, function, /, *args, **kwargs):
        context = copy_context()
        return super().submit(context.run, function, *args, **kwargs)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise SpendBlocked('spend_request_invalid') from None


def _object(raw):
    try:
        value = json.loads(raw)
        if type(value) is not dict:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise SpendBlocked('spend_context_invalid') from None


def configured_ledger():
    try:
        policy = SpendPolicy(**_object(settings.studio_spend_policy_json)).validate()
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        return SpendLedger(client, policy)
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_policy_invalid') from None


def _job_identity(job):
    spec = job.get('spec')
    if type(spec) is not dict:
        raise SpendBlocked('spend_context_invalid')
    channel_id = spec.get('production_channel_id') or spec.get('youtube_channel_id')
    connection_id = spec.get('production_connection_id') or spec.get('youtube_connection_id')
    if (type(channel_id) is not str or not _CHANNEL_ID.fullmatch(channel_id)
            or type(connection_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', connection_id)):
        raise SpendBlocked('spend_channel_binding_invalid')
    duration = spec.get('duration_minutes')
    if (type(duration) not in (int, float) or not math.isfinite(duration)
            or not 0 < duration <= 120):
        raise SpendBlocked('spend_context_invalid')
    return channel_id, connection_id, 'long' if duration > 1 else 'shorts'


def _resolve_context_once(client, task_id):
    """Read the stored ancestry, not task arguments; fail closed on lost jobs.

    Parent links are server-owned. Repairs/derived outputs keep the oldest
    job's budget kind and allowance. A persistent binding detects later edits
    to the lineage/channel/kind even after a new queue task is introduced.
    """
    if type(task_id) is not str or not _JOB_ID.fullmatch(task_id):
        raise SpendBlocked('spend_context_missing')
    try:
        with client.pipeline() as pipe:
            pipe.watch(LEDGER_KEY)
            # This is deliberately not an initialization path.
            if not pipe.hexists(LEDGER_KEY, 'policy'):
                raise SpendBlocked('spend_not_initialized')
            current, seen, nodes, expected = task_id, set(), [], None
            while current is not None:
                if (type(current) is not str or not _JOB_ID.fullmatch(current)
                        or current in seen or len(seen) >= 64):
                    raise SpendBlocked('spend_lineage_invalid')
                seen.add(current)
                pipe.watch(_JOB_PREFIX + current)
                job = _object(pipe.get(_JOB_PREFIX + current))
                if job.get('task_id') != current:
                    raise SpendBlocked('spend_lineage_invalid')
                identity = _job_identity(job)
                if expected is not None and identity[:2] != expected[:2]:
                    raise SpendBlocked('spend_lineage_binding_invalid')
                expected = identity
                nodes.append((current, identity))
                current = job.get('parent_id')
            channel_id, connection_id, kind = nodes[-1][1]
            binding = {'channel_id': channel_id, 'connection_id': connection_id,
                       'lineage_id': nodes[-1][0], 'kind': kind}
            pipe.watch(_CHANNEL_PREFIX + channel_id, _CHANNEL_INDEX)
            channel = _object(pipe.get(_CHANNEL_PREFIX + channel_id))
            if (channel.get('id') != channel_id or channel.get('connection_id') != connection_id
                    or not pipe.sismember(_CHANNEL_INDEX, channel_id)):
                raise SpendBlocked('spend_channel_binding_invalid')
            bindings = {}
            for job_id, _ in nodes:
                field = 'binding:' + job_id
                prior = pipe.hget(LEDGER_KEY, field)
                if prior is not None and _object(prior) != binding:
                    raise SpendBlocked('spend_lineage_binding_invalid')
                bindings[field] = _json(binding)
            pipe.multi()
            pipe.hset(LEDGER_KEY, mapping=bindings)
            result = pipe.execute()
            if len(result) != 1 or type(result[0]) is not int:
                raise SpendBlocked('spend_binding_uncertain')
            return binding
    except redis.exceptions.WatchError:
        raise
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_context_unavailable') from None


def resolve_context(client, task_id):
    for _ in range(12):
        try:
            return _resolve_context_once(client, task_id)
        except redis.exceptions.WatchError:
            continue  # No provider request or spending permit exists yet.
    raise SpendBlocked('spend_store_contention')


def reserve_request(provider, operation, payload, quote):
    ledger = configured_ledger()
    context = resolve_context(ledger.client, _TASK_ID.get())
    fingerprint = hashlib.sha256(_json({
        'lineage': context['lineage_id'], 'provider': provider,
        'operation': operation, 'payload': payload,
    }).encode()).hexdigest()
    return ledger.reserve(
        request_key=fingerprint, channel_id=context['channel_id'],
        lineage_id=context['lineage_id'], kind=context['kind'], quote=quote,
    )


def paid_post(sender, url, **kwargs):
    """Guard a paid HTTP POST before its transport sees credentials or media."""
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_http_request
        provider, operation, quote = quote_http_request(url, kwargs)
        # Unsupported multipart/media payloads are rejected by the quote layer;
        # nothing reads upload streams or persists narration/image contents.
        reserve_request(provider, operation, kwargs.get('json'), quote)
    return sender(url, **kwargs)


def paid_response(client, **kwargs):
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_openai_response
        if str(getattr(client, 'base_url', '')).rstrip('/') != 'https://api.openai.com/v1':
            raise SpendBlocked('spend_endpoint_not_priced')
        kwargs = dict(kwargs)
        kwargs.setdefault('max_output_tokens', 8192)
        kwargs.setdefault('store', False)
        quote = quote_openai_response(kwargs)
        reserve_request('openai', 'responses', kwargs, quote)
        client = client.with_options(max_retries=0)
    return client.responses.create(**kwargs)


def paid_runway_create(client, **kwargs):
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_runway_video
        if str(getattr(client, 'base_url', '')).rstrip('/') != 'https://api.dev.runwayml.com':
            raise SpendBlocked('spend_endpoint_not_priced')
        quote = quote_runway_video(kwargs)
        reserve_request('runway', 'text_to_video', kwargs, quote)
        client = client.with_options(max_retries=0)
    return client.text_to_video.create(**kwargs)


def budget_status():
    """Safe operator-facing state; never implies invoices or total cash caps."""
    if not enforcement_enabled():
        return {'enforced': False, 'status': 'not_enabled'}
    try:
        return {'enforced': True, 'status': 'active', **configured_ledger().snapshot()}
    except SpendBlocked as error:
        return {'enforced': True, 'status': 'blocked', 'reason_code': str(error)}
