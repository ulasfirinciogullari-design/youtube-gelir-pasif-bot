"""Original-source audio admission with real offline Redis WATCH and HTTPX bytes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import subprocess
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_audio_review_journal as journal
from app.services import abacus_router_audio_adapter as adapter
from app.services import abacus_router_review_journal as story_journal
from app.services import audio_qc, production_connection_continuity as continuity
from app.services.production_spend import LEDGER_KEY
from app.services.production_spend_runtime import _request_fingerprint
from app.services.voice import normalize_turkish_tts
from test_production_connection_continuity import case, REVISION, OLD, NEW, _dump
from test_production_credit_ledger import InterceptClient


NOW = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
ASR, PROSODY = adapter.AudioReviewPurpose
KEYS = (journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY)
KEY = 'offline-original-audio-key'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value).encode())


@lru_cache(maxsize=2)
def mp3(frequency=440):
    return subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'sine=frequency={frequency}:sample_rate=48000:duration=2', '-c:a', 'libmp3lame',
        '-b:a', '128k', '-threads', '1', '-write_xing', '0', '-f', 'mp3', 'pipe:1'],
        check=True, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


@pytest.fixture(autouse=True)
def no_transport(monkeypatch):
    blocked = Mock(side_effect=AssertionError('No provider sender belongs in the journal.'))
    for name in ('post', 'get', 'request', 'stream', 'Client', 'AsyncClient'):
        monkeypatch.setattr(httpx, name, blocked)
    yield
    blocked.assert_not_called()


@pytest.fixture
def source(case):
    prepared = adapter.prepare_blind_asr_request(mp3(), api_key=KEY)
    audio = prepared.audio
    package = {'title': 'Original synthetic fixture', 'sources': [], 'scenes': [
        {'index': index, 'narration': text, 'ai_prompt': None}
        for index, text in enumerate(('Merhaba', 'Güzel', 'Bir', 'Yeni', 'Gün', 'Başladı.'))]}
    spoken = [normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
              for index, scene in enumerate(package['scenes'])]
    duration = audio['decoded_samples'] / 48000
    voice = {'spoken_texts': spoken, 'scene_durations': [duration / 6] * 6,
        'duration_before_fit': duration, 'duration_after_fit': duration, 'tempo_rate': 1.0,
        'voice_profile': {'voice_language_code': 'tr'}}
    audio_key = f"audio_candidates/{continuity.LEAF_ID}/{audio['sha256']}/candidate.mp3"
    metadata = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
        'requires_full_qa': True, 'source_task_id': continuity.LEAF_ID,
        'audio': {'key': audio_key, 'sha256': audio['sha256'], 'size': audio['bytes']},
        'package_sha256': digest(package), 'package': package, 'voice': voice}
    metadata_bytes = canonical(metadata).encode()
    pointer = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
        'requires_full_qa': True, 'audio_sha256': audio['sha256'], 'metadata_sha256': sha(metadata_bytes),
        'package_sha256': digest(package), 'size': audio['bytes'], 'audio_key': audio_key,
        'metadata_key': f"audio_candidates/{continuity.LEAF_ID}/{audio['sha256']}/metadata-{sha(metadata_bytes)}.json"}
    profile = {**case.profile, 'default_language': 'tr'}
    case.client.set(continuity._PROFILE + continuity.CHANNEL_ID, canonical(profile))
    for task in continuity.LINEAGE:
        key = continuity._JOB + task
        job = json.loads(case.client.get(key))
        job['spec']['language'] = 'tr'
        if task == continuity.LEAF_ID:
            job['audio_candidate_checkpoint'] = pointer
            for entry in job['generated_asset_candidates']['entries']:
                entry['audio_sha256'] = audio['sha256']
        case.client.set(key, canonical(job))
    proof = continuity.prepare_connection_continuity(continuity.ROOT_ID, continuity.LEAF_ID,
        continuity.CHANNEL_ID, REVISION, client=case.client)
    expected = ' '.join(spoken)
    policy = {'version': 1, 'kind': 'existing_subscription_retained_audio_review',
        'endpoint': adapter.ENDPOINT, 'model': adapter.MODEL, 'original_task_id': continuity.ROOT_ID,
        'leaf_task_id': continuity.LEAF_ID, 'channel_id': continuity.CHANNEL_ID, 'language': 'tr',
        'profile_revision': REVISION, 'old_connection_id': OLD, 'current_connection_id': NEW,
        'continuity_sha256': proof['receipt_sha256'], 'credential_sha256': prepared.credential_sha256,
        'entitlement_evidence_sha256': 'e' * 64,
        'entitlement_source': 'owner_subscription_and_official_router_api_terms',
        'valid_from': '2026-09-12T15:00:00Z', 'valid_until': '2026-09-13T00:00:00Z',
        'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0, 'audio': audio,
        'audio_checkpoint_sha256': digest(pointer), 'source_metadata_sha256': sha(metadata_bytes),
        'audio_candidate_package_sha256': digest(package), 'original_full_package_sha256': 'd' * 64,
        'voice_contract_sha256': digest(voice), 'scene_durations_sha256': digest(voice['scene_durations']),
        'expected_narration_sha256': sha(expected.encode())}
    result = {'text': expected, 'language': 'tr', 'words': [
        {'word': text, 'start': index * .25 + .1, 'end': index * .25 + .3}
        for index, text in enumerate(spoken)]}
    return SimpleNamespace(policy=policy, metadata=metadata, metadata_bytes=metadata_bytes,
        expected=expected, prepared=prepared, asr_result=result)


def store(case, *, client=None, now=NOW):
    return journal.RouterAudioReviewJournal(case.client if client is None else client, clock=lambda: now)


def commission(case, source, **options):
    ledger = store(case, **options)
    ledger.commission(source.policy, source_metadata_bytes=source.metadata_bytes)
    return ledger


def prosody(source, **patch):
    return adapter.prepare_audio_prosody_request(mp3(), **{
        'api_key': KEY, 'expected_narration': source.expected,
        'system_instruction': audio_qc._PROSODY_SYSTEM_INSTRUCTION,
        'json_schema': deepcopy(audio_qc._PROSODY_REVIEW_SCHEMA), **patch})


def response(prepared, result, **patch):
    payload = {'id': 'synthetic-audio-review-1', 'object': 'chat.completion', 'model': 'route-llm',
        'choices': [{'index': 0, 'finish_reason': 'stop',
                     'message': {'role': 'assistant', 'content': canonical(result)}}], **patch}
    wire = prepared.wire_kwargs(); wire.pop('timeout')
    return httpx.Response(200, json=payload, request=httpx.Request('POST', adapter.ENDPOINT, **wire))


def observe_asr(ledger, source, result=None):
    ledger.reserve(ASR, source.prepared)
    return ledger.settle(ASR, source.prepared, response(source.prepared, source.asr_result if result is None else result))


def reserve_prosody(ledger, source, **patch):
    return ledger.reserve(PROSODY, prosody(source), **{
        'expected_narration': source.expected, 'asr_result': source.asr_result, **patch})


def state(case):
    return json.loads(case.client.get(journal.STATE_KEY))


def test_exact_two_purposes_preserve_source_story_journal_and_unknown_finance(case, source):
    for key in (story_journal.STATE_KEY, story_journal.JOURNAL_KEY, story_journal.ANCHOR_KEY):
        case.client.set(key, 'original-story-review-evidence')
    before = _dump(case.client)
    ledger = commission(case, source)
    settled = observe_asr(ledger, source)
    receipt = reserve_prosody(ledger, source)
    assert receipt['asr_binding']['parsed_result_sha256'] == settled['evidence']['parsed_result_sha256']
    assert receipt['root_request_fingerprint'] == _request_fingerprint(
        {'lineage_id': continuity.ROOT_ID}, 'abacus', adapter.OPERATION, prosody(source).payload)
    result = {'pass': True, 'summary': 'Clear delivery.', 'scores': {
        'pronunciation': 80, 'naturalness': 80, 'pacing': 80, 'sentence_flow': 80, 'emphasis': 80,
        'roboticness': 20}, 'issues': []}
    end = ledger.settle(PROSODY, prosody(source), response(prosody(source), result))
    for returned in (settled, receipt, end):
        assert returned['diagnostic_only'] is True
        assert returned['qa_approved'] is returned['publish_eligible'] is False
    assert journal.PURPOSES == (ASR, PROSODY)
    assert set(state(case)['slots']) == {ASR.value, PROSODY.value}
    assert all(case.client.pttl(key) == -1 for key in KEYS)
    assert {key: value for key, value in _dump(case.client).items() if key not in KEYS} == before
    raw = case.client.get(journal.STATE_KEY)
    assert source.expected not in raw and KEY not in raw and 'input_audio' not in raw
    assert not hasattr(ledger, 'reconfirm_unused')
    with pytest.raises(journal.RouterAudioReviewBlocked, match='already_reserved'):
        reserve_prosody(ledger, source)


@pytest.mark.parametrize('field,value', [
    ('expected_narration_sha256', 'f' * 64), ('original_task_id', continuity.MIDDLE_ID),
    ('new_cash_allowance_micro', False), ('historical_extra_cash_micro', 0),
    ('source_metadata_sha256', 'f' * 64), ('audio_checkpoint_sha256', 'f' * 64),
    ('original_full_package_sha256', 'f' * 64), ('voice_contract_sha256', 'f' * 64),
    ('scene_durations_sha256', 'f' * 64), ('continuity_sha256', 'f' * 64),
    ('valid_until', '2026-09-15T00:00:00Z'), ('language', 'en'),
])
def test_invalid_policy_cannot_commission(case, source, field, value):
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        store(case).commission({**source.policy, field: value}, source_metadata_bytes=source.metadata_bytes)
    assert _dump(case.client) == before


def test_self_consistent_substitute_metadata_is_not_original_source_authority(case, source):
    forged = deepcopy(source.metadata)
    forged['package']['title'] = 'Substituted package'
    forged['package_sha256'] = digest(forged['package'])
    raw = canonical(forged).encode()
    policy = {**source.policy, 'source_metadata_sha256': sha(raw),
              'audio_candidate_package_sha256': forged['package_sha256']}
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked, match='source_changed'):
        store(case).commission(policy, source_metadata_bytes=raw)
    assert _dump(case.client) == before


def test_metadata_binds_full_spoken_contract_not_a_caller_expected_hash(case, source):
    forged = deepcopy(source.metadata)
    forged['voice']['spoken_texts'][0] = 'Different heard words'
    raw = canonical(forged).encode()
    policy = {**source.policy, 'source_metadata_sha256': sha(raw),
        'voice_contract_sha256': digest(forged['voice']),
        'expected_narration_sha256': sha(' '.join(forged['voice']['spoken_texts']).encode())}
    with pytest.raises(journal.RouterAudioReviewBlocked, match='spoken_contract_changed'):
        store(case).commission(policy, source_metadata_bytes=raw)
    assert case.client.exists(*KEYS) == 0


def test_commission_detaches_policy_before_real_watch_retry(case, source):
    before = deepcopy(source.policy)
    def mutate(call):
        if call == 1:
            source.policy['credential_sha256'] = 'f' * 64
            source.policy['audio']['decoded_pcm_sha256'] = 'f' * 64
            case.client.set(continuity._AUTH_EPOCH, case.client.get(continuity._AUTH_EPOCH))
    commission(case, source, client=InterceptClient(case.client, before=mutate))
    assert state(case)['policy'] == before
    store(case).reserve(ASR, source.prepared)


@pytest.mark.parametrize('stage', ['not_reserved', 'unknown', 'mismatch'])
def test_prosody_needs_durable_asr_response_and_exact_spoken_content(case, source, stage):
    ledger = commission(case, source)
    if stage == 'unknown':
        ledger.reserve(ASR, source.prepared)
    elif stage == 'mismatch':
        result = deepcopy(source.asr_result)
        result['text'] = result['text'].replace('Merhaba', 'Elveda')
        result['words'][0]['word'] = 'Elveda'
        observe_asr(ledger, source, result)
        source.asr_result = result
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        reserve_prosody(ledger, source)
    assert PROSODY.value not in state(case)['slots'] and _dump(case.client) == before


@pytest.mark.parametrize('forgery', ['boolean', 'text', 'time', 'expected'])
def test_prosody_recomputes_only_the_stored_actual_asr_result(case, source, forgery):
    ledger = commission(case, source)
    settled = observe_asr(ledger, source)
    supplied = deepcopy(settled['result'])
    expected = source.expected
    if forgery == 'boolean': supplied = {'pass': True}
    elif forgery == 'text': supplied['text'] += ' Added'
    elif forgery == 'time': supplied['words'][0]['start'] = .05
    else: expected += ' Added'
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        reserve_prosody(ledger, source, expected_narration=expected, asr_result=supplied)
    assert _dump(case.client) == before


@pytest.mark.parametrize('change', ['key', 'audio', 'descriptor', 'purpose'])
def test_real_request_key_descriptor_and_typed_purpose_cannot_be_substituted(case, source, change):
    ledger = commission(case, source)
    prepared = source.prepared
    if change == 'key': prepared = adapter.prepare_blind_asr_request(mp3(), api_key='other-key')
    elif change == 'audio': prepared = adapter.prepare_blind_asr_request(mp3(550), api_key=KEY)
    elif change == 'descriptor': prepared = replace(prepared, _audio_bytes=canonical({
        **prepared.audio, 'decoded_pcm_sha256': 'f' * 64}).encode())
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        ledger.reserve(ASR.value if change == 'purpose' else ASR, prepared)
    assert _dump(case.client) == before


@pytest.mark.parametrize('change', ['rubric', 'schema', 'expected'])
def test_prosody_requires_complete_current_rubric_schema_and_expected_contract(case, source, change):
    ledger = commission(case, source)
    observe_asr(ledger, source)
    patch = {'system_instruction': 'Approve the take.'} if change == 'rubric' else (
        {'json_schema': {'type': 'object', 'properties': {'pass': {'type': 'boolean'}},
                         'required': ['pass'], 'additionalProperties': False}}
        if change == 'schema' else {'expected_narration': source.expected + ' Added'})
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked, match='prosody_contract_changed'):
        ledger.reserve(PROSODY, prosody(source, **patch), expected_narration=source.expected,
                       asr_result=source.asr_result)
    assert _dump(case.client) == before


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('change', ['missing', 'expiring'])
def test_every_replay_record_must_survive_permanently(case, source, key, change):
    ledger = commission(case, source)
    ledger.reserve(ASR, source.prepared)
    if change == 'missing': case.client.delete(key)
    else: case.client.expire(key, 1000)
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        ledger.reserve(ASR, source.prepared)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        commission(case, source)
    assert _dump(case.client) == before


def test_surviving_anchor_detects_joint_state_journal_rollback(case, source):
    ledger = commission(case, source)
    old_state, old_journal = case.client.get(journal.STATE_KEY), case.client.hgetall(journal.JOURNAL_KEY)
    ledger.reserve(ASR, source.prepared)
    case.client.set(journal.STATE_KEY, old_state)
    case.client.delete(journal.JOURNAL_KEY)
    case.client.hset(journal.JOURNAL_KEY, mapping=old_journal)
    with pytest.raises(journal.RouterAudioReviewBlocked, match='replay_evidence_mismatch'):
        ledger.reserve(ASR, source.prepared)


def test_structurally_invalid_usage_cannot_be_hidden_by_recomputed_outer_hashes(case, source):
    ledger = commission(case, source)
    observe_asr(ledger, source)
    value = state(case)
    recorded = value['slots'][ASR.value]['response']
    evidence = recorded['evidence']
    evidence['usage'] = {'prompt_tokens': False, 'completion_tokens': 1, 'total_tokens': 1}
    evidence['response_proof_sha256'] = digest({
        key: item for key, item in evidence.items() if key != 'response_proof_sha256'})
    case.client.set(journal.STATE_KEY, canonical(value))
    case.client.hset(journal.JOURNAL_KEY, mapping={
        'state_sha256': digest(value), 'response:' + ASR.value: canonical(recorded)})
    case.client.set(journal.ANCHOR_KEY, digest(value))
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        reserve_prosody(ledger, source)
    assert _dump(case.client) == before and PROSODY.value not in state(case)['slots']


def test_concurrent_reservation_has_exactly_one_acknowledged_winner(case, source):
    commission(case, source)
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait(timeout=10)
        try: return store(case).reserve(ASR, source.prepared)
        except journal.RouterAudioReviewBlocked: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(attempt, range(2)))
    assert sum(receipt is not None for receipt in receipts) == 1


@pytest.mark.parametrize('operation', ['commission', 'reserve', 'settle'])
def test_unknown_commit_acknowledgement_never_reopens_a_send(case, source, operation):
    ledger = store(case)
    if operation != 'commission': commission(case, source)
    if operation == 'settle': ledger.reserve(ASR, source.prepared)
    def lose(*_): raise ConnectionError('private transport details')
    broken = store(case, client=InterceptClient(case.client, after=lose))
    with pytest.raises(journal.RouterAudioReviewBlocked) as error:
        if operation == 'commission': broken.commission(source.policy, source_metadata_bytes=source.metadata_bytes)
        elif operation == 'reserve': broken.reserve(ASR, source.prepared)
        else: broken.settle(ASR, source.prepared, response(source.prepared, source.asr_result))
    assert 'private' not in str(error.value)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        if operation == 'commission': commission(case, source)
        else: ledger.reserve(ASR, source.prepared)
    if operation == 'settle':
        before = _dump(case.client)
        assert ledger.settle(ASR, source.prepared, response(source.prepared, source.asr_result))['result'] == source.asr_result
        assert _dump(case.client) == before
        with pytest.raises(journal.RouterAudioReviewBlocked, match='settlement_conflict'):
            ledger.settle(ASR, source.prepared, response(source.prepared, source.asr_result, id='different'))


@pytest.mark.parametrize('change', ['oauth', 'cancel', 'queue', 'source', 'profile', 'cash_marker'])
def test_watch_rechecks_source_owner_and_finance_fences_at_commit(case, source, change):
    commission(case, source)
    def mutate(call):
        if call != 1: return
        if change == 'oauth': case.client.incr(continuity._AUTH_EPOCH)
        elif change == 'cancel': case.client.set('youtube_studio:render_cancellation:v1:' + continuity.LEAF_ID, 'yes')
        elif change == 'queue': case.client.lpush('celery', 'new-work')
        elif change == 'profile': case.client.set(continuity._PROFILE + continuity.CHANNEL_ID, '{}')
        elif change == 'source':
            key = continuity._JOB + continuity.LEAF_ID
            value = json.loads(case.client.get(key)); value['updated_at'] = '2026-09-12T16:01:00Z'
            case.client.set(key, canonical(value))
        else:
            fingerprint = _request_fingerprint({'lineage_id': continuity.ROOT_ID}, 'abacus', adapter.OPERATION,
                                               source.prepared.payload)
            case.client.hset(LEDGER_KEY, 'request:' + sha(fingerprint.encode()), 'unknown-history')
    with pytest.raises(journal.RouterAudioReviewBlocked):
        store(case, client=InterceptClient(case.client, before=mutate)).reserve(ASR, source.prepared)
    assert state(case)['slots'] == {}


@pytest.mark.parametrize('prefix', ['request:', 'native_request:'])
def test_existing_financial_marker_is_checked_without_relabeling(case, source, prefix):
    ledger = commission(case, source)
    fingerprint = _request_fingerprint({'lineage_id': continuity.ROOT_ID}, 'abacus', adapter.OPERATION,
                                       source.prepared.payload)
    case.client.hset(LEDGER_KEY, prefix + sha(fingerprint.encode()), 'original-unknown-history')
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked, match='cross_mode_request_conflict'):
        ledger.reserve(ASR, source.prepared)
    assert _dump(case.client) == before


def test_absent_cash_foundation_stays_absent(case, source):
    case.client.delete(LEDGER_KEY)
    commission(case, source).reserve(ASR, source.prepared)
    assert case.client.exists(LEDGER_KEY) == 0


def test_late_actual_response_is_preserved_after_expiry_or_cancellation(case, source):
    ledger = commission(case, source)
    ledger.reserve(ASR, source.prepared)
    case.client.set('youtube_studio:render_cancellation:v1:' + continuity.LEAF_ID, 'yes')
    late = store(case, now=NOW + timedelta(days=1))
    assert late.settle(ASR, source.prepared, response(source.prepared, source.asr_result))['result'] == source.asr_result
    with pytest.raises(journal.RouterAudioReviewBlocked):
        reserve_prosody(late, source)


@pytest.mark.parametrize('invalid', ['detached', 'mismatched_wire', 'zero_interval', 'out_of_audio'])
def test_settlement_requires_actual_bound_httpx_and_valid_audio_timing(case, source, invalid):
    ledger = commission(case, source)
    ledger.reserve(ASR, source.prepared)
    result = deepcopy(source.asr_result)
    if invalid == 'zero_interval': result['words'][0]['end'] = result['words'][0]['start']
    elif invalid == 'out_of_audio': result['words'][-1]['end'] = 30.0
    received = response(source.prepared, result)
    if invalid == 'detached': received = adapter.observe_audio_router_response(source.prepared, received)
    elif invalid == 'mismatched_wire':
        received = response(adapter.prepare_blind_asr_request(mp3(550), api_key=KEY), result)
    before = _dump(case.client)
    with pytest.raises(journal.RouterAudioReviewBlocked):
        ledger.settle(ASR, source.prepared, received)
    assert _dump(case.client) == before and state(case)['slots'][ASR.value]['response'] is None
