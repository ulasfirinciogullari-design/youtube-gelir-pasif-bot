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
    c.client.delete(c.key)
    record = languages._queue_record(c.client, 'source', 'new-task', ['en', 'es'], NOW)
    job = json.loads(c.client.get(studio_state.JOB_PREFIX + 'new-task'))
    assert record['status'] == 'queued' and job['kind'] == 'localization'
    assert job['parent_id'] is None and job['spec']['publish_after_render'] is False
    assert c.client.sismember(languages.INDEX, c.record['video_id'])
    original = c.client.get(studio_state.JOB_PREFIX + 'new-task')
    assert languages._queue_record(c.client, 'source', 'new-task', ['en'], NOW) == record
    assert c.client.get(studio_state.JOB_PREFIX + 'new-task') == original
