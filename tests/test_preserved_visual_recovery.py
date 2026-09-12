"""Offline end-to-end preservation -> fresh review -> ordinary v3 claim."""
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

from app.services import audio_checkpoint, preserved_visual_recovery as recovery, studio_state, visual_allocation_checkpoint
from test_paid_render_recovery import _task_runtime
from test_failed_visual_recovery import _render_runtime


SOURCE = '11111111-1111-4111-8111-111111111111'
PREP = '22222222-2222-4222-8222-222222222222'
CHILD = '33333333-3333-4333-8333-333333333333'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _immutable_boundary():
    """Execute the real director pre-provider boundary, without SDK imports."""
    source = Path(__file__).resolve().parents[1] / 'app' / 'services' / 'director.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    definitions = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name == '_immutable_narration_map'
    ) or (
        isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == '_MAX_PRODUCTION_SCENES' for target in node.targets)
    )]
    assert len(definitions) == 2
    namespace = {}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_immutable_narration_map']


@pytest.fixture
def case(tmp_path, monkeypatch, request):
    parameters = getattr(request, 'param', {})
    language = parameters.get('language', 'en')
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
        {'index': index, 'narration': f'Exact original spoken scene number {index} stays unchanged.', 'visual_queries': ['warehouse merchandise'],
         'ai_prompt': 'A genuine warehouse documentary shot.', 'transition': 'dip' if index == 3 else 'cut'} for index in range(6)],
        'sources': [{'url': 'https://investor.costco.com/overview/default.aspx', 'evidence': 'Official source describes the membership business.'},
                    {'url': 'https://www.costco.com/about.html', 'evidence': 'Company describes its warehouse operations.'}]}
    if 'narrations' in parameters:
        for scene, narration in zip(package['scenes'], parameters['narrations'], strict=True):
            scene['narration'] = narration
    audio = b'ID3' + b'exact unchanged saved narration' * 150
    audio_path = tmp_path / f'{SOURCE}.mp3'
    audio_path.write_bytes(audio)
    durations = [4.5, 5.0, 4.7, 4.8, 4.9, 5.2]
    voice = {'path': str(audio_path), 'spoken_texts': [scene['narration'] for scene in package['scenes']],
             'scene_durations': durations, 'duration_before_fit': 29.1, 'duration_after_fit': 29.1,
             'tempo_rate': 1.0, 'content_target_seconds': 29.5, 'reserved_tail_seconds': .5,
             'voice_name': 'Existing approved speech', 'voice_model': 'existing', 'voice_language_code': 'en'}
    voice['spoken_texts'] = parameters.get('spoken_texts', voice['spoken_texts'])
    voice['voice_language_code'] = language
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
              'spec': {'topic': 'How membership fees fund a warehouse business', 'language': language, 'duration_minutes': .5,
                       'channel_id': 'channel-profile', **options},
              'generated_asset_candidates': {**recovery._FLAGS, 'source_task_id': SOURCE, 'status': 'candidate_journal',
                                            'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0, 'entries': entries}}
    client.set(studio_state.JOB_PREFIX + SOURCE, json.dumps(source))
    client.hset(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE, mapping={'cap': '6', 'used': '6'})
    client.set(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + source['parent_id'], 'consumed-ancestor-claim')
    client.hset(studio_state.RETRY_DISPATCH_PREFIX + source['parent_id'], mapping={'state': 'dispatched', 'child_task_id': SOURCE})
    immutable = _immutable_boundary()
    def revalidate(original, topic, duration, language, opts, **kwargs):
        immutable(original, kwargs['immutable_candidate_narrations'])
        assert language == case_language and duration == .5
        if kwargs.get('immutable_scene_fields') is True:
            assert original['studio_options'] == opts
        assert kwargs['immutable_candidate_narrations'] == [scene['narration'] for scene in original['scenes']]
        assert 'short_story_qc' not in original
        return {**deepcopy(original), 'narration': ' '.join(scene['narration'] for scene in original['scenes']),
                'studio_options': deepcopy(opts), 'short_story_qc': {'fresh_test': True}}
    story = Mock(side_effect=revalidate)
    approved = Mock(side_effect=lambda value, topic: value.get('short_story_qc') == {'fresh_test': True})
    director = SimpleNamespace(revalidate_immutable_short_story=story, short_story_package_is_approved=approved)
    case_language = language
    from app.services.voice import normalize_turkish_tts
    monkeypatch.setattr(recovery, '_runtime', lambda: (tasks, director, SimpleNamespace(normalize_turkish_tts=normalize_turkish_tts)))
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


_DECIMAL_NARRATIONS = [
    'Maliyet 3,69 sent.', 'Tutar 1,25 sent.',
    'Üretim giderleri hesaba giriyor.', 'Mevcut paralar geçerli kalıyor.',
    'Dolaşımdaki paralar kullanılabilir.', 'Üretim maliyeti ayrıca ölçülüyor.',
]
_DECIMAL_LEGACY = ['Maliyet 3, 69 sent.', 'Tutar 1, 25 sent.', *_DECIMAL_NARRATIONS[2:]]


@pytest.mark.parametrize('case', [
    {'language': 'tr', 'narrations': _DECIMAL_NARRATIONS, 'spoken_texts': _DECIMAL_NARRATIONS},
    {'language': 'tr', 'narrations': _DECIMAL_NARRATIONS, 'spoken_texts': _DECIMAL_LEGACY},
], indirect=True, ids=['current', 'legacy'])
@pytest.mark.parametrize('repairs', [(), (3,)])
def test_hash_bound_current_or_legacy_turkish_voice_reaches_fresh_review_unchanged(case, repairs):
    before = _snapshot(case)
    source, original_objects = deepcopy(case.source), deepcopy(case.objects)
    receipt = _record(case, _prepare(case, repair_scene_indices=repairs))
    package = receipt['approved_package']
    assert _snapshot(case) == before and case.source == source
    assert all(case.objects[key] == value for key, value in original_objects.items())
    assert package['scenes'] == case.package['scenes']
    assert package['_recovered_voice']['spoken_texts'] == case.voice['spoken_texts']
    assert package['_recovered_voice']['sha256'] == _sha(case.audio)
    assert case.objects[package['_recovered_voice']['key']][0] == case.audio
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    assert receipt['qa_approved'] is False and receipt['requires_full_qa'] is True
    case.story.assert_called_once()
    case.visual.assert_called_once()


@pytest.mark.parametrize('case', [
    {'language': 'tr', 'narrations': _DECIMAL_NARRATIONS, 'spoken_texts': spoken}
    for spoken in [
        ['Maliyet 3,96 sent.', *_DECIMAL_NARRATIONS[1:]],
        ['Maliyet 3, 69 cent.', *_DECIMAL_LEGACY[1:]],
        ['Maliyet 3,  69 sent.', *_DECIMAL_LEGACY[1:]],
        ['Maliyet 3 69 sent.', *_DECIMAL_LEGACY[1:]],
        ['Maliyet 3, 69 sent değil.', *_DECIMAL_LEGACY[1:]],
        [_DECIMAL_NARRATIONS[0], *_DECIMAL_LEGACY[1:]],
        [_DECIMAL_LEGACY[0], *_DECIMAL_NARRATIONS[1:]],
    ]
] + [{'language': 'en', 'narrations': _DECIMAL_NARRATIONS, 'spoken_texts': _DECIMAL_LEGACY}],
    indirect=True, ids=['wrong_number', 'changed_word', 'extra_space', 'lost_comma',
                       'changed_meaning', 'mixed_current_first', 'mixed_legacy_first', 'other_language'])
def test_changed_or_mixed_saved_speech_stops_before_any_review(case):
    before, objects = _snapshot(case), deepcopy(case.objects)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        _prepare(case)
    assert _snapshot(case) == before and case.objects == objects
    case.story.assert_not_called()
    case.visual.assert_not_called()
    assert case.writes == []


@pytest.mark.parametrize('case', [
    {'language': 'tr', 'narrations': _DECIMAL_NARRATIONS, 'spoken_texts': _DECIMAL_LEGACY},
], indirect=True)
def test_legacy_spelling_does_not_admit_a_changed_audio_object(case):
    key = case.source['audio_candidate_checkpoint']['audio_key']
    raw, kind = case.objects[key]
    case.objects[key] = raw[:-1] + bytes([raw[-1] ^ 1]), kind
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        _prepare(case)
    assert _snapshot(case) == before and case.writes == []
    case.story.assert_not_called()
    case.visual.assert_not_called()


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


@pytest.mark.parametrize('repairs', [(), (3, 4, 5), (1, 3, 4, 5)])
def test_stripped_manifest_is_reconstructed_for_real_immutable_director_boundary(case, repairs):
    manifest_pointer = case.source['generated_asset_candidates']['entries'][0]
    stored = json.loads(case.objects[manifest_pointer['manifest_key']][0])['package']
    original = deepcopy(stored)
    spoken = [scene['narration'] for scene in stored['scenes']]
    assert 'narration' not in stored and 'studio_options' not in stored
    with pytest.raises(RuntimeError, match='exact scene mapping'):
        _immutable_boundary()(stored, spoken)  # the live failure, before any provider
    pointer = _prepare(case, repair_scene_indices=repairs,
                       shot_prompt_overrides={index: f'Exact replacement visual {index}.' for index in repairs})
    supplied = case.story.call_args.args[0]
    assert _immutable_boundary()(supplied, spoken) == dict(enumerate(spoken))
    assert supplied['narration'] == ' '.join(spoken)
    assert case.story.call_args.args[4] == recovery._options(case.source)
    assert supplied['studio_options'] == recovery._options(case.source)
    assert case.story.call_args.kwargs['immutable_scene_fields'] is True
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    assert audit['package']['narration'] == receipt['approved_package']['narration'] == ' '.join(spoken)
    assert receipt['approved_package']['_recovered_voice']['sha256'] == _sha(case.audio)
    assert case.objects[receipt['approved_package']['_recovered_voice']['key']][0] == case.audio
    assert json.loads(case.objects[manifest_pointer['manifest_key']][0])['package'] == original
    assert recovery.publish_preserved_visual_recovery(pointer)['status'] == 'checkpoint_published'


@pytest.mark.parametrize('repairs', [(), (3,), (1, 3, 4, 5)])
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


@pytest.mark.parametrize('repairs', [(), (3,), (1, 3, 4, 5)])
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


@pytest.mark.parametrize('repairs', [(3,), (0, 3), (0, 3, 5), (1, 3, 4, 5)])
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
    ((-1,), None), ((6,), None), ((0, 1, 2, 3, 4), None), ((), {3: 'A new shot'}),
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


@pytest.mark.parametrize('repairs', [(), (3,)])
@pytest.mark.parametrize('change', ['retained_prompt', 'retained_queries', 'repair_queries', 'narration', 'order',
                                   'inplace', 'options', 'sources', 'title', 'new_field', 'scene_index_type'])
def test_critic_cannot_change_frozen_scene_package_fields_or_immutable_audio(case, change, repairs):
    original = case.story.side_effect
    def changed(package, *args, **kwargs):
        result = original(package, *args, **kwargs)
        if change == 'retained_prompt': result['scenes'][0]['ai_prompt'] += ' changed'
        elif change == 'retained_queries': result['scenes'][0]['visual_queries'] = ['changed query']
        elif change == 'repair_queries': result['scenes'][3]['visual_queries'] = ['changed query']
        elif change == 'narration': result['scenes'][3]['narration'] += ' New words.'
        elif change == 'order': result['scenes'][0], result['scenes'][1] = result['scenes'][1], result['scenes'][0]
        elif change == 'options': result['studio_options']['quality_threshold'] = 1
        elif change == 'sources': result['sources'][0]['evidence'] += ' Another claim.'
        elif change == 'title': result['title'] += ' Changed'
        elif change == 'new_field': result['description'] = 'New editorial content.'
        elif change == 'scene_index_type': result['scenes'][0]['index'] = 0.0
        else:
            package['scenes'][0]['ai_prompt'] += ' mutation in supplied candidate'
            result['scenes'] = package['scenes']
        return result
    case.story.side_effect = changed
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case, repair_scene_indices=repairs,
                 shot_prompt_overrides={index: 'Exact replacement shot.' for index in repairs})
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


@pytest.mark.parametrize('retained_index', [0, 2])
@pytest.mark.parametrize('damage', ['score', 'editorial_gate'])
def test_four_repairs_cannot_approve_either_bad_retained_clip_or_reset_six_paid_ledger(case, retained_index, damage):
    repairs = (1, 3, 4, 5)
    for index in repairs:
        case.reviews[index].update(score=38, editorial_gate_passed=False)
    if damage == 'score':
        case.reviews[retained_index]['score'] = 75
    else:
        case.reviews[retained_index]['editorial_gate_passed'] = False
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case, repair_scene_indices=repairs)
    audit = _record(case, caught.value.diagnostic_pointer)
    assert audit['status'] == 'visual_preparation_rejected'
    assert audit['repair_scene_indices'] == list(repairs)
    assert len(audit['retained_visual_reviews']) == len(case.visual.call_args.args[1]) == 6
    assert _snapshot(case) == before and len(case.writes) == 1
    assert case.client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    assert not case.client.exists(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE)


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


def _actual_director(case, monkeypatch):
    from app import config
    from app.services import director, research

    settings = SimpleNamespace(studio_plan_provider='gemini', gemini_api_key='mock-only',
                               gemini_model='existing-model', openai_api_key='mock-only', openai_model='existing-openai-model',
                               gemini_critic_enabled=False)
    monkeypatch.setattr(director, 'settings', settings)
    monkeypatch.setattr(research, 'settings', settings)
    monkeypatch.setattr(config.settings, 'studio_production_short_paid_create_cap', 2)
    writer = Mock(side_effect=AssertionError('Existing assets must not invoke a writer'))
    monkeypatch.setattr(director, '_run_director', writer)
    monkeypatch.setattr(director, 'OpenAI', Mock(side_effect=AssertionError('Unexpected provider')))

    def response(prompt, **kwargs):
        assert prompt.startswith('Act as an independent')
        return json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])[0]

    model = Mock(side_effect=response)
    monkeypatch.setattr(director, 'generate_gemini_json', model)
    video = Mock(side_effect=AssertionError('No new generated video'))
    voice = SimpleNamespace(generate_voice=Mock(side_effect=AssertionError('No new TTS')))
    case.tasks.generate_scene = video
    monkeypatch.setattr(recovery, '_runtime', lambda: (case.tasks, director, voice))
    return SimpleNamespace(director=director, model=model, writer=writer, video=video, voice=voice)


@pytest.mark.parametrize('repairs', [(), (3,), (1, 3, 4, 5)])
@pytest.mark.parametrize('calibrated', [False, True])
def test_actual_director_reviews_six_existing_assets_once_without_new_generation_cap(case, monkeypatch, repairs, calibrated):
    if calibrated:
        from test_preserved_english_spoken_budget import _calibrate
        _calibrate(case)
    actual = _actual_director(case, monkeypatch)
    options = recovery._options(case.source)
    overrides = {index: f'Exact replacement shot {index}; sealed parcels on the same warehouse table.' for index in repairs}
    expected = recovery._immutable_shooting_package(case.package, overrides, options)
    before, original = _snapshot(case), deepcopy(case.package)
    objects_before = dict(case.objects)
    assert actual.director.preview_authored_ai_limit(options, 6, .5) == 2
    assert sum(bool(scene['ai_prompt']) for scene in expected['scenes']) == 6
    with pytest.raises(RuntimeError, match='authored paid-generation limit'):
        actual.director.revalidate_immutable_short_story(
            expected, case.source['spec']['topic'], .5, 'en', options,
            immutable_candidate_narrations=case.voice['spoken_texts'],
            **({'verified_spoken_word_budget': expected['spoken_word_budget']} if calibrated else {}))
    actual.model.assert_not_called()

    pointer = _prepare(case, repair_scene_indices=repairs, shot_prompt_overrides=overrides)
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    reviewed = audit['package']
    assert recovery._story_fields(reviewed) == expected
    assert actual.director.short_story_package_is_approved(reviewed, case.source['spec']['topic'])
    assert reviewed['stock_scene_qc']['generator_calls'] == reviewed['stock_scene_qc']['attempts_used'] == 0
    assert reviewed['stock_scene_qc']['critic_calls'] == 1
    actual.model.assert_called_once()
    request = actual.model.call_args
    assert request.kwargs['retry_once'] is request.kwargs['google_search'] is False
    context = json.JSONDecoder().raw_decode(request.args[0].split('\n', 2)[2])[0]
    assert context['complete_immutable_scenes'] == expected['scenes']
    assert context['sources'] == expected['sources']
    actual.writer.assert_not_called(); actual.video.assert_not_called(); actual.voice.generate_voice.assert_not_called()
    assert _snapshot(case) == before and case.package == original
    assert all(case.objects[key] == value for key, value in objects_before.items())
    assert receipt['new_paid_create_requests'] == receipt['new_tts_requests'] == 0
    assert case.objects[receipt['approved_package']['_recovered_voice']['key']][0] == case.audio
    assert case.visual.call_args.args[0] == expected['scenes']
    assert recovery.publish_preserved_visual_recovery(pointer)['status'] == 'checkpoint_published'
    checkpoint = json.loads(case.client.get(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE))
    assert checkpoint['approved_package'] == receipt['approved_package']
    assert case.client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE) == {'cap': '6', 'used': '6'}
    actual.model.assert_called_once()


@pytest.mark.parametrize('damage', ['negative_story', 'negative_language', 'protocol'])
def test_actual_director_rejection_is_durable_without_writer_or_retry(case, monkeypatch, damage):
    actual = _actual_director(case, monkeypatch)
    response = actual.model.side_effect

    def reject(prompt, **kwargs):
        if damage == 'protocol':
            raise actual.director.GeminiGenerationError('private provider error')
        result = response(prompt, **kwargs)
        key = 'natural_spoken_language' if damage == 'negative_language' else 'causal_claim_supported'
        result['story_review'][key] = False
        result['story_review']['reason'] = f'{key}: the supplied scene lacks the required evidence.'
        if damage == 'negative_language':
            result['story_review']['natural_spoken_language_evidence'] = 'Scene 0 "Exact spoken" is not natural narration.'
        return result

    actual.model.side_effect = reject
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case)
    assert _record(case, caught.value.diagnostic_pointer)['status'] == 'story_review_rejected_or_unavailable'
    actual.model.assert_called_once(); actual.writer.assert_not_called()
    actual.video.assert_not_called(); actual.voice.generate_voice.assert_not_called(); case.visual.assert_not_called()
    assert _snapshot(case) == before and len(case.writes) == 1


@pytest.mark.parametrize('damage', ['scene', 'sources', 'scene_index_type'])
def test_visual_reviewer_cannot_mutate_the_frozen_prepared_story(case, damage):
    def mutate(scenes, *args, **kwargs):
        if damage == 'scene': scenes[0]['ai_prompt'] += ' New action.'
        elif damage == 'scene_index_type': scenes[0]['index'] = 0.0
        else: kwargs['evidence_sources'][0]['evidence'] += ' Changed evidence.'
        return {'reviews': case.reviews, 'missing_review_indices': []}
    case.visual.side_effect = mutate
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError) as caught:
        _prepare(case)
    assert _record(case, caught.value.diagnostic_pointer)['status'] == 'visual_review_unavailable'
    assert _snapshot(case) == before and len(case.writes) == 1


@pytest.mark.parametrize('repairs', [(), (3,)])
@pytest.mark.parametrize('damage', ['scene', 'sources', 'title', 'options', 'new_field', 'scene_index_type', 'receipt_index_type'])
def test_rehashed_audit_and_receipt_cannot_redefine_the_frozen_published_story(case, repairs, damage):
    pointer = _prepare(case, repair_scene_indices=repairs)
    receipt = _record(case, pointer)
    audit = _record(case, receipt['audit_pointer'])
    package = receipt['approved_package']
    if damage == 'scene': package['scenes'][0]['ai_prompt'] += ' Changed'
    elif damage == 'sources': package['sources'][0]['evidence'] += ' New unsupported claim.'
    elif damage == 'title': package['title'] += ' Changed'
    elif damage == 'options': package['studio_options']['quality_threshold'] = 1
    elif damage in {'scene_index_type', 'receipt_index_type'}: package['scenes'][0]['index'] = 0.0
    else: package['description'] = 'New unreviewed editorial content.'
    if damage != 'receipt_index_type':
        audit['package'] = {key: deepcopy(value) for key, value in package.items()
                            if key not in {'_recovered_voice', '_recovered_generated_media'}}
    receipt['package_sha256'] = case.tasks._recovery_package_sha256(package)
    for key in ('_recovered_voice', '_recovered_generated_media'):
        package[key]['package_sha256'] = receipt['package_sha256']
    receipt['audit_pointer'] = _rewrite_record(case, receipt['audit_pointer'], audit)
    pointer = _rewrite_record(case, pointer, receipt)
    before = _snapshot(case)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        recovery.publish_preserved_visual_recovery(pointer)
    assert _snapshot(case) == before
