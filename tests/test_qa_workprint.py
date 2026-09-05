from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import audio_checkpoint, qa_workprint, visual_allocation_checkpoint


TASK = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'local-existing-video' * 80


@pytest.fixture
def case(tmp_path, monkeypatch):
    factory = tmp_path / 'youtube_factory'
    factory.mkdir()
    work = factory / f'{TASK}_attempt_0'
    work.mkdir()
    monkeypatch.setattr(visual_allocation_checkpoint, '_WORK_ROOT', factory)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    audio = tmp_path / f'{TASK}.mp3'
    audio.write_bytes(b'ID3' + b'existing-voice' * 150)
    scenes, visuals, reviews = [], [], {}
    for index in range(6):
        clip = work / f'scene{index}.mp4'
        clip.write_bytes(MP4 + bytes([index]))
        scenes.append({'narration': f'Sahne {index}: gerçek banknot görünür.', 'transition': 'cut',
                       'private': 'DO NOT EXPORT'})
        visuals.append([{'path': str(clip), 'source_type': 'stock', 'stock_provider': 'pexels',
                         'pexels_id': index + 100, 'start_fraction': 0.18,
                         'url': 'https://secret.test?token=DO NOT EXPORT'}])
        reviews[index] = {'score': 60 if index == 1 else 90, 'best_candidate_index': 0,
                          'best_start_fraction': 0.5, 'reason': 'The selected genuine banknote is visible.',
                          'subject_visible': True, 'evidence_gate_passed': index != 1,
                          'pass': True, 'headers': {'Authorization': 'DO NOT EXPORT'}}
    durations = [29.4 / 6] * 6
    args = dict(scenes=scenes, scene_visuals=visuals, final_reviews=reviews,
                voice_result={'path': str(audio), 'scene_durations': durations,
                              'authorization': 'DO NOT EXPORT'},
                scene_durations=durations, narration=' '.join(s['narration'] for s in scenes),
                options={'mode': 'production', 'format': 'shorts', 'secret': 'DO NOT EXPORT'},
                voice_quality_passed=True, target_seconds=30)
    renders, uploads, probes = [], [], []

    def render(**kwargs):
        renders.append(deepcopy(kwargs))
        Path(kwargs['output_path']).write_bytes(MP4 + b'rendered-master')
        return {'path': str(kwargs['output_path']), 'private': 'DO NOT EXPORT'}

    def put(**kwargs):
        content = kwargs['Body'].read()
        uploads.append({**kwargs, 'Body': content})
        return {'ETag': '"immutable-etag"', 'private': 'DO NOT EXPORT'}

    client = SimpleNamespace(put_object=put)
    monkeypatch.setattr(qa_workprint.storage, '_client', lambda: client)
    monkeypatch.setattr(qa_workprint, 'render_video', render)
    monkeypatch.setattr(qa_workprint, 'media_duration', lambda path: 29.4)
    monkeypatch.setattr(qa_workprint, '_probe', lambda path: probes.append(path))
    return SimpleNamespace(args=args, work=work, audio=audio, renders=renders,
                           uploads=uploads, probes=probes, client=client, tmp=tmp_path)


def persist(case, **changes):
    return qa_workprint.persist_qa_workprint(TASK, case.work, **{**case.args, **changes})


def metadata(case):
    return json.loads(case.uploads[0]['Body'])


def test_actual_six_scene_edit_is_private_unapproved_and_never_mutates_inputs(case):
    from app.services.qa_workprint_access import validated_pointer
    before = deepcopy(case.args)
    result = persist(case)
    pointer = result['qa_workprint']
    assert set(result) == {'qa_workprint'}
    assert pointer['status'] == 'qa_workprint'
    for field in ('qa_approved', 'publish_eligible', 'reusable'):
        assert pointer[field] is False
        assert metadata(case)[field] is False
    assert pointer['task_id'] == TASK
    assert validated_pointer({'task_id': TASK, 'state': 'FAILURE', 'kind': 'render',
                              'qa_workprint': pointer}) == pointer
    assert pointer['key'] == f'qa_workprints/{TASK}/{pointer["sha256"]}.mp4'
    assert pointer['metadata_key'] == f'qa_workprints/{TASK}/{pointer["metadata_sha256"]}.json'
    assert pointer['metadata_sha256'] == hashlib.sha256(case.uploads[0]['Body']).hexdigest()
    assert pointer['sha256'] == hashlib.sha256(case.uploads[1]['Body']).hexdigest()
    assert pointer['size'] == len(case.uploads[1]['Body'])
    assert (pointer['duration_seconds'], pointer['frame_count'], pointer['width'], pointer['height']) == (30, 900, 1080, 1920)
    assert case.args == before
    assert len(case.renders) == len(case.probes) == 1
    render = case.renders[0]
    assert render['target_duration'] == 30 and render['output_resolution'] == '1080x1920'
    assert render['voice_path'] == case.audio
    assert len(render['scene_visual_paths']) == 6
    assert all(len(pool) == 1 for pool in render['scene_visual_paths'])
    assert all(pool[0]['start_fraction'] == 0.5 for pool in render['scene_visual_paths'])
    assert metadata(case)['scenes'][1]['review']['score'] == 60
    assert metadata(case)['scenes'][1]['review']['gates']['evidence_gate_passed'] is False
    for upload in case.uploads:
        assert upload['IfNoneMatch'] == '*'
        assert upload['CacheControl'] == 'private, no-store'
        assert 'ACL' not in upload
        assert upload['Metadata']['sha256'] == hashlib.sha256(upload['Body']).hexdigest()
    serialized = json.dumps(metadata(case)) + json.dumps(pointer)
    for forbidden in ('DO NOT EXPORT', 'https://', str(case.work), 'Authorization', '"pass"', '"result"'):
        assert forbidden not in serialized
    assert not Path(render['output_path']).exists()
    assert case.audio.exists() and all(Path(pool[0]['path']).exists() for pool in case.args['scene_visuals'])


def test_exact_nonzero_best_candidate_and_collapsed_stale_index(case):
    extra = case.work / 'exact-best.mp4'
    extra.write_bytes(MP4 + b'exact-best')
    case.args['scene_visuals'][0].append({'path': str(extra), 'start_fraction': 0.1})
    case.args['final_reviews'][0]['best_candidate_index'] = 1
    case.args['final_reviews'][1]['best_candidate_index'] = 9
    assert persist(case)
    assert case.renders[0]['scene_visual_paths'][0][0]['path'] == str(extra)
    assert metadata(case)['scenes'][0]['selection']['selected_spec_index'] == 1
    assert metadata(case)['scenes'][1]['selection']['selected_spec_index'] == 0
    assert metadata(case)['scenes'][1]['review']['best_candidate_index'] == 9


def test_generated_clip_is_never_reselected_or_looped(case):
    spec = case.args['scene_visuals'][3][0]
    spec.update(source_type='generated', generation_provider='gemini_veo', forbid_loop=True,
                preserve_start_fraction=True, start_fraction=0.0)
    assert persist(case)
    chosen = case.renders[0]['scene_visual_paths'][3][0]
    assert chosen['start_fraction'] == 0 and chosen['forbid_loop'] is True
    assert metadata(case)['scenes'][3]['selection']['generation_provider'] == 'gemini_veo'


@pytest.mark.parametrize('field,value', [
    ('voice_quality_passed', False), ('voice_quality_passed', 1),
    ('target_seconds', 29.9), ('target_seconds', True), ('target_seconds', float('nan')),
    ('options', {'mode': 'preview', 'format': 'shorts'}),
    ('options', {'mode': 'production', 'format': 'landscape'}),
    ('scenes', []), ('scenes', [{}] * 13), ('final_reviews', {}),
    ('final_reviews', []), ('scene_durations', [30]), ('narration', 'Different script'),
])
def test_invalid_input_never_renders_or_uploads(case, field, value):
    assert persist(case, **{field: value}) == {}
    assert not case.renders and not case.uploads


@pytest.mark.parametrize('index', [None, True, -1, 2, '1'])
def test_ambiguous_multi_candidate_never_defaults_to_first(case, index):
    case.args['scene_visuals'][0] *= 2
    case.args['final_reviews'][0]['best_candidate_index'] = index
    assert persist(case) == {}
    assert not case.renders and not case.uploads


@pytest.mark.parametrize('fraction', [None, True, -0.01, 1.01, float('inf')])
def test_invalid_selected_fraction_is_not_repaired_by_diagnostic(case, fraction):
    case.args['final_reviews'][0]['best_start_fraction'] = fraction
    assert persist(case) == {}
    assert not case.renders


@pytest.mark.parametrize('kind', ['foreign_clip', 'foreign_voice', 'foreign_attempt', 'remote', 'relative', 'not_mp4'])
def test_same_task_local_path_binding(case, kind):
    if kind == 'foreign_voice':
        path = case.tmp / f'{OTHER}.mp3'
        path.write_bytes(case.audio.read_bytes())
        case.args['voice_result']['path'] = str(path)
    elif kind == 'foreign_attempt':
        folder = case.work.parent / f'{TASK}_attempt_1'
        folder.mkdir()
        path = folder / 'recovered_voice.mp3'
        path.write_bytes(case.audio.read_bytes())
        case.args['voice_result']['path'] = str(path)
    else:
        path = case.tmp / 'outside.mp4'
        path.write_bytes(MP4)
        value = str(path)
        if kind == 'remote':
            value = 'https://secret.test/clip.mp4'
        elif kind == 'relative':
            value = 'scene0.mp4'
        elif kind == 'not_mp4':
            path = case.work / 'invalid.mp4'
            path.write_bytes(b'not-a-media-container' * 50)
            value = str(path)
        case.args['scene_visuals'][0][0]['path'] = value
    assert persist(case) == {}
    assert not case.renders and not case.uploads


def test_exact_attempt_recovered_voice_is_supported(case):
    path = case.work / 'recovered_voice.mp3'
    path.write_bytes(case.audio.read_bytes())
    case.args['voice_result']['path'] = str(path)
    assert persist(case)
    assert case.renders[0]['voice_path'] == path


@pytest.mark.parametrize('duration', [27, 30.2, float('nan'), float('inf')])
def test_measured_voice_must_match_bounded_already_passed_timeline(case, monkeypatch, duration):
    monkeypatch.setattr(qa_workprint, 'media_duration', lambda path: duration)
    assert persist(case) == {}
    assert not case.renders


def test_voice_timeline_disagreement_fails_closed(case):
    case.args['scene_durations'] = [4.8] * 6
    assert persist(case) == {}
    assert not case.renders


def test_mislabeled_audio_playlist_never_reaches_probe_or_renderer(case, monkeypatch):
    case.audio.write_bytes(b'#EXTM3U\nhttps://private.test/remote\n' * 100)
    calls = []
    monkeypatch.setattr(qa_workprint, 'media_duration', lambda path: calls.append(path))
    assert persist(case) == {}
    assert not calls and not case.renders


@pytest.mark.parametrize('target', ['MAX_VIDEO_BYTES', 'MAX_SOURCE_BYTES', 'MAX_METADATA_BYTES'])
def test_size_limits_fail_without_publishing_pointer(case, monkeypatch, target):
    monkeypatch.setattr(qa_workprint, target, 100)
    assert persist(case) == {}
    assert not case.uploads


def test_sensitive_review_fields_are_redacted_not_exported(case):
    case.args['final_reviews'][0]['reason'] = 'https://private.test?X-Amz-Signature=SECRET'
    case.args['final_reviews'][1]['retry_queries'] = ['Bearer abcdef', 'linen fabric']
    assert persist(case)
    assert metadata(case)['scenes'][0]['review']['reason'] == '[redacted]'
    assert metadata(case)['scenes'][1]['review']['retry_queries'] == ['[redacted]', 'linen fabric']
    assert 'SECRET' not in json.dumps(metadata(case))


@pytest.mark.parametrize('point', ['render', 'probe', 'storage'])
def test_diagnostic_failure_is_best_effort_and_secret_free(case, monkeypatch, point):
    def fail(*args, **kwargs):
        raise RuntimeError('secret URL https://signed.test/?token=SECRET')
    if point == 'render':
        monkeypatch.setattr(qa_workprint, 'render_video', fail)
    elif point == 'probe':
        monkeypatch.setattr(qa_workprint, '_probe', fail)
    else:
        case.client.put_object = fail
    assert persist(case) == {}
    assert not list(case.work.glob('qa_workprint_*'))


def test_invalid_storage_etag_cannot_become_pointer(case):
    case.client.put_object = lambda **kwargs: {'ETag': 'https://signed.test?SECRET'}
    assert persist(case) == {}


@pytest.mark.parametrize('matches', [True, False])
def test_existing_content_addressed_object_is_never_overwritten(case, matches):
    def conflict(**kwargs):
        case.uploads.append(kwargs)
        error = RuntimeError('private provider response')
        error.response = {'Error': {'Code': 'PreconditionFailed'}}
        raise error

    def head(**kwargs):
        last = case.uploads[-1]
        return {'ContentLength': last['ContentLength'], 'ContentType': last['ContentType'],
                'Metadata': {'sha256': last['Metadata']['sha256'] if matches else 'f' * 64},
                'ETag': '"existing-etag"'}
    case.client.put_object, case.client.head_object = conflict, head
    result = persist(case)
    assert bool(result) is matches
    assert all(call['IfNoneMatch'] == '*' for call in case.uploads)


def _probe_payload():
    return {'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920,
                         'nb_read_frames': '900', 'avg_frame_rate': '30/1'},
                        {'codec_type': 'audio'}], 'format': {'duration': '30.000'}}


@pytest.mark.parametrize('mutation', [None, 'frames', 'landscape', 'silent', 'duration', 'nan', 'fps'])
def test_actual_master_probe_requires_900_portrait_frames_with_audio(monkeypatch, mutation):
    payload = _probe_payload()
    if mutation == 'frames':
        payload['streams'][0]['nb_read_frames'] = '560'
    elif mutation == 'landscape':
        payload['streams'][0].update(width=1920, height=1080)
    elif mutation == 'silent':
        payload['streams'].pop()
    elif mutation in ('duration', 'nan'):
        payload['format']['duration'] = '18.66' if mutation == 'duration' else 'NaN'
    elif mutation == 'fps':
        payload['streams'][0]['avg_frame_rate'] = '24/1'
    calls = []

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return json.dumps(payload)
    monkeypatch.setattr(qa_workprint.subprocess, 'check_output', probe)
    if mutation:
        with pytest.raises(ValueError):
            qa_workprint._probe(Path('fixture.mp4'))
    else:
        qa_workprint._probe(Path('fixture.mp4'))
    assert calls[0][0][calls[0][0].index('-protocol_whitelist') + 1] == 'file,pipe'
    assert calls[0][1]['timeout'] == 60
