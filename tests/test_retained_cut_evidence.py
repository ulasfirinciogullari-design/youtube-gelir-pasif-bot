"""Local FFmpeg derivation, immutable private storage and opt-in ordering only."""
from copy import copy, deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import socket
import subprocess
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retained_cut_evidence as cuts
from app.services import preserved_visual_recovery as recovery, render
from app.services import abacus_router_review_runtime as runtime


SOURCE = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
BUCKET, ENDPOINT = 'private-fixture', 'https://t3.storageapi.dev'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', Mock(side_effect=AssertionError('network forbidden')))
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('network forbidden')))


class FakeS3:
    def __init__(self):
        self.meta = SimpleNamespace(endpoint_url=ENDPOINT,
                                    config=SimpleNamespace(retries={'total_max_attempts': 1}))
        self.objects, self.puts, self.reads = {}, [], []
        self.after_put = self.damage_read = None
        self.extra_grants = []

    def put_object(self, **kw):
        self.puts.append(kw)
        assert kw['Bucket'] == BUCKET and kw['ACL'] == 'private' and kw['IfNoneMatch'] == '*'
        assert kw['ContentLength'] == len(kw['Body']) and kw['CacheControl'] == 'private, no-store'
        assert kw['Metadata'] == {'sha256': cuts._sha(kw['Body'])}
        assert kw['Key'] not in self.objects
        self.objects[kw['Key']] = (kw['Body'], kw['ContentType'])
        response = {'ResponseMetadata': {'HTTPStatusCode': 200}}
        if self.after_put:
            self.after_put(kw, response)
        return response

    def get_object(self, *, Bucket, Key):
        assert Bucket == BUCKET
        self.reads.append(Key)
        data, mime = self.objects[Key]
        response = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'ContentLength': len(data),
                    'ContentType': mime, 'Body': BytesIO(data)}
        if self.damage_read:
            self.damage_read(response)
        return response

    def get_object_acl(self, **kw):
        return {'ResponseMetadata': {'HTTPStatusCode': 200}, 'Owner': {'ID': 'owner'}, 'Grants': [
            {'Grantee': {'Type': 'CanonicalUser', 'ID': 'owner'}, 'Permission': 'FULL_CONTROL'},
            *deepcopy(self.extra_grants)]}


def material(work, clip, audio):
    work.mkdir()
    paths = []
    for index in range(6):
        path = work / f'raw-{index}.mp4'
        shutil.copyfile(clip, path)
        paths.append((path, 'runway'))
    voice_path = work / 'original.mp3'
    shutil.copyfile(audio, voice_path)
    measured = render.media_duration.__wrapped__(str(voice_path))
    package = {'scenes': [{'narration': f'Synthetic scene {i}.', 'transition': 'cut'} for i in range(6)],
               'studio_options': {'content_style': 'documentary'}}
    voice = {'path': str(voice_path), 'duration_after_fit': measured,
             'scene_durations': [124/30, 149/30, 5.0, 5.0, 5.0, 5.0]}
    source = {'source_task_id': SOURCE, 'source_state_sha256': '1'*64,
              'source_spec_sha256': '2'*64, 'source_journal_sha256': '3'*64,
              'source_metadata_sha256': '4'*64, 'audio': cuts._file(voice_path)[0]}
    raw = [{**cuts._file(path)[0], 'provider': provider} for path, provider in paths]
    return SimpleNamespace(work=work, package=package, voice=voice, paths=paths, source=source, raw=raw)


def prepare(box):
    return cuts.prepare_retained_cuts(box.package, box.voice, box.paths, box.work,
                                     source_binding=box.source, raw_bindings=box.raw)


@pytest.fixture(scope='module')
def real_media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is required for real local derivation evidence')
    work = tmp_path_factory.mktemp('actual-retained-cuts')
    clip, audio = work / 'raw.mp4', work / 'voice.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=0x6699cc:s=128x72:r=30:d=5.8', '-c:v', 'libx264', '-threads', '2',
        '-pix_fmt', 'yuv420p', '-an', str(clip)], check=True, timeout=30)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=22050:duration=29.1', '-c:a', 'libmp3lame',
        str(audio)], check=True, timeout=30)
    box = material(work / 'real-preparation', clip, audio)
    artifact = prepare(box)
    return SimpleNamespace(clip=clip, audio=audio, box=box, artifact=artifact,
                           data=[Path(specs[0]['path']).read_bytes() for specs in artifact.inputs])


@pytest.fixture
def box(real_media, tmp_path, monkeypatch):
    box = material(tmp_path / 'preparation', real_media.clip, real_media.audio)
    # Storage/failure tests replay local fixture bytes through the real helper;
    # only the real_media fixture above establishes actual renderer behavior.
    rows = real_media.artifact.record['cuts']
    def recorded_normalization(spec, target, duration, index, transition, resolution, *, _recipe_recorder):
        Path(target).write_bytes(real_media.data[index])
        recipe = deepcopy(rows[index]['recipe'])
        for attempt in recipe['attempts']:
            attempt.pop('executed_argv_sha256')
            attempt['argv'] = [spec['path'] if arg == '$input' else str(target) if arg == '$output'
                               else arg for arg in attempt['argv']]
        _recipe_recorder(recipe)
        return str(target)
    monkeypatch.setattr(render, 'normalize_clip', recorded_normalization)
    monkeypatch.setattr(cuts.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    box.s3 = FakeS3()
    return box


def test_real_six_cut_bytes_frames_and_actual_recipe_build_are_preserved(real_media, monkeypatch):
    monkeypatch.setattr(cuts.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    artifact, box, s3 = real_media.artifact, real_media.box, FakeS3()
    record = artifact.record
    assert record['source'] == box.source and record['package_sha256'] == cuts._sha(cuts._raw(box.package))
    assert len(record['cuts']) == 6 and sum(artifact.frame_counts) == round(record['measured_voice_duration'] * 30)
    for index, (row, specs) in enumerate(zip(record['cuts'], artifact.inputs)):
        assert row['scene_index'] == index and row['candidate_index'] == 0
        assert row['cut'] == cuts._file(specs[0]['path'])[0]
        assert row['actual_frames'] == row['target_frames'] == render.video_frame_count(specs[0]['path'])
        command = row['recipe']['attempts'][-1]['argv']
        assert command[command.index('-frames:v') + 1] == str(row['actual_frames'])
        assert '$input' in command and command[-1] == '$output'
        assert row['recipe']['forbid_loop'] is True and '-stream_loop' not in command
        assert Path(specs[0]['path']).stat().st_mode & 0o777 == 0o600
    assert set(record['build']['tools']) == {'ffmpeg', 'ffprobe'}
    assert all(len(value['version_sha256']) == 64 for value in record['build']['tools'].values())
    receipt = cuts.persist_retained_cuts(artifact, s3, bucket=BUCKET)
    manifest = json.loads(s3.objects[receipt['manifest']['key']][0])
    assert len(s3.puts) == len(s3.reads) == 7 and len(manifest['objects']) == 6
    assert manifest['cuts'] == record['cuts'] and manifest['build'] == record['build']
    assert receipt['storage_readback_verified'] and not receipt['admission_anchor_created']
    assert receipt['diagnostic_only'] and not any(receipt[name] for name in cuts._FLAGS if name != 'diagnostic_only')
    assert not any('jpeg' in key for key in manifest)
    detached = artifact.record
    detached['cuts'][0]['cut']['sha256'] = 'f'*64
    assert artifact.record == record
    assert 'raw-' not in repr(artifact)


@pytest.mark.parametrize('measurements,crop', [([0.0], None), ([0.5, 0.0], None),
                                               ([0.5, 0.5, 0.0], (640, 40))])
def test_observation_keeps_existing_commands_and_records_successful_crop_branch(monkeypatch, tmp_path, measurements, crop):
    spec = {'path': str(tmp_path / 'raw.mp4'), 'forbid_loop': True}
    output = tmp_path / 'normalized.mp4'
    monkeypatch.setattr(render, 'media_duration', lambda path: 6.0)
    monkeypatch.setattr(render, 'video_frame_count', lambda path: 124)
    monkeypatch.setattr(render, 'detect_symmetric_letterbox_crop', lambda *a, **kw: crop)
    actual, observed = [], []
    monkeypatch.setattr(render, '_run', lambda cmd: actual.append(deepcopy(cmd)))
    monkeypatch.setattr(render, 'max_horizontal_letterbox_duration', Mock(side_effect=measurements))
    render.normalize_clip(spec, output, 124/30, 0, output_resolution='1080x1920')
    ordinary = deepcopy(actual)
    actual.clear()
    monkeypatch.setattr(render, 'max_horizontal_letterbox_duration', Mock(side_effect=measurements))
    render.normalize_clip(spec, output, 124/30, 0, output_resolution='1080x1920', _recipe_recorder=observed.append)
    assert actual == ordinary and len(observed) == 1
    assert [row['argv'] for row in observed[0]['attempts']] == actual
    assert observed[0]['letterbox_measurements'] == measurements
    assert observed[0]['attempts'][-1]['source_crop'] == (list(crop) if crop else None)
    assert observed[0]['target_frames'] == 124


def test_failed_frame_gate_never_emits_success_recipe(monkeypatch, tmp_path):
    monkeypatch.setattr(render, 'media_duration', lambda path: 6.0)
    monkeypatch.setattr(render, '_run', lambda cmd: None)
    monkeypatch.setattr(render, 'video_frame_count', lambda path: 123)
    callback = Mock()
    with pytest.raises(RuntimeError, match='frame gate'):
        render.normalize_clip('fixture.mp4', tmp_path / 'cut.mp4', 124/30, 0, _recipe_recorder=callback)
    callback.assert_not_called()


@pytest.mark.parametrize('damage', ['source', 'audio', 'cut', 'build'])
def test_changed_bytes_or_build_fail_before_any_storage_write(box, monkeypatch, damage):
    artifact = prepare(box)
    if damage == 'source': box.paths[0][0].write_bytes(b'changed original')
    elif damage == 'audio': Path(box.voice['path']).write_bytes(b'changed audio')
    elif damage == 'cut': Path(artifact.inputs[0][0]['path']).write_bytes(b'changed cut')
    else: monkeypatch.setattr(cuts, '_build_identity', lambda: {'changed': True})
    with pytest.raises(cuts.RetainedCutEvidenceError, match='^retained_cut_evidence_unverified$'):
        cuts.persist_retained_cuts(artifact, box.s3, bucket=BUCKET)
    assert box.s3.puts == []


@pytest.mark.parametrize('damage', ['lost_ack', 'bad_status', 'readback', 'public_acl', 'retry', 'http'])
def test_storage_uncertainty_is_terminal_and_never_creates_manifest_or_retries(box, monkeypatch, damage):
    artifact = prepare(box)
    if damage == 'lost_ack':
        box.s3.after_put = lambda *args: (_ for _ in ()).throw(RuntimeError('private backend secret'))
    elif damage == 'bad_status': box.s3.after_put = lambda kw, response: response['ResponseMetadata'].update(HTTPStatusCode=True)
    elif damage == 'readback': box.s3.damage_read = lambda response: response.update(Body=BytesIO(b'wrong data'))
    elif damage == 'public_acl': box.s3.extra_grants = [{'Grantee': {'Type': 'Group',
        'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}]
    elif damage == 'retry': box.s3.meta.config.retries = {'total_max_attempts': 2}
    else:
        box.s3.meta.endpoint_url = 'http://fixture.invalid'
        monkeypatch.setattr(cuts.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint='http://fixture.invalid'))
    for attempt in range(2):
        with pytest.raises(cuts.RetainedCutEvidenceError, match='^retained_cut_evidence_unverified$'):
            cuts.persist_retained_cuts(artifact, box.s3, bucket=BUCKET)
    assert len(box.s3.puts) == (0 if damage in {'retry', 'http'} else 1)
    assert not any('/manifests/' in key for key in box.s3.objects)


def test_typed_diagnostic_rejects_forged_copy_and_cross_thread_access(box):
    artifact = prepare(box)
    with pytest.raises(TypeError): cuts.PreparedRetainedCuts()
    with pytest.raises(TypeError): copy(artifact)
    with pytest.raises(cuts.RetainedCutEvidenceError):
        cuts.persist_retained_cuts(object.__new__(cuts.PreparedRetainedCuts), box.s3, bucket=BUCKET)
    outcomes = []
    def other_thread():
        try: outcomes.append(artifact.record)
        except cuts.RetainedCutEvidenceError: outcomes.append('rejected')
    thread = Thread(target=other_thread)
    thread.start(); thread.join()
    assert outcomes == ['rejected'] and box.s3.puts == []


@pytest.mark.parametrize('fail_storage', [False, True])
def test_opt_in_storage_finishes_before_reviewer_and_failure_never_calls_it(box, monkeypatch, fail_storage):
    monkeypatch.setattr(recovery, '_state', lambda *args: ({}, box.source['source_state_sha256']))
    monkeypatch.setattr(recovery.studio_state, '_client', lambda: object())
    monkeypatch.setattr(recovery.storage, '_client', lambda **kw: box.s3)
    def reviewer(*args, **kwargs):
        assert len(box.s3.objects) == 7 and any('/manifests/' in key for key in box.s3.objects)
        assert context['audit']['retained_cut_evidence']['storage_readback_verified']
        return {'reviews': []}
    review = Mock(side_effect=reviewer)
    monkeypatch.setattr(recovery, '_review_runtime', lambda: (render, review))
    context = {'source': box.source, 'raw_bindings': box.raw, 'audit': {}}
    if fail_storage:
        box.s3.after_put = lambda *args: (_ for _ in ()).throw(RuntimeError('private failure'))
        with pytest.raises(cuts.RetainedCutEvidenceError):
            recovery._exact_review(box.package, box.voice, box.paths, box.work, 'fixture', cut_context=context)
        review.assert_not_called()
        assert context['audit'] == {}
    else:
        result, counts = recovery._exact_review(box.package, box.voice, box.paths, box.work,
                                                'fixture', cut_context=context)
        assert result == {'reviews': []} and len(counts) == 6
        review.assert_called_once()


@pytest.mark.parametrize('options', [dict(completion_plan=object()),
    dict(completion_plan=object(), preserve_exact_cuts=True),
    dict(completion_plan=object(), capture_transport=True), dict(preserve_exact_cuts=1),
    dict(capture_transport=1)])
def test_completion_wrapper_requires_both_explicit_opt_ins_before_scope_or_story(monkeypatch, options):
    scope, prepare_call = Mock(), Mock()
    monkeypatch.setattr(runtime, 'retained_router_review_scope', scope)
    monkeypatch.setattr(recovery, 'prepare_preserved_visual_recovery', prepare_call)
    with pytest.raises(ValueError):
        recovery.prepare_subscription_router_recovery(SOURCE, '/unused', **options)
    scope.assert_not_called(); prepare_call.assert_not_called()
