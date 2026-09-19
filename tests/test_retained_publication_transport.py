"""Genuine private final and plan, with synthetic YouTube responses only."""
from pathlib import Path

import pytest

from app.services import retained_publication_transport as transport
from app.services import retained_publication_plan as publication
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from test_retained_publication_plan import (
    executing, admitted, corrected_visual, eligible, rejected, qualified, captured,
    source, case, planning_case, real_media, prepared, frozen_three, completed_probe,
    wire, forbid_live_transport, prepare,
)
from test_production_connection_continuity import _dump


@pytest.mark.parametrize('outcome', ['public', 'upload_unknown', 'release_unknown'])
def test_one_private_upload_and_ordered_public_receipt_or_permanent_unknown(executing, monkeypatch, outcome):
    box = executing
    value = prepare(box)
    record = value.record
    before = _dump(box.client)
    calls = []
    video_id = 'Synthetic01'
    credentials = object()
    monkeypatch.setattr(transport.youtube_auth, 'load_credentials',
        lambda channel, **kw: credentials if channel == admission.continuity.CHANNEL_ID
        and kw == {'expected_connection_id': record['connection_id'], 'refresh': True} else None)
    monkeypatch.setattr(transport.youtube_auth, '_channel_from_credentials',
        lambda actual: {'id': admission.continuity.CHANNEL_ID} if actual is credentials else {})
    def effect(phase):
        assert box.client.pttl(transport._key(phase, 'intent')) == -1
        assert not box.client.exists(transport._key(phase, 'result'))
        calls.append(phase)
    def upload(actual, path, title, description, **kw):
        effect('upload')
        assert actual is credentials and Path(path).name == 'final.mp4'
        assert title == record['plan']['title'] and description == record['plan']['description']
        assert kw['privacy_status'] == 'private' and kw['contains_synthetic_media'] is True
        if outcome == 'upload_unknown': raise TimeoutError('PRIVATE unobserved video ID')
        return {'id': video_id, 'status': {'privacyStatus': 'private'}}
    def status(actual, video):
        assert actual is credentials and video == video_id
        phase = 'public_status' if 'release' in calls else 'private_status'
        effect(phase)
        return {'privacyStatus': 'public' if phase == 'public_status' else 'private',
                'containsSyntheticMedia': True, 'uploadStatus': 'processed'}
    def captions(actual, video, path, language):
        effect('captions')
        assert actual is credentials and video == video_id and Path(path).suffix == '.srt' and language == 'tr'
        return {'id': 'SyntheticCaption01', 'snippet': {'videoId': video, 'language': language}}
    def release(actual, video, mode, **kw):
        effect('release')
        assert actual is credentials and video == video_id and mode == 'public' and kw == {'contains_synthetic_media': True}
        assert box.client.exists(transport._key('captions', 'result'), transport._key('thumbnail', 'result')) == 2
        if outcome == 'release_unknown': raise TimeoutError('PRIVATE unobserved release')
        return {'id': video_id, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}}
    monkeypatch.setattr(transport.youtube, 'upload_video_with_credentials', upload)
    monkeypatch.setattr(transport.youtube, 'get_video_status_with_credentials', status)
    monkeypatch.setattr(transport.youtube, 'upload_caption_with_credentials', captions)
    monkeypatch.setattr(transport.youtube, 'set_video_release_with_credentials', release)
    monkeypatch.setattr(transport.youtube, 'upload_thumbnail_with_credentials',
                        lambda *a, **k: pytest.fail('Optional thumbnail must not become another upload'))
    if outcome == 'public':
        try:
            receipt = transport.publish_retained_final(value)
        except transport.RetainedPublicationError as error:
            locations = []
            current = error
            while current is not None:
                tb = current.__traceback__
                while tb:
                    locations.append((tb.tb_frame.f_code.co_name, tb.tb_lineno));tb = tb.tb_next
                current = current.__context__
            pytest.fail(f'Actual publisher failed at {locations}', pytrace=False)
        assert receipt['privacy_status'] == 'public' and receipt['video_id'] == video_id
        assert receipt['contains_synthetic_media'] is receipt['captions_uploaded'] is True
        assert receipt['resume_authorized'] is receipt['next_production_authorized'] is False
        assert receipt['thumbnail_status'] == 'not_required'
        assert calls == ['upload', 'private_status', 'captions', 'release', 'public_status']
        assert box.client.pttl(transport.PUBLIC_RECEIPT_KEY) == -1
        assert len(list((box.render_root / 'retained-publication-records').glob('*.json'))) == 6
    else:
        with pytest.raises(transport.RetainedPublicationError) as error:
            transport.publish_retained_final(value)
        assert 'PRIVATE' not in str(error.value)
        assert not box.client.exists(transport.PUBLIC_RECEIPT_KEY)
        phase = 'upload' if outcome == 'upload_unknown' else 'release'
        assert box.client.pttl(transport._key(phase, 'intent')) == -1
        assert not box.client.exists(transport._key(phase, 'result'))
    assert calls.count('upload') == 1 and calls.count('release') <= 1
    after = _dump(box.client)
    assert all(box.client.dump(k) == v for k, v in before.items())
    with pytest.raises(transport.RetainedPublicationError): transport.publish_retained_final(value)
    with pytest.raises(delivery.RetainedDispatchError):
        delivery.acquire_retained_execution(box.client, box.child, box.manifest_sha)
    assert _dump(box.client) == after
    assert len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    box.forbidden_publication.assert_not_called()


def test_unissued_plan_cannot_access_oauth_or_any_transport(monkeypatch):
    monkeypatch.setattr(transport.youtube_auth, 'load_credentials', lambda *a, **k: pytest.fail('No OAuth'))
    for value in (None, {}, object.__new__(publication.PreparedRetainedPublication)):
        with pytest.raises(transport.RetainedPublicationError): transport.publish_retained_final(value)
