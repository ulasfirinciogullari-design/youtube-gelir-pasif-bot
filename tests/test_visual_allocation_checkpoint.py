import ast
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = '11111111-1111-4111-8111-111111111111'
OTHER_TASK_ID = '22222222-2222-4222-8222-222222222222'
JPEG = b'\xff\xd8\xff\xe0' + b'bounded-fixture-jpeg' * 30 + b'\xff\xd9'


@pytest.fixture
def checkpoint(tmp_path):
    source = ROOT / 'app' / 'services' / 'visual_allocation_checkpoint.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    uploads = []

    def upload(path, key, content_type):
        uploads.append((key, content_type, Path(path).read_bytes(), Path(path)))
        return {'url': 'https://private.test/?token=SECRET', 'authorization': 'DO NOT EXPORT'}

    namespace = {'upload_file': upload}
    exec(compile(tree, str(source), 'exec'), namespace)
    real_extract_frame = namespace['_extract_frame']
    namespace['_WORK_ROOT'] = tmp_path
    captured = []

    def frame(path, output, fraction, timeout):
        captured.append((path, output, fraction, timeout))
        return JPEG

    namespace['_extract_frame'] = frame
    work = tmp_path / f'{TASK_ID}_attempt_0'
    work.mkdir()
    module = SimpleNamespace(**namespace)
    module.namespace = namespace
    module.uploads = uploads
    module.captured = captured
    module.work = work
    module.real_extract_frame = real_extract_frame
    return module


def _case(module, count=6):
    scenes, visuals, reviews = [], [], {}
    for index in range(count):
        path = module.work / f'pexels_scene_{index}.mp4'
        path.write_bytes(b'\x00\x00\x00\x18ftypmp42' + b'original-video-not-for-export' * 8)
        scenes.append({'narration': f'Sahne {index}: banknot kâğıdı pamuk ve keten içerir.',
                       'visual_queries': ['cotton fabric close up'], 'ai_prompt': None,
                       'secret': 'DO NOT EXPORT'})
        visuals.append([{'path': str(path), 'source_type': 'stock', 'stock_provider': 'pexels',
                         'start_fraction': 0.18, 'url': 'https://signed.test/?secret=SECRET'}])
        reviews[index] = {
            'scene_index': index, 'score': [88, 90, 91, 35, 68, 65][index % 6],
            'raw_score': 91, 'best_candidate_index': 0, 'best_moment_index': 1,
            'best_start_fraction': 0.5, 'reason': 'Seçili sahnede malzeme eşleşmesi incelendi.',
            'retry_queries': ['cotton cloth texture close up', 'linen fabric close up'],
            'subject_visible': index != 3, 'spoken_action_visible': True,
            'identity_gate_passed': True, 'evidence_gate_passed': index != 3,
            'editorial_gate_passed': True, 'evidence_moment_indices': [0, 1, 2],
            'headers': {'Authorization': 'DO NOT EXPORT'},
            'provider_response': 'SECRET', 'pass': True,
        }
    return scenes, visuals, reviews


def _persist(module, data, **kwargs):
    return module.persist_visual_allocation_checkpoint(TASK_ID, *data, module.work, **kwargs)


def _metadata(module):
    return json.loads(module.uploads[-2][2])


def test_six_real_selections_keep_scores_reasons_frames_and_no_approval(checkpoint):
    module = checkpoint
    data = _case(module)
    original = deepcopy(data)
    result = _persist(module, data, allocation={
        'paid_create_cap': 2, 'paid_create_used': 0, 'required_paid_scenes': 3,
        'overflow_scene_indices': [3, 4, 5], 'secret': 'DO NOT EXPORT',
    })
    pointer = result['visual_allocation_checkpoint']
    assert pointer['status'] == 'diagnostic_only'
    assert pointer['qa_approved'] is False
    assert pointer['reusable_for_render'] is False
    assert pointer['scene_count'] == pointer['frame_count'] == 6
    assert len(module.uploads) == 2
    assert [item[1] for item in module.uploads] == ['application/json', 'text/html; charset=utf-8']
    metadata = _metadata(module)
    assert [item['review']['score'] for item in metadata['scenes']] == [88, 90, 91, 35, 68, 65]
    assert metadata['allocation']['paid_create_used'] == 0
    assert metadata['allocation']['overflow_scene_indices'] == [3, 4, 5]
    assert metadata['qa_approved'] is False
    assert metadata['reusable_for_render'] is False
    for index, item in enumerate(metadata['scenes']):
        assert item['scene_index'] == index
        assert item['selection']['selected_spec_index'] == 0
        assert item['selection']['sample_fraction'] == 0.5
        assert item['selection']['sample_basis'] == 'reviewed_moment_resample'
        assert base64.b64decode(item['frame']['jpeg_base64']) == JPEG
        assert item['frame']['sha256'] == hashlib.sha256(JPEG).hexdigest()
        assert item['review']['reason'] == data[2][index]['reason']
    assert data == original
    assert all(Path(specs[0]['path']).exists() for specs in data[1])
    assert all(not entry[3].exists() for entry in module.uploads)
    assert pointer['metadata_sha256'] == hashlib.sha256(module.uploads[0][2]).hexdigest()
    assert pointer['html_sha256'] == hashlib.sha256(module.uploads[1][2]).hexdigest()
    assert str(module.work) not in json.dumps(metadata)
    for encoded in (json.dumps(metadata), module.uploads[1][2].decode(), json.dumps(pointer)):
        for forbidden in ('https://', 'DO NOT EXPORT', 'SECRET', 'authorization', 'provider_response',
                          'original-video-not-for-export', '"pass"'):
            assert forbidden not in encoded


def test_collapsed_selection_uses_actual_spec_zero_not_old_candidate_index(checkpoint):
    module = checkpoint
    data = _case(module, 1)
    data[2][0]['best_candidate_index'] = 5
    assert _persist(module, data)['visual_allocation_checkpoint']['frame_count'] == 1
    scene = _metadata(module)['scenes'][0]
    assert scene['review']['best_candidate_index'] == 5
    assert scene['selection']['selected_spec_index'] == 0
    assert module.captured[0][0] == Path(data[1][0][0]['path'])


def test_uncollapsed_pool_uses_reviewed_index_without_mutating_pool(checkpoint):
    module = checkpoint
    data = _case(module, 2)
    data[1][0].append(deepcopy(data[1][1][0]))
    data[2][0]['best_candidate_index'] = 1
    _persist(module, data)
    assert _metadata(module)['scenes'][0]['selection']['selected_spec_index'] == 1
    assert module.captured[0][0] == Path(data[1][1][0]['path'])
    assert len(data[1][0]) == 2


def test_string_spec_does_not_shift_reviewed_index_to_a_different_stock_clip(checkpoint):
    module = checkpoint
    data = _case(module, 1)
    data[1][0].insert(0, '/tmp/untrusted-original.mp4')
    _persist(module, data)
    scene = _metadata(module)['scenes'][0]
    assert scene['selection']['selected_spec_index'] == 0
    assert 'frame' not in scene
    assert module.captured == []


def test_more_than_twelve_reviews_are_bounded_without_hiding_omitted_count(checkpoint):
    module = checkpoint
    data = _case(module, 15)
    result = _persist(module, data)['visual_allocation_checkpoint']
    assert result['scene_count'] == result['frame_count'] == len(module.captured) == 12
    assert _metadata(module)['reviewed_scene_count'] == 15
    assert _metadata(module)['omitted_scene_count'] == 3
    assert all(len(item[2]) < module.MAX_ARTIFACT_BYTES for item in module.uploads)


def test_repeated_same_diagnostic_uses_identical_content_addressed_keys(checkpoint):
    module = checkpoint
    data = _case(module, 1)
    first = _persist(module, data)
    assert _persist(module, data) == first
    data[2][0]['reason'] = 'Yeni değerlendirme açıklaması.'
    second = _persist(module, data)
    assert second['visual_allocation_checkpoint']['metadata_key'] != first['visual_allocation_checkpoint']['metadata_key']


@pytest.mark.parametrize('secret', [
    'See https://signed.test/file?X-Amz-Signature=SECRET', 'Bearer SECRET',
    'api_key=SECRET', 'Authorization: SECRET', 'refresh_token=SECRET',
    'sk-' + 'x' * 24, 'AIza' + 'x' * 30, 'https://user:pass@private.test/',
    'C:\\Users\\private\\credentials.txt', '/tmp/private/credential.txt',
    'contact@example.test', 'X-Amz-Credential=SECRET', 's3://private-bucket/secret',
])
def test_sensitive_free_text_fields_are_redacted_whole(checkpoint, secret):
    module = checkpoint
    data = _case(module, 1)
    data[0][0]['narration'] = secret
    data[0][0]['visual_queries'] = [secret]
    data[2][0]['reason'] = secret
    data[2][0]['retry_queries'] = [secret]
    _persist(module, data)
    scene = _metadata(module)['scenes'][0]
    assert scene['narration'] == scene['review']['reason'] == '[redacted]'
    assert scene['visual_queries'] == scene['review']['retry_queries'] == ['[redacted]']
    assert secret not in module.uploads[0][2].decode()
    assert secret not in module.uploads[1][2].decode()


def test_html_escapes_untrusted_text_and_has_no_external_assets_or_scripts(checkpoint):
    module = checkpoint
    data = _case(module, 1)
    data[2][0]['reason'] = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    data[0][0]['narration'] = '<form action="/steal">hello & goodbye</form>'
    _persist(module, data)
    page = module.uploads[1][2].decode()
    assert '<script>' not in page
    assert '<form action=' not in page
    assert '&lt;script&gt;' in page
    assert 'default-src \'none\'' in page
    assert 'img-src data:' in page
    assert 'name="referrer" content="no-referrer"' in page
    assert page.count('<img ') == 1
    assert 'src="data:image/jpeg;base64,' in page
    assert 'href=' not in page


def test_numeric_and_boolean_gates_are_strict_and_free_text_is_bounded(checkpoint):
    module = checkpoint
    data = _case(module, 1)
    data[2][0].update(score=True, raw_score=float('nan'), subject_visible='true',
                      spoken_action_visible=1, evidence_gate_passed=False,
                      reason='word ' * 500, retry_queries=['q ' * 300] * 10,
                      evidence_moment_indices=[True, 1, 2, 2, 999])
    _persist(module, data, allocation={'paid_create_cap': True, 'paid_create_used': float('inf')})
    review = _metadata(module)['scenes'][0]['review']
    assert review['score'] is review['raw_score'] is None
    assert 'subject_visible' not in review['gates']
    assert 'spoken_action_visible' not in review['gates']
    assert review['gates']['evidence_gate_passed'] is False
    assert len(review['reason']) <= 600
    assert len(review['retry_queries']) == 3
    assert all(len(query) <= 160 for query in review['retry_queries'])
    assert review['evidence_moment_indices'] == [1, 2]
    assert _metadata(module)['allocation'] == {}


@pytest.mark.parametrize('damage', ['outside', 'sibling_job', 'remote', 'traversal', 'non_mp4',
                                   'fake_mp4', 'generated', 'unknown_provider', 'symlink'])
def test_only_this_jobs_local_verified_stock_files_can_be_sampled(checkpoint, damage):
    module = checkpoint
    data = _case(module, 1)
    spec = data[1][0][0]
    path = Path(spec['path'])
    if damage == 'outside':
        spec['path'] = str(module.work.parent / path.name)
    elif damage == 'sibling_job':
        other = module.work.parent / f'{OTHER_TASK_ID}_attempt_0'
        other.mkdir()
        (other / path.name).write_bytes(path.read_bytes())
        spec['path'] = str(other / path.name)
    elif damage == 'remote':
        spec['path'] = 'https://private.test/source.mp4?token=SECRET'
    elif damage == 'traversal':
        spec['path'] = str(module.work / '..' / module.work.name / path.name)
    elif damage == 'non_mp4':
        spec['path'] = str(path.with_suffix('.txt'))
    elif damage == 'fake_mp4':
        path.write_bytes(b'#EXTM3U\nhttps://private.test/secret' * 3)
    elif damage == 'generated':
        spec.update(generated=True)
    elif damage == 'unknown_provider':
        spec['stock_provider'] = 'unknown'
    elif damage == 'symlink':
        link = module.work / 'linked.mp4'
        try:
            link.symlink_to(path)
        except OSError:
            pytest.skip('Host does not permit symlinks')
        spec['path'] = str(link)
    result = _persist(module, data)['visual_allocation_checkpoint']
    assert result['status'] == 'diagnostic_only'
    assert result['frame_count'] == 0
    assert module.captured == []
    assert 'frame' not in _metadata(module)['scenes'][0]


@pytest.mark.parametrize('frame', [None, b'not-jpeg', b'\xff\xd8\xff' + b'x' * (180 * 1024) + b'\xff\xd9'],
                         ids=['missing', 'invalid', 'oversize'])
def test_missing_or_oversize_frame_still_preserves_reasons_without_approval(checkpoint, frame):
    module = checkpoint
    data = _case(module, 1)
    module.namespace['_extract_frame'] = lambda *_: frame
    result = _persist(module, data)['visual_allocation_checkpoint']
    assert result['status'] == 'diagnostic_only'
    assert result['frame_count'] == 0
    assert _metadata(module)['scenes'][0]['review']['reason'] == data[2][0]['reason']


def test_frame_time_budget_prevents_further_extraction(checkpoint):
    module = checkpoint
    data = _case(module, 2)
    module.namespace['_FRAME_BUDGET_SECONDS'] = 0
    assert _persist(module, data)['visual_allocation_checkpoint']['frame_count'] == 0
    assert module.captured == []


@pytest.mark.parametrize('failure_on', [1, 2])
def test_storage_failure_never_raises_or_leaks_and_cleans_temp_files(checkpoint, failure_on):
    module = checkpoint
    data = _case(module, 1)
    calls = []

    def failing(path, *_):
        calls.append(Path(path))
        if len(calls) == failure_on:
            raise RuntimeError('SECRET https://private.test/?credential=DO-NOT-EXPORT')

    module.namespace['upload_file'] = failing
    result = _persist(module, data)
    assert result == {'visual_allocation_checkpoint': {'version': 1, 'status': 'unavailable'}}
    assert all(not path.exists() for path in calls)
    assert Path(data[1][0][0]['path']).exists()


@pytest.mark.parametrize('invalid', ['task', 'work', 'duplicate', 'wrong_mapping', 'index', 'empty', 'oversize'])
def test_invalid_request_returns_fixed_unavailable_without_upload(checkpoint, invalid):
    module = checkpoint
    data = _case(module, 1)
    task_id, work = TASK_ID, module.work
    if invalid == 'task':
        task_id = '../other-job'
    elif invalid == 'work':
        work = module.work.parent
    elif invalid == 'duplicate':
        data = (data[0], data[1], [data[2][0], data[2][0]])
    elif invalid == 'wrong_mapping':
        data = (data[0], data[1], {1: data[2][0]})
    elif invalid == 'index':
        data[2][0]['scene_index'] = True
    elif invalid == 'empty':
        data = (data[0], data[1], [])
    elif invalid == 'oversize':
        data = (data[0] * 257, data[1] * 257, data[2])
    result = module.persist_visual_allocation_checkpoint(task_id, *data, work)
    assert result['visual_allocation_checkpoint']['status'] == 'unavailable'
    assert module.uploads == []


def test_extractor_is_local_only_single_frame_with_bounded_subprocesses(checkpoint, monkeypatch):
    module = checkpoint
    data = _case(module, 1)
    output = module.work / 'extracted.jpg'
    # Exercise real command construction with local subprocesses mocked.
    probe = Mock(return_value='20.0')

    def ffmpeg(args, **kwargs):
        assert args[0] == 'ffmpeg'
        assert args[args.index('-protocol_whitelist') + 1] == 'file,pipe'
        assert args[args.index('-frames:v') + 1] == '1'
        assert args[args.index('-ss') + 1] == '10.000'
        assert args[args.index('-map_metadata') + 1] == '-1'
        assert kwargs['timeout'] <= 6.0
        Path(args[-1]).write_bytes(JPEG)

    monkeypatch.setattr(subprocess, 'check_output', probe)
    monkeypatch.setattr(subprocess, 'run', ffmpeg)
    assert module.real_extract_frame(Path(data[1][0][0]['path']), output, 0.5, 10.0) == JPEG
    args, kwargs = probe.call_args
    assert args[0][args[0].index('-protocol_whitelist') + 1] == 'file,pipe'
    assert kwargs['timeout'] <= 3.0


def test_extractor_timeout_is_fixed_missing_frame_not_provider_exception(checkpoint, monkeypatch):
    module = checkpoint
    data = _case(module, 1)
    monkeypatch.setattr(subprocess, 'check_output', Mock(side_effect=subprocess.TimeoutExpired('local', 1)))
    assert module.real_extract_frame(Path(data[1][0][0]['path']), module.work / 'unused.jpg', 0.5, 1) is None


def test_moment_fraction_contract_stays_aligned_with_visual_qc(checkpoint):
    source = ROOT / 'app' / 'services' / 'visual_qc.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    value = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'MOMENT_FRACTIONS' for target in node.targets))
    assert tuple(value) == checkpoint._MOMENT_FRACTIONS
