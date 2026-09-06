import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import fakeredis
import pytest

from app.services import audio_checkpoint, failed_visual_recovery as recovery, studio_state
from test_paid_render_recovery import _task_runtime


SOURCE = recovery.SOURCE
PREP = '22222222-2222-4222-8222-222222222222'
CHILD = '33333333-3333-4333-8333-333333333333'


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _render_runtime():
    path = Path(__file__).resolve().parents[1] / 'app/services/render.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in {'_scene_timeline', '_timeline_frame_counts'}]
    namespace = {'FPS': 30, '_spec_path': lambda spec: spec.get('path'), 'math': math}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


@pytest.fixture
def case(tmp_path, monkeypatch):
    tasks, render = _task_runtime(), _render_runtime()
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_state, '_client', lambda: redis)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    monkeypatch.setattr(recovery.storage.settings, 'bucket', 'private-test', raising=False)
    audio = b'ID3' + b'actual-existing-voice' * 256
    clips = {index: b'\0\0\0\x18ftyp' + bytes([65 + index]) * 2048 for index in range(6)}
    monkeypatch.setattr(recovery, 'VOICE_SHA', _sha(audio))
    monkeypatch.setattr(recovery, 'VOICE_SIZE', len(audio))
    monkeypatch.setattr(recovery, 'SELECTED', {index: (_sha(raw), len(raw)) for index, raw in clips.items()})
    package = {'title': 'Barkodun ilk taraması', 'scenes': [
        {'index': index, 'narration': f'Barkod sahnesinin değişmeyen anlatımı {index}.', 'visual_queries': ['checkout barcode'],
         'ai_prompt': 'Observed checkout barcode', 'transition': 'dip' if index == 2 else 'cut'}
        for index in range(6)], 'sources': [{'url': 'https://www.gs1.org/about/history', 'evidence': 'A source-backed barcode fact.'}]}
    path = tmp_path / f'{SOURCE}.mp3'
    path.write_bytes(audio)
    durations = [4.50, 5.00, 4.70, 4.80, 4.90, 5.20]
    voice = {'path': str(path), 'spoken_texts': [scene['narration'] for scene in package['scenes']],
             'scene_durations': durations, 'duration_before_fit': 29.1, 'duration_after_fit': 29.1,
             'tempo_rate': 1.0, 'content_target_seconds': 29.5, 'reserved_tail_seconds': 0.5,
             'voice_name': 'existing', 'voice_model': 'existing', 'voice_language_code': 'tr'}
    objects = {}
    monkeypatch.setattr(audio_checkpoint, 'upload_file', lambda path, key, kind: objects.__setitem__(key, Path(path).read_bytes()))
    audio_pointer = audio_checkpoint.persist_audio_candidate_checkpoint(SOURCE, package, voice)['audio_candidate_checkpoint']
    options = tasks._normalized_options({'mode': 'production', 'format': 'shorts', 'music': 'off',
              'publish_after_render': True, 'production_channel_id': 'frozen-channel',
              'production_connection_id': 'frozen-connection', 'production_profile_revision': 'frozen-revision'}, 0.5)
    metadata = {'version': 1, 'task_id': SOURCE, 'status': 'qa_workprint', 'qa_approved': False,
                'reusable': False, 'publish_eligible': False,
                'voice': {'sha256': _sha(audio), 'size': len(audio), 'existing_voice_quality_passed': True},
                'scenes': [{'scene_index': index, 'narration': scene['narration'], 'duration_seconds': durations[index],
                            'selection': {'sha256': _sha(clips[index]), 'size': len(clips[index]),
                                'selected_spec_index': 1 if index in {0, 5} else 0,
                                'start_fraction': 0.5 if index == 5 else 0.0, 'forbid_loop': True,
                                **({'stock_provider': 'pexels', 'pexels_id': 38052460} if index == 5
                                   else {'generation_provider': 'gemini_veo'})},
                            'review': {'score': [40, 95, 92, 90, 92, 78][index]}}
                           for index, scene in enumerate(package['scenes'])]}
    raw_metadata = _encoded(metadata)
    monkeypatch.setattr(recovery, 'WORKPRINT_SHA', _sha(raw_metadata))
    monkeypatch.setattr(recovery, 'WORKPRINT_SIZE', len(raw_metadata))
    metadata_key = f'qa_workprints/{SOURCE}/{recovery.WORKPRINT_SHA}.json'
    objects[metadata_key] = raw_metadata
    source = {'task_id': SOURCE, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
              'failure_stage': 'final_visual_qc_rescue', 'result': {}, 'error': 'Historical rejected video',
              'updated_at': '2026-09-05T00:00:00Z', 'parent_id': '44444444-4444-4444-8444-444444444444',
              'spec': {'topic': 'Barkod tarihi', 'language': 'tr', 'duration_minutes': 0.5, 'channel_id': 'profile', **options},
              'paid_create_slots_used': 6, 'preview_total_paid_create_cap': 6, 'audio_candidate_checkpoint': audio_pointer,
              'qa_workprint': {'version': 1, 'task_id': SOURCE, 'status': 'qa_workprint', 'qa_approved': False,
                              'reusable': False, 'publish_eligible': False, 'metadata_key': metadata_key,
                              'metadata_sha256': recovery.WORKPRINT_SHA, 'metadata_size': len(raw_metadata)}}
    redis.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(source))
    redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': '6', 'used': '6'})
    # Ancestors are not read, rewritten, reset or claimed by this leaf adapter.
    ancestor = source['parent_id']
    redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + ancestor, 'consumed-ancestor-claim')
    redis.hset(studio_state.RETRY_DISPATCH_PREFIX + ancestor, mapping={'state': 'dispatched', 'child_task_id': SOURCE})
    manifest = {'version': 1, 'source_task_id': SOURCE, 'status': 'unapproved_preservation',
                'diagnostic_only': True, 'qa_approved': False, 'reusable': False, 'requires_full_qa': True,
                'source_state_sha256': recovery._digest({'source': {key: source.get(key) for key in recovery._RETRIEVAL_SOURCE_FIELDS},
                                                       'ledger': {'cap': '6', 'used': '6'}}),
                'source_spec_sha256': recovery._digest(source['spec']), 'workprint_metadata_key': metadata_key,
                'workprint_metadata_sha256': recovery.WORKPRINT_SHA, 'workprint_metadata_size': len(raw_metadata),
                'voice_sha256': _sha(audio), 'voice_size': len(audio),
                'source_audio_metadata_sha256': audio_pointer['metadata_sha256'],
                'source_audio_package_sha256': audio_pointer['package_sha256'],
                'source_paid_create_slots_used': 6, 'source_paid_create_cap': 6,
                'new_paid_create_requests': 0, 'new_tts_requests': 0, 'preserved_raw_scene_indices': [1, 2, 3, 4],
                'rejected_selected_scene_indices': [0, 5], 'stock_scene_five_not_downloaded': True, 'voice_not_downloaded': True,
                'selection_proof': [{'scene_index': index, 'selection': row['selection']} for index, row in enumerate(metadata['scenes'])],
                'operations': []}
    for ordinal in range(6):
        index = ordinal if ordinal < 5 else None
        raw = clips[ordinal] if index is not None else b'\0\0\0\x18ftyp' + b'unmapped-diagnostic' * 128
        digest = _sha(raw)
        key = f'recovery/{SOURCE}/raw/scene-{index:02d}-initial.mp4' if index in recovery.RETAINED else f'recovery/{SOURCE}/diagnostic/{digest}.mp4'
        objects[key] = raw
        manifest['operations'].append({'operation_name': f'models/veo-3.1-lite-generate-preview/operations/private-op-{ordinal}',
            'provider': 'gemini_veo', 'matched_scene_index': index, 'clip_key': key, 'clip_sha256': digest,
            'clip_size': len(raw), 'duration_seconds': 6.0, 'video_frames': 144,
            'role': 'preserved_raw_candidate' if index in recovery.RETAINED else 'diagnostic_only',
            'qa_approved': False, 'reusable': False})
    gets, puts, bodies = [], [], []
    def get_object(*, Bucket, Key):
        assert Bucket == 'private-test'
        gets.append(Key)
        body = io.BytesIO(objects[Key])
        bodies.append(body)
        return {'Body': body, 'ContentLength': len(objects[Key])}
    def put_object(*, Bucket, Key, Body, ContentType, IfNoneMatch, **kwargs):
        assert Bucket == 'private-test' and IfNoneMatch == '*'
        assert not {'ACL', 'GrantRead', 'GrantFullControl'} & kwargs.keys()
        puts.append(Key)
        if Key in objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        objects[Key] = Body.read()
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}
    storage = SimpleNamespace(get_object=get_object, put_object=put_object)
    monkeypatch.setattr(recovery.storage, '_client', lambda: storage)
    def revalidate(original, topic, duration, language, supplied_options, **kwargs):
        assert kwargs['immutable_candidate_narrations'] == [scene['narration'] for scene in original['scenes']]
        assert 'short_story_qc' not in original
        return {**deepcopy(original), 'studio_options': deepcopy(supplied_options), 'short_story_qc': {'fresh_test': True}}
    story_review = Mock(side_effect=revalidate)
    approved = Mock(side_effect=lambda pkg, topic: pkg.get('short_story_qc') == {'fresh_test': True})
    director = SimpleNamespace(revalidate_immutable_short_story=story_review, short_story_package_is_approved=approved)
    voice_module = SimpleNamespace(normalize_turkish_tts=lambda text, **kwargs: text)
    monkeypatch.setattr(recovery, '_runtime', lambda: (tasks, director, voice_module))
    render.media_duration = lambda path: 29.1
    render.normalize_clip = Mock(side_effect=lambda spec, target, *args: target.write_bytes(b'normalized-exact-cut'))
    good = [{'scene_index': index, 'score': 90, 'best_candidate_index': 0, 'best_moment_index': 0,
             'best_start_fraction': 0.18, 'reason': 'Literal subject and action with no artifacts.',
             'evidence_gate_passed': True, 'identity_gate_passed': True, 'editorial_gate_passed': True}
            for index in range(4)]
    visual_review = Mock(return_value={'reviews': good, 'missing_review_indices': []})
    monkeypatch.setattr(recovery, '_review_runtime', lambda: (render, visual_review))
    work = tmp_path / 'youtube_factory' / f'{PREP}_attempt_0'
    work.mkdir(parents=True)
    result = SimpleNamespace(redis=redis, source=source, objects=objects, storage=storage, tasks=tasks,
                             package=package, voice=voice, manifest=manifest, metadata=metadata,
                             gets=gets, puts=puts, bodies=bodies, story_review=story_review,
                             visual_review=visual_review, good=good, approved=approved, render=render,
                             work=work, audio=audio, clips=clips, pointer={})
    _save_manifest(result)
    return result


def _save_manifest(case):
    raw = _encoded(case.manifest)
    checksum = _sha(raw)
    case.pointer = {'manifest_key': f'recovery/{SOURCE}/provider_retrieval_v1-{checksum}.json',
                    'manifest_sha256': checksum, 'manifest_size': len(raw)}
    case.objects[case.pointer['manifest_key']] = raw


def _save_source(case):
    case.redis.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(case.source))


def _snapshot(case):
    return {key: case.redis.dump(key) for key in case.redis.keys()}


def _prepare(case):
    return recovery.prepare_failed_visual_repair(SOURCE, case.pointer, case.work)


def _receipt(case, pointer):
    return json.loads(case.objects[pointer['prepared_key']])


def test_prepare_uses_four_exact_existing_cuts_current_critic_and_no_registry_writes(case):
    before = _snapshot(case)
    pointer = _prepare(case)
    receipt = _receipt(case, pointer)
    assert _snapshot(case) == before
    assert receipt['qa_approved'] is False and receipt['requires_full_qa'] is True
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    package = receipt['approved_package']
    assert package['scenes'] == case.package['scenes']
    assert package['studio_options']['production_profile_revision'] == 'frozen-revision'
    assert package['_recovered_voice']['sha256'] == _sha(case.audio)
    assert case.objects[package['_recovered_voice']['key']] == case.audio
    media = case.tasks._validated_recovered_generated_media(package['_recovered_generated_media'], 6, receipt['package_sha256'])
    assert media['version'] == 2 and media['repair_scene_indices'] == [0, 5]
    assert set(media['scenes']) == {1, 2, 3, 4}
    assert [entry[0]['sha256'] for entry in media['scenes'].values()] == [_sha(case.clips[i]) for i in range(1, 5)]
    assert not any('/diagnostic/' in key for key in case.gets)
    assert not any(key.endswith('.mp4') and 'qa_workprints/' in key for key in case.gets)
    case.story_review.assert_called_once()
    case.visual_review.assert_called_once()
    args, kwargs = case.visual_review.call_args
    assert args[0] == case.package['scenes'][1:5]
    assert kwargs['story_scenes'] == case.package['scenes']
    assert kwargs['evidence_sources'] == case.package['sources']
    assert kwargs['_missing_review_attempts'] == kwargs['_score_reason_consistency_attempts'] == 0
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list] == [1, 2, 3, 4]
    assert [round(call.args[2] * 30) for call in case.render.normalize_clip.call_args_list] == [150, 141, 144, 147]
    assert all(call.args[0]['forbid_loop'] is True and call.args[0]['start_fraction'] == 0 for call in case.render.normalize_clip.call_args_list)
    assert case.render.normalize_clip.call_args_list[1].args[4] == 'dip'
    assert all(body.closed for body in case.bodies)
    assert 'private-op' not in str(receipt) and 'https://' not in str(pointer)


def test_publish_then_normal_lua_repair_claim_has_exactly_one_child(case):
    pointer = _prepare(case)
    old = deepcopy(case.source)
    before = _snapshot(case)
    assert recovery.publish_failed_visual_repair(pointer)['status'] == 'checkpoint_published'
    current = studio_state.get_job(SOURCE)
    assert {k: v for k, v in current.items() if k not in {'updated_at', 'repair_available'}} == {k: v for k, v in old.items() if k not in {'updated_at', 'repair_available'}}
    assert current['state'] == 'FAILURE' and current['repair_available'] is True
    assert current['spec'] == old['spec'] and current['result'] == old['result']
    for key in before:
        if key != studio_state.JOB_PREFIX + SOURCE:
            assert case.redis.dump(key) == before[key]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda ordinal: studio_state.claim_retry_dispatch(
            SOURCE, CHILD if ordinal == 0 else PREP, ('a' if ordinal == 0 else 'b') * 32,
            allow_repair=True), [0, 1]))
    assert sum(item['claimed'] for item in outcomes) == 1
    chosen = next(item for item in outcomes if item['claimed'])
    assert chosen['mode'] == 'repair'
    assert chosen['checkpoint']['approved_package']['_recovered_generated_media']['repair_scene_indices'] == [0, 5]
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    snapshot = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError): recovery.publish_failed_visual_repair(pointer)
    assert _snapshot(case) == snapshot


@pytest.mark.parametrize('damage', ['state', 'kind', 'failure_stage', 'source_id', 'paid5', 'paid0', 'ledger',
    'duration', 'format', 'music', 'language', 'revision', 'voice', 'workprint', 'retry_child', 'claim', 'checkpoint', 'dispatch'])
def test_source_guard_fails_before_any_storage_or_qc(case, damage):
    if damage in {'state', 'kind', 'failure_stage'}: case.source[damage] = 'wrong'
    if damage == 'source_id': case.source['task_id'] = PREP
    if damage in {'paid5', 'paid0'}: case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5' if damage == 'paid5' else '0')
    if damage == 'ledger': case.redis.delete(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE)
    for field in ('duration', 'format', 'music', 'language'):
        if damage == field: case.source['spec']['duration_minutes' if field == 'duration' else field] = 'wrong'
    if damage == 'revision': case.source['spec'].pop('production_profile_revision')
    if damage == 'voice': case.source['audio_candidate_checkpoint']['audio_sha256'] = '0' * 64
    if damage == 'workprint': case.source['qa_workprint']['qa_approved'] = True
    if damage == 'retry_child': case.source['retry_child_task_id'] = CHILD
    for name, prefix in [('claim', studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX), ('checkpoint', studio_state.REPAIR_CHECKPOINT_PREFIX), ('dispatch', studio_state.RETRY_DISPATCH_PREFIX)]:
        if damage == name: case.redis.set(prefix + SOURCE, 'existing')
    _save_source(case)
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError): _prepare(case)
    assert case.gets == case.puts == [] and _snapshot(case) == before
    case.story_review.assert_not_called()
    case.visual_review.assert_not_called()


@pytest.mark.parametrize('repairs', [[0, 1, 5], [0, 2], [5, 0], [False, 5], [], [0, 5, 5]])
def test_no_other_repair_or_scope_expansion(case, repairs):
    with pytest.raises(recovery.FailedVisualRecoveryError): recovery.prepare_failed_visual_repair(SOURCE, case.pointer, case.work, repair_scene_indices=repairs)
    assert case.gets == case.puts == []


@pytest.mark.parametrize('damage', ['hash', 'size', 'key', 'manifest_flags', 'source_fingerprint', 'mapping',
    'duplicate_operation', 'operation_url', 'raw_hash', 'raw_size', 'voice_bytes', 'metadata_bytes', 'probe', 'frames'])
def test_tampered_evidence_fails_before_paid_review_or_copy(case, damage):
    if damage == 'hash': case.pointer['manifest_sha256'] = '0' * 64
    if damage == 'size': case.pointer['manifest_size'] += 1
    if damage == 'key': case.pointer['manifest_key'] = 'https://outside.invalid/private'
    if damage == 'manifest_flags': case.manifest['qa_approved'] = True
    if damage == 'source_fingerprint': case.manifest['source_state_sha256'] = '0' * 64
    if damage == 'mapping': case.manifest['operations'][2]['matched_scene_index'] = 5
    if damage == 'duplicate_operation': case.manifest['operations'][2]['operation_name'] = case.manifest['operations'][1]['operation_name']
    if damage == 'operation_url': case.manifest['operations'][1]['operation_name'] = 'https://outside.invalid/key'
    if damage in {'manifest_flags', 'source_fingerprint', 'mapping', 'duplicate_operation', 'operation_url'}: _save_manifest(case)
    if damage in {'raw_hash', 'raw_size'}:
        key = case.manifest['operations'][2]['clip_key']
        case.objects[key] = case.objects[key][:-1] + (b'x' if damage == 'raw_hash' else b'')
    if damage == 'voice_bytes': case.objects[case.source['audio_candidate_checkpoint']['audio_key']] = b'ID3' + b'x' * 5000
    if damage == 'metadata_bytes': case.objects[case.source['qa_workprint']['metadata_key']] += b' '
    if damage == 'probe': case.tasks.media_duration = lambda path: 5.9
    if damage == 'frames': case.tasks.video_frame_count = lambda path: 150
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError): _prepare(case)
    assert _snapshot(case) == before and case.puts == []
    case.story_review.assert_not_called()
    case.visual_review.assert_not_called()


@pytest.mark.parametrize('damage', ['narration', 'order', 'approval', 'options'])
def test_story_must_be_fresh_approved_and_immutable(case, damage):
    original = case.story_review.side_effect
    def changed(*args, **kwargs):
        package = original(*args, **kwargs)
        if damage == 'narration': package['scenes'][2]['narration'] += ' Changed.'
        if damage == 'order': package['scenes'].reverse()
        if damage == 'approval': package.pop('short_story_qc')
        if damage == 'options': package['studio_options']['music'] = 'auto'
        return package
    case.story_review.side_effect = changed
    with pytest.raises(recovery.FailedVisualRecoveryError): _prepare(case)
    case.visual_review.assert_not_called()
    assert case.puts and all('failed_visual_audit_v1-' in key for key in case.puts)


@pytest.mark.parametrize('damage', ['score', 'evidence', 'identity', 'editorial', 'missing', 'duplicate', 'candidate'])
def test_suspect_scene_two_never_authorizes_new_creates_or_checkpoint(case, damage):
    row = case.good[1]
    row['reason'] = 'Printed package front has a major visual artifact.'
    if damage == 'score': row['score'] = 40
    if damage in {'evidence', 'identity', 'editorial'}: row[damage + '_gate_passed'] = False
    if damage == 'missing': case.good.pop(1)
    if damage == 'duplicate': row['scene_index'] = 0
    if damage == 'candidate': row['best_candidate_index'] = 1
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught: _prepare(case)
    assert _snapshot(case) == before and case.puts
    assert all('failed_visual_audit_v1-' in key for key in case.puts)
    if damage not in {'missing', 'duplicate'}:
        assert caught.value.diagnostics[0]['scene_index'] == 2
        assert 'artifact' in caught.value.diagnostics[0]['review']['reason']
    assert 'private-op' not in str(caught.value)


def test_diagnostic_reason_redacts_provider_credentials(case):
    case.good[1].update(score=40, reason='Bearer private-secret https://private.invalid/path')
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught: _prepare(case)
    assert caught.value.diagnostics[0]['review']['reason'] == '[redacted]'


@pytest.mark.parametrize('damage', ['changed_source', 'paid_ledger', 'claim', 'checkpoint', 'stale_attestation', 'tampered_receipt'])
def test_publication_rechecks_durable_state_and_stored_receipt(case, damage):
    pointer = _prepare(case)
    if damage == 'changed_source': case.source['error'] = 'A later state'; _save_source(case)
    if damage == 'paid_ledger': case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '7')
    if damage == 'claim': case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'racing-token')
    if damage == 'checkpoint': case.redis.set(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE, 'existing-checkpoint')
    if damage == 'stale_attestation': case.approved.side_effect = lambda *args: False
    if damage == 'tampered_receipt': case.objects[pointer['prepared_key']] += b' '
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError): recovery.publish_failed_visual_repair(pointer)
    assert _snapshot(case) == before


def test_watch_conflict_preserves_competing_claim_and_source(case):
    pointer = _prepare(case)
    original = case.storage.get_object
    fired = False
    def race(**kwargs):
        nonlocal fired
        result = original(**kwargs)
        if not fired and kwargs['Key'] == case.pointer['manifest_key']:
            fired = True
            case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'racing-claim')
        return result
    case.storage.get_object = race
    with pytest.raises(recovery.FailedVisualRecoveryError): recovery.publish_failed_visual_repair(pointer)
    assert case.redis.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE) == 'racing-claim'
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)
    assert studio_state.get_job(SOURCE) == case.source


def test_concurrent_publication_is_single_use_without_reset(case):
    pointer = _prepare(case)
    def publish(_):
        try:
            return recovery.publish_failed_visual_repair(pointer)['status']
        except recovery.FailedVisualRecoveryError:
            return 'stopped'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, range(2)))
    assert sorted(outcomes) == ['checkpoint_published', 'stopped']


def test_uncertain_preparation_storage_does_not_publish(case):
    original = case.storage.put_object
    def ambiguous(**kwargs):
        result = original(**kwargs)
        if kwargs['ContentType'] == 'application/json':
            raise RuntimeError('private-storage-provider-body')
        return result
    case.storage.put_object = ambiguous
    before = _snapshot(case)
    with pytest.raises(recovery.FailedVisualRecoveryError) as caught: _prepare(case)
    assert _snapshot(case) == before
    assert 'private-storage' not in str(caught.value)


def test_preparation_directory_is_one_shot(case):
    _prepare(case)
    calls = case.story_review.call_count
    with pytest.raises(recovery.FailedVisualRecoveryError): _prepare(case)
    assert case.story_review.call_count == calls
