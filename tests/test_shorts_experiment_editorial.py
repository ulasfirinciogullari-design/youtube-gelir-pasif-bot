from copy import deepcopy
import json
from uuid import uuid4

import fakeredis
import pytest

from app.config import settings
from app.services import content_plan as plan, shorts_experiment as batch, shorts_experiment_editorial as edit
from app.services import studio_state as jobs, channel_production as production, channel_cadence as cadence

C, M = tuple(batch.COUNTS)
NOW = 1790373600.0
DAY = '2026-09-26'


def fixture(monkeypatch, failed=True):
    client = fakeredis.FakeRedis(decode_responses=True)
    entries = {channel: [plan.item('Story', str(uuid4())) for _ in range(count)] for channel,count in batch.COUNTS.items()}
    manifest = {'version':1,'id':'ten','day':DAY,'items':[{'item_id':v['id'],'channel_id':c,'item_sha256':plan._sha(v)}for c,vs in entries.items()for v in vs]}
    client.set(batch.approval_key(DAY), plan._raw(manifest))
    item=entries[C][0];root=batch.root_id(C,item['id'])
    profile={'production_enabled':True,'auto_publish':True,'release_mode':'public','profile_revision':'revision'}
    client.set(production.PROFILE_PREFIX+C, plan._raw(profile))
    document={'version':1,'channel_id':C,'revision':str(uuid4()),'enabled':True,'after_queue':'auto_shorts','items':entries[C],'updated_at':item['created_at']}
    client.set(plan.PLAN_PREFIX+C,plan._raw(document))
    source=None
    if failed:
        error='Director could not produce fully stock-safe short-preview scenes: must contain one simple sentence'
        spec={'format':'shorts','content_plan_item_id':item['id'],'production_channel_id':C}
        source={'task_id':root,'kind':'render','state':'FAILURE','parent_id':None,'paid_create_slots_used':0,'failure_stage':'director_qc','error':error,'spec':spec}
        client.set(jobs.JOB_PREFIX+root,plan._raw(source))
        client.set(plan.DISPATCH_PREFIX+item['id'],plan._raw({'item':item,'task_id':root,'channel_id':C,'profile_revision':'revision','spec_sha256':plan._sha(spec)}))
        client.set(plan.ACTIVE_KEY,plan._raw({C:item['id'],M:'11111111-1111-4111-8111-111111111111'}))
        client.hset(batch.produced_key(DAY,C),root,'shorts')
        client.set('celery-task-meta-'+root,plan._raw({'task_id':root,'status':'FAILURE','result':{'exc_type':'ProductionContentError','exc_message':[error]}}))
    monkeypatch.setattr(edit,'_settled',lambda *a:{'verified':'receipts'})
    return client,item,root,source,manifest


def test_known_prevoice_brand_rejection_allows_fresh_draft_without_rewriting_failure(monkeypatch):
    c, item, root, source, manifest = fixture(monkeypatch)
    source['error'] = 'Short-preview editorial gate rejected narration before paid media: ' + json.dumps({
        'issues': ['scene 1 uses TTS-unsafe raw term(s): IKEA']})
    c.set(jobs.JOB_PREFIX + root, plan._raw(source))
    terminal = {'task_id': root, 'status': 'FAILURE', 'result': {
        'exc_type': 'ProductionContentError', 'exc_message': [source['error']]}}
    c.set('celery-task-meta-' + root, plan._raw(terminal))
    original = c.get(jobs.JOB_PREFIX + root)
    result = edit.replace(item['id'], plan.item('Ikea', 'Ikea paletleri taşıyor.'),
        expected_job_sha256=plan._sha(source), client=c, now=NOW)
    assert result['status'] == 'editorial_replaced'
    assert c.get(jobs.JOB_PREFIX + root) == original
    assert c.get('celery-task-meta-' + root) == plan._raw(terminal)
    assert c.hget(batch.produced_key(DAY, C), root) == 'shorts'
    assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
    for error in (source['error'].replace('IKEA', 'GPS'),
                  source['error'].replace('scene 1', 'scene 7'),
                  'Short-preview editorial gate rejected narration before paid media: {}'):
        assert not edit._eligible({**source, 'error': error})


def test_failed_draft_replacement_preserves_job_hold_and_old_production_count(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch)
    c.set(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX+root,'unchanged negative verdict')
    original=c.get(jobs.JOB_PREFIX+root);grant=c.get(batch.approval_key(DAY))
    new=plan.item('Corrected story','New simple sentence and unchanged cited facts')
    result=edit.replace(item['id'],new,expected_job_sha256=plan._sha(source),client=c,now=NOW)
    assert result['status']=='editorial_replaced'
    assert c.get(jobs.JOB_PREFIX+root)==original and c.get(batch.approval_key(DAY))==grant
    assert c.get(jobs.QUALITY_HOLD_JOB_FENCE_PREFIX+root)=='unchanged negative verdict'
    assert c.hget(batch.produced_key(DAY,C),root)=='shorts'
    assert not c.exists(plan.COMPLETION_PREFIX+item['id'],jobs.JOB_PREFIX+result['new_root'])
    assert plan._active(c)=={M:'11111111-1111-4111-8111-111111111111'}
    assert plan.read(C,client=c)['items'][0]==new
    assert not json.loads(c.get(edit.ARCHIVE+item['id']))['qa_approved']


@pytest.mark.parametrize('mutation',['running','paid_visual','publication','unknown_provider','changed_job','cancelled','retry'])
def test_uncertain_or_ineligible_prior_work_cannot_be_replaced(monkeypatch,mutation):
    c,item,root,source,manifest=fixture(monkeypatch);expected=plan._sha(source)
    if mutation=='running':source['state']='PROGRESS'
    if mutation=='paid_visual':source['paid_create_slots_used']=1
    if mutation=='cancelled':source['owner_cancellation']=True
    if mutation=='retry':source['retry_claimed']=True
    if mutation=='changed_job':source['error']+=' changed'
    c.set(jobs.JOB_PREFIX+root,plan._raw(source))
    if mutation=='publication':c.set(cadence.PREFIX+'publication:'+root,'existing claim')
    if mutation=='unknown_provider':
        def blocked(*a):raise ValueError('unresolved request')
        monkeypatch.setattr(edit,'_settled',blocked)
    before=c.get(plan.PLAN_PREFIX+C);active=c.get(plan.ACTIVE_KEY)
    with pytest.raises(ValueError):edit.replace(item['id'],plan.item('New','New text'),expected_job_sha256=expected,client=c,now=NOW)
    assert c.get(plan.PLAN_PREFIX+C)==before and c.get(plan.ACTIVE_KEY)==active
    assert not c.exists(batch.replacement_key(DAY,item['id']),edit.ARCHIVE+item['id'])


def test_unstarted_editorial_amendment_does_not_clear_another_active_item(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch,False)
    c.set(plan.ACTIVE_KEY,plan._raw({C:'22222222-2222-4222-8222-222222222222'}))
    result=edit.replace(item['id'],plan.item('New','Revised future narration'),client=c,now=NOW)
    assert result['old_job_preserved'] and plan._active(c)=={C:'22222222-2222-4222-8222-222222222222'}
    assert not c.exists(batch.produced_key(DAY,C))


def test_rejected_abstract_draft_can_be_reauthored_without_approving_original(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch)
    source['error']='Director could not produce fully stock-safe short-preview scenes: ' + json.dumps({
        'positions':[0,1,2,3,4,5], 'generator_calls':3, 'critic_calls':0,
        'failures':[{'position':3,'reason':'position 3 contains an unfilmable abstraction'}]})
    c.set(jobs.JOB_PREFIX+root,plan._raw(source))
    terminal={'task_id':root,'status':'FAILURE','result':{'exc_type':'ProductionContentError','exc_message':[source['error']]}}
    c.set('celery-task-meta-'+root,plan._raw(terminal))
    original=c.get(jobs.JOB_PREFIX+root)
    result=edit.replace(item['id'],plan.item('Concrete scene','A phone scans a visibly stained label'),
        expected_job_sha256=plan._sha(source),client=c,now=NOW)
    assert result['status']=='editorial_replaced' and c.get(jobs.JOB_PREFIX+root)==original
    assert json.loads(c.get('celery-task-meta-'+root))==terminal
    assert c.hget(batch.produced_key(DAY,C),root)=='shorts'
    assert not c.exists(plan.COMPLETION_PREFIX+item['id'])


def test_observation_is_read_only_and_duplicate_operator_is_rejected(monkeypatch):
    c,item,root,source,manifest=fixture(monkeypatch)
    new=plan.item('Fixed','Reviewed new script');kwargs={'expected_job_sha256':plan._sha(source),'client':c,'now':NOW}
    before=sorted(c.keys('*'))
    assert edit.replace(item['id'],new,observe_only=True,**kwargs)['eligible']
    assert sorted(c.keys('*'))==before
    edit.replace(item['id'],new,**kwargs)
    with pytest.raises(ValueError):edit.replace(item['id'],new,**kwargs)


def visual_fixture(monkeypatch):
    import hashlib
    from uuid import uuid5, NAMESPACE_URL
    from app.services import content_plan_recovery as recovery
    c, item, root, source, manifest = fixture(monkeypatch)
    error = 'Final visual quality gate rejected: ' + json.dumps({
        'stage': 'after_rescue', 'accepted': 5, 'total': 6,
        'rejected': {'5': {'score': 30}}, 'repair_checkpoint_available': False})
    source.update(error=error, failure_stage='final_visual_qc_rescue', paid_create_slots_used=6,
        audio_candidate_checkpoint={'preserved': True}, included_stock_pools={'preserved': True},
        generated_asset_candidates={'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0,
            'entries': [{'preserved': True}] * 6},
        failure_classification={'category': 'content_rejected', 'code': 'visual_quality_exhausted',
            'error_sha256': hashlib.sha256(error.encode()).hexdigest()})
    c.set(jobs.JOB_PREFIX + root, plan._raw(source))
    c.set('celery-task-meta-' + root, plan._raw({'task_id': root, 'status': 'FAILURE',
        'result': {'exc_type': 'FinalVisualQualityError', 'exc_message': [error]}}))
    operation = str(uuid5(NAMESPACE_URL, 'owner-plan-render-recovery:v4:' + root))
    c.set(recovery.DISPATCH + root, plan._raw({'version': 1, 'source_task_id': root,
        'task_id': operation, 'source_sha256': recovery._fingerprint(source)}))
    c.set(recovery.EXECUTION + root, operation)
    c.set(recovery.STATUS + root, plan._raw({'state': 'stopped', 'error_type': 'RuntimeError'}))
    c.set('celery-task-meta-' + operation, plan._raw({'task_id': operation, 'status': 'FAILURE',
        'result': {'exc_type': 'RuntimeError',
            'exc_message': ['Saved stock candidates did not pass independent exact-cut review']}}))
    return c, item, root, source, operation


def test_exhausted_visual_and_stock_reviews_allow_new_story_but_preserve_negative_evidence(monkeypatch):
    from app.services import content_plan_recovery as recovery
    c, item, root, source, operation = visual_fixture(monkeypatch)
    original = {k: c.get(k) for k in (jobs.JOB_PREFIX + root, 'celery-task-meta-' + root,
        recovery.DISPATCH + root, recovery.EXECUTION + root, recovery.STATUS + root,
        'celery-task-meta-' + operation)}
    result = edit.replace(item['id'], plan.item('Rivets', 'A different sourced product mechanism'),
        expected_job_sha256=plan._sha(source), client=c, now=NOW)
    assert all(c.get(k) == raw for k, raw in original.items())
    archive = json.loads(c.get(edit.ARCHIVE + item['id']))
    assert archive['retained_recovery']['terminal'] == original['celery-task-meta-' + operation]
    assert not archive['qa_approved'] and not archive['old_publish_eligible']
    assert not c.exists(plan.COMPLETION_PREFIX + item['id'], jobs.JOB_PREFIX + result['new_root'])
    assert c.hget(batch.produced_key(DAY, C), root) == 'shorts'


@pytest.mark.parametrize('mutation', ['preparing', 'uncertain', 'missing_terminal', 'unexpected_failure',
    'approved_recovery', 'changed_dispatch', 'missing_execution', 'missing_candidate',
    'changed_negative_evidence', 'repair_checkpoint', 'unknown_provider'])
def test_visual_reauthor_cannot_detach_unresolved_or_recoverable_work(monkeypatch, mutation):
    from app.services import content_plan_recovery as recovery
    c, item, root, source, operation = visual_fixture(monkeypatch)
    if mutation in ('preparing', 'uncertain'):
        c.set(recovery.STATUS + root, plan._raw({'state': mutation}))
    if mutation == 'missing_terminal': c.delete('celery-task-meta-' + operation)
    if mutation == 'unexpected_failure':
        terminal = json.loads(c.get('celery-task-meta-' + operation))
        terminal['result']['exc_message'] = ['lost provider response']
        c.set('celery-task-meta-' + operation, plan._raw(terminal))
    if mutation == 'approved_recovery': c.set(recovery.RECORD + root, '{}')
    if mutation == 'changed_dispatch':
        value = json.loads(c.get(recovery.DISPATCH + root)); value['source_sha256'] = 'changed'
        c.set(recovery.DISPATCH + root, plan._raw(value))
    if mutation == 'missing_execution': c.delete(recovery.EXECUTION + root)
    if mutation == 'missing_candidate': source['generated_asset_candidates']['preserved_count'] = 5
    if mutation == 'changed_negative_evidence': source['failure_classification']['error_sha256'] = 'changed'
    if mutation == 'repair_checkpoint': c.set(jobs.REPAIR_CHECKPOINT_PREFIX + root, '{}')
    if mutation == 'unknown_provider':
        def blocked(*a): raise ValueError('unresolved request')
        monkeypatch.setattr(edit, '_settled', blocked)
    c.set(jobs.JOB_PREFIX + root, plan._raw(source))
    before = c.get(plan.PLAN_PREFIX + C); active = c.get(plan.ACTIVE_KEY)
    with pytest.raises(ValueError):
        edit.replace(item['id'], plan.item('New', 'New source grounded story'),
            expected_job_sha256=plan._sha(source), client=c, now=NOW)
    assert c.get(plan.PLAN_PREFIX + C) == before and c.get(plan.ACTIVE_KEY) == active
    assert not c.exists(edit.ARCHIVE + item['id'], batch.replacement_key(DAY, item['id']))


@pytest.mark.parametrize('mutation', [None, 'missing_trace', 'different_guard', 'different_module',
    'different_error', 'preparing', 'different_callsite', 'extra_trace'])
def test_only_conclusive_local_cut_exhaustion_allows_a_fresh_story(monkeypatch, mutation):
    from app.services import content_plan_recovery as recovery
    c, item, root, source, operation = visual_fixture(monkeypatch)
    trace = ('  File "/app/app/services/content_plan_stock_repair.py", line 89, in select\n'
        '    _require(prepared)\n'
        '  File "/app/app/services/content_plan_stock_repair.py", line 15, in _require\n'
        "    plan._require(value, 'plan_stock_repair_unverified')\n"
        '  File "/app/app/services/content_plan.py", line 43, in _require\n'
        '    raise ContentPlanError(code)\n'
        'app.services.content_plan.ContentPlanError: plan_stock_repair_unverified\n')
    terminal = {'task_id': operation, 'status': 'FAILURE', 'traceback': trace,
        'result': {'exc_type': 'ContentPlanError', 'exc_module': 'app.services.content_plan',
            'exc_message': ['plan_stock_repair_unverified']}}
    if mutation == 'missing_trace': terminal.pop('traceback')
    if mutation == 'different_guard': terminal['traceback'] = trace.replace('_require(prepared)', '_require(all(candidates))')
    if mutation == 'different_module': terminal['result']['exc_module'] = 'other'
    if mutation == 'different_error': terminal['result']['exc_message'] = ['provider_uncertain']
    if mutation == 'different_callsite': terminal['traceback'] = trace.replace('in select', 'in prepare')
    if mutation == 'extra_trace': terminal['traceback'] += 'Another error was raised\n'
    c.set('celery-task-meta-' + operation, plan._raw(terminal))
    c.set(recovery.STATUS + root, plan._raw({'state': 'preparing' if mutation == 'preparing' else 'stopped',
        'error_type': 'ContentPlanError'}))
    before = {k: c.get(k) for k in (jobs.JOB_PREFIX + root, 'celery-task-meta-' + operation,
        recovery.STATUS + root, plan.PLAN_PREFIX + C, plan.ACTIVE_KEY)}
    kwargs = {'expected_job_sha256': plan._sha(source), 'client': c, 'now': NOW}
    new = plan.item('Andon', 'A sourced factory signal story')
    if mutation:
        with pytest.raises(ValueError): edit.replace(item['id'], new, **kwargs)
        assert all(c.get(k) == value for k, value in before.items())
        assert not c.exists(edit.ARCHIVE + item['id'])
    else:
        result = edit.replace(item['id'], new, **kwargs)
        assert result['status'] == 'editorial_replaced'
        assert all(c.get(k) == before[k] for k in (jobs.JOB_PREFIX + root,
            'celery-task-meta-' + operation, recovery.STATUS + root))
        assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
        assert not json.loads(c.get(edit.ARCHIVE + item['id']))['qa_approved']
