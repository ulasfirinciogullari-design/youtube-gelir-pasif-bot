"""Offline end-to-end preservation -> fresh review -> ordinary v3 claim."""
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

from app.services import audio_checkpoint, preserved_visual_recovery as recovery, studio_state, visual_allocation_checkpoint
from test_paid_render_recovery import _task_runtime
from test_failed_visual_recovery import _render_runtime


SOURCE = '11111111-1111-4111-8111-111111111111'
PREP = '22222222-2222-4222-8222-222222222222'
CHILD = '33333333-3333-4333-8333-333333333333'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(tmp_path, monkeypatch):
    tasks, render = _task_runtime(), _render_runtime()
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    monkeypatch.setattr(audio_checkpoint, '_AUDIO_TEMP_ROOT', tmp_path)
    monkeypatch.setattr(visual_allocation_checkpoint, '_WORK_ROOT', tmp_path / 'youtube_factory')
    monkeypatch.setattr(recovery.storage.settings, 'bucket', 'private-test', raising=False)
    objects, writes, reads = {}, [], []
    def get_object(*, Bucket, Key):
        assert Bucket == 'private-test'
        reads.append(Key)
        raw, content_type = objects[Key]
        return {'ContentLength': len(raw), 'ContentType': content_type, 'Body': io.BytesIO(raw)}
    def put_object(*, Bucket, Key, Body, ContentType, IfNoneMatch, **kwargs):
        assert Bucket == 'private-test' and IfNoneMatch == '*'
        assert kwargs.get('CacheControl') == 'private, no-store'
        assert 'ACL' not in kwargs
        writes.append(Key)
        if Key in objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        raw = Body.read()
        assert len(raw) == kwargs['ContentLength']
        assert _sha(raw) == kwargs['Metadata']['sha256']
        objects[Key] = raw, ContentType
        return {'ResponseMetadata': {'HTTPStatusCode': 200}}
    storage = SimpleNamespace(get_object=get_object, put_object=put_object)
    monkeypatch.setattr(recovery.storage, '_client', lambda: storage)
    monkeypatch.setattr(audio_checkpoint, 'upload_file', lambda path, key, kind: objects.__setitem__(key, (Path(path).read_bytes(), kind)))
    package = {'title': 'Membership fees', 'scenes': [
        {'index': index, 'narration': f'Exact spoken scene number {index}.', 'visual_queries': ['warehouse merchandise'],
         'ai_prompt': 'A genuine warehouse documentary shot.', 'transition': 'dip' if index == 3 else 'cut'} for index in range(6)],
        'sources': [{'url': 'https://investor.costco.com/overview/default.aspx', 'evidence': 'Official source describes the membership business.'},
                    {'url': 'https://www.costco.com/about.html', 'evidence': 'Company describes its warehouse operations.'}]}
    audio = b'ID3' + b'exact unchanged saved narration' * 150
    audio_path = tmp_path / f'{SOURCE}.mp3'
    audio_path.write_bytes(audio)
    durations = [4.5, 5.0, 4.7, 4.8, 4.9, 5.2]
    voice = {'path': str(audio_path), 'spoken_texts': [scene['narration'] for scene in package['scenes']],
             'scene_durations': durations, 'duration_before_fit': 29.1, 'duration_after_fit': 29.1,
             'tempo_rate': 1.0, 'content_target_seconds': 29.5, 'reserved_tail_seconds': .5,
             'voice_name': 'Existing approved speech', 'voice_model': 'existing', 'voice_language_code': 'en'}
    audio_pointer = audio_checkpoint.persist_audio_candidate_checkpoint(SOURCE, package, voice)['audio_candidate_checkpoint']
    options = tasks._normalized_options({'mode': 'production', 'format': 'shorts', 'music': 'off',
        'publish_after_render': True, 'production_channel_id': 'frozen-channel', 'production_connection_id': 'frozen-connection',
        'production_profile_revision': 'frozen-revision'}, .5)
    initial_work = tmp_path / 'youtube_factory' / f'{SOURCE}_attempt_0'
    initial_work.mkdir(parents=True)
    entries, clips = [], []
    for index in range(6):
        raw = b'\0\0\0\x18ftyp' + bytes([65 + index]) * 2048
        path = initial_work / f'runway_s{index:02d}.mp4'
        path.write_bytes(raw); clips.append(raw)
        entry = recovery.assets.persist_generated_asset_candidate(SOURCE, initial_work,
            package=package, voice_result=voice,
            visual_spec={'path': str(path), 'generated': True, 'source_type': 'generated', 'generation_provider': 'gemini_veo',
                         'generation_provider_attempts': 1, 'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True},
            scene_index=index, phase='initial_generation', options=options, duration_minutes=.5)
        entries.append(entry)
    source = {'task_id': SOURCE, 'kind': 'render', 'state': 'FAILURE', 'stage': 'failed',
              'failure_stage': 'final_visual_qc', 'result': {}, 'error': 'Temporal scene evidence missing',
              'updated_at': '2026-09-07T00:00:00Z', 'parent_id': '44444444-4444-4444-8444-444444444444',
              'paid_create_slots_used': 6, 'preview_total_paid_create_cap': 6,
              'audio_candidate_checkpoint': audio_pointer, 'audio_candidate_checkpoint_error': None,
              'spec': {'topic': 'How membership fees fund a warehouse business', 'language': 'en', 'duration_minutes': .5,
                       'channel_id': 'channel-profile', **options},
              'generated_asset_candidates': {**recovery._FLAGS, 'source_task_id': SOURCE, 'status': 'candidate_journal',
                                            'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0, 'entries': entries}}
    client.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(source))
    client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': '6', 'used': '6'})
    client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + source['parent_id'], 'consumed-ancestor-claim')
    client.hset(studio_state.RETRY_DISPATCH_PREFIX + source['parent_id'], mapping={'state': 'dispatched', 'child_task_id': SOURCE})
    def revalidate(original, topic, duration, language, opts, **kwargs):
        assert language == 'en' and duration == .5
        assert kwargs['immutable_candidate_narrations'] == [scene['narration'] for scene in original['scenes']]
        assert 'short_story_qc' not in original
        return {**deepcopy(original), 'narration': ' '.join(scene['narration'] for scene in original['scenes']),
                'studio_options': deepcopy(opts), 'short_story_qc': {'fresh_test': True}}
    story = Mock(side_effect=revalidate)
    approved = Mock(side_effect=lambda value, topic: value.get('short_story_qc') == {'fresh_test': True})
    director = SimpleNamespace(revalidate_immutable_short_story=story, short_story_package_is_approved=approved)
    monkeypatch.setattr(recovery, '_runtime', lambda: (tasks, director, SimpleNamespace(normalize_turkish_tts=lambda text, **kwargs: text)))
    render.media_duration = lambda path: 29.1
    render.normalize_clip = Mock(side_effect=lambda spec, path, *args: path.write_bytes(b'exact local cut'))
    reviews = [{'scene_index': index, 'score': 90, 'best_candidate_index': 0,
                'best_start_fraction': .18, 'reason': 'The exact action is visible with sufficient temporal evidence.',
                'evidence_gate_passed': True, 'identity_gate_passed': True, 'editorial_gate_passed': True}
               for index in range(6)]
    visual = Mock(return_value={'reviews': reviews, 'missing_review_indices': []})
    monkeypatch.setattr(recovery, '_review_runtime', lambda: (render, visual))
    work = tmp_path / 'youtube_factory' / f'{PREP}_attempt_0'
    work.mkdir(parents=True)
    writes.clear(); reads.clear()
    return SimpleNamespace(client=client, storage=storage, source=source, objects=objects,
        writes=writes, reads=reads, package=package, voice=voice, audio=audio, clips=clips, work=work,
        story=story, approved=approved, visual=visual, reviews=reviews, render=render, tasks=tasks)


def _prepare(case, **kwargs):
    return recovery.prepare_preserved_visual_recovery(SOURCE, case.work, **kwargs)


def _record(case, pointer):
    return json.loads(case.objects[pointer['key']][0])


def _snapshot(case):
    return {key: case.client.dump(key) for key in case.client.keys()}


def _save(case):
    case.client.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(case.source))


def test_complete_real_preservation_journal_becomes_fresh_zero_create_v3_only(case):
    before = _snapshot(case)
    pointer = _prepare(case)
    assert _snapshot(case) == before
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    assert receipt['qa_approved'] is False and receipt['requires_full_qa'] is True
    assert audit['status'] == 'visual_preparation_passed' and len(audit['retained_visual_reviews']) == 6
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    package = receipt['approved_package']
    assert package['scenes'] == case.package['scenes']
    assert package['studio_options']['production_profile_revision'] == 'frozen-revision'
    assert package['_recovered_generated_media']['version'] == 3
    assert package['_recovered_generated_media']['recovery_only'] is True
    assert 'repair_scene_indices' not in package['_recovered_generated_media']
    validated = case.tasks._validated_recovered_generated_media(package['_recovered_generated_media'], 6, receipt['package_sha256'])
    assert set(validated['scenes']) == set(range(6))
    for index, raw in enumerate(case.clips):
        entry = validated['scenes'][index][0]
        assert case.objects[entry['key']][0] == raw and entry['sha256'] == _sha(raw)
    assert case.objects[package['_recovered_voice']['key']][0] == case.audio
    assert len(case.writes) == 9  # audit first, six raw copies, voice, prepared.
    assert '/audit-' in case.writes[0] and '/prepared-' in case.writes[-1]
    case.story.assert_called_once(); case.visual.assert_called_once()
    args, kwargs = case.visual.call_args
    assert args[0] == kwargs['story_scenes'] == case.package['scenes']
    assert kwargs['evidence_sources'] == case.package['sources']
    assert kwargs['_missing_review_attempts'] == kwargs['_score_reason_consistency_attempts'] == 0
    assert [call.args[3] for call in case.render.normalize_clip.call_args_list] == list(range(6))
    assert [round(call.args[2] * 30) for call in case.render.normalize_clip.call_args_list] == [135, 150, 141, 144, 147, 156]
    assert len(list(case.work.glob('audit-*.json'))) == len(list(case.work.glob('prepared-*.json'))) == 1


def test_generation_priority_order_does_not_change_scene_identity(case):
    case.source['generated_asset_candidates']['entries'].reverse()
    _save(case)
    receipt = _record(case, _prepare(case))
    media = receipt['approved_package']['_recovered_generated_media']['scenes']
    assert [media[str(i)][0]['sha256'] for i in range(6)] == [_sha(raw) for raw in case.clips]


@pytest.mark.parametrize('repairs', [(), (3,)])
def test_publish_then_actual_lua_claim_is_one_child_preserving_history_and_spend(case, repairs):
    pointer = _prepare(case, repair_scene_indices=repairs)
    before, original = _snapshot(case), deepcopy(case.source)
    assert recovery.publish_preserved_visual_recovery(pointer)['status'] == 'checkpoint_published'
    current = studio_state.get_job(SOURCE)
    assert current['repair_available'] is True and current['state'] == 'FAILURE'
    assert {k: v for k, v in current.items() if k not in {'repair_available', 'updated_at'}} == {k: v for k, v in original.items() if k not in {'repair_available', 'updated_at'}}
    for key in before:
        if key != studio_state.JOB_PREFIX + SOURCE:
            assert case.client.dump(key) == before[key]
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda i: studio_state.claim_retry_dispatch(SOURCE, CHILD if i == 0 else PREP,
                           ('a' if i == 0 else 'b') * 32, allow_repair=True), range(2)))
    assert sum(item['claimed'] for item in result) == 1
    checkpoint = next(item for item in result if item['claimed'])['checkpoint']
    assert checkpoint['approved_package']['_recovered_generated_media']['version'] == (4 if repairs else 3)
    snapshot = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): recovery.publish_preserved_visual_recovery(pointer)
    assert _snapshot(case) == snapshot


@pytest.mark.parametrize('damage', ['state', 'kind', 'stage', 'cap', 'paid', 'journal_failed', 'missing', 'duplicate',
    'repair', 'provider', 'voice', 'audio_error', 'package', 'claim', 'dispatch', 'checkpoint', 'revision', 'format', 'music', 'language'])
def test_incomplete_or_incompatible_source_stops_before_review_or_writes(case, damage):
    journal = case.source['generated_asset_candidates']
    if damage == 'state': case.source['state'] = 'SUCCESS'
    if damage == 'kind': case.source['kind'] = 'publish'
    if damage == 'stage': case.source['failure_stage'] = 'audio_qc'
    if damage == 'cap': case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'cap', '5')
    if damage == 'paid': case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '5')
    if damage == 'journal_failed': journal['failed_count'] = 1
    if damage == 'missing': journal['entries'].pop()
    if damage == 'duplicate': journal['entries'][2] = deepcopy(journal['entries'][1])
    if damage == 'repair': journal['entries'][0]['phase'] = 'final_repair'
    if damage == 'provider': journal['entries'][0]['provider'] = 'gemini_image_motion'
    if damage == 'voice': journal['entries'][1]['audio_sha256'] = 'e' * 64
    if damage == 'audio_error': case.source['audio_candidate_checkpoint_error'] = 'unavailable'
    if damage == 'package': journal['entries'][1]['package_sha256'] = 'd' * 64
    if damage == 'revision': case.source['spec'].pop('production_profile_revision')
    for field in ('format', 'music', 'language'):
        if damage == field: case.source['spec'][field] = 'invalid'
    for field, prefix in [('claim', studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX), ('dispatch', studio_state.RETRY_DISPATCH_PREFIX), ('checkpoint', studio_state.REPAIR_CHECKPOINT_PREFIX)]:
        if damage == field: case.client.set(prefix + SOURCE, 'existing')
    _save(case)
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    assert _snapshot(case) == before and case.writes == []
    case.story.assert_not_called(); case.visual.assert_not_called()


@pytest.mark.parametrize('damage', ['manifest_hash', 'raw_hash', 'voice_hash', 'probe', 'spoken'])
def test_cryptographic_candidate_binding_precedes_model_call(case, damage):
    pointer = case.source['generated_asset_candidates']['entries'][2]
    key = {'manifest_hash': pointer['manifest_key'], 'raw_hash': pointer['raw_key'],
           'voice_hash': case.source['audio_candidate_checkpoint']['audio_key']}.get(damage)
    if key:
        raw, content_type = case.objects[key]
        case.objects[key] = (raw[:-1] + b'x', content_type)
    if damage == 'probe': case.tasks._validate_recovered_generated_clip.__globals__['media_duration'] = lambda path: 4.0
    if damage == 'spoken':
        original = recovery.load_voice_retry_candidate
        def changed(*args):
            result = original(*args); result['voice_result']['spoken_texts'][0] += ' changed'; return result
        # Only this fixture is affected; no global import/module replacement.
        from unittest.mock import patch
        with patch.object(recovery, 'load_voice_retry_candidate', side_effect=changed):
            with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    else:
        with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    case.story.assert_not_called(); case.visual.assert_not_called()
    assert case.writes == []


@pytest.mark.parametrize('damage', ['score', 'temporal_gate', 'all_rejected', 'missing', 'duplicate', 'unavailable'])
def test_failed_visual_reports_are_durable_before_raise_never_checkpoint(case, damage):
    if damage == 'score': case.reviews[2]['score'] = 35
    if damage == 'temporal_gate': case.reviews[2]['evidence_gate_passed'] = False
    if damage == 'all_rejected':
        for row in case.reviews: row.update(score=40, evidence_gate_passed=False)
    if damage == 'missing': case.reviews.pop(2)
    if damage == 'duplicate': case.reviews[2]['scene_index'] = 1
    if damage == 'unavailable': case.visual.side_effect = TimeoutError('secret provider body')
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught: _prepare(case)
    pointer = caught.value.diagnostic_pointer
    assert pointer and pointer['kind'] == 'audit'
    audit = _record(case, pointer)
    assert audit['qa_approved'] is audit['reusable'] is False
    assert audit['status'] == ('visual_review_unavailable' if damage == 'unavailable' else 'visual_preparation_rejected')
    if damage != 'unavailable':
        assert len(audit['retained_visual_reviews']) == 6
        assert [row['scene_index'] for row in audit['retained_visual_reviews']] == list(range(6))
    assert audit['package']['short_story_qc'] == {'fresh_test': True}
    assert _snapshot(case) == before and len(case.writes) == 1
    assert len(list(case.work.glob('audit-*.json'))) == 1
    assert not list(case.work.glob('prepared-*.json'))
    with pytest.raises(recovery.PreservedVisualRecoveryError): recovery.publish_preserved_visual_recovery(pointer)
    assert _snapshot(case) == before
    assert 'secret' not in str(caught.value)


def test_failed_story_is_durable_without_visual_call_or_retry(case):
    case.story.side_effect = RuntimeError('secret critic details')
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught: _prepare(case)
    assert _record(case, caught.value.diagnostic_pointer)['status'] == 'story_review_rejected_or_unavailable'
    case.visual.assert_not_called()
    assert len(case.writes) == 1


def test_storage_outage_keeps_full_local_failed_report_before_raise(case):
    case.reviews[2]['score'] = 35
    case.storage.put_object = Mock(side_effect=TimeoutError('secret storage body'))
    with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    paths = list(case.work.glob('audit-*.json'))
    assert len(paths) == 1
    audit = json.loads(paths[0].read_text(encoding='utf-8'))
    assert audit['retained_visual_reviews'][2]['review']['score'] == 35
    assert not case.client.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


@pytest.mark.parametrize('damage', ['source', 'ledger', 'claim', 'copy', 'voice_copy', 'audit', 'prepared', 'attestation'])
def test_publish_rechecks_exact_source_claim_assets_audit_and_current_story(case, damage):
    pointer = _prepare(case)
    receipt = _record(case, pointer)
    if damage == 'source': case.source['spec']['production_profile_revision'] = 'changed'; _save(case)
    if damage == 'ledger': case.client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '7')
    if damage == 'claim': case.client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'new-owner')
    if damage == 'copy': key = receipt['approved_package']['_recovered_generated_media']['scenes']['2'][0]['key']
    if damage == 'voice_copy': key = receipt['approved_package']['_recovered_voice']['key']
    if damage == 'audit': key = receipt['audit_pointer']['key']
    if damage == 'prepared': key = pointer['key']
    if damage in {'copy', 'voice_copy', 'audit', 'prepared'}:
        raw, kind = case.objects[key]; case.objects[key] = (raw[:-1] + b'x', kind)
    if damage == 'attestation': case.approved.side_effect = lambda *args: False
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): recovery.publish_preserved_visual_recovery(pointer)
    assert _snapshot(case) == before


@pytest.mark.parametrize('repairs', [(), (3,)])
def test_mid_transaction_claim_race_never_overwrites_claim_or_job(case, repairs):
    pointer = _prepare(case, repair_scene_indices=repairs)
    original, fired = case.storage.get_object, False
    manifest = case.source['generated_asset_candidates']['entries'][0]['manifest_key']
    def raced(**kwargs):
        nonlocal fired
        result = original(**kwargs)
        if not fired and kwargs['Key'] == manifest:
            fired = True
            case.client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'raced-claim')
        return result
    case.storage.get_object = raced
    with pytest.raises(recovery.PreservedVisualRecoveryError): recovery.publish_preserved_visual_recovery(pointer)
    assert case.client.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE) == 'raced-claim'
    assert studio_state.get_job(SOURCE) == case.source
    assert not case.client.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


def test_same_preparation_directory_cannot_repeat_paid_reviews(case):
    _prepare(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): _prepare(case)
    case.story.assert_called_once(); case.visual.assert_called_once()


@pytest.mark.parametrize('repairs', [(3,), (0, 3), (0, 3, 5)])
def test_explicit_v4_partition_preserves_all_audio_and_copies_only_passing_retained_media(case, repairs):
    for index in repairs:
        case.reviews[index].update(score=38, editorial_gate_passed=False,
                                  reason='The foreground packaging has fabricated garbled lettering.')
    overrides = {index: f'Exact replacement shot {index}; genuine product viewed from the side.' for index in repairs}
    original = deepcopy(case.package)
    before = _snapshot(case)
    pointer = _prepare(case, repair_scene_indices=repairs, shot_prompt_overrides=overrides)
    assert _snapshot(case) == before and case.package == original
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    package = receipt['approved_package']
    media = package['_recovered_generated_media']
    assert receipt['status'] == 'prepared_bounded_repair'
    assert receipt['repair_scene_indices'] == audit['repair_scene_indices'] == list(repairs)
    assert receipt['shot_prompt_overrides'] == audit['shot_prompt_overrides'] == {str(i): value for i, value in overrides.items()}
    assert audit['status'] == 'retained_visual_preparation_passed' and len(audit['retained_visual_reviews']) == 6
    assert all(audit['retained_visual_reviews'][i]['review']['score'] == 38 for i in repairs)
    assert media['version'] == 4 and media['repair_only'] is True and 'recovery_only' not in media
    assert media['repair_scene_indices'] == list(repairs)
    parsed = case.tasks._validated_recovered_generated_media(media, 6, receipt['package_sha256'])
    assert set(parsed['scenes']) == set(range(6)) - set(repairs)
    assert package['_recovered_voice']['package_sha256'] == receipt['package_sha256']
    assert case.objects[package['_recovered_voice']['key']][0] == case.audio
    for index in range(6):
        expected = {**original['scenes'][index], 'ai_prompt': overrides[index]} if index in repairs else original['scenes'][index]
        assert package['scenes'][index] == expected
        raw_key = f'recovery/{SOURCE}/raw/scene-{index:02d}-initial.mp4'
        assert (raw_key in case.writes) is (index not in repairs)
        if index not in repairs:
            assert case.objects[raw_key][0] == case.clips[index]
    assert case.story.call_args.args[0]['scenes'] == package['scenes']
    assert len(case.render.normalize_clip.call_args_list) == 6
    assert len(case.visual.call_args.args[1]) == 6  # no repair-slot exemption from a fresh review
    assert receipt['qa_approved'] is receipt['reusable'] is False
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    assert len(case.writes) == 9 - len(repairs)
    assert recovery.publish_preserved_visual_recovery(pointer)['status'] == 'checkpoint_published'
    assert case.client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    claim = studio_state.claim_retry_dispatch(SOURCE, CHILD, 'c' * 32, allow_repair=True)
    assert claim['claimed'] is True and claim['checkpoint']['approved_package']['_recovered_generated_media'] == media


def test_v4_without_prompt_override_retains_the_existing_authored_shot(case):
    case.reviews[3].update(score=38, editorial_gate_passed=False)
    receipt = _record(case, _prepare(case, repair_scene_indices=(3,)))
    assert receipt['shot_prompt_overrides'] == {}
    assert receipt['approved_package']['scenes'] == case.package['scenes']


@pytest.mark.parametrize('indices,overrides', [
    ([3], None), (None, None), ((3, 0), None), ((3, 3), None), ((True,), None), (('3',), None),
    ((-1,), None), ((6,), None), ((0, 1, 2, 3), None), ((), {3: 'A new shot'}),
    ((3,), {2: 'Wrong retained scene'}), ((3,), {'3': 'String input key'}),
    ((3,), {3: ''}), ((3,), {3: ' padded '}), ((3,), {3: 'x' * 4001}),
    ((3,), {3: 'bad\ncontrol'}), ((3,), {3: 'https://private.example/media'}),
    ((3,), {3: 'api_key=private-value'}), ((3,), {3: 42}), ((3,), ['A shot']),
])
def test_invalid_explicit_repair_request_stops_before_reads_models_or_state_mutation(case, indices, overrides):
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        _prepare(case, repair_scene_indices=indices, shot_prompt_overrides=overrides)
    assert _snapshot(case) == before and not case.reads and not case.writes
    case.story.assert_not_called(); case.visual.assert_not_called()


@pytest.mark.parametrize('change', ['retained_prompt', 'retained_queries', 'repair_queries', 'narration', 'order', 'inplace'])
def test_v4_critic_cannot_change_other_scene_fields_or_immutable_audio(case, change):
    original = case.story.side_effect
    def changed(package, *args, **kwargs):
        result = original(package, *args, **kwargs)
        if change == 'retained_prompt': result['scenes'][0]['ai_prompt'] += ' changed'
        elif change == 'retained_queries': result['scenes'][0]['visual_queries'] = ['changed query']
        elif change == 'repair_queries': result['scenes'][3]['visual_queries'] = ['changed query']
        elif change == 'narration': result['scenes'][3]['narration'] += ' New words.'
        elif change == 'order': result['scenes'][0], result['scenes'][1] = result['scenes'][1], result['scenes'][0]
        else:
            package['scenes'][0]['ai_prompt'] += ' mutation in supplied candidate'
            result['scenes'] = package['scenes']
        return result
    case.story.side_effect = changed
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case, repair_scene_indices=(3,), shot_prompt_overrides={3: 'Exact replacement shot.'})
    assert _record(case, caught.value.diagnostic_pointer)['status'] == 'story_review_rejected_or_unavailable'
    assert _snapshot(case) == before and len(case.writes) == 1
    case.visual.assert_not_called()


@pytest.mark.parametrize('damage', ['retained_score', 'retained_gate', 'missing_repair', 'malformed_repair', 'duplicate'])
def test_declared_repair_never_excuses_bad_retained_or_missing_actual_review(case, damage):
    case.reviews[3].update(score=38, editorial_gate_passed=False)
    if damage == 'retained_score': case.reviews[1]['score'] = 75
    elif damage == 'retained_gate': case.reviews[1]['identity_gate_passed'] = False
    elif damage == 'missing_repair': case.reviews.pop(3)
    elif damage == 'malformed_repair': case.reviews[3].pop('editorial_gate_passed')
    else: case.reviews[3]['scene_index'] = 2
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case, repair_scene_indices=(3,))
    audit = _record(case, caught.value.diagnostic_pointer)
    assert audit['status'] == 'visual_preparation_rejected' and len(audit['retained_visual_reviews']) == 6
    assert _snapshot(case) == before and len(case.writes) == 1
    assert not list(case.work.glob('prepared-*.json'))


def _rewrite_record(case, pointer, record):
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    replacement = {**pointer, 'key': pointer['key'].replace(pointer['sha256'], _sha(payload)),
                   'sha256': _sha(payload), 'size': len(payload)}
    case.objects[replacement['key']] = payload, 'application/json'
    return replacement


@pytest.mark.parametrize('damage', ['repairs', 'override', 'voice', 'partition', 'version', 'audit_indices', 'retained_failure', 'scene_mutation', 'source', 'claim'])
def test_v4_publisher_rechecks_frozen_partition_audit_voice_and_claims(case, damage):
    case.reviews[3].update(score=38, editorial_gate_passed=False)
    pointer = _prepare(case, repair_scene_indices=(3,), shot_prompt_overrides={3: 'Exact replacement shot.'})
    receipt = _record(case, pointer)
    if damage == 'repairs': receipt['repair_scene_indices'] = [2, 3]
    elif damage == 'override': receipt['shot_prompt_overrides'] = {'0': 'An unauthorized retained-scene change.'}
    elif damage == 'voice': receipt['approved_package']['_recovered_voice']['sha256'] = 'e' * 64
    elif damage == 'partition': receipt['approved_package']['_recovered_generated_media']['scenes'].pop('0')
    elif damage == 'version': receipt['approved_package']['_recovered_generated_media']['version'] = 2
    elif damage == 'scene_mutation': receipt['approved_package']['scenes'][0]['ai_prompt'] += ' changed'
    elif damage in {'audit_indices', 'retained_failure'}:
        audit = _record(case, receipt['audit_pointer'])
        if damage == 'audit_indices': audit['repair_scene_indices'] = [2, 3]
        else: audit['retained_visual_reviews'][0]['review']['score'] = 38
        receipt['audit_pointer'] = _rewrite_record(case, receipt['audit_pointer'], audit)
    elif damage == 'source': case.source['spec']['production_profile_revision'] = 'changed'; _save(case)
    else: case.client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + SOURCE, 'concurrent-owner')
    pointer = _rewrite_record(case, pointer, receipt)
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError): recovery.publish_preserved_visual_recovery(pointer)
    assert _snapshot(case) == before
