"""Retained speech requires a real, exact blind transcript and a single claim."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs, storage
from app.services import content_plan_voice_resume as resume, content_plan_recovery as recovery
from app.services import commissioning_scribe as scribe, youtube_auth
from test_content_plan import case
from test_content_plan_research_resume import failed
from test_voice_candidate_recovery import stored_candidate, SOURCE_ID


def candidate_failure(case, monkeypatch, *, child=False):
    from app import tasks
    from app.services import content_plan_research_resume as pre
    source = failed(case, monkeypatch); root = source['task_id']
    monkeypatch.setattr(jobs, '_client', lambda: case.client)
    monkeypatch.setattr(tasks.run_video_pipeline, 'apply_async', Mock())
    if child:
        pre.schedule(source, Mock()); outcome = pre.run(root, pre.operation(root))
        assert jobs.acquire_retry_child_execution(outcome['task_id'], root)
        source = jobs.get_job(outcome['task_id'])
    task = source['task_id']
    source.update(state='FAILURE', failure_stage='audio_qc_retry', error='Narration mismatch',
        paid_create_slots_used=0, preview_total_paid_create_cap=6, audio_candidate_checkpoint={'private': 'pointer'})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '6', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalAudioQualityError', 'exc_message': ['Narration mismatch']}}))
    proof = {'scribe_key': 'bound-transcript', 'scribe_record_sha256': 'a' * 64,
        'audio_sha256': 'b' * 64, 'metadata_sha256': 'c' * 64, 'package_sha256': 'd' * 64}
    monkeypatch.setattr(resume, '_transcript_proof', Mock(return_value=proof))
    return source, root


@pytest.mark.parametrize('damage', [None, 'terminal', 'root_hold', 'root_cancel', 'lineage_claim',
    'media', 'spec', 'budget', 'profile', 'connection'])
def test_old_failure_is_immutable_and_ineligible_lineages_never_dispatch(case, monkeypatch, damage):
    from app.services.source_publication_hold import HOLD_PREFIX
    source, root = candidate_failure(case, monkeypatch, child=True); task = source['task_id']
    if damage == 'terminal': case.client.delete('celery-task-meta-' + task)
    if damage == 'root_hold': case.client.set(HOLD_PREFIX + root, 'held')
    if damage == 'root_cancel': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stopped')
    if damage == 'lineage_claim': case.client.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + task)
    if damage == 'media': source['generated_asset_candidates'] = {'preserved_count': 1}
    if damage == 'spec': source['spec'] = {**source['spec'], 'topic': 'Changed narration'}
    if damage == 'budget': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '1')
    if damage == 'profile':
        profile = {**case.profile, 'production_enabled': False}
        case.client.set(plan.production.PROFILE_PREFIX + profile['channel_id'], plan._raw(profile))
    if damage == 'connection':
        case.client.set(plan.production.OAUTH_CHANNEL_PREFIX + case.profile['channel_id'],
            plan._raw({'id': case.profile['channel_id'], 'connection_id': 'changed'}))
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; enqueue = Mock(side_effect=TimeoutError())
    if damage:
        with pytest.raises((plan.ContentPlanError, ValueError, TypeError)):
            recovery.schedule(source, enqueue)
        enqueue.assert_not_called()
    else:
        assert recovery.schedule(source, enqueue) == 'voice_resume_uncertain'
        assert recovery.schedule(source, enqueue) == 'voice_resume_reserved'
        enqueue.assert_called_once()
    assert all(case.client.dump(k) == value for k, value in before.items())


def test_simultaneous_ticks_reserve_once_and_continue_with_original_unapproved_voice(case, monkeypatch):
    from app import tasks
    source, root = candidate_failure(case, monkeypatch); task = source['task_id']; enqueue = Mock()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: recovery.schedule(source, enqueue), range(4)))
    enqueue.assert_called_once()
    result = recovery.run(task, resume.operation(task)); child = result['task_id']
    assert result == {'status': 'enqueued', 'task_id': child, 'new_voice_requests': 0}
    assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    resume.verify_child(child, task, source['spec'])
    call = tasks.run_video_pipeline.apply_async.call_args.kwargs
    assert call['args'][1] == .5 and call['args'][5:] == (None, task) and call['retry'] is False
    assert jobs.get_job(child).get('audio_candidate_checkpoint') is None
    # A later failure in the same root cannot obtain another voice continuation.
    new_source = jobs.get_job(child)
    new_source.update(state='FAILURE', failure_stage='audio_qc_retry', error='Narration mismatch',
        paid_create_slots_used=0, preview_total_paid_create_cap=6, audio_candidate_checkpoint={'private': 'pointer'})
    case.client.set(jobs.JOB_PREFIX + child, plan._raw(new_source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + child, mapping={'cap': '6', 'used': '0'})
    case.client.set('celery-task-meta-' + child, plan._raw({'task_id': child, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalAudioQualityError', 'exc_message': ['Narration mismatch']}}))
    assert resume.schedule(new_source, enqueue) == 'voice_resume_reserved'
    enqueue.assert_called_once()


def proof_record(case, stored_candidate, monkeypatch):
    candidate = stored_candidate; object_store = storage._client()
    monkeypatch.setattr(storage, '_client', lambda **kwargs: object_store)
    monkeypatch.setattr(youtube_auth.settings, 'app_encryption_key', 'unit-test-key')
    text = ' '.join(candidate.voice['spoken_texts']); words = text.split()
    payload = {'text': text, 'language_code': 'tr', 'language_probability': 1,
        'words': [{'text': word, 'type': 'word', 'start': i * .3, 'end': (i+1) * .3}
                  for i, word in enumerate(words)]}
    source = {'task_id': SOURCE_ID, 'audio_candidate_checkpoint': candidate.pointer,
        'spec': {'production_channel_id': case.profile['channel_id'],
                 'production_connection_id': 'connection-current', 'language': 'tr'}}
    context = {'lineage_id': SOURCE_ID, 'kind': 'shorts', 'channel_id': case.profile['channel_id'],
               'connection_id': 'connection-current'}
    request = {'audio': {'sha256': candidate.pointer['audio_sha256'], 'bytes': candidate.pointer['size']},
               'model_id': 'scribe_v2'}
    key = scribe.PREFIX + scribe._sha(scribe._raw({'context': context, 'request': request}).encode())
    def save(value):
        raw = plan._raw(value).encode()
        row = {'version': 1, 'context': context, 'request': request, 'outcome': {
            'response_sha256': hashlib.sha256(raw).hexdigest(),
            'encrypted_response': youtube_auth._encrypt_json({'response': raw.decode()})}}
        case.client.set(key, plan._raw(row)); return row
    return source, payload, key, save


@pytest.mark.parametrize('damage', [None, 'unknown', 'missing_words', 'wrong_speech', 'wrong_audio',
    'response_hash', 'metadata_hash', 'approved', 'expiring', 'duplicate', 'changed_context'])
def test_transcript_requires_bound_audio_complete_words_and_no_synthetic_approval(
        case, stored_candidate, monkeypatch, damage):
    source, payload, key, save = proof_record(case, stored_candidate, monkeypatch)
    if damage == 'missing_words': payload['words'] = []
    if damage == 'wrong_speech': payload['text'] = payload['text'].replace('pamuk', 'altın')
    row = save(payload)
    if damage == 'unknown': row['outcome'] = None
    if damage == 'wrong_audio': row['request']['audio']['sha256'] = 'a' * 64
    if damage == 'response_hash': row['outcome']['response_sha256'] = 'a' * 64
    if damage == 'changed_context': row['context']['lineage_id'] = 'another-root'
    case.client.set(key, plan._raw(row))
    if damage == 'metadata_hash': stored_candidate.objects[stored_candidate.pointer['metadata_key']] += b' '
    if damage == 'approved': source['audio_candidate_checkpoint']['qa_approved'] = True
    if damage == 'expiring': case.client.expire(key, 60)
    if damage == 'duplicate': case.client.set(key + 'duplicate', plan._raw(row))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}
    if damage:
        with pytest.raises(Exception): resume._transcript_proof(case.client, source, SOURCE_ID)
    else:
        proof = resume._transcript_proof(case.client, source, SOURCE_ID)
        assert proof['scribe_key'] == key and proof['audio_sha256'] == stored_candidate.pointer['audio_sha256']
        assert not any(k in proof for k in ('pass', 'qa_approved', 'approved_package'))
    assert stored_candidate.pointer['audio_key'] not in stored_candidate.calls
    assert all(body.closed for body in stored_candidate.bodies)
    assert all(case.client.dump(k) == value for k, value in before.items())
