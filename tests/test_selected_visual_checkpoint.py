"""Offline exact-byte preservation and GET-only candidate eligibility."""
import ast
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest

from app.services import audio_checkpoint, selected_visual_checkpoint as checkpoint
from app.services import visual_allocation_checkpoint


TASK = '11111111-1111-4111-8111-111111111111'
ROOT = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
MP4 = b'\x00\x00\x00\x18ftypisom' + b'existing stock or generated video' * 100
MP3 = b'ID3' + b'existing narration' * 150
REAL_DURATION = checkpoint._duration


class MemoryStorage:
    def __init__(self):
        self.objects, self.puts, self.gets = {}, [], []
        self.fail_at = None
        self.length_override = None
        self.streams = []

    def put_object(self, **request):
        self.puts.append(request)
        assert request['IfNoneMatch'] == '*' and request['CacheControl'] == 'private, no-store'
        assert 'ACL' not in request
        if len(self.puts) == self.fail_at:
            raise RuntimeError('SECRET provider payload /private/path?key=SECRET')
        if request['Key'] in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        body = request['Body'].read()
        assert len(body) == request['ContentLength']
        assert hashlib.sha256(body).hexdigest() == request['Metadata']['sha256']
        self.objects[request['Key']] = (body, request['ContentType'])
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}

    def get_object(self, **request):
        self.gets.append(request['Key'])
        data, content_type = self.objects[request['Key']]
        body = io.BytesIO(data)
        self.streams.append(body)
        return {'Body': body, 'ContentLength': len(data) if self.length_override is None else self.length_override,
                'ContentType': content_type}


@pytest.fixture
def case(tmp_path, monkeypatch):
    factory = tmp_path / 'youtube_factory'
    work = factory / f'{TASK}_attempt_0'
    work.mkdir(parents=True)
    monkeypatch.setattr(visual_allocation_checkpoint, '_WORK_ROOT', factory)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    client = MemoryStorage()
    monkeypatch.setattr(checkpoint.storage, '_client', lambda: client)
    monkeypatch.setattr(checkpoint.storage, 'settings', SimpleNamespace(bucket='private-fixture-bucket'))
    measured = [27.4]
    monkeypatch.setattr(checkpoint, '_duration', lambda path: measured[0] if path.suffix == '.mp3' else 8.0)
    audio = tmp_path / f'{TASK}.mp3'
    audio.write_bytes(MP3)
    scenes, visuals, reviews = [], [], {}
    for index in range(6):
        path = work / f'scene-{index}.mp4'
        path.write_bytes(MP4 + bytes([index]))
        scenes.append({'index': index, 'narration': f'Anlatım sahnesi {index}.', 'ai_prompt': f'Original shot {index}',
                       'transition': 'cut', 'extra_server_field': {'preserve': index}})
        generated = index % 2 == 1
        visuals.append([{'path': str(path), 'generated': generated,
                         'source_type': 'generated' if generated else 'stock',
                         **({'generation_provider': 'runway', 'generation_provider_attempts': 2}
                            if generated else {'stock_provider': 'pexels', 'pexels_id': 100 + index}),
                         'start_fraction': 0.0 if generated else .3, 'preserve_start_fraction': generated,
                         'forbid_loop': generated, 'source_media_type': 'video',
                         'url': 'https://private.invalid/?token=SECRET'}])
        reviews[index] = {'scene_index': index, 'score': 92 if index < 3 else 50,
                          'best_candidate_index': 0, 'best_start_fraction': .7,
                          'reason': 'The described subject is visible.', 'subject_visible': True,
                          'evidence_gate_passed': index < 3, 'authorization': 'SECRET'}
    package = {'title': 'Dondurulmuş bölüm', 'scenes': scenes, 'narration': ' '.join(s['narration'] for s in scenes),
               'studio_options': {'mode': 'production', 'format': 'shorts'}, 'server_extra': {'keep': [1, 2, 3]},
               '_recovered_voice': {'prior': 'metadata'}, '_recovered_generated_media': {'prior': 'metadata'}}
    binding = {'source_task_id': TASK, 'lineage_id': ROOT, 'channel_id': CHANNEL,
               'connection_id': 'connection_AAAAA', 'ancestry_sha256': 'a' * 64}
    args = dict(binding=binding, package=package,
                voice_result={'path': str(audio), 'spoken_texts': [s['narration'] for s in scenes],
                              'scene_durations': [27.4 / 6] * 6, 'duration_before_fit': 27.4,
                              'duration_after_fit': 27.4, 'tempo_rate': 1.0,
                              'voice_name': 'existing-voice', 'voice_language_code': 'tr',
                              'content_target_seconds': 27.4, 'reserved_tail_seconds': 0.0},
                scene_visuals=visuals, final_reviews=reviews, rejected_scene_indices=[3, 4, 5],
                quality_threshold=86, options={'mode': 'production', 'format': 'shorts', 'music': 'off'},
                effective_edit_target_seconds=28.0, duration_minutes=.5)
    return SimpleNamespace(args=args, client=client, work=work, audio=audio, measured=measured)


def persist(case, **changes):
    return checkpoint.persist_selected_visual_checkpoint(TASK, case.work, **{**case.args, **changes})


def load(case, pointer):
    return checkpoint.load_selected_visual_checkpoint(pointer, expected_binding=case.args['binding'],
                                                       expected_package_sha256=pointer['package_sha256'])


def manifest(case, pointer):
    return json.loads(case.client.objects[pointer['manifest_key']][0])


def repoint(case, pointer, record):
    payload = checkpoint._json(record)
    digest = hashlib.sha256(payload).hexdigest()
    key = f'selected_candidates/{TASK}/manifests/{digest}.json'
    case.client.objects[key] = (payload, 'application/json')
    return {**pointer, 'manifest_key': key, 'manifest_sha256': digest, 'manifest_size': len(payload)}


@pytest.mark.parametrize('rejected', [[5], [3, 4, 5], list(range(6))])
def test_any_complete_partition_preserves_actual_bytes_without_approval(case, rejected):
    case.args['rejected_scene_indices'] = rejected
    for index, review in case.args['final_reviews'].items():
        review['score'] = 50 if index in rejected else 92
    before = deepcopy(case.args)
    pointer = persist(case)
    put_count = len(case.client.puts)
    record = load(case, pointer)
    assert len(case.client.puts) == put_count == 8  # audio, six exact assets, manifest last
    assert record['status'] == 'verified_selected_candidates'
    for value in (pointer, record):
        assert all(type(value[key]) is type(expected) and value[key] == expected
                   for key, expected in checkpoint.FLAGS.items())
    assert record['rejected_scene_indices'] == rejected
    assert record['accepted_candidate_indices'] == [i for i in range(6) if i not in rejected]
    assert case.args == before
    assert record['binding'] == before['binding']
    assert case.client.objects[record['audio']['key']][0] == MP3
    for index, scene in enumerate(record['scenes']):
        assert case.client.objects[scene['asset']['key']][0] == MP4 + bytes([index])
        assert scene['selected_edit_identity'] == checkpoint._edit_identity(record, scene)
    serialized = checkpoint._json(record).decode()
    assert 'SECRET' not in serialized and str(case.work) not in serialized
    assert all(body.closed for body in case.client.streams)


def test_package_hash_matches_real_worker_projection_and_preserves_extra_fields(case):
    tree = ast.parse((Path(__file__).parents[1] / 'app/tasks.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_recovery_package_sha256')
    namespace = {'hashlib': hashlib, 'json': json, 'FinalVisualQualityError': RuntimeError}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual-worker-hash>', 'exec'), namespace)
    pointer = persist(case)
    saved = manifest(case, pointer)
    assert pointer['package_sha256'] == namespace['_recovery_package_sha256'](case.args['package'])
    assert saved['package']['server_extra'] == {'keep': [1, 2, 3]}
    assert saved['package']['scenes'][0]['extra_server_field'] == {'preserve': 0}
    assert not {'_recovered_voice', '_recovered_generated_media'} & set(saved['package'])


def test_shortened_edit_keeps_actual_timeline_cut_speed_and_tail(case):
    record = manifest(case, persist(case))
    assert record['timing'] == {'requested_seconds': 30, 'voice_duration_seconds': 27.4,
        'effective_edit_target_seconds': 28.0, 'fps': 30, 'voice_frames': 822,
        'target_frames': 840, 'tail_pad_frames': 18}
    stock, generated = record['scenes'][:2]
    assert stock['selection']['start_fraction'] == .7
    assert stock['cut']['start_seconds'] > 0
    assert generated['selection']['start_fraction'] == 0
    assert generated['cut']['start_seconds'] == 0
    assert generated['cut']['playback_speed'] == pytest.approx(1.014)
    assert generated['cut']['frame_start'] == stock['cut']['frame_count']
    assert generated['selection']['generation_provider_attempts'] == 2
    assert generated['cut']['shot_index'] == generated['cut']['crop_selector_index'] == 1
    assert record['renderer']['source_sha256'] == hashlib.sha256(Path(checkpoint.render.__file__).read_bytes()).hexdigest()
    assert record['renderer']['adaptive_letterbox_crop_verified'] is False


def test_actual_best_pool_member_and_collapsed_singleton_are_both_preserved(case):
    other = case.work / 'second.mp4'
    other.write_bytes(MP4 + b'second-selected')
    case.args['scene_visuals'][0].append({**case.args['scene_visuals'][0][0], 'path': str(other)})
    case.args['final_reviews'][0]['best_candidate_index'] = 1
    # A singleton was already selected by the worker; its old QA pool index is historical.
    case.args['final_reviews'][1]['best_candidate_index'] = 2
    record = manifest(case, persist(case))
    assert record['scenes'][0]['selection']['selected_spec_index'] == 1
    assert case.client.objects[record['scenes'][0]['asset']['key']][0] == other.read_bytes()
    assert record['scenes'][1]['selection']['selected_spec_index'] == 0
    assert record['scenes'][1]['qa_observation']['best_candidate_index'] == 2
    assert record['qa_input_verified'] is False


def test_identical_preservation_is_idempotent_but_changed_bytes_get_new_objects(case):
    first = persist(case)
    original_objects = deepcopy(case.client.objects)
    assert persist(case) == first
    assert case.client.objects == original_objects
    Path(case.args['scene_visuals'][0][0]['path']).write_bytes(MP4 + b'changed existing candidate')
    second = persist(case)
    assert second['manifest_key'] != first['manifest_key']
    assert all(case.client.objects[key] == value for key, value in original_objects.items())


@pytest.mark.parametrize('field', ['source_task_id', 'lineage_id', 'channel_id', 'connection_id', 'ancestry_sha256', 'package'])
def test_changed_expected_identity_blocks_before_storage(case, field):
    pointer = persist(case)
    binding = dict(case.args['binding'])
    package_sha = pointer['package_sha256']
    if field in {'source_task_id', 'lineage_id'}:
        binding[field] = '33333333-3333-4333-8333-333333333333'
    elif field == 'channel_id': binding[field] = 'UCgvESYtYbn2w9R2ExBOF_cw'
    elif field == 'connection_id': binding[field] = 'connection_BBBBB'
    elif field == 'ancestry_sha256': binding[field] = 'b' * 64
    else: package_sha = 'b' * 64
    before = list(case.client.gets)
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='ineligible'):
        checkpoint.load_selected_visual_checkpoint(pointer, expected_binding=binding,
                                                    expected_package_sha256=package_sha)
    assert case.client.gets == before


@pytest.mark.parametrize('kind', ['audio', 'video', 'manifest'])
@pytest.mark.parametrize('damage', ['missing', 'changed', 'content_type', 'oversized'])
def test_missing_or_corrupted_objects_never_become_eligible(case, kind, damage):
    pointer = persist(case)
    record = manifest(case, pointer)
    key = (record['audio']['key'] if kind == 'audio' else record['scenes'][0]['asset']['key']
           if kind == 'video' else pointer['manifest_key'])
    raw, mime = case.client.objects[key]
    if damage == 'missing': del case.client.objects[key]
    elif damage == 'changed': case.client.objects[key] = (raw[:-1] + b'X', mime)
    elif damage == 'content_type': case.client.objects[key] = (raw, 'text/plain')
    else: case.client.objects[key] = (raw + b'EXTRA', mime)
    before = len(case.client.puts)
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='ineligible'):
        load(case, pointer)
    assert len(case.client.puts) == before
    assert all(body.closed for body in case.client.streams)


@pytest.mark.parametrize('damage', ['partition', 'score', 'audio_binding', 'scene_index', 'cut', 'identity',
                                  'package', 'voice_timing', 'qa_approved', 'qa_input_verified', 'unknown_field'])
def test_internally_changed_manifest_is_rejected_even_with_updated_manifest_address(case, damage):
    pointer = persist(case)
    record = manifest(case, pointer)
    if damage == 'partition': record['rejected_scene_indices'] = [3, 4]
    elif damage == 'score': record['scenes'][0]['qa_observation']['score'] = 1
    elif damage == 'audio_binding': record['audio']['key'] = 'elsewhere/audio.mp3'
    elif damage == 'scene_index': record['scenes'][1]['scene_index'] = 0
    elif damage == 'cut': record['scenes'][1]['cut']['start_seconds'] = 1.0
    elif damage == 'identity': record['scenes'][1]['selected_edit_identity'] = 'b' * 64
    elif damage == 'package': record['package']['scenes'][0]['narration'] = 'Changed narration'
    elif damage == 'voice_timing': record['voice']['scene_durations'][0] += 1
    elif damage == 'unknown_field': record['new_generation_allowed'] = True
    else: record[damage] = True
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='ineligible'):
        load(case, repoint(case, pointer, record))


@pytest.mark.parametrize('damage', ['mode', 'format', 'music', 'duration', 'partition_empty', 'partition_bool', 'partition_duplicate',
                                  'missing_accepted_review', 'missing_selected_file', 'symlink',
                                  'other_task_audio', 'nonfinite_package', 'target_mismatch', 'ancestry'])
def test_invalid_capture_inputs_fail_before_storage_without_mutation(case, damage):
    if damage == 'mode': case.args['options']['mode'] = 'preview'
    elif damage == 'format': case.args['options']['format'] = 'landscape'
    elif damage == 'music': case.args['options']['music'] = 'auto'
    elif damage == 'duration': case.args['duration_minutes'] = 1
    elif damage == 'partition_empty': case.args['rejected_scene_indices'] = []
    elif damage == 'partition_bool': case.args['rejected_scene_indices'] = [False, 3, 4, 5]
    elif damage == 'partition_duplicate': case.args['rejected_scene_indices'] = [3, 3, 4, 5]
    elif damage == 'missing_accepted_review': del case.args['final_reviews'][0]
    elif damage == 'missing_selected_file': Path(case.args['scene_visuals'][0][0]['path']).unlink()
    elif damage == 'symlink':
        original = Path(case.args['scene_visuals'][0][0]['path'])
        link = case.work / 'alias.mp4'
        link.symlink_to(original)
        case.args['scene_visuals'][0][0]['path'] = str(link)
    elif damage == 'other_task_audio': case.args['voice_result']['path'] = str(case.work / 'another.mp3')
    elif damage == 'nonfinite_package': case.args['package']['bad'] = float('inf')
    elif damage == 'target_mismatch': case.args['effective_edit_target_seconds'] = 25.0
    else: case.args['binding']['ancestry_sha256'] = 'not-a-sha'
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='unavailable'):
        persist(case)
    assert case.client.puts == []
    assert not list(case.work.glob('selected_checkpoint_*'))


def test_missing_rejected_review_cannot_publish_an_incomplete_checkpoint(case):
    del case.args['final_reviews'][3]
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='unavailable'):
        persist(case)
    assert case.client.puts == []


@pytest.mark.parametrize('review', [None, {}, {'score': None}, {'score': float('nan')}, {'score': True},
                                  {'score': 40, 'scene_index': 2}])
def test_missing_or_misbound_rejected_observation_is_unavailable(case, review):
    case.args['final_reviews'][3] = review
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='unavailable'):
        persist(case)
    assert case.client.puts == []


def test_image_motion_or_historical_manual_pass_never_grants_automated_reuse(case):
    case.args['scene_visuals'][1][0].update(synthetic_motion_only=True, source_media_type='image',
                                         generation_provider='gemini_image_motion',
                                         motion_recipe_version='diagonal-push-v2')
    case.args['final_reviews'][1].update(manual_qa_pass=True, pass_for_publication=True)
    record = load(case, persist(case))
    assert record['scenes'][1]['selection']['synthetic_motion_only'] is True
    assert record['scenes'][1]['selection']['motion_recipe_version'] == 'diagonal-push-v2'
    assert record['reusable'] is record['accepted'] is record['qa_approved'] is record['publish_eligible'] is False
    assert record['exact_cut_qa_required'] is True
    assert 'manual_qa_pass' not in record['scenes'][1]['qa_observation']


def test_changed_renderer_recipe_blocks_loader_without_any_write(case, monkeypatch, tmp_path):
    pointer = persist(case)
    changed = tmp_path / 'changed-render.py'
    changed.write_text('# another recipe')
    monkeypatch.setattr(checkpoint.render, '__file__', str(changed))
    before = len(case.client.puts)
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='ineligible'):
        load(case, pointer)
    assert len(case.client.puts) == before


def test_real_ffmpeg_media_uses_probed_byte_duration_and_refuses_false_voice_metadata(case, monkeypatch):
    video = case.work / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=30',
                    '-t', '8', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', str(video)],
                   check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=22050',
                    '-t', '27.4', '-c:a', 'libmp3lame', '-threads', '1', '-y', str(case.audio)],
                   check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setattr(checkpoint, '_duration', REAL_DURATION)
    for pool in case.args['scene_visuals']:
        pool[0].update(path=str(video), source_duration=999.0)
    pointer = persist(case)
    record = load(case, pointer)
    assert record['timing']['voice_duration_seconds'] == pytest.approx(REAL_DURATION(case.audio))
    assert all(scene['cut']['source_duration_seconds'] == pytest.approx(8.0) for scene in record['scenes'])
    assert len({scene['asset']['key'] for scene in record['scenes']}) == 1
    assert case.client.objects[record['audio']['key']][0] == case.audio.read_bytes()
    before = len(case.client.puts)
    case.args['voice_result']['duration_after_fit'] = 26.0
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='unavailable'):
        persist(case)
    assert len(case.client.puts) == before


@pytest.mark.parametrize('failed_put', [1, 4, 8])
def test_storage_failure_never_returns_a_pointer_or_leaks_private_errors(case, failed_put):
    case.client.fail_at = failed_put
    with pytest.raises(checkpoint.SelectedVisualCheckpointError) as error:
        persist(case)
    assert str(error.value) == 'selected_visual_checkpoint_unavailable'
    assert not any('/manifests/' in key for key in case.client.objects)


def test_existing_object_conflict_checks_actual_bytes_not_metadata(case):
    pointer = persist(case)
    key = manifest(case, pointer)['audio']['key']
    case.client.objects[key] = (MP3[:-1] + b'X', 'audio/mpeg')
    with pytest.raises(checkpoint.SelectedVisualCheckpointError, match='unavailable'):
        persist(case)


def test_service_has_no_dispatch_generation_or_financial_mutation_entrypoints():
    tree = ast.parse(Path(checkpoint.__file__).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    forbidden = {'render_video', 'normalize_clip', 'review_scene_visuals', 'paid_post', 'paid_response',
                 'paid_runway_create', 'reserve', 'initialize', 'initialize_scene_plan', 'initialize_funding',
                 'save_repair_checkpoint', 'update_job', 'apply_async', 'delay', 'upload_file'}
    assert all((node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                if isinstance(node.func, ast.Attribute) else '') not in forbidden for node in calls)
