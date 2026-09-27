"""Prepaid Kie credit reservations and encrypted one-shot queue receipts.

An operator allocates only a freshly observed existing balance. Saving a key
does not allocate funds. Unknown requests keep their full ceiling; settled
requests use the provider's observed charge, when present. No top-ups,
USD guesses, implicit refunds, replacement grants or native ElevenLabs edits.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

import httpx

from app.services import kie_credentials as credentials, kie_voice_adapter as api
from app.services.included_stock_pool import _local_transaction
from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:{production_spend}:kie_voice:v1:'
POLICY_KEY, JOURNAL_KEY, ANCHOR_KEY = (PREFIX + s for s in ('policy', 'journal', 'anchor'))
ACTIVE_KEY = PREFIX + 'activation'
CHANNELS = frozenset({'UC5v9AvNtD3PTLgo6m1jROOA', 'UCgvESYtYbn2w9R2ExBOF_cw'})
MAX_REQUESTS = 10000


def require(value, code='kie_voice_accounting_unverified'):
    if not value:
        raise SpendBlocked(code)


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def now():
    return datetime.now(timezone.utc)


def _stamp(value):
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(instant.tzinfo is not None and instant <= now())
    return instant


def _seal(response, secret):
    from app.services.youtube_auth import _encrypt_json
    content = response.content
    require(0 < len(content) <= api.MAX_RESPONSE and secret.encode() not in content)
    return {'http_status': response.status_code, 'response_sha256': sha(content),
        'encrypted_response': _encrypt_json({'response': content.decode()}),
        'observed_at': now().isoformat()}


def restore(record):
    from app.services.youtube_auth import _decrypt_json
    require(type(record) is dict and set(record) == {
        'http_status', 'response_sha256', 'encrypted_response', 'observed_at'})
    require(type(record['http_status']) is int and 100 <= record['http_status'] <= 599)
    _stamp(record['observed_at'])
    content = _decrypt_json(record['encrypted_response'])['response'].encode()
    require(0 < len(content) <= api.MAX_RESPONSE and sha(content) == record['response_sha256'])
    return httpx.Response(record['http_status'], content=content)


def _foundation(pipe, foundation):
    from app.services import production_spend_runtime as runtime, production_cash_disabled as cash
    pipe.watch(runtime.LEDGER_KEY, cash.ANCHOR_KEY)
    require(cash.present(pipe), 'kie_voice_operating_budget_required')
    cash.read(pipe, foundation, now=foundation.clock())


def _binding(pipe, channel, connection):
    if channel not in CHANNELS:
        from app.services import framecase_kie_voice
        require(channel == framecase_kie_voice.CHANNEL_ID, 'kie_voice_channel_not_authorized')
        grant = framecase_kie_voice.read(pipe)
        require(grant is not None and grant['connection_id'] == connection,
                'kie_voice_channel_not_authorized')
    return _connected_owner(pipe, channel, connection)


def channel_connection(pipe, policy, channel):
    if channel in CHANNELS:
        from app.services.kie_voice_reconnection import read
        grant = read(pipe, policy, channel)
        return grant['connection_id'] if grant is not None else policy['channels'][channel]
    from app.services import framecase_kie_voice
    if channel != framecase_kie_voice.CHANNEL_ID:
        return None
    grant = framecase_kie_voice.read(pipe, policy)
    return grant['connection_id'] if grant is not None else None


def _connected_owner(pipe, channel, connection):
    from app.services import production_spend_runtime as runtime, production_continuation as continuation
    key = runtime._CHANNEL_PREFIX + channel
    pipe.watch(key, runtime._CHANNEL_INDEX)
    row = json.loads(pipe.get(key))
    require(row.get('id') == channel and row.get('connection_id') == connection
        and row.get('requires_reconnect') is not True
        and pipe.sismember(runtime._CHANNEL_INDEX, channel), 'kie_voice_connection_changed')
    proof = continuation.authority(pipe, channel)
    require(proof is not None, 'kie_voice_owner_authority_inactive')
    return proof


def _read(pipe):
    pipe.watch(POLICY_KEY, JOURNAL_KEY, ANCHOR_KEY, credentials.KEY, ACTIVE_KEY)
    p, j, a = (pipe.get(k) for k in (POLICY_KEY, JOURNAL_KEY, ANCHOR_KEY))
    p, j, a = (v.decode('utf-8') if isinstance(v, bytes) else v for v in (p, j, a))
    if p is None and j is None and a is None:
        require(pipe.get(ACTIVE_KEY) is None, 'kie_voice_partial_state')
        return None, None, None
    require(all(type(v) is str for v in (p, j, a))
        and all(pipe.pttl(k) == -1 for k in (POLICY_KEY, JOURNAL_KEY, ANCHOR_KEY))
        and len(p) <= 32768 and len(j) <= 64 * 1024 * 1024)
    policy, journal = json.loads(p), json.loads(j)
    require(a == sha(j) and type(policy) is dict and set(policy) == {
        'version', 'purpose', 'credential_sha256', 'credential_record_sha256',
        'allocation_microcredits', 'channels', 'owner_authorization_sha256',
        'balance', 'rates', 'created_at'}
        and type(policy['version']) is int and policy['version'] == 1
        and policy['purpose'] == 'owner_funded_kie_voice'
        and type(policy['allocation_microcredits']) is int
        and 0 < policy['allocation_microcredits'] <= 10**15
        and policy['rates'] == api.RATES
        and type(policy['channels']) is dict and set(policy['channels']) == CHANNELS
        and type(journal) is dict and set(journal) == {'policy_sha256', 'requests'}
        and journal['policy_sha256'] == sha(p) and type(journal['requests']) is dict
        and len(journal['requests']) <= MAX_REQUESTS)
    for field in ('credential_sha256', 'credential_record_sha256', 'owner_authorization_sha256'):
        require(type(policy[field]) is str and re.fullmatch('[0-9a-f]{64}', policy[field]))
    _stamp(policy['created_at'])
    key = pipe.get(credentials.KEY)
    require(key is not None and pipe.pttl(credentials.KEY) == -1)
    credential = credentials._decode(key)
    require(credential.fingerprint == policy['credential_sha256']
        and credential.record_sha256 == policy['credential_record_sha256'], 'kie_voice_key_changed')
    balance = api.object_response(restore(policy['balance']))
    require(api.microcredits(balance['data']) >= policy['allocation_microcredits'])
    from app.services import kie_gemini_voice as gemini
    extension = gemini.read(pipe, policy)
    for identity, row in journal['requests'].items():
        require(type(row) is dict and set(row) == {'scope', 'descriptor', 'reserved_at',
            'create', 'result'} and identity == sha(raw({'scope': row['scope'], 'request': row['descriptor']})))
        d = row['descriptor']
        require(type(d) is dict and set(d) == {'route', 'model', 'request_sha256',
            'credential_sha256', 'ceiling_microcredits', 'attempt', 'voice_id'}
            and d['route'] == api.CREATE and (d['model'] in api.RATES
                or (d['model'] == gemini.MODEL and extension is not None))
            and d['credential_sha256'] == credential.fingerprint
            and re.fullmatch('[0-9a-f]{64}', d['request_sha256'])
            and type(d['ceiling_microcredits']) is int and 0 < d['ceiling_microcredits'] <= 60_000_000
            and type(d['attempt']) is int and 0 <= d['attempt'] <= 2)
        _stamp(row['reserved_at'])
        for field in ('create', 'result'):
            if row[field] is not None:
                restore(row[field])
        require(row['result'] is None or row['create'] is not None)
    return policy, journal, credential


def _used(journal):
    total = 0
    for row in journal['requests'].values():
        amount = row['descriptor']['ceiling_microcredits']
        if row['result'] is not None:
            data = api.object_response(restore(row['result'])).get('data')
            require(type(data) is dict)
            observed = data.get('creditsConsumed')
            if observed is not None:
                amount = api.microcredits(observed)
        total += amount
    return total


def commission(foundation, *, owner_authorization_sha256):
    """Explicit operator allocation, after an authenticated balance GET."""
    require(type(owner_authorization_sha256) is str
        and re.fullmatch('[0-9a-f]{64}', owner_authorization_sha256))
    credential = credentials.read(foundation.client)
    require(credential is not None, 'kie_voice_key_missing')
    observation = api.read_balance(credential.api_key)
    require(observation['available_microcredits'] > 0, 'kie_voice_balance_empty')
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = _read(pipe)
        require(policy is None and journal is None, 'kie_voice_already_allocated')
        _foundation(pipe, foundation)
        from app.services import production_spend_runtime as runtime
        channels = {}
        for channel in sorted(CHANNELS):
            key = runtime._CHANNEL_PREFIX + channel
            pipe.watch(key)
            connection = json.loads(pipe.get(key))['connection_id']
            _binding(pipe, channel, connection)
            channels[channel] = connection
        stored = pipe.get(credentials.KEY)
        require(stored is not None and credentials._decode(stored).record_sha256 == credential.record_sha256)
        policy = {'version': 1, 'purpose': 'owner_funded_kie_voice',
            'credential_sha256': credential.fingerprint, 'credential_record_sha256': credential.record_sha256,
            'allocation_microcredits': observation['available_microcredits'], 'channels': channels,
            'owner_authorization_sha256': owner_authorization_sha256, 'created_at': now().isoformat(),
            'rates': api.RATES, 'balance': _seal(httpx.Response(200, content=observation['response']), credential.api_key)}
        encoded = raw({'policy_sha256': sha(raw(policy)), 'requests': {}})
        pipe.multi(); pipe.set(POLICY_KEY, raw(policy), nx=True)
        pipe.set(JOURNAL_KEY, encoded, nx=True); pipe.set(ANCHOR_KEY, sha(encoded), nx=True)
        require(pipe.execute() == [True, True, True], 'kie_voice_allocation_write_uncertain')
    return {'status': 'allocated_for_validation', 'allocation_microcredits': policy['allocation_microcredits']}


def _write(pipe, journal):
    encoded = raw(journal)
    pipe.multi(); pipe.set(JOURNAL_KEY, encoded); pipe.set(ANCHOR_KEY, sha(encoded))
    require(pipe.execute() == [True, True], 'kie_voice_journal_write_uncertain')


def _scope(pipe, foundation, policy, scope):
    _foundation(pipe, foundation)
    require(type(scope) is dict)
    if scope.get('kind') == 'video_dub':
        from app.services.video_dubbing import authorize_scope
        authorize_scope(pipe, policy, scope)
        return
    if scope.get('kind') == 'connection_probe':
        require(set(scope) == {'kind', 'language', 'voice_id'}
            and scope['language'] in {'tr', 'en'}
            and type(scope['voice_id']) is str and re.fullmatch('[A-Za-z0-9_-]{4,80}', scope['voice_id']))
        return
    from app.services import production_spend_runtime as runtime
    require(scope == runtime.resolve_context(foundation.client, runtime._TASK_ID.get()),
        'kie_voice_runtime_scope_changed')
    require(scope.get('kind') in {'shorts', 'long'} and 'purpose' not in scope)
    require(channel_connection(pipe, policy, scope['channel_id']) == scope['connection_id'])
    _binding(pipe, scope['channel_id'], scope['connection_id'])
    if scope['channel_id'] not in CHANNELS:
        from app.services.framecase_kie_voice import authorize_context
        authorize_context(pipe, scope)
    if scope['kind'] == 'long':
        from app.services.commissioning_longform import authorize
        authorize(pipe, scope)
    activation = pipe.get(ACTIVE_KEY)
    require(activation is not None and pipe.pttl(ACTIVE_KEY) == -1, 'kie_voice_validation_required')
    active = json.loads(activation)
    require(active.get('policy_sha256') == sha(raw(policy))
        and active.get('purpose') == 'verified_kie_voice_production')


class Journal:
    def __init__(self, foundation, scope, body, ceiling, *, attempt=0):
        self.foundation, self.scope, self.body = foundation, scope, body
        self.ceiling, self.attempt = ceiling, attempt
        self.identity = self.prior = self.credential = None

    @_local_transaction
    def _reserve(self):
        with self.foundation.client.pipeline() as pipe:
            policy, journal, credential = _read(pipe)
            require(policy is not None, 'kie_voice_funding_missing')
            _scope(pipe, self.foundation, policy, self.scope)
            from app.services import kie_gemini_voice as gemini
            is_gemini = self.body.get('model') == gemini.MODEL
            if is_gemini:
                require(gemini.read(pipe, policy) is not None, 'kie_voice_model_not_authorized')
                text, voice, expected_ceiling, language = gemini.describe(self.body)
                require(self.scope.get('language', language) == language)
                from app.services.framecase_cadence import CHANNEL_ID as framecase_channel
                if self.scope.get('channel_id') == framecase_channel:
                    require(language == 'en', 'framecase_kie_language_unverified')
            else:
                require(self.body.get('model') in api.RATES)
                text, voice = self.body['input']['text'], self.body['input']['voice']
                units = len(text.encode('utf-16-le')) // 2
                expected_ceiling = ((units + 999) // 1000) * api.RATES[self.body['model']] * 1_000_000
            d = {'route': api.CREATE, 'model': self.body['model'],
                'request_sha256': sha(raw(self.body)), 'credential_sha256': credential.fingerprint,
                'ceiling_microcredits': self.ceiling, 'attempt': self.attempt,
                'voice_id': voice}
            require(type(self.attempt) is int and 0 <= self.attempt <= 2
                and type(self.ceiling) is int and 0 < self.ceiling <= 60_000_000)
            # Recompute the frozen upper bound; callers cannot under-reserve.
            units = len(text.encode('utf-16-le')) // 2
            require(1 <= units <= 5000 and self.ceiling == expected_ceiling)
            if self.scope.get('kind') == 'video_dub':
                from app.services.video_dubbing import authorize_request
                authorize_request(pipe, policy, self.scope, d)
            elif self.scope.get('kind') != 'connection_probe':
                from app.services.kie_voice_production import authorize_request
                authorize_request(pipe, self.foundation, policy, self.scope, d)
            identity = sha(raw({'scope': self.scope, 'request': d}))
            prior = journal['requests'].get(identity)
            if prior is not None:
                require(prior['create'] is not None, 'kie_voice_previous_outcome_unknown')
                pipe.multi(); pipe.ping(); require(pipe.execute() == [True])
            else:
                same_root = [r for r in journal['requests'].values() if r['scope'] == self.scope]
                schema_repair = (is_gemini and self.scope.get('kind') == 'connection_probe'
                    and gemini.schema_repair_allowed(pipe, policy, same_root, self.body, self.attempt))
                maximum_attempts = 1 if self.scope.get('kind') == 'connection_probe' else 2 if self.scope.get('kind') == 'video_dub' else 3
                require(schema_repair or len(same_root) < maximum_attempts,
                    'kie_voice_attempt_limit')
                require(schema_repair or not any(r['result'] is None or r['descriptor']['attempt'] == self.attempt
                    for r in same_root), 'kie_voice_previous_request_pinned')
                if self.scope.get('kind') == 'connection_probe':
                    probe_count = sum(r['scope'].get('kind') == 'connection_probe'
                        and ((r['descriptor']['model'] == gemini.MODEL) is is_gemini)
                        for r in journal['requests'].values())
                    require(units <= 400 and self.scope['voice_id'] == d['voice_id']
                        and (schema_repair or (self.attempt == 0
                            and probe_count < (gemini.SPEC['max_connection_probes'] if is_gemini else 8))),
                        'kie_voice_probe_limit')
                require(len(journal['requests']) < MAX_REQUESTS
                    and _used(journal) + self.ceiling <= policy['allocation_microcredits'], 'kie_voice_balance_exhausted')
                journal['requests'][identity] = {'scope': self.scope, 'descriptor': d,
                    'reserved_at': now().isoformat(), 'create': None, 'result': None}
                _write(pipe, journal)
        self.identity, self.prior, self.credential = identity, prior, credential

    def submit(self, sender, url, **kwargs):
        require(url == api.CREATE and kwargs.get('json') == self.body)
        self._reserve()
        require(kwargs.get('headers', {}).get('Authorization') == 'Bearer ' + self.credential.api_key)
        if self.prior is not None:
            return restore(self.prior['create'])
        response = sender(url, **kwargs)
        self._observe('create', response)
        return response

    def result_response(self):
        saved = self.prior.get('result') if self.prior is not None else None
        return restore(saved) if saved is not None else None

    def observe_result(self, response):
        self._observe('result', response)

    @_local_transaction
    def _observe(self, field, response):
        # Capturing an already accepted response remains possible after a pause.
        require(field in {'create', 'result'} and self.identity is not None)
        with self.foundation.client.pipeline() as pipe:
            _, journal, credential = _read(pipe)
            require(credential.record_sha256 == self.credential.record_sha256)
            row = journal['requests'][self.identity]
            require(row['scope'] == self.scope)
            if row[field] is not None:
                old = restore(row[field])
                require(old.status_code == response.status_code and old.content == response.content,
                    'kie_voice_receipt_changed')
                pipe.multi(); pipe.ping(); require(pipe.execute() == [True])
                return
            row[field] = _seal(response, credential.api_key)
            _write(pipe, journal)


@_local_transaction
def status(client):
    with client.pipeline() as pipe:
        policy, journal, _ = _read(pipe)
        from app.services.kie_gemini_voice import rejected_style
        used = _used(journal) if journal is not None else 0
        value = {'status': 'not_allocated'} if policy is None else {
            'status': 'validation_pending' if pipe.get(ACTIVE_KEY) is None else 'active',
            'accounting_unit': 'kie_microcredits', 'allocation_microcredits': policy['allocation_microcredits'],
            'balance_observed_at': policy['balance']['observed_at'],
            'committed_microcredits': used,
            'remaining_microcredits': max(0, policy['allocation_microcredits'] - used),
            'requests': len(journal['requests']),
            'failed_requests': sum(r['result'] is not None
                and api.object_response(restore(r['result']))['data']['state'] == 'fail'
                for r in journal['requests'].values()),
            'rejected_before_creation': sum(rejected_style(r) for r in journal['requests'].values()),
            'unknown_or_pending': sum(r['result'] is None and not rejected_style(r)
                for r in journal['requests'].values())}
        pipe.multi(); pipe.ping(); require(pipe.execute() == [True])
    return value
