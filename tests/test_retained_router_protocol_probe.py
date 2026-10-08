"""Real Redis admission for one diagnostic; old unknowns never become permits."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError

from app.services import retained_router_protocol_probe as probe
from app.services import abacus_router_protocol_diagnostic as transport
from app.services import retained_review_credential_successor as controller
from app.services import abacus_router_review_journal as story
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services import production_connection_continuity as continuity
from app.services.production_spend import SpendBlocked
import test_retained_review_credential_successor as history
from test_retained_review_credential_successor import prepared
from test_abacus_router_audio_review_journal import case, source, no_transport, NOW, ASR, mp3
from test_production_connection_continuity import _dump


HEAD = 'a' * 40
ALL_OLD = (*controller.LEGACY_KEYS, *controller.ALL_KEYS)


@pytest.fixture
def frozen_three(prepared, monkeypatch):
    cap = history.commission(prepared)
    selected, audio = history.selected(prepared, cap)
    selected.reserve(story.PURPOSES[0], prepared.new_request)
    failure = {**probe._FAILURE,
        'controller_manifest_sha256': probe._sha(prepared.client.get(controller.STATE_KEY).encode()),
        'story_state_sha256': probe._sha(prepared.client.get(controller.STORY_KEYS[0]).encode()),
        'audio_state_sha256': probe._sha(prepared.client.get(controller.AUDIO_KEYS[0]).encode())}
    monkeypatch.setattr(probe, '_FAILURE', failure)
    monkeypatch.setattr(probe, 'settings', prepared.config)
    monkeypatch.setattr(transport.spending, 'settings', prepared.config)
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', HEAD)
    attestation = {'version': 1, 'kind': 'explicit_frozen_protocol_diagnostic',
        'runtime_head_sha': HEAD, 'owner_authorization_sha256': 'b'*64,
        'operator_attempt_sha256': 'c'*64, **failure}
    return SimpleNamespace(**vars(prepared), cap=cap, selected=selected, audio=audio,
        probe_attestation=attestation, original_records=history.records(prepared.client, ALL_OLD),
        before_probe=_dump(prepared.client))


def scope(box, *, client=None, attestation=None, clock=None):
    return probe.protocol_diagnostic_probe_scope(client or box.client,
        attestation=box.probe_attestation if attestation is None else attestation,
        clock=clock or (lambda: NOW))


def assert_old_unchanged(box):
    assert history.records(box.client, ALL_OLD) == box.original_records


def test_readonly_context_cannot_mint_a_send_permit(frozen_three):
    box = frozen_three
    client = history.Intercept(box.client)
    context = probe.protocol_probe_admission_context(client, clock=lambda: NOW)
    assert context['prior_unknown_count'] == context['prior_occupied_count'] == 3
    assert context['occupied_count_after_probe'] == 4 <= context['max_total_attempts'] == 6
    assert context['probe_records_absent'] is True and context['send_authorized'] is False
    assert client.executions == [('PING',)]
    assert _dump(box.client) == box.before_probe
    assert context['failure_binding'] == probe._FAILURE
    for name in ('qa_approved', 'publish_eligible', 'resume_authorized', 'retry_authorized'):
        assert context[name] is False


def test_exactly_one_three_record_reservation_then_one_frozen_request(frozen_three):
    box = frozen_three
    client = history.Intercept(box.client)
    with scope(box, client=client) as permit:
        assert type(permit) is probe.ProtocolProbePermit
        assert client.executions[0] == ('SET', 'HSET', 'SET')
        assert set(_dump(box.client)) - set(box.before_probe) == set(probe.PROBE_KEYS)
        assert all(box.client.pttl(k) == -1 for k in probe.PROBE_KEYS)
        request = permit.take_request()
        wire = json.loads(request._body_bytes)
        assert wire['model'] == 'route-llm' and wire['max_tokens'] == 32
        assert wire['messages'][1]['content'] == [{'type': 'text', 'text': 'Return {"ok":true}.'}]
        assert wire['stream'] is False and wire['modalities'] == ['text']
        assert_old_unchanged(box)
        with pytest.raises(SpendBlocked):
            permit.take_request()
    with pytest.raises(SpendBlocked):
        permit.take_request()
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        with scope(box):
            pytest.fail('A durable diagnostic cannot mint a second permit')
    assert _dump(box.client) == before


@pytest.mark.parametrize('field', ['failure_proof_sha256', 'failure_attempt_id',
    'failure_source_head_sha', 'failure_source_tree_sha', 'controller_manifest_sha256',
    'story_state_sha256', 'audio_state_sha256', 'runtime_head_sha'])
def test_changed_failure_or_release_binding_creates_no_records(frozen_three, field):
    box = frozen_three
    value = deepcopy(box.probe_attestation)
    value[field] = 'changed'
    with pytest.raises(SpendBlocked):
        with scope(box, attestation=value):
            pytest.fail('Changed operator evidence cannot authorize a request')
    assert _dump(box.client) == box.before_probe


@pytest.mark.parametrize('damage', ['extra', 'boolean_version', 'owner_sha', 'attempt_sha'])
def test_attestation_is_closed_and_exact(frozen_three, damage):
    value = deepcopy(frozen_three.probe_attestation)
    if damage == 'extra': value['caller_prompt'] = 'Different request'
    elif damage == 'boolean_version': value['version'] = True
    elif damage == 'owner_sha': value['owner_authorization_sha256'] = False
    else: value['operator_attempt_sha256'] = 'not-a-digest'
    with pytest.raises(SpendBlocked):
        with scope(frozen_three, attestation=value):
            pytest.fail('Malformed authority cannot reserve')
    assert _dump(frozen_three.client) == frozen_three.before_probe


@pytest.mark.parametrize('index', range(3))
def test_any_partial_probe_record_is_terminal(frozen_three, index):
    box = frozen_three
    box.client.set(probe.PROBE_KEYS[index], 'partial')
    before = _dump(box.client)
    with pytest.raises(SpendBlocked):
        probe.protocol_probe_admission_context(box.client, clock=lambda: NOW)
    with pytest.raises(SpendBlocked):
        with scope(box):
            pytest.fail('Partial records never recover an admission')
    assert _dump(box.client) == before


@pytest.mark.parametrize('index', range(3))
def test_probe_presence_fences_otherwise_unused_normal_scope(prepared, index):
    cap = history.commission(prepared)
    selected, audio = history.selected(prepared, cap)
    prepared.client.set(probe.PROBE_KEYS[index], 'partial')
    before = _dump(prepared.client)
    with pytest.raises(SpendBlocked):
        selected.reserve(story.PURPOSES[0], prepared.new_request)
    with pytest.raises(SpendBlocked):
        audio.reserve(ASR, audio_adapter.prepare_blind_asr_request(mp3(), api_key=history.NEW_KEY))
    assert _dump(prepared.client) == before


@pytest.mark.parametrize('index', range(3))
def test_probe_presence_also_fences_legacy_guard_before_default_return(prepared, index):
    assert prepared.client.exists(*controller.ALL_KEYS) == 0
    prepared.client.set(probe.PROBE_KEYS[index], 'partial')
    before = _dump(prepared.client)
    with prepared.client.pipeline() as pipe:
        with pytest.raises(SpendBlocked):
            controller.guard_mutation(pipe, None, 'story', reserve=True)
    assert _dump(prepared.client) == before


@pytest.mark.parametrize('damage', ['enforcement', 'cash', 'key', 'head', 'expiry'])
def test_changed_preflight_keeps_all_probe_records_absent(frozen_three, monkeypatch, damage):
    box = frozen_three
    clock = lambda: NOW
    if damage == 'enforcement': box.config.studio_spend_enforcement = False
    elif damage == 'cash': box.config.studio_spend_policy_json = json.dumps({k: 1 for k in probe._CAPS})
    elif damage == 'key': box.config.abacus_api_key = 'different-current-provider-key'
    elif damage == 'head': monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'changed')
    else: clock = lambda: NOW + timedelta(hours=2)
    with pytest.raises(SpendBlocked):
        with scope(box, clock=clock):
            pytest.fail('Changed configuration or window cannot reserve')
    assert _dump(box.client) == box.before_probe


@pytest.mark.parametrize('damage', ['key', 'head', 'source', 'expiry', 'request'])
def test_drift_after_reservation_leaves_one_occupied_slot_without_send(frozen_three, monkeypatch, damage):
    box = frozen_three
    current = [NOW]
    with scope(box, clock=lambda: current[0]) as permit:
        if damage == 'key': box.config.abacus_api_key = 'different-current-provider-key'
        elif damage == 'head': monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'b'*40)
        elif damage == 'expiry': current[0] += timedelta(hours=2)
        elif damage == 'request': monkeypatch.setattr(transport, '_prepare_probe', lambda: box.new_request)
        else:
            box.client.set(controller.STORY_KEYS[0], 'changed')
        with pytest.raises(SpendBlocked):
            permit.take_request()
        assert json.loads(box.client.get(probe.PROBE_KEYS[0]))['capture'] is None
        with pytest.raises(SpendBlocked):
            permit.take_request()


@pytest.mark.parametrize('ack', [None, [1, 3, True], [True, True, True], [True, 3, 1]])
def test_lost_or_malformed_reservation_ack_never_yields_a_permit(frozen_three, ack):
    box = frozen_three
    def after(commands, result):
        return ack if commands == ('SET', 'HSET', 'SET') else result
    with pytest.raises(SpendBlocked):
        with scope(box, client=history.Intercept(box.client, after=after)):
            pytest.fail('An uncertain committed reservation cannot yield a permit')
    assert box.client.exists(*probe.PROBE_KEYS) == 3
    assert json.loads(box.client.get(probe.PROBE_KEYS[0]))['capture'] is None
    assert_old_unchanged(box)


def test_committed_reservation_with_connection_lost_never_yields(frozen_three):
    box = frozen_three
    def after(commands, result):
        if commands == ('SET', 'HSET', 'SET'):
            raise ConnectionError(history.SENTINEL)
        return result
    with pytest.raises(SpendBlocked) as error:
        with scope(box, client=history.Intercept(box.client, after=after)):
            pytest.fail('Lost reply is terminal')
    assert history.SENTINEL not in str(error.value)
    assert box.client.exists(*probe.PROBE_KEYS) == 3
    assert_old_unchanged(box)


@pytest.mark.parametrize('authority', ['legacy', 'controller', 'child', 'archive', 'probe'])
def test_watched_authority_race_cannot_reserve(frozen_three, authority):
    box = frozen_three
    key = {'legacy': controller.LEGACY_KEYS[0], 'controller': controller.STATE_KEY,
           'child': controller.STORY_KEYS[0], 'archive': box.archive_key,
           'probe': probe.PROBE_KEYS[0]}[authority]
    def before(commands):
        if commands == ('SET', 'HSET', 'SET'):
            box.client.set(key, 'raced')
    with pytest.raises(SpendBlocked):
        with scope(box, client=history.Intercept(box.client, before=before)):
            pytest.fail('WATCH race cannot reserve')
    assert box.client.exists(*probe.PROBE_KEYS) == (1 if authority == 'probe' else 0)


def test_nested_copied_and_constructed_permits_never_send(frozen_three):
    box = frozen_three
    with pytest.raises(TypeError):
        probe.ProtocolProbePermit()
    with scope(box) as permit:
        forged = object.__new__(probe.ProtocolProbePermit)
        object.__setattr__(forged, '_scope', permit._scope)
        with pytest.raises(SpendBlocked): forged.take_request()
        with pytest.raises(SpendBlocked):
            with scope(box): pytest.fail('Nested probe cannot reserve')
        assert permit.take_request().request_sha256 == transport._prepare_probe().request_sha256
    assert_old_unchanged(box)


def test_only_typed_capture_can_update_its_own_records(frozen_three):
    box = frozen_three
    with scope(box) as permit:
        permit.take_request()
        before = _dump(box.client)
        with pytest.raises(SpendBlocked):
            permit.record_capture({'captured': True, 'qa_approved': True})
        assert _dump(box.client) == before
    assert_old_unchanged(box)


@pytest.mark.parametrize('stage', ['readback', 'take_request'])
def test_uncertain_read_ack_cannot_authorize_send(frozen_three, stage):
    box = frozen_three
    reads = [0]
    def after(commands, result):
        if commands == ('PING',):
            reads[0] += 1
            if reads[0] == (1 if stage == 'readback' else 2):
                return [1]
        return result
    client = history.Intercept(box.client, after=after)
    with pytest.raises(SpendBlocked):
        with scope(box, client=client) as permit:
            assert stage == 'take_request'
            permit.take_request()
    assert box.client.exists(*probe.PROBE_KEYS) == 3
    assert json.loads(box.client.get(probe.PROBE_KEYS[0]))['capture'] is None
    assert_old_unchanged(box)


@pytest.mark.parametrize('index', range(3))
def test_reservation_losing_durability_cannot_yield(frozen_three, index):
    box = frozen_three
    def after(commands, result):
        if commands == ('SET', 'HSET', 'SET'):
            box.client.pexpire(probe.PROBE_KEYS[index], 300000)
        return result
    with pytest.raises(SpendBlocked):
        with scope(box, client=history.Intercept(box.client, after=after)):
            pytest.fail('A non-durable reservation cannot authorize a send')
    assert box.client.exists(*probe.PROBE_KEYS) == 3
    assert_old_unchanged(box)


def test_copied_context_on_other_thread_cannot_consume_owner_permit(frozen_three):
    with scope(frozen_three) as permit:
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ctx.run, permit.take_request)
            with pytest.raises(SpendBlocked):
                future.result()
        assert permit.take_request().request_sha256 == transport._prepare_probe().request_sha256
    assert_old_unchanged(frozen_three)


@pytest.mark.parametrize('value', [True, 'false', 0])
def test_changed_feature_flag_is_not_coerced(frozen_three, value):
    setattr(frozen_three.config, 'studio_abacus_router_retained_review_enabled', value)
    with pytest.raises(SpendBlocked):
        with scope(frozen_three):
            pytest.fail('Only exact false preserves the configured guard')
    assert _dump(frozen_three.client) == frozen_three.before_probe
