import hashlib
import json
from pathlib import Path

import pytest

from app.services import audio_checkpoint


TASK_ID = '11111111-1111-4111-8111-111111111111'
OTHER_TASK_ID = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    audio = b'ID3' + b'paid-existing-audio' * 128
    path = tmp_path / f'{TASK_ID}.mp3'
    path.write_bytes(audio)
    package = {
        'title': 'Doların kâğıdı',
        'thumbnail_text': 'Pamuk mu?',
        'scenes': [{
            'narration': 'Doların kâğıdı pamuk ve keten içerir.',
            'visual_queries': ['us dollar in hands'],
            'ai_prompt': None,
            'pace': 'steady',
            'transition': 'cut',
            'headers': {'Authorization': 'DO NOT EXPORT'},
        }],
        'sources': [{'url': 'https://private.example.test/signed?token=SECRET'}],
        'api_key': 'DO NOT EXPORT',
        'qa': {'pass': True},
    }
    voice = {
        'path': str(path),
        'spoken_texts': ['Doların kâğıdı pamuk ve keten içerir.'],
        'scene_durations': [29.5],
        'duration_before_fit': 30.1,
        'duration_after_fit': 29.5,
        'tempo_rate': 1.0203,
        'content_target_seconds': 29.5,
        'reserved_tail_seconds': 0.5,
        'removed_silence_seconds': 0.1,
        'voice_name': 'Narrator',
        'voice_model': 'eleven_multilingual_v2',
        'voice_language_code': 'tr',
        'url': 'https://private.example.test/voice?token=SECRET',
        'headers': {'Authorization': 'DO NOT EXPORT'},
        'voice_id': 'PRIVATE PROVIDER ID',
        'audio_qc': {'pass': True, 'score': 100},
    }
    uploaded = []

    def upload(local, key, content_type):
        uploaded.append((key, content_type, Path(local).read_bytes(), Path(local)))
        return {'url': 'https://private.example.test?SECRET', 'api_key': 'DO NOT EXPORT'}

    monkeypatch.setattr(audio_checkpoint, 'upload_file', upload)
    return package, voice, audio, uploaded


def test_checkpoint_preserves_existing_audio_and_whitelisted_unapproved_metadata(candidate):
    package, voice, audio, uploaded = candidate
    result = audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert set(result) == {'audio_candidate_checkpoint'}
    pointer = result['audio_candidate_checkpoint']
    assert pointer['status'] == 'unapproved_candidate'
    assert pointer['qa_approved'] is False
    assert pointer['requires_full_qa'] is True
    assert len(uploaded) == 2
    assert uploaded[0][1:3] == ('audio/mpeg', audio)
    assert pointer['audio_sha256'] == hashlib.sha256(audio).hexdigest()
    assert pointer['metadata_sha256'] == hashlib.sha256(uploaded[1][2]).hexdigest()
    metadata = json.loads(uploaded[1][2])
    assert metadata['package']['scenes'][0]['narration'] == package['scenes'][0]['narration']
    assert metadata['voice']['spoken_texts'] == voice['spoken_texts']
    assert metadata['voice']['scene_durations'] == voice['scene_durations']
    assert metadata['voice']['duration_before_fit'] == 30.1
    assert metadata['voice']['duration_after_fit'] == 29.5
    assert metadata['voice']['tempo_rate'] == 1.0203
    assert metadata['voice']['voice_profile'] == {
        'voice_name': 'Narrator',
        'voice_model': 'eleven_multilingual_v2',
        'voice_language_code': 'tr',
    }
    assert metadata['qa_approved'] is False
    for text in (uploaded[1][2].decode(), json.dumps(result)):
        for excluded in ('https://', 'SECRET', 'DO NOT EXPORT', 'PRIVATE PROVIDER ID', 'headers', 'audio_qc', '"pass"', str(Path(voice['path']).parent)):
            assert excluded not in text
    assert Path(voice['path']).read_bytes() == audio


_EN_BUDGET = {'version': 1, 'profile': 'fresh_en_30s_v1', 'language': 'en',
              'duration_minutes': 0.5, 'target_words': 65, 'minimum_words': 62, 'maximum_words': 66}


def test_fixed_word_budget_survives_content_addressed_unapproved_voice_recovery(candidate, monkeypatch):
    import io
    from types import SimpleNamespace
    from app.services import storage, voice_candidate_recovery

    monkeypatch.setattr(storage.settings, 'bucket', 'test-bucket', raising=False)
    package, voice, audio, uploaded = candidate
    package['spoken_word_budget'] = dict(_EN_BUDGET)
    pointer = audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)['audio_candidate_checkpoint']
    metadata = json.loads(uploaded[1][2])
    assert metadata['package']['spoken_word_budget'] == _EN_BUDGET
    assert metadata['qa_approved'] is False and metadata['requires_full_qa'] is True
    package_bytes = json.dumps(metadata['package'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(package_bytes).hexdigest() == pointer['package_sha256']
    objects = {key: content for key, _, content, _ in uploaded}
    monkeypatch.setattr(storage, '_client', lambda: SimpleNamespace(get_object=lambda **kw: {
        'Body': io.BytesIO(objects[kw['Key']]), 'ContentLength': len(objects[kw['Key']]),
    }))
    work = Path(voice['path']).parent / 'youtube_factory' / f'{OTHER_TASK_ID}_attempt_0'
    work.mkdir(parents=True)
    recovered = voice_candidate_recovery.load_voice_retry_candidate(TASK_ID, OTHER_TASK_ID, pointer, work)
    assert recovered['package']['spoken_word_budget'] == _EN_BUDGET
    assert recovered['qa_approved'] is False and recovered['requires_full_qa'] is True
    assert Path(recovered['voice_result']['path']).read_bytes() == audio


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('profile', 'custom'), ('language', 'tr'),
    ('duration_minutes', True), ('duration_minutes', 1), ('target_words', 66),
    ('minimum_words', 51), ('maximum_words', 100), ('qa_approved', True),
    ('api_key', 'PRIVATE'),
])
def test_checkpoint_rejects_noncanonical_budget_before_upload(candidate, field, value):
    package, voice, _audio, uploaded = candidate
    package['spoken_word_budget'] = {**_EN_BUDGET, field: value}
    with pytest.raises(audio_checkpoint.AudioCandidateCheckpointError):
        audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert uploaded == []


def test_checkpoint_without_budget_keeps_legacy_package_shape(candidate):
    package, voice, _audio, uploaded = candidate
    audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert 'spoken_word_budget' not in json.loads(uploaded[1][2])['package']
    assert all(not entry[3].exists() for entry in uploaded)


def test_identical_retry_is_content_addressed_but_changed_metadata_gets_new_key(candidate):
    package, voice, _audio, uploaded = candidate
    first = audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    second = audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert second == first
    assert uploaded[0][:3] == uploaded[2][:3]
    assert uploaded[1][:3] == uploaded[3][:3]
    package['title'] = 'Yeni başlık'
    third = audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert third['audio_candidate_checkpoint']['audio_key'] == first['audio_candidate_checkpoint']['audio_key']
    assert third['audio_candidate_checkpoint']['metadata_key'] != first['audio_candidate_checkpoint']['metadata_key']


def test_public_citations_survive_but_private_signed_and_provider_links_do_not(candidate):
    package, voice, _audio, uploaded = candidate
    public = {
        'url': 'https://www.bep.gov/currency/how-money-is-made',
        'evidence': 'U.S. currency paper consists of 75% cotton and 25% linen.',
    }
    unsafe_urls = (
        'https://www.bep.gov/currency?token=SECRET',
        'https://www.bep.gov/currency#SECRET',
        'https://user:pass@www.bep.gov/currency',
        'http://127.0.0.1/source',
        'http://10.1.2.3/source',
        'http://169.254.169.254/source',
        'http://[::1]/source',
        'http://intranet/source',
        'http://service.internal/source',
        'http://private.example.test/source',
        'https://media.storageapi.dev/audio.mp3',
        'https://bucket.s3.amazonaws.com/audio.mp3',
        'https://api.elevenlabs.io/v1/audio',
        'https://www.bep.gov:8443/source',
    )
    package['sources'] = [public, *[dict(public, url=url) for url in unsafe_urls], public]
    audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    metadata = json.loads(uploaded[1][2])
    assert metadata['package']['sources'] == [public]
    assert all(url not in uploaded[1][2].decode() for url in unsafe_urls)


def test_source_evidence_cannot_smuggle_credentials_or_extra_fields(candidate):
    package, voice, _audio, uploaded = candidate
    package['sources'] = [{
        'url': 'https://www.bep.gov/currency/how-money-is-made',
        'evidence': 'Authorization: Bearer SECRET-CREDENTIAL',
        'headers': {'Authorization': 'SECRET-CREDENTIAL'},
    }]
    audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    metadata = json.loads(uploaded[1][2])
    assert metadata['package']['sources'] == []
    assert 'SECRET-CREDENTIAL' not in uploaded[1][2].decode()


@pytest.mark.parametrize('relative', [
    f'{TASK_ID}_audio_retry_1.mp3',
    f'{TASK_ID}_voice/joined.mp3',
    f'{TASK_ID}_audio_retry_1_voice/joined.mp3',
    f'youtube_factory/{TASK_ID}_attempt_1/recovered_voice.mp3',
])
def test_known_same_task_audio_paths_are_allowed(candidate, tmp_path, relative):
    package, voice, audio, uploaded = candidate
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    voice['path'] = str(path)
    audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert len(uploaded) == 2


@pytest.mark.parametrize('kind', ['url', 'relative', 'other_job', 'unexpected_name', 'traversal', 'outside_root'])
def test_rejects_untrusted_paths_before_upload(candidate, tmp_path, kind):
    package, voice, audio, uploaded = candidate
    other = tmp_path / f'{OTHER_TASK_ID}.mp3'
    other.write_bytes(audio)
    values = {
        'url': 'https://private.example.test/existing.mp3?token=SECRET',
        'relative': f'{TASK_ID}.mp3',
        'other_job': str(other),
        'unexpected_name': str(tmp_path / 'credentials.mp3'),
        'traversal': str(tmp_path / 'inside' / '..' / f'{TASK_ID}.mp3'),
        'outside_root': str(tmp_path.parent / f'{TASK_ID}.mp3'),
    }
    voice['path'] = values[kind]
    with pytest.raises(audio_checkpoint.AudioCandidateCheckpointError, match='^Audio candidate checkpoint unavailable$'):
        audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert uploaded == []


def test_rejects_symlink_to_another_task_without_reading_it(candidate, tmp_path, monkeypatch):
    package, voice, audio, uploaded = candidate
    other = tmp_path / f'{OTHER_TASK_ID}.mp3'
    other.write_bytes(audio)
    original_resolve = Path.resolve

    def fake_symlink_resolve(path, *args, **kwargs):
        if path == Path(voice['path']):
            return other
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', fake_symlink_resolve)
    with pytest.raises(audio_checkpoint.AudioCandidateCheckpointError):
        audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert uploaded == []


@pytest.mark.parametrize('kind', ['empty', 'too_large', 'not_mp3', 'nan', 'missing_scenes', 'secret_in_text', 'invalid_id'])
def test_invalid_or_sensitive_candidate_is_not_uploaded(candidate, kind):
    package, voice, _audio, uploaded = candidate
    task_id = TASK_ID
    if kind == 'empty':
        Path(voice['path']).write_bytes(b'')
    elif kind == 'too_large':
        with Path(voice['path']).open('r+b') as stream:
            stream.truncate(audio_checkpoint.MAX_AUDIO_CANDIDATE_BYTES + 1)
    elif kind == 'not_mp3':
        Path(voice['path']).write_bytes(b'x' * 1500)
    elif kind == 'nan':
        voice['tempo_rate'] = float('nan')
    elif kind == 'missing_scenes':
        voice['spoken_texts'] = []
    elif kind == 'secret_in_text':
        package['scenes'][0]['narration'] = 'Authorization: Bearer PRIVATE'
    else:
        task_id = '../not-a-job'
    with pytest.raises(audio_checkpoint.AudioCandidateCheckpointError, match='^Audio candidate checkpoint unavailable$'):
        audio_checkpoint.persist_audio_candidate_checkpoint(task_id, package, voice)
    assert uploaded == []


@pytest.mark.parametrize('failed_upload', [1, 2])
def test_storage_failure_never_returns_success_or_leaks_provider_detail(candidate, monkeypatch, capsys, failed_upload):
    package, voice, audio, _uploaded = candidate
    count = 0

    def fail_upload(*_args):
        nonlocal count
        count += 1
        if count == failed_upload:
            raise RuntimeError('https://private.example.test?api_key=SECRET Authorization: PRIVATE')

    monkeypatch.setattr(audio_checkpoint, 'upload_file', fail_upload)
    with pytest.raises(audio_checkpoint.AudioCandidateCheckpointError) as failure:
        audio_checkpoint.persist_audio_candidate_checkpoint(TASK_ID, package, voice)
    assert str(failure.value) == 'Audio candidate checkpoint unavailable'
    assert failure.value.__suppress_context__ is True
    assert count == failed_upload
    assert capsys.readouterr() == ('', '')
    assert Path(voice['path']).read_bytes() == audio
