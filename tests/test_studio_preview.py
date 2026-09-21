"""Owner playback must never imply QA approval, a retry or a publication."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from app.services import studio_preview as preview
from test_qa_workprint_access import pointer, TASK
from test_studio_workflow_presentation import ui, job, NOW


def request(method='GET'):
    return Request({'type': 'http', 'method': method, 'path': '/', 'headers': []})


def failed_workprint():
    return job(state='FAILURE', task_id=TASK, qa_workprint=pointer(), failure_stage='final_visual_qc_rescue')


def audio_job():
    return job(state='FAILURE', task_id=TASK, failure_stage='audio_qc', audio_candidate_checkpoint={
        'status': 'unapproved_candidate', 'qa_approved': False, 'requires_full_qa': True,
        'audio_sha256': 'a' * 64, 'audio_key': f'audio_candidates/{TASK}/{"a" * 64}/candidate.mp3',
        'size': 2000})


@pytest.fixture
def store(monkeypatch):
    client = Mock()
    client.head_object.return_value = {'ContentLength': 2000, 'ContentType': 'audio/mpeg'}
    client.generate_presigned_url.return_value = 'https://media.example/short-lived-owner-playback'
    monkeypatch.setattr(preview.storage, '_client', lambda **kwargs: client)
    monkeypatch.setattr(preview.storage.settings, 'bucket', 'private-fixture', raising=False)
    return client


def test_failed_workprint_is_playable_without_approval_or_any_job_change(monkeypatch):
    record = failed_workprint()
    before = deepcopy(record)
    stream = Mock(return_value='stream-response')
    monkeypatch.setattr(preview.qa_workprint_access, 'stream_response', stream)
    value = preview.describe(record)
    assert value['kind'] == 'video' and value['variant'] == 'workprint'
    assert value['url'] == f'/studio/job/{TASK}/preview-media'
    assert 'qa_workprints/' not in str(value)
    assert preview.media_response(record, request()) == 'stream-response'
    assert stream.call_args.args[0] == pointer() and record == before


def test_failed_audio_uses_fresh_five_minute_link_and_never_mutates_checkpoint(store):
    record = audio_job()
    before = deepcopy(record)
    response = preview.media_response(record, request())
    assert response.status_code == 307 and response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert store.generate_presigned_url.call_args.kwargs['ExpiresIn'] == 300
    assert 'inline' in store.generate_presigned_url.call_args.kwargs['Params']['ResponseContentDisposition']
    assert record == before and record['audio_candidate_checkpoint']['qa_approved'] is False


def test_final_file_ignores_expired_saved_url_and_leaves_delivery_unchanged(store):
    record = job(state='SUCCESS', task_id=TASK, result={'video_key': 'videos/20260901/final.mp4',
        'download_url': 'https://expired.example/old-signed-url', 'manual_qa_required': True})
    before = deepcopy(record)
    store.head_object.return_value = {'ContentLength': 2000, 'ContentType': 'video/mp4'}
    assert preview.describe(record)['url'] == f'/studio/job/{TASK}/preview-media'
    assert preview.media_response(record, request()).status_code == 307
    assert record == before


@pytest.mark.parametrize('field,value', [
    ('qa_approved', True), ('requires_full_qa', False), ('size', 0), ('size', True),
    ('size', preview.MAX_AUDIO_BYTES + 1), ('audio_sha256', 'wrong'),
    ('audio_key', 'audio_candidates/another-task/candidate.mp3'),
    ('audio_key', 'https://untrusted.example/sound.mp3'),
])
def test_bad_or_cross_task_audio_pointer_never_contacts_storage(store, field, value):
    record = audio_job()
    record['audio_candidate_checkpoint'][field] = value
    assert preview.describe(record) is None
    assert preview.media_response(record, request()).status_code == 404
    store.head_object.assert_not_called()


@pytest.mark.parametrize('key', ['videos/../secrets.mp4', '/videos/final.mp4',
    'https://example.test/final.mp4', 'videos/file.mp4?secret=1', 'private/credentials.json'])
def test_untrusted_final_key_is_not_a_storage_or_redirect_input(store, key):
    record = job(state='SUCCESS', result={'video_key': key})
    assert preview.describe(record) is None
    preview.media_response(record, request())
    store.head_object.assert_not_called()


@pytest.mark.parametrize('head', [
    {'ContentLength': 2001, 'ContentType': 'audio/mpeg'},
    {'ContentLength': 2000, 'ContentType': 'text/html'},
    {'ContentLength': 2000, 'ContentType': 'audio/mpeg', 'ContentEncoding': 'gzip'},
])
def test_changed_or_wrong_type_media_does_not_issue_a_link(store, head):
    store.head_object.return_value = head
    assert preview.media_response(audio_job(), request()).status_code == 503
    store.generate_presigned_url.assert_not_called()


def test_anonymous_preview_fails_before_job_or_storage_lookup(ui, store):
    ui.records[TASK] = audio_job()
    ui.client.cookies.clear()
    response = ui.client.get(f'/studio/job/{TASK}/preview-media', follow_redirects=False)
    assert response.status_code in {303, 401}
    ui.lookup.assert_not_called()
    store.head_object.assert_not_called()


def test_owner_can_read_rejected_audio_and_api_only_adds_public_preview_fields(ui, store):
    ui.records[TASK] = audio_job()
    before = deepcopy(ui.records)
    response = ui.client.get(f'/studio/job/{TASK}/preview-media', follow_redirects=False)
    assert response.status_code == 307
    data = ui.client.get(f'/studio/api/job/{TASK}').json()
    assert data['owner_preview'] == preview.describe(audio_job())
    assert data['upload_allowed'] is False
    assert ui.records == before
    ui.forbidden.assert_not_called()


def test_old_failed_workprint_stays_accessible_even_when_its_child_has_no_media(ui):
    record = failed_workprint()
    record['updated_ts'] = NOW - 8 * 86400
    child = job(12, parent_id=TASK, failure_stage='research')
    record.update(retry_child_task_id=child['task_id'], retry_claimed=True)
    ui.records.update({record['task_id']: record, child['task_id']: child})
    before = deepcopy(ui.records)
    response = ui.client.get('/studio/history?status=previews&media=video')
    assert response.status_code == 200 and f'/studio/job/{TASK}/preview-media' in response.text
    assert 'Kalite kontrolünü geçmedi. Yayımlanmaz.' in response.text
    assert 'method="post"' not in response.text
    assert ui.records == before
    ui.forbidden.assert_not_called()


def test_failure_before_media_is_honest_and_search_is_escaped(ui):
    record = job(failure_stage='research')
    ui.records[record['task_id']] = record
    response = ui.client.get('/studio/history?status=attention')
    assert 'Video dosyası henüz oluşmadı.' in response.text
    assert '<video ' not in response.text and '<audio ' not in response.text
    response = ui.client.get('/studio/history', params={'status': 'attention', 'q': '\"><script>alert(1)</script>'})
    assert '"><script>alert(1)</script>' not in response.text
    ui.forbidden.assert_not_called()
