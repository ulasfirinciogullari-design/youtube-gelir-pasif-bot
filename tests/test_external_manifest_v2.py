"""Bounded v2 media contract; real local encode and fake Storage only."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess

import pytest

from app.services import external_artifact_import as artifact, external_master_ingest as ingest
from test_external_artifact_import import staged, _manifest, _run
from test_external_master_ingest import case, run as ingest_run, job, CHANNEL, post


def _captions(scenes):
    def stamp(ms):
        seconds, millis = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'
    return ('\n\n'.join(f'{i + 1}\n{stamp(s["start_ms"])} --> {stamp(s["end_ms"])}\n{s["narration"]}'
                       for i, s in enumerate(scenes)) + '\n').encode('utf-8')


def _v2(c, duration=35833, language='tr'):
    c.manifest.update(version=2, duration_ms=duration, language=language)
    for i, row in enumerate(c.manifest['scenes']):
        row.update(start_ms=duration * i // 6, end_ms=duration * (i + 1) // 6,
                   narration=f'Bu bölümde kasadaki barkodun öyküsü anlatılıyor {i + 1}.' if language == 'tr'
                             else f'This is the complete narration for scene {i + 1}.')
    captions = _captions(c.manifest['scenes'])
    c.captions.write_bytes(captions)
    c.manifest['files']['captions'].update(size=len(captions), sha256=hashlib.sha256(captions).hexdigest())
    for stream in c.probe['streams']:
        stream['duration'] = str(duration / 1000)
    c.probe['streams'][0]['nb_read_frames'] = str(round(duration * 30 / 1000))
    c.probe['format']['duration'] = str(duration / 1000)


@pytest.mark.parametrize('duration', [15000, 35833, 60000])
@pytest.mark.parametrize('language', ['tr', 'en'])
def test_v2_accepts_only_real_declared_duration_and_preserves_unapproved_contract(staged, duration, language):
    _v2(staged, duration, language)
    original = deepcopy(staged.manifest)
    result = _run(staged)
    assert result['manifest'] == original and staged.manifest == original
    assert result['media_structure']['video_duration_seconds'] == duration / 1000
    assert result['media_structure']['frame_count'] == round(duration * 30 / 1000)
    assert result['caption_contract']['cues'][-1]['end_ms'] == duration
    assert result['manifest_sha256'] == hashlib.sha256(artifact._json(original).encode()).hexdigest()
    assert result['qa_approved'] is result['publish_eligible'] is result['media_generation_authorized'] is False
    assert result['audio_transcription_verified'] is result['source_evidence_verified'] is False
    assert result['version'] == 1  # Descriptor schema is unchanged; nested manifest has its own version.


@pytest.mark.parametrize('field,value', [('version', 0), ('version', 3), ('version', True),
    ('duration_ms', 14999), ('duration_ms', 60001), ('duration_ms', 35833.0), ('duration_ms', True),
    ('language', 'de'), ('language', 'es'), ('language', 'ar'), ('language', 'xx'),
    ('qa_approved', True), ('duration_minutes', .5)])
def test_v2_unknown_version_duration_language_and_flags_fail_before_probe(staged, field, value):
    _v2(staged)
    staged.manifest[field] = value
    with pytest.raises(artifact.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('mutation', ['scene_count', 'gap', 'overlap', 'short_scene', 'long_scene',
    'caption_text', 'caption_range', 'video_hash', 'caption_hash', 'source'])
def test_v2_keeps_six_scene_caption_hash_and_source_integrity(staged, mutation):
    _v2(staged)
    if mutation == 'scene_count': staged.manifest['scenes'].pop()
    elif mutation == 'gap': staged.manifest['scenes'][2]['start_ms'] += 1
    elif mutation == 'overlap': staged.manifest['scenes'][2]['start_ms'] -= 1
    elif mutation == 'short_scene': staged.manifest['scenes'][0]['end_ms'] = 999
    elif mutation == 'long_scene': staged.manifest['scenes'][0]['end_ms'] = 10001
    elif mutation == 'caption_text': staged.manifest['scenes'][1]['narration'] += ' Yanlış.'
    elif mutation == 'caption_range':
        changed = staged.captions.read_bytes().replace(b'00:00:35,833', b'00:00:35,834')
        staged.captions.write_bytes(changed)
        staged.manifest['files']['captions'].update(size=len(changed), sha256=hashlib.sha256(changed).hexdigest())
    elif mutation.endswith('_hash'):
        staged.manifest['files']['video' if mutation == 'video_hash' else 'captions']['sha256'] = '0' * 64
    else: staged.manifest['sources'][0]['url'] = 'https://localhost/private'
    with pytest.raises(artifact.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('mutation', ['old_video_duration', 'wrong_container_duration', 'wrong_frame_count',
                                    'missing_audio', 'wrong_codec', 'audio_overrun'])
def test_v2_does_not_relax_actual_probe_checks(staged, mutation):
    _v2(staged)
    video, audio = staged.probe['streams']
    if mutation == 'old_video_duration': video['duration'] = '30'
    elif mutation == 'wrong_container_duration': staged.probe['format']['duration'] = '40'
    elif mutation == 'wrong_frame_count': video['nb_read_frames'] = '900'
    elif mutation == 'missing_audio': staged.probe['streams'].pop()
    elif mutation == 'wrong_codec': video['codec_name'] = 'png'
    else: audio['duration'] = '36.2'
    with pytest.raises(artifact.ExternalArtifactValidationError): _run(staged)


def test_v1_probe_call_and_descriptor_byte_shape_remain_unchanged(staged, monkeypatch):
    original = _run(staged)
    real_probe, calls = artifact._probe_mp4, []
    def legacy_probe(path):
        calls.append(path)
        return real_probe(path)
    monkeypatch.setattr(artifact, '_probe_mp4', legacy_probe)
    assert artifact._json(_run(staged)) == artifact._json(original)
    assert len(calls) == 1
    assert 'manifest_version' not in original and 'duration_ms' not in original


@pytest.mark.parametrize('duration', [35833, 15000, 60000])
def test_v1_still_rejects_non30_duration(staged, duration):
    _v2(staged, duration)
    staged.manifest['version'] = 1
    with pytest.raises(artifact.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('duration', [True, '35.833', float('nan'), float('inf'), 14.999, 60.001])
def test_probe_expected_duration_is_bounded_server_input(staged, duration):
    with pytest.raises(artifact.ExternalArtifactValidationError, match='expected_duration'):
        artifact._probe_mp4(staged.video, expected_duration=duration)
    assert not staged.calls


def _allow_tr(case):
    key = ingest.PROFILE_PREFIX + CHANNEL
    profile = json.loads(case.client.values[key]); profile['languages'] = ['tr']
    case.client.values[key] = json.dumps(profile)


def test_v2_ingest_stores_server_provenance_truthful_spec_and_remains_private(case):
    _v2(case.staged); _allow_tr(case)
    before = deepcopy(case.client.values)
    response = ingest_run(case)
    saved = job(case, response)
    assert saved['spec']['duration_minutes'] == 35833 / 60000 and saved['spec']['language'] == 'tr'
    assert saved['result']['duration'] == 35.833
    provenance = saved['result']['external_provenance']
    assert provenance['manifest_version'] == 2 and provenance['duration_ms'] == 35833
    assert provenance['manifest_sha256'] == ingest._digest(case.staged.manifest)
    assert saved['spec']['publish_after_render'] is saved['spec']['production_scheduled'] is False
    assert saved['result']['manual_qa_required'] is True and saved['result']['qa_approved'] is False
    assert saved['result']['audio_transcription_verified'] is saved['result']['new_media_generated'] is False
    assert all(case.client.values[key] == value for key, value in before.items())
    metadata = json.loads(case.storage.objects[saved['result']['metadata_key']]['bytes'])
    assert metadata['manifest'] == case.staged.manifest and metadata['manifest']['version'] == 2
    state, calls = deepcopy(case.client.values), list(case.storage.calls)
    assert ingest_run(case)['idempotent_replay'] is True
    assert case.client.values == state and case.storage.calls == calls


def test_v1_ingest_does_not_add_v2_fields(case):
    saved = job(case, ingest_run(case))
    assert saved['spec']['duration_minutes'] == .5
    assert not {'manifest_version', 'duration_ms'} & saved['result']['external_provenance'].keys()


def test_v2_language_binding_precedes_reservation_and_storage(case):
    _v2(case.staged)
    before = deepcopy(case.client.values)
    with pytest.raises(ingest.ExternalMasterIngestError, match='language_not_allowed'): ingest_run(case)
    assert case.client.values == before and not case.storage.calls


def test_v2_http_ingest_uses_same_authenticated_private_contract(case):
    _v2(case.staged); _allow_tr(case)
    response = post(case)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'imported_unreviewed'
    assert job(case, response.json())['result']['external_provenance']['duration_ms'] == 35833


def test_real_35833ms_tr_master_uses_1075_actual_frames_without_retiming(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local ffmpeg tools unavailable')
    video, captions = tmp_path / 'master.mp4', tmp_path / 'captions.tr.srt'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=navy:s=720x1280:r=30:d=35.833333333', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=44100:duration=35.833333333', '-t', '35.833333333',
        '-ac', '1', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-movflags', '+faststart', '-n', str(video)], check=True,
        capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    captions.write_bytes(b'placeholder')
    manifest = _manifest(video, captions)
    manifest.update(version=2, language='tr', duration_ms=35833)
    edges = [0, 6433, 12767, 18267, 22133, 27733, 35833]
    for i, row in enumerate(manifest['scenes']):
        row.update(start_ms=edges[i], end_ms=edges[i + 1], narration=f'Barkod öyküsünün {i + 1}. bölümü.')
    data = _captions(manifest['scenes']); captions.write_bytes(data)
    manifest['files']['captions'].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    before = video.read_bytes()
    result = artifact.validate_staged_external_artifact(tmp_path, video, captions, manifest)
    assert result['media_structure']['frame_count'] == 1075
    assert result['media_structure']['video_duration_seconds'] == pytest.approx(35.833333, abs=.000001)
    assert result['media_structure']['frame_rate'] == '30/1'
    assert result['media_structure']['audio_channels'] == 1
    assert video.read_bytes() == before and captions.read_bytes() == data
    assert result['qa_approved'] is False and result['audio_transcription_verified'] is False
