"""Generated/mocked staged files only; no real downloaded artifact or provider."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.services import external_artifact_import as importer


def _captions(scenes):
    def stamp(ms):
        return f'00:00:{ms // 1000:02d},{ms % 1000:03d}'
    return '\n\n'.join(f'{i + 1}\n{stamp(s["start_ms"])} --> {stamp(s["end_ms"])}\n{s["narration"]}'
                        for i, s in enumerate(scenes)).encode('utf-8') + b'\n'


def _manifest(video, captions):
    scenes = [{'index': i, 'start_ms': i * 5000, 'end_ms': (i + 1) * 5000,
               'narration': f'This is the exact narration for scene {i + 1}.',
               'visual_intent': f'An explanatory diagram illustrates the declared business step {i + 1}.'} for i in range(6)]
    return {'version': 1, 'origin': {'kind': 'external_master', 'provider': 'abacus',
                                   'model': None, 'voice_provider': None, 'voice_model': None},
            'language': 'en', 'format': 'shorts', 'duration_ms': 30000, 'title': 'How the change happened',
            'scenes': scenes,
            'sources': [{'url': 'https://www.gs1.org/standards/barcodes',
                         'evidence': 'This public reference describes the relevant barcode standard.'},
                        {'url': 'https://www.ikea.com/global/en/our-business/how-we-work/',
                         'evidence': 'This public reference describes the relevant business practice.'}],
            'files': {key: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'size': path.stat().st_size, 'content_type': mime}
                      for key, path, mime in [('video', video, 'video/mp4'),
                                              ('captions', captions, 'application/x-subrip')]}}


def _probe(size):
    return {'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'width': 1080, 'height': 1920,
         'pix_fmt': 'yuv420p', 'avg_frame_rate': '30/1', 'r_frame_rate': '30/1', 'duration': '30.000000',
         'nb_read_frames': '900', 'disposition': {'attached_pic': 0}, 'tags': {}},
        {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac', 'duration': '30.000000',
         'sample_rate': '48000', 'channels': 2}],
        'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2', 'duration': '30.000000', 'size': str(size)}}


@pytest.fixture
def staged(tmp_path, monkeypatch):
    video, captions = tmp_path / 'master.mp4', tmp_path / 'captions.en.srt'
    video.write_bytes((32).to_bytes(4, 'big') + b'ftypisom' + b'\0' * 20 + b'fixture-media' * 1000)
    captions.write_bytes(b'placeholder')
    manifest = _manifest(video, captions)
    captions.write_bytes(_captions(manifest['scenes']))
    manifest = _manifest(video, captions)
    data, calls = _probe(video.stat().st_size), []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b'')
    monkeypatch.setattr(importer.subprocess, 'run', run)
    return SimpleNamespace(root=tmp_path, video=video, captions=captions, manifest=manifest, probe=data, calls=calls)


def _run(c):
    return importer.validate_staged_external_artifact(c.root, c.video, c.captions, c.manifest)


def _sync_caption_declaration(c, value):
    c.captions.write_bytes(value)
    c.manifest['files']['captions'].update(sha256=hashlib.sha256(value).hexdigest(), size=len(value))


def test_descriptor_is_hash_bound_unapproved_and_never_publishing_authority(staged):
    c = staged
    before = {path: path.read_bytes() for path in (c.video, c.captions)}
    manifest = deepcopy(c.manifest)
    result = _run(c)
    assert result['status'] == 'unapproved_external_candidate'
    assert result['qa_approved'] is result['publish_eligible'] is result['media_generation_authorized'] is False
    assert result['source_evidence_verified'] is result['audio_transcription_verified'] is False
    assert all(result[k] is True for k in ('requires_fresh_story_qa', 'requires_fresh_audio_qa',
                                         'requires_fresh_visual_qa', 'requires_current_episode_and_oauth_binding'))
    assert result['manifest'] == manifest and c.manifest == manifest
    assert result['manifest']['origin']['voice_model'] is None
    assert result['provenance_status'] == 'caller_declared_unverified'
    assert result['video_sha256'] == manifest['files']['video']['sha256']
    assert result['files']['video']['local_path'] == str(c.video.resolve())
    assert result['caption_contract']['cue_count'] == 6
    assert result['media_structure']['frame_count'] == 900
    assert not {'video_key', 'quality_disposition', 'state', 'youtube', 'publish_plan'} & result.keys()
    assert {path: path.read_bytes() for path in before} == before
    assert _run(c) == result


def test_probe_disables_external_references_and_uses_bounded_non_shell_command(staged):
    _run(staged)
    command, kwargs = staged.calls[0]
    assert command[command.index('-f') + 1] == 'mov'
    assert command[command.index('-protocol_whitelist') + 1] == 'file'
    assert command[command.index('-enable_drefs') + 1] == '0'
    assert command[command.index('-use_absolute_path') + 1] == '0'
    assert command[-1] == str(staged.video.resolve()) and '-count_frames' in command
    assert kwargs['timeout'] == 45 and not kwargs.get('shell')


@pytest.mark.parametrize('value', ['{"api_key": "sensitive-value"}',
    "{'password' : 'multiple secret words'}", '{"Authorization": "Basic opaque"}'])
def test_quoted_credential_assignments_are_rejected_before_the_descriptor_can_echo_them(staged, value):
    staged.manifest['origin']['model'] = value
    with pytest.raises(importer.ExternalArtifactValidationError, match='external_text_invalid'):
        _run(staged)
    assert not staged.calls


def test_fstat_lstat_ctime_semantics_may_differ_but_same_api_identity_is_preserved(staged, monkeypatch):
    real_fstat = importer.os.fstat
    def fstat(descriptor):
        actual = real_fstat(descriptor)
        return SimpleNamespace(**{name: getattr(actual, name) + (123456 if name == 'st_ctime_ns' else 0)
                                  for name in ('st_mode', 'st_nlink', 'st_dev', 'st_ino', 'st_size',
                                               'st_mtime_ns', 'st_ctime_ns')})
    monkeypatch.setattr(importer.os, 'fstat', fstat)
    assert _run(staged)['qa_approved'] is False


def test_fstat_ctime_change_during_hash_still_rejects(staged, monkeypatch):
    real_fstat, calls = importer.os.fstat, []
    def fstat(descriptor):
        actual = real_fstat(descriptor)
        calls.append(descriptor)
        return SimpleNamespace(**{name: getattr(actual, name) + (len(calls) if name == 'st_ctime_ns' else 0)
                                  for name in ('st_mode', 'st_nlink', 'st_dev', 'st_ino', 'st_size',
                                               'st_mtime_ns', 'st_ctime_ns')})
    monkeypatch.setattr(importer.os, 'fstat', fstat)
    with pytest.raises(importer.ExternalArtifactValidationError, match='external_file_hash_changed'):
        _run(staged)


@pytest.mark.parametrize('field,value', [('qa_approved', True), ('publish_eligible', True),
    ('quality_disposition', 'automated_qc_pass'), ('state', 'SUCCESS'), ('version', True),
    ('version', 3), ('language', 'xx'), ('format', 'landscape'), ('duration_ms', 30000.0),
    ('duration_ms', 31000), ('title', 'secret=do-not-return-this'), ('title', '<script>alert(1)</script>')])
def test_caller_flags_unknown_schema_or_secret_text_are_rejected_before_probe(staged, field, value):
    staged.manifest[field] = value
    with pytest.raises(importer.ExternalArtifactValidationError):
        _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('origin', [
    {'kind': 'internal_render', 'provider': 'abacus', 'model': None, 'voice_provider': None, 'voice_model': None},
    {'kind': 'external_master', 'provider': 'invented', 'model': None, 'voice_provider': None, 'voice_model': None},
    {'kind': 'external_master', 'provider': 'abacus', 'model': None, 'voice_provider': None, 'voice_model': None, 'qa_approved': True},
    {'kind': 'external_master', 'provider': 'abacus', 'model': None},
])
def test_origin_does_not_accept_invented_observed_provenance_or_qa(staged, origin):
    staged.manifest['origin'] = origin
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)


def test_unknown_provider_and_explicitly_declared_model_remain_unverified(staged):
    staged.manifest['origin'].update(provider='unknown', model='caller-declared-model', voice_provider='caller-declared-voice')
    result = _run(staged)
    assert result['provenance_status'] == 'caller_declared_unverified'
    assert result['manifest']['origin']['provider'] == 'unknown' and result['qa_approved'] is False


@pytest.mark.parametrize('mutate', [
    lambda m: m['scenes'].pop(), lambda m: m['scenes'].append(deepcopy(m['scenes'][-1])),
    lambda m: m['scenes'][0].update(index=True), lambda m: m['scenes'][2].update(index=3),
    lambda m: m['scenes'][0].update(start_ms=1), lambda m: m['scenes'][1].update(start_ms=4999),
    lambda m: m['scenes'][1].update(start_ms=5001), lambda m: m['scenes'][-1].update(end_ms=29999),
    lambda m: m['scenes'][0].update(end_ms=5000.0), lambda m: m['scenes'][0].update(end_ms=float('nan')),
    lambda m: m['scenes'][0].update(narration=''), lambda m: m['scenes'][0].update(visual_intent=''),
    lambda m: m['scenes'][0].update(qa_approved=True), lambda m: m['scenes'][0].update(narration='A different narrated fact.'),
])
def test_exact_six_scene_timeline_and_narration_are_required(staged, mutate):
    mutate(staged.manifest)
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://localhost/private', 'https://127.0.0.1/a',
    'https://10.1.2.3/a', 'http://0x7f.0.0.1/a', 'http://2130706433/a', 'https://x.storageapi.dev/a',
    'https://user:password@example.com/a', 'https://www.gs1.org/a?token=x', 'https://www.gs1.org/a#secret',
    'https://host.internal/a', 'https://www.gs1.org:8080/a'])
def test_source_references_must_not_be_private_signed_or_asset_urls(staged, url):
    staged.manifest['sources'][0]['url'] = url
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('mutation', ['duplicate', 'one_source', 'empty_evidence', 'truncated_evidence', 'extra_field'])
def test_citation_shape_uses_existing_normalizer_without_silent_truncation(staged, mutation):
    sources = staged.manifest['sources']
    if mutation == 'duplicate': sources[1] = deepcopy(sources[0])
    elif mutation == 'one_source': sources.pop()
    elif mutation == 'empty_evidence': sources[0]['evidence'] = 'short'
    elif mutation == 'truncated_evidence': sources[0]['evidence'] = 'x' * 601
    else: sources[0]['verified'] = True
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)


@pytest.mark.parametrize('kind,field,value', [('video', 'sha256', '0' * 64), ('captions', 'sha256', '0' * 64),
    ('video', 'size', 1000), ('video', 'size', 128 * 1024 * 1024 + 1), ('video', 'size', True),
    ('captions', 'size', 65537), ('captions', 'size', 1), ('video', 'content_type', 'text/html'),
    ('captions', 'content_type', 'application/zip')])
def test_declared_hash_size_and_type_must_match_real_bounded_files(staged, kind, field, value):
    staged.manifest['files'][kind][field] = value
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


@pytest.mark.parametrize('mutation', ['non_utf8', 'overlap', 'reverse', 'index_gap', 'missing_scene', 'cross_scene',
    'beyond_end', 'word_missing', 'wrong_number', 'wrong_punctuation', 'html', 'ass_style', 'timestamp_extra', 'invalid_minute'])
def test_real_srt_coherence_not_just_filename_or_subtitle_existence(staged, mutation):
    raw = staged.captions.read_bytes()
    if mutation == 'non_utf8': raw = b'\xff\xfe' + raw
    elif mutation == 'overlap': raw = raw.replace(b'00:00:05,000 --> 00:00:10,000', b'00:00:04,900 --> 00:00:10,000')
    elif mutation == 'reverse': raw = raw.replace(b'00:00:00,000 --> 00:00:05,000', b'00:00:05,000 --> 00:00:00,000')
    elif mutation == 'index_gap': raw = raw.replace(b'\n\n2\n', b'\n\n3\n')
    elif mutation == 'missing_scene': raw = raw.rsplit(b'\n\n', 1)[0]
    elif mutation == 'cross_scene': raw = raw.replace(b'00:00:05,000\n', b'00:00:05,001\n', 1)
    elif mutation == 'beyond_end': raw = raw.replace(b'00:00:30,000', b'00:00:30,001')
    elif mutation == 'word_missing': raw = raw.replace(b'exact narration', b'narration', 1)
    elif mutation == 'wrong_number': raw = raw.replace(b'scene 1.', b'scene 2.', 1)
    elif mutation == 'wrong_punctuation': raw = raw.replace(b'scene 1.', b'scene 1!', 1)
    elif mutation == 'html': raw = raw.replace(b'This is', b'<i>This</i> is', 1)
    elif mutation == 'ass_style': raw = raw.replace(b'This is', b'{\\an8}This is', 1)
    elif mutation == 'timestamp_extra': raw = raw.replace(b'00:00:05,000\n', b'00:00:05,000 X1:1\n', 1)
    elif mutation == 'invalid_minute': raw = raw.replace(b'00:00:00,000', b'00:60:00,000', 1)
    _sync_caption_declaration(staged, raw)
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


def test_srt_bom_crlf_and_line_wrapping_preserve_exact_text(staged):
    raw = staged.captions.read_bytes().replace(b'exact narration', b'exact\nnarration').replace(b'\n', b'\r\n')
    _sync_caption_declaration(staged, b'\xef\xbb\xbf' + raw)
    assert _run(staged)['caption_contract']['cue_count'] == 6


def test_multiple_timed_cues_per_scene_are_matched_as_the_exact_full_narration(staged):
    cues = []
    for i, scene in enumerate(staged.manifest['scenes']):
        first, second = scene['narration'].split('exact ', 1)
        start = i * 5
        cues.extend([f'{i * 2 + 1}\n00:00:{start:02d},000 --> 00:00:{start + 2:02d},000\n{first.strip()}',
                     f'{i * 2 + 2}\n00:00:{start + 2:02d},250 --> 00:00:{start + 5:02d},000\nexact {second}'])
    _sync_caption_declaration(staged, ('\n\n'.join(cues) + '\n').encode())
    contract = _run(staged)['caption_contract']
    assert contract['cue_count'] == 12 and contract['audio_transcription_verified'] is False
    assert [cue['scene_index'] for cue in contract['cues']] == [i for i in range(6) for _ in range(2)]


@pytest.mark.parametrize('mutation', ['missing_audio', 'extra_stream', 'wrong_container', 'wrong_codec', 'pixel_format',
    'landscape', 'low_resolution', 'rotation', 'attached_pic', 'short', 'long', 'missing_frames', 'frame_mismatch',
    'invalid_fps', 'zero_fps', 'infinite_duration', 'wrong_size', 'no_audio_duration', 'bad_sample_rate', 'bad_channels'])
def test_ffprobe_must_confirm_real_supported_media_structure(staged, mutation):
    probe, video, audio = staged.probe, staged.probe['streams'][0], staged.probe['streams'][1]
    if mutation == 'missing_audio': probe['streams'].pop()
    elif mutation == 'extra_stream': probe['streams'].append({'codec_type': 'attachment'})
    elif mutation == 'wrong_container': probe['format']['format_name'] = 'hls'
    elif mutation == 'wrong_codec': video['codec_name'] = 'png'
    elif mutation == 'pixel_format': video['pix_fmt'] = 'rgba'
    elif mutation == 'landscape': video.update(width=1920, height=1080)
    elif mutation == 'low_resolution': video.update(width=360, height=640)
    elif mutation == 'rotation': video['side_data_list'] = [{'rotation': 90}]
    elif mutation == 'attached_pic': video['disposition']['attached_pic'] = 1
    elif mutation == 'short': video['duration'] = '28'
    elif mutation == 'long': probe['format']['duration'] = '35'
    elif mutation == 'missing_frames': video.pop('nb_read_frames')
    elif mutation == 'frame_mismatch': video['nb_read_frames'] = '8990'
    elif mutation == 'invalid_fps': video['avg_frame_rate'] = 'not-a-rate'
    elif mutation == 'zero_fps': video['avg_frame_rate'] = '0/0'
    elif mutation == 'infinite_duration': video['duration'] = 'inf'
    elif mutation == 'wrong_size': probe['format']['size'] = '1'
    elif mutation == 'no_audio_duration': audio['duration'] = '0'
    elif mutation == 'bad_sample_rate': audio['sample_rate'] = '8000'
    elif mutation == 'bad_channels': audio['channels'] = True
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)


@pytest.mark.parametrize('value', ['https://apps.abacus.ai/file.mp4', 'file:///tmp/master.mp4', '../master.mp4',
                                  '//server/share/master.mp4'])
def test_video_input_is_never_an_arbitrary_url_or_path_escape(staged, value):
    staged.video = value
    with pytest.raises(importer.ExternalArtifactValidationError): _run(staged)
    assert not staged.calls


def test_existing_file_outside_staging_is_not_read_or_probed(staged, tmp_path):
    outer = tmp_path.parent / (tmp_path.name + '-outside.mp4')
    outer.write_bytes(staged.video.read_bytes())
    staged.video = outer
    with pytest.raises(importer.ExternalArtifactValidationError, match='outside_staging'): _run(staged)
    assert not staged.calls


def test_symlink_and_hardlink_are_not_uploaded_regular_files(staged):
    linked = staged.root / 'linked.mp4'
    try:
        os.link(staged.video, linked)
    except OSError:
        pytest.skip('Hardlinks unavailable on test filesystem')
    staged.video = linked
    with pytest.raises(importer.ExternalArtifactValidationError, match='regular_file'): _run(staged)


def test_probe_failure_never_leaks_stderr_or_full_paths(staged, monkeypatch):
    secret = 'private endpoint API_KEY=never-surface-this'
    monkeypatch.setattr(importer.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=b'', stderr=secret.encode()))
    with pytest.raises(importer.ExternalArtifactValidationError) as error: _run(staged)
    assert str(error.value) == 'external_probe_failed' and secret not in str(error.value)


def test_bytes_changed_while_probe_runs_are_rejected(staged, monkeypatch):
    def changed(*args, **kwargs):
        data = staged.video.read_bytes()
        staged.video.write_bytes(data[:-1] + b'X')
        return SimpleNamespace(returncode=0, stdout=json.dumps(staged.probe).encode())
    monkeypatch.setattr(importer.subprocess, 'run', changed)
    with pytest.raises(importer.ExternalArtifactValidationError, match='hash_changed'): _run(staged)


def test_same_bytes_replaced_during_probe_do_not_certify_a_different_file_identity(staged, monkeypatch):
    def changed(*args, **kwargs):
        replacement = staged.root / 'replacement.mp4'
        replacement.write_bytes(staged.video.read_bytes())
        replacement.replace(staged.video)
        return SimpleNamespace(returncode=0, stdout=json.dumps(staged.probe).encode())
    monkeypatch.setattr(importer.subprocess, 'run', changed)
    with pytest.raises(importer.ExternalArtifactValidationError, match='file_changed'): _run(staged)


def test_duplicate_json_keys_are_not_overwritten(staged):
    raw = json.dumps(staged.manifest)
    raw = raw.replace('"version": 1', '"version": 0, "version": 1', 1)
    staged.manifest = raw
    with pytest.raises(importer.ExternalArtifactValidationError, match='duplicate_field'): _run(staged)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg unavailable')
def test_actual_generated_mp4_probe_does_not_claim_visual_or_audio_quality(tmp_path):
    video, captions = tmp_path / 'generated.mp4', tmp_path / 'generated.srt'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=720x1280:r=24:d=30',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=30', '-t', '30',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)],
                   check=True, capture_output=True, timeout=60)
    captions.write_bytes(b'placeholder')
    manifest = _manifest(video, captions)
    captions.write_bytes(_captions(manifest['scenes']))
    manifest = _manifest(video, captions)
    result = importer.validate_staged_external_artifact(tmp_path, video, captions, manifest)
    assert result['media_structure']['frame_count'] == 720
    assert result['qa_approved'] is result['audio_transcription_verified'] is False
    assert result['requires_fresh_visual_qa'] is True  # Static color is structural input, NOT an approved video.
