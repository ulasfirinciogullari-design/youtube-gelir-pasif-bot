"""Real chained private grants and Redis CAS; no media, auth or provider mocks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from test_full_video_rebuild import (
    case as full_case, SOURCE as OLD_ROOT, CHILD as PLANNING, CHANNEL,
    _load, _write, _all, _child as full_child,
)


FRESH = str(UUID(int=201))
SAVED = str(UUID(int=202))
REPLACEMENT = str(UUID(int=203))
TOKEN = 'one-shot-voice-after-full-rebuild'
AUDIO = 'a' * 64


def _checkpoint(task_id):
    prefix = f'audio_candidates/{task_id}/{AUDIO}'
    return {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
            'requires_full_qa': True, 'audio_key': prefix + '/candidate.mp3',
            'metadata_key': prefix + '/metadata-' + 'b' * 64 + '.json',
            'audio_sha256': AUDIO, 'metadata_sha256': 'b' * 64,
            'package_sha256': 'c' * 64, 'size': 4000}


def _edit(c, key, **fields):
    record = json.loads(c.client.get(key))
    record.update(fields)
    _write(c.client, key, record)


@pytest.fixture
def lineage(full_case):
    c = full_case
    m = c.module
    full_child(c)
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + PLANNING, mapping={'cap': '6', 'used': '0'})
    _edit(c, m.JOB_PREFIX + PLANNING, state='FAILURE', failure_stage='director_qc')
    assert m.reserve_full_video_rebuild(PLANNING, FRESH, 'fresh-full-rebuild-token')['claimed']
    assert c.state.acquire_retry_child_execution(FRESH, PLANNING)
    assert m.get_full_rebuild_policy(FRESH, PLANNING, c.spec)['fresh_voice'] is True
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + FRESH, mapping={'cap': '6', 'used': '0'})
    _edit(c, m.JOB_PREFIX + FRESH, state='FAILURE', failure_stage='audio_qc_retry',
          audio_candidate_checkpoint=_checkpoint(FRESH))
    assert c.state.claim_retry_dispatch(FRESH, SAVED, 'ordinary-saved-voice-token', allow_repair=False)['claimed']
    c.state.create_job(SAVED, deepcopy(c.spec), kind='render', parent_id=FRESH)
    assert c.state.acquire_retry_child_execution(SAVED, FRESH)
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SAVED, mapping={'cap': '6', 'used': '0'})
    _edit(c, m.JOB_PREFIX + SAVED, state='FAILURE', failure_stage='audio_qc',
          audio_candidate_checkpoint=_checkpoint(SAVED),
          voice_candidate_reuse={'source_task_id': FRESH, 'new_tts_requests': 0})
    ns = dict(vars(m), FULL_REBUILD_POLICY_PREFIX=m.POLICY_PREFIX,
              _executed_full_rebuild_grant=m._executed_full_rebuild_grant)
    voice = _load('voice_replacement', ns)
    voice._redis = lambda: c.client
    c.voice = voice
    return c


def _reserve(c, source=SAVED, child=REPLACEMENT):
    return c.voice.reserve_voice_replacement(source, child, TOKEN, AUDIO)


def _ready(c):
    result = _reserve(c)
    assert result['claimed'] is True
    c.state.create_job(REPLACEMENT, deepcopy(c.spec), kind='render', parent_id=SAVED)
    c.client.hset(c.voice.PAID_CREATE_BUDGET_PREFIX + REPLACEMENT, mapping={'cap': '6', 'used': '0'})
    assert c.state.acquire_retry_child_execution(REPLACEMENT, SAVED)
    return result


def _get(c):
    return c.voice.get_voice_replacement_policy(REPLACEMENT, SAVED, AUDIO, c.spec)


def _acquire(c):
    return c.voice.acquire_voice_replacement_attempt(REPLACEMENT, SAVED, AUDIO, c.spec, 'KnownElevenVoice12345')


def test_genuine_full_rebuild_sets_new_voice_root_without_rewriting_old_history(lineage):
    c, m, v = lineage, lineage.module, lineage.voice
    before = _all(c.client)
    result = _ready(c)
    policy = json.loads(c.client.get(v.POLICY_PREFIX + REPLACEMENT))
    assert policy['lineage_root_task_id'] == FRESH
    assert policy['full_rebuild_boundary'] == {
        'task_id': FRESH,
        'policy_sha256': m._digest(json.loads(c.client.get(m.POLICY_PREFIX + FRESH))),
        'checkpoint_sha256': v._digest(_checkpoint(FRESH)),
    }
    assert c.client.get(v.LINEAGE_PREFIX + FRESH) == c.client.get(v.POLICY_PREFIX + REPLACEMENT)
    after = _all(c.client)
    for key, value in before.items():
        if key not in {m.JOB_PREFIX + SAVED, m.JOB_INDEX}:
            assert after[key] == value, key
    assert dict(after[m.JOB_INDEX][1]) == {**dict(before[m.JOB_INDEX][1]),
                                        REPLACEMENT: c.client.zscore(m.JOB_INDEX, REPLACEMENT)}
    assert _get(c)['max_attempts'] == 1
    assert _acquire(c)['model_id'] == 'eleven_multilingual_v2'
    assert c.client.ttl(v.ATTEMPT_PREFIX + REPLACEMENT) == -1
    with pytest.raises(v.VoiceReplacementError):
        _acquire(c)
    assert 'full_rebuild_boundary' not in result['voice_replacement']
    assert 'encrypted-credential-sentinel' not in json.dumps(result)


@pytest.mark.parametrize('change', [
    'private_policy_missing', 'source_policy_missing', 'private_policy_forged',
    'execution_missing', 'claim_changed', 'ledger_changed', 'ancestor_success',
    'ancestor_publication', 'ancestor_cycle', 'schedule_changed', 'credential_changed',
    'fresh_paid_media', 'fresh_reused_voice', 'prior_replacement', 'prior_lineage_allowance',
])
def test_unverified_boundary_never_reserves_replacement(lineage, change):
    c, m, v = lineage, lineage.module, lineage.voice
    if change == 'private_policy_missing':
        c.client.delete(m.POLICY_PREFIX + FRESH)
        _edit(c, m.JOB_PREFIX + FRESH, full_rebuild={'fresh_voice': True})
    elif change == 'source_policy_missing': c.client.delete(m.SOURCE_PREFIX + PLANNING)
    elif change == 'private_policy_forged': _edit(c, m.POLICY_PREFIX + FRESH, fresh_voice=False)
    elif change == 'execution_missing': c.client.delete(m.RETRY_CHILD_EXECUTION_PREFIX + FRESH)
    elif change == 'claim_changed': c.client.hset(m.RETRY_CHILD_CLAIM_PREFIX + FRESH, 'token', 'different')
    elif change == 'ledger_changed': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + OLD_ROOT, 'used', '5')
    elif change == 'ancestor_success': _edit(c, m.JOB_PREFIX + OLD_ROOT, state='SUCCESS')
    elif change == 'ancestor_publication': c.client.set(m.UPLOAD_PREFIX + OLD_ROOT, '{}')
    elif change == 'ancestor_cycle': _edit(c, m.JOB_PREFIX + OLD_ROOT, parent_id=FRESH)
    elif change == 'schedule_changed': c.client.hset(m.CHANNEL_STATE_PREFIX + CHANNEL, 'next_due', '2')
    elif change == 'credential_changed': c.client.set(m.OAUTH_CREDENTIAL_PREFIX + CHANNEL, 'changed-credential')
    elif change == 'fresh_paid_media': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + FRESH, 'used', '1')
    elif change == 'fresh_reused_voice': _edit(c, m.JOB_PREFIX + FRESH, voice_candidate_reuse={'source_task_id': PLANNING})
    elif change == 'prior_replacement': _edit(c, m.JOB_PREFIX + FRESH, voice_replacement={'child_task_id': str(UUID(int=300))})
    else: c.client.set(v.LINEAGE_PREFIX + FRESH, '{}')
    before = _all(c.client)
    with pytest.raises(v.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('operation', [_get, _acquire])
@pytest.mark.parametrize('change', ['grant_fingerprint', 'old_ledger', 'old_claim', 'schedule', 'source_policy', 'boundary_removed', 'root_checkpoint'])
def test_prior_grant_and_full_ancestry_revalidated_before_get_and_paid_attempt(lineage, operation, change):
    c, m, v = lineage, lineage.module, lineage.voice
    _ready(c)
    if change == 'grant_fingerprint':
        for key in (m.POLICY_PREFIX + FRESH, m.SOURCE_PREFIX + PLANNING):
            _edit(c, key, extra='changed-after-reservation')
    elif change == 'old_ledger': c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + OLD_ROOT, 'used', '5')
    elif change == 'old_claim': c.client.delete(m.RETRY_CHILD_EXECUTION_PREFIX + PLANNING)
    elif change == 'schedule': c.client.hset(m.CHANNEL_STATE_PREFIX + CHANNEL, 'cursor', '1')
    elif change == 'source_policy': c.client.delete(m.SOURCE_PREFIX + PLANNING)
    elif change == 'root_checkpoint':
        changed = _checkpoint(FRESH)
        changed['size'] += 1
        _edit(c, m.JOB_PREFIX + FRESH, audio_candidate_checkpoint=changed)
    else:
        for key in (v.POLICY_PREFIX + REPLACEMENT, v.LINEAGE_PREFIX + FRESH):
            policy = json.loads(c.client.get(key))
            policy.pop('full_rebuild_boundary')
            _write(c.client, key, policy)
    before = _all(c.client)
    with pytest.raises(v.VoiceReplacementError): operation(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('operation', ['reserve', 'acquire'])
def test_grant_mutation_at_atomic_boundary_never_consumes_authority(lineage, monkeypatch, operation):
    c, m, v = lineage, lineage.module, lineage.voice
    if operation == 'acquire': _ready(c)
    original = c.client.eval
    def race(*args):
        c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + OLD_ROOT, 'used', '5')
        return original(*args)
    monkeypatch.setattr(c.client, 'eval', race)
    with pytest.raises(v.VoiceReplacementError):
        (_reserve if operation == 'reserve' else _acquire)(c)
    assert not c.client.exists(v.ATTEMPT_PREFIX + REPLACEMENT)
    if operation == 'reserve':
        assert not c.client.exists(v.POLICY_PREFIX + REPLACEMENT)
        assert not c.client.exists(v.LINEAGE_PREFIX + FRESH)


def test_replacement_cannot_be_reset_by_retrying_its_new_failed_child(lineage):
    c, m, v = lineage, lineage.module, lineage.voice
    _ready(c)
    _acquire(c)
    _edit(c, m.JOB_PREFIX + REPLACEMENT, state='FAILURE', failure_stage='audio_qc',
          audio_candidate_checkpoint=_checkpoint(REPLACEMENT))
    before = _all(c.client)
    with pytest.raises(v.VoiceReplacementError):
        _reserve(c, source=REPLACEMENT, child=str(UUID(int=204)))
    assert _all(c.client) == before


def test_duplicate_and_concurrent_requests_keep_one_new_voice_allowance(lineage):
    c, v = lineage, lineage.voice
    _ready(c)
    assert _reserve(c, child=str(UUID(int=204))) == {'claimed': False, 'child_task_id': REPLACEMENT}
    def acquire(_):
        try: return _acquire(c)
        except v.VoiceReplacementError: return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(acquire, range(5)))
    assert sum(result is not None for result in results) == 1


def test_terminal_audio_boundary_does_not_reopen_original_full_worker(lineage):
    c = lineage
    with pytest.raises(c.module.FullVideoRebuildError):
        c.module.get_full_rebuild_policy(FRESH, PLANNING, c.spec)
    assert _reserve(c)['claimed'] is True


@pytest.mark.parametrize('field,value', [
    ('audio_candidate_checkpoint_error', 'error'), ('generated_asset_candidates', {}),
    ('qa_workprint', {}), ('repair_checkpoint', {}), ('repair_available', True),
    ('repair_claimed', True), ('youtube', {'video_id': 'old-video'}),
    ('voice_candidate_reuse', {}), ('voice_candidate_reuse', False),
    ('voice_candidate_reuse', ''), ('voice_replacement', {}),
    ('voice_replacement', False), ('voice_replacement', ''),
])
def test_boundary_with_any_media_repair_or_failed_checkpoint_evidence_rejects(lineage, field, value):
    c = lineage
    _edit(c, c.module.JOB_PREFIX + FRESH, **{field: value})
    before = _all(c.client)
    with pytest.raises(c.voice.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


@pytest.mark.parametrize('prefix', ['REPAIR_CHECKPOINT_PREFIX', 'REPAIR_CHECKPOINT_CLAIM_PREFIX'])
def test_private_repair_on_fresh_voice_boundary_rejects(lineage, prefix):
    c = lineage
    c.client.set(getattr(c.module, prefix) + FRESH, 'private-existing-record')
    before = _all(c.client)
    with pytest.raises(c.voice.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


def test_intermediate_saved_voice_cap_cannot_differ_from_private_fresh_grant(lineage):
    c = lineage
    c.client.hset(c.module.PAID_CREATE_BUDGET_PREFIX + SAVED, 'cap', '4')
    before = _all(c.client)
    with pytest.raises(c.voice.VoiceReplacementError): _reserve(c)
    assert _all(c.client) == before


def test_direct_fresh_root_replacement_preserves_boundary_during_get_and_attempt(lineage):
    c, m, v = lineage, lineage.module, lineage.voice
    # Restore the genuine fresh failure before its ordinary saved-voice retry.
    # Fixture setup only: product code never removes an existing claim.
    fresh = json.loads(c.client.get(m.JOB_PREFIX + FRESH))
    for key in ('retry_claimed', 'retry_child_task_id', 'retry_dispatch_state'):
        fresh.pop(key, None)
    _write(c.client, m.JOB_PREFIX + FRESH, fresh)
    c.client.delete(m.RETRY_DISPATCH_PREFIX + FRESH)
    assert _reserve(c, source=FRESH)['claimed'] is True
    c.state.create_job(REPLACEMENT, deepcopy(c.spec), kind='render', parent_id=FRESH)
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + REPLACEMENT, mapping={'cap': '6', 'used': '0'})
    assert c.state.acquire_retry_child_execution(REPLACEMENT, FRESH)
    assert v.get_voice_replacement_policy(REPLACEMENT, FRESH, AUDIO, c.spec)['max_attempts'] == 1
    assert v.acquire_voice_replacement_attempt(REPLACEMENT, FRESH, AUDIO, c.spec, 'KnownElevenVoice12345')['max_attempts'] == 1
