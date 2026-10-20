"""Display observers must not change media, skip gates, or restart rendering."""
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from app.services import render


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='Native media tools required')
def test_real_render_is_identical_when_display_updates_fail(tmp_path, monkeypatch):
    def ffmpeg(*args):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], capture_output=True,
                       check=True, timeout=30)

    voice = tmp_path / 'voice.wav'
    ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5',
           '-c:a', 'pcm_s16le', str(voice))

    normalized = []
    def normalize(_spec, path, seconds, *_args):
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30',
               '-frames:v', str(round(seconds * 30)), '-an', '-c:v', 'libx264',
               '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path))
        normalized.append(path)
    monkeypatch.setattr(render, 'normalize_clip', normalize)
    commands = []
    original_run = render._run
    def run(command):
        commands.append(command)
        original_run(command)
    monkeypatch.setattr(render, '_run', run)

    events = []
    def report(phase, completed, total):
        events.append((phase, completed, total))
        if phase == 'segments':
            assert len(normalized) == completed
            assert all(path.exists() for path in normalized)
        raise ConnectionError('Dashboard temporarily unavailable')

    output = tmp_path / 'final.mp4'
    kwargs = dict(scenes=[{'narration': 'First scene.'}, {'narration': 'Second scene.'}],
                  scene_durations=[2, 3], scene_visual_paths=[['first'], ['second']],
                  capture_scene_windows=True)
    first = render.render_video(voice, ['first', 'second'], 'First scene. Second scene.',
                                output, progress_callback=report, **kwargs)
    first_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    first_commands = list(commands)
    commands.clear()
    normalized.clear()
    second = render.render_video(voice, ['first', 'second'], 'First scene. Second scene.',
                                 output, **kwargs)
    assert events == [('segments', 0, 2), ('segments', 1, 2), ('segments', 2, 2),
                      ('assembly', 2, 2), ('audio', 2, 2), ('checks', 2, 2)]
    assert first == second
    assert first_hash == hashlib.sha256(output.read_bytes()).hexdigest()
    assert commands == first_commands
    assert first['frame_count'] == 150 and first['duration'] == pytest.approx(5)
    assert first['scene_windows'][-1]['end_frame'] == 150
    assert first['ending_silence_seconds'] < .05


def test_real_media_failure_still_stops_before_assembly(tmp_path, monkeypatch):
    monkeypatch.setattr(render, 'media_duration', lambda _: 5)
    monkeypatch.setattr(render, 'normalize_clip', Mock(side_effect=RuntimeError('bad source')))
    run = Mock()
    monkeypatch.setattr(render, '_run', run)
    events = []
    with pytest.raises(RuntimeError, match='bad source'):
        render.render_video('voice', ['clip'], 'Words.', tmp_path / 'final.mp4',
                            progress_callback=lambda *row: events.append(row))
    assert events == [('segments', 0, 1)]
    run.assert_not_called()


def test_worker_reports_real_counts_below_upload_stage():
    source = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source.read_text())
    definition = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == 'report_render_progress')
    stage = Mock()
    namespace = {'set_stage': stage, 'self': object(), 'task_id': 'current-job'}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
    callback = namespace['report_render_progress']
    for completed in range(31):
        callback('segments', completed, 30)
    for phase in ('assembly', 'audio', 'checks'):
        callback(phase, 30, 30)
    records = [call.args for call in stage.call_args_list]
    assert all(row[1:3] == ('current-job', 'render') for row in records)
    assert [row[3] for row in records] == sorted(row[3] for row in records)
    assert records[0][3] == 76 and records[-1][3] == 90
    assert '30/30' in records[30][4]
    render_call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name) and n.func.id == 'render_video')
    assert any(k.arg == 'progress_callback' and isinstance(k.value, ast.Name)
               and k.value.id == definition.name for k in render_call.keywords)
