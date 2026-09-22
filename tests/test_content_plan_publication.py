from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.services import content_plan
from test_youtube_synthetic_disclosure import worker, source


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
