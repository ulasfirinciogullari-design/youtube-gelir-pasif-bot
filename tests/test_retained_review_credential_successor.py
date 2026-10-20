"""Explicit credential continuation with real disposable Redis and frozen legacy history."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import retained_review_credential_successor as successor
from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services import provider_key_candidate as candidate
from app.services import production_connection_continuity as continuity
from app.services.production_spend import SpendBlocked
from test_abacus_router_audio_review_journal import (
    case, source, no_transport, NOW, KEY, ASR, PROSODY, mp3, response as audio_response,
    canonical, digest, sha, prosody as prosody_request,
)
from test_abacus_router_review_journal import request, response as story_response
from test_production_connection_continuity import _dump


NEW_KEY = 'offline-promoted-provider-key-019fb730ae'
APP_MATERIAL = 'offline-successor-encryption-material-8c0219fa'
SENTINEL = 'SECRET_BACKEND_EXCEPTION_MUST_NOT_ESCAPE'
STORY_PURPOSE, VISUAL_PURPOSE = story.PURPOSES
OLD_KEYS = (story.STATE_KEY, story.JOURNAL_KEY, story.ANCHOR_KEY,
            audio.STATE_KEY, audio.JOURNAL_KEY, audio.ANCHOR_KEY)
ARCHIVE_PREFIX = 'youtube_studio:{provider_key_candidate}:abacus:archive:v1:'


def stamp(now):
    return now.strftime('%Y-%m-%dT%H:%M:%SZ')


def records(client, keys):
    return {key: client.dump(key) for key in keys}


class Intercept:
    """Real transactions with exactly one optional fault at each EXEC boundary."""
    def __init__(self, client, *, before=None, after=None):
        self.client, self.before, self.after = client, before, after
        self.executions = []

    def __getattr__(self, name):
        return getattr(self.client, name)

    def pipeline(self):
        outer, pipe = self, self.client.pipeline()
        class Wrapped:
            def __enter__(self):
                pipe.__enter__()
                return self
            def __exit__(self, *args):
                return pipe.__exit__(*args)
            def __getattr__(self, name):
                return getattr(pipe, name)
            def execute(self):
                commands = tuple(command[0][0] for command in pipe.command_stack)
                outer.executions.append(commands)
                if outer.before:
                    outer.before(commands)
                result = pipe.execute()
                return outer.after(commands, result) if outer.after else result
        return Wrapped()


@pytest.fixture
def prepared(case, source, monkeypatch):
    old_time = NOW - timedelta(hours=2)
    source.policy['valid_from'] = stamp(old_time - timedelta(minutes=1))
    source.policy['valid_until'] = stamp(old_time + timedelta(minutes=1))
    story_policy = {name: deepcopy(source.policy[name]) for name in story._POLICY_FIELDS}
    story_policy['kind'] = 'existing_subscription_retained_review'
    old_story = story.RouterReviewJournal(case.client, clock=lambda: old_time)
    old_audio = audio.RouterAudioReviewJournal(case.client, clock=lambda: old_time)
    old_request = request(key=KEY)
    old_story.commission(story_policy)
    old_story.reserve(STORY_PURPOSE, old_request)
    old_audio.commission(source.policy, source_metadata_bytes=source.metadata_bytes)
    old_audio.reserve(ASR, source.prepared)
    config = SimpleNamespace(app_encryption_key=APP_MATERIAL, abacus_api_key=KEY,
        studio_spend_enforcement=True, studio_spend_policy_json=canonical({
            name: 0 for name in ('monthly_micro', 'daily_micro', 'channel_monthly_micro',
                                 'shorts_micro', 'long_micro', 'derived_micro')}))
    monkeypatch.setattr(candidate, 'settings', config)
    candidate.stage_candidate(case.client, NEW_KEY)
    staged = candidate.read_candidate(case.client)
    ciphertext = case.client.get(candidate.CANDIDATE_KEY)
    archive_key = ARCHIVE_PREFIX + staged.candidate_id + ':' + staged.record_sha256
    assert case.client.set(archive_key, ciphertext, nx=True) is True
    assert case.client.delete(candidate.CANDIDATE_KEY) == 1
    config.abacus_api_key = NEW_KEY
    snapshot = successor.snapshot_predecessors(case.client)
    new_request = request(key=NEW_KEY)
    update = {'credential_sha256': new_request.credential_sha256,
              'entitlement_evidence_sha256': 'f'*64,
              'valid_from': stamp(NOW-timedelta(minutes=1)),
              'valid_until': stamp(NOW+timedelta(hours=1))}
    attestation = {'version': 1, 'kind': 'operator_included_credential_successor',
        'candidate_id': staged.candidate_id, 'candidate_record_sha256': staged.record_sha256,
        'original_active_key_sha256': hashlib.sha256(KEY.encode()).hexdigest(),
        'old_credential_sha256': old_request.credential_sha256,
        'new_credential_sha256': new_request.credential_sha256,
        'predecessor_snapshot_sha256': snapshot['snapshot_sha256'],
        'promotion_plan_sha256': '1'*64, 'old_vault_before_sha256': '2'*64,
        'promotion_finalized_proof_sha256': '3'*64, 'readonly_access_proof_sha256': '4'*64,
        'owner_authorization_sha256': '5'*64, 'entitlement_evidence_sha256': 'f'*64,
        'old_key_binding': 'operator_verified_promotion_backup',
        'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0,
        'account_plan_verified': False, 'completion_or_funding_verified': False}
    return SimpleNamespace(client=case.client, source=source, config=config, old_story=old_story,
        old_audio=old_audio, old_request=old_request, new_request=new_request,
        old_story_policy=story_policy, story_policy={**story_policy, **update},
        audio_policy={**deepcopy(source.policy), **update}, snapshot=snapshot,
        attestation=attestation, archive_key=archive_key, ciphertext=ciphertext,
        legacy=records(case.client, OLD_KEYS), before=_dump(case.client))


def commission(prepared, *, client=None, **changes):
    return successor.commission_credential_successor(client or prepared.client, **{
        'story_policy': prepared.story_policy, 'audio_policy': prepared.audio_policy,
        'source_metadata_bytes': prepared.source.metadata_bytes,
        'attestation': prepared.attestation, 'clock': lambda: NOW, **changes})


def selected(prepared, cap, *, client=None):
    client = client or prepared.client
    return (story.RouterReviewJournal(client, clock=lambda: NOW, successor=cap),
            audio.RouterAudioReviewJournal(client, clock=lambda: NOW, successor=cap))


def rejected(call):
    with pytest.raises((SpendBlocked, RuntimeError, TypeError, AttributeError)) as caught:
        call()
    assert all(secret not in str(caught.value) for secret in (KEY, NEW_KEY, APP_MATERIAL, SENTINEL))


def assert_legacy_preserved(prepared):
    assert records(prepared.client, OLD_KEYS) == prepared.legacy


def test_snapshot_is_read_only_exact_two_unknown_predecessors(prepared):
    value = successor.snapshot_predecessors(prepared.client)
    assert set(value) == {'story', 'audio', 'snapshot_sha256', 'old_credential_sha256', 'occupied_unknown_count'}
    assert value['occupied_unknown_count'] == 2
    assert value['old_credential_sha256'] == prepared.old_request.credential_sha256
    for part in ('story', 'audio'):
        assert set(value[part]) == {'state_sha256', 'journal_sha256', 'anchor_sha256'}
        assert all(len(item) == 64 for item in value[part].values())
    assert _dump(prepared.client) == prepared.before
    value['story']['state_sha256'] = '0'*64
    assert successor.snapshot_predecessors(prepared.client) == prepared.snapshot


def test_one_commission_creates_only_nine_durable_keys_and_preserves_all_old_bytes(prepared):
    cap = commission(prepared)
    assert type(cap) is successor.SuccessorAuthorization
    after = _dump(prepared.client)
    assert set(after) - set(prepared.before) == set(successor.ALL_KEYS)
    assert len(set(successor.ALL_KEYS)) == 9
    assert all(prepared.client.pttl(key) == -1 for key in successor.ALL_KEYS)
    assert {key: value for key, value in after.items() if key not in successor.ALL_KEYS} == prepared.before
    assert set(successor.LEGACY_KEYS) == set(OLD_KEYS)
    assert all(secret not in repr(cap) + json.dumps(cap.receipt) for secret in (KEY, NEW_KEY, APP_MATERIAL, prepared.ciphertext))
    rejected(lambda: commission(prepared))
    assert _dump(prepared.client) == after
    assert_legacy_preserved(prepared)


def test_actual_successor_read_requires_only_ping_ack_and_returns_detached_receipt(prepared):
    cap = commission(prepared)
    client = Intercept(prepared.client)
    value = successor.read_credential_successor(client)
    assert type(value) is successor.SuccessorAuthorization and value.receipt == cap.receipt
    assert client.executions and all(commands == ('PING',) for commands in client.executions)
    receipt = value.receipt
    receipt['arbitrary_field'] = 'changed'
    assert 'arbitrary_field' not in value.receipt


def test_same_body_new_key_gets_one_successor_slot_without_reopening_old_slot(prepared):
    cap = commission(prepared)
    new_story, _ = selected(prepared, cap)
    assert prepared.old_request.request_sha256 == prepared.new_request.request_sha256
    assert prepared.old_request.credential_sha256 != prepared.new_request.credential_sha256
    assert new_story.keys == successor.STORY_KEYS
    receipt = new_story.reserve(STORY_PURPOSE, prepared.new_request)
    assert receipt['request_sha256'] == prepared.new_request.request_sha256
    rejected(lambda: new_story.reserve(STORY_PURPOSE, prepared.new_request))
    rejected(lambda: prepared.old_story.reserve(STORY_PURPOSE, prepared.old_request))
    assert_legacy_preserved(prepared)


@pytest.mark.parametrize('first', ['story', 'asr'])
def test_any_unsettled_new_slot_blocks_all_other_purposes(prepared, first):
    cap = commission(prepared)
    new_story, new_audio = selected(prepared, cap)
    asr = audio_adapter.prepare_blind_asr_request(mp3(), api_key=NEW_KEY)
    if first == 'story': new_story.reserve(STORY_PURPOSE, prepared.new_request)
    else: new_audio.reserve(ASR, asr)
    before = _dump(prepared.client)
    rejected(lambda: new_story.reserve(VISUAL_PURPOSE, request('Actual retained frames', key=NEW_KEY)))
    rejected(lambda: new_story.reserve(STORY_PURPOSE, prepared.new_request))
    rejected(lambda: new_audio.reserve(ASR, asr))
    assert _dump(prepared.client) == before
    assert_legacy_preserved(prepared)


def test_matching_settlement_clears_unsettled_fence_without_resend(prepared):
    cap = commission(prepared)
    new_story, new_audio = selected(prepared, cap)
    new_story.reserve(STORY_PURPOSE, prepared.new_request)
    observed = new_story.settle(STORY_PURPOSE, prepared.new_request, story_response(prepared.new_request))
    assert observed.result == {'approved': False}
    asr = audio_adapter.prepare_blind_asr_request(mp3(), api_key=NEW_KEY)
    new_audio.reserve(ASR, asr)
    assert_legacy_preserved(prepared)


@pytest.mark.parametrize('action', ['story_commission', 'story_reconfirm', 'story_reserve', 'story_settle',
                                     'audio_commission', 'audio_reserve', 'audio_settle'])
def test_all_legacy_mutations_are_fenced_after_successor_commission(prepared, action):
    commission(prepared)
    before = _dump(prepared.client)
    operations = {
        'story_commission': lambda: prepared.old_story.commission(prepared.old_story_policy),
        'story_reconfirm': lambda: prepared.old_story.reconfirm_unused(prepared.story_policy),
        'story_reserve': lambda: prepared.old_story.reserve(VISUAL_PURPOSE, request('Other old body', key=KEY)),
        'story_settle': lambda: prepared.old_story.settle(STORY_PURPOSE, prepared.old_request, story_response(prepared.old_request)),
        'audio_commission': lambda: prepared.old_audio.commission(prepared.source.policy, source_metadata_bytes=prepared.source.metadata_bytes),
        'audio_reserve': lambda: prepared.old_audio.reserve(ASR, prepared.source.prepared),
        'audio_settle': lambda: prepared.old_audio.settle(ASR, prepared.source.prepared,
            audio_response(prepared.source.prepared, prepared.source.asr_result)),
    }
    rejected(operations[action])
    assert _dump(prepared.client) == before


@pytest.mark.parametrize('bad', [None, {}, {'authorized': True}, 'namespace', True])
def test_fake_capability_never_selects_successor_namespace(prepared, bad):
    commission(prepared)
    if bad is None:
        assert story.RouterReviewJournal(prepared.client).keys == OLD_KEYS[:3]
        assert audio.RouterAudioReviewJournal(prepared.client).keys == OLD_KEYS[3:]
    else:
        rejected(lambda: story.RouterReviewJournal(prepared.client, successor=bad))
        rejected(lambda: audio.RouterAudioReviewJournal(prepared.client, successor=bad))


def test_capability_private_constructor_and_read_only_namespace(prepared):
    cap = commission(prepared)
    rejected(lambda: successor.SuccessorAuthorization())
    for ledger in selected(prepared, cap):
        with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
            ledger.keys = ('external', 'namespace', 'injection')
    rejected(lambda: story.RouterReviewJournal(prepared.client, keys=('external',)*3))
    rejected(lambda: audio.RouterAudioReviewJournal(prepared.client, namespace='external'))
    fake = object.__new__(successor.SuccessorAuthorization)
    rejected(lambda: selected(prepared, fake))


@pytest.mark.parametrize('field,value', [
    ('version', True), ('kind', 'other'), ('candidate_id', '../../outside'),
    ('candidate_record_sha256', '0'*64), ('original_active_key_sha256', '0'*64),
    ('old_credential_sha256', '0'*64), ('new_credential_sha256', '0'*64),
    ('predecessor_snapshot_sha256', '0'*64), ('promotion_plan_sha256', 'bad'),
    ('old_key_binding', 'mathematically_proven'), ('historical_extra_cash_micro', 0),
    ('new_cash_allowance_micro', 1), ('new_cash_allowance_micro', False),
    ('account_plan_verified', True), ('completion_or_funding_verified', True),
])
def test_invalid_attestation_has_zero_new_records(prepared, field, value):
    rejected(lambda: commission(prepared, attestation={**prepared.attestation, field: value}))
    assert prepared.client.exists(*successor.ALL_KEYS) == 0
    assert _dump(prepared.client) == prepared.before


@pytest.mark.parametrize('damage', ['current_key', 'encryption', 'archive_missing', 'archive_expiring',
                                  'archive_changed', 'source_epoch', 'old_state', 'old_anchor'])
def test_current_archive_source_and_predecessor_drift_rejects_commission(prepared, damage):
    if damage == 'current_key': prepared.config.abacus_api_key = 'different-active-provider-key'
    elif damage == 'encryption': prepared.config.app_encryption_key = 'changed-material'
    elif damage == 'archive_missing': prepared.client.delete(prepared.archive_key)
    elif damage == 'archive_expiring': prepared.client.expire(prepared.archive_key, 300)
    elif damage == 'archive_changed': prepared.client.set(prepared.archive_key, 'changed-ciphertext')
    elif damage == 'source_epoch': prepared.client.incr(continuity._AUTH_EPOCH)
    elif damage == 'old_state': prepared.client.set(story.STATE_KEY, '{}')
    elif damage == 'old_anchor': prepared.client.set(audio.ANCHOR_KEY, '0'*64)
    before = _dump(prepared.client)
    rejected(lambda: commission(prepared))
    assert _dump(prepared.client) == before and prepared.client.exists(*successor.ALL_KEYS) == 0


@pytest.mark.parametrize('family,index', [('controller', 0), ('controller', 1), ('controller', 2),
                                        ('story', 0), ('story', 1), ('story', 2),
                                        ('audio', 0), ('audio', 1), ('audio', 2)])
@pytest.mark.parametrize('damage', ['missing', 'expiring'])
def test_every_new_durable_record_is_required_for_future_capability(prepared, family, index, damage):
    commission(prepared)
    keys = {'controller': (successor.STATE_KEY, successor.JOURNAL_KEY, successor.ANCHOR_KEY),
            'story': successor.STORY_KEYS, 'audio': successor.AUDIO_KEYS}[family]
    if damage == 'missing': prepared.client.delete(keys[index])
    else: prepared.client.expire(keys[index], 300)
    before = _dump(prepared.client)
    rejected(lambda: successor.read_credential_successor(prepared.client))
    assert _dump(prepared.client) == before


def test_lost_commission_ack_does_not_return_cap_or_repeat_write(prepared):
    def after(commands, ack):
        if any(name != 'PING' for name in commands):
            raise ConnectionError(SENTINEL)
        return ack
    client = Intercept(prepared.client, after=after)
    rejected(lambda: commission(prepared, client=client))
    assert sum(any(name != 'PING' for name in commands) for commands in client.executions) == 1
    assert all(prepared.client.exists(key) for key in successor.ALL_KEYS)
    assert_legacy_preserved(prepared)
    # Explicit later read recovery verifies durable records; it cannot replay the
    # commission or hand out a cached one-use reservation.
    recovered = successor.read_credential_successor(prepared.client)
    assert type(recovered) is successor.SuccessorAuthorization
    rejected(lambda: commission(prepared))


def test_read_ack_loss_returns_no_cap_and_never_writes(prepared):
    commission(prepared)
    before = _dump(prepared.client)
    def after(commands, ack):
        raise ConnectionError(SENTINEL)
    client = Intercept(prepared.client, after=after)
    rejected(lambda: successor.read_credential_successor(client))
    assert client.executions == [('PING',)] and _dump(prepared.client) == before


def test_predecessor_race_before_commission_exec_cannot_create_successor(prepared):
    raced = False
    def before(commands):
        nonlocal raced
        if any(name != 'PING' for name in commands):
            raced = True
            prepared.client.set(story.ANCHOR_KEY, '0'*64)
    client = Intercept(prepared.client, before=before)
    rejected(lambda: commission(prepared, client=client))
    assert raced and prepared.client.exists(*successor.ALL_KEYS) == 0


def test_context_change_during_commission_ack_cannot_return_usable_cap(prepared):
    def after(commands, ack):
        if any(name != 'PING' for name in commands):
            prepared.config.abacus_api_key = 'concurrent-promoted-key-change'
        return ack
    client = Intercept(prepared.client, after=after)
    rejected(lambda: commission(prepared, client=client))
    rejected(lambda: successor.read_credential_successor(prepared.client))
    assert_legacy_preserved(prepared)


def test_new_candidate_entry_is_never_deleted_by_successor_admission(prepared):
    candidate.stage_candidate(prepared.client, 'offline-next-provider-key-983f750ac')
    raw = prepared.client.get(candidate.CANDIDATE_KEY)
    commission(prepared)
    successor.read_credential_successor(prepared.client)
    assert prepared.client.get(candidate.CANDIDATE_KEY) == raw
    assert_legacy_preserved(prepared)


@pytest.mark.parametrize('family', ['story', 'audio'])
def test_complete_child_trio_rollback_cannot_clear_global_unknown_fence(prepared, family):
    cap = commission(prepared)
    new_story, new_audio = selected(prepared, cap)
    keys = successor.STORY_KEYS if family == 'story' else successor.AUDIO_KEYS
    initial = records(prepared.client, keys)
    if family == 'story':
        ledger, purpose, actual = new_story, STORY_PURPOSE, prepared.new_request
    else:
        ledger, purpose = new_audio, ASR
        actual = audio_adapter.prepare_blind_asr_request(mp3(), api_key=NEW_KEY)
    ledger.reserve(purpose, actual)
    others = records(prepared.client, set(successor.ALL_KEYS) - set(keys))
    # All three local child copies agree after this rollback. Surviving control
    # commitments must still remember that its actual request was reserved.
    for key, value in initial.items():
        prepared.client.restore(key, 0, value, replace=True)
    rejected(lambda: successor.read_credential_successor(prepared.client))
    rejected(lambda: ledger.reserve(purpose, actual))
    assert records(prepared.client, set(successor.ALL_KEYS) - set(keys)) == others
    assert_legacy_preserved(prepared)


def test_capability_manifest_mutation_cannot_select_any_journal(prepared):
    cap = commission(prepared)
    before = _dump(prepared.client)
    object.__setattr__(cap, '_manifest_bytes', b'{}')
    rejected(lambda: cap.receipt)
    rejected(lambda: selected(prepared, cap))
    assert _dump(prepared.client) == before


@pytest.mark.parametrize('family', ['story', 'audio'])
def test_control_head_pair_rollback_cannot_discard_a_surviving_child_reservation(prepared, family):
    cap = commission(prepared)
    new_story, new_audio = selected(prepared, cap)
    control_keys = (successor.JOURNAL_KEY, successor.ANCHOR_KEY)
    initial = records(prepared.client, control_keys)
    if family == 'story':
        ledger, purpose, actual = new_story, STORY_PURPOSE, prepared.new_request
    else:
        ledger, purpose = new_audio, ASR
        actual = audio_adapter.prepare_blind_asr_request(mp3(), api_key=NEW_KEY)
    ledger.reserve(purpose, actual)
    surviving = records(prepared.client, set(successor.ALL_KEYS) - set(control_keys))
    for key, value in initial.items():
        prepared.client.restore(key, 0, value, replace=True)
    rejected(lambda: successor.read_credential_successor(prepared.client))
    rejected(lambda: ledger.reserve(purpose, actual))
    assert records(prepared.client, set(successor.ALL_KEYS) - set(control_keys)) == surviving
    assert_legacy_preserved(prepared)


def first_selected_request(prepared, cap, family, *, client=None):
    new_story, new_audio = selected(prepared, cap, client=client)
    if family == 'story':
        return new_story, STORY_PURPOSE, prepared.new_request
    return new_audio, ASR, audio_adapter.prepare_blind_asr_request(mp3(), api_key=NEW_KEY)


@pytest.mark.parametrize('family', ['story', 'audio'])
@pytest.mark.parametrize('fault', ['lost', 'malformed'])
def test_uncertain_selected_reservation_ack_never_permits_any_second_reservation(prepared, family, fault):
    cap = commission(prepared)
    def after(commands, ack):
        if fault == 'lost':
            raise ConnectionError(SENTINEL)
        # A truthy integer is not the exact boolean acknowledgement of SET.
        return [*ack[:-1], 1]
    client = Intercept(prepared.client, after=after)
    ledger, purpose, actual = first_selected_request(prepared, cap, family, client=client)
    rejected(lambda: ledger.reserve(purpose, actual))
    assert len(client.executions) == 1
    durable = _dump(prepared.client)
    restored = successor.read_credential_successor(prepared.client)
    for other in ('story', 'audio'):
        next_ledger, next_purpose, next_request = first_selected_request(prepared, restored, other)
        rejected(lambda: next_ledger.reserve(next_purpose, next_request))
    assert _dump(prepared.client) == durable
    assert_legacy_preserved(prepared)


@pytest.mark.parametrize('family', ['story', 'audio'])
def test_lost_selected_settlement_ack_recovers_exact_observation_using_only_ping(prepared, family):
    cap = commission(prepared)
    ledger, purpose, actual = first_selected_request(prepared, cap, family)
    ledger.reserve(purpose, actual)
    received = (story_response(actual) if family == 'story'
                else audio_response(actual, prepared.source.asr_result))
    def after(commands, ack):
        raise ConnectionError(SENTINEL)
    lost = Intercept(prepared.client, after=after)
    lost_ledger, _, _ = first_selected_request(prepared, cap, family, client=lost)
    rejected(lambda: lost_ledger.settle(purpose, actual, received))
    assert len(lost.executions) == 1
    durable = _dump(prepared.client)
    restored = successor.read_credential_successor(prepared.client)
    readback = Intercept(prepared.client)
    read_ledger, _, _ = first_selected_request(prepared, restored, family, client=readback)
    result = read_ledger.settle(purpose, actual, received)
    assert (result.result if family == 'story' else result['result']) == (
        {'approved': False} if family == 'story' else prepared.source.asr_result)
    assert readback.executions == [('PING',)]
    assert _dump(prepared.client) == durable
    rejected(lambda: read_ledger.reserve(purpose, actual))
    assert_legacy_preserved(prepared)


@pytest.mark.parametrize('family', ['story', 'audio'])
def test_historical_settled_read_survives_expiry_but_never_source_drift_or_fresh_reserve(prepared, family):
    cap = commission(prepared)
    ledger, purpose, actual = first_selected_request(prepared, cap, family)
    ledger.reserve(purpose, actual)
    received = (story_response(actual) if family == 'story'
                else audio_response(actual, prepared.source.asr_result))
    ledger.settle(purpose, actual, received)
    ledger.clock = lambda: NOW + timedelta(days=1)

    def historical_read():
        with prepared.client.pipeline() as pipe:
            pipe.watch(*ledger.keys)
            value = ledger._read(pipe)
            pipe.multi()
            pipe.ping()
            assert pipe.execute() == [True]
            return value

    durable = _dump(prepared.client)
    state = historical_read()
    slot_name = purpose if family == 'story' else purpose.value
    assert state['slots'][slot_name]['response'] is not None
    assert successor.read_credential_successor(prepared.client).receipt == cap.receipt
    if family == 'story':
        rejected(lambda: ledger.reserve(VISUAL_PURPOSE, request('Actual retained frames', key=NEW_KEY)))
    else:
        next_request = prosody_request(prepared.source, api_key=NEW_KEY)
        rejected(lambda: ledger.reserve(PROSODY, next_request,
            expected_narration=prepared.source.expected, asr_result=prepared.source.asr_result))
    assert _dump(prepared.client) == durable
    prepared.client.incr(continuity._AUTH_EPOCH)
    drifted = _dump(prepared.client)
    rejected(historical_read)
    assert _dump(prepared.client) == drifted
    assert_legacy_preserved(prepared)
