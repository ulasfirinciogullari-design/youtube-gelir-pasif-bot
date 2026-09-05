"""Hash-bound recovery-only contracts never authorize a new paid repair."""

import ast
import copy
import hashlib
import math
from pathlib import Path
import re

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
SOURCE_ID = 'b0730000-0000-4000-8000-000000000000'


@pytest.fixture
def boundary():
    constants = {'_RECOVERED_MEDIA_SOURCE_PATTERN', '_RECOVERED_MEDIA_KEY_PATTERN',
                 '_RECOVERED_MEDIA_PROVIDERS', '_REPAIR_RECOVERED_MEDIA_PROVIDERS',
                 '_MAX_RECOVERED_VIDEO_BYTES', '_SHA256_PATTERN'}
    functions = {'_validated_recovered_generated_media', '_validate_recovered_generated_clip',
                 '_file_sha256', '_require_recovered_media_coverage'}
    definitions = [node for node in TREE.body if (
        isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                           for target in node.targets)) or (
        isinstance(node, ast.FunctionDef) and node.name in functions)]
    namespace = {'re': re, 'Path': Path, 'hashlib': hashlib, 'math': math,
                 'FinalVisualQualityError': RuntimeError,
                 'media_duration': lambda path: 8.0, 'video_frame_count': lambda path: 240}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


def _contract():
    return {
        'version': 3, 'recovery_only': True, 'source_task_id': SOURCE_ID,
        'package_sha256': 'b' * 64,
        'scenes': {'3': [{
            'key': f'recovery/{SOURCE_ID}/raw/clip-03.mp4',
            'sha256': 'a' * 64, 'size': 4096, 'provider': 'gemini_veo',
            'provider_attempts': 1, 'synthetic_motion_only': False,
            'motion_recipe_version': None, 'source_media_type': 'video',
        }]},
    }


def test_v3_preserves_exact_integrity_and_has_no_repair_authority(boundary):
    raw = _contract()
    before = copy.deepcopy(raw)
    result = boundary['_validated_recovered_generated_media'](raw, 6, 'b' * 64)
    assert result == {**raw, 'scenes': {3: raw['scenes']['3']}}
    assert result['scenes'][3][0]['sha256'] == 'a' * 64
    assert result['scenes'][3][0]['size'] == 4096
    assert raw == before
    assert 'repair_scene_indices' not in result and 'repair_only' not in result
    boundary['_require_recovered_media_coverage'](result, [3])
    for indices in ([], [0], [0, 3]):
        with pytest.raises(RuntimeError, match='exactly match'):
            boundary['_require_recovered_media_coverage'](result, indices)


@pytest.mark.parametrize('field,value', [
    ('repair_scene_indices', [0]), ('repair_only', True), ('provider', 'gemini_veo'),
    ('recovery_only', False), ('recovery_only', 1), ('version', 3.0),
    ('package_sha256', 'invalid'), ('source_task_id', 'invalid'), ('scenes', {}),
])
def test_invalid_or_expanded_contract_fails_closed(boundary, field, value):
    raw = _contract()
    raw[field] = value
    with pytest.raises(RuntimeError):
        boundary['_validated_recovered_generated_media'](raw, 6, 'b' * 64)


@pytest.mark.parametrize('field,value', [
    ('sha256', None), ('sha256', 'not-a-hash'), ('size', None), ('size', True),
    ('size', 1023), ('size', 100 * 1024 * 1024 + 1), ('provider', 'untrusted'),
    ('provider_attempts', 0), ('synthetic_motion_only', 'false'),
    ('key', 'https://example.invalid/private.mp4'),
    ('key', 'recovery/00000000-0000-4000-8000-000000000000/raw/clip-03.mp4'),
    ('download_url', 'https://example.invalid/private.mp4'),
])
def test_entry_integrity_and_private_key_scope_cannot_be_weakened(boundary, field, value):
    raw = _contract()
    raw['scenes']['3'][0][field] = value
    with pytest.raises(RuntimeError):
        boundary['_validated_recovered_generated_media'](raw, 6, 'b' * 64)


def test_v3_rejects_different_story_string_entries_and_duplicate_keys(boundary):
    validate = boundary['_validated_recovered_generated_media']
    with pytest.raises(RuntimeError):
        validate(_contract(), 6, 'c' * 64)
    raw = _contract()
    raw['scenes']['3'] = [raw['scenes']['3'][0]['key']]
    with pytest.raises(RuntimeError):
        validate(raw, 6, 'b' * 64)
    raw = _contract()
    raw['scenes']['4'] = copy.deepcopy(raw['scenes']['3'])
    with pytest.raises(RuntimeError):
        validate(raw, 6, 'b' * 64)


def test_worker_download_validator_rejects_changed_bytes_and_size(boundary, tmp_path):
    clip = tmp_path / 'recovered.mp4'
    content = b'\x00\x00\x00\x18ftyp' + b'0' * 2048
    clip.write_bytes(content)
    validate = boundary['_validate_recovered_generated_clip']
    digest = hashlib.sha256(content).hexdigest()
    validate(clip, 5.0, expected_size=len(content), expected_sha256=digest)
    with pytest.raises(RuntimeError, match='size'):
        validate(clip, 5.0, expected_size=len(content) + 1, expected_sha256=digest)
    clip.write_bytes(content[:-1] + b'1')
    with pytest.raises(RuntimeError, match='checksum'):
        validate(clip, 5.0, expected_size=len(content), expected_sha256=digest)


@pytest.mark.parametrize('version', [2, 3])
def test_structured_recovery_requires_matching_voice_before_worker_can_synthesize(version):
    guard = next(node for node in ast.walk(TREE) if isinstance(node, ast.If)
                 and 'not recovered_voice' in ast.unparse(node.test)
                 and 'recovered_generated_media' in ast.unparse(node.test))
    namespace = {'FinalVisualQualityError': RuntimeError, 'recovered_voice': None,
                 'recovered_generated_media': {'version': version}}
    with pytest.raises(RuntimeError, match='matching media and voice'):
        exec(compile(ast.Module(body=[guard], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    namespace['recovered_voice'] = {'source_task_id': SOURCE_ID}
    exec(compile(ast.Module(body=[guard], type_ignores=[]), str(SOURCE), 'exec'), namespace)
