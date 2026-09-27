from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest

from app.services import video_localization as languages

SRT = '1\n00:00:00,000 --> 00:00:02,400\nBir karar her şeyi değiştirdi.\n\n2\n00:00:02,400 --> 00:00:05,000\nPeki neden?\n'
NOW = datetime(2026, 9, 23, 22, tzinfo=timezone.utc)


def test_translation_preserves_exact_timing_and_cue_identity():
    cues = languages.parse_srt(SRT)
    translated = [{'id': 1, 'text': 'One decision changed everything.'}, {'id': 2, 'text': 'But why?'}]
    output = languages.translated_srt(cues, translated)
    assert [c['timing'] for c in languages.parse_srt(output)] == [c['timing'] for c in cues]
    for broken in (translated[::-1], translated[:1], [{'id': 1, 'text': '<script>'}, translated[1]]):
        with pytest.raises(ValueError): languages.translated_srt(cues, broken)


@pytest.mark.parametrize('text', [SRT.replace('00:00:02,400 -->', '00:00:02,000 -->'),
    SRT.replace('00:00:05,000', '00:00:01,000'), SRT.replace('Peki neden?', '<i>Peki neden?</i>')])
def test_invalid_original_subtitles_are_not_translated(text):
    with pytest.raises(ValueError): languages.parse_srt(text)


@pytest.fixture
def case(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    video = 'abcdefghijk'; key = languages.PREFIX + 'video:' + video
    record = {'source_task_id': 'source', 'video_id': video, 'channel_id': 'channel',
        'languages': {'en': {'status': 'prepared'}}}
    previous = languages._json(record); client.set(key, previous)
    service = Mock()
    service.captions.return_value.list.return_value.execute.return_value = {'items': []}
    name = 'Studio EN ' + languages._sha(SRT)[:12]
    response = {'id': 'caption-id', 'snippet': {'videoId': video, 'language': 'en', 'name': name,
        'status': 'serving', 'isDraft': False}}
    service.captions.return_value.insert.return_value.execute.return_value = response
    monkeypatch.setattr(languages, '_source', lambda *a: ({}, {}))
    monkeypatch.setattr(languages, '_public', lambda *a: {})
    return SimpleNamespace(client=client, key=key, record=record, previous=previous, service=service, response=response)


def step(c, now=NOW):
    return languages._caption_step(c.client, c.key, c.previous, c.record, 'en', c.service, SRT, now=now)


def test_accepted_caption_is_only_read_back_on_next_tick(case):
    c = case
    c.previous = step(c)
    assert c.record['languages']['en']['status'] == 'awaiting_processing'
    c.service.captions.return_value.insert.return_value.execute.assert_called_once_with(num_retries=0)
    step(c)
    assert c.service.captions.return_value.insert.call_count == 1
    c.service.captions.return_value.list.return_value.execute.return_value = {'items': [c.response]}
    c.previous = step(c, now=NOW + timedelta(minutes=16))
    assert c.record['languages']['en']['status'] == 'published'
    assert c.service.captions.return_value.insert.call_count == 1


def test_lost_caption_reply_is_never_reposted(case):
    c = case
    def uncertain(**kwargs):
        observed = json.loads(c.client.get(c.key))
        assert observed['languages']['en']['status'] == 'insert_reserved'
        raise TimeoutError('provider outcome unknown')
    c.service.captions.return_value.insert.return_value.execute.side_effect = uncertain
    c.previous = step(c)
    assert c.record['languages']['en']['status'] == 'uncertain'
    step(c)
    assert c.service.captions.return_value.insert.call_count == 1


def test_language_quota_share_leaves_primary_video_quota_available(case):
    c = case
    day = languages.PREFIX + 'quota_day:2026-09-23'
    c.client.sadd(day, *[str(i) for i in range(languages.MAX_CAPTION_WRITES_PER_DAY)])
    assert step(c) == c.previous
    c.service.captions.return_value.insert.assert_not_called()
    c.service.captions.return_value.list.assert_not_called()
    assert c.record['languages']['en']['status'] == 'prepared'


def test_existing_owner_caption_is_preserved(case):
    c = case
    external = deepcopy(c.response)
    external['snippet'].update(name='Owner authored', trackKind='standard')
    c.service.captions.return_value.list.return_value.execute.return_value = {'items': [external]}
    step(c)
    assert c.record['languages']['en']['status'] == 'existing_caption_preserved'
    c.service.captions.return_value.insert.assert_not_called()


def test_failed_translation_review_never_becomes_a_subtitle(monkeypatch):
    from app.services import production_included_router as model
    call = Mock(side_effect=[{'language': 'en', 'title': 'Title', 'description': '',
        'cues': [{'id': 1, 'text': 'Invented new information.'}, {'id': 2, 'text': 'Why?'}]},
        {'pass': False, 'issues': ['An invented fact.']}])
    monkeypatch.setattr(model, 'generate_text_json', call)
    with pytest.raises(ValueError, match='review_failed'):
        languages._translate(languages.parse_srt(SRT), 'en', {}, 'tr')
    assert call.call_count == 2


def test_pending_reply_checks_are_bounded_and_do_not_consume_primary_quota(case):
    c = case
    c.previous = step(c)
    first_reads = c.service.captions.return_value.list.call_count
    for _ in range(20):
        c.previous = step(c)
    assert c.service.captions.return_value.list.call_count == first_reads
    c.previous = step(c, NOW + timedelta(hours=1))
    assert c.service.captions.return_value.list.call_count == first_reads + 1
    for _ in range(20):
        c.previous = step(c, NOW + timedelta(hours=2))
    assert c.service.captions.return_value.list.call_count == first_reads + 1
    assert c.service.captions.return_value.insert.call_count == 1


def test_optional_caption_reads_have_shared_daily_cap(case):
    c = case
    for _ in range(languages.MAX_CAPTION_READS_PER_DAY):
        assert languages._caption_tracks(c.client, c.service, 'abcdefghijk', NOW) == []
    assert languages._caption_tracks(c.client, c.service, 'different', NOW) is None
    assert c.service.captions.return_value.list.call_count == languages.MAX_CAPTION_READS_PER_DAY


def test_registry_and_job_are_created_together_and_preserve_existing_accounting(case, monkeypatch):
    c = case
    from app.services import studio_state
    source = {'spec': {'language': 'tr', 'duration_minutes': 3}, 'result': {'title': 'Original'}}
    receipt = {'youtube_video_id': c.record['video_id'], 'target_channel_id': 'channel', 'connection_id': 'connection'}
    monkeypatch.setattr(languages, '_source', lambda *a: (source, receipt))
    monkeypatch.setattr(languages, '_current_connection', lambda *a: 'current-owner-connection')
    c.client.delete(c.key)
    record = languages._queue_record(c.client, 'source', 'new-task', ['en', 'es'], NOW)
    job = json.loads(c.client.get(studio_state.JOB_PREFIX + 'new-task'))
    assert record['status'] == 'queued' and job['kind'] == 'localization'
    assert job['parent_id'] is None and job['spec']['publish_after_render'] is False
    assert job['spec']['production_connection_id'] == 'current-owner-connection'
    assert c.client.sismember(languages.INDEX, c.record['video_id'])
    original = c.client.get(studio_state.JOB_PREFIX + 'new-task')
    assert languages._queue_record(c.client, 'source', 'new-task', ['en'], NOW) == record
    assert c.client.get(studio_state.JOB_PREFIX + 'new-task') == original


@pytest.fixture
def renewed_source(monkeypatch):
    from app.services import studio_state, youtube_publish_state, youtube_auth, youtube_automation
    client = fakeredis.FakeRedis(decode_responses=True)
    channel = 'UC5v9AvNtD3PTLgo6m1jROOA'
    task = '11111111-1111-4111-8111-111111111111'
    source = {'kind': 'render', 'state': 'SUCCESS', 'spec': {'production_channel_id': channel,
        'production_connection_id': 'original-owner-connection', 'language': 'tr'},
        'result': {'caption_key': f'videos/{task}/captions.tr.srt'}}
    receipt = {'status': 'complete', 'release_status': 'public', 'privacy_status': 'public',
        'youtube_video_id': 'abcdefghijk', 'target_channel_id': channel,
        'connection_id': 'original-owner-connection'}
    client.set(studio_state.JOB_PREFIX+task, json.dumps(source))
    client.set(youtube_publish_state.UPLOAD_PREFIX+task, json.dumps(receipt))
    client.set(youtube_auth.CHANNEL_PREFIX+channel, json.dumps({'id':channel,
        'connection_id':'current-owner-connection'}))
    client.set(youtube_auth.CREDENTIAL_PREFIX+channel, 'encrypted-current-owner-credential')
    client.sadd(youtube_auth.CHANNEL_INDEX_KEY,channel)
    monkeypatch.setattr(youtube_automation, 'automated_quality_approved', lambda job: True)
    return client, channel, task, source, receipt


def test_same_channel_renewal_keeps_original_publication_and_paid_identity(renewed_source):
    client, channel, task, source, receipt = renewed_source
    before={key:client.dump(key) for key in client.scan_iter()}
    assert languages._source(client,task)==(source,receipt)
    assert languages._current_connection(client,channel)=='current-owner-connection'
    assert {key:client.dump(key) for key in client.scan_iter()}==before


@pytest.mark.parametrize('damage',['other_channel','revoked','missing_credential','unindexed',
    'changed_upload_binding','held','not_public','failed_original'])
def test_renewal_never_authorizes_another_channel_or_ineligible_video(renewed_source,damage):
    from app.services import studio_state,youtube_publish_state,youtube_auth
    c,channel,task,source,receipt=renewed_source
    key=youtube_auth.CHANNEL_PREFIX+channel
    if damage=='other_channel':c.set(key,json.dumps({'id':'other','connection_id':'current-owner-connection'}))
    if damage=='revoked':c.set(key,json.dumps({'id':channel,'connection_id':'current-owner-connection','requires_reconnect':True}))
    if damage=='missing_credential':c.delete(youtube_auth.CREDENTIAL_PREFIX+channel)
    if damage=='unindexed':c.srem(youtube_auth.CHANNEL_INDEX_KEY,channel)
    if damage=='held':c.set(studio_state.QUALITY_HOLD_PREFIX+task,'hold')
    if damage=='changed_upload_binding':
        receipt['connection_id']='different-original';c.set(youtube_publish_state.UPLOAD_PREFIX+task,json.dumps(receipt))
    if damage=='not_public':
        receipt['release_status']='private';c.set(youtube_publish_state.UPLOAD_PREFIX+task,json.dumps(receipt))
    if damage=='failed_original':
        source['state']='FAILURE';c.set(studio_state.JOB_PREFIX+task,json.dumps(source))
    before={key:c.dump(key) for key in c.scan_iter()}
    with pytest.raises(ValueError):languages._source(c,task)
    assert {key:c.dump(key) for key in c.scan_iter()}==before


def test_connection_change_before_caption_reservation_never_writes(case,monkeypatch):
    c=case
    monkeypatch.setattr(languages,'_current_connection',lambda *a:'renewed-again')
    before=c.client.get(c.key)
    with pytest.raises(ValueError,match='connection_changed'):
        languages._caption_step(c.client,c.key,c.previous,c.record,'en',c.service,SRT,now=NOW,
            expected_connection_id='connection-used-to-load-credentials')
    assert c.client.get(c.key)==before
    c.service.captions.return_value.insert.assert_not_called()


def test_durable_upload_discovery_survives_job_window_and_prioritizes_core_languages():
    from app.services import youtube_publish_state, youtube_automation
    c=fakeredis.FakeRedis(decode_responses=True)
    channel='UC5v9AvNtD3PTLgo6m1jROOA';paused='UCgvESYtYbn2w9R2ExBOF_cw'
    c.set(youtube_automation.PROFILE_PREFIX+channel,json.dumps({'production_enabled':True,'auto_publish':True}))
    c.set(youtube_automation.PROFILE_PREFIX+paused,json.dumps({'production_enabled':False,'auto_publish':False}))
    for task,day,ch,status in [('old-hit','24',channel,'public'),('new','27',channel,'public'),
        ('extra','26',channel,'public'),('done','25',channel,'public'),('private','28',channel,'private'),
        ('paused','28',paused,'public')]:
        c.set(youtube_publish_state.UPLOAD_PREFIX+task,json.dumps({'source_task_id':task,
            'status':'complete','release_status':status,'target_channel_id':ch,
            'release_completed_at':'2026-09-'+day+'T12:00:00+00:00'}))
    known=[{'source_task_id':'done','status':'complete'},
        {'source_task_id':'extra','source_language':'tr','languages':{l:{'status':'published'} for l in languages.PRIMARY_LANGUAGES}}]
    before={key:c.dump(key) for key in c.scan_iter()}
    assert languages._candidate_sources(c,known)==['new','old-hit','extra']
    assert {key:c.dump(key) for key in c.scan_iter()}==before


def test_finished_localization_releases_only_its_own_lease():
    c=fakeredis.FakeRedis(decode_responses=True);key=languages.PREFIX+'running'
    c.set(key,'new-worker',ex=1500)
    assert languages.release_run('old-worker',client=c) is False
    assert c.get(key)=='new-worker'
    assert languages.release_run('new-worker',client=c) is True
    assert c.get(key) is None


@pytest.mark.parametrize('prior_writes,expected',[(0,['en','es','pt']),(13,['en'])])
def test_core_languages_get_first_slots_and_old_quota_reservations_stay_counted(monkeypatch,prior_writes,expected):
    from app.services import production_spend_runtime as runtime, storage, youtube, youtube_auth
    c=fakeredis.FakeRedis(decode_responses=True);video='abcdefghijk';key=languages.PREFIX+'video:'+video
    record={'task_id':'translation-task','source_task_id':'source','video_id':video,'channel_id':'channel',
        'metadata_status':'verified','metadata_additions':{},'languages':{l:{'status':'prepared',
        'srt_key':'saved/'+l+'.srt','srt_sha256':languages._sha(SRT)} for l in ('ar','en','es','hi','pt')}}
    c.set(key,languages._json(record))
    now=datetime.now(timezone.utc);quota=languages.PREFIX+'quota_day:'+languages._quota_day(now)
    if prior_writes:c.sadd(quota,*[str(i) for i in range(prior_writes)])
    original={ 'spec':{'language':'tr'},'result':{'caption_key':'original.srt'}}
    receipt={'youtube_video_id':video,'target_channel_id':'channel','connection_id':'old-owner'}
    monkeypatch.setattr(runtime,'configured_ledger',lambda **k:SimpleNamespace(client=c))
    monkeypatch.setattr(languages,'_source',lambda *a:(original,receipt))
    monkeypatch.setattr(languages,'_current_connection',lambda *a:'current-owner')
    monkeypatch.setattr(languages.strategy,'read_settings',lambda *a,**k:{'languages':['en','es','pt','hi','ar']})
    monkeypatch.setattr(storage,'download_file',lambda key,path:path.write_text(SRT))
    monkeypatch.setattr(youtube_auth,'load_credentials',lambda *a,**k:'credentials')
    service=Mock();monkeypatch.setattr(youtube,'_service',lambda *a:service)
    monkeypatch.setattr(languages,'_public',lambda *a:{'localizations':{}})
    service.captions.return_value.list.return_value.execute.return_value={'items':[]}
    inserted=[]
    def insert(**kwargs):
        snippet=kwargs['body']['snippet'];inserted.append(snippet['language'])
        response={'id':'caption-'+snippet['language'],'snippet':snippet}
        return SimpleNamespace(execute=lambda **k:response)
    service.captions.return_value.insert.side_effect=insert
    translate=Mock(side_effect=AssertionError('Prepared translations must not be regenerated'))
    monkeypatch.setattr(languages,'_translate',translate)
    result=languages.run('source','translation-task')
    assert inserted==expected and result['status']=='in_progress'
    assert c.scard(quota)==prior_writes+len(expected)
    for language in expected:assert result['languages'][language]=='awaiting_processing'
    for language in ('ar','hi'):assert result['languages'][language]=='prepared'
    translate.assert_not_called()
