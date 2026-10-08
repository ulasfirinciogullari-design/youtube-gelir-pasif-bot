from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import content_plan as plan, studio_state as jobs
from app.services import content_plan_fal_cut_resume as cut, content_plan_fal_visual_resume as original
from app.services import content_plan_recovery as recovery, commissioning_video as video
from app.services import production_spend_runtime as runtime
from test_content_plan_fal_visual_resume import retained
from test_content_plan import case

WORKPRINT = cut._workprint


@pytest.fixture
def failed_cut(retained, monkeypatch):
    r = retained; ancestor = r.source; old = ancestor['task_id']
    ancestor['generated_asset_candidates']['entries'][0].update(raw_sha256='a' * 64, raw_size=6000)
    r.client.set(jobs.JOB_PREFIX + old, plan._raw(ancestor))
    original.schedule(ancestor, Mock())
    task = original.run(old, original.operation(old))['task_id']
    assert jobs.acquire_retry_child_execution(task, old)
    source = jobs.get_job(task)
    diagnostic = {'stage': 'after_rescue', 'total': 30, 'accepted': 29,
                  'repair_checkpoint_available': False, 'rejected': {'0': {'score': 68}}}
    error = original.ERROR + plan._raw(diagnostic)
    source.update(state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue', error=error,
        audio_candidate_checkpoint=deepcopy(ancestor['audio_candidate_checkpoint']),
        paid_create_slots_used=0, preview_total_paid_create_cap=32,
        qa_workprint={'version': 4, 'sha256': 'f' * 64, 'metadata_sha256': 'e' * 64},
        retained_long_media={'source_task_id': old, 'stock_only': True, 'new_tts_requests': 0, 'new_video_requests': 0},
        failure_classification={'version': 1, 'stage': 'final_visual_qc_rescue', 'category': 'content_rejected',
            'code': 'visual_quality_exhausted', 'error_sha256': hashlib.sha256(error.encode()).hexdigest()})
    r.client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    r.client.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, mapping={'cap': '32', 'used': '0'})
    r.client.set('celery-task-meta-' + task, plan._raw({'task_id': task, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]},
        'traceback': 'File "tasks.py", line 1, in run_video_pipeline\n'}))
    metadata = {'version': 4, 'task_id': task, 'qa_approved': False, 'publish_eligible': False,
        'reusable': False, 'failure_stage': 'final_visual_qc', 'video_sha256': 'f' * 64,
        'voice': {'sha256': 'b' * 64}, 'scenes': [{'scene_index': i, 'selection': {}} for i in range(30)]}
    metadata['scenes'][0]['selection'] = {'generated': True, 'preserve_start_fraction': True,
        'forbid_loop': True, 'source_type': 'generated', 'start_fraction': .82, 'sha256': 'a' * 64, 'size': 6000}
    monkeypatch.setattr(cut, '_workprint', lambda source: deepcopy(metadata))
    return SimpleNamespace(**{**vars(r), 'ancestor': ancestor, 'source': source, 'metadata': metadata})


@pytest.mark.parametrize('damage', [None, 'zero_cut', 'wrong_media', 'wrong_size', 'unpinned', 'approved',
    'voice', 'counter', 'hold', 'cancel', 'terminal', 'new_media', 'old_admission', 'unknown_changed', 'source_changed'])
def test_only_proven_cut_mutation_can_admit_one_readonly_child(failed_cut, damage):
    r = failed_cut; source = deepcopy(r.source); task = source['task_id']; c = r.client
    selected = r.metadata['scenes'][0]['selection']
    if damage == 'zero_cut': selected['start_fraction'] = 0.
    if damage == 'wrong_media': selected['sha256'] = 'c' * 64
    if damage == 'wrong_size': selected['size'] = 7000
    if damage == 'unpinned': selected['preserve_start_fraction'] = False
    if damage == 'approved': r.metadata['qa_approved'] = True
    if damage == 'voice': r.metadata['voice']['sha256'] = 'd' * 64
    if damage == 'counter': c.hset(jobs.PAID_CREATE_BUDGET_PREFIX + task, 'used', 1)
    if damage == 'hold': source['publication_hold'] = True
    if damage == 'cancel': c.set(jobs.RENDER_CANCELLATION_PREFIX + r.root, 'stop')
    if damage == 'terminal': c.delete('celery-task-meta-' + task)
    if damage == 'new_media': source['generated_asset_candidates'] = {'entries': ['new clip']}
    if damage == 'old_admission': c.delete(original.ROOT + r.root)
    if damage == 'unknown_changed':
        journal = plan._object(c.get(video.PREFIX + r.root))
        next(row for row in journal['requests'].values() if row['result'] is None)['reserved_at'] = '2026-09-25T01:41:00+00:00'
        c.set(video.PREFIX + r.root, plan._raw(journal))
    if damage == 'source_changed': source['spec']['topic'] = 'Another film'
    c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = {k: c.dump(k) for k in c.scan_iter()}; queue = Mock(side_effect=TimeoutError('lost ACK'))
    if damage:
        with pytest.raises(Exception): cut.schedule(source, queue)
        queue.assert_not_called()
    else:
        assert recovery.schedule(source, queue) == 'fal_cut_resume_uncertain'
        assert recovery.schedule(source, queue) == 'fal_cut_resume_reserved'
        queue.assert_called_once()
    assert all(c.dump(k) == value for k, value in before.items())


def test_schedulers_and_worker_keep_exact_original_requests(failed_cut, monkeypatch):
    r = failed_cut; task = r.source['task_id']; queue = Mock(); before = r.client.get(video.PREFIX + r.root)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: recovery.schedule(r.source, queue), range(3)))
    queue.assert_called_once()
    result = recovery.run(task, cut.operation(task)); child = result['task_id']
    assert result['new_voice_requests'] == result['new_video_requests'] == 0
    assert recovery.run(task, cut.operation(task)) == {'status': 'already_started'}
    assert jobs.acquire_retry_child_execution(child, task)
    source, root, evidence, ancestor, proof = cut.verify_child(child, task, r.source['spec'])
    assert root == r.root and ancestor['task_id'] == r.ancestor['task_id']
    assert evidence['moved_cuts'][0]['original_fraction'] == 0.
    from app.services import content_plan_long_media_resume as media, commissioning_longform as longform
    from app.services import commissioning_fal_video as provider
    monkeypatch.setattr(video, 'enabled_for_task', lambda: True)
    monkeypatch.setattr(longform, 'active', lambda: True)
    monkeypatch.setattr(provider, 'settings', SimpleNamespace(fal_key='test-key', studio_fal_video_model='seedance_pro'))
    forbidden = Mock(side_effect=AssertionError('Generation forbidden'))
    monkeypatch.setattr(provider, '_Journal', forbidden); monkeypatch.setattr(provider, 'generate_fal_video', forbidden)
    token = runtime._TASK_ID.set(child)
    try:
        assert media.registered(task) and media.retained_cap(child, {'content_plan_media_source': task}) == 32
        assert original.readonly_for_task()
        assert video.completion_capacity(r.source['spec'], 3, 30, 32, 0) == 0
        scope = {'context': r.journal['context'], 'package_sha256': 'a' * 64, 'scene_index': 4,
                 'narration_millis': 5000, 'generation_seconds': 6, 'aspect_ratio': '16:9'}
        with pytest.raises(video.CommissionedVideoUnavailable): provider.generate(scope, 'Exact original scene', 6, '16:9')
        forbidden.assert_not_called()
        r.client.set(jobs.RENDER_CANCELLATION_PREFIX + child, 'stop')
        with pytest.raises(plan.ContentPlanError): original.readonly_for_task()
    finally:
        runtime._TASK_ID.reset(token)
    assert r.client.get(video.PREFIX + r.root) == before


def test_preparation_uses_original_voice_and_media_under_new_verified_parent(failed_cut, monkeypatch, tmp_path):
    r = failed_cut; task = r.source['task_id']; cut.schedule(r.source, Mock())
    child = cut.run(task, cut.operation(task))['task_id']; assert jobs.acquire_retry_child_execution(child, task)
    loader = Mock(return_value={'content_plan_media_source': task, 'preserve_audio_bytes': True})
    monkeypatch.setattr(original, '_prepare_assets', loader)
    assert cut.prepare(child, task, r.source['spec'], tmp_path)['preserve_audio_bytes'] is True
    assert loader.call_args.args[:4] == (child, task, r.source['spec'], tmp_path)
    assert loader.call_args.args[4]['task_id'] == r.ancestor['task_id']
    assert jobs.get_job(child)['retained_cut_correction']['requires_full_qa'] is True


@pytest.mark.parametrize('damage', [None, 'digest', 'bound', 'size', 'pointer'])
def test_diagnostic_metadata_is_bounded_hashed_and_never_an_approval(monkeypatch, damage):
    from app.services import qa_workprint_access as access, storage
    data = b'{"qa_approved":false}'
    pointer = {'version': 4, 'metadata_key': 'private/metadata.json',
               'metadata_sha256': hashlib.sha256(data).hexdigest(), 'metadata_size': len(data)}
    if damage == 'digest': pointer['metadata_sha256'] = 'a' * 64
    body = BytesIO(data)
    length = len(data) if damage not in {'bound', 'size'} else (2 * 1024 * 1024 if damage == 'bound' else len(data) + 1)
    monkeypatch.setattr(access, 'validated_pointer', lambda _: None if damage == 'pointer' else pointer)
    store = Mock(); store.get_object.return_value = {'ContentLength': length, 'Body': body}
    monkeypatch.setattr(storage, '_client', lambda **kw: store)
    if damage:
        with pytest.raises(Exception): WORKPRINT({})
    else:
        assert WORKPRINT({}) == {'qa_approved': False}
    if damage != 'pointer': assert body.closed


@pytest.mark.parametrize('damage', [None, 'hold', 'unknown_changed', 'no_cut_proof'])
def test_server_tick_routes_proven_cut_before_editorial_disposition(failed_cut, damage):
    r = failed_cut; c = r.client; source = r.source; task = source['task_id']
    channel = source['spec']['production_channel_id']
    if damage == 'hold':
        from app.services.source_publication_hold import HOLD_PREFIX
        c.set(HOLD_PREFIX + r.root, 'held')
    if damage == 'unknown_changed':
        journal = plan._object(c.get(video.PREFIX + r.root))
        next(row for row in journal['requests'].values() if row['result'] is None)['reserved_at'] = '2026-09-25T01:41:00+00:00'
        c.set(video.PREFIX + r.root, plan._raw(journal))
    if damage == 'no_cut_proof': r.metadata['scenes'][0]['selection']['start_fraction'] = 0.
    before = {k: c.dump(k) for k in c.scan_iter()}
    normal = Mock(); repair = Mock(side_effect=TimeoutError('unknown queue ACK'))
    result = plan.maintain([{**r.profile, 'channel_id': channel}], normal, repair_enqueue=repair)
    if damage:
        repair.assert_not_called()
        assert not c.exists(cut.DISPATCH + task)
    else:
        assert result['channels'][channel] == 'fal_cut_resume_uncertain'
        assert plan.maintain([{**r.profile, 'channel_id': channel}], normal,
                             repair_enqueue=repair)['channels'][channel] == 'fal_cut_resume_reserved'
        repair.assert_called_once()
    normal.assert_not_called()
    assert all(c.dump(k) == value for k, value in before.items())


@pytest.fixture
def rejected_correction(failed_cut):
    from datetime import datetime, timezone
    from test_qa_workprint_access import pointer
    from app.services import channel_cadence as cadence
    r = failed_cut; c = r.client; parent = r.source['task_id']
    cut.schedule(r.source, Mock()); task = cut.run(parent, cut.operation(parent))['task_id']
    assert jobs.acquire_retry_child_execution(task, parent)
    _, _, proof, ancestor, _ = cut.checked(c, parent, claimed=True)
    source = deepcopy(r.source)
    source.update(task_id=task, parent_id=parent,
        retained_cut_correction={'version':1,'source_task_id':parent,
            'original_source_task_id':ancestor['task_id'],'moved_cuts':proof['moved_cuts'],
            'new_tts_requests':0,'new_video_requests':0,'requires_full_qa':True},
        retained_long_media={'source_task_id':parent,'stock_only':True,
            'new_tts_requests':0,'new_video_requests':0,'requires_full_qa':True})
    p = pointer(); old = p['task_id']
    p.update(task_id=task, version=4, width=1920, height=1080, duration_seconds=180., frame_count=5400)
    p['key'] = p['key'].replace(old,task); p['metadata_key'] = p['metadata_key'].replace(old,task)
    source['qa_workprint'] = p
    c.set(jobs.JOB_PREFIX+task,plan._raw(source))
    c.hset(jobs.PAID_CREATE_BUDGET_PREFIX+task,mapping={'cap':'32','used':'0'})
    terminal = plan._object(c.get('celery-task-meta-'+parent))
    terminal.update(task_id=task,date_done=datetime.now(timezone.utc).isoformat())
    c.set('celery-task-meta-'+task,plan._raw(terminal))
    channel = source['spec']['production_channel_id']
    document=plan.read(channel,client=c)
    plan.change(channel,document['revision'],'settings',payload={'enabled':True,'after_queue':'auto_shorts'},client=c)
    c.hset(cadence.keys(channel)[0],r.root,'long')
    return SimpleNamespace(**{**vars(r),'source':source,'channel':channel})


@pytest.mark.parametrize('damage', [None, 'claim', 'unknown_changed', 'known_unfinished',
    'paid', 'live', 'missing_terminal', 'upload', 'owner_hold', 'audio', 'new_asset', 'unknown_model'])
def test_terminal_correction_can_detach_but_never_release_unknown_charges(rejected_correction, damage):
    from app.services import content_plan_attention as attention, channel_cadence as cadence
    from app.services import commissioning_reasoning as reasoning
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    r = rejected_correction; c = r.client; source = r.source; task = source['task_id']
    if damage == 'claim': c.delete(cut.EXECUTION+source['parent_id'])
    if damage == 'unknown_changed':
        journal=plan._object(c.get(video.PREFIX+r.root))
        next(row for row in journal['requests'].values() if row['result'] is None)['reserved_at']='2026-09-25T01:44:00+00:00'
        c.set(video.PREFIX+r.root,plan._raw(journal))
    if damage == 'known_unfinished':
        journal=plan._object(c.get(video.PREFIX+r.root))
        next(row for row in journal['requests'].values() if row['result'] is not None)['result']=None
        c.set(video.PREFIX+r.root,plan._raw(journal))
    if damage == 'paid': source['paid_create_slots_used']=1
    if damage == 'live': source['state']='PROGRESS'
    if damage == 'missing_terminal': c.delete('celery-task-meta-'+task)
    if damage == 'upload': c.set(UPLOAD_PREFIX+task,plan._raw({'status':'uncertain'}))
    if damage == 'owner_hold': source['publication_hold']={'reason':'owner'}
    if damage == 'audio': source['audio_candidate_checkpoint']['audio_sha256']='f'*64
    if damage == 'new_asset': source['generated_asset_candidates']={'entries':['new']}
    if damage == 'unknown_model':
        c.sadd(reasoning.PREFIX+'lineage:'+r.root,'unknown-model')
        c.set(reasoning.PREFIX+'request:unknown-model',plan._raw({'context':{'lineage_id':r.root}}))
    c.set(jobs.JOB_PREFIX+task,plan._raw(source))
    before={k:c.dump(k)for k in c.scan_iter()};entry=source['spec']['content_plan_item_id']
    if damage:
        try: assert attention.isolate(source,client=c) is None
        except (plan.ContentPlanError,ValueError,TypeError): pass
        assert {k:c.dump(k)for k in c.scan_iter()} == before
    else:
        assert attention.isolate(source,client=c,observe_only=True)['state_writes']==0
        assert {k:c.dump(k)for k in c.scan_iter()} == before
        assert attention.isolate(source,client=c)=='attention_archived'
        assert not plan.owns_channel(r.channel)
        assert not c.exists(plan.COMPLETION_PREFIX+entry)
        assert cadence.snapshot(r.channel,client=c)['counts']['produced']['long']==1
        assert cadence.daily_editorial(r.channel,{},client=c)['format']=='shorts'
        archive=plan._object(c.get(attention.PREFIX+entry))
        proof=archive['provider_evidence']['reserved_fal_unknowns']
        assert proof['financial_status']=='unresolved_reserved'
        assert proof['retry_authorized'] is proof['refund_authorized'] is proof['daily_capacity_released'] is False
        assert len(proof['request_sha256s'])==1
        for key,value in before.items():
            if key not in {plan.PLAN_PREFIX+r.channel,plan.ACTIVE_KEY}: assert c.dump(key)==value


def test_unknown_fal_receipt_race_cannot_detach_the_terminal_film(rejected_correction, monkeypatch):
    from redis.exceptions import WatchError
    from app.services import content_plan_attention as attention
    r=rejected_correction;c=r.client;source=r.source;entry=source['spec']['content_plan_item_id']
    document=c.get(plan.PLAN_PREFIX+r.channel);active=c.get(plan.ACTIVE_KEY)
    execute=type(c.pipeline()).execute
    def change_before_commit(pipe,*args,**kwargs):
        journal=plan._object(c.get(video.PREFIX+r.root))
        next(row for row in journal['requests'].values()if row['result'] is None)['reserved_at']='2026-09-25T01:44:00+00:00'
        c.set(video.PREFIX+r.root,plan._raw(journal))
        return execute(pipe,*args,**kwargs)
    monkeypatch.setattr(type(c.pipeline()),'execute',change_before_commit)
    with pytest.raises(WatchError):attention.isolate(source,client=c)
    assert c.get(plan.PLAN_PREFIX+r.channel)==document and c.get(plan.ACTIVE_KEY)==active
    assert not c.exists(attention.PREFIX+entry,plan.COMPLETION_PREFIX+entry)
    assert plan._object(c.get(jobs.JOB_PREFIX+source['task_id']))==source


@pytest.mark.parametrize('damage', [None, 'hold', 'unknown_changed'])
def test_future_correctly_pinned_retained_failure_releases_queue_without_cut_retry(failed_cut, damage):
    from datetime import datetime, timezone
    from app.services import content_plan_attention as attention, channel_cadence as cadence
    from test_qa_workprint_access import pointer
    r=failed_cut;c=r.client;source=r.source;task=source['task_id'];channel=source['spec']['production_channel_id']
    p=pointer();old=p['task_id']
    p.update(task_id=task,version=4,width=1920,height=1080,duration_seconds=180.,frame_count=5400)
    p['key']=p['key'].replace(old,task);p['metadata_key']=p['metadata_key'].replace(old,task)
    source['qa_workprint']=p;r.metadata['video_sha256']=p['sha256']
    r.metadata['scenes'][0]['selection']['start_fraction']=0.
    document=plan.read(channel,client=c)
    plan.change(channel,document['revision'],'settings',payload={'enabled':True,'after_queue':'auto_shorts'},client=c)
    c.set(jobs.JOB_PREFIX+task,plan._raw(source))
    terminal=plan._object(c.get('celery-task-meta-'+task));terminal['date_done']=datetime.now(timezone.utc).isoformat()
    c.set('celery-task-meta-'+task,plan._raw(terminal));c.hset(cadence.keys(channel)[0],r.root,'long')
    if damage=='hold':source['publication_hold']={'reason':'owner'};c.set(jobs.JOB_PREFIX+task,plan._raw(source))
    if damage=='unknown_changed':
        journal=plan._object(c.get(video.PREFIX+r.root))
        next(row for row in journal['requests'].values()if row['result'] is None)['reserved_at']='2026-09-25T01:44:00+00:00'
        c.set(video.PREFIX+r.root,plan._raw(journal))
    before={key:c.dump(key)for key in c.scan_iter()};normal=Mock();repair=Mock()
    result=plan.maintain([{**r.profile,'channel_id':channel}],normal,repair_enqueue=repair)
    normal.assert_not_called();repair.assert_not_called();assert not c.exists(cut.DISPATCH+task)
    if damage:assert {key:c.dump(key)for key in c.scan_iter()}==before
    else:
        assert result['channels'][channel]=='attention_archived'
        assert not plan.owns_channel(channel)
        assert cadence.snapshot(channel,client=c)['counts']['produced']['long']==1
        archive=plan._object(c.get(attention.PREFIX+source['spec']['content_plan_item_id']))
        assert archive['provider_evidence']['reserved_fal_unknowns']['financial_status']=='unresolved_reserved'
        for key,value in before.items():
            if key not in {plan.PLAN_PREFIX+channel,plan.ACTIVE_KEY}:assert c.dump(key)==value
