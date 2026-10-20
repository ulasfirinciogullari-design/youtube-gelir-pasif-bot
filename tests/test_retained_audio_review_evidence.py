"""Persisted audio verification using synthetic MP3, actual HTTPX and Redis WATCH."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import retained_audio_review_evidence as reader
from app.services import abacus_router_audio_review_journal as journal
from app.services import abacus_router_audio_adapter as adapter
from app.services import production_connection_continuity as continuity
from app.services import retained_router_audio_qa as bridge
from app.services import retained_review_credential_successor as successor
import test_retained_review_credential_successor as successor_cases
from test_retained_review_credential_successor import prepared as successor_prepared
from test_abacus_router_audio_review_journal import (
    case, source, commission, response, prosody, mp3, state, ASR, PROSODY,
    canonical, digest, sha,
)
from test_production_connection_continuity import _dump
from test_production_credit_ledger import InterceptClient


FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False}
ENDPOINT = 'https://t3.storageapi.dev'
ADMINS = {'Grantee': {'Type': 'Group', 'URI': 'https://groups.tigris.dev/org/admins'},
          'Permission': 'FULL_CONTROL'}


class ReadOnlyS3:
    def __init__(self):
        self.objects, self.gets, self.acls, self.bodies = {}, [], [], []
        self.extra_grants = []
        self.patch = None
        self.meta = SimpleNamespace(endpoint_url=ENDPOINT)

    def get_object(self, *, Bucket, Key):
        assert Bucket == 'private-fixture'
        self.gets.append(Key)
        raw = self.objects[Key]
        body = BytesIO(raw)
        self.bodies.append(body)
        result = {'Body': body, 'ContentLength': len(raw),
                  'ContentType': 'audio/mpeg' if Key.endswith('.mp3') else 'application/json',
                  'ResponseMetadata': {'HTTPStatusCode': 200}}
        if self.patch:
            self.patch(Key, result)
        return result

    def get_object_acl(self, *, Bucket, Key):
        assert Bucket == 'private-fixture'
        self.acls.append(Key)
        return {'Owner': {'ID': 'synthetic-owner'}, 'Grants': [
            {'Grantee': {'Type': 'CanonicalUser', 'ID': 'synthetic-owner'},
             'Permission': 'FULL_CONTROL'}, *deepcopy(self.extra_grants)],
            'ResponseMetadata': {'HTTPStatusCode': 200}}


def review(ledger, prepared, parsed, source, *, asr_response=None):
    reservation = ledger.reserve(prepared.purpose, prepared, **({
        'expected_narration': source.expected, 'asr_result': source.asr_result}
        if prepared.purpose is PROSODY else {}))
    received = response(prepared, parsed)
    settlement = ledger.settle(prepared.purpose, prepared, received)
    observed = adapter.observe_audio_router_response(prepared, received)
    if prepared.purpose is ASR:
        diagnostic = bridge.validate_retained_router_asr(prepared, received, observed,
            expected_narration=source.expected, original_audio=prepared.audio)
    else:
        diagnostic = bridge.validate_retained_router_prosody(prepared, received, observed,
            asr_prepared=source.prepared, asr_response=asr_response,
            asr_observed=adapter.observe_audio_router_response(source.prepared, asr_response),
            expected_narration=source.expected, original_audio=prepared.audio)
    return {'reservation': reservation, 'settlement': settlement, 'diagnostic': diagnostic}, received


def save_artifact(box, kind):
    """Fixture materializes the documented v1 sink format; no production writes."""
    record = box.records[kind]
    raw = canonical(record).encode()
    pointer = {'key': f'recovery/{continuity.LEAF_ID}/included_router_audio_review/{kind}/{sha(raw)}.json',
               'sha256': sha(raw), 'size': len(raw), 'content_type': 'application/json'}
    box.s3.objects[pointer['key']] = raw
    current = json.loads(box.case.client.get(box.journal.state_key))
    asr_slot = current['slots'][ASR.value]
    snapshot = {'policy': current['policy'], 'slots': {ASR.value: asr_slot},
                'updated_at': asr_slot['response']['observed_at']} if kind == 'asr' else current
    anchor = {'version': 1, 'kind': kind, 'source_task_id': continuity.LEAF_ID,
        'original_task_id': continuity.ROOT_ID, 'policy_sha256': digest(current['policy']),
        'continuity_sha256': current['policy']['continuity_sha256'],
        'journal_state_sha256': digest(snapshot), 'pointer': pointer,
        'asr_anchor_sha256': None if kind == 'asr' else digest(box.anchors['asr']), **FLAGS}
    box.anchors[kind] = anchor
    prefix = box.journal.state_key.rsplit(':', 1)[0]
    box.case.client.set(prefix + ':' + kind + '_artifact', canonical(anchor))


@pytest.fixture
def complete(case, source, monkeypatch):
    monkeypatch.setattr(reader.storage, 'settings', SimpleNamespace(endpoint=ENDPOINT, bucket='private-fixture'))
    source.policy['valid_from'] = '2020-09-12T15:00:00Z'
    source.policy['valid_until'] = '2020-09-13T00:00:00Z'
    ledger = commission(case, source, now=datetime(2020, 9, 12, 16, tzinfo=timezone.utc))
    asr, asr_response = review(ledger, source.prepared, source.asr_result, source)
    first = {'version': 1, 'kind': 'asr', 'source': {
        'source_task_id': continuity.LEAF_ID, 'policy_sha256': digest(source.policy),
        'continuity_sha256': source.policy['continuity_sha256'], 'expected_narration': source.expected,
        'expected_narration_sha256': source.policy['expected_narration_sha256'],
        'audio': source.prepared.audio}, 'reviews': {ASR.value: asr}, **FLAGS}
    output = {'pass': True, 'summary': 'Clear synthetic delivery.', 'scores': {
        'pronunciation': 80, 'naturalness': 80, 'pacing': 80, 'sentence_flow': 80,
        'emphasis': 80, 'roboticness': 20}, 'issues': []}
    second, _ = review(ledger, prosody(source), output, source, asr_response=asr_response)
    final = {**deepcopy(first), 'kind': 'final', 'reviews': {**deepcopy(first['reviews']), PROSODY.value: second}}
    box = SimpleNamespace(case=case, source=source, journal=ledger, s3=ReadOnlyS3(),
                          records={'asr': first, 'final': final}, anchors={})
    job = json.loads(case.client.get(continuity._JOB + continuity.LEAF_ID))
    pointer = job['audio_candidate_checkpoint']
    box.s3.objects[pointer['metadata_key']] = source.metadata_bytes
    box.s3.objects[pointer['audio_key']] = mp3()
    save_artifact(box, 'asr')
    save_artifact(box, 'final')
    return box


def read(box, *, client=None, successor=None):
    return reader.read_retained_audio_review_evidence(
        box.case.client if client is None else client, box.s3, bucket='private-fixture', successor=successor)


@pytest.mark.parametrize('args,kwargs', [((), {}), ((b'{}',), {}), ((), {'_record_bytes': b'{}'})])
def test_normal_constructor_cannot_manufacture_verified_component(args, kwargs):
    with pytest.raises(TypeError):
        reader.RetainedAudioReviewEvidence(*args, **kwargs)


def rewrite_state(box, mutate):
    current = state(box.case)
    mutate(current)
    box.case.client.set(journal.STATE_KEY, canonical(current))
    box.case.client.delete(journal.JOURNAL_KEY)
    box.case.client.hset(journal.JOURNAL_KEY, mapping=journal._journal(current))
    box.case.client.set(journal.ANCHOR_KEY, digest(current))
    return current


def test_expired_positive_evidence_is_read_only_immutable_component(complete, monkeypatch):
    box = complete
    # Both real reservations are six years old; the reader must not consult
    # current request entitlement or manufacture a historical clock.
    blocked = Mock(side_effect=AssertionError('No request, observation, clock or write is allowed.'))
    for name in ('commission', 'reserve', 'settle', '_fresh', '_now', '_commit'):
        monkeypatch.setattr(journal.RouterAudioReviewJournal, name, blocked)
    for name in ('prepare_blind_asr_request', 'prepare_audio_prosody_request', 'observe_audio_router_response'):
        monkeypatch.setattr(adapter, name, blocked)
    for name in ('post', 'get', 'request', 'Client', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, blocked)
    before = _dump(box.case.client)
    actual_pipeline = box.case.client.pipeline
    executions = []
    def pipeline():
        pipe = actual_pipeline()
        execute = pipe.execute
        def checked_execute():
            assert [args for args, _ in pipe.command_stack] == [('PING',)]
            executions.append(True)
            return execute()
        pipe.execute = checked_execute
        return pipe
    monkeypatch.setattr(box.case.client, 'pipeline', pipeline)
    value = read(box)
    assert type(value) is reader.RetainedAudioReviewEvidence
    assert value.component_pass and value.diagnostic_only
    assert not any((value.qa_approved, value.publish_eligible, value.full_qa_complete,
                    value.edit_duration_qa_complete))
    detached = value.diagnostics
    detached['reviews'][ASR.value]['settlement']['result']['text'] = 'changed'
    assert value.diagnostics['reviews'][ASR.value]['settlement']['result']['text'] == box.source.expected
    with pytest.raises(FrozenInstanceError):
        value._record_bytes = b'changed'
    assert box.source.expected not in repr(value)
    assert value.diagnostics['journal_read_acknowledged'] is True
    assert value.diagnostics['journal_state_sha256'] == digest(state(box.case))
    assert _dump(box.case.client) == before and executions == [True]
    assert len(box.s3.gets) == 4 and len(box.s3.acls) == 2 and all(body.closed for body in box.s3.bodies)
    blocked.assert_not_called()


@pytest.mark.parametrize('change', ['checkpoint', 'profile', 'oauth'])
def test_changed_authoritative_source_rejected_before_storage(complete, change):
    client = complete.case.client
    if change == 'checkpoint':
        key = continuity._JOB + continuity.LEAF_ID
        value = json.loads(client.get(key))
        value['audio_candidate_checkpoint']['metadata_sha256'] = 'f' * 64
    elif change == 'profile':
        key = continuity._PROFILE + continuity.CHANNEL_ID
        value = json.loads(client.get(key))
        value['default_language'] = 'en'
    else:
        key = continuity._CHANNEL + continuity.CHANNEL_ID
        value = json.loads(client.get(key))
        value['connection_id'] = 'replacement-connection'
    client.set(key, canonical(value))
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)
    assert complete.s3.gets == []


def test_current_full_decode_descriptor_is_rechecked(complete, monkeypatch):
    inspect = reader.inspect_bounded_short_audio
    def changed(raw, mime_type):
        measured = inspect(raw, mime_type)
        measured['decoded_pcm_sha256'] = 'f' * 64
        return measured
    monkeypatch.setattr(reader, 'inspect_bounded_short_audio', changed)
    with pytest.raises(reader.RetainedAudioEvidenceError, match='original_audio_changed'):
        read(complete)


@pytest.mark.parametrize('score,value', [('naturalness', 69), ('roboticness', 31)])
def test_durable_full_schema_negative_prosody_does_not_pass(complete, score, value):
    saved = complete.records['final']['reviews'][PROSODY.value]
    parsed = saved['settlement']['result']
    parsed['scores'][score] = value
    parsed['pass'] = False
    parsed['issues'] = [{'code': 'mispronunciation', 'phrase': 'Merhaba',
        'start_seconds': .1, 'end_seconds': .3, 'detail': 'The opening is unclear.'}]
    evidence = saved['settlement']['evidence']
    evidence['parsed_result_sha256'] = digest(parsed)
    evidence['response_proof_sha256'] = digest({k: v for k, v in evidence.items() if k != 'response_proof_sha256'})
    diagnostic = saved['diagnostic']
    checked = reader.audio_qc._validate_prosody_review(parsed, complete.source.expected,
        audio_duration_seconds=diagnostic['audio_duration_seconds'],
        transcript_evidence=diagnostic['asr_comparison'], language='tr', provider='abacus_router')
    assert checked is not None and checked['pass'] is False
    checked['review_attempts'] = 1
    diagnostic.update(component_pass=False, prosody=checked, observation=deepcopy(evidence))
    rewrite_state(complete, lambda current: current['slots'][PROSODY.value]['response'].update(evidence=evidence))
    save_artifact(complete, 'final')
    with pytest.raises(reader.RetainedAudioEvidenceError, match='prosody_rejected'):
        read(complete)


@pytest.mark.parametrize('key', [*reader._WATCH])
@pytest.mark.parametrize('damage', ['missing', 'ttl'])
def test_partial_or_expiring_permanent_records_fail(complete, key, damage):
    if damage == 'missing':
        complete.case.client.delete(key)
    else:
        complete.case.client.pexpire(key, 60000)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)


@pytest.mark.parametrize('purpose', [ASR, PROSODY])
def test_unknown_response_never_reads_storage_or_grants_component(complete, purpose):
    def mutate(current):
        current['slots'][purpose.value]['response'] = None
        if purpose is ASR:
            current['slots'].pop(PROSODY.value)
    rewrite_state(complete, mutate)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)
    assert complete.s3.gets == []


@pytest.mark.parametrize('kind,field,value', [
    ('asr', 'journal_state_sha256', 'a' * 64), ('final', 'journal_state_sha256', 'b' * 64),
    ('asr', 'asr_anchor_sha256', 'a' * 64), ('final', 'asr_anchor_sha256', 'b' * 64),
    ('final', 'version', True), ('final', 'qa_approved', True),
    ('asr', 'policy_sha256', 'a' * 64), ('final', 'continuity_sha256', 'b' * 64),
])
def test_anchor_history_and_identity_must_match(complete, kind, field, value):
    anchor = deepcopy(complete.anchors[kind]); anchor[field] = value
    key = reader.ASR_ANCHOR_KEY if kind == 'asr' else reader.FINAL_ANCHOR_KEY
    complete.case.client.set(key, canonical(anchor))
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)


@pytest.mark.parametrize('target', ['metadata', 'audio', 'asr', 'final'])
@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_missing_or_changed_content_hash_blob_rejects(complete, target, damage):
    key = (complete.anchors[target]['pointer']['key'] if target in ('asr', 'final') else
           next(key for key in complete.s3.objects if key.endswith('.mp3') == (target == 'audio')
                and key.startswith('audio_candidates/')))
    if damage == 'missing':
        del complete.s3.objects[key]
    else:
        complete.s3.objects[key] += b'changed'
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)
    assert all(body.closed for body in complete.s3.bodies)


@pytest.mark.parametrize('mutation', ['parsed', 'reservation', 'rubric', 'schema', 'expected', 'flags'])
def test_self_consistent_blob_hash_does_not_replace_journal_or_diagnostic_proof(complete, mutation):
    record = complete.records['final']
    review = record['reviews'][PROSODY.value]
    if mutation == 'parsed':
        review['settlement']['result']['scores']['naturalness'] = 100
    elif mutation == 'reservation':
        review['reservation']['reservation_sha256'] = 'f' * 64
    elif mutation in ('rubric', 'schema'):
        review['diagnostic'][mutation + '_sha256'] = 'f' * 64
    elif mutation == 'expected':
        record['source']['expected_narration'] = 'forged narration'
    else:
        review['diagnostic']['qa_approved'] = True
    save_artifact(complete, 'final')
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)


def test_prosody_asr_comparison_hash_is_recomputed_even_with_consistent_receipts(complete):
    def mutate(current):
        slot = current['slots'][PROSODY.value]
        slot['asr_binding']['comparison_sha256'] = 'f' * 64
        slot['response']['reservation_sha256'] = digest(journal._receipt(current['policy'], PROSODY.value, slot))
    current = rewrite_state(complete, mutate)
    receipt = journal._receipt(current['policy'], PROSODY.value, current['slots'][PROSODY.value])
    complete.records['final']['reviews'][PROSODY.value]['reservation'] = {
        **receipt, 'reservation_sha256': digest(receipt), **FLAGS}
    save_artifact(complete, 'final')
    with pytest.raises(reader.RetainedAudioEvidenceError, match='asr_binding_changed'):
        read(complete)


@pytest.mark.parametrize('change', ['heard_words', 'missing_words', 'zero_width', 'outside_audio'])
def test_asr_semantics_recomputed_despite_consistent_parsed_and_durable_hashes(complete, change):
    original = complete.records['asr']['reviews'][ASR.value]
    parsed = original['settlement']['result']
    if change == 'heard_words':
        parsed['words'][0]['word'] = 'Selam'
        parsed['text'] = 'Selam ' + ' '.join(complete.source.metadata['voice']['spoken_texts'][1:])
    elif change == 'missing_words':
        parsed['words'] = parsed['words'][1:]
    elif change == 'zero_width':
        parsed['words'][0]['end'] = parsed['words'][0]['start']
    else:
        parsed['words'][-1]['end'] = 30.0
    evidence = original['settlement']['evidence']
    evidence['parsed_result_sha256'] = digest(parsed)
    evidence['response_proof_sha256'] = digest({k: v for k, v in evidence.items() if k != 'response_proof_sha256'})
    original['diagnostic']['observation'] = deepcopy(evidence)
    complete.records['final']['reviews'][ASR.value] = deepcopy(original)
    def mutate(current):
        current['slots'][ASR.value]['response']['evidence'] = deepcopy(evidence)
        slot = current['slots'][PROSODY.value]
        slot['asr_binding'].update(parsed_result_sha256=evidence['parsed_result_sha256'],
                                   response_proof_sha256=evidence['response_proof_sha256'])
        slot['response']['reservation_sha256'] = digest(journal._receipt(current['policy'], PROSODY.value, slot))
    current = rewrite_state(complete, mutate)
    receipt = journal._receipt(current['policy'], PROSODY.value, current['slots'][PROSODY.value])
    complete.records['final']['reviews'][PROSODY.value]['reservation'] = {
        **receipt, 'reservation_sha256': digest(receipt), **FLAGS}
    save_artifact(complete, 'asr')
    save_artifact(complete, 'final')
    # The structural journal validator accepts the self-consistent stored hashes;
    # the consumer must still reject actual transcript/timing semantics.
    with complete.case.client.pipeline() as pipe:
        pipe.watch(journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY)
        complete.journal._read(pipe)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)


@pytest.mark.parametrize('key', [reader.ASR_ANCHOR_KEY, reader.FINAL_ANCHOR_KEY, journal.STATE_KEY,
                                continuity._JOB + continuity.LEAF_ID])
def test_watch_race_has_no_second_attempt(complete, key):
    def change(_):
        raw = complete.case.client.get(key)
        complete.case.client.set(key, raw)
    client = InterceptClient(complete.case.client, before=change)
    with pytest.raises(reader.RetainedAudioEvidenceError, match='read_unverified'):
        read(complete, client=client)
    assert client.calls == 1


@pytest.mark.parametrize('ack', [None, [], [False], [1], [True, True], 'lost'])
def test_uncertain_read_ack_is_terminal_without_retry(complete, ack):
    def outcome(_, result):
        if ack == 'lost':
            raise ConnectionError('private details must not escape')
        return ack
    client = InterceptClient(complete.case.client, after=outcome)
    before = _dump(complete.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError) as exc:
        read(complete, client=client)
    assert 'private details' not in str(exc.value)
    assert client.calls == 1 and _dump(complete.case.client) == before


@pytest.mark.parametrize('extra', [[ADMINS], [], [ADMINS, ADMINS],
    [{'Grantee': {'Type': 'Group', 'URI': 'https://groups.tigris.dev/other'}, 'Permission': 'FULL_CONTROL'}],
    [{'Grantee': {'Type': 'Group', 'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}],
    [{**ADMINS, 'Permission': 'READ'}]])
def test_private_acl_accepts_only_owner_and_bound_tigris_admins(complete, extra):
    complete.s3.extra_grants = extra
    if extra in ([], [ADMINS]):
        assert read(complete).component_pass
    else:
        with pytest.raises(reader.RetainedAudioEvidenceError, match='not_private'):
            read(complete)


@pytest.mark.parametrize('actual,configured', [
    ('https://untrusted.example', ENDPOINT), (ENDPOINT, 'https://t3.storage.dev')])
def test_tigris_admins_require_actual_configured_endpoint(complete, monkeypatch, actual, configured):
    complete.s3.extra_grants = [ADMINS]
    complete.s3.meta.endpoint_url = actual
    monkeypatch.setattr(reader.storage, 'settings', SimpleNamespace(endpoint=configured, bucket='private-fixture'))
    with pytest.raises(reader.RetainedAudioEvidenceError, match='storage_changed'):
        read(complete)


def test_wrong_bucket_fails_before_reads(complete, monkeypatch):
    monkeypatch.setattr(reader.storage, 'settings', SimpleNamespace(endpoint=ENDPOINT, bucket='other-bucket'))
    with pytest.raises(reader.RetainedAudioEvidenceError, match='storage_changed'):
        read(complete)
    assert complete.s3.gets == []


@pytest.mark.parametrize('field,value', [('ContentLength', True), ('ContentLength', 512 * 1024 + 1),
    ('ContentType', 'text/plain'), ('ResponseMetadata', {'HTTPStatusCode': 206})])
def test_bounded_identity_checked_before_read_and_stream_closed(complete, field, value):
    complete.s3.patch = lambda key, result: result.update({field: value})
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(complete)
    assert len(complete.s3.gets) == 1 and complete.s3.bodies[0].closed


@pytest.fixture
def successor_complete(request, monkeypatch):
    # Build real historical admission/settlements with a fixed fixture clock.
    # Reading later must never invoke a clock or extend that expired window.
    past = datetime(2020, 9, 12, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(successor_cases, 'NOW', past)
    original = request.getfixturevalue('successor_prepared')
    cap = successor_cases.commission(original)
    monkeypatch.setattr(reader.storage, 'settings', SimpleNamespace(endpoint=ENDPOINT, bucket='private-fixture'))
    ledger = journal.RouterAudioReviewJournal(original.client, clock=lambda: past, successor=cap)
    source = SimpleNamespace(**vars(original.source))
    source.policy = deepcopy(original.audio_policy)
    source.prepared = adapter.prepare_blind_asr_request(mp3(), api_key=successor_cases.NEW_KEY)
    initial = successor_cases.records(original.client, successor.ALL_KEYS)
    asr, asr_response = review(ledger, source.prepared, source.asr_result, source)
    first = {'version': 1, 'kind': 'asr', 'source': {
        'source_task_id': continuity.LEAF_ID, 'policy_sha256': digest(source.policy),
        'continuity_sha256': source.policy['continuity_sha256'], 'expected_narration': source.expected,
        'expected_narration_sha256': source.policy['expected_narration_sha256'],
        'audio': source.prepared.audio}, 'reviews': {ASR.value: asr}, **FLAGS}
    output = {'pass': True, 'summary': 'Clear synthetic delivery.', 'scores': {
        'pronunciation': 80, 'naturalness': 80, 'pacing': 80, 'sentence_flow': 80,
        'emphasis': 80, 'roboticness': 20}, 'issues': []}
    second, _ = review(ledger, prosody(source, api_key=successor_cases.NEW_KEY), output,
                       source, asr_response=asr_response)
    final = {**deepcopy(first), 'kind': 'final', 'reviews': {
        **deepcopy(first['reviews']), PROSODY.value: second}}
    prefix = ledger.state_key.rsplit(':', 1)[0]
    box = SimpleNamespace(case=SimpleNamespace(client=original.client), source=source, journal=ledger,
        s3=ReadOnlyS3(), records={'asr': first, 'final': final}, anchors={}, cap=cap,
        anchor_keys={kind: prefix + ':' + kind + '_artifact' for kind in ('asr', 'final')},
        original=original, initial=initial)
    pointer = json.loads(original.client.get(continuity._JOB + continuity.LEAF_ID))['audio_candidate_checkpoint']
    box.s3.objects[pointer['metadata_key']] = source.metadata_bytes
    box.s3.objects[pointer['audio_key']] = mp3()
    save_artifact(box, 'asr')
    save_artifact(box, 'final')
    return box


def read_successor(box, *, client=None):
    return read(box, client=client, successor=box.cap)


def test_successor_positive_expired_components_require_only_one_read_ack(successor_complete, monkeypatch):
    box = successor_complete
    blocked = Mock(side_effect=AssertionError('Historical reads cannot request or renew anything.'))
    for name in ('commission', 'reserve', 'settle', '_fresh', '_now', '_commit'):
        monkeypatch.setattr(journal.RouterAudioReviewJournal, name, blocked)
    for name in ('prepare_blind_asr_request', 'prepare_audio_prosody_request', 'observe_audio_router_response'):
        monkeypatch.setattr(adapter, name, blocked)
    for name in ('read_credential_successor', 'commission_credential_successor', 'verify_scope_successor'):
        monkeypatch.setattr(successor, name, blocked)
    for name in ('post', 'get', 'request', 'Client', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, blocked)
    before = _dump(box.case.client)
    client = successor_cases.Intercept(box.case.client)
    value = read_successor(box, client=client)
    assert type(value) is reader.RetainedAudioReviewEvidence and value.component_pass
    assert value.diagnostic_only and not any((value.qa_approved, value.publish_eligible,
                                              value.full_qa_complete, value.edit_duration_qa_complete))
    assert value.diagnostics['source']['policy_sha256'] == digest(box.source.policy)
    assert value.diagnostics['final_anchor_sha256'] == digest(box.anchors['final'])
    assert client.executions == [('PING',)] and _dump(box.case.client) == before
    assert reader._artifact_keys(box.journal) == box.anchor_keys
    assert successor_cases.records(box.case.client, successor_cases.OLD_KEYS) == box.original.legacy
    assert len(box.s3.gets) == 4 and all(body.closed for body in box.s3.bodies)
    blocked.assert_not_called()


def test_default_read_still_rejects_legacy_unknown_even_with_valid_successor_artifacts(successor_complete):
    box = successor_complete
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError, match='response_unacknowledged'):
        read(box)
    assert box.s3.gets == [] and _dump(box.case.client) == before


@pytest.mark.parametrize('cap', [False, True, {}, ('state', 'journal', 'anchor'), 'external-namespace'])
def test_successor_reader_rejects_untyped_selector_before_storage(successor_complete, cap):
    box = successor_complete
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box, successor=cap)
    assert box.s3.gets == []


def test_successor_capability_or_namespace_cannot_be_forged(successor_complete):
    box = successor_complete
    with pytest.raises(TypeError):
        successor.SuccessorAuthorization()
    fake = object.__new__(successor.SuccessorAuthorization)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read(box, successor=fake)
    object.__setattr__(box.cap, '_manifest_bytes', b'{}')
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    with pytest.raises(TypeError):
        reader.read_retained_audio_review_evidence(box.case.client, box.s3,
            bucket='private-fixture', namespace='caller-selected')
    with pytest.raises(reader.RetainedAudioEvidenceError):
        reader._artifact_keys(SimpleNamespace(keys=successor.AUDIO_KEYS))
    assert box.s3.gets == []


@pytest.mark.parametrize('kind', ['asr', 'final'])
def test_old_anchor_locations_cannot_substitute_for_selected_anchors(successor_complete, kind):
    box = successor_complete
    key = box.anchor_keys[kind]
    old_key = reader.ASR_ANCHOR_KEY if kind == 'asr' else reader.FINAL_ANCHOR_KEY
    box.case.client.set(old_key, box.case.client.get(key))
    box.case.client.delete(key)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    assert box.s3.gets == []
    assert successor_cases.records(box.case.client, successor_cases.OLD_KEYS) == box.original.legacy


@pytest.mark.parametrize('damage', ['old_policy_anchor', 'old_policy_record', 'swapped_kinds', 'final_prior'])
def test_successor_anchors_and_records_cannot_be_transplanted_or_relabelled(successor_complete, damage):
    box = successor_complete
    if damage == 'old_policy_record':
        box.records['final']['source']['policy_sha256'] = digest(box.original.source.policy)
        save_artifact(box, 'final')
    elif damage == 'swapped_kinds':
        first, final = (box.case.client.get(box.anchor_keys[kind]) for kind in ('asr', 'final'))
        box.case.client.set(box.anchor_keys['asr'], final)
        box.case.client.set(box.anchor_keys['final'], first)
    else:
        anchor = deepcopy(box.anchors['final'])
        anchor['policy_sha256' if damage == 'old_policy_anchor' else 'asr_anchor_sha256'] = (
            digest(box.original.source.policy) if damage == 'old_policy_anchor' else '0'*64)
        box.case.client.set(box.anchor_keys['final'], canonical(anchor))
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    assert successor_cases.records(box.case.client, successor_cases.OLD_KEYS) == box.original.legacy


@pytest.mark.parametrize('family', ['child_trio', 'control_pair'])
def test_successor_historical_read_rejects_partial_external_rollback(successor_complete, family):
    box = successor_complete
    keys = successor.AUDIO_KEYS if family == 'child_trio' else (successor.JOURNAL_KEY, successor.ANCHOR_KEY)
    for key in keys:
        box.case.client.restore(key, 0, box.initial[key], replace=True)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    assert _dump(box.case.client) == before and box.s3.gets == []


@pytest.mark.parametrize('part,damage', [
    ('controller', 'ttl'), ('sibling', 'missing'), ('predecessor', 'missing'),
    ('archive', 'ttl'), ('source', 'changed'), ('final_anchor', 'ttl'),
])
def test_successor_read_requires_all_authorities_to_remain_durable_and_unchanged(successor_complete, part, damage):
    box = successor_complete
    key = {'controller': successor.ANCHOR_KEY, 'sibling': successor.STORY_KEYS[2],
        'predecessor': successor_cases.OLD_KEYS[2], 'archive': box.original.archive_key,
        'source': continuity._AUTH_EPOCH, 'final_anchor': box.anchor_keys['final']}[part]
    if damage == 'missing': box.case.client.delete(key)
    elif damage == 'ttl': box.case.client.pexpire(key, 60000)
    else: box.case.client.incr(key)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    assert _dump(box.case.client) == before and box.s3.gets == []


@pytest.mark.parametrize('part', ['controller', 'sibling', 'predecessor', 'archive', 'source'])
def test_successor_watch_covers_controller_sibling_predecessors_archive_and_source(successor_complete, part):
    box = successor_complete
    key = {'controller': successor.ANCHOR_KEY, 'sibling': successor.STORY_KEYS[2],
        'predecessor': successor_cases.OLD_KEYS[2], 'archive': box.original.archive_key,
        'source': continuity._AUTH_EPOCH}[part]
    def change(_):
        box.case.client.set(key, box.case.client.get(key))
    client = InterceptClient(box.case.client, before=change)
    with pytest.raises(reader.RetainedAudioEvidenceError, match='read_unverified'):
        read_successor(box, client=client)
    assert client.calls == 1 and all(body.closed for body in box.s3.bodies)


@pytest.mark.parametrize('ack', [[1], [True, True], 'lost'])
def test_successor_uncertain_read_ack_returns_no_component_or_retry(successor_complete, ack):
    box = successor_complete
    def outcome(_, result):
        if ack == 'lost': raise ConnectionError('private-ack-details')
        return ack
    client = InterceptClient(box.case.client, after=outcome)
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError) as caught:
        read_successor(box, client=client)
    assert 'private-ack-details' not in str(caught.value)
    assert client.calls == 1 and _dump(box.case.client) == before


def test_actual_selected_audio_unknown_response_rejects_before_storage(successor_prepared, monkeypatch):
    original = successor_prepared
    cap = successor_cases.commission(original)
    monkeypatch.setattr(reader.storage, 'settings', SimpleNamespace(endpoint=ENDPOINT, bucket='private-fixture'))
    ledger = journal.RouterAudioReviewJournal(original.client, clock=lambda: successor_cases.NOW, successor=cap)
    prepared = adapter.prepare_blind_asr_request(mp3(), api_key=successor_cases.NEW_KEY)
    ledger.reserve(ASR, prepared)
    stored = json.loads(original.client.get(ledger.state_key))
    assert set(stored['slots']) == {ASR.value} and stored['slots'][ASR.value]['response'] is None
    s3, before = ReadOnlyS3(), _dump(original.client)
    with pytest.raises(reader.RetainedAudioEvidenceError, match='response_unacknowledged'):
        reader.read_retained_audio_review_evidence(original.client, s3,
                                                   bucket='private-fixture', successor=cap)
    assert s3.gets == [] and _dump(original.client) == before
    assert successor_cases.records(original.client, successor_cases.OLD_KEYS) == original.legacy


def test_well_formed_sealed_capability_must_match_actual_controller_manifest(successor_complete):
    box = successor_complete
    manifest = json.loads(box.cap._manifest_bytes)
    manifest['attestation']['owner_authorization_sha256'] = '0'*64
    object.__setattr__(box.cap, '_manifest_bytes', canonical(manifest).encode())
    # This passes the closed type/seal/schema gate, so rejection below must come
    # from comparing the presented manifest against the actual durable one.
    assert successor.selected_keys(box.cap, 'audio') == successor.AUDIO_KEYS
    before = _dump(box.case.client)
    with pytest.raises(reader.RetainedAudioEvidenceError):
        read_successor(box)
    assert box.s3.gets == [] and _dump(box.case.client) == before
