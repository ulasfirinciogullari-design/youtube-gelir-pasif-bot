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


CONTINUATION_PREP = '44444444-4444-4444-8444-444444444444'
GRANDCHILD = '55555555-5555-4555-8555-555555555555'
OTHER_CHILD = '66666666-6666-4666-8666-666666666666'


@pytest.fixture
def continuation(case):
    original = _prepare(case)
    recovery.publish_paid_render_recovery(original)
    token = 'original-repair-token-long-enough'
    dispatch = studio_state.claim_retry_dispatch(SOURCE, CHILD, token, allow_repair=True)
    assert dispatch['claimed'] and dispatch['checkpoint'] == original
    assert studio_state.mark_retry_dispatch(SOURCE, token, 'dispatched')
    studio_state.create_job(CHILD, deepcopy(case.source['spec']), kind='render', parent_id=SOURCE)
    assert studio_state.acquire_retry_child_execution(CHILD, SOURCE)
    leaf = studio_state.get_job(CHILD)
    child_voice_path = case.work.parent.parent / f'{CHILD}.mp3'
    child_voice_path.write_bytes(case.audio)
    voice = {**original['approved_package']['_recovered_voice'], 'path': str(child_voice_path)}
    candidate = audio_checkpoint.persist_audio_candidate_checkpoint(CHILD, original['approved_package'], voice)
    leaf.update(candidate, state='FAILURE', stage='failed', failure_stage='final_visual_qc_rescue',
                error='Stock scene rejected', result={}, paid_create_slots_used=0, preview_total_paid_create_cap=4)
    case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
    case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD, mapping={'cap':'4','used':'0'})
    raw = json.dumps(original, ensure_ascii=False, sort_keys=True).encode()
    pointer = {'source_task_id':SOURCE, 'checkpoint_key':f'recovery/{SOURCE}/prepared_checkpoint_v1.json',
               'checkpoint_sha256':hashlib.sha256(raw).hexdigest(), 'checkpoint_size':len(raw)}
    case.objects[pointer['checkpoint_key']] = raw
    work = case.work.parent / f'{CONTINUATION_PREP}_attempt_0'
    work.mkdir()
    return SimpleNamespace(case=case, original=original, pointer=pointer, leaf=leaf, work=work, token=token, child_voice_path=child_voice_path)


def _continue(fixture):
    return recovery.prepare_paid_recovery_continuation(CHILD, fixture.pointer, fixture.work)


def test_continuation_preserves_exact_paired_assets_and_only_publishes_leaf(continuation):
    f, case = continuation, continuation.case
    before = _redis_snapshot(case)
    puts, calls = list(case.puts), case.review.call_count
    receipt = _continue(f)
    assert _redis_snapshot(case) == before
    assert case.puts == puts and case.review.call_count == calls
    assert receipt['source_task_id'] == CHILD
    assert receipt['approved_package'] == f.original['approved_package']
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    assert receipt['requires_full_qa'] is True
    assert receipt['approved_package']['_recovered_generated_media']['source_task_id'] == SOURCE
    assert receipt['approved_package']['_recovered_voice']['source_task_id'] == SOURCE
    assert f.token not in json.dumps(receipt)
    assert recovery.publish_paid_recovery_continuation(receipt)['source_task_id'] == CHILD
    after = _redis_snapshot(case)
    allowed = {studio_state.JOB_PREFIX+CHILD, studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD}
    assert {key: value for key,value in before.items() if key not in allowed} == {key:value for key,value in after.items() if key not in allowed}
    updated = studio_state.get_job(CHILD)
    assert updated.pop('repair_available') is True
    updated.pop('updated_at')
    original_leaf = deepcopy(f.leaf)
    original_leaf.pop('updated_at')
    assert updated == original_leaf
    assert all(body.closed for body in case.bodies)


def test_continuation_ordinary_claim_creates_only_one_grandchild_and_cannot_republish(continuation):
    f, case = continuation, continuation.case
    receipt = _continue(f)
    recovery.publish_paid_recovery_continuation(receipt)
    root_before = {key:case.redis.dump(key) for key in case.redis.keys() if SOURCE in key}
    def claim(child_id):
        result = studio_state.claim_retry_dispatch(CHILD, child_id, 'next-token-'+child_id, allow_repair=True)
        if result['claimed']:
            assert result['mode'] == 'repair' and result['checkpoint'] == receipt
            studio_state.create_job(child_id, deepcopy(f.leaf['spec']), kind='render', parent_id=CHILD)
        return child_id, result
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [GRANDCHILD, OTHER_CHILD]))
    winners = [task_id for task_id, result in outcomes if result['claimed']]
    assert len(winners) == 1
    assert studio_state.get_job(CHILD)['retry_child_task_id'] == winners[0]
    assert studio_state.get_job(winners[0])['parent_id'] == CHILD
    assert studio_state.acquire_retry_child_execution(winners[0], CHILD)
    assert not studio_state.acquire_retry_child_execution(winners[0], CHILD)
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(receipt)
    assert _redis_snapshot(case) == before
    assert root_before == {key:case.redis.dump(key) for key in case.redis.keys() if SOURCE in key}


@pytest.mark.parametrize('damage', [
    'parent', 'reciprocal_child', 'frozen_channel', 'frozen_revision', 'state', 'failure_stage',
    'missing_ledger', 'leaf_paid', 'root_paid', 'ledger_cap', 'repair_claimed',
    'dispatch_token', 'dispatch_mode', 'dispatch_state', 'claim_token', 'execution', 'child_claim_source',
    'outgoing_dispatch', 'outgoing_checkpoint', 'outgoing_claim', 'child_audio', 'child_package',
])
def test_continuation_rejects_unproven_lineage_and_cost_without_any_writes(continuation, damage):
    f, case = continuation, continuation.case
    root, leaf = studio_state.get_job(SOURCE), studio_state.get_job(CHILD)
    if damage == 'parent': leaf['parent_id'] = PREP
    if damage == 'reciprocal_child': root['retry_child_task_id'] = PREP
    if damage == 'frozen_channel': leaf['spec']['production_channel_id'] = 'other'
    if damage == 'frozen_revision': leaf['spec']['production_profile_revision'] = 'new'
    if damage in {'state','failure_stage'}: leaf[damage] = 'wrong'
    if damage == 'repair_claimed': root['repair_claimed'] = False
    if damage == 'child_audio': leaf['audio_candidate_checkpoint']['audio_sha256'] = 'f'*64
    if damage == 'child_package': leaf['audio_candidate_checkpoint']['package_sha256'] = 'e'*64
    case.redis.set(studio_state.JOB_PREFIX+SOURCE, json.dumps(root))
    case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
    if damage == 'missing_ledger': case.redis.delete(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD)
    if damage == 'leaf_paid': case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD, 'used', '1')
    if damage == 'root_paid': case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE, 'used', '2')
    if damage == 'ledger_cap': case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD, 'cap', '5')
    for field in ('token','mode','state'):
        if damage == 'dispatch_'+field: case.redis.hset(studio_state.RETRY_DISPATCH_PREFIX+SOURCE, field, 'wrong')
    if damage == 'claim_token': case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX+SOURCE, 'wrong')
    if damage == 'execution': case.redis.delete(studio_state.RETRY_CHILD_EXECUTION_PREFIX+CHILD)
    if damage == 'child_claim_source': case.redis.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX+CHILD, 'source_task_id', PREP)
    for name, prefix in [('dispatch',studio_state.RETRY_DISPATCH_PREFIX),('checkpoint',studio_state.REPAIR_CHECKPOINT_PREFIX),('claim',studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX)]:
        if damage == 'outgoing_'+name: case.redis.set(prefix+CHILD, 'existing')
    before, puts = _redis_snapshot(case), list(case.puts)
    with pytest.raises(recovery.PaidRenderRecoveryError): _continue(f)
    assert _redis_snapshot(case) == before and case.puts == puts
    assert case.review.call_count == 1


@pytest.mark.parametrize('damage', ['receipt_key','receipt_sha','receipt_size','receipt_body','voice_bytes','clip_bytes','expired_story','timing'])
def test_continuation_current_approval_and_immutable_assets_fail_closed(continuation, damage):
    f, case = continuation, continuation.case
    if damage == 'receipt_key': f.pointer['checkpoint_key'] = f'recovery/{CHILD}/prepared_checkpoint_v1.json'
    if damage == 'receipt_sha': f.pointer['checkpoint_sha256'] = 'f'*64
    if damage == 'receipt_size': f.pointer['checkpoint_size'] += 1
    if damage == 'receipt_body': case.objects[f.pointer['checkpoint_key']] += b'x'
    if damage == 'voice_bytes': case.objects[f.original['approved_package']['_recovered_voice']['key']] = case.audio[:-1]+b'x'
    if damage == 'clip_bytes': case.objects[case.manifest['clip_key']] = case.clip[:-1]+b'x'
    if damage == 'expired_story': case.approved.side_effect = lambda *_: False
    if damage == 'timing':
        voice = {**f.original['approved_package']['_recovered_voice'], 'path':str(f.child_voice_path), 'tempo_rate':1.01}
        candidate = audio_checkpoint.persist_audio_candidate_checkpoint(CHILD, f.original['approved_package'], voice)
        f.leaf.update(candidate)
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(f.leaf))
    before, puts = _redis_snapshot(case), list(case.puts)
    with pytest.raises(recovery.PaidRenderRecoveryError): _continue(f)
    assert _redis_snapshot(case) == before and case.puts == puts
    assert case.review.call_count == 1


@pytest.mark.parametrize('damage', ['stale_result', 'stale_error', 'source_spec', 'package', 'lineage_hash', 'paid_allowance'])
def test_continuation_publisher_rechecks_receipt_and_live_source(continuation, damage):
    f, case = continuation, continuation.case
    receipt = _continue(f)
    if damage.startswith('stale_'):
        leaf = studio_state.get_job(CHILD)
        leaf[damage.removeprefix('stale_')] = {'changed':True} if damage == 'stale_result' else 'changed'
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
    if damage == 'source_spec': receipt['source_spec_sha256'] = 'f'*64
    if damage == 'package': receipt['approved_package']['scenes'][0]['narration'] = 'Different words'
    if damage == 'lineage_hash': receipt['continuation']['lineage_sha256'] = 'e'*64
    if damage == 'paid_allowance': receipt['new_paid_create_requests'] = 1
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(receipt)
    assert _redis_snapshot(case) == before


@pytest.mark.parametrize('target', ['origin_job','leaf_job','origin_ledger','leaf_ledger','dispatch','claim','child_claim','execution','checkpoint'])
def test_continuation_real_watch_race_never_reopens_or_overwrites_claims(continuation, monkeypatch, target):
    f, case = continuation, continuation.case
    receipt = _continue(f)
    other = fakeredis.FakeRedis(server=case.server, decode_responses=True)
    original_pipeline = case.redis.pipeline
    calls = []
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute
        def execute():
            calls.append(True)
            if target in {'origin_job','leaf_job'}:
                key = studio_state.JOB_PREFIX + (SOURCE if target == 'origin_job' else CHILD)
                job = json.loads(other.get(key)); job['error'] = 'race'; other.set(key, json.dumps(job))
            elif target in {'origin_ledger','leaf_ledger'}:
                other.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+(SOURCE if target == 'origin_ledger' else CHILD), 'used', '2')
            elif target == 'dispatch': other.hset(studio_state.RETRY_DISPATCH_PREFIX+SOURCE, 'token', 'racing-dispatch')
            elif target == 'child_claim': other.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX+CHILD, 'token', 'racing-child-claim')
            else:
                prefixes = {'claim':studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX, 'execution':studio_state.RETRY_CHILD_EXECUTION_PREFIX, 'checkpoint':studio_state.REPAIR_CHECKPOINT_PREFIX}
                other.set(prefixes[target]+(SOURCE if target == 'claim' else CHILD), 'racing-claim')
            return original_execute()
        pipe.execute = execute
        return pipe
    monkeypatch.setattr(case.redis, 'pipeline', pipeline)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(receipt)
    assert calls == [True] and not studio_state.get_job(CHILD).get('repair_available')
    assert case.redis.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX+SOURCE)
    if target == 'checkpoint': assert case.redis.get(studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD) == 'racing-claim'
    else: assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD)


def _add_failed_descendant(f, parent_id, child_id):
    """Use real consumed Studio claims, not fabricated lineage tokens."""
    case = f.case
    checkpoint_key = studio_state.REPAIR_CHECKPOINT_PREFIX + parent_id
    if not case.redis.exists(checkpoint_key):
        fixture_checkpoint = {**deepcopy(f.original), 'source_task_id':parent_id}
        case.redis.set(checkpoint_key, json.dumps(fixture_checkpoint))
    token = 'consumed-repair-token-' + child_id
    assert studio_state.claim_retry_dispatch(parent_id, child_id, token, allow_repair=True)['claimed']
    assert studio_state.mark_retry_dispatch(parent_id, token, 'dispatched')
    studio_state.create_job(child_id, deepcopy(f.leaf['spec']), kind='render', parent_id=parent_id)
    assert studio_state.acquire_retry_child_execution(child_id, parent_id)
    path = case.work.parent.parent / f'{child_id}.mp3'
    path.write_bytes(case.audio)
    voice = {**f.original['approved_package']['_recovered_voice'], 'path':str(path)}
    candidate = audio_checkpoint.persist_audio_candidate_checkpoint(child_id, f.original['approved_package'], voice)
    leaf = studio_state.get_job(child_id)
    leaf.update(candidate, state='FAILURE', stage='failed', failure_stage='final_visual_qc',
                result={}, error='Stock evidence rejected', paid_create_slots_used=0, preview_total_paid_create_cap=4)
    case.redis.set(studio_state.JOB_PREFIX+child_id, json.dumps(leaf))
    case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+child_id, mapping={'cap':'4','used':'0'})
    return leaf


@pytest.fixture
def second_continuation(continuation):
    f = continuation
    # First continuation is actually prepared, published, consumed and executed.
    recovery.publish_paid_recovery_continuation(_continue(f))
    f.leaf = _add_failed_descendant(f, CHILD, GRANDCHILD)
    f.work = f.case.work.parent / f'{OTHER_CHILD}_attempt_0'
    f.work.mkdir()
    return f


def _continue_second(f):
    return recovery.prepare_paid_recovery_continuation(GRANDCHILD, f.pointer, f.work)


def test_two_consumed_repairs_preserve_all_ancestors_and_single_use_leaf(second_continuation):
    f, case = second_continuation, second_continuation.case
    before, puts, calls = _redis_snapshot(case), list(case.puts), case.review.call_count
    checkpoint = _continue_second(f)
    assert _redis_snapshot(case) == before
    assert case.puts == puts and case.review.call_count == calls
    assert checkpoint['source_task_id'] == GRANDCHILD
    assert checkpoint['approved_package'] == f.original['approved_package']
    assert recovery.publish_paid_recovery_continuation(checkpoint)['source_task_id'] == GRANDCHILD
    allowed = {studio_state.JOB_PREFIX+GRANDCHILD, studio_state.REPAIR_CHECKPOINT_PREFIX+GRANDCHILD}
    after = _redis_snapshot(case)
    assert {key:value for key,value in before.items() if key not in allowed} == {key:value for key,value in after.items() if key not in allowed}
    dispatch = studio_state.claim_retry_dispatch(GRANDCHILD, OTHER_CHILD, 'third-retry-token-long-enough', allow_repair=True)
    assert dispatch['claimed'] and dispatch['checkpoint'] == checkpoint
    assert not studio_state.claim_retry_dispatch(GRANDCHILD, PREP, 'duplicate-token-long-enough', allow_repair=True)['claimed']
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX+SOURCE)['used'] == '1'
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD)['used'] == '0'
    assert case.redis.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX+GRANDCHILD)['used'] == '0'


@pytest.mark.parametrize('damage', ['mid_paid','mid_spec','mid_state','mid_audio','mid_package','mid_reciprocal',
    'first_claim','second_claim','second_dispatch','second_child_claim','second_execution','mid_checkpoint','cycle','missing_parent'])
def test_every_intermediate_edge_and_zero_paid_ledger_is_required(second_continuation, damage):
    f, case = second_continuation, second_continuation.case
    middle = studio_state.get_job(CHILD)
    if damage == 'mid_paid':
        middle['paid_create_slots_used'] = 1
        case.redis.hset(studio_state.PAID_CREATE_BUDGET_PREFIX+CHILD, 'used', '1')
    if damage == 'mid_spec': middle['spec']['production_profile_revision'] = 'other'
    if damage == 'mid_state': middle['state'] = 'SUCCESS'
    if damage == 'mid_audio': middle['audio_candidate_checkpoint']['audio_sha256'] = 'f'*64
    if damage == 'mid_package': middle['audio_candidate_checkpoint']['package_sha256'] = 'e'*64
    if damage == 'mid_reciprocal': middle['retry_child_task_id'] = PREP
    if damage == 'cycle': middle['parent_id'] = GRANDCHILD
    if damage == 'missing_parent': middle['parent_id'] = PREP
    case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(middle))
    if damage == 'first_claim': case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX+SOURCE, 'wrong')
    if damage == 'second_claim': case.redis.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX+CHILD, 'wrong')
    if damage == 'second_dispatch': case.redis.hset(studio_state.RETRY_DISPATCH_PREFIX+CHILD, 'token', 'wrong')
    if damage == 'second_child_claim': case.redis.hset(studio_state.RETRY_CHILD_CLAIM_PREFIX+GRANDCHILD, 'source_task_id', SOURCE)
    if damage == 'second_execution': case.redis.delete(studio_state.RETRY_CHILD_EXECUTION_PREFIX+GRANDCHILD)
    if damage == 'mid_checkpoint': case.redis.set(studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD, 'unconsumed')
    before, puts = _redis_snapshot(case), list(case.puts)
    with pytest.raises(recovery.PaidRenderRecoveryError): _continue_second(f)
    assert _redis_snapshot(case) == before and case.puts == puts


@pytest.mark.parametrize('target', ['job','ledger','dispatch','repair_claim','inbound_claim','execution','checkpoint'])
def test_every_intermediate_record_is_watched(second_continuation, monkeypatch, target):
    f, case = second_continuation, second_continuation.case
    checkpoint = _continue_second(f)
    other = fakeredis.FakeRedis(server=case.server, decode_responses=True)
    original_pipeline = case.redis.pipeline
    calls = []
    def pipeline(*args, **kwargs):
        pipe = original_pipeline(*args, **kwargs)
        original_execute = pipe.execute
        def execute():
            calls.append(True)
            if target == 'job':
                job = json.loads(other.get(studio_state.JOB_PREFIX+CHILD))
                job['error'] = 'raced'
                other.set(studio_state.JOB_PREFIX+CHILD, json.dumps(job))
            elif target in {'ledger','dispatch','inbound_claim'}:
                prefix, field = {'ledger':(studio_state.PAID_CREATE_BUDGET_PREFIX,'used'),
                    'dispatch':(studio_state.RETRY_DISPATCH_PREFIX,'token'),
                    'inbound_claim':(studio_state.RETRY_CHILD_CLAIM_PREFIX,'token')}[target]
                other.hset(prefix+CHILD, field, 'raced')
            else:
                prefix = {'repair_claim':studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
                    'execution':studio_state.RETRY_CHILD_EXECUTION_PREFIX,'checkpoint':studio_state.REPAIR_CHECKPOINT_PREFIX}[target]
                other.set(prefix+CHILD, 'raced')
            return original_execute()
        pipe.execute = execute
        return pipe
    monkeypatch.setattr(case.redis, 'pipeline', pipeline)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(checkpoint)
    assert calls == [True]
    assert not studio_state.get_job(GRANDCHILD).get('repair_available')
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+GRANDCHILD)


def test_changed_lineage_between_discovery_and_watch_is_rejected(second_continuation, monkeypatch):
    f, case = second_continuation, second_continuation.case
    checkpoint = _continue_second(f)
    original_pipeline = case.redis.pipeline
    def pipeline(*args, **kwargs):
        middle = studio_state.get_job(CHILD)
        middle['parent_id'] = PREP
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(middle))
        return original_pipeline(*args, **kwargs)
    monkeypatch.setattr(case.redis, 'pipeline', pipeline)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(checkpoint)
    assert not studio_state.get_job(GRANDCHILD).get('repair_available')
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+GRANDCHILD)


@pytest.mark.parametrize('edges', [8,9])
def test_continuation_discovery_is_bounded_to_eight_consumed_edges(continuation, edges):
    f, case = continuation, continuation.case
    parent = CHILD
    for index in range(2, edges+1):
        child = f'77777777-7777-4777-8777-{index:012d}'
        _add_failed_descendant(f, parent, child)
        parent = child
    before = _redis_snapshot(case)
    if edges == 8:
        assert len(recovery._continuation_lineage(SOURCE, parent, case.redis)) == 9
        assert recovery._continuation_state(SOURCE, parent, case.redis)[1]['task_id'] == parent
    else:
        with pytest.raises(ValueError): recovery._continuation_state(SOURCE, parent, case.redis)
    assert _redis_snapshot(case) == before
