"""Offline V6 byte identity, local isolation and exact-edit preparation."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.services import selected_visual_checkpoint as checkpoint, selected_visual_recovery as recovery
from test_selected_visual_checkpoint import (
    MP3, MP4, REAL_DURATION, TASK, case as selected_case, manifest, persist, repoint,
)


CHILD = '33333333-3333-4333-8333-333333333333'


@pytest.fixture
def case(selected_case):
    child_work = selected_case.work.parent / f'{CHILD}_attempt_0'
    child_work.mkdir()
    return SimpleNamespace(saved=selected_case, work=child_work)


def contracts(case, **changes):
    pointer = persist(case.saved, **changes)
    media = {'version': 6, 'repair_only': True, 'source_task_id': TASK,
             'package_sha256': pointer['package_sha256'], 'selected_checkpoint': pointer,
             'repair_scene_indices': list(changes.get('rejected_scene_indices', [3, 4, 5]))}
    voice = {key: deepcopy(media[key]) for key in ('version', 'source_task_id', 'package_sha256', 'selected_checkpoint')}
    case.saved.client.gets.clear()
    return media, voice


def materialize(case, media, voice, **changes):
    return recovery.load_selected_recovery(media, voice, CHILD, case.work,
        **{'expected_binding': case.saved.args['binding'], **changes})


@pytest.mark.parametrize('rejected', [[5], [3, 4, 5], list(range(6))])
def test_roundtrip_preserves_every_raw_byte_once_and_exact_partition(case, rejected):
    for index, review in case.saved.args['final_reviews'].items():
        review['score'] = 50 if index in rejected else 92
    media, voice = contracts(case, rejected_scene_indices=rejected)
    before, puts = deepcopy((media, voice, case.saved.args)), len(case.saved.client.puts)
    loaded = materialize(case, media, voice)
    assert loaded['repair_scene_indices'] == rejected
    retained = [i for i in range(6) if i not in rejected]
    assert loaded['retained_generated_indices'] == [i for i in retained if i % 2]
    assert loaded['retained_stock_indices'] == [i for i in retained if not i % 2]
    assert loaded['package'] == loaded['manifest']['package']
    assert checkpoint._sha(loaded['package']) == media['package_sha256']
    assert loaded['voice_result']['voice_name'] == 'existing-voice'
    assert loaded['voice_result']['spoken_texts'] == case.saved.args['voice_result']['spoken_texts']
    assert Path(loaded['voice_result']['path']).read_bytes() == MP3
    assert loaded['timing']['voice_duration_seconds'] == 27.4
    assert loaded['timing']['target_frames'] == 840
    assert loaded['timing']['tail_pad_frames'] == 18
    for index, scene in enumerate(loaded['manifest']['scenes']):
        raw = case.work / 'selected_recovery' / Path(scene['asset']['key']).name
        assert raw.read_bytes() == MP4 + bytes([index])  # rejected originals also retained
        if index in rejected:
            assert loaded['scene_visuals'][index] == []
        else:
            spec = loaded['scene_visuals'][index][0]
            assert spec == {**scene['selection'], 'path': str(raw),
                            'curated_pinned': True, 'selected_recovery_pinned': True}
            assert 'url' not in spec and 'SECRET' not in repr(spec)
    assert len(case.saved.client.gets) == len(set(case.saved.client.gets)) == 8
    assert all(body.closed for body in case.saved.client.streams)
    assert len(case.saved.client.puts) == puts and (media, voice, case.saved.args) == before
    assert loaded['qa_approved'] is loaded['publish_eligible'] is loaded['reusable'] is False
    assert loaded['requires_full_qa'] is True


def test_repeated_object_is_downloaded_and_probed_once(case, monkeypatch):
    for pool in case.saved.args['scene_visuals']:
        pool[0]['path'] = case.saved.args['scene_visuals'][0][0]['path']
    media, voice = contracts(case)
    calls = []
    monkeypatch.setattr(checkpoint, '_duration', lambda path: calls.append(path) or (27.4 if path.suffix == '.mp3' else 8.0))
    loaded = materialize(case, media, voice)
    assert len(case.saved.client.gets) == 3  # manifest, one audio, one video
    assert len(calls) == 2
    assert loaded['scene_visuals'][0][0]['path'] == loaded['scene_visuals'][1][0]['path']


@pytest.mark.parametrize('damage', ['extra', 'version_bool', 'empty', 'duplicate', 'reorder', 'bool_index',
                                  'out_of_range', 'source', 'package', 'flags', 'pointer_extra', 'pointer_path'])
def test_pure_contract_is_strict_and_performs_no_storage(case, damage):
    media, voice = contracts(case)
    if damage == 'extra': media['budget_allowance'] = 1
    elif damage == 'version_bool': media['version'] = True
    elif damage == 'empty': media['repair_scene_indices'] = []
    elif damage == 'duplicate': media['repair_scene_indices'] = [3, 3, 4]
    elif damage == 'reorder': media['repair_scene_indices'] = [5, 4, 3]
    elif damage == 'bool_index': media['repair_scene_indices'] = [True]
    elif damage == 'out_of_range': media['repair_scene_indices'] = [6]
    elif damage == 'source': media['source_task_id'] = CHILD
    elif damage == 'package': media['package_sha256'] = 'f' * 64
    elif damage == 'flags': media['selected_checkpoint']['reusable'] = True
    elif damage == 'pointer_extra': media['selected_checkpoint']['approval'] = True
    elif damage == 'pointer_path': media['selected_checkpoint']['manifest_key'] = 'https://outside.invalid/manifest'
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        recovery.validate_selected_visual_recovery(media, 6, voice['package_sha256'])
    assert case.saved.client.gets == []


def test_validator_does_not_share_nested_caller_data_and_voice_rejects_extra(case):
    media, voice = contracts(case)
    checked = recovery.validate_selected_visual_recovery(media, 6, media['package_sha256'])
    checked['selected_checkpoint']['binding']['connection_id'] = 'changed_connection'
    assert media['selected_checkpoint']['binding']['connection_id'] == 'connection_AAAAA'
    assert recovery.validate_selected_voice_recovery(voice, 6, media['package_sha256']) == voice
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        recovery.validate_selected_voice_recovery({**voice, 'key': 'new-voice'}, 6, media['package_sha256'])


@pytest.mark.parametrize('field', ['source_task_id', 'lineage_id', 'channel_id', 'connection_id', 'ancestry_sha256'])
def test_independent_source_binding_is_required_before_any_get(case, field):
    media, voice = contracts(case)
    binding = deepcopy(case.saved.args['binding'])
    binding[field] = {'source_task_id': CHILD, 'lineage_id': CHILD, 'channel_id': 'UC' + 'A' * 22,
                      'connection_id': 'other_connection', 'ancestry_sha256': 'f' * 64}[field]
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        materialize(case, media, voice, expected_binding=binding)
    assert case.saved.client.gets == []


@pytest.mark.parametrize('damage', ['expanded_repair', 'different_voice_pointer', 'package', 'partition', 'renderer'])
def test_manifest_binding_and_frozen_partition_cannot_be_rewritten(case, damage, monkeypatch, tmp_path):
    media, voice = contracts(case)
    if damage == 'expanded_repair': media['repair_scene_indices'] = [2, 3, 4, 5]
    elif damage == 'different_voice_pointer': voice['selected_checkpoint']['manifest_sha256'] = 'f' * 64
    elif damage == 'renderer':
        changed = tmp_path / 'new-renderer.py'
        changed.write_text('another renderer')
        monkeypatch.setattr(checkpoint.render, '__file__', str(changed))
    else:
        record = manifest(case.saved, media['selected_checkpoint'])
        if damage == 'package': record['package']['scenes'][0]['narration'] += ' changed'
        else: record['rejected_scene_indices'] = [2, 3, 4, 5]
        pointer = repoint(case.saved, media['selected_checkpoint'], record)
        media['selected_checkpoint'], voice['selected_checkpoint'] = pointer, deepcopy(pointer)
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        materialize(case, media, voice)
    assert not (case.work / 'selected_recovery').exists()


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'wrong_mime', 'wrong_size', 'wrong_duration'])
@pytest.mark.parametrize('kind', ['audio', 'rejected_video'])
def test_bad_saved_object_never_leaves_partial_render_inputs(case, damage, kind, monkeypatch):
    media, voice = contracts(case)
    record = manifest(case.saved, media['selected_checkpoint'])
    descriptor = record['audio'] if kind == 'audio' else record['scenes'][5]['asset']
    key = descriptor['key']
    data, mime = case.saved.client.objects[key]
    if damage == 'missing': del case.saved.client.objects[key]
    elif damage == 'corrupt': case.saved.client.objects[key] = (data[:-1] + b'?', mime)
    elif damage == 'wrong_mime': case.saved.client.objects[key] = (data, 'application/octet-stream')
    elif damage == 'wrong_size': case.saved.client.length_override = 1
    else:
        monkeypatch.setattr(checkpoint, '_duration', lambda path: 20.0 if path.name == Path(key).name
                            else (27.4 if path.suffix == '.mp3' else 8.0))
    with pytest.raises(recovery.SelectedVisualRecoveryError, match='^selected_visual_recovery_unavailable$'):
        materialize(case, media, voice)
    assert not (case.work / 'selected_recovery').exists()
    assert all(body.closed for body in case.saved.client.streams)


@pytest.mark.parametrize('damage', ['existing_directory', 'symlink_directory', 'outside', 'source_directory'])
def test_local_no_overwrite_and_child_scope(case, damage, tmp_path):
    media, voice = contracts(case)
    destination = case.work / 'selected_recovery'
    marker = tmp_path / 'keep'
    marker.write_text('original')
    work = case.work
    if damage == 'existing_directory':
        destination.mkdir()
        (destination / 'keep').write_text('original')
    elif damage == 'symlink_directory': destination.symlink_to(tmp_path, target_is_directory=True)
    elif damage == 'outside': work = tmp_path
    else: work = case.saved.work
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        recovery.load_selected_recovery(media, voice, CHILD, work, expected_binding=case.saved.args['binding'])
    assert marker.read_text() == 'original'
    if damage == 'existing_directory': assert (destination / 'keep').read_text() == 'original'
    if damage == 'symlink_directory': assert destination.is_symlink()


@pytest.mark.parametrize('fields', [
    {'synthetic_motion_only': True, 'source_media_type': 'image', 'generation_provider': 'gemini_image_motion'},
    {'generated': True, 'source_type': 'stock'},
    {'generated': False, 'source_type': 'generated'},
    {'generated': True, 'source_type': 'generated', 'generation_provider': 'pexels'},
])
def test_no_relabeling_or_image_motion_retention_from_historical_high_score(case, fields):
    case.saved.args['scene_visuals'][1][0].update(fields)
    case.saved.args['final_reviews'][1]['manual_qa_pass'] = True
    media, voice = contracts(case)
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        materialize(case, media, voice)
    assert not (case.work / 'selected_recovery').exists()


@pytest.fixture
def fake_normalizer(monkeypatch):
    calls, frames, tails = [], {}, []
    def normalize(spec, output, duration, index, transition, resolution):
        calls.append((deepcopy(spec), output, duration, index, transition, resolution))
        output.write_bytes(MP4 + bytes([index]))
        frames[str(output)] = round(duration * 30)
        return str(output)
    def run(command, **kwargs):
        tails.append(command)
        Path(command[-1]).write_bytes(MP4 + b'qa tail')
        frames[command[-1]] = int(command[command.index('-frames:v') + 1])
    monkeypatch.setattr(recovery.render, 'normalize_clip', normalize)
    monkeypatch.setattr(recovery.render, 'video_frame_count', lambda path: frames.get(str(path), 0))
    monkeypatch.setattr(recovery.subprocess, 'run', run)
    return SimpleNamespace(calls=calls, frames=frames, tails=tails)


def replacements(case, loaded):
    pools = deepcopy(loaded['scene_visuals'])
    for index in loaded['repair_scene_indices']:
        output = case.work / f'runway_s{index:02d}.mp4'
        output.write_bytes(MP4 + b'new replacement' + bytes([index]))
        pools[index] = [{'path': str(output), 'start_fraction': 0.0, 'preserve_start_fraction': True,
                         'forbid_loop': True, 'generated': True, 'source_type': 'generated',
                         'generation_provider': 'runway', 'generation_provider_attempts': 1}]
    return pools


def test_retained_exact_inputs_never_replace_raw_specs_or_import_historical_approval(case, fake_normalizer):
    loaded = materialize(case, *contracts(case))
    before = deepcopy(loaded)
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work)
    assert loaded == before and set(exact['scene_visuals']) == {0, 1, 2}
    assert [call[3] for call in fake_normalizer.calls] == [0, 1, 2]
    assert exact['frame_counts'] == [137] * 6
    assert fake_normalizer.tails == []  # last scene is an empty repair slot
    for index, pool in exact['scene_visuals'].items():
        raw = loaded['scene_visuals'][index][0]
        assert pool[0]['selected_recovery_qa_only'] is True
        assert pool[0]['path'] != raw['path'] and 'selected_recovery_qa_only' not in raw
        assert fake_normalizer.calls[index][0] == raw
        proof = exact['qa_inputs'][index]
        assert proof['qa_approved'] is False and proof['frame_count'] == 137
        assert proof['qa_sha256'] == hashlib.sha256(Path(pool[0]['path']).read_bytes()).hexdigest()
        assert proof['selected_edit_identity'] == loaded['manifest']['scenes'][index]['selected_edit_identity']
    assert exact['qa_approved'] is exact['reusable'] is False


@pytest.mark.parametrize('target', [27.4, 28.0, 30.0])
def test_full_exact_final_uses_original_voice_cut_then_master_tail(case, fake_normalizer, target):
    loaded = materialize(case, *contracts(case, effective_edit_target_seconds=target))
    pools = replacements(case, loaded)
    before = deepcopy(pools)
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
    assert pools == before and set(exact['scene_visuals']) == set(range(6))
    assert all(call[2] == 137 / 30 for call in fake_normalizer.calls)
    assert sum(row['frame_count'] for row in exact['qa_inputs'].values()) == round(target * 30)
    assert exact['qa_inputs'][5]['tail_pad_frames'] == round((target - 27.4) * 30)
    assert exact['qa_inputs'][5]['selected_edit_identity'] is None
    if target > 27.4:
        assert f'tpad=stop_mode=clone:stop={round((target - 27.4) * 30)}' in fake_normalizer.tails[0][
            fake_normalizer.tails[0].index('-vf') + 1]


@pytest.mark.parametrize('damage', ['raw_bytes', 'raw_symlink', 'voice_bytes', 'voice_timing', 'narration',
                                  'repair_expansion', 'raw_fraction', 'qa_as_final', 'retained_replaced',
                                  'new_stock', 'outside_replacement', 'multiple_replacements', 'unexpected_metadata'])
def test_local_or_final_input_drift_blocks_before_normalization(case, fake_normalizer, damage, tmp_path):
    loaded = materialize(case, *contracts(case))
    pools = replacements(case, loaded)
    if damage == 'raw_bytes': Path(loaded['scene_visuals'][0][0]['path']).write_bytes(MP4 + b'changed')
    elif damage == 'raw_symlink':
        raw = Path(loaded['scene_visuals'][0][0]['path'])
        outside = tmp_path / 'outside.mp4'
        outside.write_bytes(raw.read_bytes())
        raw.unlink()
        raw.symlink_to(outside)
    elif damage == 'voice_bytes': Path(loaded['voice_result']['path']).write_bytes(MP3 + b'changed')
    elif damage == 'voice_timing': loaded['voice_result']['scene_durations'][0] += .1
    elif damage == 'narration': loaded['package']['scenes'][0]['narration'] += ' changed'
    elif damage == 'repair_expansion': loaded['repair_scene_indices'] = [2, 3, 4, 5]
    elif damage == 'raw_fraction': pools[0][0]['start_fraction'] = .9
    elif damage == 'qa_as_final': pools[3][0]['selected_recovery_qa_only'] = True
    elif damage == 'retained_replaced': pools[0] = deepcopy(pools[3])
    elif damage == 'new_stock': pools[3][0].update(generated=False, source_type='stock')
    elif damage == 'outside_replacement':
        raw = tmp_path / 'outside.mp4'
        raw.write_bytes(MP4)
        pools[3][0]['path'] = str(raw)
    elif damage == 'multiple_replacements': pools[3].append(deepcopy(pools[3][0]))
    else: pools[3][0]['private_url'] = 'https://private.invalid/?key=SECRET'
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
    assert fake_normalizer.calls == []
    assert not list(case.work.glob('selected_exact_qa_*'))


def test_normalization_failure_removes_only_qa_copies_and_preserves_originals(case, fake_normalizer, monkeypatch):
    loaded = materialize(case, *contracts(case))
    originals = {path: path.read_bytes() for path in (case.work / 'selected_recovery').iterdir()}
    def fail(spec, output, *args):
        output.write_bytes(MP4)
        raise RuntimeError('SECRET /private/input.mp4')
    monkeypatch.setattr(recovery.render, 'normalize_clip', fail)
    with pytest.raises(recovery.SelectedVisualRecoveryError, match='^selected_visual_recovery_exact_cut_unavailable$'):
        recovery.prepare_selected_exact_visuals(loaded, case.work)
    assert not list(case.work.glob('selected_exact_qa_*'))
    assert {path: path.read_bytes() for path in originals} == originals


def test_empty_retained_partition_prepares_no_qa_or_paid_work(case, fake_normalizer):
    loaded = materialize(case, *contracts(case, rejected_scene_indices=list(range(6))))
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work)
    assert exact['scene_visuals'] == exact['qa_inputs'] == {}
    assert fake_normalizer.calls == [] and loaded['repair_scene_indices'] == list(range(6))


def test_twelve_scene_source_uses_its_full_original_partition(case, fake_normalizer):
    args = case.saved.args
    for index in range(6, 12):
        scene = deepcopy(args['package']['scenes'][index - 6])
        scene.update(index=index, narration=f'Original narration {index}.')
        args['package']['scenes'].append(scene)
        args['scene_visuals'].append(deepcopy(args['scene_visuals'][index - 6]))
        args['final_reviews'][index] = {**deepcopy(args['final_reviews'][index - 6]), 'scene_index': index,
                                        'score': 92 if index < 9 else 50}
    for index in range(6): args['final_reviews'][index]['score'] = 92
    texts = [scene['narration'] for scene in args['package']['scenes']]
    args['package']['narration'] = ' '.join(texts)
    args['voice_result'].update(spoken_texts=texts, scene_durations=[27.4 / 12] * 12)
    loaded = materialize(case, *contracts(case, rejected_scene_indices=[9, 10, 11]))
    assert loaded['repair_scene_indices'] == [9, 10, 11]
    assert len(loaded['scene_visuals']) == 12
    assert len(loaded['retained_generated_indices']) + len(loaded['retained_stock_indices']) == 9
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work)
    assert set(exact['scene_visuals']) == set(range(9)) and sum(exact['frame_counts']) == 822


def test_target_trim_matches_last_visible_master_frames(case, fake_normalizer):
    # A voice may exceed the frame-aligned target by <= .08 sec. The final
    # master trims the tail, never changes the original narration or cut speed.
    loaded = materialize(case, *contracts(case, effective_edit_target_seconds=27.333333333333332))
    pools = replacements(case, loaded)
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
    assert sum(row['frame_count'] for row in exact['qa_inputs'].values()) == 820
    assert exact['qa_inputs'][5]['frame_count'] == 135
    assert exact['qa_inputs'][5]['cut']['frame_count'] == 137
    assert 'tpad=' not in fake_normalizer.tails[0][fake_normalizer.tails[0].index('-vf') + 1]


def test_final_identity_check_is_read_only_and_does_not_repeat_normalization(case, fake_normalizer):
    loaded = materialize(case, *contracts(case))
    pools = replacements(case, loaded)
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
    before = len(fake_normalizer.calls), len(case.saved.client.gets), deepcopy(pools)
    assert recovery.verify_selected_final_inputs(loaded, case.work, pools, exact['qa_inputs']) is None
    assert (len(fake_normalizer.calls), len(case.saved.client.gets), pools) == before
    assert type(exact['qa_inputs']) is recovery.SelectedQAInputs
    assert '/tmp/' not in repr(dict(exact['qa_inputs']))
    assert exact['qa_inputs'][5]['qa_approved'] is False


@pytest.mark.parametrize('value', [True, False, 1, 'true'])
def test_new_generated_worker_pin_is_exact_boolean_and_bound_to_final_proof(case, fake_normalizer, value):
    loaded = materialize(case, *contracts(case))
    pools = replacements(case, loaded)
    pools[3][0]['curated_pinned'] = value
    if value is not True:
        with pytest.raises(recovery.SelectedVisualRecoveryError):
            recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
        assert fake_normalizer.calls == []
    else:
        exact = recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
        assert exact['qa_inputs'][3]['selection']['curated_pinned'] is True
        recovery.verify_selected_final_inputs(loaded, case.work, pools, exact['qa_inputs'])
        del pools[3][0]['curated_pinned']
        with pytest.raises(recovery.SelectedVisualRecoveryError):
            recovery.verify_selected_final_inputs(loaded, case.work, pools, exact['qa_inputs'])


@pytest.mark.parametrize('damage', ['plain_dict', 'omitted_scene', 'revised_record', 'qa_bytes',
                                  'raw_retained', 'raw_replacement', 'raw_fraction', 'voice_bytes',
                                  'voice_metadata', 'qa_as_final', 'frame_count', 'renderer'])
def test_final_identity_check_rejects_post_qa_changes(case, fake_normalizer, damage, monkeypatch, tmp_path):
    loaded = materialize(case, *contracts(case))
    pools = replacements(case, loaded)
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work, scene_visuals=pools)
    proof = exact['qa_inputs']
    if damage == 'plain_dict': proof = dict(proof)
    elif damage == 'omitted_scene': del proof[5]
    elif damage == 'revised_record': proof[5]['qa_approved'] = True
    elif damage == 'qa_bytes': Path(exact['scene_visuals'][5][0]['path']).write_bytes(MP4 + b'changed QA')
    elif damage == 'raw_retained': Path(pools[0][0]['path']).write_bytes(MP4 + b'changed raw')
    elif damage == 'raw_replacement': Path(pools[3][0]['path']).write_bytes(MP4 + b'changed replacement')
    elif damage == 'raw_fraction': pools[3][0]['start_fraction'] = .5
    elif damage == 'voice_bytes': Path(loaded['voice_result']['path']).write_bytes(MP3 + b'changed speech')
    elif damage == 'voice_metadata': loaded['voice_result']['spoken_texts'][0] += ' changed'
    elif damage == 'qa_as_final': pools[3][0]['path'] = exact['scene_visuals'][3][0]['path']
    elif damage == 'frame_count': fake_normalizer.frames[exact['scene_visuals'][3][0]['path']] += 1
    else:
        file = tmp_path / 'changed-renderer.py'
        file.write_text('different recipe')
        monkeypatch.setattr(checkpoint.render, '__file__', str(file))
    before = len(fake_normalizer.calls)
    with pytest.raises(recovery.SelectedVisualRecoveryError, match='^selected_visual_recovery_final_inputs_changed$'):
        recovery.verify_selected_final_inputs(loaded, case.work, pools, proof)
    assert len(fake_normalizer.calls) == before


def test_retained_only_preparation_cannot_satisfy_all_scene_final_identity(case, fake_normalizer):
    loaded = materialize(case, *contracts(case))
    retained = recovery.prepare_selected_exact_visuals(loaded, case.work)
    pools = replacements(case, loaded)
    with pytest.raises(recovery.SelectedVisualRecoveryError):
        recovery.verify_selected_final_inputs(loaded, case.work, pools, retained['qa_inputs'])


def test_real_ffmpeg_short_edit_preserves_sources_and_counts_tail_frames(case, monkeypatch):
    video = case.saved.work / 'real-source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30',
                    '-t', '2', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', str(video)],
                   check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=22050',
                    '-t', '2.4', '-c:a', 'libmp3lame', '-threads', '1', '-y', str(case.saved.audio)],
                   check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(checkpoint, '_duration', REAL_DURATION)
    measured = REAL_DURATION(case.saved.audio)
    case.saved.args['voice_result'].update(scene_durations=[measured / 6] * 6,
        duration_before_fit=measured, duration_after_fit=measured, content_target_seconds=measured)
    for pool in case.saved.args['scene_visuals']:
        pool[0]['path'] = str(video)
    for index, review in case.saved.args['final_reviews'].items():
        review['score'] = 92 if index == 5 else 50
    loaded = materialize(case, *contracts(case, rejected_scene_indices=list(range(5)),
                                         effective_edit_target_seconds=2.6))
    source = Path(loaded['scene_visuals'][5][0]['path'])
    before = (source.read_bytes(), Path(loaded['voice_result']['path']).read_bytes())
    exact = recovery.prepare_selected_exact_visuals(loaded, case.work)
    output = Path(exact['scene_visuals'][5][0]['path'])
    expected = 78 - loaded['manifest']['scenes'][5]['cut']['frame_start']
    assert recovery.render.video_frame_count(output) == expected
    assert exact['qa_inputs'][5]['frame_count'] == expected
    assert exact['qa_inputs'][5]['tail_pad_frames'] == 78 - round(measured * 30)
    assert source.read_bytes() == before[0]
    assert Path(loaded['voice_result']['path']).read_bytes() == before[1]
    assert len(case.saved.client.gets) == 3
