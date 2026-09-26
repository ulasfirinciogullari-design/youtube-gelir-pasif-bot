from unittest.mock import Mock
from uuid import uuid4

import pytest
import fakeredis

from app.services import content_plan
from app.services import youtube_quota_recovery as quota
from test_youtube_synthetic_disclosure import worker, source
from test_experiment_quota_continuation import GoogleError


@pytest.fixture(autouse=True)
def quota_state(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(content_plan, '_client', lambda: client)
    return client


@pytest.mark.parametrize('queued,required,public', [(True,False,True),(True,True,False),(False,False,False),(False,True,False)])
def test_optional_caption_failure_is_specific_to_verified_owner_queue(worker, monkeypatch, queued, required, public):
    candidate=source(caption_key='accepted/captions.srt')
    if queued:
        candidate['spec']['content_plan_item_id']=str(uuid4())
    worker.ns['get_job'].return_value=candidate
    worker.ns['get_upload_record'].return_value['publish_plan']['caption_required']=required
    validation=Mock()
    monkeypatch.setattr(content_plan,'check_publication',validation)
    worker.ns['download_file'].side_effect=lambda key,path:path.write_bytes(b'accepted bytes')
    worker.ns['upload_caption_with_credentials'].side_effect=RuntimeError('Optional caption rejected')
    result=worker.run()
    assert result['caption_error_code']=='RuntimeError'
    assert result['release_status']==('public' if public else 'blocked')
    assert worker.ns['set_video_release_with_credentials'].call_count==int(public)
    if queued:
        assert validation.call_count==2


def test_queue_revocation_after_upload_keeps_the_same_video_private(worker, monkeypatch):
    candidate=source(caption_key='accepted/captions.srt')
    candidate['spec']['content_plan_item_id']=str(uuid4())
    worker.ns['get_job'].return_value=candidate
    worker.ns['get_upload_record'].return_value['publish_plan']['caption_required']=False
    monkeypatch.setattr(content_plan,'check_publication',Mock(side_effect=[None,ValueError('changed assignment')]))
    result=worker.run()
    assert result['youtube_video_id']=='same-video' and result['release_status']=='blocked'
    assert result['release_error_code']=='content_plan_publication_changed'
    worker.ns['set_video_release_with_credentials'].assert_not_called()


@pytest.mark.parametrize('already_waiting', [False, True])
def test_optional_caption_quota_preserves_normal_upload_release_and_defers_only_captions(
        worker, monkeypatch, quota_state, already_waiting):
    candidate = source(caption_key='accepted/captions.srt')
    candidate['spec']['content_plan_item_id'] = str(uuid4())
    worker.ns['get_job'].return_value = candidate
    worker.ns['get_upload_record'].return_value['publish_plan']['caption_required'] = False
    monkeypatch.setattr(content_plan, 'check_publication', Mock())
    worker.ns['download_file'].side_effect = lambda key, path: path.write_bytes(b'accepted bytes')
    worker.ns['upload_caption_with_credentials'].side_effect = GoogleError()
    if already_waiting:
        quota.observe_optional_caption_quota(GoogleError(), client=quota_state)
    result = worker.run()
    assert result['release_status'] == 'public' and result['caption_uploaded'] is False
    assert worker.ns['upload_caption_with_credentials'].call_count == int(not already_waiting)
    worker.ns['upload_video_with_credentials'].assert_called_once()
    worker.ns['set_video_release_with_credentials'].assert_called_once()
    assert quota.waiting(client=quota_state) is None
    assert quota.optional_caption_waiting(client=quota_state) is not None
    if already_waiting: assert result['caption_error_code'] == 'youtube_caption_quota_wait'


def test_required_caption_failure_keeps_existing_blocking_policy(worker, monkeypatch, quota_state):
    candidate = source(caption_key='accepted/captions.srt')
    candidate['spec']['content_plan_item_id'] = str(uuid4())
    worker.ns['get_job'].return_value = candidate
    worker.ns['get_upload_record'].return_value['publish_plan']['caption_required'] = True
    monkeypatch.setattr(content_plan, 'check_publication', Mock())
    worker.ns['download_file'].side_effect = lambda key, path: path.write_bytes(b'accepted bytes')
    worker.ns['upload_caption_with_credentials'].side_effect = GoogleError()
    quota.observe_optional_caption_quota(GoogleError(), client=quota_state)
    result = worker.run()
    assert result['release_status'] == 'blocked'
    worker.ns['upload_caption_with_credentials'].assert_called_once()
    worker.ns['set_video_release_with_credentials'].assert_not_called()
    assert quota.waiting(client=quota_state) is not None
