"""Local-only approved-frame preparation and the real worker artifact hook."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK = '11111111-1111-4111-8111-111111111111'


def _module(name):
    path = ROOT / 'app' / 'services' / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    ns = {'__name__': name}
    exec(compile(tree, str(path), 'exec'), ns)
    return ns


@pytest.fixture
def case(tmp_path):
    ns = _module('publication_thumbnail')
    ns['_TEMP_ROOT'] = tmp_path
    work = tmp_path / 'youtube_factory' / f'{TASK}_attempt_0'
    work.mkdir(parents=True)
    video, output = work / 'final.mp4', work / 'thumbnail.jpg'
    video.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'existing approved final')
    source = {'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1080,
                           'height': 1920, 'avg_frame_rate': '30/1'}, {'codec_type': 'audio'}],
              'format': {'duration': '30.0'}}
    image = {'streams': [{'codec_type': 'video', 'codec_name': 'mjpeg', 'width': 720, 'height': 1280}]}
    runner = Mock(side_effect=lambda *args, **kwargs: output.write_bytes(b'\xff\xd8\xffreal-frame\xff\xd9'))
    probe = Mock(side_effect=lambda args, **kwargs: json.dumps(source if args[-1] == str(video) else image))
    ns['subprocess'] = SimpleNamespace(run=runner, check_output=probe, DEVNULL=subprocess.DEVNULL)
    return SimpleNamespace(ns=ns, work=work, video=video, output=output, source=source, image=image,
                           runner=runner, probe=probe)


def _prepare(case, **changes):
    args = dict(task_id=TASK, video_path=case.video, output_path=case.output, video_format='shorts', qa_approved=True)
    args.update(changes)
    return case.ns['prepare_approved_final_thumbnail'](**args)


@pytest.mark.parametrize('video_format,source_geometry,geometry', [
    ('shorts', (1080, 1920), (720, 1280)), ('landscape', (1920, 1080), (1280, 720)),
])
def test_exact_actual_frame_keeps_format_and_does_not_edit_final(case, video_format, source_geometry, geometry):
    case.source['streams'][0].update(width=source_geometry[0], height=source_geometry[1])
    case.image['streams'][0].update(width=geometry[0], height=geometry[1])
    before = case.video.read_bytes()
    result = _prepare(case, video_format=video_format)
    assert result['status'] == 'prepared' and result['origin'] == 'approved_final_frame'
    assert result['frame_seconds'] == 1.0 and result['source_sha256'] == hashlib.sha256(before).hexdigest()
    assert (result['width'], result['height']) == geometry
    assert result['sha256'] == hashlib.sha256(case.output.read_bytes()).hexdigest()
    assert case.video.read_bytes() == before and 'path' not in result
    command = case.runner.call_args.args[0]
    assert command.count('-frames:v') == 1 and command[command.index('-frames:v') + 1] == '1'
    assert '-n' in command and '-nostdin' in command and '-y' not in command
    assert not any(word in ' '.join(command) for word in ('drawtext', 'http:', 'https:'))
    assert case.runner.call_args.kwargs['timeout'] == 45


@pytest.mark.parametrize('changes', [
    {'qa_approved': False}, {'qa_approved': 1}, {'qa_approved': None}, {'video_format': 'unknown'},
    {'task_id': '22222222-2222-4222-8222-222222222222'}, {'task_id': 'invalid'},
    {'video_path': 'https://example.test/final.mp4'}, {'output_path': 'relative/thumbnail.jpg'},
])
def test_invalid_or_unapproved_inputs_fail_before_encoder(case, changes):
    with pytest.raises(case.ns['PublicationThumbnailError'], match='^thumbnail_preparation_unavailable$'):
        _prepare(case, **changes)
    case.runner.assert_not_called()


def test_existing_thumbnail_is_not_overwritten(case):
    case.output.write_bytes(b'owner-existing-file')
    with pytest.raises(case.ns['PublicationThumbnailError']):
        _prepare(case)
    assert case.output.read_bytes() == b'owner-existing-file'
    case.runner.assert_not_called()


@pytest.mark.parametrize('change', ['wrong_geometry', 'no_audio', 'not_h264', 'bad_fps', 'nonfinite_duration', 'bad_mp4'])
def test_raw_or_malformed_master_is_not_a_thumbnail_source(case, change):
    if change == 'wrong_geometry':
        case.source['streams'][0]['width'] = 1920
    elif change == 'no_audio':
        case.source['streams'].pop()
    elif change == 'not_h264':
        case.source['streams'][0]['codec_name'] = 'mjpeg'
    elif change == 'bad_fps':
        case.source['streams'][0]['avg_frame_rate'] = '24/1'
    elif change == 'nonfinite_duration':
        case.source['format']['duration'] = 'nan'
    else:
        case.video.write_bytes(b'not an MP4')
    with pytest.raises(case.ns['PublicationThumbnailError']):
        _prepare(case)
    case.runner.assert_not_called()


@pytest.mark.parametrize('change', ['oversize', 'wrong_geometry', 'not_jpeg', 'source_changed', 'encoder_error'])
def test_invalid_output_or_changed_source_is_never_approved(case, change):
    if change == 'wrong_geometry':
        case.image['streams'][0]['width'] = 1280
    elif change == 'encoder_error':
        case.runner.side_effect = RuntimeError('SECRET encoder output /private/path')
    else:
        def run(*_args, **_kwargs):
            content = b'\xff\xd8\xffframe\xff\xd9'
            if change == 'oversize':
                content = b'\xff\xd8\xff' + b'x' * 2_000_000 + b'\xff\xd9'
            elif change == 'not_jpeg':
                content = b'not JPEG'
            elif change == 'source_changed':
                case.video.write_bytes(case.video.read_bytes() + b'changed')
            case.output.write_bytes(content)
        case.runner.side_effect = run
    with pytest.raises(case.ns['PublicationThumbnailError']) as exc:
        _prepare(case)
    assert str(exc.value) == 'thumbnail_preparation_unavailable' and case.runner.call_count == 1


def _worker(monkeypatch):
    tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_persist_final_thumbnail')
    proof = {'status': 'prepared', 'sha256': 'a' * 64, 'source_sha256': 'b' * 64, 'size': 4000,
             'origin': 'approved_final_frame', 'frame_seconds': 1.0, 'width': 720, 'height': 1280}
    prepare = Mock(return_value=proof)
    monkeypatch.setitem(sys.modules, 'app.services.publication_thumbnail',
                        SimpleNamespace(prepare_approved_final_thumbnail=prepare))
    upload = Mock(return_value={'key': f'videos/{TASK}/thumbnail.jpg', 'size': 4000})
    ns = {'upload_file': upload}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), '<real-thumbnail-hook>', 'exec'), ns)
    return ns['_persist_final_thumbnail'], prepare, upload


def test_real_worker_persists_key_and_proof_without_bearer_url(monkeypatch, tmp_path):
    hook, prepare, upload = _worker(monkeypatch)
    result = hook(TASK, tmp_path, {'path': str(tmp_path / 'final.mp4')},
                  {'mode': 'production', 'format': 'shorts'}, 'automated_qc_pass', False)
    assert result['thumbnail_key'] == f'videos/{TASK}/thumbnail.jpg'
    assert result['thumbnail_generation']['status'] == 'stored'
    assert result['thumbnail_sha256'] == 'a' * 64 and result['thumbnail_size'] == 4000
    assert prepare.call_args.kwargs['qa_approved'] is True and upload.call_args.args[2] == 'image/jpeg'
    assert 'http' not in json.dumps(result)


@pytest.mark.parametrize('mode,quality,manual', [('preview', 'automated_qc_pass', False),
    ('production', 'needs_manual_review', True), ('production', 'automated_qc_pass', True),
    ('production', 'automated_qc_pass', None)])
def test_no_thumbnail_preparation_before_actual_quality_approval(monkeypatch, tmp_path, mode, quality, manual):
    hook, prepare, upload = _worker(monkeypatch)
    result = hook(TASK, tmp_path, {'path': 'unused'}, {'mode': mode, 'format': 'shorts'}, quality, manual)
    assert result['thumbnail_key'] is None
    prepare.assert_not_called(); upload.assert_not_called()


@pytest.mark.parametrize('phase', ['prepare', 'storage', 'wrong_receipt'])
def test_thumbnail_failure_keeps_final_but_missing_key_blocks_required_public_release(monkeypatch, tmp_path, phase):
    hook, prepare, upload = _worker(monkeypatch)
    if phase == 'wrong_receipt':
        upload.return_value = {'key': 'other-object', 'size': 4000}
    else:
        (prepare if phase == 'prepare' else upload).side_effect = RuntimeError('SECRET')
    fields = hook(TASK, tmp_path, {'path': 'unchanged-final'},
                  {'mode': 'production', 'format': 'shorts'}, 'automated_qc_pass', False)
    assert fields['thumbnail_key'] is None and fields['thumbnail_generation']['status'] == 'unavailable'
    assert 'SECRET' not in json.dumps(fields)
    tree = ast.parse((ROOT / 'app/publish_tasks.py').read_text(encoding='utf-8'))
    error = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'asset_error' for target in node.targets))
    gate = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                and isinstance(node.test, ast.Name) and node.test.id == 'asset_error')
    ns = dict(synthetic_disclosure=True, youtube_response={'status': {'containsSyntheticMedia': True}},
              editorial_error=None,
              caption_error_code=None, thumbnail_error_code=None, require_thumbnail=True, thumbnail_result=None,
              source_task_id=TASK, task_id='publisher', mark_release_blocked=Mock())
    exec(compile(ast.Module(body=[error, gate], type_ignores=[]), '<real-publisher-required-cover-gate>', 'exec'), ns)
    assert ns['release_status'] == 'blocked' and ns['release_error_code'] == 'thumbnail_required'
    ns['mark_release_blocked'].assert_called_once_with(TASK, 'publisher', 'thumbnail_required')


def test_hook_is_after_final_gates_and_before_metadata_result_and_publish_plan():
    tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    runtime = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    calls = [node for node in ast.walk(runtime) if isinstance(node, ast.Call)]
    hook = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == '_persist_final_thumbnail')
    final_qc = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == '_strict_short_preview_render_qc')
    motion_gate = next(node for node in ast.walk(runtime) if isinstance(node, ast.If) and ast.unparse(node.test) == 'max_freeze_seconds > freeze_limit')
    metadata = next(node for node in calls if isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == 'meta_path.write_text')
    success = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'mark_success')
    assert final_qc.lineno < motion_gate.lineno < hook.lineno < metadata.lineno < success.lineno
    dictionaries = [node for node in ast.walk(runtime) if isinstance(node, ast.Dict)
                    and any(key is None and isinstance(value, ast.Name) and value.id == 'thumbnail_fields'
                            for key, value in zip(node.keys, node.values))]
    assert len(dictionaries) == 2  # Stored metadata and render result.
    assert any(isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == 'thumbnail_key'
        and ast.unparse(value) == "thumbnail_fields['thumbnail_key']"
        for key, value in zip(node.keys, node.values)) for node in ast.walk(runtime))


def test_actual_frozen_publish_plan_receives_generated_thumbnail_key(monkeypatch, tmp_path):
    hook, _, _ = _worker(monkeypatch)
    fields = hook(TASK, tmp_path, {'path': 'final'}, {'mode': 'production', 'format': 'shorts'}, 'automated_qc_pass', False)
    automation = _module('youtube_automation')
    job = {'kind': 'render', 'state': 'SUCCESS', 'spec': {'language': 'tr', 'format': 'shorts'},
           'result': {'title': 'Onaylı konu', 'video_key': f'videos/{TASK}/final.mp4',
                      'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False, **fields,
                      'publish_metadata': {'title': 'Onaylı konu', 'description': 'Onaylı açıklama.',
                                           'thumbnail_key': fields['thumbnail_key']}}}
    before = deepcopy(job)
    plan = automation['build_publish_plan'](TASK, job, {
        'channel_id': 'UC_channel_000', 'default_language': 'tr', 'languages': ['tr'],
        'release_mode': 'public', 'require_thumbnail': True})
    assert plan['thumbnail_key'] == fields['thumbnail_key'] and plan['require_thumbnail'] is True
    assert job == before


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Native FFmpeg unavailable')
@pytest.mark.parametrize('video_format,size,expected', [('shorts', '1080x1920', (720, 1280)),
                                                       ('landscape', '1920x1080', (1280, 720))])
def test_native_approved_master_extracts_real_correctly_oriented_jpeg(tmp_path, video_format, size, expected):
    ns = _module('publication_thumbnail')
    ns['_TEMP_ROOT'] = tmp_path
    work = tmp_path / 'youtube_factory' / f'{TASK}_attempt_0'
    work.mkdir(parents=True)
    video, output = work / 'final.mp4', work / 'thumbnail.jpg'
    subprocess.run(['ffmpeg', '-n', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=c=blue:s={size}:r=30:d=2', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                    '-t', '2', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1',
                    '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(video)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    before = hashlib.sha256(video.read_bytes()).hexdigest()
    result = ns['prepare_approved_final_thumbnail'](TASK, video, output, video_format=video_format, qa_approved=True)
    assert result['source_sha256'] == before == hashlib.sha256(video.read_bytes()).hexdigest()
    assert (result['width'], result['height']) == expected
    assert 0 < result['size'] <= 2_000_000 and output.read_bytes().startswith(b'\xff\xd8\xff')
