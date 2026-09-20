"""Connect paid provider submissions to the durable production spend ledger.

No credentials or request contents are persisted. Deterministic request hashes
are shared by a repair family: a timeout, SDK retry or new queue ID cannot
authorize an identical paid submission again. Unknown prices stay blocked.
The default rollout flag is OFF; this module never initializes live budgets.
"""
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import math
import re

import httpx
import redis

from app.config import settings
from app.services.production_spend import (
    LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy,
)


_TASK_ID = ContextVar('production_spend_task', default=None)
_SCENE = ContextVar('production_spend_scene', default=None)
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


def configured_ledger(*, read_timeout=None):
    try:
        policy = SpendPolicy(**_object(settings.studio_spend_policy_json)).validate()
        options = {'decode_responses': True}
        if read_timeout is not None:
            if (type(read_timeout) not in (int, float) or not math.isfinite(read_timeout)
                    or not 0 < read_timeout <= 5):
                raise SpendBlocked('spend_status_timeout_invalid')
            from redis.backoff import NoBackoff
            from redis.retry import Retry
            options.update(socket_timeout=read_timeout, socket_connect_timeout=read_timeout,
                           retry_on_timeout=False, retry=Retry(NoBackoff(), 0))
        client = redis.Redis.from_url(settings.redis_url, **options)
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
            from app.services.production_series_spend import CONTEXT_PREFIX, read_context
            pipe.watch(CONTEXT_PREFIX + task_id)
            if pipe.exists(CONTEXT_PREFIX + task_id):
                binding = read_context(pipe, task_id)
                if binding.get('version') == 2 and binding.get('funding_mode') == 'existing_subscription_included_router':
                    binding = {key: binding[key] for key in ('channel_id', 'connection_id', 'lineage_id', 'kind')}
                field = 'binding:' + task_id
                prior = pipe.hget(LEDGER_KEY, field)
                if prior is not None and _object(prior) != binding:
                    raise SpendBlocked('spend_lineage_binding_invalid')
                pipe.multi()
                pipe.hset(LEDGER_KEY, field, _json(binding))
                reply = pipe.execute()
                if len(reply) != 1 or type(reply[0]) is not int:
                    raise SpendBlocked('spend_binding_uncertain')
                return binding
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


def _request_fingerprint(context, provider, operation, payload):
    return hashlib.sha256(_json({
        'lineage': context['lineage_id'], 'provider': provider,
        'operation': operation, 'payload': payload,
    }).encode()).hexdigest()


@dataclass(frozen=True)
class _PreparedVideoScenes:
    package_sha256: str
    narration_millis: tuple
    generation_seconds: tuple
    aspect_ratio: str


def prepare_video_scene_budget(package_sha256, scene_durations, aspect_ratio, *, scene_count=None):
    """Freeze measured inputs without pricing, opening a ledger or buying media.

    Retained-only recovery therefore works without a new financial plan or a
    still-current generation price. Only entry into a new generation needs it.
    """
    if not enforcement_enabled():
        return None
    if (type(package_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', package_sha256)
            or type(scene_durations) is not list or not 1 <= len(scene_durations) <= 500
            or aspect_ratio not in ('9:16', '16:9') or type(aspect_ratio) is not str
            or (scene_count is not None and (type(scene_count) is not int
                                            or scene_count != len(scene_durations)))):
        raise SpendBlocked('spend_scene_inputs_invalid')
    if any(type(value) not in (int, float) or not math.isfinite(value)
           or not 0 < value <= 7200 for value in scene_durations):
        raise SpendBlocked('spend_scene_inputs_invalid')
    return _PreparedVideoScenes(
        package_sha256, tuple(math.ceil(value * 1000) for value in scene_durations),
        # Match the actual worker mapping before millisecond rounding; that
        # rounding must not silently add another billable generation second.
        tuple(max(5, min(10, math.ceil(value * 1.02 + .20))) for value in scene_durations),
        aspect_ratio,
    )


def _quoted_video_scenes(prepared):
    from app.services.production_spend_quotes import (
        describe_video_request, quote_http_request, quote_runway_video,
    )
    scenes = []
    ratio = '720:1280' if prepared.aspect_ratio == '9:16' else '1280:720'
    for index, seconds in enumerate(prepared.generation_seconds):
        body = {'model': 'gen4.5', 'prompt_text': 'budgeted scene',
                'ratio': ratio, 'duration': seconds}
        primary = quote_runway_video(body)
        allowed = []
        for model in ('gen4.5', 'seedance2_fast'):
            request = {**body, 'model': model}
            quote = quote_runway_video(request)
            allowed.append(describe_video_request('runway', 'text_to_video', request, quote))
        if seconds <= 8:
            billed_seconds = 4 if seconds <= 4 else 6 if seconds <= 6 else 8
            for model in ('veo-3.1-lite-generate-preview', 'veo-3.1-fast-generate-preview',
                          'veo-3.1-generate-preview'):
                path = '/v1beta/models/' + model + ':predictLongRunning'
                request = {'instances': [{'prompt': 'budgeted scene'}], 'parameters': {
                    'aspectRatio': prepared.aspect_ratio, 'resolution': '720p',
                    'durationSeconds': billed_seconds}}
                provider, operation, quote = quote_http_request(
                    'https://generativelanguage.googleapis.com' + path, {'json': request})
                allowed.append(describe_video_request(provider, operation, request, quote))
        scenes.append({'scene_index': index, 'narration_millis': prepared.narration_millis[index],
                       'generation_seconds': seconds, 'max_request_micro': primary.maximum_micro,
                       'total_micro': primary.maximum_micro * 2, 'allowed_requests': allowed})
    return scenes


@contextmanager
def spending_scene(prepared, scene_index):
    """Scope only new scene generation; all provider fallbacks share its cap."""
    if not enforcement_enabled():
        yield
        return
    if (type(prepared) is not _PreparedVideoScenes or type(scene_index) is not int
            or not 0 <= scene_index < len(prepared.generation_seconds)):
        raise SpendBlocked('spend_scene_context_missing')
    ledger = configured_ledger()
    context = resolve_context(ledger.client, _TASK_ID.get())
    if context.get('purpose') == 'series_preparation':
        from app.services.production_series_spend import validate_request
        validate_request(context, provider, operation, payload, quote, funding)
    ledger.initialize_scene_plan(
        channel_id=context['channel_id'], lineage_id=context['lineage_id'], kind=context['kind'],
        connection_id=context['connection_id'], package_sha256=prepared.package_sha256,
        scenes=_quoted_video_scenes(prepared),
    )
    token = _SCENE.set({**context, 'package_sha256': prepared.package_sha256,
                       'scene_index': scene_index})
    try:
        yield
    finally:
        _SCENE.reset(token)


def _funding_admission(provider, operation, api_key):
    """Bind funding evidence to the credential actually sent, never a UI choice."""
    origins = {'abacus': 'https://routellm.abacus.ai',
               'gemini': 'https://generativelanguage.googleapis.com',
               'openai': 'https://api.openai.com', 'runway': 'https://api.dev.runwayml.com',
               'elevenlabs': 'https://api.elevenlabs.io'}
    if (provider not in origins or type(api_key) is not str or not 1 <= len(api_key) <= 8192
            or any(not 32 < ord(char) < 127 for char in api_key)):
        raise SpendBlocked('spend_funding_credential_invalid')
    path = {'responses': '/v1/responses', 'text_to_video': '/v1/text_to_video'}.get(operation, operation)
    if type(path) is not str or not path.startswith('/'):
        raise SpendBlocked('spend_funding_route_invalid')
    return {'route': origins[provider] + path,
            'credential_sha256': hashlib.sha256((provider + '\0' + api_key).encode()).hexdigest()}


def _header_key(headers, name):
    if type(headers) is not dict:
        raise SpendBlocked('spend_funding_credential_invalid')
    matches = [value for key, value in headers.items() if type(key) is str and key.lower() == name]
    if len(matches) != 1:
        raise SpendBlocked('spend_funding_credential_invalid')
    return matches[0]


def _sdk_funding_headers(client, provider, *, expected_key=None):
    """Snapshot the SDK's effective auth, rejecting unbound billing overrides.

    New OpenAI SDKs add auth separately; Runway also includes it in defaults.
    Merge exactly as the SDK does before checking case-insensitive duplicates.
    Only the real SDK Omit sentinel represents an absent optional header.
    """
    if provider == 'openai':
        from openai import Omit
    else:
        from runwayml import Omit
    try:
        base_url = 'https://api.openai.com/v1' if provider == 'openai' else 'https://api.dev.runwayml.com'
        if str(getattr(client, 'base_url', '')).rstrip('/') != base_url:
            raise SpendBlocked('spend_endpoint_not_priced')
        key = getattr(client, 'api_key', None)
        if (type(key) is not str or not 1 <= len(key) <= 8192
                or any(not 32 < ord(char) < 127 for char in key)):
            raise SpendBlocked('spend_funding_credential_invalid')
        if expected_key is not None and key != expected_key:
            raise SpendBlocked('spend_funding_sdk_identity_changed')
        if type(client.default_query) is not dict or client.default_query:
            raise SpendBlocked('spend_funding_sdk_query_unbound')
        # HTTPX Auth runs after SDK header construction and can replace Bearer
        # auth. These production routes use the standard key-auth transport.
        if client.custom_auth is not None or client._client.auth is not None:
            raise SpendBlocked('spend_funding_sdk_transport_unbound')
        transport = client._client
        if not isinstance(transport.params, Mapping) or transport.params:
            raise SpendBlocked('spend_funding_sdk_query_unbound')
        hooks = transport.event_hooks
        if (type(hooks) is not dict or type(hooks.get('request')) is not list
                or hooks['request']):
            raise SpendBlocked('spend_funding_sdk_transport_unbound')
        if not isinstance(transport.headers, Mapping):
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        lower_headers = (list(transport.headers.multi_items())
                         if hasattr(transport.headers, 'multi_items')
                         else list(transport.headers.items()))
        if any(type(name) is not str or type(value) is not str for name, value in lower_headers):
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        lower_authorization = [value for name, value in lower_headers if name.lower() == 'authorization']
        if lower_authorization and (len(lower_authorization) != 1 or lower_authorization[0] != 'Bearer ' + key):
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        auth, defaults = client.auth_headers, client.default_headers
        if (type(auth) is not dict or type(defaults) is not dict
                or any(type(name) is not str for name in (*auth, *defaults))):
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        headers = {**auth, **defaults}
        if any(type(value) is not str and type(value) is not Omit for value in headers.values()):
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        authorization = [value for name, value in headers.items() if name.lower() == 'authorization']
        if len(authorization) != 1 or authorization[0] != 'Bearer ' + key:
            raise SpendBlocked('spend_funding_sdk_headers_invalid')
        if provider == 'openai':
            if (getattr(client, 'organization', None) is not None
                    or getattr(client, 'project', None) is not None):
                raise SpendBlocked('spend_funding_sdk_account_unbound')
            if any(name.lower() in {'openai-organization', 'openai-project'} for name, _ in lower_headers):
                raise SpendBlocked('spend_funding_sdk_account_unbound')
            for name in ('openai-organization', 'openai-project'):
                values = [value for header, value in headers.items() if header.lower() == name]
                if len(values) > 1 or any(type(value) is not Omit for value in values):
                    raise SpendBlocked('spend_funding_sdk_account_unbound')
        return key, headers
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_funding_sdk_headers_invalid') from None


def _sdk_funding_client(client, provider, base_url):
    """Freeze key/header values in a private clone before reserving any money."""
    key, headers = _sdk_funding_headers(client, provider)
    try:
        cloned = client.with_options(max_retries=0, api_key=key, set_default_headers=dict(headers))
        if str(getattr(cloned, 'base_url', '')).rstrip('/') != base_url:
            raise SpendBlocked('spend_endpoint_not_priced')
        cloned_key, cloned_headers = _sdk_funding_headers(cloned, provider, expected_key=key)
        if cloned_headers != headers:
            raise SpendBlocked('spend_funding_sdk_identity_changed')
        return cloned, cloned_key, headers
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_funding_sdk_headers_invalid') from None


def reserve_request(provider, operation, payload, quote, *, funding):
    from app.services.production_spend_quotes import describe_video_request
    ledger = configured_ledger()
    context = resolve_context(ledger.client, _TASK_ID.get())
    fingerprint = _request_fingerprint(context, provider, operation, payload)
    descriptor = describe_video_request(provider, operation, payload, quote)
    scene = None
    if descriptor is not None:
        scope = _SCENE.get()
        if (type(scope) is not dict or any(scope.get(key) != value for key, value in context.items())):
            raise SpendBlocked('spend_scene_context_missing')
        scene = {key: scope[key] for key in ('connection_id', 'package_sha256', 'scene_index')}
        scene['descriptor'] = descriptor
    receipt = ledger.reserve(
        request_key=fingerprint, channel_id=context['channel_id'],
        lineage_id=context['lineage_id'], kind=context['kind'], quote=quote,
        scene=scene, funding=funding,
    )
    if context.get('purpose') == 'series_preparation':
        # A known profile/connection/context change after reserving stops the
        # POST and retains the upper-bound hold. It never grants a refund/retry.
        if resolve_context(ledger.client, _TASK_ID.get()) != context:
            raise SpendBlocked('spend_series_request_changed')
    return receipt


def record_abacus_usage(payload, usage_record):
    """Attach verified response usage to its existing immutable reservation.

    This is an observation, not settlement or a refund. Reserved counters and
    replay fences remain untouched, including when the returned JSON is bad.
    A lost write reply stops the worker; recording the same receipt again is
    harmless and never grants a second provider submission.
    """
    if not enforcement_enabled():
        raise SpendBlocked('spend_not_enabled')
    fields = {'request_id', 'model', 'input_tokens', 'output_tokens',
              'cache_creation_input_tokens', 'cache_read_input_tokens', 'actual_micro'}
    if (type(payload) is not dict or type(usage_record) is not dict
            or set(usage_record) != fields
            or type(usage_record['request_id']) is not str
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', usage_record['request_id'])
            or type(usage_record['model']) is not str
            or usage_record['model'] != payload.get('model')
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', usage_record['model'])
            or any(type(usage_record[name]) is not int or not 0 <= usage_record[name] <= 10_000_000_000
                   for name in fields - {'request_id', 'model'})):
        raise SpendBlocked('spend_usage_invalid')
    ledger = configured_ledger()
    context = resolve_context(ledger.client, _TASK_ID.get())
    fingerprint = _request_fingerprint(context, 'abacus', '/v1/messages', payload)
    suffix = hashlib.sha256(fingerprint.encode()).hexdigest()
    request_field, usage_field = 'request:' + suffix, 'usage:' + suffix
    record = {'version': 1, 'provider': 'abacus', 'operation': '/v1/messages',
              'accounting': 'observed_list_cost_not_settlement', **usage_record}
    encoded = _json(record)
    for _ in range(12):
        try:
            with ledger.client.pipeline() as pipe:
                pipe.watch(LEDGER_KEY)
                reservation = _object(pipe.hget(LEDGER_KEY, request_field))
                quote = reservation.get('quote')
                if (any(reservation.get(key) != context[key]
                        for key in ('channel_id', 'lineage_id', 'kind'))
                        or type(quote) is not dict or quote.get('provider') != 'abacus'
                        or quote.get('model') != usage_record['model']
                        or type(quote.get('maximum_micro')) is not int
                        or usage_record['actual_micro'] > quote['maximum_micro']):
                    raise SpendBlocked('spend_usage_binding_invalid')
                previous = pipe.hget(LEDGER_KEY, usage_field)
                if previous is not None:
                    if _object(previous) != record:
                        raise SpendBlocked('spend_usage_conflict')
                    # The observation exists; no counters or submission permit.
                    return
                pipe.multi()
                pipe.hset(LEDGER_KEY, usage_field, encoded)
                acknowledged = pipe.execute()
                if len(acknowledged) != 1 or type(acknowledged[0]) is not int or acknowledged[0] != 1:
                    raise SpendBlocked('spend_usage_write_uncertain')
                return
        except redis.exceptions.WatchError:
            continue
        except SpendBlocked:
            raise
        except Exception:
            raise SpendBlocked('spend_usage_unavailable') from None
    raise SpendBlocked('spend_store_contention')


def _copy_native_json(value, depth=0):
    if depth > 64:
        raise SpendBlocked('spend_request_not_priced')
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise SpendBlocked('spend_request_not_priced')
        return {key: _copy_native_json(item, depth + 1) for key, item in value.items()}
    if type(value) is list:
        return [_copy_native_json(item, depth + 1) for item in value]
    if value is None or type(value) in (str, int, bool) or type(value) is float and math.isfinite(value):
        return value
    raise SpendBlocked('spend_request_not_priced')


def _freeze_native_request(kwargs):
    if set(kwargs) - {'json', 'headers', 'params', 'timeout'}:
        raise SpendBlocked('spend_request_not_priced')
    frozen = dict(kwargs)
    try:
        for key in ('json', 'headers', 'params'):
            if key in frozen:
                frozen[key] = _copy_native_json(frozen[key])
        if len(_json({key: frozen[key] for key in ('json', 'headers', 'params') if key in frozen}).encode()) > 16 * 1024 * 1024:
            raise SpendBlocked('spend_request_not_priced')
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_request_not_priced') from None
    return frozen


def _native_sender_identity(sender, headers, provider):
    """Check trusted HTTPX dispatch configuration; never invoke a transport."""
    names = {'abacus': {'x-api-key', 'content-type', 'anthropic-version', 'accept'},
             'gemini': {'x-goog-api-key', 'content-type', 'accept'},
             'elevenlabs': {'xi-api-key', 'content-type', 'accept'}}
    try:
        if (not callable(sender) or type(headers) is not dict
                or any(type(key) is not str or type(value) is not str for key, value in headers.items())
                or len({key.lower() for key in headers}) != len(headers)
                or not {key.lower() for key in headers} <= names[provider]):
            raise SpendBlocked('spend_funding_native_headers_invalid')
        owner = getattr(sender, '__self__', None)
        if owner is None:
            return  # Plain application functions use their reviewed transport.
        if (not isinstance(owner, httpx.Client) or owner.auth is not None
                or owner.params or owner.cookies or owner.follow_redirects is not False
                or type(owner.event_hooks) is not dict
                or any(owner.event_hooks.get(kind) for kind in ('request', 'response'))):
            raise SpendBlocked('spend_funding_native_transport_unbound')
        forbidden = {'authorization', 'proxy-authorization', 'x-goog-user-project',
                     'openai-organization', 'openai-project', 'host'}
        if any(key.lower() in forbidden for key in owner.headers):
            raise SpendBlocked('spend_funding_native_transport_unbound')
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('spend_funding_native_transport_unbound') from None


def paid_post(sender, url, **kwargs):
    """Guard a paid HTTP POST before its transport sees credentials or media."""
    if getattr(settings, 'studio_elevenlabs_native_credits', False) is True:
        from app.services.production_credit_funding import ROUTE
        if url == ROUTE:
            from app.services.production_credit_runtime import paid_credit_post
            return paid_credit_post(sender, url, kwargs)
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_http_request
        kwargs = _freeze_native_request(kwargs)
        provider, operation, quote = quote_http_request(url, kwargs)
        # Unsupported multipart/media payloads are rejected by the quote layer;
        # nothing reads upload streams or persists narration/image contents.
        key_header = {'abacus': 'x-api-key', 'gemini': 'x-goog-api-key', 'elevenlabs': 'xi-api-key'}[provider]
        funding = _funding_admission(provider, operation, _header_key(kwargs.get('headers'), key_header))
        payload = kwargs.get('json')
        if provider == 'elevenlabs':
            from datetime import datetime, timezone
            from app.services.elevenlabs_spend_quotes import elevenlabs_price_revision
            from app.services.production_spend_quotes import configured_elevenlabs_pricing
            now = datetime.now(timezone.utc)
            evidence = configured_elevenlabs_pricing(now=now)
            if elevenlabs_price_revision(evidence, now=now) != quote.price_revision:
                raise SpendBlocked('spend_funding_evidence_changed')
            funding['account_sha256'] = evidence['account_sha256']
            # ElevenLabs has one explicitly priced output-format parameter.
            # Include it in the replay identity; existing provider keys stay unchanged.
            payload = {'json': payload, 'params': kwargs.get('params')}
        _native_sender_identity(sender, kwargs.get('headers'), provider)
        reserve_request(provider, operation, payload, quote, funding=funding)
        _native_sender_identity(sender, kwargs.get('headers'), provider)
    return sender(url, **kwargs)


def paid_response(client, **kwargs):
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_openai_response
        if str(getattr(client, 'base_url', '')).rstrip('/') != 'https://api.openai.com/v1':
            raise SpendBlocked('spend_endpoint_not_priced')
        kwargs = _freeze_native_request({'json': kwargs})['json']
        kwargs.setdefault('max_output_tokens', 8192)
        kwargs.setdefault('store', False)
        kwargs.setdefault('service_tier', 'default')
        quote = quote_openai_response(kwargs)
        client, key, headers = _sdk_funding_client(client, 'openai', 'https://api.openai.com/v1')
        funding = _funding_admission('openai', 'responses', key)
        reserve_request('openai', 'responses', kwargs, quote, funding=funding)
        # Standard lower-HTTP configuration is shared by SDK copies. A known
        # drift after reservation stops this POST and keeps the conservative
        # hold; trusted callers must not mutate it concurrently during send.
        if _sdk_funding_headers(client, 'openai', expected_key=key)[1] != headers:
            raise SpendBlocked('spend_funding_sdk_identity_changed')
    return client.responses.create(**kwargs)


def paid_runway_create(client, **kwargs):
    if enforcement_enabled():
        from app.services.production_spend_quotes import quote_runway_video
        if str(getattr(client, 'base_url', '')).rstrip('/') != 'https://api.dev.runwayml.com':
            raise SpendBlocked('spend_endpoint_not_priced')
        quote = quote_runway_video(kwargs)
        client, key, headers = _sdk_funding_client(client, 'runway', 'https://api.dev.runwayml.com')
        funding = _funding_admission('runway', 'text_to_video', key)
        reserve_request('runway', 'text_to_video', kwargs, quote, funding=funding)
        if _sdk_funding_headers(client, 'runway', expected_key=key)[1] != headers:
            raise SpendBlocked('spend_funding_sdk_identity_changed')
    return client.text_to_video.create(**kwargs)


def budget_status(*, read_timeout=None):
    """Safe operator-facing state; never implies invoices or total cash caps."""
    if not enforcement_enabled():
        return {'enforced': False, 'status': 'not_enabled'}
    try:
        ledger = configured_ledger() if read_timeout is None else configured_ledger(read_timeout=read_timeout)
        return {'enforced': True, 'status': 'active', **ledger.snapshot(),
                'funding': ledger.funding_snapshot()}
    except SpendBlocked as error:
        return {'enforced': True, 'status': 'blocked', 'reason_code': str(error)}


def preflight_scheduled_production(channel_id, *, kind):
    """Reject an unfunded queue admission without touching a topic or ledger."""
    if not enforcement_enabled():
        raise SpendBlocked('spend_enforcement_not_enabled')
    if getattr(settings, 'studio_abacus_included_production', False) is True:
        from app.services.production_included_router import preflight_production
        return preflight_production(channel_id, kind=kind)
    configured_ledger(read_timeout=2).check_dispatch_capacity(channel_id=channel_id, kind=kind)
