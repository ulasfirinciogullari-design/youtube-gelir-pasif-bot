"""One explicit diagnostic request after this root's frozen three unknowns.

These records never repair/settle an old review or authorize quality. Presence
of any probe record permanently fences normal review reservations. External
loss of every copy also needs the operator's independent one-shot marker.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import threading
from uuid import uuid4

from app.config import settings
from app.services import provider_key_candidate as candidate
from app.services import retained_review_credential_successor as controller
from app.services.production_spend import SpendBlocked


PURPOSE = 'router_protocol_diagnostic_v1'
_PREFIX = controller.STATE_KEY.rsplit(':', 1)[0] + ':protocol_diagnostic:v1'
PROBE_KEYS = tuple(_PREFIX + suffix for suffix in (':state', ':journal', ':reserved'))
_CAPS = {'monthly_micro', 'daily_micro', 'channel_monthly_micro', 'shorts_micro', 'long_micro', 'derived_micro'}
_OFF = ('studio_abacus_editorial_enabled', 'studio_longform_delivery_enabled',
        'studio_elevenlabs_native_credits', 'studio_abacus_router_retained_review_enabled',
        'studio_abacus_router_retained_audio_review_enabled')
_FAILURE = {
    'failure_proof_sha256': '751ad3a6bf805f408063802ba1758e426726c178e29e22042856bf737c81a9db',
    'failure_attempt_id': '009b907e-1e4f-45d2-88d6-7f4286219d47',
    'failure_source_head_sha': '5fdcad676383059998868440c9364b268818c06b',
    'failure_source_tree_sha': '3f1f65e1fc1ad33073917eeeabc98d82a9d360ed',
    'controller_manifest_sha256': 'bf58438b1d245ed9c7e4fc3713449622a87cdae80bcef7128883a0616f3e01e0',
    'story_state_sha256': 'd9c97d624834ff0512fe638be22f4f008a74a7e26e2395e57715f972e1b2adf6',
    'audio_state_sha256': '7fa1980547349ff0c576e9557d1d0e5afc9cfcb542b1c897bb2ee1f5a9deac2f',
}
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'resume_authorized': False, 'retry_authorized': False}
_MAX = 1024 * 1024
_CURRENT = ContextVar('retained_router_protocol_probe', default=None)


class ProtocolProbeBlocked(SpendBlocked):
    """Fixed local diagnostic admission failures only."""


def _require(value, reason='protocol_probe_unverified'):
    if not value:
        raise ProtocolProbeBlocked(reason)


def _raw(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(raw) <= _MAX)
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(_raw(value))


def _object(raw):
    _require(type(raw) in (bytes, str) and 0 < len(raw) <= _MAX)
    def pairs(items):
        value = {}
        for key, item in items:
            _require(key not in value)
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    return value


def _stamp(clock):
    now = clock()
    _require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None)
    return now.astimezone(timezone.utc).replace(microsecond=0)


def _configuration():
    _require(settings.studio_spend_enforcement is True, 'protocol_probe_cash_lock_changed')
    policy = _object(settings.studio_spend_policy_json)
    _require(set(policy) == _CAPS and all(type(n) is int and n == 0 for n in policy.values()),
             'protocol_probe_cash_lock_changed')
    _require(all(getattr(settings, name, False) is False for name in _OFF),
             'protocol_probe_feature_state_changed')
    app_key, key = candidate._context()
    head = os.environ.get('RAILWAY_GIT_COMMIT_SHA', '')
    _require(re.fullmatch('[0-9a-f]{40}', head) is not None, 'protocol_probe_runtime_changed')
    return app_key, key, head


def _attestation(value, context):
    _require(type(value) is dict and set(value) == {'version', 'kind', 'runtime_head_sha',
        'owner_authorization_sha256', 'operator_attempt_sha256', *_FAILURE})
    _require(type(value['version']) is int and value['version'] == 1
             and value['kind'] == 'explicit_frozen_protocol_diagnostic'
             and value['runtime_head_sha'] == context[2]
             and all(type(value[k]) is str and value[k] == expected for k, expected in _FAILURE.items()),
             'protocol_probe_failure_binding_changed')
    for key in ('owner_authorization_sha256', 'operator_attempt_sha256'):
        _require(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]) is not None)
    return _object(_raw(value))


def _frozen_source(pipe, context, now):
    manifest, states, raw, actual_context = controller._read_control(pipe, current=True)
    _require(actual_context == context[:2] and _sha(raw) == _FAILURE['controller_manifest_sha256'],
             'protocol_probe_source_changed')
    story, audio = states['story'], states['audio']
    _require(set(story['slots']) == {controller.story.PURPOSES[0]}
             and story['slots'][controller.story.PURPOSES[0]]['response'] is None
             and audio['slots'] == {}, 'protocol_probe_requires_three_frozen_unknowns')
    for kind, keys in (('story', controller.STORY_KEYS), ('audio', controller.AUDIO_KEYS)):
        value = pipe.get(keys[0])
        _require(type(value) is str and _sha(value.encode()) == _FAILURE[kind + '_state_sha256'],
                 'protocol_probe_source_changed')
        policy = states[kind]['policy']
        _require(controller._date(policy['valid_from']) <= now < controller._date(policy['valid_until']),
                 'protocol_probe_window_expired')
    _require(manifest['old_occupied_unknown_count'] == 2
             and 2 + sum(len(s['slots']) for s in states.values()) + 1 == 4 <= manifest['max_total_attempts'])
    snapshots = {}
    for keys in (controller.LEGACY_KEYS[:3], controller.LEGACY_KEYS[3:],
                 (controller.STATE_KEY, controller.JOURNAL_KEY, controller.ANCHOR_KEY),
                 controller.STORY_KEYS, controller.AUDIO_KEYS):
        for index, key in enumerate(keys):
            value = pipe.hgetall(key) if index == 1 else pipe.get(key)
            snapshots[key] = _hash(value)
    return {'policy_sha256': _hash(story['policy']),
            'continuity_sha256': story['policy']['continuity_sha256'],
            'controller_manifest_sha256': _sha(raw), 'frozen_records_sha256': _hash(snapshots)}


def _journal(state):
    return {'reservation_sha256': _hash(state['reservation']),
            'frozen_records_sha256': state['reservation']['frozen_records_sha256'],
            'capture_sha256': _hash(state['capture']) if state['capture'] is not None else 'unknown'}


def _safe_binding(reservation):
    names = ('version', 'purpose', 'original_task_id', 'source_task_id', 'probe_id',
             'request_sha256', 'credential_sha256', 'policy_sha256', 'continuity_sha256',
             'controller_manifest_sha256', 'frozen_records_sha256', 'failure_proof_sha256',
             'runtime_head_sha', 'reserved_at')
    return {**{key: reservation[key] for key in names},
            'reservation_sha256': _hash(reservation)}


def _read(pipe, reservation):
    pipe.watch(*PROBE_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in PROBE_KEYS)),
             'protocol_probe_records_not_durable')
    state = _object(pipe.get(PROBE_KEYS[0]))
    _require(set(state) == {'version', 'reservation', 'capture'}
             and type(state['version']) is int and state['version'] == 1
             and _raw(state['reservation']) == _raw(reservation)
             and pipe.hlen(PROBE_KEYS[1]) == 3 and pipe.hgetall(PROBE_KEYS[1]) == _journal(state)
             and pipe.get(PROBE_KEYS[2]) == _hash(state), 'protocol_probe_records_changed')
    if state['capture'] is not None:
        from app.services.abacus_router_protocol_diagnostic import _validate_capture_record
        _validate_capture_record(state['capture'], binding=_safe_binding(reservation))
    return state


def _ack(pipe):
    pipe.multi(); pipe.ping()
    result = pipe.execute()
    _require(type(result) is list and len(result) == 1 and result[0] is True,
             'protocol_probe_ack_unknown')


def protocol_probe_admission_context(client, *, clock=None):
    """Read-only snapshot for explicit operator preparation; no send capability."""
    try:
        context = _configuration()
        actual_clock = clock or (lambda: datetime.now(timezone.utc))
        with client.pipeline() as pipe:
            pipe.watch(*PROBE_KEYS)
            _require(all(type(ttl) is int and ttl == -2 for ttl in (pipe.pttl(k) for k in PROBE_KEYS)),
                     'protocol_probe_already_reserved_or_partial')
            source = _frozen_source(pipe, context, _stamp(actual_clock))
            _ack(pipe)
        _require(_configuration() == context, 'protocol_probe_configuration_changed')
        return {'version': 1, 'purpose': PURPOSE, 'runtime_head_sha': context[2],
                'failure_binding': dict(_FAILURE), 'source_binding': source,
                'probe_records_absent': True, 'prior_occupied_count': 3,
                'prior_unknown_count': 3, 'occupied_count_after_probe': 4,
                'max_total_attempts': 6, 'send_authorized': False, **_FLAGS}
    except ProtocolProbeBlocked:
        raise
    except Exception:
        raise ProtocolProbeBlocked('protocol_probe_read_unverified') from None


@dataclass(repr=False)
class _Scope:
    client: object
    clock: object
    prepared: object
    context: tuple
    reservation: dict
    owner: int
    permit: object = None
    attempted: bool = False
    capture_attempted: bool = False
    closed: bool = False
    failed: bool = False


@dataclass(frozen=True, repr=False, init=False, slots=True)
class ProtocolProbePermit:
    _scope: object

    def __new__(cls, *args, **kwargs):
        raise TypeError('protocol_probe_permit_private')

    def __repr__(self):
        return '<ProtocolProbePermit diagnostic only redacted>'

    def assert_active(self):
        scope = _CURRENT.get()
        _require(type(self) is ProtocolProbePermit and scope is getattr(self, '_scope', None)
                 and scope is not None and scope.permit is self and not scope.closed
                 and not scope.failed and scope.owner == threading.get_ident(),
                 'protocol_probe_scope_unavailable')
        return None

    @property
    def safe_binding(self):
        self.assert_active()
        return _safe_binding(self._scope.reservation)

    def take_request(self):
        self.assert_active()
        scope = self._scope
        try:
            _require(not scope.attempted, 'protocol_probe_already_attempted')
            scope.attempted = True
            _require(_configuration() == scope.context, 'protocol_probe_configuration_changed')
            from app.services.abacus_router_protocol_diagnostic import _prepare_probe
            _require(_prepare_probe() == scope.prepared, 'protocol_probe_request_changed')
            with scope.client.pipeline() as pipe:
                state = _read(pipe, scope.reservation)
                _require(state['capture'] is None)
                actual = _frozen_source(pipe, scope.context, _stamp(scope.clock))
                _require(all(actual[k] == scope.reservation[k] for k in actual), 'protocol_probe_source_changed')
                _ack(pipe)
            _require(_configuration() == scope.context, 'protocol_probe_configuration_changed')
            return scope.prepared
        except Exception:
            scope.failed = True
            raise ProtocolProbeBlocked('protocol_probe_send_not_authorized') from None

    def record_capture(self, capture):
        self.assert_active()
        scope = self._scope
        try:
            _require(scope.attempted and not scope.capture_attempted, 'protocol_probe_capture_not_authorized')
            scope.capture_attempted = True
            from app.services.abacus_router_protocol_diagnostic import _capture_record, _validate_capture_record
            record = _capture_record(capture, permit=self,
                request_sha256=scope.prepared.request_sha256,
                credential_sha256=scope.prepared.credential_sha256)
            record = _object(_raw(record))
            _validate_capture_record(record, binding=_safe_binding(scope.reservation))
            # Preserve a genuine late/invalid response even if configuration or
            # source changes after send. This only updates its own diagnostic;
            # no request/old settlement or quality permission follows.
            with scope.client.pipeline() as pipe:
                state = _read(pipe, scope.reservation)
                _require(state['capture'] is None, 'protocol_probe_capture_already_recorded')
                state['capture'] = record
                pipe.multi()
                pipe.set(PROBE_KEYS[0], _raw(state).decode())
                pipe.hset(PROBE_KEYS[1], mapping=_journal(state))
                pipe.set(PROBE_KEYS[2], _hash(state))
                ack = pipe.execute()
                _require(type(ack) is list and len(ack) == 3 and ack[0] is True
                         and type(ack[1]) is int and ack[1] == 0 and ack[2] is True,
                         'protocol_probe_capture_ack_unknown')
            with scope.client.pipeline() as pipe:
                _require(_raw(_read(pipe, scope.reservation)) == _raw(state))
                _ack(pipe)
            return {**self.safe_binding, 'capture_sha256': _hash(record),
                    'capture_record_acknowledged': True, 'occupied_count': 4,
                    'prior_unknown_count': 3, **_FLAGS}
        except Exception:
            scope.failed = True
            raise ProtocolProbeBlocked('protocol_probe_capture_unverified') from None


@contextmanager
def protocol_diagnostic_probe_scope(client, *, attestation, clock=None):
    """Explicit one-shot admission; absence/readback never recovers a send permit."""
    _require(_CURRENT.get() is None, 'protocol_probe_nested')
    from app.services import abacus_router_review_runtime as story_runtime
    from app.services import abacus_router_audio_review_runtime as audio_runtime
    _require(story_runtime._SCOPE.get() is None and audio_runtime._SCOPE.get() is None,
             'protocol_probe_nested')
    scope = None
    token = None
    try:
        context = _configuration()
        checked = _attestation(attestation, context)
        actual_clock = clock or (lambda: datetime.now(timezone.utc))
        now = _stamp(actual_clock)
        from app.services.abacus_router_protocol_diagnostic import _prepare_probe
        prepared = _prepare_probe()
        _require(prepared.credential_sha256 == _sha(('abacus\0' + context[1]).encode('ascii')))
        with client.pipeline() as pipe:
            pipe.watch(*PROBE_KEYS)
            _require(all(type(ttl) is int and ttl == -2 for ttl in (pipe.pttl(k) for k in PROBE_KEYS)),
                     'protocol_probe_already_reserved_or_partial')
            source = _frozen_source(pipe, context, now)
            reservation = {'version': 1, 'purpose': PURPOSE,
                'original_task_id': controller.continuity.ROOT_ID, 'source_task_id': controller.continuity.LEAF_ID,
                'probe_id': str(uuid4()), 'request_sha256': prepared.request_sha256,
                'credential_sha256': prepared.credential_sha256, **source,
                'failure_proof_sha256': _FAILURE['failure_proof_sha256'], 'runtime_head_sha': context[2],
                'reserved_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'attestation': checked,
                'prior_occupied_count': 3, 'prior_unknown_count': 3, 'total_occupied_count': 4, **_FLAGS}
            state = {'version': 1, 'reservation': reservation, 'capture': None}
            _require(_configuration() == context, 'protocol_probe_configuration_changed')
            pipe.multi()
            pipe.set(PROBE_KEYS[0], _raw(state).decode(), nx=True)
            pipe.hset(PROBE_KEYS[1], mapping=_journal(state))
            pipe.set(PROBE_KEYS[2], _hash(state), nx=True)
            ack = pipe.execute()
            _require(type(ack) is list and len(ack) == 3 and ack[0] is True
                     and type(ack[1]) is int and ack[1] == 3 and ack[2] is True,
                     'protocol_probe_reservation_ack_unknown')
        with client.pipeline() as pipe:
            _require(_raw(_read(pipe, reservation)) == _raw(state))
            _require(_frozen_source(pipe, context, _stamp(actual_clock)) == source)
            _ack(pipe)
        _require(_configuration() == context, 'protocol_probe_configuration_changed')
        scope = _Scope(client, actual_clock, prepared, context, reservation, threading.get_ident())
        permit = object.__new__(ProtocolProbePermit)
        object.__setattr__(permit, '_scope', scope)
        scope.permit = permit
        token = _CURRENT.set(scope)
        yield permit
    except ProtocolProbeBlocked:
        if scope is not None: scope.failed = True
        raise
    except Exception:
        if scope is not None: scope.failed = True
        raise ProtocolProbeBlocked('protocol_probe_outcome_unverified') from None
    finally:
        if scope is not None: scope.closed = True
        if token is not None: _CURRENT.reset(token)
