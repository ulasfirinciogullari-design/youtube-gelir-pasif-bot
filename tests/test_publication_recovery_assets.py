"""Existing Storage and local FFmpeg only; no live credentials or providers."""
import ast
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '5fae28c5-7113-4794-b0f7-609fb39cc7d5'
PREP = '00000000-0000-0000-0000-000000000099'
OTHER = '00000000-0000-0000-0000-000000000098'
BASE = f'videos/{SOURCE}/'
JPEG = b'\xff\xd8\xff' + b'fake-jpeg-unit-fixture' * 100 + b'\xff\xd9'


def load_module():
    class Imports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None if (node.module or '').startswith('app') else node
    tree = Imports().visit(ast.parse((ROOT / 'app/services/publication_recovery_assets.py').read_text(encoding='utf-8')))
    namespace = {}
    # Execute the real approval gate, without importing Celery/config/providers.
    automation = ast.parse((ROOT / 'app/services/youtube_automation.py').read_text(encoding='utf-8'))
    gate = next(n for n in automation.body if isinstance(n, ast.FunctionDef) and n.name == 'automated_quality_approved')
    namespace['Any'] = object
    exec(compile(ast.Module(body=[gate], type_ignores=[]), '<actual-quality-gate>', 'exec'), namespace)
    exec(compile(tree, '<publication-assets>', 'exec'), namespace)
    return namespace


@pytest.fixture
def case(tmp_path):
    ns = load_module()
    ns['_TEMP_ROOT'] = tmp_path
    source = {
        'task_id': SOURCE, 'kind': 'render', 'state': 'SUCCESS',
        'spec': {'topic': 'IKEA flat-pack history', 'channel_id': 'margin-verdict', 'mode': 'production',
                 'format': 'shorts', 'duration_minutes': .5, 'language': 'en'},
        'result': {'task_id': SOURCE, 'status': 'complete', 'title': 'Why IKEA packs furniture flat',
                   'video_key': BASE + 'final.mp4', 'caption_key': BASE + 'captions.en.srt',
                   'metadata_key': BASE + 'metadata.json', 'scenes': 6,
                   'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False},
    }
    scenes = [{'index': i, 'narration': f'This is approved sentence {i + 1}.'} for i in range(6)]
    metadata = {'task_id': SOURCE, 'title': source['result']['title'], 'topic': source['spec']['topic'],
                'channel_id': 'margin-verdict', 'requested_duration_minutes': .5,
                'studio_options': {'mode': 'production', 'format': 'shorts'},
                'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
                'audio_qc': {'pass': True}, 'audio_duration_qc': {'pass': True},
                'audio_prosody_qc': {'pass': True}, 'scenes': scenes}
    srt = '\n\n'.join(f'{i+1}\n00:00:{i*5:02},000 --> 00:00:{(i+1)*5:02},000\n{s["narration"]}' for i, s in enumerate(scenes)) + '\n'
    objects = {
        BASE + 'metadata.json': [json.dumps(metadata).encode(), 'application/json'],
        BASE + 'final.mp4': [b'\x00\x00\x00\x20ftypisom' + b'video-fixture' * 100, 'video/mp4'],
        BASE + 'captions.en.srt': [srt.encode(), 'application/x-subrip'],
    }
    heads, gets, streams = {}, {}, []
    def response(key):
        data, mime = objects[key]
        return {'ETag': '"fixture-etag"', 'ContentLength': len(data), 'ContentType': mime,
                'Metadata': {'sha256': hashlib.sha256(data).hexdigest()},
                'ResponseMetadata': {'HTTPStatusCode': 200}}
    def head(**kwargs):
        return {**response(kwargs['Key']), **heads.get(kwargs['Key'], {})}
    def get(**kwargs):
        assert kwargs['IfMatch'] == '"fixture-etag"'
        overrides = gets.get(kwargs['Key'], {})
        body = overrides.get('Body', io.BytesIO(objects[kwargs['Key']][0]))
        result = {**response(kwargs['Key']), 'Body': body, **overrides}
        streams.append(result['Body'])
        return result
    client = SimpleNamespace(head_object=Mock(side_effect=head), get_object=Mock(side_effect=get),
                             put_object=Mock(side_effect=AssertionError('Storage write forbidden')))
    ns['storage'] = SimpleNamespace(_client=Mock(return_value=client), settings=SimpleNamespace(bucket='fixture-private'))
    video_probe = {'streams': [
        {'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
         'nb_read_frames': '900', 'avg_frame_rate': '30/1', 'duration': '30.000000'},
        {'codec_type': 'audio', 'codec_name': 'aac'},
    ]}
    image_probe = {'streams': [{'codec_type': 'video', 'codec_name': 'mjpeg', 'width': 720, 'height': 1280}]}
    def probe(command, **kwargs):
        assert kwargs['timeout'] == 60
        return json.dumps(image_probe if str(command[-1]).endswith('.jpg') else video_probe)
    def extract(command, **kwargs):
        assert kwargs['check'] is True and kwargs['timeout'] == 45 and kwargs.get('shell', False) is False
        Path(command[-1]).write_bytes(JPEG)
    process = SimpleNamespace(check_output=Mock(side_effect=probe), run=Mock(side_effect=extract), DEVNULL=subprocess.DEVNULL)
    ns['subprocess'] = process
    return SimpleNamespace(ns=ns, source=source, metadata=metadata, objects=objects, heads=heads, gets=gets,
                           client=client, streams=streams, process=process, video_probe=video_probe, image_probe=image_probe,
                           work=tmp_path / 'youtube_asset_recovery' / SOURCE / PREP)


def prepare(c, **kwargs):
    return c.ns['prepare_publication_recovery_assets'](c.source, source_task_id=SOURCE, work_dir=kwargs.get('work_dir', c.work))


def failure(c, **kwargs):
    with pytest.raises(c.ns['PublicationRecoveryAssetsError'], match='^publication_recovery_assets_unavailable$'):
        prepare(c, **kwargs)
    assert all(stream.closed for stream in c.streams)
    c.client.put_object.assert_not_called()


def save_metadata(c):
    c.objects[BASE + 'metadata.json'][0] = json.dumps(c.metadata).encode()


def test_prepares_only_existing_hash_bound_assets_and_one_real_portrait_frame(case):
    c = case
    before = deepcopy(c.source)
    result = prepare(c)
    assert set(result) == {'caption', 'thumbnail', 'final', 'metadata'}
    for name, asset in result.items():
        data = Path(asset['path']).read_bytes()
        assert asset['sha256'] == hashlib.sha256(data).hexdigest() and asset['size'] == len(data)
        assert Path(asset['path']).parent == c.work
    assert result['caption']['language'] == 'en'
    assert result['thumbnail']['origin'] == 'approved_final_frame'
    assert result['thumbnail']['source_sha256'] == result['final']['sha256']
    assert result['thumbnail']['frame_seconds'] == 1.0
    command = c.process.run.call_args.args[0]
    assert command[command.index('-frames:v') + 1] == '1'
    assert command[command.index('-ss') + 1] == '1.0'
    assert command[command.index('-i') + 1] == result['final']['path']
    assert command[command.index('-vf') + 1] == 'scale=720:1280:flags=lanczos,setsar=1'
    assert '-an' in command and '-n' in command and '-nostdin' in command
    assert '-protocol_whitelist' in command and 'file,pipe' in command
    c.process.run.assert_called_once()
    assert c.client.get_object.call_count == 3 and all(b.closed for b in c.streams)
    assert c.source == before
    c.client.put_object.assert_not_called()
    assert 'qa_approved' not in result and 'publish_eligible' not in result


@pytest.mark.parametrize('field,value', [
    ('state', 'FAILURE'), ('kind', 'publish'), ('task_id', OTHER), ('retry_child_task_id', OTHER),
])
def test_ineligible_source_never_reads_storage(case, field, value):
    case.source[field] = value
    failure(case)
    case.client.head_object.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('task_id', OTHER), ('status', 'pending'), ('title', ''), ('quality_disposition', 'manual_qa_preview'),
    ('manual_qa_required', True), ('video_key', f'videos/{OTHER}/final.mp4'),
    ('metadata_key', 'https://private.example/metadata.json'), ('caption_key', BASE + 'captions.tr.srt'),
])
def test_result_binding_never_reads_unrelated_assets(case, field, value):
    case.source['result'][field] = value
    failure(case)
    case.client.head_object.assert_not_called()


@pytest.mark.parametrize('field,value', [('mode', 'preview'), ('format', 'landscape'), ('duration_minutes', True),
                                          ('duration_minutes', 1), ('language', 'de')])
def test_spec_scope_is_exact(case, field, value):
    case.source['spec'][field] = value
    failure(case)
    case.client.head_object.assert_not_called()


@pytest.mark.parametrize('damage', ['root', 'other_source', 'bad_prep', 'traversal', 'occupied'])
def test_fresh_workdir_cannot_escape_task_or_overwrite(case, damage):
    c = case
    work = c.work
    if damage == 'root': work = c.ns['_TEMP_ROOT']
    if damage == 'other_source': work = c.ns['_TEMP_ROOT'] / 'youtube_asset_recovery' / OTHER / PREP
    if damage == 'bad_prep': work = c.work.parent / 'not-a-uuid'
    if damage == 'traversal': work = c.work / '..' / PREP
    if damage == 'occupied':
        work.mkdir(parents=True)
        (work / 'keep.txt').write_text('existing user data')
    failure(c, work_dir=work)
    c.client.head_object.assert_not_called()
    if damage == 'occupied': assert (work / 'keep.txt').read_text() == 'existing user data'


@pytest.mark.parametrize('key,maximum', [('metadata.json', 1024*1024), ('final.mp4', 64*1024*1024), ('captions.en.srt', 256*1024)])
def test_declared_oversize_is_rejected_before_body_download(case, key, maximum):
    case.heads[BASE + key] = {'ContentLength': maximum + 1}
    failure(case)
    assert not any(call.kwargs['Key'] == BASE + key for call in case.client.get_object.call_args_list)
    case.process.run.assert_not_called()


@pytest.mark.parametrize('damage', ['etag', 'status', 'length', 'encoding', 'wrong_hash', 'truncated', 'extra'])
def test_etag_safe_streams_fail_closed_and_close(case, damage):
    key = BASE + 'metadata.json'
    if damage == 'etag': case.gets[key] = {'ETag': '"changed"'}
    if damage == 'status': case.gets[key] = {'ResponseMetadata': {'HTTPStatusCode': 206}}
    if damage == 'length': case.gets[key] = {'ContentLength': True}
    if damage == 'encoding': case.gets[key] = {'ContentEncoding': 'gzip'}
    if damage == 'wrong_hash': case.gets[key] = {'Metadata': {'sha256': '0'*64}}
    if damage in ('truncated', 'extra'):
        data = case.objects[key][0]
        stream = io.BytesIO(data[:-1] if damage == 'truncated' else data + b'x')
        case.gets[key] = {'Body': stream}
        # Only streams actually returned must be closed by the reader.
        failure(case)
        assert stream.closed
        return
    failure(case)


@pytest.mark.parametrize('field', ['video_sha256', 'metadata_sha256', 'caption_sha256'])
def test_existing_expected_hash_cannot_be_ignored(case, field):
    case.source['result'][field] = '0' * 64
    failure(case)
    case.process.run.assert_not_called()


@pytest.mark.parametrize('damage', ['task', 'title', 'topic', 'scene_count', 'scene_order', 'qa', 'audio', 'mode'])
def test_metadata_binding_and_existing_qa_are_mandatory(case, damage):
    if damage == 'task': case.metadata['task_id'] = OTHER
    if damage == 'title': case.metadata['title'] = 'Other final cut'
    if damage == 'topic': case.metadata['topic'] = 'Unapproved topic'
    if damage == 'scene_count': case.metadata['scenes'].pop()
    if damage == 'scene_order': case.metadata['scenes'][0]['index'] = 1
    if damage == 'qa': case.metadata['manual_qa_required'] = True
    if damage == 'audio': case.metadata['audio_prosody_qc']['pass'] = False
    if damage == 'mode': case.metadata['studio_options']['mode'] = 'preview'
    save_metadata(case)
    failure(case)
    assert case.client.get_object.call_count == 1
    case.process.run.assert_not_called()


def test_duplicate_metadata_keys_reject(case):
    original = case.objects[BASE + 'metadata.json'][0]
    case.objects[BASE + 'metadata.json'][0] = b'{"task_id":"' + SOURCE.encode() + b'",' + original[1:]
    failure(case)


@pytest.mark.parametrize('damage', ['end', 'negative', 'overlap', 'number', 'text', 'empty', 'encoding'])
def test_srt_has_strict_time_sequence_and_same_approved_words(case, damage):
    key = BASE + 'captions.en.srt'
    data = case.objects[key][0]
    if damage == 'end': data = data.replace(b'00:00:30,000', b'00:00:30,001')
    if damage == 'negative': data = data.replace(b'00:00:00,000', b'-0:00:00,000')
    if damage == 'overlap': data = data.replace(b'00:00:05,000 --> 00:00:10', b'00:00:04,999 --> 00:00:10')
    if damage == 'number': data = data.replace(b'\n\n2\n', b'\n\n9\n')
    if damage == 'text': data = data.replace(b'approved', b'fabricated', 1)
    if damage == 'empty': data = b'\n'
    if damage == 'encoding': data = b'\xff\xff'
    case.objects[key][0] = data
    failure(case)
    case.process.run.assert_not_called()


@pytest.mark.parametrize('field,value', [('nb_read_frames', '899'), ('duration', '29.5'), ('duration', 'nan'),
                                        ('width', 1920), ('height', 1080), ('codec_name', 'other'), ('avg_frame_rate', '24/1')])
def test_actual_final_must_be_the_30_second_portrait_master(case, field, value):
    case.video_probe['streams'][0][field] = value
    failure(case)
    case.process.run.assert_not_called()


def test_approved_speech_tail_matches_both_retained_records_and_actual_frames(case):
    case.source['result']['duration'] = 30.5
    case.metadata['render'] = {'duration': 30.5}
    case.objects[BASE + 'metadata.json'][0] = json.dumps(case.metadata).encode()
    case.video_probe['streams'][0].update(duration='30.500000', nb_read_frames='915')
    assert prepare(case)['final']['sha256']


@pytest.mark.parametrize('duration,frames,stored', [(30.5, '900', 30.5), (30.5, '915', 30.0),
    (30.5, '915', None), (1000, '30000', 1000)])
def test_tail_cannot_change_without_matching_approved_metadata_and_bounded_real_master(case, duration, frames, stored):
    case.source['result']['duration'] = duration
    case.metadata['render'] = {'duration': stored}
    case.objects[BASE + 'metadata.json'][0] = json.dumps(case.metadata).encode()
    case.video_probe['streams'][0].update(duration=str(duration), nb_read_frames=frames)
    failure(case)


def test_authored_jpeg_is_preserved_byte_for_byte_without_frame_extraction(case):
    case.source['result']['thumbnail_key'] = BASE + 'thumbnail.jpg'
    case.objects[BASE + 'thumbnail.jpg'] = [JPEG, 'image/jpeg']
    result = prepare(case)
    assert result['thumbnail']['origin'] == 'authored'
    assert Path(result['thumbnail']['path']).read_bytes() == JPEG
    case.process.run.assert_not_called()


@pytest.mark.parametrize('key', [BASE + 'thumbnail.png', f'videos/{OTHER}/thumbnail.jpg', 'https://example.com/image.jpg', ''])
def test_invalid_or_unsupported_authored_thumbnail_is_never_silently_replaced(case, key):
    case.source['result']['thumbnail_key'] = key
    failure(case)
    case.process.run.assert_not_called()


def test_ffmpeg_failure_is_sanitized_and_never_retried(case):
    case.process.run.side_effect = RuntimeError('secret signed-url credential raw stderr')
    failure(case)
    case.process.run.assert_called_once()


def test_thumbnail_oversize_rejects(case):
    case.process.run.side_effect = lambda args, **_: Path(args[-1]).write_bytes(b'\xff\xd8\xff' + b'x' * (2 * 1024 * 1024))
    failure(case)


def test_source_mp4_header_is_required(case):
    case.objects[BASE + 'final.mp4'][0] = b'not a video container'
    failure(case)
    case.process.run.assert_not_called()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Native FFmpeg unavailable')
def test_native_extraction_keeps_real_portrait_frame_without_padding_or_new_video(tmp_path):
    ns = load_module()
    video = tmp_path / 'fixture.mp4'
    subprocess.run(['ffmpeg', '-n', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=108x192:r=30:d=2', '-an', '-c:v', 'mpeg4', str(video)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    output = tmp_path / 'thumbnail.jpg'
    before = video.read_bytes()
    final = {'path': str(video), 'sha256': hashlib.sha256(before).hexdigest()}
    provenance = ns['_thumbnail'](final, output)
    image = ns['_probe'](output, image=True)
    assert image['width'] == 720 and image['height'] == 1280
    assert 1 <= output.stat().st_size <= 2 * 1024 * 1024
    assert video.read_bytes() == before and provenance['source_sha256'] == final['sha256']
