import ast
import hashlib
import json
from pathlib import Path

import pytest

from app.services import audio_checkpoint, audio_evidence


TASK_ID = '11111111-1111-4111-8111-111111111111'
OTHER_TASK_ID = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def existing_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    path = tmp_path / f'{TASK_ID}.mp3'
    audio = b'ID3' + b'existing-paid-audio' * 128
    path.write_bytes(audio)
    uploads = []

    def upload(local, key, content_type):
        uploads.append((Path(local), key, content_type, Path(local).read_bytes()))
        return {'url': 'https://private.example.test?api_key=SECRET', 'headers': {'Authorization': 'SECRET'}}

    monkeypatch.setattr(audio_evidence, 'upload_file', upload)
    return path, audio, uploads


def _store(path, payload, **kwargs):
    return audio_evidence.persist_audio_provider_evidence(
        TASK_ID, path,
        provider=kwargs.pop('provider', 'openai'),
        model=kwargs.pop('model', 'whisper-1'),
        language=kwargs.pop('language', 'tr'),
        payload=payload, **kwargs,
    )


@pytest.mark.parametrize('provider,model,text_field', [
    ('openai', 'whisper-1', 'word'),
    ('elevenlabs', 'scribe_v2', 'text'),
])
def test_existing_word_evidence_is_whitelisted_and_audio_is_only_hashed(existing_audio, provider, model, text_field):
    path, audio, uploads = existing_audio
    payload = {
        'text': "Doların yüzde yetmiş beşi pamuk.",
        'language_code': 'tr',
        'words': [{text_field: 'yüzde', 'start': 0.0, 'end': 0.3, 'type': 'word',
                   'speaker_id': 'PRIVATE', 'url': 'https://private.example.test'}],
        'headers': {'Authorization': 'SECRET'},
        'api_key': 'SECRET', 'request_id': 'PRIVATE',
        'url': 'https://private.example.test?token=SECRET',
        'error': {'message': 'SECRET'}, 'qa': {'pass': True},
    }
    pointer = _store(path, payload, provider=provider, model=model)
    assert len(uploads) == 1
    temporary, key, content_type, encoded = uploads[0]
    assert content_type == 'application/json'
    assert temporary.suffix == '.json'
    assert not temporary.exists()
    assert pointer['key'] == key
    assert pointer['audio_sha256'] == hashlib.sha256(audio).hexdigest()
    assert pointer['evidence_sha256'] == hashlib.sha256(encoded).hexdigest()
    assert pointer['audio_size'] == len(audio)
    assert pointer['size'] == len(encoded) <= audio_evidence.MAX_AUDIO_EVIDENCE_BYTES
    saved = json.loads(encoded)
    assert saved['payload'] == {
        'text': payload['text'], 'language_code': 'tr',
        'words': [{text_field: 'yüzde', 'start': 0.0, 'end': 0.3, 'type': 'word'}],
    }
    for record in (saved, pointer):
        assert record['status'] == 'unvalidated_provider_evidence'
        assert record['diagnostic_only'] is True
        assert record['qa_approved'] is False
        assert record['requires_full_qa'] is True
        serialized = json.dumps(record)
        for excluded in ('SECRET', 'PRIVATE', 'https://', 'headers', 'request_id', '"pass"', str(path.parent)):
            assert excluded not in serialized
    assert path.read_bytes() == audio


def test_gemini_retains_word_info_offsets_but_not_tool_output_files_or_secrets(existing_audio):
    path, _audio, uploads = existing_audio
    content = {
        'type': 'text', 'text': 'Yüzde yetmiş beşi pamuk.',
        'annotations': [{
            'type': 'word_info', 'text': 'yetmiş beşi',
            'start_offset': '0.00s', 'end_offset': {'seconds': 1, 'nanos': 500000000, 'api_key': 'SECRET'},
            'headers': {'Authorization': 'SECRET'},
        }, {'type': 'file', 'uri': 'https://private.example.test?SECRET'}],
    }
    payload = {
        'status': 'completed',
        'steps': [
            {'type': 'tool_result', 'text': 'SECRET'},
            {'type': 'model_output', 'content': [content, {'type': 'audio', 'uri': 'https://private.example.test?SECRET'}],
             'error': {'message': 'SECRET'}, 'request_headers': {'Authorization': 'SECRET'}},
        ],
        'url': 'https://private.example.test?SECRET',
    }
    _store(path, payload, provider='gemini', model='gemini-3.5-transcribe', language='tr-TR')
    saved = json.loads(uploads[0][3])
    assert saved['payload'] == {
        'status': 'completed',
        'steps': [{'type': 'model_output', 'content': [{
            'type': 'text', 'text': content['text'],
            'annotations': [{
                'type': 'word_info', 'text': 'yetmiş beşi',
                'start_offset': '0.00s', 'end_offset': {'seconds': 1, 'nanos': 500000000},
            }],
        }]}],
    }
    assert 'SECRET' not in uploads[0][3].decode()


@pytest.mark.parametrize('timing', [0, -1.5, 'not-a-time', None, True, '0s', '-1.0s'])
def test_invalid_safe_timing_values_survive_without_validation_or_repair(existing_audio, timing):
    path, _audio, uploads = existing_audio
    payload = {'text': '', 'words': [{'word': '%', 'start': timing, 'end': timing}, {'text': '75i', 'end': 0}]}
    _store(path, payload)
    assert json.loads(uploads[0][3])['payload'] == payload


def test_gemini_malformed_offset_strings_and_missing_values_are_preserved(existing_audio):
    path, _audio, uploads = existing_audio
    payload = {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{
        'type': 'text', 'text': 'Yetmiş beş.', 'annotations': [{
            'type': 'word_info', 'text': 'Yetmiş beş',
            'start_offset': 'bad-but-safe', 'end_offset': {'nanos': -5},
        }],
    }]}]}
    _store(path, payload, provider='gemini', model='gemini-3.5-transcribe')
    assert json.loads(uploads[0][3])['payload'] == payload


def test_content_addressed_keys_are_stable_and_bind_changed_audio_or_payload(existing_audio):
    path, audio, _uploads = existing_audio
    payload = {'text': 'Pamuk.', 'words': [{'word': 'Pamuk', 'start': 0, 'end': 1}]}
    first = _store(path, payload)
    assert _store(path, payload) == first
    changed = _store(path, {**payload, 'text': 'Keten.'})
    assert changed['key'] != first['key']
    assert changed['audio_sha256'] == first['audio_sha256']
    path.write_bytes(audio + b'different')
    changed_audio = _store(path, payload)
    assert changed_audio['key'] != first['key']
    assert changed_audio['audio_sha256'] != first['audio_sha256']


@pytest.mark.parametrize('unsafe', [
    'https://private.example.test?token=SECRET', 'ftp://private.example.test',
    'Authorization: Bearer SECRET', 'api_key=SECRET',
    'sk-abcdefghijklmnopqrstuvwxyz', 'AIzaABCDEFGHIJKLMNOPQRSTUVWXYZ012345',
])
def test_sensitive_whitelisted_text_is_not_persisted(existing_audio, unsafe):
    path, _audio, uploads = existing_audio
    with pytest.raises(audio_evidence.AudioProviderEvidenceError, match='^Audio provider evidence unavailable$'):
        _store(path, {'text': unsafe, 'words': []})
    assert uploads == []


@pytest.mark.parametrize('kind', ['too_big_payload', 'too_many_words', 'too_big_audio', 'nan', 'foreign_path', 'remote_path', 'invalid_provider', 'invalid_model', 'secret_model', 'invalid_language'])
def test_bounds_and_identities_fail_safely_before_storage(existing_audio, kind):
    path, _audio, uploads = existing_audio
    payload = {'text': 'Pamuk.', 'words': []}
    kwargs = {}
    if kind == 'too_big_payload':
        payload['text'] = 'ş' * audio_evidence.MAX_AUDIO_EVIDENCE_BYTES
    elif kind == 'too_many_words':
        payload['words'] = [{}] * (audio_evidence.MAX_AUDIO_EVIDENCE_ITEMS + 1)
    elif kind == 'too_big_audio':
        with path.open('r+b') as stream:
            stream.truncate(audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES + 1)
    elif kind == 'nan':
        payload['words'] = [{'start': float('nan')}]
    elif kind == 'foreign_path':
        path = path.parent / f'{OTHER_TASK_ID}.mp3'
        path.write_bytes(b'ID3' + b'x' * 1500)
    elif kind == 'remote_path':
        path = 'https://private.example.test?token=SECRET'
    elif kind == 'invalid_provider':
        kwargs['provider'] = 'untrusted'
    elif kind == 'invalid_model':
        kwargs['model'] = 'https://private.example.test'
    elif kind == 'secret_model':
        kwargs['model'] = 'sk-abcdefghijklmnopqrstuvwxyz'
    else:
        kwargs['language'] = 'Authorization: SECRET'
    with pytest.raises(audio_evidence.AudioProviderEvidenceError, match='^Audio provider evidence unavailable$'):
        _store(path, payload, **kwargs)
    assert uploads == []


def test_storage_errors_are_fixed_and_do_not_modify_audio_or_print_details(existing_audio, monkeypatch, capsys):
    path, audio, _uploads = existing_audio

    def fail(*_args):
        raise RuntimeError('Authorization: SECRET https://private.example.test')

    monkeypatch.setattr(audio_evidence, 'upload_file', fail)
    with pytest.raises(audio_evidence.AudioProviderEvidenceError) as failure:
        _store(path, {'text': 'Pamuk.'})
    assert str(failure.value) == 'Audio provider evidence unavailable'
    assert failure.value.__suppress_context__ is True
    assert capsys.readouterr() == ('', '')
    assert path.read_bytes() == audio


def test_module_has_no_eager_task_qc_voice_or_network_provider_imports():
    tree = ast.parse(Path(audio_evidence.__file__).read_text(encoding='utf-8'))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or '')
    for forbidden in ('app.tasks', 'app.services.audio_qc', 'app.services.voice', 'httpx', 'requests', 'openai'):
        assert forbidden not in imports
