"""Provider-free, pre-claim local rendering of verified retained components.

The readers authenticate the components; this module freezes their exact local
inputs while the original source remains unchanged. Its one-use capability is
only for a reversible local preview. No job claim, upload, retry, publication or
final QA permission is issued. AAC is a recorded transform of the reviewed MP3,
not a newly transcribed/listened-to audio observation.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import weakref

from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_journal as story_journal
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import retained_audio_review_evidence as audio_reader
from app.services import retained_story_visual_evidence as visual_reader
from app.services import retained_captured_story_visual_evidence as captured_visual_reader
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_cut_evidence as cuts
from app.services import retained_review_completion_plan as completion
from app.services import production_connection_continuity as continuity
from app.services import preserved_visual_recovery as recovery
from app.services import render
from app.services.voice_candidate_recovery import _voice_result

_FLAGS = {'diagnostic_only': True, 'claim_authorized': False, 'publish_eligible': False,
          'resume_authorized': False, 'qa_approved': False, 'full_qa_complete': False,
          'automatic_retry_permitted': False, 'render_authorized': False}
_ISSUED = weakref.WeakKeyDictionary()
_RESULTS = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()


class RetainedRenderError(RuntimeError):
    """Fixed local rejection; no source, backend, file or provider error text."""


def _require(value):
    if not value:
        raise RetainedRenderError('retained_render_unverified')


def _raw(value):
    return cuts._raw(value)


def _hash(value):
    return hashlib.sha256(_raw(value)).hexdigest()


class ExactRetainedRenderCuts:
    """Owner-thread one-use local render input; never a production grant."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_render_inputs_private')

    def __repr__(self):
        return '<ExactRetainedRenderCuts local-preview-only>'


class RetainedLocalRender:
    """Immutable local artifact diagnostics; no publisher or worker admission."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_local_render_private')

    def __repr__(self):
        return '<RetainedLocalRender private-local-preview>'

    @property
    def record(self):
        _require(type(self) is RetainedLocalRender and self in _RESULTS)
        return artifacts._object(_RESULTS[self])

    @property
    def qa_approved(self):
        return False

    @property
    def publish_eligible(self):
        return False


def _checked(value, phase):
    _require(type(value) is ExactRetainedRenderCuts)
    with _LOCK:
        state = _ISSUED.get(value)
    _require(state is not None and state['owner'] == threading.get_ident()
             and state['phase'] == phase)
    return state


def _directory(path):
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts)
    _require(all(not part.is_symlink() for part in (path, *path.parents)))
    info = path.stat(follow_symlinks=False)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700)
    return path


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _file(path, expected=None):
    info = Path(path).stat(follow_symlinks=False)
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    identity, _ = cuts._file(path)
    _require(_fingerprint(info) == _fingerprint(Path(path).stat(follow_symlinks=False)))
    if expected is not None:
        _require(identity == expected)
    return {'path': str(path), 'identity': identity, 'stat': _fingerprint(info)}


def _write(directory, name, data, expected):
    _directory(directory)
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        _require(stream.write(data) == len(data))
        stream.flush()
        os.fsync(stream.fileno())
    return _file(path, expected)


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _files(state):
    _directory(state['base'])
    _directory(state['input_dir'])
    for row in state['files']:
        _require(_file(row['path'], row['identity']) == row)


def _watched(pipe, state):
    """Repeat exact source/control/anchor checks, never renew an entitlement."""
    c, a = json.loads(state['visual']), json.loads(state['audio'])
    captured = state['captured_story_continuation'] is not None
    controller = continuation if captured else completion
    cap = state['captured_story_continuation'] if captured else state['completion_plan']
    controller._checked(cap)
    options = {'captured_story_continuation' if captured else 'completion_plan': cap}
    keys = controller.selected_keys(cap, 'story')
    _require(keys == (continuation.VISUAL_KEYS if captured else completion.STORY_KEYS))
    audio = audio_journal.RouterAudioReviewJournal(state['client'], **options)
    artifact_keys = artifacts._anchor_keys(keys)
    pipe.watch(*keys, *audio.keys, *artifact_keys.values(),
               *audio_reader._artifact_keys(audio).values(), c['sampled_link_anchor_key'],
               *recovery._keys(continuity.LEAF_ID))
    control, states, control_raw, _ = controller._read_control(pipe)
    story = story_journal.RouterReviewJournal(state['client'], **options)._read(pipe)
    audio_state = audio._read(pipe)
    original_cap = continuation._issued_bytes(cap) if captured else cap._manifest_bytes
    prefix = 'continuation' if captured else 'completion'
    _require(control_raw == original_cap and states['story'] == story
             and states['audio'] == audio_state
             and hashlib.sha256(control_raw).hexdigest() == c[prefix + '_manifest_sha256']
             and _hash(pipe.hgetall(controller.JOURNAL_KEY)) == c[prefix + '_journal_sha256']
             and pipe.get(controller.ANCHOR_KEY) == c[prefix + '_anchor_sha256']
             and control['predecessors']['snapshot_sha256'] == c['predecessor_snapshot_sha256']
             and list(keys) == c['journal_keys'] and _hash(story) == c['journal_state_sha256']
             and _hash(story['policy']) == c['policy_sha256']
             and _hash(audio_state) == a['journal_state_sha256'])
    source = continuity._derive(pipe, story['policy']['profile_revision'])
    original, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
    _require(_hash(source) == c['continuity_sha256'] == a['source']['continuity_sha256']
             and fingerprint == c['source_state_sha256']
             and _hash(original['spec']) == c['source_spec_sha256']
             and _hash(original['generated_asset_candidates']) == c['source_journal_sha256'])
    sink = artifacts.RetainedRouterReviewArtifactSink(state['s3'], bucket=state['bucket'])
    sink._keys = keys
    final = sink._read_anchor(pipe, story_journal.PURPOSES[1])
    _require(_hash(final) == c['visual_anchor_sha256'] and final['manifest'] == c['visual_manifest'])
    if captured:
        qualification = continuation._evidence(state['story_evidence'])
        tag = {'version': 1, 'kind': 'captured_transport_story',
               'continuation_manifest_sha256': hashlib.sha256(control_raw).hexdigest(),
               'evidence': qualification}
        _require(pipe.exists(artifact_keys[story_journal.PURPOSES[0]]) == 0
                 and final['prior_story_anchor_sha256'] is None
                 and _raw(tag) == _raw(c['story_predecessor']) == _raw(final['story_predecessor'])
                 and _raw(qualification) == _raw(control['story_qualification'])
                 and _hash(qualification) == c['story_evidence_sha256'])
    else:
        first = sink._read_anchor(pipe, story_journal.PURPOSES[0])
        _require(_hash(first) == c['story_anchor_sha256'] and first['manifest'] == c['story_manifest']
                 and final['prior_story_anchor_sha256'] == _hash(first))
    link_ttl = pipe.pttl(c['sampled_link_anchor_key'])
    _require(type(link_ttl) is int and link_ttl == -1)
    link = artifacts._object(pipe.get(c['sampled_link_anchor_key']))
    _require(_hash(link) == c['sampled_link_anchor_sha256'] and link['pointer'] == c['sampled_link'])
    for kind in ('asr', 'final'):
        anchor = audio_reader._anchor(pipe, kind, audio_state, audio)
        _require(_hash(anchor) == a[kind + '_anchor_sha256'])
    pointer = audio_reader._source(pipe, audio_state['policy'])
    _require(pointer['metadata_sha256'] == c['source_metadata_sha256']
             and audio_state['policy']['audio'] == c['audio'] == a['source']['audio'])
    return original, pointer, audio_state['policy']


def _remote(state):
    with state['client'].pipeline() as pipe:
        _watched(pipe, state)
        artifacts._ack_read(pipe)


def prepare_retained_render_inputs(client, s3, *, bucket, audit_pointer, workdir,
                                  completion_plan=None, captured_story_continuation=None,
                                  story_evidence=None):
    """Authenticate both component families and materialize private exact bytes.

    Independent reader ACKs are followed by a shared watched source/anchor
    transaction spanning materialization. Partial local files on uncertainty
    remain diagnostics; no capability is returned and nothing is retried.
    """
    try:
        base = _directory(workdir)
        _require((completion_plan is None) != (captured_story_continuation is None))
        if captured_story_continuation is not None:
            continuation._checked(captured_story_continuation)
            continuation._evidence(story_evidence)
            v = captured_visual_reader.read_retained_captured_story_visual_evidence(
                client, s3, bucket=bucket, captured_story_continuation=captured_story_continuation,
                story_evidence=story_evidence, audit_pointer=audit_pointer)
            _require(type(v) is captured_visual_reader.RetainedCapturedStoryVisualEvidence)
        else:
            _require(story_evidence is None)
            completion._checked(completion_plan)
            v = visual_reader.read_retained_story_visual_evidence(client, s3, bucket=bucket,
                completion_plan=completion_plan, audit_pointer=audit_pointer)
            _require(type(v) is visual_reader.RetainedStoryVisualEvidence)
        a = audio_reader.read_retained_audio_review_evidence(client, s3, bucket=bucket,
            completion_plan=completion_plan, captured_story_continuation=captured_story_continuation)
        _require(type(a) is audio_reader.RetainedAudioReviewEvidence)
        c, diagnostic = v.commitments, a.diagnostics
        state = {'client': client, 's3': s3, 'bucket': bucket, 'completion_plan': completion_plan,
                 'captured_story_continuation': captured_story_continuation, 'story_evidence': story_evidence,
                 'visual': _raw(c), 'audio': _raw(diagnostic), 'base': str(base),
                 'owner': threading.get_ident(), 'phase': 'preparing'}
        with client.pipeline() as pipe:
            original, pointer, policy = _watched(pipe, state)
            metadata_raw = audio_reader._blob(s3, bucket, pointer['metadata_key'],
                pointer['metadata_sha256'], audio_reader.MAX_RECORD_BYTES, content_type='application/json')
            audio_journal._metadata(metadata_raw, policy)
            metadata = artifacts._object(metadata_raw)
            voice = _voice_result(metadata['voice'], 6)
            _require(' '.join(voice['spoken_texts']) == diagnostic['source']['expected_narration'])
            record = artifacts._object(cuts._read_private(s3, bucket, c['cut_manifest']))
            audit = artifacts._object(cuts._read_private(s3, bucket,
                {k: c['audit_pointer'][k] for k in ('key', 'sha256', 'size')}
                | {'content_type': 'application/json'}))
            package = recovery._story_fields(audit['package'])
            _require(_hash(audit['package']) == c['cut_package_sha256'] == record['package_sha256']
                     and _hash(package) == c['immutable_core_sha256']
                     and record['voice_metadata_sha256'] == _hash(voice)
                     and record['objects'] == c['cuts'] and len(c['cuts']) == 6
                     and record['build'] == c['historical_build']
                     and record['fps'] == 30 and record['output_resolution'] == '1080x1920')
            options = recovery._options(original)
            from app import tasks
            target = tasks._effective_short_edit_target(options, 30, voice['duration_after_fit'])
            _require(tasks._short_preview_voice_duration_qc(voice, target).get('pass') is True)
            directory = base / 'retained-render-inputs'
            directory.mkdir(mode=0o700, exist_ok=False)
            files = []
            audio_raw = audio_reader._blob(s3, bucket, pointer['audio_key'], pointer['audio_sha256'],
                audio_reader.adapter.MAX_AUDIO_BYTES, size=pointer['size'], content_type='audio/mpeg')
            files.append(_write(directory, 'original.mp3', audio_raw,
                {'sha256': c['audio']['sha256'], 'size': c['audio']['bytes']}))
            inputs = []
            for index, (cut_pointer, row) in enumerate(zip(c['cuts'], record['cuts'])):
                _require(row['scene_index'] == index and row['candidate_index'] == 0
                         and row['cut'] == {k: cut_pointer[k] for k in ('sha256', 'size')})
                local = _write(directory, f'scene-{index:02d}.mp4',
                    cuts._read_private(s3, bucket, cut_pointer), row['cut'])
                files.append(local)
                inputs.append([{'path': local['path'], 'generated': True, 'source_type': 'generated',
                    'generation_provider': row['raw']['provider'], 'start_fraction': 0.0,
                    'preserve_start_fraction': True, 'forbid_loop': True}])
            state.update(input_dir=str(directory), files=tuple(files), package=_raw(package),
                voice=_raw(voice), cuts=_raw(record), options=_raw(options), target=target, inputs=_raw(inputs))
            _verify_timing(state)
            _files(state)
            _sync_directory(directory)
            _sync_directory(base)
            _watched(pipe, state)
            artifacts._ack_read(pipe)
        value = object.__new__(ExactRetainedRenderCuts)
        state['phase'] = 'prepared'
        with _LOCK:
            _ISSUED[value] = state
        return value
    except RetainedRenderError:
        raise
    except Exception:
        raise RetainedRenderError('retained_render_unverified') from None


def _verify_timing(state):
    voice, package, record = (json.loads(state[name]) for name in ('voice', 'package', 'cuts'))
    inputs = json.loads(state['inputs'])
    measured = getattr(render.media_duration, '__wrapped__', render.media_duration)(state['files'][0]['path'])
    _require(math.isfinite(measured) and abs(measured - record['measured_voice_duration']) <= 1e-6
             and abs(measured - voice['duration_after_fit']) <= 0.12)
    timeline = render._scene_timeline(package['scenes'], inputs, voice['scene_durations'], measured, [])
    counts = render._timeline_frame_counts(timeline, measured)
    _require(len(timeline) == 6 and [t[3] for t in timeline] == list(range(6))
             and counts == [row['target_frames'] for row in record['cuts']]
             and sum(counts) == record['total_frames']
             and all(t[1] == row['timeline_duration'] and t[2] == row['transition']
                     for t, row in zip(timeline, record['cuts'])))
    for local, count in zip(state['files'][1:], counts):
        _require(render.video_frame_count(local['path']) == count)
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,r_frame_rate,avg_frame_rate,pix_fmt,sample_aspect_ratio',
            '-of', 'json', local['path']], timeout=30))
        _require(type(probe.get('streams')) is list and len(probe['streams']) == 1)
        video = probe['streams'][0]
        _require(video == {'width': 1080, 'height': 1920, 'r_frame_rate': '30/1',
                          'avg_frame_rate': '30/1', 'pix_fmt': 'yuv420p', 'sample_aspect_ratio': '1:1'})
    return measured, counts


def _assert_renderer_entry(value):
    state = _checked(value, 'rendering')
    _require(state['seam_used'] is False)


def _take_exact_cuts(value, *, voice_path, scenes, scene_durations, scene_visual_paths,
                     narration, timeline, frame_counts, target_duration, output_resolution, output_path):
    """Private renderer seam: exact issued invocation, before any FFmpeg render."""
    state = _checked(value, 'rendering')
    _require(state['seam_used'] is False)
    state['seam_used'] = True
    _files(state)
    voice, package = json.loads(state['voice']), json.loads(state['package'])
    rows, inputs = json.loads(state['cuts'])['cuts'], json.loads(state['inputs'])
    expected_timeline = [(inputs[index][0], row['timeline_duration'], row['transition'], index)
                         for index, row in enumerate(rows)]
    _require(str(voice_path) == state['files'][0]['path'] and scenes == package['scenes']
             and narration == package['narration'] and scene_durations == voice['scene_durations']
             and scene_visual_paths == inputs
             and target_duration == state['target'] and output_resolution == '1080x1920'
             and str(output_path) == state['output_path']
             and timeline == expected_timeline
             and frame_counts == [row['target_frames'] for row in rows])
    return [Path(row['path']) for row in state['files'][1:]]


def _assembly_build():
    # Historical cut recipes remain independent of the currently executed
    # concatenation/mux build. This is a new local transform, not a new review.
    return {**cuts._build_identity(),
            'consumer_sha256': cuts._file(Path(__file__), 2 * 1024 * 1024)[0]['sha256']}


def _run_exact_command(value, command, *, phase):
    state = _checked(value, 'rendering')
    _require(state['seam_used'] is True and phase in ('concat', 'mux')
             and len(state['assembly_commands']) == (0 if phase == 'concat' else 1)
             and type(command) is list and len(command) <= 128
             and all(type(arg) is str and len(arg.encode()) <= 16384 for arg in command)
             and command[0] == 'ffmpeg' and _assembly_build() == state['assembly_build'])
    _files(state)
    executable = str(Path(shutil.which('ffmpeg')).resolve(strict=True))
    _require(cuts._file(executable, 256 * 1024 * 1024)[0]
             == state['assembly_build']['tools']['ffmpeg']['executable'])
    # Six independent 1080p decoders otherwise allocate worker pools together.
    # Bound this opt-in assembly's actual decoder, filter and encoder threads;
    # the decoded concat graph, frames, codec settings and quality gates stay
    # the same. The recorded argv includes every executed resource option.
    executed = [executable, '-filter_complex_threads', '1', '-filter_threads', '1']
    for arg in command[1:-1]:
        if arg == '-i':
            executed.extend(['-threads', '1'])
        executed.append(arg)
    executed.extend(['-threads', '1', command[-1]])
    output = Path(state['output_path'])
    expected_output = output.parent / 'silent.mp4' if phase == 'concat' else output
    _require(Path(executed[-1]) == expected_output and not expected_output.exists())
    render._run(list(executed))
    _files(state)
    _require(_assembly_build() == state['assembly_build'])
    os.chmod(expected_output, 0o600)
    actual_output = _file(expected_output)
    path_labels = {row['path']: '$voice' if index == 0 else f'$cut_{index - 1}'
                   for index, row in enumerate(state['files'])}
    path_labels.update({executable: '$ffmpeg', str(output.parent / 'silent.mp4'): '$silent',
                        str(output): '$final'})
    state['assembly_commands'].append({'phase': phase,
        'executed_argv_sha256': _hash(list(executed)),
        'argv': [path_labels.get(arg, arg) for arg in executed],
        'output': actual_output['identity']})


def _finish_exact_cuts(value):
    state = _checked(value, 'rendering')
    _require(state['seam_used'] is True and len(state['assembly_commands']) == 2
             and _assembly_build() == state['assembly_build'])
    _files(state)


def _final_probe(path):
    raw = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
        'stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,sample_aspect_ratio,sample_rate,channels,duration:format=duration',
        '-of', 'json', str(path)], timeout=30)
    _require(type(raw) is bytes and 0 < len(raw) <= 64 * 1024)
    probe = json.loads(raw)
    streams = probe.get('streams')
    _require(type(streams) is list and len(streams) == 2
             and sorted(row.get('codec_type', '') for row in streams) == ['audio', 'video'])
    video = next(row for row in streams if row['codec_type'] == 'video')
    audio = next(row for row in streams if row['codec_type'] == 'audio')
    _require(all(video.get(name) == value for name, value in {
        'codec_name': 'h264', 'width': 1080, 'height': 1920, 'r_frame_rate': '30/1',
        'avg_frame_rate': '30/1', 'pix_fmt': 'yuv420p', 'sample_aspect_ratio': '1:1'}.items())
        and audio.get('codec_name') == 'aac' and audio.get('sample_rate') == '48000'
        and type(audio.get('channels')) is int and 1 <= audio['channels'] <= 2)
    durations = {name: float(value['duration']) for name, value in (
        ('video', video), ('audio', audio), ('container', probe['format']))}
    _require(all(math.isfinite(number) and number > 0 for number in durations.values()))
    return {'video': video, 'audio': audio, 'durations': durations,
            'probe_sha256': hashlib.sha256(raw).hexdigest()}


def _measure_freeze(state, path, duration):
    """Measure completed freezes and an open EOF interval on the video clock."""
    _require(type(duration) is float and math.isfinite(duration) and 0 < duration <= 40
             and _assembly_build() == state['assembly_build'])
    executable = str(Path(shutil.which('ffmpeg')).resolve(strict=True))
    _require(cuts._file(executable, 256 * 1024 * 1024)[0]
             == state['assembly_build']['tools']['ffmpeg']['executable'])
    argv = [executable, '-hide_banner', '-nostats', '-filter_threads', '1',
        '-threads', '1', '-i', str(path), '-map', '0:v:0',
        '-vf', 'scale=320:-2,freezedetect=n=-40dB:d=2.00', '-an', '-f', 'null', '-']
    # A bounded read of a private temporary log avoids returning arbitrary
    # decoder messages or retaining unbounded stderr in process memory.
    with tempfile.TemporaryFile() as log:
        result = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=log, timeout=90, check=False)
        size = log.tell()
        _require(result.returncode == 0 and 0 <= size <= 256 * 1024)
        log.seek(0)
        raw = log.read(256 * 1024 + 1)
        _require(len(raw) == size)
    active, intervals = None, []
    for kind, text in re.findall(rb'lavfi\.freezedetect\.freeze_(start|end):\s*([0-9.]+)', raw):
        value = float(text)
        _require(math.isfinite(value) and 0 <= value <= 40)
        value = min(duration, value)
        if kind == b'start':
            _require(active is None)
            active = value
        else:
            _require(active is not None and value >= active)
            intervals.append(value - active)
            active = None
    tail = duration - active if active is not None else 0.0
    _require(_assembly_build() == state['assembly_build'])
    return {'method': 'actual_ffmpeg_freezedetect', 'includes_open_tail': True,
        'minimum_seconds': 2.0, 'video_timeline_seconds': duration,
        'max_freeze_seconds': max([tail, *intervals]),
        'completed_interval_count': len(intervals), 'open_tail_seconds': tail,
        'stderr_sha256': hashlib.sha256(raw).hexdigest(), 'stderr_size': len(raw),
        'executed_argv_sha256': _hash(argv),
        'argv': ['$ffmpeg' if arg == executable else '$final' if arg == str(path) else arg for arg in argv]}


def render_retained_review(inputs, *, workdir):
    """Render once locally, preserving the existing short-master quality gates."""
    state = _checked(inputs, 'prepared')
    state['phase'] = 'failed'  # Every attempted invocation is terminal, including bad workdirs.
    try:
        _require(str(_directory(workdir)) == state['base'])
        _files(state)
        _remote(state)
        measured, _ = _verify_timing(state)
        output_dir = Path(workdir) / 'retained-render-output'
        output_dir.mkdir(mode=0o700, exist_ok=False)
        state.update(phase='rendering', seam_used=False, output_path=str(output_dir / 'final.mp4'),
                     assembly_build=_assembly_build(), assembly_commands=[])
        voice, package, options = (json.loads(state[name]) for name in ('voice', 'package', 'options'))
        scene_inputs = json.loads(state['inputs'])
        rendered = render.render_video(voice_path=state['files'][0]['path'],
            visual_paths=[row[0] for row in scene_inputs], narration=package['narration'],
            output_path=state['output_path'], scenes=package['scenes'],
            scene_durations=voice['scene_durations'], scene_visual_paths=scene_inputs,
            target_duration=state['target'], output_resolution='1080x1920',
            capture_scene_windows=True, retained_cuts=inputs)
        from app import tasks
        _require(state['seam_used'] is True
                 and tasks._preview_duration_within_gate(rendered['duration'], state['target'], measured))
        gate = tasks._strict_short_preview_render_qc(rendered, state['target'], voice['duration_after_fit'])
        _require(gate.get('pass') is True and type(rendered['max_freeze_seconds']) in (int, float)
                 and math.isfinite(rendered['max_freeze_seconds'])
                 and rendered['max_freeze_seconds'] <= (5.0 if options['mode'] == 'preview' else 6.0))
        probe = _final_probe(rendered['path'])
        _require(all(tasks._preview_duration_within_gate(value, state['target'], measured)
                     for value in probe['durations'].values()))
        freeze = _measure_freeze(state, rendered['path'], probe['durations']['video'])
        _require(freeze['max_freeze_seconds'] <= (5.0 if options['mode'] == 'preview' else 6.0))
        _files(state)
        _remote(state)
        for path in (rendered['path'], rendered['srt']):
            os.chmod(path, 0o600)
        final, captions = _file(rendered['path']), _file(rendered['srt'])
        _require(final['identity'] == state['assembly_commands'][-1]['output']
                 and _assembly_build() == state['assembly_build'])
        _sync_directory(output_dir)
        state['phase'] = 'finished'
        record = {'version': 1, 'kind': 'retained_local_render', **_FLAGS,
            'preclaim_only': True, 'local_render_verified': True,
            'path': final['path'], 'final': final['identity'], 'captions': captions['identity'],
            'source_commitments': json.loads(state['visual']),
            'audio_evidence_sha256': hashlib.sha256(state['audio']).hexdigest(),
            'original_audio': json.loads(state['visual'])['audio'],
            'cut_manifest': json.loads(state['visual'])['cut_manifest'],
            'target_seconds': state['target'], 'render_metrics': deepcopy(rendered),
            'local_final_gates': gate, 'final_aac_independently_listened': False,
            'final_probe': probe, 'freeze_measurement': freeze,
            'assembly_build': deepcopy(state['assembly_build']),
            'assembly_commands': deepcopy(state['assembly_commands'])}
        result = object.__new__(RetainedLocalRender)
        _RESULTS[result] = _raw(record)
        return result
    except Exception:
        state['phase'] = 'failed'
        raise RetainedRenderError('retained_render_unverified') from None
