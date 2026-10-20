"""Prospective accounting uses real disposable history; no production admission."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_review_completion_plan as plan
from app.services import retained_router_protocol_probe as probe
from app.services import abacus_router_protocol_diagnostic as diagnostic
from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_review_journal as audio
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services.production_spend import SpendBlocked
import test_retained_review_credential_successor as history
from test_retained_review_credential_successor import prepared, Intercept
from test_retained_router_protocol_probe import frozen_three, scope
from test_abacus_router_protocol_diagnostic import wire, forbid_live_transport
from test_abacus_router_audio_review_journal import case, source, no_transport, NOW, ASR, PROSODY, mp3
import test_abacus_router_audio_review_journal as audio_fixture
import test_abacus_router_review_journal as story_fixture
from test_production_connection_continuity import _dump


@pytest.fixture
def completed_probe(frozen_three, wire):
    box = frozen_three
    wire.chunks = [b'{"choices":[{"index":0,"message":{"role":"assistant","content":"{\\"ok\\":true}"},"finish_reason":"stop"}],"model":"synthetic-model","usage":{"input_tokens":217,"output_tokens":9,"raw_input_tokens":217}}']
    with scope(box) as permit:
        diagnostic.run_protocol_diagnostic(permit)
    assert len(wire.calls) == 1
    snapshot = plan.snapshot_completion_predecessors(box.client)
    policies = {'story': deepcopy(box.story_policy), 'audio': deepcopy(box.audio_policy)}
    for policy in policies.values():
        policy.update(valid_from=history.stamp(NOW), valid_until=history.stamp(NOW+timedelta(hours=1)))
    attestation = {**plan._ATTESTATION_FIXED,
        **{name: 'e'*64 for name in plan._ATTESTATION_HASHES},
        'runtime_head_sha': box.probe_attestation['runtime_head_sha'],
        'predecessor_snapshot_sha256': snapshot['snapshot_sha256'],
        'completed_probe_capture_sha256': snapshot['probe_capture_sha256'],
        'current_credential_sha256': snapshot['credential_sha256'],
        'entitlement_evidence_sha256': policies['story']['entitlement_evidence_sha256']}
    return SimpleNamespace(**vars(box), completion_policies=policies,
        completion_attestation=attestation, completion_snapshot=snapshot,
        historical_records=history.records(box.client, plan.HISTORICAL_KEYS),
        before_completion=_dump(box.client), wire=wire)


def commission(box, *, client=None, **changes):
    return plan.commission_retained_completion_plan(client or box.client, **{
        'story_policy': box.completion_policies['story'], 'audio_policy': box.completion_policies['audio'],
        'source_metadata_bytes': box.source.metadata_bytes,
        'attestation': box.completion_attestation, 'clock': lambda: NOW, **changes})


def selected(box, cap, *, client=None, clock=None):
    options = {'completion_plan': cap, 'clock': clock or (lambda: NOW)}
    return (story.RouterReviewJournal(client or box.client, **options),
            audio.RouterAudioReviewJournal(client or box.client, **options))


def preserve(box):
    assert history.records(box.client, plan.HISTORICAL_KEYS) == box.historical_records
    assert len(box.wire.calls) == 1  # Only the fixture's diagnostic MockHTTPX POST.


def advance_story(box, ledger):
    for purpose in story.PURPOSES:
        request = story_fixture.request(purpose, key=history.NEW_KEY)
        ledger.reserve(purpose, request)
        # A protocol-observed negative remains negative, never a quality permit.
        observed = ledger.settle(purpose, request, story_fixture.response(request))
        assert observed.result == {'approved': False}


def test_snapshot_preserves_all_eighteen_records_and_all_four_liabilities(completed_probe):
    box = completed_probe
    client = Intercept(box.client)
    snapshot = plan.snapshot_completion_predecessors(client)
    assert snapshot == box.completion_snapshot
    assert len(snapshot['records']) == 18
    assert snapshot['prior_occupied_count'] == 4 and snapshot['prior_unknown_count'] == 3
    assert client.executions == [('PING',)]
    assert _dump(box.client) == box.before_completion
    preserve(box)


def test_explicit_commission_and_readback_use_only_nine_new_permanent_records(completed_probe):
    box = completed_probe
    client = Intercept(box.client)
    cap = commission(box, client=client)
    assert type(cap) is plan.CompletionPlanAuthorization
    assert set(_dump(box.client)) - set(box.before_completion) == set(plan.ALL_KEYS)
    assert all(box.client.pttl(k) == -1 for k in plan.ALL_KEYS)
    assert client.executions == [('SET','HSET','SET','SET','HSET','SET','SET','HSET','SET'), ('PING',)]
    receipt = cap.receipt
    assert receipt['prior_occupied_count'] == receipt['additional_attempt_limit'] == 4
    assert receipt['prior_unknown_count'] == 3 and receipt['max_total_attempts'] == 8
    assert receipt['pre_observer_capture_required'] is True
    assert receipt['source_bound_semantic_gates_required'] is True
    for name, expected in plan._FLAGS.items():
        assert receipt[name] is expected
    receipt['max_total_attempts'] = 100
    assert cap.receipt['max_total_attempts'] == 8
    assert plan.read_retained_completion_plan(box.client).receipt == cap.receipt
    assert history.NEW_KEY not in repr(cap) + str(cap.receipt)
    with pytest.raises(FrozenInstanceError):
        cap._manifest_bytes = b'changed'
    with pytest.raises(SpendBlocked):
        commission(box)
    preserve(box)


@pytest.mark.parametrize('index', range(9))
def test_every_partial_new_record_blocks_a_second_commission(completed_probe, index):
    box = completed_probe
    box.client.set(plan.ALL_KEYS[index], 'partial')
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        commission(box)
    assert _dump(box.client) == before
    preserve(box)


@pytest.mark.parametrize('index', range(18))
def test_each_historical_record_is_required_and_never_recreated(completed_probe, index):
    box = completed_probe
    box.client.delete(plan.HISTORICAL_KEYS[index])
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        commission(box)
    assert _dump(box.client) == before


@pytest.mark.parametrize('damage', ['bool_version','extra','wrong_snapshot','wrong_probe','wrong_key','head','cash'])
def test_closed_attestation_cannot_change_source_limits_or_contract(completed_probe, damage):
    box = completed_probe
    value = deepcopy(box.completion_attestation)
    field = {'bool_version':'version','extra':'extra','wrong_snapshot':'predecessor_snapshot_sha256',
             'wrong_probe':'completed_probe_capture_sha256','wrong_key':'current_credential_sha256',
             'head':'runtime_head_sha','cash':'included_cash_allowance_micro'}[damage]
    value[field] = True if damage == 'bool_version' else 1 if damage == 'cash' else '0'*64
    with pytest.raises(SpendBlocked):
        commission(box, attestation=value)
    assert _dump(box.client) == box.before_completion


@pytest.mark.parametrize('fault', ['lost','short','bool_count','readback_lost','after_key_change'])
def test_commission_unknown_ack_is_terminal_without_success_or_retry(completed_probe, fault):
    box = completed_probe
    def after(commands, result):
        if len(commands) == 9:
            if fault == 'lost': raise ConnectionError('PRIVATE_STORAGE_FAILURE')
            if fault == 'short': return result[:-1]
            if fault == 'bool_count': result[1] = True
            if fault == 'after_key_change': box.config.abacus_api_key = 'changed-valid-looking-key'
        elif fault == 'readback_lost': raise ConnectionError('PRIVATE_STORAGE_FAILURE')
        return result
    client = Intercept(box.client, after=after)
    with pytest.raises(SpendBlocked) as caught:
        commission(box, client=client)
    assert 'PRIVATE_STORAGE_FAILURE' not in str(caught.value)
    assert box.client.exists(*plan.ALL_KEYS) == 9
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        commission(box)
    assert _dump(box.client) == before
    preserve(box)


@pytest.mark.parametrize('value', [True, {}, None, object()])
def test_authority_rejects_nonissued_types(value):
    with pytest.raises(SpendBlocked):
        plan.selected_keys(value, 'story')


def test_subclass_and_unsealed_copy_cannot_select_a_namespace(completed_probe):
    cap = commission(completed_probe)
    class Child(plan.CompletionPlanAuthorization):
        pass
    for cls in (Child, plan.CompletionPlanAuthorization):
        fake = object.__new__(cls)
        object.__setattr__(fake, '_manifest_bytes', cap._manifest_bytes)
        object.__setattr__(fake, '_seal', object())
        with pytest.raises(SpendBlocked):
            selected(completed_probe, fake)
    with pytest.raises(TypeError):
        plan.CompletionPlanAuthorization()
    with pytest.raises(SpendBlocked):
        story.RouterReviewJournal(completed_probe.client, successor=completed_probe.cap, completion_plan=cap)


def test_actual_journals_enforce_all_four_ordered_acknowledged_purposes(completed_probe):
    box = completed_probe
    cap = commission(box)
    first, second = selected(box, cap)
    asr = audio_adapter.prepare_blind_asr_request(mp3(), api_key=history.NEW_KEY)
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        second.reserve(ASR, asr)
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[1], story_fixture.request('visual first', key=history.NEW_KEY))
    assert _dump(box.client) == before
    advance_story(box, first)
    second.reserve(ASR, asr)
    second.settle(ASR, asr, audio_fixture.response(asr, box.source.asr_result))
    prosody = audio_fixture.prosody(box.source, api_key=history.NEW_KEY)
    second.reserve(PROSODY, prosody, expected_narration=box.source.expected, asr_result=box.source.asr_result)
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[0], box.new_request)
    with box.client.pipeline() as pipe:
        _, states, _, _ = plan._read_control(pipe)
        assert len(plan._ordered(states)) == 4
    assert cap.receipt['semantic_acceptance_verified'] is False
    preserve(box)


@pytest.mark.parametrize('fault', ['lost','malformed'])
def test_lost_reserve_ack_fences_all_new_scopes_and_preserves_old_unknowns(completed_probe, fault):
    box = completed_probe
    cap = commission(box)
    def after(commands, result):
        if len(commands) == 5:
            if fault == 'lost': raise ConnectionError('private')
            return result[:-1]
        return result
    first, _ = selected(box, cap, client=Intercept(box.client, after=after))
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[0], box.new_request)
    first, second = selected(box, plan.read_retained_completion_plan(box.client))
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[1], story_fixture.request('later visual', key=history.NEW_KEY))
    with pytest.raises(SpendBlocked):
        second.reserve(ASR, audio_adapter.prepare_blind_asr_request(mp3(), api_key=history.NEW_KEY))
    assert _dump(box.client) == before
    preserve(box)


@pytest.mark.parametrize('part', ['story','control'])
def test_independent_child_or_control_rollback_is_rejected(completed_probe, part):
    box = completed_probe
    cap = commission(box)
    keys = plan.STORY_KEYS if part == 'story' else (plan.JOURNAL_KEY, plan.ANCHOR_KEY)
    snapshot = {key: box.client.dump(key) for key in keys}
    first, _ = selected(box, cap)
    first.reserve(story.PURPOSES[0], box.new_request)
    for key, raw in snapshot.items():
        box.client.restore(key, 0, raw, replace=True)
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        plan.read_retained_completion_plan(box.client)
    assert _dump(box.client) == before
    preserve(box)


def test_old_expired_windows_remain_history_but_new_expired_window_cannot_send(completed_probe):
    box = completed_probe
    cap = commission(box)
    first, _ = selected(box, cap, clock=lambda: NOW+timedelta(hours=2))
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[0], box.new_request)
    assert _dump(box.client) == before
    assert plan.read_retained_completion_plan(box.client).receipt == cap.receipt
    preserve(box)


def test_default_successor_and_selected_commission_stay_fenced(completed_probe):
    box = completed_probe
    cap = commission(box)
    first, second = selected(box, cap)
    before = _dump(box.client)
    for action in (lambda: first.commission(box.completion_policies['story']),
                   lambda: first.reconfirm_unused(box.completion_policies['story']),
                   lambda: second.commission(box.completion_policies['audio'], source_metadata_bytes=box.source.metadata_bytes),
                   lambda: box.selected.reserve(story.PURPOSES[1], box.new_request),
                   lambda: box.old_story.reserve(story.PURPOSES[1], box.new_request)):
        with pytest.raises(SpendBlocked): action()
    assert _dump(box.client) == before
    preserve(box)


@pytest.mark.parametrize('damage', ['key', 'cash', 'head', 'source'])
def test_scope_and_reserve_recheck_current_context_without_new_writes(completed_probe, monkeypatch, damage):
    box = completed_probe
    cap = commission(box)
    if damage == 'key': box.config.abacus_api_key = 'different-current-provider-key'
    elif damage == 'cash': box.config.studio_spend_policy_json = plan._raw({k:1 for k in probe._CAPS}).decode()
    elif damage == 'head': monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'b'*40)
    else:
        box.client.set(history.continuity._PROFILE + history.continuity.CHANNEL_ID, '{}')
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        plan.verify_scope_completion_plan(box.client, cap)
    first, _ = selected(box, cap)
    with pytest.raises(SpendBlocked):
        first.reserve(story.PURPOSES[0], box.new_request)
    assert _dump(box.client) == before


@pytest.mark.parametrize('part', ['audio', 'control'])
def test_audio_child_and_independent_head_rollback_are_both_rejected(completed_probe, part):
    box = completed_probe
    cap = commission(box)
    first, second = selected(box, cap)
    advance_story(box, first)
    keys = plan.AUDIO_KEYS if part == 'audio' else (plan.JOURNAL_KEY, plan.ANCHOR_KEY)
    snapshot = {key: box.client.dump(key) for key in keys}
    second.reserve(ASR, audio_adapter.prepare_blind_asr_request(mp3(), api_key=history.NEW_KEY))
    for key, raw in snapshot.items(): box.client.restore(key, 0, raw, replace=True)
    before = _dump(box.client)
    with pytest.raises(SpendBlocked): plan.read_retained_completion_plan(box.client)
    assert _dump(box.client) == before
    preserve(box)


@pytest.mark.parametrize('index', [0, 4, 8])
def test_selected_state_or_control_must_never_expire(completed_probe, index):
    box = completed_probe
    commission(box)
    box.client.pexpire(plan.ALL_KEYS[index], 60_000)
    with pytest.raises(SpendBlocked): plan.read_retained_completion_plan(box.client)
    assert box.client.pttl(plan.ALL_KEYS[index]) > 0
    preserve(box)


def test_lost_settlement_ack_recovers_only_exact_ping_readback_without_a_new_slot(completed_probe):
    box = completed_probe
    cap = commission(box)
    first, _ = selected(box, cap)
    first.reserve(story.PURPOSES[0], box.new_request)
    response = story_fixture.response(box.new_request)
    def after(commands, result):
        if len(commands) == 5: raise ConnectionError('private')
        return result
    broken, _ = selected(box, cap, client=Intercept(box.client, after=after))
    with pytest.raises(SpendBlocked): broken.settle(story.PURPOSES[0], box.new_request, response)
    client = Intercept(box.client)
    recovered, _ = selected(box, cap, client=client)
    before = _dump(box.client)
    assert recovered.settle(story.PURPOSES[0], box.new_request, response).result == {'approved':False}
    assert client.executions == [('PING',)] and _dump(box.client) == before
    preserve(box)


def test_competing_explicit_commission_wins_once_without_overwriting_its_plan(completed_probe):
    box = completed_probe
    winner = []
    def before(commands):
        if len(commands) == 9 and not winner:
            winner.append(commission(box))
    with pytest.raises(SpendBlocked):
        commission(box, client=Intercept(box.client, before=before))
    assert len(winner) == 1
    assert plan.read_retained_completion_plan(box.client).receipt == winner[0].receipt
    assert box.client.exists(*plan.ALL_KEYS) == 9
    preserve(box)


def test_source_drift_during_commission_watch_never_creates_new_records(completed_probe):
    box = completed_probe
    def before(commands):
        if len(commands) == 9:
            box.client.set(history.continuity._PROFILE + history.continuity.CHANNEL_ID, '{}')
    with pytest.raises(SpendBlocked):
        commission(box, client=Intercept(box.client, before=before))
    assert box.client.exists(*plan.ALL_KEYS) == 0
    preserve(box)
