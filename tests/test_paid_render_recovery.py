import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from botocore.exceptions import ClientError
import fakeredis
import pytest

from app.services import audio_checkpoint, paid_render_recovery as recovery, studio_state
from test_recovered_media import _load_recovery_boundary


SOURCE = '11111111-1111-4111-8111-111111111111'
PREP = '22222222-2222-4222-8222-222222222222'
CHILD = '33333333-3333-4333-8333-333333333333'


def _task_runtime():
    namespace = _load_recovery_boundary()
    source = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_normalized_options']
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    return SimpleNamespace(**namespace)


@pytest.fixture
def case(tmp_path, monkeypatch):
    runtime = _task_runtime()
    server = fakeredis.FakeServer()
    redis = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(studio_state, '_client', lambda: redis)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    monkeypatch.setattr(recovery.storage.settings, 'bucket', 'private-test-bucket', raising=False)
    audio = b'ID3' + b'old-voice' * 512
    clip = b'\x00\x00\x00\x18ftyp' + b'old-generated-clip' * 512
    voice_path = tmp_path / f'{SOURCE}.mp3'
    voice_path.write_bytes(audio)
    package = {'title': 'Banknot kâğıdı', 'scenes': [
        {'narration': f'Banknot için sahne {index}.', 'visual_queries': ['US dollar bill'], 'ai_prompt': None}
        for index in range(6)
    ], 'sources': [{'url': 'https://www.bep.gov/currency/how-money-is-made',
                    'evidence': 'Currency paper is 75 percent cotton and 25 percent linen.'}]}
    voice = {
        'path': str(voice_path), 'scene_durations': [4.85] * 6,
        'spoken_texts': [scene['narration'] for scene in package['scenes']],
        'duration_before_fit': 29.1, 'duration_after_fit': 29.1, 'tempo_rate': 1.0,
        'content_target_seconds': 29.5, 'reserved_tail_seconds': 0.5,
        'voice_name': 'Saved voice', 'voice_model': 'existing', 'voice_language_code': 'tr',
    }
    objects = {}
    monkeypatch.setattr(audio_checkpoint, 'upload_file', lambda path, key, _kind: objects.__setitem__(key, Path(path).read_bytes()))
    audio_pointer = audio_checkpoint.persist_audio_candidate_checkpoint(SOURCE, package, voice)['audio_candidate_checkpoint']
    options = runtime._normalized_options({
        'mode': 'production', 'format': 'shorts', 'music': 'off',
        'publish_after_render': True, 'production_channel_id': 'frozen-channel',
        'production_connection_id': 'frozen-connection', 'production_profile_revision': 'frozen-revision',
    }, 0.5)
    source = {
        'task_id': SOURCE, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
        'failure_stage': 'render', 'error': 'Final alignment failed', 'updated_at': '2026-09-05T00:00:00Z',
        'spec': {'topic': 'Banknot kâğıdı', 'language': 'tr', 'duration_minutes': 0.5, 'channel_id': 'channel-profile', **options},
        'audio_candidate_checkpoint': audio_pointer, 'paid_create_slots_used': 1,
        'preview_total_paid_create_cap': 4, 'prepaid_visual_diagnostics': {
            'stage': 'before_paid_allocation', 'paid_create_cap': 4, 'paid_slots_used': 0,
            'scenes': [{'scene_index': index, 'requires_paid_replacement': index == 3,
                        'score': 72 if index == 3 else 88, 'has_visual': True} for index in range(6)],
        },
    }
    redis.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(source))
    redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': '4', 'used': '1'})
    manifest = {
        'version': 1, 'source_task_id': SOURCE, 'status': 'unapproved_candidate',
        'qa_approved': False, 'requires_full_qa': True, 'provider': 'gemini_veo',
        'operation_name': 'models/veo-3.1-lite-generate-preview/operations/existing-operation',
        'scene_index': 3, 'clip_key': f'recovery/{SOURCE}/raw/scene-03-initial.mp4',
        'clip_sha256': hashlib.sha256(clip).hexdigest(), 'clip_size': len(clip),
        'duration_seconds': 6.0, 'video_frames': 144,
        'source_audio_metadata_sha256': audio_pointer['metadata_sha256'],
        'source_audio_package_sha256': audio_pointer['package_sha256'],
        'source_paid_create_slots_used': 1, 'new_paid_create_requests': 0,
        'provenance': 'Existing operation and file observed for the unique source paid scene.',
    }
    objects[manifest['clip_key']] = clip
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    pointer = {'manifest_key': f'recovery/{SOURCE}/provider_retrieval_v1.json',
               'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'manifest_size': len(manifest_bytes)}
    objects[pointer['manifest_key']] = manifest_bytes
    gets, puts, bodies = [], [], []

    def get_object(*, Bucket, Key):
        assert Bucket == 'private-test-bucket'
        gets.append(Key)
        body = io.BytesIO(objects[Key])
        bodies.append(body)
        return {'Body': body, 'ContentLength': len(objects[Key])}

    def put_object(*, Bucket, Key, Body, ContentType, IfNoneMatch):
        assert Bucket == 'private-test-bucket' and ContentType == 'audio/mpeg' and IfNoneMatch == '*'
        puts.append(Key)
        if Key in objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        objects[Key] = Body.read()
        return {'ETag': 'discarded-response'}

    storage_client = SimpleNamespace(get_object=get_object, put_object=put_object)
    monkeypatch.setattr(recovery.storage, '_client', lambda: storage_client)
    def revalidate(candidate, topic, duration, language, supplied_options, **kwargs):
        assert topic == source['spec']['topic'] and duration == 0.5 and language == 'tr'
        assert kwargs['immutable_candidate_narrations'] == [scene['narration'] for scene in candidate['scenes']]
        assert 'short_story_qc' not in candidate and 'stock_scene_qc' not in candidate
        return {**deepcopy(candidate), 'studio_options': deepcopy(supplied_options),
                'short_story_qc': {'fresh_test_attestation': True}}
    review = Mock(side_effect=revalidate)
    approved = Mock(side_effect=lambda value, _topic: value.get('short_story_qc') == {'fresh_test_attestation': True})
    director = SimpleNamespace(revalidate_immutable_short_story=review, short_story_package_is_approved=approved)
    voice_module = SimpleNamespace(normalize_turkish_tts=lambda text, **_kwargs: text)
    monkeypatch.setattr(recovery, '_runtime', lambda: (runtime, director, voice_module))
    work = tmp_path / 'youtube_factory' / f'{PREP}_attempt_0'
    work.mkdir(parents=True)
    return SimpleNamespace(redis=redis, server=server, source=source, manifest=manifest, pointer=pointer,
                           objects=objects, audio=audio, clip=clip, work=work, runtime=runtime,
                           review=review, approved=approved, gets=gets, puts=puts, bodies=bodies,
                           storage=storage_client)


def _prepare(case):
    return recovery.prepare_paid_render_recovery(SOURCE, case.pointer, case.work)


def _save_source(case):
    case.redis.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(case.source))


def _save_manifest(case):
    data = json.dumps(case.manifest, sort_keys=True).encode()
    case.objects[case.pointer['manifest_key']] = data
    case.pointer.update(manifest_sha256=hashlib.sha256(data).hexdigest(), manifest_size=len(data))


def _redis_snapshot(case):
    return {key: case.redis.dump(key) for key in case.redis.keys()}


def test_prepare_uses_only_existing_assets_fresh_critic_and_hash_bound_v3(case):
    before = _redis_snapshot(case)
    checkpoint = _prepare(case)
    assert _redis_snapshot(case) == before
    assert checkpoint['new_paid_create_requests'] == checkpoint['new_tts_requests'] == 0
    assert checkpoint['requires_full_qa'] is True
    package = checkpoint['approved_package']
    media = package['_recovered_generated_media']
    voice = package['_recovered_voice']
    assert media['version'] == 3 and media['recovery_only'] is True
    assert 'repair_scene_indices' not in media
    assert media['package_sha256'] == voice['package_sha256'] == checkpoint['package_sha256']
    entry = media['scenes']['3'][0]
    assert entry['sha256'] == hashlib.sha256(case.clip).hexdigest()
    assert entry['size'] == len(case.clip)
    assert entry['provider'] == 'gemini_veo'
    assert voice['sha256'] == hashlib.sha256(case.audio).hexdigest()
    assert case.objects[voice['key']] == case.audio
    assert case.puts == [f'recovery/{SOURCE}/raw/voice.mp3']
    assert package['studio_options']['production_profile_revision'] == 'frozen-revision'
    assert package['studio_options']['production_connection_id'] == 'frozen-connection'
    case.review.assert_called_once()
    assert all(body.closed for body in case.bodies)
    assert 'ETag' not in str(checkpoint)
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


@pytest.mark.parametrize('damage', [
    'state', 'kind', 'failure_stage', 'music', 'duration', 'format', 'channel', 'connection',
    'revision', 'retry_child', 'retry_claimed', 'repair_claimed', 'repair_available',
    'paid_zero', 'paid_two', 'paid_counter', 'paid_cap', 'missing_ledger', 'corrupt_ledger',
    'retry_dispatch_key', 'checkpoint_key', 'claim_key',
])
def test_source_preflight_rejects_before_asset_download_or_critic(case, damage):
    source = case.source
    if damage in {'state', 'kind', 'failure_stage'}: source[damage] = 'wrong'
    if damage == 'music': source['spec']['music'] = 'auto'
    if damage == 'duration': source['spec']['duration_minutes'] = 1
    if damage == 'format': source['spec']['format'] = 'landscape'
    for key in ('channel', 'connection', 'revision'):
        if damage == key: source['spec'].pop({'channel':'production_channel_id','connection':'production_connection_id','revision':'production_profile_revision'}[key])
    if damage == 'retry_child': source['retry_child_task_id'] = CHILD
    if damage in {'retry_claimed','repair_claimed','repair_available'}: source[damage] = True
    budget_key = studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE
    if damage in {'paid_zero','paid_two'}: case.redis.hset(budget_key, 'used', '0' if damage == 'paid_zero' else '2')
    if damage == 'paid_counter': source['paid_create_slots_used'] = True
    if damage == 'paid_cap': source['preview_total_paid_create_cap'] = 3
    if damage == 'missing_ledger': case.redis.delete(budget_key)
    if damage == 'corrupt_ledger': case.redis.hset(budget_key, 'used', 'invalid')
    for key, prefix in [('retry_dispatch_key',studio_state.RETRY_DISPATCH_PREFIX),
                        ('checkpoint_key',studio_state.REPAIR_CHECKPOINT_PREFIX),
                        ('claim_key',studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX)]:
        if damage == key: case.redis.set(prefix + SOURCE, 'existing')
    _save_source(case)
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): _prepare(case)
    assert case.gets == case.puts == []
    case.review.assert_not_called()
    assert _redis_snapshot(case) == before


@pytest.mark.parametrize('field,value', [
    ('version', True), ('source_task_id', PREP), ('status', 'approved'),
    ('qa_approved', True), ('requires_full_qa', False), ('provider', 'runway'),
    ('operation_name', 'https://other/operation'), ('scene_index', 2), ('scene_index', True),
    ('clip_key', 'other-object'), ('clip_sha256', 'wrong'), ('clip_size', 0),
    ('source_audio_metadata_sha256', 'f'*64), ('source_audio_package_sha256', 'e'*64),
    ('source_paid_create_slots_used', 2), ('new_paid_create_requests', 1),
    ('duration_seconds', 0), ('video_frames', 119),
])
def test_manifest_must_bind_unapproved_existing_source_and_unique_scene(case, field, value):
    case.manifest[field] = value
    _save_manifest(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): _prepare(case)
    case.review.assert_not_called()
    assert case.puts == []


@pytest.mark.parametrize('damage', ['manifest_hash', 'manifest_size', 'manifest_key', 'clip_hash', 'clip_size', 'probe_duration', 'probe_frames', 'multiple_scenes', 'missing_scene', 'wrong_audio'])
def test_hash_probe_and_allocation_failures_never_invoke_paid_critic_or_copy(case, damage):
    if damage == 'manifest_hash': case.pointer['manifest_sha256'] = 'f'*64
    if damage == 'manifest_size': case.pointer['manifest_size'] += 1
    if damage == 'manifest_key': case.pointer['manifest_key'] = f'recovery/{PREP}/provider_retrieval_v1.json'
    if damage == 'clip_hash': case.objects[case.manifest['clip_key']] = case.clip[:-1] + b'x'
    if damage == 'clip_size': case.objects[case.manifest['clip_key']] = case.clip + b'x'
    if damage == 'probe_duration': case.runtime.media_duration = lambda _path: 6.2
    if damage == 'probe_frames': case.runtime.video_frame_count = lambda _path: 150
    if damage == 'multiple_scenes': case.source['prepaid_visual_diagnostics']['scenes'][2]['requires_paid_replacement'] = True
    if damage == 'missing_scene': case.source['prepaid_visual_diagnostics']['scenes'].pop()
    if damage == 'wrong_audio': case.objects[case.source['audio_candidate_checkpoint']['audio_key']] = b'ID3' + b'x'*4096
    _save_source(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): _prepare(case)
    case.review.assert_not_called()
    assert case.puts == []


@pytest.mark.parametrize('damage', ['critic_error', 'unapproved', 'narration_change', 'options_change', 'source_changed'])
def test_fresh_story_failures_cannot_be_replaced_with_old_approval(case, damage):
    original = case.review.side_effect
    def changed(*args, **kwargs):
        out = original(*args, **kwargs)
        if damage == 'critic_error': raise RuntimeError('SECRET provider body')
        if damage == 'unapproved': out.pop('short_story_qc')
        if damage == 'narration_change': out['scenes'][0]['narration'] = 'Different text.'
        if damage == 'options_change': out['studio_options']['production_channel_id'] = 'other'
        if damage == 'source_changed':
            case.source['spec']['production_profile_revision'] = 'new-revision'
            _save_source(case)
        return out
    case.review.side_effect = changed
    with pytest.raises(recovery.PaidRenderRecoveryError) as error: _prepare(case)
    assert 'SECRET' not in str(error.value)
    assert case.puts == []
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


@pytest.mark.parametrize('matches', [True, False])
def test_existing_private_voice_is_never_overwritten(case, matches):
    key = f'recovery/{SOURCE}/raw/voice.mp3'
    original = case.audio if matches else b'ID3' + b'x'*(len(case.audio)-3)
    case.objects[key] = original
    if matches: assert _prepare(case)['new_tts_requests'] == 0
    else:
        with pytest.raises(recovery.PaidRenderRecoveryError): _prepare(case)
    assert case.objects[key] == original


def test_explicit_publication_is_atomic_nx_and_preserves_source_and_budget(case):
    checkpoint = _prepare(case)
    prior = deepcopy(case.source)
    budget = case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE)
    result = recovery.publish_paid_render_recovery(checkpoint)
    assert result == {'status':'checkpoint_published','source_task_id':SOURCE,'requires_full_qa':True}
    stored = studio_state.get_job(SOURCE)
    assert stored.pop('repair_available') is True
    assert stored.pop('updated_at') != prior.pop('updated_at')
    assert stored == prior
    assert json.loads(case.redis.get(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)) == checkpoint
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == budget
    assert not case.redis.exists(studio_state.RETRY_DISPATCH_PREFIX + SOURCE)
    assert case.redis.ttl(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE) > 0
    assert case.review.call_count == 1  # Publishing never calls a remote critic.


@pytest.mark.parametrize('mutation', ['spec','stage','error','result','used','cap','missing_budget','checkpoint','claim','dispatch'])
def test_publication_rejects_changed_source_or_existing_claim_without_writes(case, mutation):
    checkpoint = _prepare(case)
    if mutation == 'spec': case.source['spec']['production_channel_id'] = 'other'
    if mutation in {'stage','error'}: case.source[mutation] = 'changed'
    if mutation == 'result': case.source['result'] = {'changed':True}
    _save_source(case)
    if mutation in {'used','cap'}: case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE, mutation, '2')
    if mutation == 'missing_budget': case.redis.delete(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE)
    for name, prefix in [('checkpoint',studio_state.REPAIR_CHECKPOINT_PREFIX),('claim',studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX),('dispatch',studio_state.RETRY_DISPATCH_PREFIX)]:
        if mutation == name: case.redis.set(prefix+SOURCE, 'existing-claim')
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_render_recovery(checkpoint)
    assert _redis_snapshot(case) == before


@pytest.mark.parametrize('target', ['job','budget','checkpoint','claim','dispatch'])
def test_real_watch_conflict_aborts_once_without_overwriting_other_writer(case, monkeypatch, target):
    checkpoint = _prepare(case)
    keys = {'job':studio_state.JOB_PREFIX, 'budget':studio_state.PAID_CREATE_BUDGET_PREFIX,
            'checkpoint':studio_state.REPAIR_CHECKPOINT_PREFIX, 'claim':studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
            'dispatch':studio_state.RETRY_DISPATCH_PREFIX}
    other = fakeredis.FakeRedis(server=case.server, decode_responses=True)
    original_pipeline = case.redis.pipeline
    executions = []
    def racing_pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute
        def execute():
            executions.append(True)
            if target == 'budget': other.hset(keys[target]+SOURCE, 'used', '2')
            elif target == 'job': other.set(keys[target]+SOURCE, json.dumps({**case.source, 'error':'concurrent-change'}))
            else: other.set(keys[target]+SOURCE, 'concurrent-claim')
            return original_execute()
        pipe.execute = execute
        return pipe
    monkeypatch.setattr(case.redis, 'pipeline', racing_pipeline)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_render_recovery(checkpoint)
    assert executions == [True]
    assert not studio_state.get_job(SOURCE).get('repair_available')
    if target != 'checkpoint': assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+SOURCE)
    else: assert case.redis.get(studio_state.REPAIR_CHECKPOINT_PREFIX+SOURCE) == 'concurrent-claim'


def test_published_checkpoint_is_single_use_through_real_studio_retry_claim(case):
    checkpoint = _prepare(case)
    recovery.publish_paid_render_recovery(checkpoint)
    dispatch = studio_state.claim_retry_dispatch(SOURCE, CHILD, 'one-shot-token-that-is-long-enough', allow_repair=True)
    assert dispatch['claimed'] is True and dispatch['checkpoint'] == checkpoint
    duplicate = studio_state.claim_retry_dispatch(SOURCE, PREP, 'other-token-that-is-long-enough', allow_repair=True)
    assert duplicate['claimed'] is False and duplicate['child_task_id'] == CHILD
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_render_recovery(checkpoint)
    assert _redis_snapshot(case) == before
    assert studio_state.get_job(SOURCE)['retry_child_task_id'] == CHILD
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE) == {'used':'1','cap':'4'}


@pytest.mark.parametrize('damage', ['package_hash','clip_hash','fake_attestation'])
def test_publisher_rejects_receipt_tampering_without_writes(case, damage):
    checkpoint = _prepare(case)
    if damage == 'package_hash': checkpoint['package_sha256'] = 'f'*64
    if damage == 'clip_hash': checkpoint['recovered_clip_sha256'] = 'e'*64
    if damage == 'fake_attestation':
        checkpoint['approved_package']['short_story_qc'] = {'accepted':True}
        checkpoint['package_sha256'] = case.runtime._recovery_package_sha256(checkpoint['approved_package'])
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_render_recovery(checkpoint)
    assert _redis_snapshot(case) == before


def test_concurrent_normal_retry_claims_create_exactly_one_reciprocal_child(case):
    checkpoint = _prepare(case)
    recovery.publish_paid_render_recovery(checkpoint)
    def claim(child_id):
        dispatch = studio_state.claim_retry_dispatch(SOURCE, child_id, 'token-'+child_id, allow_repair=True)
        if dispatch['claimed']:
            studio_state.create_job(child_id, deepcopy(case.source['spec']), kind='render', parent_id=SOURCE)
        return child_id, dispatch
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [CHILD, PREP]))
    winners = [child_id for child_id, dispatch in outcomes if dispatch['claimed']]
    assert len(winners) == 1
    assert studio_state.get_job(SOURCE)['retry_child_task_id'] == winners[0]
    assert studio_state.get_job(winners[0])['parent_id'] == SOURCE
    assert sum(studio_state.get_job(child_id) is not None for child_id in [CHILD, PREP]) == 1
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE) == {'used':'1','cap':'4'}
