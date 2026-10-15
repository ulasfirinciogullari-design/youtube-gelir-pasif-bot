from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_retained_completion as completion, content_plan_recovery as recovery
from app.services import content_plan_voice_resume as voice, commissioning_video as video
from app.services import production_spend_runtime as runtime
from test_content_plan import case
from test_content_plan_long_media_resume import captured_failure
from test_content_plan_voice_resume import candidate_failure


def quota_failure(case, monkeypatch):
    source, root, value, observed = captured_failure(case, monkeypatch)
    source.update(failure_stage='final_visual_qc_ai_repair', error=completion.QUOTA_ERROR)
    source['audio_candidate_checkpoint']['audio_sha256'] = 'b' * 64
    row = next(row for row in value['requests'].values() if row['request']['scene_index'] == 1)
    row['create'] = observed({'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED'}})
    row['create']['http_status'] = 429; row['result'] = None
    case.client.set(video.PREFIX + root, plan._raw(value))
    case.client.set(jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    case.client.set('celery-task-meta-' + source['task_id'], plan._raw({'task_id': source['task_id'], 'status': 'FAILURE',
        'result': {'exc_type': 'SpendBlocked', 'exc_message': [completion.QUOTA_ERROR]},
        'traceback': 'File "commissioning_video.py", line 1, in _payload\n'}))
    return source, root, value


@pytest.mark.parametrize('damage', [None, 'unknown', 'permission', 'hash', 'counter', 'hold', 'changed_spec'])
def test_known_quota_only_keeps_receipts_and_dispatches_at_most_once(case, monkeypatch, damage):
    source, root, journal = quota_failure(case, monkeypatch); task = source['task_id']
    row = next(r for r in journal['requests'].values() if r['result'] is None)
    if damage == 'unknown': row['create'] = None
    if damage == 'permission': row['create']['http_status'] = 403
    if damage == 'hash': row['create']['response_sha256'] = 'a' * 64
    if damage == 'counter': case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', '0')
    if damage == 'hold': case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
    if damage == 'changed_spec': source['spec'] = {**source['spec'], 'topic': 'Different story'}
    case.client.set(video.PREFIX + root, plan._raw(journal)); case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: case.client.dump(k) for k in case.client.scan_iter()}; queue = Mock(side_effect=TimeoutError())
    if damage:
        with pytest.raises(Exception): recovery.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert recovery.schedule(source, queue) == 'retained_completion_uncertain'
        assert recovery.schedule(source, queue) == 'retained_completion_reserved'
        queue.assert_called_once()
    assert all(case.client.dump(k) == v for k, v in before.items())


def test_private_quota_child_cannot_purchase_another_clip_or_escape_a_hold(case, monkeypatch):
    source, root, journal = quota_failure(case, monkeypatch); task = source['task_id']
    recovery.schedule(source, Mock()); result = recovery.run(task, completion.operation(task)); child = result['task_id']
    assert result['new_voice_requests'] == 0 and jobs.acquire_retry_child_execution(child, task)
    assert recovery.run(task, completion.operation(task)) == {'status': 'already_started'}
    scope = {'context': journal['context'], 'foundation': SimpleNamespace(client=case.client)}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.stock_only_scope(scope) is True
        case.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'stop')
        with pytest.raises(plan.ContentPlanError): completion.stock_only_scope(scope)
    finally: runtime._TASK_ID.reset(token)
    assert json.loads(case.client.get(video.PREFIX + root)) == journal


def test_source_recovery_uses_original_audio_parent_without_another_synthesis(case, monkeypatch):
    source, root = candidate_failure(case, monkeypatch, child=True, long=True); saved = source['task_id']
    voice.schedule(source, Mock()); first = voice.run(saved, voice.operation(saved)); task = first['task_id']
    assert jobs.acquire_retry_child_execution(task, saved)
    source = jobs.get_job(task)
    source.update(state='FAILURE', stage='failed', failure_stage='director_qc', error=completion.SOURCE_ERROR,
        paid_create_slots_used=0, preview_total_paid_create_cap=32)
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'ProductionContentError', 'exc_message': [completion.SOURCE_ERROR]},
        'traceback': 'File "commissioning_longform.py", line 1, in review_story\n'}))
    restored = {'sources': [{'url': 'https://www.okhistory.org/publications/enc/entry?entry=GO004'}]}
    monkeypatch.setattr(completion, '_restored_sources', Mock(return_value=restored))
    recovery.schedule(source, Mock()); result = recovery.run(task, completion.operation(task)); child = result['task_id']
    assert jobs.acquire_retry_child_execution(child, task)
    _, _, proof = completion.verify_child(child, task, source['spec'])
    assert proof['saved_voice_source'] == saved and proof['mode'] == 'sources'
    assert proof['media_sources'] == [] and proof['video_records'] == {}
    assert result['new_voice_requests'] == 0 and not case.client.exists(video.PREFIX + root)
    scope = {'context': {'lineage_id': root, 'kind': 'long'}, 'foundation': SimpleNamespace(client=case.client)}
    token = runtime._TASK_ID.set(child)
    try:
        assert completion.continuation_identity(scope) == child
        # This child's unknown request keeps its identity and blocks its own
        # retransmission in the provider adapter; old admission stays immutable.
        journal = {'version': 1, 'context': scope['context'], 'requests': {
            'new': {'request': {'continuation_task_id': child}, 'create': None, 'result': None}}}
        case.client.set(video.PREFIX + root, plan._raw(journal))
        assert completion.continuation_identity(scope) == child
        journal['requests']['new']['request']['continuation_task_id'] = 'unrelated'
        case.client.set(video.PREFIX + root, plan._raw(journal))
        with pytest.raises(plan.ContentPlanError): completion.continuation_identity(scope)
    finally: runtime._TASK_ID.reset(token)


@pytest.mark.parametrize('damage', [None, 'wrong_source', 'extra_query', 'hash', 'unknown', 'not_original'])
def test_citation_is_restored_only_from_complete_matching_original_research(case, monkeypatch, damage):
    from cryptography.fernet import Fernet
    from app.services import commissioning_reasoning as native, production_included_router as included
    cipher = Fernet(Fernet.generate_key()); monkeypatch.setattr(included, '_cipher', lambda: cipher)
    old = [{'url': 'https://www.sony.com/history', 'evidence': 'The original source documents portable audio development.'}]
    restored = [{'url': 'https://www.okhistory.org/publications/enc/entry?entry=GO004',
                 'evidence': 'The original entry documents the history of grocery shopping.'}, *deepcopy(old)]
    if damage == 'wrong_source': restored[1]['evidence'] = 'A different source annotation changed the original evidence.'
    if damage == 'extra_query': restored[0]['url'] += '&token=SECRET'
    monkeypatch.setattr(completion, '_metadata', lambda _: {'package': {'sources': old}})
    root = 'test-root'; identity = 'a' * 64
    req = {'request_sha256': identity, 'purpose': 'research', 'model': native.MODEL,
           'context': {'lineage_id': root}, 'reserved_at': '2026-09-22T01:00:00+00:00'}
    if damage == 'not_original': req['reserved_at'] = '2026-09-22T03:00:00+00:00'
    body = plan._raw({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': plan._raw({'sources': restored})}]}}]}).encode()
    res = {'request_sha256': identity, 'http_status': 200,
        'response_sha256': hashlib.sha256(body).hexdigest(), 'encrypted_response': cipher.encrypt(body).decode()}
    if damage == 'hash': res['response_sha256'] = 'b' * 64
    case.client.sadd(native.PREFIX + 'lineage:' + root, identity)
    case.client.set(native.PREFIX + 'request:' + identity, plan._raw(req))
    if damage != 'unknown': case.client.set(native.PREFIX + 'response:' + identity, plan._raw(res))
    saved = {'created_at': '2026-09-22T02:00:00+00:00'}
    if damage:
        with pytest.raises(Exception): completion._restored_sources(case.client, root, saved)
    else:
        proof = completion._restored_sources(case.client, root, saved)
        assert proof['sources'] == restored and proof['request_id'] == identity


def test_two_preserved_versions_of_one_scene_are_materialized_without_overwriting(monkeypatch, tmp_path):
    from app import tasks
    from app.services import content_plan_long_media_resume as media, generated_asset_checkpoint as assets, storage
    package = {'scenes': [{'narration': 'An unchanged historical narration.', 'visual_queries': ['banknote printing']}
                          for _ in range(30)]}
    canonical = assets._candidate_package(package); audio_hash = 'b' * 64
    source = {'task_id': 'source', 'audio_candidate_checkpoint': {'package_sha256': plan._sha(canonical)},
              'generated_asset_candidates': {'entries': []}}
    objects = {}
    for ordinal, phase in enumerate(('initial_generation', 'final_repair')):
        raw = ('original bytes ' + str(ordinal)).encode(); key = 'raw-' + str(ordinal)
        descriptor = {'key': key, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw),
                      'synthetic_motion_only': False, 'provider': 'gemini_veo', 'provider_attempts': 1}
        manifest = {'source_task_id': 'source', 'scene_index': 3, 'phase': phase,
            'qa_approved': False, 'requires_full_qa': True, 'package': canonical,
            'candidate_package_sha256': plan._sha(canonical), 'audio': {'sha256': audio_hash}, 'raw': descriptor}
        encoded = plan._raw(manifest).encode(); mkey = 'manifest-' + str(ordinal)
        objects.update({key: raw, mkey: encoded})
        source['generated_asset_candidates']['entries'].append({'source_task_id': 'source',
            'scene_index': 3, 'phase': phase, 'status': 'preserved_candidate', 'qa_approved': False,
            'requires_full_qa': True, 'audio_sha256': audio_hash, 'manifest_key': mkey,
            'manifest_sha256': hashlib.sha256(encoded).hexdigest(), 'manifest_size': len(encoded),
            'raw_key': key, 'raw_sha256': descriptor['sha256'], 'raw_size': len(raw)})
    def stored(client, key, digest, size, path, maximum):
        data = objects[key]
        assert len(data) == size <= maximum and hashlib.sha256(data).hexdigest() == digest
        with path.open('xb') as output: output.write(data)
        return path
    monkeypatch.setattr(recovery, '_stored', stored); monkeypatch.setattr(storage, '_client', lambda: object())
    probe = Mock(); monkeypatch.setattr(tasks, '_validate_recovered_generated_clip', probe)
    prepared = {'package': package, 'source_audio_sha256': audio_hash, 'voice_result': {'scene_durations': [5.5] * 30}}
    clips = media._load_clips(source, prepared, tmp_path, allow_repair=True)
    assert len(list(tmp_path.glob('*.mp4'))) == 2 and probe.call_count == 2
    assert clips[3]['path'].endswith('retained-s03-01.mp4') and clips[3]['forbid_loop'] is True
