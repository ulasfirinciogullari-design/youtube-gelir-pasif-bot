"""Account-approved narrator rotation with permanent per-lineage assignments.

The voice extension changes no credit allocation, policy, historical receipt,
subscription or cash control. New voice intents retain the ordinary account
meter and all remaining-credit reservation. Existing saved audio is untouched.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from redis.exceptions import WatchError

from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:{production_spend}:narrator_rotation:v1:'
GRANT_KEY = PREFIX + 'grant'
ANCHOR_FIELD = 'narrator_rotation_grant:v1'
POOLS = {
    'tr': (('teHLF0hAua8Ry47noJ1t', 'Baran'), ('xgYIZvUB5h2eFY3HUFNj', 'Melek'),
           ('HV5rAif1q1ITHdvxqyMw', 'Alp'), ('WtOce4YK0dDSxlVlSdBh', 'Mustafa')),
    'en': (('JBFqnCBsd6RMkjVDRZzb', 'George'), ('Xb7hH8MSUJpSbSDYk0k2', 'Alice'),
           ('onwK4e9ZLuTAKqWW03F9', 'Daniel'), ('hpp4J3VqNfWAUOO0d1Us', 'Bella')),
}
VOICE_IDS = frozenset(v for pool in POOLS.values() for v, _ in pool)
POOL_SHA256 = hashlib.sha256(json.dumps(POOLS, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def route(voice_id):
    if voice_id not in VOICE_IDS:
        raise SpendBlocked('credit_voice_not_approved')
    return 'https://api.elevenlabs.io/v1/text-to-speech/' + voice_id + '/with-timestamps'


def _require(value, code='credit_voice_grant_invalid'):
    if not value:
        raise SpendBlocked(code)


def grant(pipe, policy):
    from app.services.production_credit_ledger import _object, _hash
    from app.services.production_spend import LEDGER_KEY
    raw, anchor = pipe.get(GRANT_KEY), pipe.hget(LEDGER_KEY, ANCHOR_FIELD)
    if raw is None and anchor is None:
        return None
    _require(raw is not None and anchor is not None and pipe.pttl(GRANT_KEY) == -1)
    row = _object(raw)
    _require(set(row) == {'version', 'pool_sha256', 'account_sha256', 'credential_sha256',
        'catalog_sha256', 'observed_at', 'authorized_at', 'source', 'cash_controls_changed'}
        and type(row['version']) is int and row['version'] == 1 and row['pool_sha256'] == POOL_SHA256
        and row['account_sha256'] == policy['account_sha256']
        and row['credential_sha256'] == policy['credential_sha256']
        and row['source'] == 'owner_requested_existing_account_voice_rotation'
        and row['cash_controls_changed'] is False and _hash(row) == anchor
        and re.fullmatch('[0-9a-f]{64}', row['catalog_sha256']) is not None)
    return row


def commission(ledger, catalog, *, observed_at):
    """Operator-only activation after a fresh, filtered authenticated catalog GET."""
    from app.services.production_credit_ledger import _hash, _json
    from app.services.production_spend import LEDGER_KEY
    _require(ledger.foundation is not None)
    now = ledger.clock()
    _require(isinstance(observed_at, datetime) and observed_at.tzinfo is not None
        and 0 <= (now - observed_at).total_seconds() <= 120)
    _require(type(catalog) is dict and catalog.get('has_more') is False
        and type(catalog.get('voices')) is list)
    voices = {row.get('voice_id'): row for row in catalog['voices'] if type(row) is dict}
    _require(VOICE_IDS <= set(voices))
    for voice in VOICE_IDS:
        row = voices[voice]
        _require(row.get('is_legacy') is False
            and {'eleven_multilingual_v2', 'eleven_flash_v2_5'} <= set(row.get('high_quality_base_model_ids') or []))
    with ledger.client.pipeline() as pipe:
        ledger._watch(pipe)
        policy, state, _, _ = ledger._read(pipe, now)
        old = grant(pipe, policy)
        if old:
            ledger._ping(pipe)
            return {'status': 'already_active', 'voices': len(VOICE_IDS)}
        _require(state['reserved_credits'] == 0, 'credit_voice_active_reservation')
        record = {'version': 1, 'pool_sha256': POOL_SHA256,
            'account_sha256': policy['account_sha256'], 'credential_sha256': policy['credential_sha256'],
            'catalog_sha256': _hash(catalog), 'observed_at': observed_at.isoformat(),
            'authorized_at': now.isoformat(), 'source': 'owner_requested_existing_account_voice_rotation',
            'cash_controls_changed': False}
        pipe.multi(); pipe.set(GRANT_KEY, _json(record), nx=True)
        pipe.hset(LEDGER_KEY, ANCHOR_FIELD, _hash(record))
        _require(pipe.execute() == [True, 1], 'credit_voice_grant_commit_uncertain')
    return {'status': 'active', 'voices': len(VOICE_IDS), 'credit_policy_changed': False}


def assigned(language):
    """Trusted current task only; retries/continuations reuse the root choice."""
    if language not in POOLS:
        return None
    from app.services import production_spend_runtime as runtime
    if not runtime.enforcement_enabled() or not runtime._TASK_ID.get():
        return None
    from app.services.production_credit_ledger import CreditLedger, _json, _object
    from app.services.audience_strategy import read_settings
    foundation = runtime.configured_ledger(read_timeout=2)
    context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
    ledger = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
    assigned_key = PREFIX + 'root:' + context['lineage_id']
    cursor_key = PREFIX + 'cursor:' + context['channel_id'] + ':' + language
    settings_key = 'youtube_studio:audience_strategy:v1:' + context['channel_id']
    for _ in range(8):
        try:
            with foundation.client.pipeline() as pipe:
                ledger._watch(pipe); pipe.watch(assigned_key, cursor_key, settings_key)
                policy, state, _, _ = ledger._read(pipe, ledger.clock())
                approved = grant(pipe, policy)
                previous = pipe.get(assigned_key)
                if previous:
                    record = _object(previous)
                    _require(record.get('context') == context and record.get('language') == language
                        and record.get('pool_sha256') == POOL_SHA256
                        and (record.get('voice_id'), record.get('name')) in POOLS[language]
                        and approved is not None and pipe.pttl(assigned_key) == -1)
                    ledger._ping(pipe)
                    return {key: record[key] for key in ('voice_id', 'name')}
                prior_voices = {entry['reservation']['intent']['voice_id'] for entry in state['intents'].values()
                    if entry['reservation']['intent']['root_lineage_id'] == context['lineage_id']}
                if prior_voices:
                    # A root created before rotation keeps its recorded narrator.
                    _require(len(prior_voices) == 1, 'credit_prior_voice_ambiguous')
                    voice = next(iter(prior_voices))
                    names = dict(v for pool in POOLS.values() for v in pool)
                    _require(voice in names, 'credit_prior_voice_unknown')
                    ledger._ping(pipe)
                    return {'voice_id': voice, 'name': names[voice]}
                if approved is None or not read_settings(context['channel_id'], client=pipe)['voice_rotation']:
                    ledger._ping(pipe)
                    return None
                raw = pipe.get(cursor_key)
                count = int(raw) if raw is not None else 0
                _require(count >= 0 and (raw is None or str(count) == raw))
                voice, name = POOLS[language][count % len(POOLS[language])]
                record = {'version': 1, 'context': context, 'language': language, 'voice_id': voice,
                    'name': name, 'pool_sha256': POOL_SHA256, 'sequence': count + 1}
                pipe.multi(); pipe.set(assigned_key, _json(record), nx=True); pipe.set(cursor_key, str(count + 1))
                _require(pipe.execute() == [True, True], 'credit_voice_assignment_uncertain')
                return {'voice_id': voice, 'name': name}
        except WatchError:
            continue
    raise SpendBlocked('credit_voice_assignment_contention')
