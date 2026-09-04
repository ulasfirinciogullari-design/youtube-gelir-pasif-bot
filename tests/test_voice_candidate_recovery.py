import ast
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import audio_checkpoint, storage, voice_candidate_recovery as recovery


SOURCE_ID = '11111111-1111-4111-8111-111111111111'
CHILD_ID = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def stored_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    monkeypatch.setattr(storage.settings, 'bucket', 'test-bucket', raising=False)
    audio = b'ID3' + b'already-paid-voice' * 128
    source_path = tmp_path / f'{SOURCE_ID}.mp3'
    source_path.write_bytes(audio)
    package = {'title': 'Doların kâğıdı', 'scenes': [
        {'narration': 'Doların kâğıdını elinde tutuyorsun.', 'visual_queries': ['dollar bill held in hand'], 'ai_prompt': None},
        {'narration': 'İçinde pamuk ve keten var.', 'visual_queries': ['cotton linen fibers'], 'ai_prompt': None},
    ], 'sources': [{
        'url': 'https://www.bep.gov/currency/how-money-is-made',
        'evidence': 'U.S. currency paper contains 75% cotton and 25% linen.',
    }]}
    voice = {
        'path': str(source_path),
        'spoken_texts': [scene['narration'] for scene in package['scenes']],
        'scene_durations': [14.0, 13.24],
        'duration_before_fit': 27.24, 'duration_after_fit': 27.24,
        'tempo_rate': 1.0, 'content_target_seconds': 29.5,
        'reserved_tail_seconds': 0.5, 'voice_name': 'Narrator',
        'voice_model': 'eleven_flash_v2_5', 'voice_language_code': 'tr',
    }
    objects = {}

    def upload(path, key, _content_type):
        objects[key] = Path(path).read_bytes()

    monkeypatch.setattr(audio_checkpoint, 'upload_file', upload)
    pointer = audio_checkpoint.persist_audio_candidate_checkpoint(SOURCE_ID, package, voice)['audio_candidate_checkpoint']
    calls = []
    bodies = []
    declared_lengths = {}

    def get_object(*, Bucket, Key):
        assert Bucket == 'test-bucket'
        calls.append(Key)
        body = io.BytesIO(objects[Key])
        bodies.append(body)
        return {'Body': body, 'ContentLength': declared_lengths.get(Key, len(objects[Key])),
                'ResponseMetadata': {'SECRET': 'provider-private-data'}}

    monkeypatch.setattr(storage, '_client', lambda: SimpleNamespace(get_object=get_object))
    work = tmp_path / 'youtube_factory' / f'{CHILD_ID}_attempt_0'
    work.mkdir(parents=True)
    return SimpleNamespace(pointer=pointer, objects=objects, calls=calls, bodies=bodies,
                           declared_lengths=declared_lengths, audio=audio, work=work,
                           package=package, voice=voice, source_path=source_path)


def _load(candidate, **kwargs):
    return recovery.load_voice_retry_candidate(
        kwargs.pop('source_task_id', SOURCE_ID), kwargs.pop('child_task_id', CHILD_ID),
        kwargs.pop('checkpoint', candidate.pointer), kwargs.pop('work_dir', candidate.work),
        **kwargs,
    )


def _replace_metadata(candidate, change):
    metadata = json.loads(candidate.objects[candidate.pointer['metadata_key']])
    change(metadata)
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    key = candidate.pointer['metadata_key'].rsplit('/', 1)[0] + f'/metadata-{digest}.json'
    candidate.objects[key] = encoded
    candidate.pointer['metadata_key'] = key
    candidate.pointer['metadata_sha256'] = digest


def test_loader_binds_existing_audio_to_child_and_reconstructs_unapproved_story(stored_candidate):
    candidate = stored_candidate
    result = _load(candidate)
    assert result['source_task_id'] == SOURCE_ID
    assert result['child_task_id'] == CHILD_ID
    assert result['status'] == 'unapproved_candidate'
    assert result['qa_approved'] is False
    assert result['requires_full_qa'] is True
    assert result['audio_sha256'] == hashlib.sha256(candidate.audio).hexdigest()
    assert result['package']['narration'] == ' '.join(scene['narration'] for scene in candidate.package['scenes'])
    assert result['package']['sources'] == candidate.package['sources']
    assert result['voice_result']['path'] == str(candidate.work / 'recovered_voice.mp3')
    assert Path(result['voice_result']['path']).read_bytes() == candidate.audio
    assert result['voice_result']['spoken_texts'] == candidate.voice['spoken_texts']
    assert result['voice_result']['duration_after_fit'] == 27.24
    assert result['voice_result']['voice_name'] == 'Narrator'
    assert candidate.source_path.read_bytes() == candidate.audio
    assert candidate.calls == [candidate.pointer['metadata_key'], candidate.pointer['audio_key']]
    assert all(body.closed for body in candidate.bodies)
    for forbidden in ('approved_package', '_recovered_voice', '_recovered_generated_media', 'short_story_qc', 'manual_qa_required', 'pass', 'SECRET'):
        assert forbidden not in result
        assert forbidden not in result['package']
        assert forbidden not in result['voice_result']


@pytest.mark.parametrize('kind', ['source_id', 'same_id', 'wrong_work', 'relative_work', 'traversal_work', 'approved_pointer', 'private_key', 'wrong_source_key', 'extra_pointer', 'existing_destination'])
def test_identity_scope_and_pointer_guards_fail_before_storage(stored_candidate, kind):
    candidate = stored_candidate
    kwargs = {}
    if kind == 'source_id':
        kwargs['source_task_id'] = '../not-canonical'
    elif kind == 'same_id':
        kwargs['child_task_id'] = SOURCE_ID
    elif kind == 'wrong_work':
        kwargs['work_dir'] = candidate.work.parent
    elif kind == 'relative_work':
        kwargs['work_dir'] = f'youtube_factory/{CHILD_ID}_attempt_0'
    elif kind == 'traversal_work':
        kwargs['work_dir'] = candidate.work / '..' / candidate.work.name
    elif kind == 'approved_pointer':
        candidate.pointer['qa_approved'] = True
    elif kind == 'private_key':
        candidate.pointer['audio_key'] = 'https://private.example.test?token=SECRET'
    elif kind == 'wrong_source_key':
        candidate.pointer['audio_key'] = candidate.pointer['audio_key'].replace(SOURCE_ID, CHILD_ID)
    elif kind == 'extra_pointer':
        candidate.pointer['approved_package'] = {}
    else:
        (candidate.work / 'recovered_voice.mp3').write_bytes(b'preserve-existing')
    with pytest.raises(recovery.VoiceCandidateRecoveryError, match='^Voice retry candidate unavailable$'):
        _load(candidate, **kwargs)
    assert candidate.calls == []
    if kind == 'existing_destination':
        assert (candidate.work / 'recovered_voice.mp3').read_bytes() == b'preserve-existing'


@pytest.mark.parametrize('kind', ['metadata_hash', 'metadata_source', 'metadata_approval', 'package_hash', 'fake_attestation', 'voice_profile', 'voice_timing', 'metadata_size', 'short_body'])
def test_invalid_metadata_cannot_download_audio_or_grant_approval(stored_candidate, kind):
    candidate = stored_candidate
    if kind == 'metadata_hash':
        candidate.objects[candidate.pointer['metadata_key']] += b' '
    elif kind == 'metadata_source':
        _replace_metadata(candidate, lambda value: value.update(source_task_id=CHILD_ID))
    elif kind == 'metadata_approval':
        _replace_metadata(candidate, lambda value: value.update(qa_approved=True))
    elif kind == 'package_hash':
        _replace_metadata(candidate, lambda value: value['package'].update(title='Changed without package digest'))
    elif kind == 'fake_attestation':
        _replace_metadata(candidate, lambda value: value['package'].update(short_story_qc={'pass': True}))
    elif kind == 'voice_profile':
        _replace_metadata(candidate, lambda value: value['voice']['voice_profile'].update(api_key='SECRET'))
    elif kind == 'voice_timing':
        _replace_metadata(candidate, lambda value: value['voice'].update(scene_durations=[1.0, 1.0]))
    elif kind == 'metadata_size':
        candidate.declared_lengths[candidate.pointer['metadata_key']] = audio_checkpoint.MAX_AUDIO_CANDIDATE_METADATA_BYTES + 1
    else:
        candidate.declared_lengths[candidate.pointer['metadata_key']] = len(candidate.objects[candidate.pointer['metadata_key']]) + 1
    with pytest.raises(recovery.VoiceCandidateRecoveryError):
        _load(candidate)
    assert candidate.calls == [candidate.pointer['metadata_key']]
    assert not (candidate.work / 'recovered_voice.mp3').exists()
    assert all(body.closed for body in candidate.bodies)


@pytest.mark.parametrize('kind', ['hash', 'declared_size', 'extra_stream_bytes'])
def test_corrupt_or_oversized_audio_is_never_installed(stored_candidate, kind):
    candidate = stored_candidate
    key = candidate.pointer['audio_key']
    if kind == 'hash':
        candidate.objects[key] = b'x' + candidate.audio[1:]
    elif kind == 'declared_size':
        candidate.declared_lengths[key] = audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES + 1
    else:
        candidate.objects[key] += b'extra'
        candidate.declared_lengths[key] = len(candidate.audio)
    with pytest.raises(recovery.VoiceCandidateRecoveryError):
        _load(candidate)
    assert not (candidate.work / 'recovered_voice.mp3').exists()
    assert all(body.closed for body in candidate.bodies)
    assert candidate.source_path.read_bytes() == candidate.audio


def test_immutable_guard_allows_only_nonspoken_editorial_repairs(stored_candidate):
    candidate = _load(stored_candidate)['package']
    reviewed = copy.deepcopy(candidate)
    reviewed['title'] = 'A corrected title'
    reviewed['scenes'][0]['visual_queries'] = ['better exact stock search']
    reviewed['scenes'][1]['ai_prompt'] = 'A corrected visual-only direction.'
    reviewed['short_story_qc'] = {'independently_rechecked': True}
    assert recovery.require_unchanged_voice_narration(candidate, reviewed) is None


@pytest.mark.parametrize('kind', ['text', 'whitespace', 'punctuation', 'order', 'scene_count', 'index', 'aggregate'])
def test_changed_narration_never_reuses_old_voice(stored_candidate, kind):
    candidate = _load(stored_candidate)['package']
    reviewed = copy.deepcopy(candidate)
    if kind == 'text':
        reviewed['scenes'][0]['narration'] = 'Different words.'
    elif kind == 'whitespace':
        reviewed['scenes'][0]['narration'] += ' '
    elif kind == 'punctuation':
        reviewed['scenes'][0]['narration'] = reviewed['scenes'][0]['narration'].rstrip('.')
    elif kind == 'order':
        reviewed['scenes'].reverse()
    elif kind == 'scene_count':
        reviewed['scenes'].pop()
    elif kind == 'index':
        reviewed['scenes'][0]['index'] = 1
    else:
        reviewed['narration'] = 'Different aggregate narration.'
    with pytest.raises(recovery.VoiceCandidateRecoveryError, match='^Voice retry narration changed$'):
        recovery.require_unchanged_voice_narration(candidate, reviewed)


def test_storage_failure_is_sanitized_and_module_never_imports_tts_or_tasks(stored_candidate, monkeypatch, capsys):
    def fail():
        raise RuntimeError('SECRET https://private.example.test?token=SECRET')

    monkeypatch.setattr(storage, '_client', fail)
    with pytest.raises(recovery.VoiceCandidateRecoveryError) as failure:
        _load(stored_candidate)
    assert str(failure.value) == 'Voice retry candidate unavailable'
    assert failure.value.__suppress_context__ is True
    assert capsys.readouterr() == ('', '')
    tree = ast.parse(Path(recovery.__file__).read_text(encoding='utf-8'))
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    for forbidden in ('app.tasks', 'app.services.voice', 'app.services.audio_qc', 'app.services.director'):
        assert forbidden not in imported
