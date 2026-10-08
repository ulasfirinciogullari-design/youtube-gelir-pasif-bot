"""A durable before-send boundary for the native voice reservation.

Redis may commit a reservation and lose its acknowledgement. A preparation
written BEFORE that reservation lets the same request recover the held receipt;
it is not permission to send. Only one atomic, permanent claim permits a POST.
After a claim, every later invocation remains blocked, including timeouts and
worker loss. No credits are refunded and no legacy reservation is auto-adopted.
"""
import re
from uuid import uuid4

from redis.exceptions import WatchError

from app.services.production_credit_ledger import _hash, _json, _object, _require
from app.services.production_spend import LEDGER_KEY, SpendBlocked

PREFIX = 'youtube_studio:{production_spend}:native_dispatch:v1:'
_HEX = re.compile('[0-9a-f]{64}')


def _ack(result, count=2):
    _require(type(result) is list and len(result) == count
             and all(type(value) is int and value == 1 for value in result),
             'credit_dispatch_commit_uncertain')


def _keys(intent):
    identity = intent['intent_id']
    _require(type(identity) is str and _HEX.fullmatch(identity), 'credit_dispatch_invalid')
    return PREFIX + identity, PREFIX + identity + ':anchor'


def _proof_keys(intent):
    key = PREFIX + 'proof:' + intent['intent_id']
    return key, key + ':anchor'


def _identity(ledger, policy, intent, context, actual):
    from app.services.production_credit_funding import _actual_binding, _intent
    _intent(intent, policy, 'credit_dispatch_invalid')
    _actual_binding(policy, **actual)
    _require(ledger.foundation is not None and context == ledger._context(intent),
             'credit_production_context_invalid')
    return {'version': 1, 'intent': intent, 'context': context,
            'policy_sha256': _hash(policy), 'account_sha256': policy['account_sha256'],
            'credential_sha256': policy['credential_sha256']}


def _records(pipe, keys, identity, entry):
    raw, anchor = (pipe.hgetall(key) for key in keys)
    marker = pipe.hget(LEDGER_KEY, 'native_dispatch_claim:' + identity['intent']['intent_id'])
    if not raw and not anchor:
        _require(not pipe.exists(*keys) and marker is None, 'credit_dispatch_partial')
        return None
    _require(all(pipe.pttl(key) == -1 for key in keys)
             and set(raw) in ({'prepared'}, {'prepared', 'claim'})
             and set(anchor) == set(raw), 'credit_dispatch_partial')
    records = {key: _object(value) for key, value in raw.items()}
    _require(all(anchor[key] == _hash(value) for key, value in records.items()),
             'credit_dispatch_mismatch')
    prepared = records['prepared']
    _require(set(prepared) == set(identity) | {'origin'}
             and all(prepared[key] == value for key, value in identity.items()),
             'credit_dispatch_identity_changed')
    origin = prepared['origin']
    if origin != {'kind': 'runtime_before_reservation'}:
        # This origin can only be installed by a separately reviewed, private
        # operator with positive pre-send evidence. No HTTP/scheduler entry does
        # so. Missing history or elapsed time alone is never such evidence.
        _require(type(origin) is dict and set(origin) == {
            'kind', 'reservation_sha256', 'evidence_sha256'}
            and origin['kind'] == 'verified_legacy_pre_send'
            and type(origin['evidence_sha256']) is str
            and _HEX.fullmatch(origin['evidence_sha256'])
            and entry is not None
            and origin['reservation_sha256'] == entry['reservation']['reservation_sha256'],
            'credit_dispatch_legacy_outcome_unverified')
        proof_key, proof_anchor = _proof_keys(identity['intent'])
        proof = _object(pipe.get(proof_key))
        _require(all(pipe.pttl(key) == -1 for key in (proof_key, proof_anchor))
            and _hash(proof) == origin['evidence_sha256'] == pipe.get(proof_anchor)
            and type(proof.get('version')) is int and proof['version'] == 1
            and proof.get('kind') == 'verified_legacy_pre_send'
            and proof.get('intent_id') == identity['intent']['intent_id']
            and proof.get('root_lineage_id') == identity['intent']['root_lineage_id']
            and proof.get('reservation_sha256') == origin['reservation_sha256'],
            'credit_dispatch_legacy_outcome_unverified')
    if 'claim' in records:
        claim = records['claim']
        _require(type(claim) is dict and set(claim) == {
            'version', 'prepared_sha256', 'reservation_sha256', 'invocation'}
            and type(claim['version']) is int and claim['version'] == 1
            and claim['prepared_sha256'] == _hash(prepared)
            and type(claim['invocation']) is str
            and re.fullmatch('[0-9a-f]{32}', claim['invocation'])
            and entry is not None
            and claim['reservation_sha256'] == entry['reservation']['reservation_sha256']
            and marker == _hash(claim),
            'credit_dispatch_mismatch')
    else:
        _require(marker is None, 'credit_dispatch_mismatch')
    return records


def _snapshot(ledger, pipe, intent, context, actual):
    keys = _keys(intent)
    ledger._watch(pipe)
    pipe.watch(*keys, *_proof_keys(intent))
    policy, state, _, _ = ledger._read(pipe, ledger.clock())
    identity = _identity(ledger, policy, intent, context, actual)
    _require(_object(pipe.hget(LEDGER_KEY, 'binding:' + intent['root_lineage_id'])) == context,
             'credit_production_context_invalid')
    entry = state['intents'].get(intent['intent_id'])
    if entry is not None:
        _require(entry['reservation']['intent'] == intent, 'credit_dispatch_identity_changed')
    return keys, identity, entry, _records(pipe, keys, identity, entry)


def _transaction(ledger, action):
    for _ in range(8):
        try:
            with ledger.client.pipeline() as pipe:
                return action(pipe)
        except WatchError:
            continue
        except SpendBlocked:
            raise
        except Exception:
            raise SpendBlocked('credit_dispatch_store_unavailable') from None
    raise SpendBlocked('credit_dispatch_store_contention')


def prepare(ledger, intent, context, actual):
    """Persist the before-reserve boundary; never adopt a preexisting hold."""
    def action(pipe):
        keys, identity, entry, records = _snapshot(ledger, pipe, intent, context, actual)
        if records is not None:
            _require('claim' not in records and (entry is None or entry['settlement'] is None),
                     'credit_cross_mode_request_conflict')
            ledger._ping(pipe)
            return
        _require(entry is None, 'credit_dispatch_legacy_outcome_unverified')
        field = ledger._request_field(intent)
        _require(not pipe.hexists(LEDGER_KEY, 'native_request:' + field)
                 and not pipe.hexists(LEDGER_KEY, 'request:' + field),
                 'credit_cross_mode_request_conflict')
        prepared = {**identity, 'origin': {'kind': 'runtime_before_reservation'}}
        pipe.multi()
        pipe.hset(keys[0], 'prepared', _json(prepared))
        pipe.hset(keys[1], 'prepared', _hash(prepared))
        _ack(pipe.execute())
    return _transaction(ledger, action)


def pending_receipt(ledger, intent, context, actual):
    """Read an existing prepared hold; the separate send claim is mandatory."""
    def action(pipe):
        _, _, entry, records = _snapshot(ledger, pipe, intent, context, actual)
        _require(records is not None and 'claim' not in records and entry is not None
                 and entry['settlement'] is None, 'credit_dispatch_outcome_unverified')
        ledger._ping(pipe)
        return entry['reservation']
    return _transaction(ledger, action)


def claim(ledger, intent, context, actual, receipt):
    """Exactly one invocation may pass this boundary and perform one POST."""
    invocation = uuid4().hex

    def action(pipe):
        keys, _, entry, records = _snapshot(ledger, pipe, intent, context, actual)
        _require(records is not None and entry is not None and entry['settlement'] is None
                 and entry['reservation'] == receipt, 'credit_dispatch_outcome_unverified')
        expected = {'version': 1, 'prepared_sha256': _hash(records['prepared']),
                    'reservation_sha256': receipt['reservation_sha256'], 'invocation': invocation}
        if 'claim' in records:
            # WATCH may report a connection loss after EXEC committed. Only the
            # SAME still-running call knows this nonce and has not returned to
            # its sender yet. A later invocation can never obtain this permit.
            _require(records['claim'] == expected, 'credit_dispatch_already_claimed')
            ledger._ping(pipe)
            return
        pipe.multi()
        pipe.hset(keys[0], 'claim', _json(expected))
        pipe.hset(keys[1], 'claim', _hash(expected))
        # A surviving foundation marker also detects both sidecars rolling back
        # to their prepared state. Deleting one journal must never permit replay.
        pipe.hset(LEDGER_KEY, 'native_dispatch_claim:' + intent['intent_id'], _hash(expected))
        _ack(pipe.execute(), 3)
    return _transaction(ledger, action)


def ready_for_root(root):
    """Read-only scheduler hint; actual admission still reserves and claims."""
    from app.services.production_spend_runtime import configured_ledger
    from app.services.production_credit_ledger import CreditLedger
    try:
        foundation = configured_ledger(read_timeout=3)
        ledger = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
        def action(pipe):
            ledger._watch(pipe)
            policy, state, _, _ = ledger._read(pipe, ledger.clock())
            pending = [entry for entry in state['intents'].values()
                       if entry['reservation']['intent']['root_lineage_id'] == root
                       and entry['settlement'] is None]
            if len(pending) != 1:
                ledger._ping(pipe)
                return False
            intent = pending[0]['reservation']['intent']
            actual = {'actual_account_sha256': policy['account_sha256'],
                      'actual_credential_sha256': policy['credential_sha256']}
            _, _, _, records = _snapshot(ledger, pipe, intent, ledger._context(intent), actual)
            ledger._ping(pipe)
            return records is not None and 'claim' not in records
        return _transaction(ledger, action)
    except Exception:
        return False
