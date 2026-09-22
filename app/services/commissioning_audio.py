"""Explicit setup allowance for Short ASR, separate from historical cash.

The owner prioritized commissioning on 2026-09-21 and will choose the operating
budget afterward. A private operator must install the bounded, expiring grant;
ordinary production never initializes it. Reservations include unknown results,
and retries reuse observed responses instead of submitting the audio again.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
from redis.exceptions import WatchError

from app.services import production_spend_runtime as runtime, whisper_transcription as whisper
from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:commissioning:v1:whisper:'
POLICY_KEY = PREFIX + 'policy'
JOURNAL_KEY = PREFIX + 'journal'
COST_MICRO = 6000


def _require(value, code='commissioning_audio_invalid'):
    if not value:
        raise SpendBlocked(code)


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _validate(policy, now):
    _require(type(policy) is dict and set(policy) == {'version', 'purpose', 'credential_sha256',
        'channels', 'valid_from', 'valid_until', 'max_requests', 'max_per_day', 'max_per_lineage',
        'owner_authorization_sha256', 'historical_cash_micro'})
    _require(policy['version'] == 1 and type(policy['version']) is int
        and policy['purpose'] == 'owner_authorized_commissioning_whisper'
        and policy['historical_cash_micro'] is None)
    for field in ('credential_sha256', 'owner_authorization_sha256'):
        _require(type(policy[field]) is str and len(policy[field]) == 64
            and all(c in '0123456789abcdef' for c in policy[field]))
    channels = policy['channels']
    _require(type(channels) is dict and 1 <= len(channels) <= 8)
    for channel, connection in channels.items():
        _require(runtime._CHANNEL_ID.fullmatch(channel) and type(connection) is str
            and 8 <= len(connection) <= 128)
    for field, cap in (('max_requests', 100), ('max_per_day', 24), ('max_per_lineage', 6)):
        _require(type(policy[field]) is int and 1 <= policy[field] <= cap)
    start = datetime.fromisoformat(policy['valid_from'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(policy['valid_until'].replace('Z', '+00:00'))
    _require(start <= now < end <= start + timedelta(days=7), 'commissioning_audio_expired')
    return policy


def commission(client, policy):
    """Private operator only; never replace an existing grant or its history."""
    _validate(policy, _now())
    encoded = _raw(policy)
    with client.pipeline() as pipe:
        pipe.watch(POLICY_KEY, JOURNAL_KEY)
        _require(not pipe.exists(POLICY_KEY, JOURNAL_KEY), 'commissioning_audio_already_configured')
        pipe.multi()
        pipe.set(POLICY_KEY, encoded, nx=True)
        pipe.set(JOURNAL_KEY, _raw({'policy_sha256': _hash(encoded), 'requests': {}}), nx=True)
        _require(pipe.execute() == [True, True], 'commissioning_audio_setup_uncertain')


def _read(pipe):
    pipe.watch(POLICY_KEY, JOURNAL_KEY)
    _require(pipe.pttl(POLICY_KEY) == -1 and pipe.pttl(JOURNAL_KEY) == -1,
        'commissioning_audio_records_missing')
    raw_policy = pipe.get(POLICY_KEY)
    policy = _validate(json.loads(raw_policy), _now())
    journal = json.loads(pipe.get(JOURNAL_KEY))
    _require(type(journal) is dict and set(journal) == {'policy_sha256', 'requests'}
        and journal['policy_sha256'] == _hash(raw_policy) and type(journal['requests']) is dict
        and len(journal['requests']) <= policy['max_requests'])
    return policy, journal


def _check_context(pipe, policy, context, credential):
    _require(context['kind'] == 'shorts'
        and policy['channels'].get(context['channel_id']) == context['connection_id']
        and policy['credential_sha256'] == credential, 'commissioning_audio_binding_changed')
    key = runtime._CHANNEL_PREFIX + context['channel_id']
    pipe.watch(key, runtime._CHANNEL_INDEX)
    channel = json.loads(pipe.get(key))
    _require(channel.get('id') == context['channel_id']
        and channel.get('connection_id') == context['connection_id']
        and channel.get('requires_reconnect') is not True
        and pipe.sismember(runtime._CHANNEL_INDEX, context['channel_id']),
        'commissioning_audio_binding_changed')


def _reserve(client, context, descriptor, credential):
    identity = _hash(_raw({'lineage': context['lineage_id'], 'request': descriptor}))
    for attempt in range(8):
        try:
            with client.pipeline() as pipe:
                policy, journal = _read(pipe)
                _check_context(pipe, policy, context, credential)
                previous = journal['requests'].get(identity)
                if previous is not None:
                    _require(previous['context'] == context and previous.get('outcome') is not None,
                        'commissioning_audio_previous_outcome_unknown')
                    pipe.multi(); pipe.ping()
                    _require(pipe.execute() == [True])
                    return identity, previous['outcome']
                now = _now().isoformat()
                rows = list(journal['requests'].values())
                _require(len(rows) < policy['max_requests'], 'commissioning_audio_setup_limit')
                _require(sum(row['reserved_at'][:10] == now[:10] for row in rows) < policy['max_per_day'],
                    'commissioning_audio_daily_limit')
                _require(sum(row['context']['lineage_id'] == context['lineage_id'] for row in rows)
                    < policy['max_per_lineage'], 'commissioning_audio_episode_limit')
                journal['requests'][identity] = {'context': context, 'request': descriptor,
                    'reserved_at': now, 'max_list_cost_micro_usd': COST_MICRO, 'outcome': None}
                pipe.multi(); pipe.set(JOURNAL_KEY, _raw(journal))
                _require(pipe.execute() == [True], 'commissioning_audio_reservation_uncertain')
                return identity, None
        except WatchError as error:
            if (type(error) is not WatchError or str(error) != 'Watched variable changed.'
                    or error.__cause__ is not None or error.__context__ is not None or attempt == 7):
                raise SpendBlocked('commissioning_audio_reservation_uncertain') from None


def _response(outcome):
    from app.services.youtube_auth import _decrypt_json
    raw = _decrypt_json(outcome['encrypted_response'])['response'].encode()
    _require(hashlib.sha256(raw).hexdigest() == outcome['response_sha256'])
    whisper._json_payload(raw)
    return httpx.Response(200, content=raw, headers={'Content-Type': 'application/json'})


def _probe_response(client, context, descriptor, credential):
    """Reuse the already paid, immutable setup probe without another POST."""
    key = ('youtube_studio:commissioning:v1:whisper_probe:' + descriptor['audio']['sha256']
        + ':' + descriptor['fields']['language'])
    raw = client.get(key)
    if raw is None:
        return None, None
    value = json.loads(raw)
    _require(value.get('state') == 'observed' and value.get('source_task_id') == context['lineage_id']
        and value.get('credential_sha256') == credential and value.get('model') == 'whisper-1'
        and value.get('endpoint') == whisper.WHISPER_ROUTE
        and value.get('request_sha256') == _hash(json.dumps(descriptor, sort_keys=True))
        and value.get('max_list_cost_micro_usd') == COST_MICRO
        and client.pttl(key) == -1, 'commissioning_audio_probe_unverified')
    return _response(value), key


def transcribe_if_commissioned(path, *, api_key, language):
    """Return None only when no setup grant exists; failures never fall back."""
    _require(runtime.enforcement_enabled(), 'spend_enforcement_required')
    foundation = runtime.configured_ledger(read_timeout=2)
    client = foundation.client
    if client.get(POLICY_KEY) is None:
        _require(not client.exists(JOURNAL_KEY), 'commissioning_audio_records_missing')
        return None
    _require(type(api_key) is str and 1 <= len(api_key) <= 4096 and api_key.isascii()
        and not any(c.isspace() for c in api_key), 'commissioning_audio_credential_missing')
    fields = {**whisper._FIELDS, 'language': language}
    quote = whisper._quote(fields)
    _require(quote.maximum_micro == COST_MICRO)
    raw, suffix = whisper._read_audio(path)
    snapshot = whisper._snapshot_audio(raw, suffix, allow_natural_short=True)
    descriptor = snapshot.descriptor(fields)
    context = runtime.resolve_context(client, runtime._TASK_ID.get())
    credential = _hash('openai\0' + api_key)
    identity, outcome = _reserve(client, context, descriptor, credential)
    if outcome is not None:
        return _response(outcome)
    try:
        response, source_receipt = _probe_response(client, context, descriptor, credential)
        if response is None:
            response = whisper._post_bounded(snapshot, fields,
                {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json'})
        from app.services.youtube_auth import _encrypt_json
        _require(api_key.encode() not in response.content)
        observed = {'response_sha256': hashlib.sha256(response.content).hexdigest(),
            'encrypted_response': _encrypt_json({'response': response.content.decode()}),
            'observed_at': _now().isoformat(), 'source_receipt': source_receipt}
        with client.pipeline() as pipe:
            policy, journal = _read(pipe)
            _check_context(pipe, policy, context, credential)
            row = journal['requests'].get(identity)
            _require(row is not None and row['context'] == context
                and row['request'] == descriptor and row['outcome'] is None)
            row['outcome'] = observed
            pipe.multi(); pipe.set(JOURNAL_KEY, _raw(journal))
            _require(pipe.execute() == [True], 'commissioning_audio_observation_uncertain')
        return _response(observed)
    except Exception:
        # Reservation remains occupied, including lost response/commit ACK.
        raise whisper.WhisperTranscriptionError('commissioning_audio_outcome_unverified') from None


def status(client):
    """Read-only setup list-cost ceiling; not an invoice or monthly budget."""
    if client.get(POLICY_KEY) is None:
        return None
    with client.pipeline() as pipe:
        policy, journal = _read(pipe)
        rows = list(journal['requests'].values())
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    return {'mode': 'commissioning', 'requests': len(rows),
        'unknown_requests': sum(row['outcome'] is None for row in rows),
        'reserved_list_cost_micro_usd': len(rows) * COST_MICRO,
        'maximum_list_cost_micro_usd': policy['max_requests'] * COST_MICRO,
        'valid_until': policy['valid_until'], 'historical_cash_micro': None}
