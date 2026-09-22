"""Queue order and actual Redis races, with forbidden provider transport."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
import pytest

from app.services import content_plan as plan, channel_production as production
from app.services import production_spend_runtime as spending
from app.services import studio_state as jobs
from app.services.youtube_publish_state import UPLOAD_PREFIX

CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
OTHER = 'UCgvESYtYbn2w9R2ExBOF_cw'


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plan, '_client', lambda: client)
    funding = Mock(return_value={'available': True})
    monkeypatch.setattr(spending, 'preflight_scheduled_production', funding)
    profile = {'channel_id': CHANNEL, 'profile_revision': 'profile-revision-0001',
               'production_enabled': True, 'auto_publish': True, 'release_mode': 'public',
               'default_language': 'tr', 'route_label': 'capital-corrupt', 'series_id': 'legacy-series',
               'series_name': 'Different old series', 'series_total': 1, 'require_thumbnail': False}
    for channel in (CHANNEL, OTHER):
        client.set(production.PROFILE_PREFIX + channel, plan._raw({**profile, 'channel_id': channel}))
        client.set(production.OAUTH_CHANNEL_PREFIX + channel, plan._raw({'id': channel, 'connection_id': 'connection-current'}))
        client.set(production.OAUTH_CREDENTIAL_PREFIX + channel, 'opaque-private-test-credential')
        client.sadd(production.OAUTH_CHANNEL_INDEX, channel)
    a = plan.item('İlk bölüm', 'Kaynağı belirtilen ilk hikâye', series={'id': 'original-series', 'name': 'Paranın Arka Yüzü', 'number': 5, 'total': 8})
    b = plan.item('Sonraki bölüm', 'Kaynağı belirtilen sonraki hikâye', series={'id': 'original-series', 'name': 'Paranın Arka Yüzü', 'number': 6, 'total': 8}, depends_on=[a['id']])
    document = plan.change(CHANNEL, 'new', 'append', payload=[a,b], client=client)
    return SimpleNamespace(client=client, profile=profile, a=a, b=b, document=document, funding=funding)


def test_read_and_projection_do_not_dispatch_or_write(case):
    before = {key: case.client.dump(key) for key in case.client.scan_iter()}
    view = plan.project(plan.read(CHANNEL))
    assert [v['label'] for v in view['items']] == ['Sırada', 'Sırada']
    assert before == {key: case.client.dump(key) for key in case.client.scan_iter()}
    case.funding.assert_not_called()


def test_stale_browser_cannot_overwrite_new_settings(case):
    changed = plan.change(CHANNEL, case.document['revision'], 'settings', payload={'enabled': False, 'after_queue': 'pause'})
    with pytest.raises(plan.ContentPlanError, match='plan_changed'):
        plan.change(CHANNEL, case.document['revision'], 'remove', payload={'id': case.a['id']})
    assert plan.read(CHANNEL) == changed
    assert plan.owns_channel(CHANNEL)


@pytest.mark.parametrize('action,entry', [('down','a'), ('up','b'), ('remove','a')])
def test_series_dependencies_and_order_survive_edits(case, action, entry):
    before = case.client.get(plan.PLAN_PREFIX + CHANNEL)
    with pytest.raises(plan.ContentPlanError, match='order_invalid'):
        plan.change(CHANNEL, case.document['revision'], action, payload={'id': getattr(case,entry)['id']})
    assert case.client.get(plan.PLAN_PREFIX + CHANNEL) == before


def test_simultaneous_ticks_reserve_once_and_freeze_episode(case):
    enqueue = Mock()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: plan.maintain([case.profile], enqueue), range(4)))
    assert enqueue.call_count == 1
    call = enqueue.call_args.kwargs
    assert call['retry'] is False and call['args'][4]['content_plan_item_id'] == case.a['id']
    source = json.loads(case.client.get(jobs.JOB_PREFIX + call['task_id']))
    assert source['spec']['production_scheduled'] is True
    assert plan.publication_series(source, case.profile) == case.a['series']
    assert case.client.get('youtube_studio:youtube_series_counter:v1:'+CHANNEL+':original-series') is None
    for action in ('remove','down'):
        with pytest.raises(plan.ContentPlanError, match='plan_item_started'):
            plan.change(CHANNEL, case.document['revision'], action, payload={'id': case.a['id']})


def test_lost_broker_reply_never_sends_again(case):
    enqueue = Mock(side_effect=TimeoutError('unknown delivery'))
    first = plan.maintain([case.profile], enqueue)
    assert first['channels'][CHANNEL] == 'uncertain'
    plan.maintain([case.profile], enqueue)
    assert enqueue.call_count == 1
    assert json.loads(case.client.get(plan.DELIVERY_PREFIX + case.a['id']))['status'] == 'uncertain'


def test_existing_scheduled_capacity_prevents_third_job(case):
    claims = {'version': 2, 'claims': [{'channel_id': CHANNEL, 'task_id': str(uuid4())},
                                     {'channel_id': OTHER, 'task_id': str(uuid4())}]}
    case.client.set(production.ACTIVE_KEY, plan._raw(claims))
    enqueue = Mock()
    assert plan.maintain([case.profile], enqueue)['channels'][CHANNEL] == 'capacity_wait'
    enqueue.assert_not_called()
    assert not case.client.exists(plan.DISPATCH_PREFIX + case.a['id'])


def test_failed_or_private_video_never_unblocks_next_episode(case):
    enqueue = Mock(); plan.maintain([case.profile], enqueue)
    task = enqueue.call_args.kwargs['task_id']; key=jobs.JOB_PREFIX + task
    source=json.loads(case.client.get(key)); source.update(state='FAILURE', stage='failed')
    case.client.set(key,plan._raw(source))
    for _ in range(3):
        plan.maintain([case.profile], enqueue)
    assert enqueue.call_count == 1
    assert plan.project(plan.read(CHANNEL))['items'][0]['status'] == 'blocked'
    assert not case.client.exists(plan.DISPATCH_PREFIX + case.b['id'])


def public_fixture(case, *, series=None):
    enqueue=Mock(); plan.maintain([case.profile], enqueue)
    task=enqueue.call_args.kwargs['task_id']; publisher_id=str(uuid4())
    source=json.loads(case.client.get(jobs.JOB_PREFIX+task))
    binding={'target_channel_id':CHANNEL,'connection_id':'connection-current',
             'privacy_status':'public','release_status':'public'}
    plan_value={'source_task_id':task,'target_channel_id':CHANNEL,'profile_revision':case.profile['profile_revision'],
                'series':case.a['series'] if series is None else series}
    source.update(state='SUCCESS',result={'video_key':'accepted/master.mp4','caption_key':'accepted/captions.srt',
        'quality_disposition':'automated_qc_pass','manual_qa_required':False,
        'youtube':{**binding,'video_id':'abcdefghijk'}})
    publisher={'task_id':publisher_id,'kind':'publish','parent_id':task,'state':'SUCCESS',
               'result':{**binding,'youtube_video_id':'abcdefghijk'}}
    receipt={'source_task_id':task,'publish_task_id':publisher_id,'status':'complete',
             'youtube_video_id':'abcdefghijk','publish_plan':plan_value,**binding}
    case.client.set(jobs.JOB_PREFIX+task, plan._raw(source))
    case.client.set(jobs.JOB_PREFIX+publisher_id, plan._raw(publisher))
    case.client.set(UPLOAD_PREFIX+task, plan._raw(receipt))
    return task,source,receipt,publisher


def test_exact_public_result_advances_once_without_profile_or_counter_changes(case):
    task,*_=public_fixture(case)
    before_profile=case.client.get(production.PROFILE_PREFIX+CHANNEL)
    enqueue=Mock()
    result=plan.maintain([case.profile],enqueue)
    assert result['channels'][CHANNEL] == 'enqueued' and enqueue.call_count == 1
    assert enqueue.call_args.kwargs['args'][4]['content_plan_item_id'] == case.b['id']
    assert json.loads(case.client.get(plan.COMPLETION_PREFIX+case.a['id']))['source_task_id'] == task
    assert case.client.get(production.PROFILE_PREFIX+CHANNEL) == before_profile
    plan.maintain([case.profile],enqueue)
    assert enqueue.call_count == 1


@pytest.mark.parametrize('corrupt', ['series','private','wrong_channel','failed_publisher','unapproved','wrong_video'])
def test_partial_or_conflicting_public_evidence_cannot_advance(case, corrupt):
    task,source,receipt,publisher=public_fixture(case)
    if corrupt=='series':receipt['publish_plan']['series']['number']=7
    if corrupt=='private':source['result']['youtube']['privacy_status']='private'
    if corrupt=='wrong_channel':publisher['result']['target_channel_id']=OTHER
    if corrupt=='failed_publisher':publisher['state']='FAILURE'
    if corrupt=='unapproved':source['result']['manual_qa_required']=True
    if corrupt=='wrong_video':source['result']['youtube']['video_id']='wrongvideo1'
    case.client.set(jobs.JOB_PREFIX+task,plan._raw(source))
    case.client.set(jobs.JOB_PREFIX+publisher['task_id'],plan._raw(publisher))
    case.client.set(UPLOAD_PREFIX+task,plan._raw(receipt))
    enqueue=Mock();plan.maintain([case.profile],enqueue)
    enqueue.assert_not_called()
    assert not case.client.exists(plan.COMPLETION_PREFIX+case.a['id'])


def test_pause_leaves_started_work_and_history_untouched(case):
    enqueue=Mock();plan.maintain([case.profile],enqueue)
    dispatch=case.client.get(plan.DISPATCH_PREFIX+case.a['id'])
    plan.change(CHANNEL,case.document['revision'],'settings',payload={'enabled':False,'after_queue':'auto_shorts'})
    plan.maintain([case.profile],enqueue)
    assert enqueue.call_count==1 and case.client.get(plan.DISPATCH_PREFIX+case.a['id'])==dispatch
    assert plan.owns_channel(CHANNEL)


def test_unsupported_format_stays_explicit_preparation_without_spend(case):
    long=plan.item('Uzun belgesel','Üç dakikalık kaynaklı anlatım','long')
    document=plan.change(OTHER,'new','add',payload=long)
    before=case.funding.call_count
    enqueue=Mock();plan.maintain([{**case.profile,'channel_id':OTHER}],enqueue)
    enqueue.assert_not_called();assert case.funding.call_count==before
    assert plan.project(document)['items'][0]['status']=='preparation'
