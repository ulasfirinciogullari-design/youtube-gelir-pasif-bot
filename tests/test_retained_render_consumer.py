"""Real local assembly from disposable source/review records, never live QA.

Moving synthetic footage is created before any source or review commitment.
Provider replies remain synthetic; real final probes establish only local
frame/timing/tail/motion behavior, not the semantic truth of those replies.
"""
from copy import copy, deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retained_render_consumer as consumer
from app.services import render, retained_cut_evidence as cuts
from app.services import retained_review_captured_story_continuation as continuation
from app.services import abacus_router_review_runtime as text_runtime
from app.services import abacus_router_audio_review_runtime as audio_runtime
from app.services import abacus_router_review_journal as text_journal
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import production_connection_continuity as continuity
from test_retained_captured_visual_scope import (
    completed_captured_audio, audio_case, produced_captured_visual, qualified, captured,
    source, case, planning_case, prepared, frozen_three, completed_probe, wire,
    forbid_live_transport, BUCKET,
)
from test_retained_cut_evidence import material, prepare
from test_production_short_effective_duration import helpers
from test_retained_review_captured_story_continuation import restore
from test_production_connection_continuity import _dump

REAL_RUN = render._run


@pytest.fixture(scope='module')
def real_media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for actual retained render checks')
    work = tmp_path_factory.mktemp('moving-retained-render-source')
    clip, audio = work / 'moving.mp4', work / 'voice.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=s=128x72:r=30:d=5.8', '-c:v', 'libx264', '-threads', '2',
        '-pix_fmt', 'yuv420p', '-an', str(clip)], check=True, timeout=30)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=22050:duration=29.1', '-c:a', 'libmp3lame',
        str(audio)], check=True, timeout=30)
    box = material(work / 'initial-cuts', clip, audio)
    artifact = prepare(box)
    return SimpleNamespace(clip=clip, audio=audio, box=box, artifact=artifact,
        data=[Path(specs[0]['path']).read_bytes() for specs in artifact.inputs])


@pytest.fixture
def complete(completed_captured_audio, monkeypatch, tmp_path):
    box = completed_captured_audio
    # The source fixture supplies an inert app.tasks module. Attach only these
    # exact source-extracted pure gates, not any paid task or approval shim.
    from app import tasks
    for name, function in helpers().items():
        if name.startswith('_') and callable(function):
            monkeypatch.setattr(tasks, name, function, raising=False)
    forbidden = Mock(side_effect=AssertionError('No new provider, normalization or storage writes'))
    monkeypatch.setattr(render, 'normalize_clip', forbidden)
    monkeypatch.setattr(text_runtime, 'generate_retained_router_review', forbidden)
    monkeypatch.setattr(audio_runtime, 'run_blind_asr', forbidden)
    monkeypatch.setattr(audio_runtime, 'run_prosody', forbidden)
    for cls in (text_journal.RouterReviewJournal, audio_journal.RouterAudioReviewJournal):
        monkeypatch.setattr(cls, 'reserve', forbidden)
        monkeypatch.setattr(cls, 'settle', forbidden)
        monkeypatch.setattr(cls, '_fresh', forbidden)
    monkeypatch.setattr(box.s3, 'put_object', forbidden)
    box.no_new_work = forbidden
    box.render_root = tmp_path / 'private-render'
    box.render_root.mkdir(mode=0o700)
    box.original_records = _dump(box.client)
    box.before_calls = len(box.wire.calls)
    box.before_objects = deepcopy(box.s3.objects)
    return box


def inputs(box, workdir=None):
    return consumer.prepare_retained_render_inputs(box.client, box.s3, bucket=BUCKET,
        captured_story_continuation=box.visual_cap, story_evidence=box.qualification,
        audit_pointer=box.audit_pointer, workdir=workdir or box.render_root)


def unchanged(box):
    assert _dump(box.client) == box.original_records
    assert len(box.wire.calls) == box.before_calls
    assert box.s3.objects == box.before_objects
    box.no_new_work.assert_not_called()


def test_unissued_or_ambiguous_inputs_fail_before_any_io(tmp_path):
    class NoIO:
        def __getattr__(self, name):
            raise AssertionError('No I/O may precede closed selection')
    for value in (None, True, {}, object.__new__(consumer.ExactRetainedRenderCuts)):
        with pytest.raises(consumer.RetainedRenderError):
            consumer.render_retained_review(value, workdir=tmp_path)
        if value is not None:
            with pytest.raises(consumer.RetainedRenderError):
                render.render_video('unread.mp3', ['unread.mp4'], 'unused', 'unwritten.mp4',
                                    retained_cuts=value)
    for options in ({}, {'completion_plan': True}, {'captured_story_continuation': {}},
                    {'completion_plan': True, 'captured_story_continuation': True}):
        with pytest.raises(consumer.RetainedRenderError):
            consumer.prepare_retained_render_inputs(NoIO(), NoIO(), bucket=BUCKET,
                audit_pointer={}, workdir=tmp_path, **options)
    for cls in (consumer.ExactRetainedRenderCuts, consumer.RetainedLocalRender):
        with pytest.raises(TypeError): cls()


def test_real_exact_input_render_and_terminal_failures_without_new_reviews(complete, monkeypatch, subtests):
    box = complete
    value = inputs(box)
    state = consumer._checked(value, 'prepared')
    with pytest.raises(consumer.RetainedRenderError):
        render.render_video('unread.mp3', ['unread.mp4'], 'unused', 'unwritten.mp4', retained_cuts=value)
    assert consumer._checked(value, 'prepared') is state
    cut_record = json.loads(state['cuts'])
    assert [row['identity'] for row in state['files'][1:]] == [row['cut'] for row in cut_record['cuts']]
    assert state['files'][0]['identity']['sha256'] == box.source.policy['audio']['sha256']
    assert all(Path(row['path']).stat().st_mode & 0o777 == 0o600 for row in state['files'])
    try:
        result = consumer.render_retained_review(value, workdir=box.render_root)
    except consumer.RetainedRenderError as error:
        # Production errors intentionally suppress details. Test failures name
        # only the actual failing code locations, never input/config values.
        current, locations = error, []
        for _ in range(6):
            if current is None:
                break
            trace = current.__traceback__
            while trace is not None:
                locations.append((Path(trace.tb_frame.f_code.co_filename).name,
                                  trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            current = current.__context__
        pytest.fail(f'Actual local render failed at {locations}', pytrace=False)
    assert type(result) is consumer.RetainedLocalRender
    record = result.record
    assert all(record[name] is expected for name, expected in consumer._FLAGS.items())
    assert record['local_final_gates']['pass'] is True
    assert record['render_metrics']['frame_count'] == 900
    assert record['render_metrics']['max_freeze_seconds'] <= 5
    assert record['freeze_measurement']['includes_open_tail'] is True
    assert record['freeze_measurement']['max_freeze_seconds'] <= 5
    assert record['final'] == cuts._file(record['path'])[0]
    assert record['original_audio'] == box.source.policy['audio']
    assert record['final_aac_independently_listened'] is False
    assert [command['phase'] for command in record['assembly_commands']] == ['concat', 'mux']
    concat, mux = [command['argv'] for command in record['assembly_commands']]
    assert all(f'$cut_{index}' in concat for index in range(6))
    assert concat[concat.index('-filter_complex_threads') + 1] == '1'
    assert all(concat[index - 2:index] == ['-threads', '1']
               for index, arg in enumerate(concat) if arg == '-i')
    assert 'concat=n=6:v=1:a=0' in concat[concat.index('-filter_complex') + 1]
    assert '$voice' in mux and 'loudnorm=I=-15:TP=-1.0:LRA=7' in mux[mux.index('-af') + 1]
    assert record['final_probe']['audio']['codec_name'] == 'aac'
    assert record['final_probe']['video']['width'] == 1080
    record['qa_approved'] = True
    assert result.record['qa_approved'] is False
    with pytest.raises(TypeError): copy(value)
    with pytest.raises(TypeError): copy(result)
    with pytest.raises(consumer.RetainedRenderError): consumer.render_retained_review(value, workdir=box.render_root)
    unchanged(box)
    # Share the expensive genuine source/review preimage across independent
    # negatives. Each local capability/workdir remains fresh, and only these
    # disposable stores are restored after an adversarial mutation.
    parent = box.render_root
    for name, check in (('evidence', _missing_evidence), ('local', _local_failures), ('media', _media_failures)):
        box.render_root = parent / name
        box.render_root.mkdir(mode=0o700)
        if name == 'evidence': check(box, subtests)
        else: check(box, monkeypatch, subtests)


def _missing_evidence(box, subtests):
    keys = [box.visual_receipt['anchor_key'], box.link_receipt['anchor_key'],
            *consumer.audio_reader._artifact_keys(audio_journal.RouterAudioReviewJournal(
                box.client, captured_story_continuation=box.visual_cap)).values()]
    for number, mode in enumerate((*keys, 'cut_blob', 'source', 'visual_unknown')):
        with subtests.test(mode=mode):
            work = box.render_root / str(number)
            work.mkdir(mode=0o700)
            if mode in keys: box.client.delete(mode)
            elif mode == 'cut_blob':
                pointer = box.visual_evidence.commitments['cuts'][0]
                box.s3.objects[pointer['key']] = (b'changed', 'video/mp4')
            elif mode == 'source': box.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
            else:
                # A malformed current record cannot make the old unknown STORY
                # or a newly unknown VISUAL usable, even if slot names match.
                current = json.loads(box.client.get(continuation.VISUAL_KEYS[0]))
                current['slots']['retained_visual_review']['response'] = None
                box.client.set(continuation.VISUAL_KEYS[0], json.dumps(current))
            with pytest.raises(consumer.RetainedRenderError): inputs(box, work)
            assert not (work / 'retained-render-inputs').exists()
            restore(box.client, box.original_records)
            box.s3.objects = deepcopy(box.before_objects)
    unchanged(box)


def _local_failures(box, monkeypatch, subtests):
    for number, mode in enumerate(('materialize_race', 'cut_changed', 'voice_changed', 'source_changed', 'owner', 'workdir')):
        with subtests.test(mode=mode), monkeypatch.context() as patch:
            work = box.render_root / str(number)
            work.mkdir(mode=0o700)
            if mode == 'materialize_race':
                original_write = consumer._write
                def changed(*args):
                    result = original_write(*args)
                    box.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
                    return result
                patch.setattr(consumer, '_write', changed)
                with pytest.raises(consumer.RetainedRenderError): inputs(box, work)
            else:
                value = inputs(box, work)
                state = consumer._checked(value, 'prepared')
                if mode in ('cut_changed', 'voice_changed'):
                    path = Path(state['files'][1 if mode == 'cut_changed' else 0]['path'])
                    path.write_bytes(path.read_bytes() + b'changed')
                elif mode == 'source_changed': box.client.set(continuity._JOB + continuity.LEAF_ID, '{}')
                elif mode == 'owner':
                    failures = []
                    def other():
                        try: consumer.render_retained_review(value, workdir=work)
                        except consumer.RetainedRenderError: failures.append(True)
                    thread = Thread(target=other); thread.start(); thread.join(timeout=5)
                    assert not thread.is_alive() and failures == [True]
                    # Wrong-thread access cannot consume the owner's input.
                    assert consumer._checked(value, 'prepared') is state
                    state['files'][0]['path'] = 'invalidated-test-only'
                with pytest.raises(consumer.RetainedRenderError):
                    consumer.render_retained_review(value, workdir=box.render_root if mode == 'workdir' else work)
                with pytest.raises(consumer.RetainedRenderError): consumer.render_retained_review(value, workdir=work)
                assert not (work / 'retained-render-output').exists()
            restore(box.client, box.original_records)
    unchanged(box)


def _media_failures(box, monkeypatch, subtests):
    for number, mode in enumerate(('frames', 'silence', 'freeze', 'trailing_freeze')):
        with subtests.test(mode=mode), monkeypatch.context() as patch:
            work = box.render_root / str(number)
            work.mkdir(mode=0o700)
            value = inputs(box, work)
            def fault(command):
                actual = list(command)
                if mode == 'frames' and '-af' in actual:
                    actual[actual.index('-frames:v') + 1] = '899'
                elif mode == 'silence' and '-af' in actual:
                    actual[actual.index('-af') + 1] += ',volume=0'
                elif mode == 'freeze' and '-filter_complex' in actual:
                    index = actual.index('-filter_complex') + 1
                    actual[index] = actual[index].replace('[master]',
                        ',loop=loop=300:size=1:start=0,setpts=N/30/TB[master]')
                elif mode == 'trailing_freeze' and '-filter_complex' in actual:
                    index = actual.index('-filter_complex') + 1
                    actual[index] = actual[index].replace('[master]',
                        ',select=eq(n\\,0),loop=loop=-1:size=1:start=0,setpts=N/30/TB[master]')
                REAL_RUN(actual)
            # Inject a real damaged output at the process boundary; every gate
            # still performs its real probe. No synthetic pass is supplied.
            patch.setattr(render, '_run', fault)
            measured = []
            actual_measure = consumer._measure_freeze
            def measure(*args):
                result = actual_measure(*args)
                measured.append(result)
                return result
            patch.setattr(consumer, '_measure_freeze', measure)
            with pytest.raises(consumer.RetainedRenderError): consumer.render_retained_review(value, workdir=work)
            output = work / 'retained-render-output' / 'final.mp4'
            assert render.video_frame_count(output) == (899 if mode == 'frames' else 900)
            if mode == 'silence': assert render.ending_silence_duration(output) > 1.55
            if mode == 'freeze': assert render.max_freeze_duration(output) > 6
            if mode == 'trailing_freeze':
                assert measured and measured[-1]['open_tail_seconds'] > 6
                assert measured[-1]['max_freeze_seconds'] > 6
            with pytest.raises(consumer.RetainedRenderError): consumer.render_retained_review(value, workdir=work)
            assert consumer._ISSUED[value]['phase'] == 'failed'
    unchanged(box)
