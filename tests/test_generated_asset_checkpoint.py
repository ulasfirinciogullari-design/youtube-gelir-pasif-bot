"""Offline raw preservation, immutable Storage and the two real worker hooks."""
import ast
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK = '11111111-1111-4111-8111-111111111111'
MP4 = b'\x00\x00\x00\x18ftypisom' + b'raw paid video' * 100
MP3 = b'ID3' + b'existing narration' * 100


def module(name):
    path = ROOT / 'app/services' / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.'))]
    ns = {'__name__': name}
    exec(compile(tree, str(path), 'exec'), ns)
    return ns


class MemoryStorage:
    def __init__(self):
        self.objects, self.calls, self.reads = {}, [], []
        self.fail_at = None

    def put_object(self, **request):
        self.calls.append({key: value for key, value in request.items() if key != 'Body'})
        if self.fail_at == len(self.calls):
            raise RuntimeError('SECRET storage response https://private.invalid/?key=SECRET')
        assert request['IfNoneMatch'] == '*' and request['CacheControl'] == 'private, no-store'
        assert 'ACL' not in request
        if request['Key'] in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        raw = request['Body'].read()
        assert len(raw) == request['ContentLength']
        assert hashlib.sha256(raw).hexdigest() == request['Metadata']['sha256']
        self.objects[request['Key']] = (raw, request['ContentType'])
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def get_object(self, **request):
        self.reads.append(request['Key'])
        raw, content_type = self.objects[request['Key']]
        return {'Body': io.BytesIO(raw), 'ContentLength': len(raw), 'ContentType': content_type}


@pytest.fixture
def case(tmp_path):
    ns = module('generated_asset_checkpoint')
    audio = module('audio_checkpoint')
    audio['_AUDIO_TEMP_ROOT'] = tmp_path
    paths = module('visual_allocation_checkpoint')
    paths['_WORK_ROOT'] = tmp_path / 'youtube_factory'
    ns.update({name: audio[name] for name in ('_candidate_package', '_candidate_voice', '_local_candidate_path')})
    ns['_work_path'] = paths['_work_path']
    client = MemoryStorage()
    ns['storage'] = SimpleNamespace(_client=lambda: client, settings=SimpleNamespace(bucket='private-bucket'))
    work = tmp_path / 'youtube_factory' / f'{TASK}_attempt_0'
    work.mkdir(parents=True)
    raw, voice = work / 'runway_s00.mp4', tmp_path / f'{TASK}.mp3'
    raw.write_bytes(MP4); voice.write_bytes(MP3)
    package = {'title': 'The barcode story', 'scenes': [
        {'narration': f'Exact scene {i}.', 'ai_prompt': 'An unbranded object.', 'visual_queries': ['warehouse'],
         'transition': 'cut'} for i in range(6)], 'sources': [], 'narration': 'Exact narration.'}
    voice_result = {'path': str(voice), 'spoken_texts': [f'Exact scene {i}.' for i in range(6)],
                    'scene_durations': [5.0] * 6, 'duration_before_fit': 30.0,
                    'duration_after_fit': 30.0, 'tempo_rate': 1.0, 'content_target_seconds': 29.5,
                    'reserved_tail_seconds': .5}
    spec = {'path': str(raw), 'generated': True, 'source_type': 'generated',
            'generation_provider': 'gemini_veo', 'generation_provider_attempts': 1,
            'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True}
    args = dict(task_id=TASK, work_dir=work, package=package, voice_result=voice_result,
                visual_spec=spec, scene_index=0, phase='initial_generation',
                options={'mode': 'production', 'format': 'shorts'}, duration_minutes=.5)
    return SimpleNamespace(ns=ns, client=client, args=args, raw=raw, voice=voice, work=work)


def preserve(case, **updates):
    return case.ns['persist_generated_asset_candidate'](**{**case.args, **updates})


def test_exact_raw_voice_package_are_immutable_private_and_never_approved(case):
    before = deepcopy(case.args)
    pointer = preserve(case)
    assert case.args == before and case.raw.read_bytes() == MP4 and case.voice.read_bytes() == MP3
    assert len(case.client.objects) == 3
    manifest_bytes, content_type = case.client.objects[pointer['manifest_key']]
    manifest = json.loads(manifest_bytes)
    assert hashlib.sha256(manifest_bytes).hexdigest() == pointer['manifest_sha256']
    assert len(manifest_bytes) == pointer['manifest_size'] and content_type == 'application/json'
    assert manifest['raw']['sha256'] == hashlib.sha256(MP4).hexdigest()
    assert manifest['audio']['sha256'] == hashlib.sha256(MP3).hexdigest()
    assert manifest['voice']['spoken_texts'] == case.args['voice_result']['spoken_texts']
    tasks_tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    digest_fn = next(n for n in tasks_tree.body if isinstance(n, ast.FunctionDef) and n.name == '_recovery_package_sha256')
    ns = {'json': json, 'hashlib': hashlib}
    exec(compile(ast.Module(body=[digest_fn], type_ignores=[]), '<real-package-digest>', 'exec'), ns)
    assert pointer['package_sha256'] == ns['_recovery_package_sha256'](case.args['package'])
    for value in (pointer, manifest):
        assert value['qa_approved'] is False and value['reusable'] is False
        assert value['requires_full_qa'] is True and value['diagnostic_only'] is True
        assert 'approved_package' not in value and 'repair_scene_indices' not in value and 'video_key' not in value
    assert all(call['Key'].startswith(f'generated_candidates/{TASK}/') for call in case.client.calls)
    assert not any(value.startswith(('http', '/tmp')) for value in pointer.values() if isinstance(value, str))


def test_identical_repeat_verifies_bytes_without_overwrite(case):
    first = preserve(case)
    original = deepcopy(case.client.objects)
    assert preserve(case) == first
    assert case.client.objects == original and len(case.client.reads) == 3


def test_later_repair_keeps_initial_raw_and_gets_separate_manifest(case):
    initial = preserve(case)
    previous = deepcopy(case.client.objects)
    repaired = case.work / 'runway_repair_s00.mp4'
    repaired.write_bytes(MP4 + b'new bounded repair')
    spec = {**case.args['visual_spec'], 'path': str(repaired)}
    repair = preserve(case, phase='final_repair', visual_spec=spec)
    assert repair['manifest_key'] != initial['manifest_key'] and repair['raw_key'] != initial['raw_key']
    assert repair['audio_sha256'] == initial['audio_sha256']
    assert all(case.client.objects[key] == value for key, value in previous.items())
    assert len(case.client.objects) == 5


@pytest.mark.parametrize('field', ['raw_key', 'manifest_key'])
def test_changed_existing_bytes_never_validated_from_metadata(case, field):
    pointer = preserve(case)
    raw, content_type = case.client.objects[pointer[field]]
    case.client.objects[pointer[field]] = (b'X' + raw[1:], content_type)
    with pytest.raises(case.ns['GeneratedAssetCheckpointError'], match='^generated_asset_preservation_unavailable$'):
        preserve(case)


@pytest.mark.parametrize('failed_put', [1, 2, 3])
def test_storage_failures_never_return_a_preserved_receipt_or_leak_error(case, failed_put):
    case.client.fail_at = failed_put
    with pytest.raises(case.ns['GeneratedAssetCheckpointError']) as error:
        preserve(case)
    assert str(error.value) == 'generated_asset_preservation_unavailable'
    assert len(case.client.objects) == failed_put - 1
    assert case.raw.read_bytes() == MP4


@pytest.mark.parametrize('updates', [
    {'options': {'mode': 'preview', 'format': 'shorts'}},
    {'options': {'mode': 'production', 'format': 'landscape'}}, {'duration_minutes': 1},
    {'duration_minutes': True}, {'task_id': 'bad-id'}, {'scene_index': -1}, {'scene_index': 6},
    {'scene_index': True}, {'phase': 'final_visual_qc'}, {'work_dir': '/unrelated'},
])
def test_invalid_scope_fails_without_storage(case, updates):
    with pytest.raises(case.ns['GeneratedAssetCheckpointError']):
        preserve(case, **updates)
    assert not case.client.calls


@pytest.mark.parametrize('change', ['remote', 'other_task', 'wrong_file', 'not_generated', 'unknown_provider',
                                   'attempts_bool', 'shifted_cut', 'loopable', 'bad_mp4', 'oversize', 'bad_audio',
                                   'unsafe_narration', 'voice_mismatch'])
def test_bad_media_or_identity_is_rejected_without_writes(case, change):
    spec = case.args['visual_spec']
    if change == 'remote': spec['path'] = 'https://private.invalid/video.mp4'
    elif change == 'other_task': spec['path'] = str(case.work.parent / 'different' / case.raw.name)
    elif change == 'wrong_file': spec['path'] = str(case.work / 'final.mp4')
    elif change == 'not_generated': spec['source_type'] = 'stock'
    elif change == 'unknown_provider': spec['generation_provider'] = 'https://SECRET'
    elif change == 'attempts_bool': spec['generation_provider_attempts'] = True
    elif change == 'shifted_cut': spec['start_fraction'] = .5
    elif change == 'loopable': spec['forbid_loop'] = False
    elif change == 'bad_mp4': case.raw.write_bytes(b'not mp4' * 300)
    elif change == 'oversize': case.ns['MAX_RAW_BYTES'] = 1024
    elif change == 'bad_audio': case.voice.write_bytes(b'not mp3' * 300)
    elif change == 'unsafe_narration': case.args['package']['scenes'][0]['narration'] = 'Bearer SECRET'
    else: case.args['voice_result']['spoken_texts'] = ['missing other scenes']
    with pytest.raises(case.ns['GeneratedAssetCheckpointError']):
        preserve(case)
    assert not case.client.calls


def worker(monkeypatch):
    tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_checkpoint_generated_asset')
    update, persist = Mock(), Mock()
    ns = {'update_job': update}
    monkeypatch.setitem(sys.modules, 'app.services.generated_asset_checkpoint',
                        SimpleNamespace(persist_generated_asset_candidate=persist))
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<real-worker-preservation-hook>', 'exec'), ns)
    return ns['_checkpoint_generated_asset'], persist, update


def test_worker_failure_retains_previous_records_and_never_changes_quality(monkeypatch):
    hook, persist, update = worker(monkeypatch)
    journal = []
    receipt = {'source_task_id': TASK, 'scene_index': 0, 'phase': 'initial_generation',
               'status': 'preserved_candidate', 'qa_approved': False, 'reusable': False, 'requires_full_qa': True}
    persist.return_value = receipt
    args = [TASK, 'work', {'scene': 'unchanged'}, {'voice': 'unchanged'}, {'clip': 'unchanged'},
            0, 'initial_generation', {'mode': 'production', 'format': 'shorts'}, .5, journal]
    hook(*args)
    persist.side_effect = RuntimeError('SECRET provider response')
    args[5], args[6] = 5, 'final_repair'
    hook(*args)
    saved = update.call_args.kwargs
    assert set(saved) == {'generated_asset_candidates'}
    value = saved['generated_asset_candidates']
    assert value['attempted_count'] == 2 and value['preserved_count'] == 1 and value['failed_count'] == 1
    assert value['entries'][0] == receipt and value['entries'][1]['status'] == 'unavailable'
    assert 'SECRET' not in json.dumps(saved)
    assert not {'result', 'state', 'repair_available', 'paid_create_slots_used'} & set(saved)
    update.side_effect = RuntimeError('Redis down')
    hook(*args)  # No provider retry or primary render failure from diagnostic writes.
    assert persist.call_count == 3


@pytest.mark.parametrize('mode,format_,duration', [('preview', 'shorts', .5), ('production', 'landscape', .5),
                                                ('production', 'shorts', 1)])
def test_worker_outside_scope_makes_no_preservation_calls(monkeypatch, mode, format_, duration):
    hook, persist, update = worker(monkeypatch)
    hook(TASK, None, {}, {}, {}, 0, 'initial_generation', {'mode': mode, 'format': format_}, duration, [])
    persist.assert_not_called(); update.assert_not_called()


def test_two_sync_hooks_run_after_download_before_next_generation_or_final_review():
    tree = ast.parse((ROOT / 'app/tasks.py').read_text(encoding='utf-8'))
    pipeline = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_video_pipeline')
    parents = {child: parent for parent in ast.walk(pipeline) for child in ast.iter_child_nodes(parent)}
    hooks = [n for n in ast.walk(pipeline) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == '_checkpoint_generated_asset']
    assert len(hooks) == 2 and {n.args[6].value for n in hooks} == {'initial_generation', 'final_repair'}
    for hook in hooks:
        block = parents[parents[hook]]
        calls = [n for n in ast.walk(block) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        create = next(n for n in calls if n.func.id == 'generate_scene')
        download = next(n for n in calls if n.func.id == 'download_generated_scene')
        assert create.lineno < download.lineno < hook.lineno
        assert not any(n.func.id == 'generate_scene' and n.lineno > hook.lineno for n in calls)
        enclosing = block
        while not isinstance(enclosing, ast.For): enclosing = parents[enclosing]
        assert parents[hook].lineno < enclosing.end_lineno  # Synchronous before next iteration.
    # Neither hook is hidden in the old preview-only staging condition.
    for hook in hooks:
        parent = parents[hook]
        while parent is not pipeline:
            assert not (isinstance(parent, ast.If) and ast.unparse(parent.test) == 'is_bounded_short_preview')
            parent = parents[parent]
