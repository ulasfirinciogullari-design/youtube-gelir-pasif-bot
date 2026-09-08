"""Offline family planning, exact frame reuse and no inherited approval."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from app.services import production_delivery as delivery
from app.services import production_derivatives as derivatives
from app.services.production_editorial import choose_production_editorial
from app.services.render import _scene_frame_windows


TASK = '11111111-1111-4111-8111-111111111111'
OPTIONS = {'mode': 'production', 'format': 'landscape', 'production_delivery': delivery.CONTRACT.copy()}


@pytest.fixture
def family(tmp_path):
    scenes = [{'narration': f'Scene {i} explains one sourced idea with twelve natural spoken words.'}
              for i in range(60)]
    package = {'scenes': scenes, 'derived_shorts': [
        {'title': f'Independent story {n}', 'description': f'Answer to question {n}.',
         'scene_indices': list(range(start, start + 4))}
        for n, start in enumerate((0, 20, 40), 1)
    ]}
    package['delivery_plan'] = delivery.bind_delivery_plan(package)
    master = tmp_path / 'master.mp4'
    master.write_bytes(b'fake master transport fixture, not reviewed media')
    rendered = {
        'resolution': '1920x1080', 'fps': 30, 'frame_count': 14400,
        'scene_synced': True, 'path': str(master),
        'scene_windows': [{'scene_index': i, 'start_frame': i * 240, 'end_frame': (i + 1) * 240}
                          for i in range(60)],
    }
    manifest = delivery.rendered_delivery_manifest(
        TASK, package, rendered, master_sha256=delivery.file_sha256(master),
    )
    return package, rendered, manifest, master


def test_old_format_choices_stay_unchanged_and_eight_minutes_is_explicit():
    topic = 'Format: landscape. Explain this topic.'
    assert choose_production_editorial(topic)['duration_minutes'] == 3
    selected = choose_production_editorial(topic, long_duration_minutes=8)
    assert selected['duration_minutes'] == 8 and selected['version'] == 2
    assert choose_production_editorial('One specific question?', long_duration_minutes=8)['duration_minutes'] == .5
    assert choose_production_editorial(topic, 'Shorts only', long_duration_minutes=8)['duration_minutes'] == .5


@pytest.mark.parametrize('value', [True, 8.0, 9, 120, '8', None])
def test_invalid_editorial_duration_cannot_expand_spend(value):
    with pytest.raises(ValueError):
        choose_production_editorial('Format: landscape.', long_duration_minutes=value)


def test_family_schema_and_writer_use_existing_scenes():
    assert delivery.shorts_schema()['minItems'] == delivery.shorts_schema()['maxItems'] == 3
    assert delivery.writer_rule({}, 3) == ''
    rule = delivery.writer_rule(OPTIONS, 8)
    assert 'mid-sentence' in rule and 'do not speed up' in rule
    assert 'second voice track' in rule and 'Return derived_shorts' in rule
    assert 'Return derived_shorts' not in delivery.writer_rule(OPTIONS, 8, select_cuts=False)


@pytest.mark.parametrize('mutate', [
    lambda o: o['production_delivery'].update(version=True),
    lambda o: o['production_delivery'].update(derived_shorts=4),
    lambda o: o['production_delivery'].update(extra=True),
    lambda o: o.update(format='shorts'),
    lambda o: o.update(mode='preview'),
])
def test_invalid_delivery_contract_is_not_a_request_to_spend(mutate):
    options = deepcopy(OPTIONS)
    mutate(options)
    with pytest.raises(delivery.DeliveryPlanError):
        delivery.delivery_requested(options, 8)


def test_bound_plan_is_deterministic_and_does_not_mutate_input(family):
    package, _, _, _ = family
    original = deepcopy(package)
    assert delivery.bind_delivery_plan(package) == package['delivery_plan']
    assert package == original
    assert len(package['delivery_plan']['shorts']) == 3
    assert len({row['narration_sha256'] for row in package['delivery_plan']['shorts']}) == 3


@pytest.mark.parametrize('mutate', [
    lambda p: p['derived_shorts'].pop(),
    lambda p: p['derived_shorts'][0].update(scene_indices=[0, 2, 3]),
    lambda p: p['derived_shorts'][0].update(scene_indices=[3, 2, 1, 0]),
    lambda p: p['derived_shorts'][0].update(scene_indices=[0, 1, 2, 2]),
    lambda p: p['derived_shorts'][0].update(scene_indices=[True, 2, 3]),
    lambda p: p['derived_shorts'][1].update(scene_indices=[2, 3, 4, 5]),
    lambda p: p['derived_shorts'][0].update(scene_indices=[57, 58, 59, 60]),
    lambda p: p['derived_shorts'][1].update(title=p['derived_shorts'][0]['title']),
    lambda p: p['derived_shorts'][0].update(title=''),
    lambda p: p['derived_shorts'][0].update(publish_eligible=True),
    lambda p: p['scenes'][3].update(narration='This unfinished sentence'),
    lambda p: p['scenes'][2].update(narration='words ' * 101 + '.'),
])
def test_incomplete_overlapping_or_unbounded_cuts_are_rejected_before_media(family, mutate):
    package, _, _, _ = family
    mutate(package)
    with pytest.raises(delivery.DeliveryPlanError):
        delivery.bind_delivery_plan(package)


def test_later_narration_edit_invalidates_all_old_cut_bindings(family):
    package, rendered, manifest, _ = family
    package['scenes'][59]['narration'] = 'Even a changed non-selected scene changes the original binding.'
    with pytest.raises(delivery.DeliveryPlanError, match='narration_changed'):
        delivery.rendered_delivery_manifest(TASK, package, rendered, master_sha256=manifest['master_sha256'])


def test_actual_rounded_render_frames_are_used_not_script_estimates(family):
    package, rendered, manifest, _ = family
    assert [cut['start_frame'] for cut in manifest['shorts']] == [0, 4800, 9600]
    assert [cut['duration_seconds'] for cut in manifest['shorts']] == [32, 32, 32]
    timeline = [('a', 1.1, 'cut', 0), ('b', .9, 'cut', 0), ('c', 1, 'cut', 1)]
    assert _scene_frame_windows(timeline, [33, 27, 30], 93) == [
        {'scene_index': 0, 'start_frame': 0, 'end_frame': 60},
        {'scene_index': 1, 'start_frame': 60, 'end_frame': 93},
    ]
    assert manifest['new_voice_generations'] == manifest['new_video_generations'] == 0
    assert manifest['qa_approved'] is manifest['publish_eligible'] is False


@pytest.mark.parametrize('mutate', [
    lambda r: r['scene_windows'][1].update(start_frame=0),
    lambda r: r['scene_windows'][1].update(end_frame=240),
    lambda r: r['scene_windows'][1].update(scene_index=3),
    lambda r: r.update(frame_count=14399),
    lambda r: r.update(fps=True),
    lambda r: r.update(scene_synced=False),
    lambda r: r.update(resolution='1080x1920'),
])
def test_bad_frame_map_never_becomes_a_valid_edit(family, mutate):
    package, rendered, manifest, _ = family
    mutate(rendered)
    with pytest.raises(delivery.DeliveryPlanError):
        delivery.rendered_delivery_manifest(TASK, package, rendered, master_sha256=manifest['master_sha256'])


def bindings(manifest):
    return {'source_task_id': TASK, 'expected_digest': manifest['manifest_sha256'],
            'expected_master_sha256': manifest['master_sha256']}


@pytest.mark.parametrize('mutate', [
    lambda m: m.update(master_key='videos/another/final.mp4'),
    lambda m: m.update(master_sha256='a' * 64),
    lambda m: m.update(qa_approved=True),
    lambda m: m.update(publish_eligible=True),
    lambda m: m['shorts'][0].update(start_frame=1),
    lambda m: m['shorts'][0].update(narration='Changed narration.'),
])
def test_tampered_manifest_never_reaches_renderer(family, mutate, monkeypatch, tmp_path):
    _, _, manifest, master = family
    bound = bindings(manifest)
    mutate(manifest)
    renderer = Mock()
    monkeypatch.setattr(derivatives, '_render_one', renderer)
    with pytest.raises(delivery.DeliveryPlanError):
        derivatives.render_candidates(master, manifest, tmp_path, **bound)
    renderer.assert_not_called()


def test_actual_master_hash_must_match_before_any_render(family, monkeypatch, tmp_path):
    _, _, manifest, master = family
    master.write_bytes(b'different media')
    renderer = Mock()
    monkeypatch.setattr(derivatives, '_render_one', renderer)
    with pytest.raises(delivery.DeliveryPlanError, match='master_changed'):
        derivatives.render_candidates(master, manifest, tmp_path, **bindings(manifest))
    renderer.assert_not_called()


@pytest.mark.parametrize('only_numbers', [None, [2, 3]])
def test_three_cuts_use_one_master_zero_paid_generations_and_no_inherited_qc(family, monkeypatch, tmp_path, only_numbers):
    _, _, manifest, master = family
    calls = []
    def render(source, cut, path):
        calls.append((source, cut['start_frame'], cut['end_frame']))
        path.write_bytes(f'fake cut {cut["number"]}'.encode())
    monkeypatch.setattr(derivatives, '_render_one', render)
    monkeypatch.setattr(derivatives, 'probe_video', lambda path: (
        {'width': 1920, 'height': 1080, 'fps': '30/1', 'frames': 14400, 'duration': 480}
        if path == master else
        {'width': 1080, 'height': 1920, 'fps': '30/1', 'frames': 960, 'duration': 32}
    ))
    results = derivatives.render_candidates(master, manifest, tmp_path / 'cuts',
                                             only_numbers=only_numbers, **bindings(manifest))
    expected = [(master, 0, 960), (master, 4800, 5760), (master, 9600, 10560)]
    assert calls == (expected if only_numbers is None else expected[1:])
    assert len(results) == (3 if only_numbers is None else 2)
    assert all(row['new_voice_generations'] == row['new_video_generations'] == 0 for row in results)
    assert all(row['manual_qa_required'] and not row['publish_eligible'] for row in results)
    assert all(row['quality_disposition'] != 'automated_qc_pass' for row in results)


@pytest.mark.parametrize('only_numbers', [[True], [2, 2], [3, 2], [0], [4], ['2'], {2, 3}, '2'])
def test_recovery_cut_selection_cannot_expand_or_repeat_work(family, monkeypatch, tmp_path, only_numbers):
    _, _, manifest, master = family
    renderer = Mock()
    monkeypatch.setattr(derivatives, '_render_one', renderer)
    with pytest.raises(delivery.DeliveryPlanError, match='cut_selection_invalid'):
        derivatives.render_candidates(master, manifest, tmp_path, only_numbers=only_numbers,
                                      **bindings(manifest))
    renderer.assert_not_called()


def test_selected_recovery_cuts_still_require_exact_master(family, monkeypatch, tmp_path):
    _, _, manifest, master = family
    master.write_bytes(b'changed parent asset')
    renderer = Mock()
    monkeypatch.setattr(derivatives, '_render_one', renderer)
    with pytest.raises(delivery.DeliveryPlanError, match='master_changed'):
        derivatives.render_candidates(master, manifest, tmp_path, only_numbers=[2, 3],
                                      **bindings(manifest))
    renderer.assert_not_called()


def test_render_filter_preserves_speed_whole_frames_and_original_audio(monkeypatch, tmp_path):
    run = Mock()
    monkeypatch.setattr(derivatives.subprocess, 'run', run)
    derivatives._render_one(tmp_path / 'source.mp4', {'start_frame': 300, 'end_frame': 1200}, tmp_path / 'cut.mp4')
    args = run.call_args.args[0]
    filters = args[args.index('-filter_complex') + 1]
    assert 'trim=start_frame=300:end_frame=1200' in filters
    assert 'atrim=start=10.000000000:end=40.000000000' in filters
    assert 'atempo' not in filters and 'setpts=PTS-STARTPTS' in filters
    assert args[args.index('-threads') + 1] == '2'
    assert '-nostdin' in args and '-n' in args


def test_export_failure_does_not_discard_or_rebuild_paid_long_master(family, tmp_path, monkeypatch):
    from app.services import storage
    package, rendered, _, master = family
    monkeypatch.setattr(storage, 'upload_file', Mock(side_effect=TimeoutError('uncertain upload')))
    result = delivery.persist_delivery_manifest(TASK, package, rendered, OPTIONS, 8, tmp_path)
    assert result == {'delivery_status': 'export_unavailable', 'delivery_error': 'delivery_export_unavailable'}
    assert master.exists()


def test_export_off_performs_no_storage_call(family, tmp_path, monkeypatch):
    from app.services import storage
    sender = Mock()
    monkeypatch.setattr(storage, 'upload_file', sender)
    assert delivery.persist_delivery_manifest(TASK, {}, {}, {}, 3, tmp_path) == {}
    sender.assert_not_called()


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg not installed')
def test_real_ffmpeg_portrait_cut_keeps_audio_frames_and_duration(tmp_path):
    # Tiny synthetic fixture, not a claim about the channel's editorial quality.
    source, output = tmp_path / 'fixture.mp4', tmp_path / 'cut.mp4'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-n',
        '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '0.6', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '2',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    derivatives._render_one(source, {'start_frame': 3, 'end_frame': 15}, output)
    media = derivatives.probe_video(output)
    assert (media['width'], media['height'], media['frames'], media['fps']) == (1080, 1920, 12, '30/1')
    assert abs(media['duration'] - .4) < .05


def test_director_cleanup_drops_old_plan_and_requires_fresh_selection(family):
    from app.services.director import _clean_package
    package, _, _, _ = family
    revised = deepcopy(package)
    revised['qc_summary'] = []
    for scene in revised['scenes']:
        scene.update(visual_queries=['moving source one', 'moving source two'], ai_prompt=None)
    out = _clean_package(revised, package)
    assert 'delivery_plan' not in out
    assert out['derived_shorts'] == revised['derived_shorts']
    revised.pop('derived_shorts')
    assert 'derived_shorts' not in _clean_package(revised, package)


def test_director_schema_supports_family_without_changing_legacy_shape():
    from app.services.director import _director_json_schema
    original = _director_json_schema(56)
    expanded = _director_json_schema(56, delivery_family=True)
    assert 'derived_shorts' not in original['properties']
    assert expanded['properties']['derived_shorts'] == delivery.shorts_schema()
    assert 'derived_shorts' in expanded['required']


def test_pipeline_binds_before_voice_and_exports_actual_render():
    import ast
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    pipeline = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
    calls = [n for n in ast.walk(pipeline) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    binding = next(n for n in calls if n.func.id == 'bind_delivery_plan')
    voice = next(n for n in calls if n.func.id == '_synthesize_voice_candidate')
    assert binding.lineno < voice.lineno
    render = next(n for n in calls if n.func.id == 'render_video')
    export = next(n for n in calls if n.func.id == 'persist_delivery_manifest')
    finish = next(n for n in calls if n.func.id == 'mark_success')
    assert render.lineno < export.lineno < finish.lineno


def test_new_scheduler_contract_is_frozen_and_guard_is_required():
    # Reuse the actual Redis/Lua scheduler fixture through its public factory.
    from test_channel_production import production, _profile, _save, CONNECTION
    module, client = production.__wrapped__()
    profile = _profile(production_topics=['Format: landscape. Explain this topic.'], channel_identity='Source-led films.')
    _save(module, client, profile)
    module.settings.studio_longform_delivery_enabled = True
    original = {key: client.dump(key) for key in client.keys('*')}
    assert module.reserve_due_production(profile, CONNECTION, now=1000)['status'] == 'delivery_budget_not_enabled'
    assert {key: client.dump(key) for key in client.keys('*')} == original
    module.settings.studio_spend_enforcement = True
    reserved = module.reserve_due_production(profile, CONNECTION, now=1000)
    assert reserved['status'] == 'reserved'
    job = json.loads(client.get(module.JOB_PREFIX + reserved['task_id']))
    assert reserved['args'][1] == job['spec']['duration_minutes'] == 8
    assert job['spec']['production_delivery'] == delivery.CONTRACT
    assert job['spec']['production_editorial']['version'] == 2
