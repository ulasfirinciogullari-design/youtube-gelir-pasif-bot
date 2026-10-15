"""Retained documentary recovery cannot rebuy uncertain scene requests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_fal_visual_resume as resume, content_plan_fal_resume as fal
from app.services import content_plan_recovery as recovery, content_plan_research_resume as pre
from app.services import commissioning_video as video, production_spend_runtime as runtime, youtube_auth
from app.services.fal_video_catalog import MODELS, ORIGIN
from test_content_plan import case
from test_content_plan_voice_resume import candidate_failure

VOICE_VERIFIER = resume._voice


@pytest.fixture
def retained(case, monkeypatch):
    parent, root = candidate_failure(case, monkeypatch, child=True, long=True)
    parent.pop('audio_candidate_checkpoint'); source_id = parent['task_id']
    parent.update(failure_stage='voice_and_visuals', error='Voice synthesis quality rejected before paid media: mismatch')
    case.client.set(jobs.JOB_PREFIX + source_id, plan._raw(parent))
    audio = {'audio_sha256': 'b' * 64, 'package_sha256': 'c' * 64, 'metadata_sha256': 'd' * 64}
    old_proof = {'qualified_audio_sha256': audio['audio_sha256'], 'qualification_sha256': 'e' * 64}
    monkeypatch.setattr(fal, 'checked', lambda client, task, **kw: (jobs.get_job(task), deepcopy(old_proof)))
    monkeypatch.setattr(resume, '_voice', lambda *args: deepcopy(audio))
    case.client.set(fal.DISPATCH + source_id, plan._raw({'task_id': fal.operation(source_id), 'source_task_id': source_id,
        'provider_records': old_proof, 'source_sha256': pre.fingerprint(parent)}))
    case.client.set(fal.EXECUTION + source_id, fal.operation(source_id))
    task = str(uuid5(NAMESPACE_URL, 'owner-plan-qualified-fal-child:v1:' + source_id))
    assert jobs.claim_retry_dispatch(source_id, task, 'a' * 32, allow_repair=False)['claimed']
    jobs.create_job(task, parent['spec'], kind='render', parent_id=source_id)
    assert jobs.acquire_retry_child_execution(task, source_id)
    diagnostic = {'stage': 'after_rescue', 'total': 30, 'accepted': 28, 'repair_checkpoint_available': False,
        'rejected': {'1': {'score': 62}, '4': {'score': 68}},
        'provider_generation_failures': {'failures': [{'stage': 'final_repair', 'scene_index': i,
            'exception_class': 'WatchError'} for i in (1, 4)]}}
    error = resume.ERROR + plan._raw(diagnostic)
    source = jobs.get_job(task)
    source.update(state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue', error=error,
        paid_create_slots_used=3, preview_total_paid_create_cap=32, audio_candidate_checkpoint=audio,
        failure_classification={'version': 1, 'stage': 'final_visual_qc_rescue', 'category': 'content_rejected',
            'code': 'visual_quality_exhausted', 'error_sha256': hashlib.sha256(error.encode()).hexdigest()},
        created_at='2026-09-25T01:00:00+00:00', updated_at='2026-09-25T02:00:00+00:00',
        generated_asset_candidates={'failed_count': 0, 'attempted_count': 1, 'preserved_count': 1,
            'entries': [{'scene_index': 0, 'package_sha256': 'a' * 64, 'audio_sha256': 'b' * 64}]})
    case.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    case.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '3'})
    case.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    monkeypatch.setattr(youtube_auth.settings, 'app_encryption_key', 'offline-test-key')
    model = MODELS['seedance_pro']; records = {}
    def observed(value):
        raw = plan._raw(value).encode()
        return {'http_status': 200, 'response_sha256': hashlib.sha256(raw).hexdigest(),
            'encrypted_response': youtube_auth._encrypt_json({'response': raw.decode()})}
    for index in (0, 1, 4):
        descriptor = {'package_sha256': 'a' * 64, 'scene_index': index, 'aspect_ratio': '16:9',
            'model': model, 'route': ORIGIN + '/' + model}
        identity = video._sha(video._raw(descriptor).encode())
        request_id = str(uuid5(NAMESPACE_URL, str(index)))
        records[identity] = {'request': descriptor, 'reserved_at': '2026-09-25T01:30:00+00:00',
            'create': observed({'request_id': request_id, 'response_url': ORIGIN + '/' + model + '/requests/' + request_id}),
            'result': observed({'video': {'url': 'https://v3.fal.media/files/test/' + str(index) + '.mp4'}})}
        if index == 4: records[identity].update(create=None, result=None)
    journal = {'version': 1, 'context': {'lineage_id': root, 'kind': 'long',
        'channel_id': source['spec']['production_channel_id'], 'connection_id': source['spec']['production_connection_id']},
        'requests': records}
    case.client.set(video.PREFIX + root, plan._raw(journal))
    return SimpleNamespace(**vars(case), source=source, root=root, journal=journal)


@pytest.mark.parametrize('damage', [None, 'counter', 'terminal', 'hold', 'cancel', 'spec', 'claim',
    'profile', 'connection', 'qa_accepted', 'wrong_context', 'corrupt_receipt', 'changed_audio'])
def test_only_bound_failed_edit_can_continue_without_rewriting_history(retained, damage):
    r = retained; c = r.client; source = deepcopy(r.source); task = source['task_id']
    from app.services.source_publication_hold import HOLD_PREFIX
    if damage == 'counter': c.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', 0)
    if damage == 'terminal': c.delete('celery-task-meta-' + task)
    if damage == 'hold': c.set(HOLD_PREFIX + r.root, 'held')
    if damage == 'cancel': c.set(jobs.RENDER_CANCELLATION_PREFIX + r.root, 'cancelled')
    if damage == 'spec': source['spec']['topic'] = 'Different story'
    if damage == 'claim': c.delete(jobs.RETRY_CHILD_EXECUTION_PREFIX + task)
    channel = source['spec']['production_channel_id']
    if damage == 'profile': c.set(plan.production.PROFILE_PREFIX + channel, plan._raw({**r.profile, 'channel_id': channel, 'production_enabled': False}))
    if damage == 'connection': c.set(plan.production.OAUTH_CHANNEL_PREFIX + channel, plan._raw({'connection_id': 'changed'}))
    if damage == 'qa_accepted': source['failure_classification']['category'] = 'approved'
    if damage == 'changed_audio': source['generated_asset_candidates']['entries'][0]['audio_sha256'] = 'f' * 64
    journal = deepcopy(r.journal)
    if damage == 'wrong_context': journal['context']['lineage_id'] = 'unrelated'
    if damage == 'corrupt_receipt': next(v for v in journal['requests'].values() if v['result'])['result']['response_sha256'] = 'f' * 64
    c.set(video.PREFIX + r.root, plan._raw(journal)); c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: c.dump(k) for k in c.scan_iter()}; enqueue = Mock(side_effect=TimeoutError('unknown broker ACK'))
    if damage:
        with pytest.raises(Exception): recovery.schedule(source, enqueue)
        enqueue.assert_not_called()
    else:
        assert recovery.schedule(source, enqueue) == 'fal_visual_resume_uncertain'
        assert recovery.schedule(source, enqueue) == 'fal_visual_resume_reserved'
        enqueue.assert_called_once()
    assert all(c.dump(k) == v for k, v in before.items())


def test_competing_schedulers_dispatch_one_child_and_no_new_media(retained, monkeypatch):
    r = retained; source = r.source; task = source['task_id']; enqueue = Mock()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: recovery.schedule(source, enqueue), range(3)))
    enqueue.assert_called_once()
    result = recovery.run(task, resume.operation(task)); child = result['task_id']
    assert result['new_video_requests'] == result['new_voice_requests'] == 0
    assert recovery.run(task, resume.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    _, root, proof = resume.verify_child(child, task, source['spec'])
    assert root == r.root and len(proof['unknown_requests']) == 1 and set(proof['accepted_outputs']) == {'0', '1'}
    before = r.client.get(video.PREFIX + root)
    from app.services import content_plan_long_media_resume as media, content_plan_retained_completion as completion
    from app.services import commissioning_longform as longform
    token = runtime._TASK_ID.set(child)
    try:
        assert media.registered(task) and media.retained_cap(child, {'content_plan_media_source': task}) == 32
        assert completion.stock_only_scope({'context': {'kind': 'long'}}) is True
        monkeypatch.setattr(longform, 'active', lambda: True)
        monkeypatch.setattr(video, 'enabled_for_task', lambda: True)
        assert video.completion_capacity(source['spec'], 3, 30, 32, 0) == 0
        assert video.completion_repairs(source['spec'], 3, [{}] * 30, [4], {4: {'score': 60}}, 32, 0) == []
        r.client.set(jobs.RENDER_CANCELLATION_PREFIX + root, 'cancelled')
        with pytest.raises(plan.ContentPlanError): resume.readonly_for_task()
    finally: runtime._TASK_ID.reset(token)
    assert r.client.get(video.PREFIX + root) == before


def test_changed_unknown_or_result_cannot_inherit_an_earlier_claim(retained):
    r = retained; task = r.source['task_id']; resume.schedule(r.source, Mock())
    result = resume.run(task, resume.operation(task)); child = result['task_id']
    assert jobs.acquire_retry_child_execution(child, task)
    before = r.client.get(video.PREFIX + r.root)
    journal = json.loads(before)
    row = next(row for row in journal['requests'].values() if row['result'] is None)
    row['reserved_at'] = '2026-09-25T01:40:00+00:00'
    r.client.set(video.PREFIX + r.root, plan._raw(journal))
    with pytest.raises(plan.ContentPlanError): resume.verify_child(child, task, r.source['spec'])
    # The verifier observes drift; it never deletes the unknown or rolls it back.
    assert r.client.get(video.PREFIX + r.root) == plan._raw(journal)


def test_silent_generation_guard_precedes_reservation_or_transport(retained, monkeypatch):
    from app.services import commissioning_fal_video as fal_video
    from app.services import content_plan_retained_completion as completion
    r = retained; task = r.source['task_id']; resume.schedule(r.source, Mock())
    result = resume.run(task, resume.operation(task)); child = result['task_id']
    assert jobs.acquire_retry_child_execution(child, task)
    monkeypatch.setattr(video, 'enabled_for_task', lambda: True)
    monkeypatch.setattr(fal_video, 'settings', SimpleNamespace(fal_key='test-key', studio_fal_video_model='seedance_pro'))
    forbidden = Mock(side_effect=AssertionError('No new reservation or provider call'))
    monkeypatch.setattr(fal_video, '_Journal', forbidden); monkeypatch.setattr(fal_video, 'generate_fal_video', forbidden)
    scope = {'context': r.journal['context'], 'package_sha256': 'a' * 64, 'scene_index': 4,
        'narration_millis': 5000, 'generation_seconds': 6, 'aspect_ratio': '16:9'}
    token = runtime._TASK_ID.set(child)
    try:
        with pytest.raises(video.CommissionedVideoUnavailable): fal_video.generate(scope, 'Original reviewed scene', 6, '16:9')
    finally: runtime._TASK_ID.reset(token)
    forbidden.assert_not_called()


@pytest.mark.parametrize('damage', [None, 'audio', 'speech', 'package', 'model', 'tempo', 'qualification', 'source'])
def test_qualified_voice_requires_exact_original_bytes_words_and_metadata(retained, monkeypatch, damage):
    from app.services import fal_voice_production as production, fal_voice_trial as trial, kie_voice_ledger as kie
    from app.services import content_plan_retained_completion as completion
    # Restore the actual verifier; the general dispatch fixture isolates only
    # old voice admission from the new media/lineage safety tests.
    actual = VOICE_VERIFIER
    r = retained; source = deepcopy(r.source); spec = source['spec']; spec['language'] = 'en'
    narration = ['A complete original sentence remains unchanged.'] * 30
    package = {'scenes': [{'narration': text} for text in narration]}
    pointer = {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False, 'requires_full_qa': True,
        'audio_key': 'private-audio', 'audio_sha256': 'b' * 64, 'size': 30000,
        'metadata_sha256': 'd' * 64, 'package_sha256': plan._sha(package)}
    source['audio_candidate_checkpoint'] = pointer
    profile = {'voice_name': 'George', 'voice_model': production.api.MODEL, 'voice_language_code': 'en'}
    metadata = {**{k: pointer[k] for k in ('version', 'status', 'qa_approved', 'requires_full_qa')},
        'source_task_id': source['task_id'], 'package': package, 'package_sha256': pointer['package_sha256'],
        'audio': {'key': pointer['audio_key'], 'sha256': pointer['audio_sha256'], 'size': pointer['size']},
        'voice': {'spoken_texts': narration, 'scene_durations': [5.] * 30, 'duration_before_fit': 150.,
            'duration_after_fit': 150., 'tempo_rate': 1., 'voice_profile': profile}}
    context = {'lineage_id': r.root, 'kind': 'long', 'channel_id': spec['production_channel_id'],
        'connection_id': spec['production_connection_id']}
    grant = {'context': context, 'source_task_id': source['parent_id']}
    qualified = {'pass': True, 'audio_sha256': pointer['audio_sha256'], 'spoken_scenes': deepcopy(narration)}
    proof = {'new_tts_requests': 0, 'original_voice_evidence': deepcopy(grant),
        'qualified_audio_sha256': pointer['audio_sha256'], 'qualification_sha256': kie.sha(kie.raw(qualified))}
    monkeypatch.setattr(production, 'activation', lambda *args: {'channels': {context['channel_id']: context['connection_id']}})
    monkeypatch.setattr(production, '_authorize', Mock())
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: SimpleNamespace(client=r.client))
    monkeypatch.setattr(trial, '_read', lambda *args: {'grant': grant, 'qualification': qualified})
    monkeypatch.setattr(completion, '_metadata', lambda *args: metadata)
    if damage == 'audio': metadata['audio']['sha256'] = 'f' * 64
    if damage == 'speech': metadata['voice']['spoken_texts'] = ['Different spoken words.'] * 30
    if damage == 'package': metadata['package_sha256'] = 'f' * 64
    if damage == 'model': profile['voice_model'] = 'unqualified-new-model'
    if damage == 'tempo': metadata['voice']['tempo_rate'] = 1.1
    if damage == 'qualification': qualified['audio_sha256'] = 'f' * 64
    if damage == 'source': grant['source_task_id'] = r.root
    before = {k: r.client.dump(k) for k in r.client.scan_iter()}
    if damage:
        with pytest.raises(Exception): actual(r.client, source, r.root, proof)
    else:
        assert actual(r.client, source, r.root, proof)['audio_sha256'] == pointer['audio_sha256']
    assert all(r.client.dump(k) == v for k, v in before.items())


def test_preparation_reads_known_extra_output_but_never_unknown_create(retained, monkeypatch, tmp_path):
    from app.services import content_plan_voice_resume as voice, content_plan_long_media_resume as media, runway
    from app import tasks
    r = retained; task = r.source['task_id']; resume.schedule(r.source, Mock())
    child = resume.run(task, resume.operation(task))['task_id']; assert jobs.acquire_retry_child_execution(child, task)
    candidate = {'content_plan_voice_source': task, 'package': {'scenes': [{}] * 30},
        'voice_result': {'scene_durations': [5.] * 30}, 'source_audio_sha256': 'b' * 64, 'preserve_audio_bytes': True}
    review_and_voice = Mock(return_value=deepcopy(candidate)); monkeypatch.setattr(voice, '_prepare_long_candidate', review_and_voice)
    monkeypatch.setattr(media, '_load_clips', lambda *args, **kw: {0: {'path': 'existing-private-original'}})
    downloads = []
    monkeypatch.setattr(runway, 'download_generated_scene', lambda result, target: downloads.append(result['url']))
    probe = Mock(); monkeypatch.setattr(tasks, '_validate_recovered_generated_clip', probe)
    before = r.client.get(video.PREFIX + r.root)
    result = resume.prepare(child, task, r.source['spec'], tmp_path)
    review_and_voice.assert_called_once_with(child, task, r.source['spec'], tmp_path)
    assert result['preserve_audio_bytes'] is True and set(result['retained_long_clips']) == {0, 1}
    assert downloads == ['https://v3.fal.media/files/test/1.mp4'] and probe.call_count == 1
    assert jobs.get_job(child)['retained_long_media']['new_video_requests'] == 0
    assert r.client.get(video.PREFIX + r.root) == before
