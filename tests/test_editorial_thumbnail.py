from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services import editorial_thumbnail as cover
from test_editorial_publish_boundary import boundary


TASK_ID = '5ccb3b50-019d-4c8b-912d-6151a2719427'
JPEG = b'\xff\xd8\xff' + b'bounded mocked jpeg' + b'\xff\xd9'


@pytest.fixture
def local(monkeypatch, tmp_path):
    root = tmp_path / 'youtube_publish'
    work = root / TASK_ID
    work.mkdir(parents=True)
    monkeypatch.setattr(cover, '_PUBLISH_ROOT', root)
    video, output = work / 'final.mp4', work / 'editorial-thumbnail.jpg'
    video.write_bytes(b'actual-source-fixture')
    expected = {'sha256': hashlib.sha256(video.read_bytes()).hexdigest(), 'size': video.stat().st_size}
    receipt = {'receipt_sha256': 'a' * 64,
               'evidence': {'manifest': {'files': {'video': expected}},
                            'frames': [{'frame_index': 60, 'sha256': 'b' * 64}]},
               'server_proof': {'video_sha256': expected['sha256'], 'media_structure': {
                   'frame_rate': '30/1', 'frame_count': 900, 'width': 1080, 'height': 1920}}}
    calls = []
    def encode(command, **kwargs):
        calls.append(command)
        output.write_bytes(JPEG)
    monkeypatch.setattr(cover.subprocess, 'run', encode)
    monkeypatch.setattr(cover.subprocess, 'check_output', lambda *_a, **_k: json.dumps({
        'streams': [{'codec_name': 'mjpeg', 'width': 720, 'height': 1280}]}).encode())
    return video, output, receipt, calls


def test_fixed_reviewed_frame_prepares_without_any_quality_claim(local):
    video, output, receipt, calls = local
    result = cover.prepare_editorial_thumbnail(video, output, receipt)
    assert result['frame_index'] == 60 and result['frame_seconds'] == 2.0
    assert result['sha256'] == hashlib.sha256(JPEG).hexdigest()
    assert result['source_sha256'] == receipt['server_proof']['video_sha256']
    assert 'qa_approved' not in result and 'publish_eligible' not in result
    command = calls[0]
    assert len(calls) == 1 and command[command.index('-vf') + 1] == 'select=eq(n\\,60),scale=720:1280:flags=lanczos,setsar=1'
    assert command[command.index('-protocol_whitelist') + 1] == 'file'
    assert command[command.index('-frames:v') + 1] == '1' and '-n' in command
    cover.validate_editorial_thumbnail(video, output, receipt, result)


@pytest.mark.parametrize('damage', ['missing_frame', 'duplicate_frame', 'boolean_frame', 'different_frame',
    'wrong_source_hash', 'source_changed', 'existing_output', 'wrong_path', 'wrong_format',
    'source_changed_during_encode', 'malformed_jpeg', 'wrong_geometry'])
def test_invalid_binding_or_extraction_fails_closed(local, monkeypatch, damage):
    video, output, receipt, calls = local
    if damage == 'missing_frame': receipt['evidence']['frames'] = []
    elif damage == 'duplicate_frame': receipt['evidence']['frames'] *= 2
    elif damage == 'boolean_frame': receipt['evidence']['frames'][0]['frame_index'] = True
    elif damage == 'different_frame': receipt['evidence']['frames'][0]['frame_index'] = 61
    elif damage == 'wrong_source_hash': receipt['server_proof']['video_sha256'] = 'c' * 64
    elif damage == 'source_changed': video.write_bytes(b'changed source')
    elif damage == 'existing_output': output.write_bytes(JPEG)
    elif damage == 'wrong_path': output = output.with_name('other.jpg')
    elif damage in {'wrong_format', 'wrong_geometry'}:
        monkeypatch.setattr(cover.subprocess, 'check_output', lambda *_a, **_k: json.dumps({'streams': [{
            'codec_name': 'png' if damage == 'wrong_format' else 'mjpeg',
            'width': 721 if damage == 'wrong_geometry' else 720, 'height': 1280}]}).encode())
    else:
        def broken(command, **kwargs):
            output.write_bytes(JPEG if damage == 'source_changed_during_encode' else b'not jpeg')
            if damage == 'source_changed_during_encode': video.write_bytes(b'changed source')
        monkeypatch.setattr(cover.subprocess, 'run', broken)
    with pytest.raises(cover.EditorialThumbnailError, match='preparation_unavailable'):
        cover.prepare_editorial_thumbnail(video, output, receipt)


def test_no_caller_selected_frame_or_false_automated_approval_parameter(local):
    video, output, receipt, _ = local
    for extra in ({'frame_index': 61}, {'qa_approved': True}):
        with pytest.raises(TypeError):
            cover.prepare_editorial_thumbnail(video, output, receipt, **extra)


@pytest.mark.parametrize('damage', ['jpeg_bytes', 'receipt', 'selected_frame'])
def test_same_local_jpeg_and_receipt_are_required_at_use(local, damage):
    video, output, receipt, _ = local
    result = cover.prepare_editorial_thumbnail(video, output, receipt)
    if damage == 'jpeg_bytes': output.write_bytes(b'changed jpg')
    elif damage == 'receipt': receipt['receipt_sha256'] = 'c' * 64
    else: receipt['evidence']['frames'][0]['sha256'] = 'c' * 64
    with pytest.raises(cover.EditorialThumbnailError, match='changed_or_unavailable'):
        cover.validate_editorial_thumbnail(video, output, receipt, result)


@pytest.fixture
def publishing_cover(boundary, monkeypatch):
    b = boundary
    b.record['publish_plan']['require_thumbnail'] = True
    b.cover_calls, b.cover_uploads = [], []
    def prepare(video, output, receipt):
        assert receipt['evidence']['frames'][0]['frame_index'] == 60
        b.cover_calls.append(('prepare', b.events.count('insert')))
        output.write_bytes(JPEG)
        b.cover_path = output
        return {'sha256': hashlib.sha256(JPEG).hexdigest(), 'size': len(JPEG)}
    def validate(video, output, receipt, metadata):
        b.cover_calls.append(('validate', b.events.count('insert')))
        if hashlib.sha256(output.read_bytes()).hexdigest() != metadata['sha256']:
            raise cover.EditorialThumbnailError('changed')
    def upload(_creds, video_id, path):
        b.cover_uploads.append((video_id, Path(path).read_bytes()))
        return {'items': [{'id': video_id}]}
    monkeypatch.setattr(cover, 'prepare_editorial_thumbnail', prepare)
    monkeypatch.setattr(cover, 'validate_editorial_thumbnail', validate)
    monkeypatch.setattr(b.module, 'upload_thumbnail_with_credentials', upload)
    return b


def test_required_editorial_cover_is_prepared_before_insert_then_uploaded_without_storage(publishing_cover):
    b = publishing_cover
    result = b.run()
    assert b.cover_calls == [('prepare', 0), ('validate', 0), ('validate', 1), ('validate', 1)]
    assert b.cover_uploads == [('REAL_EDITORIAL_ID', JPEG)]
    assert result['thumbnail_uploaded'] is True and result['release_status'] == 'public'
    assert b.record['publish_plan']['require_thumbnail'] is True
    assert b.record['publish_plan']['thumbnail_key'] is None
    assert b.events.count(('download', 'external/captions.srt')) == 1
    assert len([event for event in b.events if isinstance(event, tuple) and event[0] == 'download']) == 2


def test_cover_preparation_failure_has_no_private_insert(publishing_cover, monkeypatch):
    b = publishing_cover
    def fail(*_a):
        raise cover.EditorialThumbnailError('redacted')
    monkeypatch.setattr(cover, 'prepare_editorial_thumbnail', fail)
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts and not b.releases and not b.cover_uploads


def test_revocation_during_local_extraction_stops_private_insert(publishing_cover, monkeypatch):
    b = publishing_cover
    prepare = cover.prepare_editorial_thumbnail
    def revoke(video, output, receipt):
        metadata = prepare(video, output, receipt)
        b.authority_current = False
        return metadata
    monkeypatch.setattr(cover, 'prepare_editorial_thumbnail', revoke)
    with pytest.raises(RuntimeError, match='preflight failed'):
        b.run()
    assert not b.inserts and not b.releases and not b.cover_uploads


@pytest.mark.parametrize('when', ['during_video_upload', 'after_thumbnail_upload'])
def test_changed_cover_keeps_actual_private_id_and_blocks_release(publishing_cover, monkeypatch, when):
    b = publishing_cover
    if when == 'during_video_upload':
        b.after_insert = lambda: b.cover_path.write_bytes(b'changed')
    else:
        def upload(*_a):
            b.cover_path.write_bytes(b'changed')
            return {'items': [{}]}
        monkeypatch.setattr(b.module, 'upload_thumbnail_with_credentials', upload)
    result = b.run()
    assert result['release_status'] == 'blocked' and result['privacy_status'] == 'private'
    assert result['youtube_video_id'] == b.source['result']['youtube']['video_id'] == 'REAL_EDITORIAL_ID'
    assert not b.releases and not b.failures and len(b.inserts) == 1
    assert b.run()['idempotent_replay'] is True and len(b.inserts) == 1


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Native FFmpeg unavailable')
def test_real_ffmpeg_extracts_reviewed_frame_60_not_first_frame(tmp_path, monkeypatch):
    root = tmp_path / 'youtube_publish'
    work = root / TASK_ID
    work.mkdir(parents=True)
    monkeypatch.setattr(cover, '_PUBLISH_ROOT', root)
    video, output = work / 'final.mp4', work / 'editorial-thumbnail.jpg'
    subprocess.run(['ffmpeg', '-n', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=red:s=720x1280:r=30:d=30', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '30',
        '-vf', 'drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable=gte(n\\,60)',
        '-c:a', 'aac', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(video)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    expected = {'sha256': hashlib.sha256(video.read_bytes()).hexdigest(), 'size': video.stat().st_size}
    # The real 30s/900-frame local fixture changes colour exactly at frame 60.
    receipt = {'receipt_sha256': 'a' * 64, 'evidence': {'manifest': {'files': {'video': expected}},
        'frames': [{'frame_index': 60, 'sha256': 'b' * 64}]}, 'server_proof': {
        'video_sha256': expected['sha256'], 'media_structure': {'frame_rate': '30/1', 'frame_count': 900,
                                                            'width': 720, 'height': 1280}}}
    result = cover.prepare_editorial_thumbnail(video, output, receipt)
    pixel = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(output),
        '-vf', 'scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
        stderr=subprocess.DEVNULL, timeout=15)
    assert len(pixel) == 3 and pixel[2] > 200 and pixel[0] < 30
    assert result['width'] == 720 and result['height'] == 1280 and result['frame_index'] == 60
    assert hashlib.sha256(video.read_bytes()).hexdigest() == expected['sha256']
    cover.validate_editorial_thumbnail(video, output, receipt, result)
