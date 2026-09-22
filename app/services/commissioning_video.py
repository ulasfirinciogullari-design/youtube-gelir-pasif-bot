"""Commission missing Short footage through one durable Gemini Veo request.

The owner deferred the final operating budget and authorized setup production.
This route requires both the explicit server switch and active owner authority.
It leaves all earlier cash/native-credit records untouched. Every submission,
including unknown results, is retained; accepted operations are only polled.
Existing per-episode create capacity and all independent media QA still apply.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services import production_spend_runtime as runtime, production_continuation as continuation
from app.services.included_stock_pool import _local_transaction
from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:commissioning:v1:video:'
MODEL = 'veo-3.1-lite-generate-preview'
BASE = 'https://generativelanguage.googleapis.com/v1beta'
ROUTE = BASE + '/models/' + MODEL + ':predictLongRunning'
_SCENE = ContextVar('commissioning_video_scene', default=None)
_MAX_RESPONSE = 2 * 1024 * 1024


class CommissionedVideoUnavailable(RuntimeError):
    """A durably observed completed no-clip result; the paid request stays used.

    The ordinary scene handler may review stock alternatives. This is never a
    retry permit, an unknown-outcome classification or approval of any footage.
    """


def _require(value, code='commissioning_video_unverified'):
    if not value:
        raise SpendBlocked(code)


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _authority(pipe, foundation, context):
    from app.services import production_cash_disabled as cash
    # Once normal cash funding replaces the setup foundation, this route must
    # not bypass the owner's subsequently selected operating budget.
    pipe.watch(runtime.LEDGER_KEY, cash.ANCHOR_KEY)
    if not cash.present(pipe):
        return None
    cash.read(pipe, foundation, now=foundation.clock())
    if context.get('kind') == 'long':
        from app.services.commissioning_longform import authorize
        authorize(pipe, context)
    _require(context.get('kind') in {'shorts', 'long'} and 'purpose' not in context)
    proof = continuation.authority(pipe, context['channel_id'])
    if proof is None:
        return None
    key = runtime._CHANNEL_PREFIX + context['channel_id']
    pipe.watch(key, runtime._CHANNEL_INDEX)
    channel = json.loads(pipe.get(key))
    _require(channel.get('id') == context['channel_id']
        and channel.get('connection_id') == context['connection_id']
        and channel.get('requires_reconnect') is not True
        and pipe.sismember(runtime._CHANNEL_INDEX, context['channel_id']))
    return proof


@_local_transaction
def enabled_for_task():
    from app.services.production_included_router import enabled
    if not (getattr(settings, 'studio_commissioning_video_generation', False) is True
            and enabled() and runtime.enforcement_enabled()):
        return False
    foundation = runtime.configured_ledger(read_timeout=2)
    context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
    if context.get('kind') not in {'shorts', 'long'} or 'purpose' in context:
        return False
    with foundation.client.pipeline() as pipe:
        proof = _authority(pipe, foundation, context)
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    return proof is not None


def completion_capacity(options, duration_minutes, scene_count, paid_cap, paid_used):
    """Permit missing footage, within the frozen episode capacity, during setup.

    The normal real-first mix is a preference, not a reason to abandon several
    rejected scenes after producing only one. Authority is checked afresh;
    reservations and every actual clip's quality checks remain mandatory.
    """
    if options.get('content_plan_item_id') and options.get('format') == 'landscape' and duration_minutes == 3:
        from app.services.commissioning_longform import active, MAX_SCENES
        if active() and enabled_for_task():
            _require(type(scene_count) is int and 1 <= scene_count <= MAX_SCENES
                and paid_cap == MAX_SCENES and type(paid_used) is int and 0 <= paid_used <= paid_cap)
            return min(scene_count, paid_cap - paid_used)
    if not (options.get('mode') == 'production' and options.get('format') == 'shorts'
            and duration_minutes == 0.5 and type(scene_count) is int and 1 <= scene_count <= 6
            and type(paid_cap) is int and 2 <= paid_cap <= 6
            and type(paid_used) is int and 0 <= paid_used <= paid_cap):
        return None
    if not enabled_for_task():
        return None
    return min(scene_count, paid_cap - paid_used)


def completion_repairs(options, duration_minutes, scenes, rejected_indices, reviews,
                       paid_cap, paid_used):
    capacity = completion_capacity(options, duration_minutes, len(scenes), paid_cap, paid_used)
    if capacity is None:
        return None
    threshold = options.get('quality_threshold', 86)
    _require(type(threshold) is int and 0 <= threshold <= 100)
    eligible = {index for index in rejected_indices
        if type(index) is int and 0 <= index < len(scenes)
        and type(reviews.get(index)) is dict and type(reviews[index].get('score')) is int
        and 0 <= reviews[index]['score'] < threshold}
    return sorted(eligible, key=lambda index: (reviews[index]['score'], index))[:capacity]


@contextmanager
def scene_scope(prepared, scene_index, context, foundation):
    _require(type(prepared) is runtime._PreparedVideoScenes
        and type(scene_index) is int and 0 <= scene_index < len(prepared.generation_seconds)
        and 1 <= len(prepared.generation_seconds) <= (32 if context.get('kind') == 'long' else 6))
    if context.get('kind') == 'long':
        with foundation.client.pipeline() as pipe:
            _require(_authority(pipe, foundation, context) is not None)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    token = _SCENE.set({'context': context, 'foundation': foundation,
        'package_sha256': prepared.package_sha256, 'scene_index': scene_index,
        'narration_millis': prepared.narration_millis[scene_index],
        'generation_seconds': prepared.generation_seconds[scene_index], 'aspect_ratio': prepared.aspect_ratio})
    try:
        yield
    finally:
        _SCENE.reset(token)


def _journal(pipe, key, context):
    pipe.watch(key)
    raw = pipe.get(key)
    if raw is None:
        return {'version': 1, 'context': context, 'requests': {}}
    _require(pipe.pttl(key) == -1 and len(raw) <= 32 * 1024 * 1024)
    row = json.loads(raw)
    _require(type(row) is dict and set(row) == {'version', 'context', 'requests'}
        and row['version'] == 1 and row['context'] == context
        and type(row['requests']) is dict and len(row['requests']) <= (32 if context.get('kind') == 'long' else 6))
    return row


@_local_transaction
def _reserve(scope, descriptor):
    foundation, context = scope['foundation'], scope['context']
    key = PREFIX + context['lineage_id']
    identity = _sha(_raw(descriptor).encode())
    with foundation.client.pipeline() as pipe:
        authority = _authority(pipe, foundation, context)
        _require(authority is not None
            and getattr(settings, 'studio_commissioning_video_generation', False) is True)
        journal = _journal(pipe, key, context)
        prior = journal['requests'].get(identity)
        if prior is not None:
            _require(prior['request'] == descriptor and prior['authority_sha256'] == authority)
            _require(prior['create'] is not None, 'commissioning_video_previous_outcome_unknown')
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return key, identity, prior
        cap = 32 if context.get('kind') == 'long' else getattr(settings, 'studio_production_short_paid_create_cap', 2)
        _require(type(cap) is int and 2 <= cap <= (32 if context.get('kind') == 'long' else 6) and len(journal['requests']) < cap,
                 'commissioning_video_episode_capacity')
        journal['requests'][identity] = {'request': descriptor, 'authority_sha256': authority,
            'reserved_at': datetime.now(timezone.utc).isoformat(), 'create': None, 'result': None}
        pipe.multi(); pipe.set(key, _raw(journal)); _require(pipe.execute() == [True])
    return key, identity, None


@_local_transaction
def _observe(scope, key, identity, field, response, status, secret):
    from app.services.youtube_auth import _encrypt_json
    _require(field in {'create', 'result'} and 0 < len(response) <= _MAX_RESPONSE
        and secret.encode() not in response)
    with scope['foundation'].client.pipeline() as pipe:
        journal = _journal(pipe, key, scope['context'])
        row = journal['requests'][identity]
        if row[field] is not None:
            _require(row[field]['response_sha256'] == _sha(response)
                and row[field]['http_status'] == status)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return row[field]
        observed = {'http_status': status, 'response_sha256': _sha(response),
            'encrypted_response': _encrypt_json({'response': response.decode()}),
            'observed_at': datetime.now(timezone.utc).isoformat()}
        row[field] = observed
        pipe.multi(); pipe.set(key, _raw(journal)); _require(pipe.execute() == [True])
        return observed


def _payload(observed):
    from app.services.youtube_auth import _decrypt_json
    raw = _decrypt_json(observed['encrypted_response'])['response'].encode()
    _require(_sha(raw) == observed['response_sha256'])
    _require(observed['http_status'] == 200, 'commissioning_video_provider_rejected')
    value = json.loads(raw)
    _require(type(value) is dict)
    return value


def quota_rejected(observed):
    """A captured HTTP rejection with no operation; never an unknown POST."""
    if not observed or observed.get('http_status') != 429:
        return False
    from app.services.youtube_auth import _decrypt_json
    raw = _decrypt_json(observed['encrypted_response'])['response'].encode()
    _require(_sha(raw) == observed['response_sha256'])
    value = json.loads(raw)
    error = value.get('error') if type(value) is dict else None
    return bool(set(value) == {'error'} and type(error) is dict
        and type(error.get('code')) is int and error['code'] == 429
        and error.get('status') == 'RESOURCE_EXHAUSTED')


def _quota_cooldown(scope, descriptor):
    """Reuse exact receipts; spare other scenes repeated requests for one hour."""
    client = scope['foundation'].client
    current = json.loads(client.get(PREFIX + scope['context']['lineage_id']) or '{}')
    if _sha(_raw(descriptor).encode()) in current.get('requests', {}):
        return False  # Accepted and ambiguous requests keep their original path.
    now = datetime.now(timezone.utc)
    keys = list(client.scan_iter(match=PREFIX + '*', count=128))
    _require(len(keys) <= 2000)
    for key in keys:
        journal = json.loads(client.get(key))
        for row in journal.get('requests', {}).values():
            request, observed = row.get('request') or {}, row.get('create')
            if (request.get('credential_sha256') != descriptor['credential_sha256']
                    or request.get('model') != MODEL or row.get('result') is not None
                    or not observed or observed.get('http_status') != 429):
                continue
            date = datetime.fromisoformat(observed['observed_at'])
            if date.tzinfo is not None and 0 <= (now - date).total_seconds() <= 3600 and quota_rejected(observed):
                return True
    return False


def _read_response(response):
    raw = bytearray()
    for chunk in response.iter_bytes():
        raw.extend(chunk)
        _require(len(raw) <= _MAX_RESPONSE)
    _require(raw)
    return bytes(raw)


def _result(payload):
    from app.services.runway import _GEMINI_VIDEO_HOSTS
    error = payload.get('error')
    if (payload.get('done') is True and type(error) is dict
            and type(error.get('code')) is int and error['code'] in {13, 14}
            and not payload.get('response')):
        # The actual provider completed with INTERNAL or UNAVAILABLE.
        # There is no returned clip to approve and no uncertain POST to replay.
        # Let the existing final stock rescue and full QA run for this scene.
        raise CommissionedVideoUnavailable('commissioned_video_completed_server_failure')
    _require(payload.get('done') is True and not payload.get('error'),
             'commissioning_video_generation_failed')
    generated = payload.get('response', {}).get('generateVideoResponse', {})
    samples = generated.get('generatedSamples', [])
    reasons = generated.get('raiMediaFilteredReasons')
    if (type(samples) is list and not samples
            and type(generated.get('raiMediaFilteredCount')) is int
            and generated['raiMediaFilteredCount'] == 1
            and type(reasons) is list and 1 <= len(reasons) <= 16
            and all(type(reason) is str and 0 < len(reason.strip()) <= 4096 for reason in reasons)):
        # A provider filter is a completed refusal, not an unknown purchase.
        # Keep its receipt and do not retry or rewrite the rejected request.
        # Only the existing stock rescue can supply independently reviewed
        # footage; this result never approves the missing generated scene.
        raise CommissionedVideoUnavailable('commissioned_video_completed_filtered')
    _require(not generated.get('raiMediaFilteredCount') and not reasons)
    _require(type(samples) is list and len(samples) == 1)
    uri = samples[0].get('video', {}).get('uri')
    _require(type(uri) is str and 0 < len(uri) <= 8192)
    parsed = urlparse(uri)
    _require(parsed.scheme == 'https' and parsed.hostname in _GEMINI_VIDEO_HOSTS
        and not parsed.username and not parsed.password and parsed.port in (None, 443))
    return {'url': uri, 'provider': 'gemini_veo', 'provider_attempts': 1,
            'commissioning_model': MODEL}


def generate_if_commissioned(prompt, seconds, aspect_ratio):
    """Return None outside a commissioned scene; never fall back after sending."""
    scope = _SCENE.get()
    if scope is None:
        return None
    _require(enabled_for_task() and type(prompt) is str and bool(prompt.strip())
        and len(prompt.encode('utf-16-le')) // 2 <= 1000
        and type(seconds) is int and 2 <= seconds <= 8
        and seconds == scope['generation_seconds'] and aspect_ratio == scope['aspect_ratio'])
    key = str(getattr(settings, 'gemini_api_key', '') or '')
    _require(1 <= len(key) <= 4096 and all(32 < ord(c) < 127 for c in key))
    duration = 4 if seconds <= 4 else 6 if seconds <= 6 else 8
    body = {'instances': [{'prompt': prompt}], 'parameters': {
        'aspectRatio': aspect_ratio, 'resolution': '720p', 'durationSeconds': duration}}
    from app.services.production_spend_quotes import quote_http_request
    provider, operation, quote = quote_http_request(ROUTE, {'json': body})
    _require(provider == 'gemini' and quote.model == MODEL)
    descriptor = {name: scope[name] for name in ('package_sha256', 'scene_index', 'narration_millis',
                                                'generation_seconds', 'aspect_ratio')}
    descriptor.update({'route': ROUTE, 'request_sha256': _sha(_raw(body).encode()),
        'credential_sha256': _sha(('gemini\0' + key).encode()), 'model': MODEL,
        'duration_seconds': duration, 'max_list_cost_micro_usd': quote.maximum_micro})
    from app.services.content_plan_retained_completion import stock_only_scope
    if stock_only_scope(scope):
        raise CommissionedVideoUnavailable('commissioned_video_quota_stock_rescue')
    from app.services.content_plan_long_media_resume import continuation_identity
    from app.services.content_plan_retained_completion import continuation_identity as completion_identity
    continuation_task = completion_identity(scope) or continuation_identity(scope)
    if continuation_task is not None:
        # This one private child follows a fully captured terminal outage.
        # Its new attempts remain in the original root's cumulative journal.
        descriptor['continuation_task_id'] = continuation_task
    if _quota_cooldown(scope, descriptor):
        raise CommissionedVideoUnavailable('commissioned_video_quota_stock_rescue')
    receipt_key, identity, prior = _reserve(scope, descriptor)
    headers = {'x-goog-api-key': key, 'Content-Type': 'application/json'}
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10), trust_env=False, follow_redirects=False) as client:
            if prior is None:
                with client.stream('POST', ROUTE, headers=headers, json=body) as response:
                    observed = _observe(scope, receipt_key, identity, 'create',
                        _read_response(response), response.status_code, key)
            else:
                observed = prior['create']
                if prior['result'] is not None:
                    return _result(_payload(prior['result']))
            if quota_rejected(observed):
                raise CommissionedVideoUnavailable('commissioned_video_quota_rejected')
            created = _payload(observed)
            name = created.get('name')
            _require(type(name) is str and re.fullmatch(
                r'models/' + re.escape(MODEL) + r'/operations/[A-Za-z0-9_-]{1,256}', name))
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                # After acceptance only safe GETs are repeatable. A temporary
                # polling error leaves the durable operation available to resume.
                with client.stream('GET', BASE + '/' + name, headers=headers) as response:
                    raw = _read_response(response)
                    _require(response.status_code == 200, 'commissioning_video_poll_unavailable')
                payload = json.loads(raw)
                _require(type(payload) is dict)
                if payload.get('done') is True:
                    final = _observe(scope, receipt_key, identity, 'result', raw, 200, key)
                    return _result(_payload(final))
                time.sleep(10)
            raise SpendBlocked('commissioning_video_poll_timeout')
    except (SpendBlocked, CommissionedVideoUnavailable):
        raise
    except Exception:
        raise SpendBlocked('commissioning_video_outcome_unverified') from None
