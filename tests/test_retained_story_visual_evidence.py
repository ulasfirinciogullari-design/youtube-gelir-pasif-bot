"""Genuine disposable completion history and actual retained sampling, no network.

Model replies are synthetic semantic fixtures. Passing these tests establishes
source/request/receipt validation, never actual image quality or publication QA.
"""
from copy import deepcopy, copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import audio_checkpoint
from app.services import abacus_router_audio_adapter as audio_adapter
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_journal as journal
from app.services import production_connection_continuity as continuity
from app.services import preserved_visual_recovery as recovery
from app.services import retained_cut_evidence as cuts
from app.services import retained_sampled_input_linkage as sampled
from app.services import retained_review_completion_plan as completion
from app.services import retained_story_visual_evidence as reader
from app.services import immutable_story_review_contract as story_contract
from app.services import visual_qc as visual
from app.services import render
from app.services.production_spend import SpendPolicy
from app.services.voice import normalize_turkish_tts
from app.services.voice_candidate_recovery import _voice_result
import test_abacus_router_audio_review_journal as base_audio
from test_abacus_router_audio_review_journal import NOW, KEY
from test_production_connection_continuity import case, REVISION, _dump
from test_scheduled_shot_prompt_preflight import case as planning_case, SHORT
from test_retained_cut_evidence import real_media, FakeS3, BUCKET, ENDPOINT
from test_retained_review_credential_successor import prepared, Intercept
from test_retained_router_protocol_probe import frozen_three
from test_retained_review_completion_plan import completed_probe, commission
from test_abacus_router_protocol_diagnostic import wire, forbid_live_transport
from test_abacus_router_review_runtime import payload
from test_visual_qc import _review

STORY, VISUAL = journal.PURPOSES
ERROR = '^retained_story_visual_'


def put_fixture(s3, key, value, mime='application/json'):
    raw = cuts._raw(value) if type(value) is dict else value
    s3.objects[key] = (raw, mime)
    return raw


@pytest.fixture
def source(case, planning_case, real_media, tmp_path, monkeypatch):
    # Full collection includes lightweight app.config stubs. Load the existing
    # source-extracted pure normalizer inside this fixture, without importing
    # Celery or copying the recovery loader's mocked quality/approval helpers.
    import app
    import sys
    from types import ModuleType
    from test_paid_render_recovery import _task_runtime
    tasks = ModuleType('app.tasks')
    tasks._normalized_options = _task_runtime()._normalized_options
    monkeypatch.setitem(sys.modules, 'app.tasks', tasks)
    monkeypatch.setattr(app, 'tasks', tasks, raising=False)
    # Populate all actual source preimages BEFORE any legacy journal is
    # commissioned. No existing occupied history is relabelled or repaired.
    initial = base_audio.source.__wrapped__(case)
    s3 = FakeS3()
    monkeypatch.setattr(cuts.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    work = tmp_path / 'source-media'
    work.mkdir()
    paths = []
    for index in range(6):
        path = work / f'raw-{index}.mp4'
        path.write_bytes(real_media.clip.read_bytes() + struct.pack('>I4s', 9, b'free') + bytes([index]))
        paths.append((path, 'runway'))
    audio = real_media.audio.read_bytes()
    audio_request = audio_adapter.prepare_blind_asr_request(audio, api_key=KEY)
    descriptor = audio_request.audio
    candidate = deepcopy(planning_case.package)
    for scene in candidate['scenes']:
        scene['ai_prompt'] = SHORT
    package = audio_checkpoint._candidate_package(candidate)
    voice = {**deepcopy(real_media.box.voice), 'spoken_texts': [
        normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
        for index, scene in enumerate(package['scenes'])],
        'duration_before_fit': real_media.box.voice['duration_after_fit'], 'tempo_rate': 1.0,
        'voice_language_code': 'tr'}
    voice.pop('path')
    voice = audio_checkpoint._candidate_voice(voice, 6)
    audio_key = f"audio_candidates/{continuity.LEAF_ID}/{descriptor['sha256']}/candidate.mp3"
    metadata = {**initial.metadata, 'package': package, 'package_sha256': reader._hash(package),
        'voice': voice, 'audio': {'key': audio_key, 'sha256': descriptor['sha256'], 'size': len(audio)}}
    metadata_raw = cuts._raw(metadata)
    pointer = {**initial.metadata['audio'], **json.loads(case.client.get(continuity._JOB + continuity.LEAF_ID))['audio_candidate_checkpoint']}
    pointer.update(audio_sha256=descriptor['sha256'], metadata_sha256=reader._sha(metadata_raw),
        package_sha256=reader._hash(package), size=len(audio), audio_key=audio_key,
        metadata_key=f"audio_candidates/{continuity.LEAF_ID}/{descriptor['sha256']}/metadata-{reader._sha(metadata_raw)}.json")
    # Use only the original checkpoint schema; metadata.audio has a different key field.
    pointer.pop('key', None); pointer.pop('sha256', None)
    put_fixture(s3, audio_key, audio, 'audio/mpeg')
    put_fixture(s3, pointer['metadata_key'], metadata_raw)
    entries = []
    for index, (path, provider) in enumerate(paths):
        identity = cuts._file(path)[0]
        raw = {'key': f"generated_candidates/{continuity.LEAF_ID}/raw/{identity['sha256']}.mp4",
            **identity, 'provider': provider, 'provider_attempts': 1, 'start_fraction': 0.0,
            'forbid_loop': True, 'synthetic_motion_only': False}
        generated_audio = {'key': f"generated_candidates/{continuity.LEAF_ID}/voice/{descriptor['sha256']}.mp3",
                           'sha256': descriptor['sha256'], 'size': len(audio)}
        manifest = {**recovery._FLAGS, 'source_task_id': continuity.LEAF_ID,
            'status': 'preserved_candidate', 'scene_index': index, 'phase': 'initial_generation',
            'package_sha256': 'd'*64, 'candidate_package_sha256': reader._hash(package),
            'package': package, 'voice': voice, 'audio': generated_audio, 'raw': raw}
        encoded = cuts._raw(manifest)
        manifest_sha = reader._sha(encoded)
        manifest_key = f'generated_candidates/{continuity.LEAF_ID}/manifests/{manifest_sha}.json'
        entry = {**continuity._CANDIDATE_FLAGS, 'source_task_id': continuity.LEAF_ID,
            'status': 'preserved_candidate', 'scene_index': index, 'phase': 'initial_generation',
            'provider': provider, 'raw_key': raw['key'], 'raw_sha256': identity['sha256'],
            'raw_size': identity['size'], 'audio_sha256': descriptor['sha256'], 'package_sha256': 'd'*64,
            'manifest_key': manifest_key, 'manifest_sha256': manifest_sha, 'manifest_size': len(encoded)}
        entries.append(entry)
        put_fixture(s3, manifest_key, encoded)
        put_fixture(s3, raw['key'], path.read_bytes(), 'video/mp4')
        put_fixture(s3, generated_audio['key'], audio, 'audio/mpeg')
    for task in continuity.LINEAGE:
        key = continuity._JOB + task
        job = json.loads(case.client.get(key))
        options = tasks._normalized_options({**recovery._options(job), 'content_style': 'documentary',
            'visual_mix': 'ai_first', 'quality_threshold': 86}, .5)
        job['spec'] = {**job['spec'], **options}
        if task == continuity.LEAF_ID:
            job['audio_candidate_checkpoint'] = pointer
            job['generated_asset_candidates']['entries'] = entries
        case.client.set(key, cuts._raw(job).decode())
    proof = continuity.prepare_connection_continuity(continuity.ROOT_ID, continuity.LEAF_ID,
        continuity.CHANNEL_ID, REVISION, client=case.client)
    policy = {**initial.policy, 'continuity_sha256': proof['receipt_sha256'], 'audio': descriptor,
        'audio_checkpoint_sha256': reader._hash(pointer), 'source_metadata_sha256': reader._sha(metadata_raw),
        'audio_candidate_package_sha256': reader._hash(package), 'voice_contract_sha256': reader._hash(voice),
        'scene_durations_sha256': reader._hash(voice['scene_durations']),
        'expected_narration_sha256': reader._sha(' '.join(voice['spoken_texts']).encode())}
    audio_path = work / 'original.mp3'; audio_path.write_bytes(audio)
    return SimpleNamespace(policy=policy, metadata=metadata, metadata_bytes=metadata_raw,
        expected=' '.join(voice['spoken_texts']), prepared=audio_request,
        asr_result=initial.asr_result, s3=s3, work=work, paths=paths, audio_path=audio_path)


@pytest.fixture
def produced(completed_probe, monkeypatch):
    box = completed_probe
    source = box.source
    box.s3 = source.s3
    cap = commission(box)
    box.completion_cap = cap
    box.config.studio_abacus_router_retained_review_enabled = True
    monkeypatch.setattr(cuts.storage, '_client', lambda *, single_attempt=False: box.s3)
    foundation = SimpleNamespace(client=box.client, clock=lambda: NOW,
        policy=SpendPolicy(**{name: 0 for name in runtime._CAPS}))
    monkeypatch.setattr(runtime.spending, 'configured_ledger', Mock(return_value=foundation))
    original, fingerprint = recovery._state(continuity.LEAF_ID, box.client)
    package = recovery._immutable_shooting_package(recovery._manifests(box.s3, original)[0]['package'],
                                                  {}, recovery._options(original))
    contract = story_contract.derive_immutable_story_review_contract(package, original['spec']['topic'], .5,
        'tr', recovery._options(original), immutable_candidate_narrations=[row['narration'] for row in package['scenes']])
    prompt = contract['request']['parts'][0]['text']
    story_result = json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])[0]
    def reply(request):
        body = json.loads(request.content)
        visual_request = any(part['type'] == 'image_url' for part in body['messages'][1]['content'])
        result = {'reviews': [_review(index, recurring_identity_continuity_applicable=True,
            recurring_identity_continuity_matches=True) for index in range(6)]} if visual_request else story_result
        response = payload()
        response['choices'][0]['message']['content'] = json.dumps(result)
        box.wire.chunks = [cuts._raw(response)]
    box.wire.on_request = reply
    # This untrusted marker is intentionally false. Only the actual parsed
    # critic replies and immutable stripped source core can establish semantics.
    full_package = {**contract['candidate'], 'short_story_qc': {'accepted': False}, 'stock_scene_qc': {'accepted': False}}
    source_binding = {'source_task_id': continuity.LEAF_ID, 'source_state_sha256': fingerprint,
        'source_spec_sha256': reader._hash(original['spec']),
        'source_journal_sha256': reader._hash(original['generated_asset_candidates']),
        'source_metadata_sha256': reader._sha(source.metadata_bytes),
        'audio': {'sha256': source.policy['audio']['sha256'], 'size': source.policy['audio']['bytes']}}
    with runtime.retained_router_review_scope(continuity.LEAF_ID, completion_plan=cap, capture_transport=True) as scope:
        runtime.generate_retained_router_review(**contract['request'])
        sink = artifacts.RetainedRouterReviewArtifactSink(box.s3, bucket=BUCKET)
        first = sink.persist(runtime.retained_router_review_artifacts()[STORY])
        voice = {**_voice_result(source.metadata['voice'], 6), 'path': str(source.audio_path)}
        derived = cuts.prepare_retained_cuts(full_package, voice, source.paths, source.work,
            source_binding=source_binding, raw_bindings=[{**cuts._file(path)[0], 'provider': provider}
                                                        for path, provider in source.paths])
        box.cut_receipt = cuts.persist_retained_cuts(derived, box.s3, bucket=BUCKET)
        collector = sampled.begin_sampled_input_capture(derived, full_package)
        result = visual.review_scene_visuals(full_package['scenes'], derived.inputs,
            Path(derived.inputs[0][0]['path']).parent, 6, topic=original['spec']['topic'],
            story_scenes=full_package['scenes'], content_style=full_package['studio_options']['content_style'],
            evidence_sources=full_package['sources'], _missing_review_attempts=0,
            _score_reason_consistency_attempts=0, _retained_sample_capture=collector)
        assert len(result['reviews']) == 6
        second = sink.persist(runtime.retained_router_review_artifacts()[VISUAL], prior_story_anchor=first)
        box.link_receipt = sampled.persist_sampled_input_link(collector,
            runtime.retained_router_review_artifacts()[VISUAL], second)
    audit = {**recovery._FLAGS, 'package': full_package, 'status': 'synthetic_untrusted_status'}
    raw = cuts._raw(audit)
    digest = reader._sha(raw)
    key = f'recovery/{continuity.LEAF_ID}/preserved_visual/audit-{digest}.json'
    put_fixture(box.s3, key, raw)
    box.audit_pointer = {'version': 1, 'source_task_id': continuity.LEAF_ID, 'kind': 'audit',
                         'key': key, 'sha256': digest, 'size': len(raw)}
    box.first, box.second = first.receipt, second.receipt
    box.before_read = _dump(box.client)
    return box


def read(box, *, client=None, **kwargs):
    return reader.read_retained_story_visual_evidence(client or box.client, box.s3,
        **{'bucket': BUCKET, 'completion_plan': box.completion_cap, 'audit_pointer': box.audit_pointer, **kwargs})


def test_genuine_completion_source_cuts_samples_and_semantics_are_read_only(produced, monkeypatch, subtests):
    box = produced
    before, puts, calls = _dump(box.client), len(box.s3.puts), len(box.wire.calls)
    assert calls == 3  # Actual disposable tiny probe, STORY and VISUAL MockHTTPX.
    client = Intercept(box.client)
    # Explicitly prohibit any replay/reobservation or provider after persistence.
    def forbidden(*args, **kwargs):
        raise AssertionError('persisted reader must not observe, send, settle or render')
    monkeypatch.setattr(artifacts.adapter, 'observe_router_response', forbidden)
    monkeypatch.setattr(journal.RouterReviewJournal, 'settle', forbidden)
    monkeypatch.setattr(journal.RouterReviewJournal, '_fresh', forbidden)
    monkeypatch.setattr(runtime, 'generate_retained_router_review', forbidden)
    monkeypatch.setattr(render, 'normalize_clip', forbidden)
    # A later application revision does not invalidate genuine historical cut
    # build identity or mint a new request window.
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'b' * 40)
    value = read(box, client=client)
    assert type(value) is reader.RetainedStoryVisualEvidence
    assert value.record['component_pass'] is True and value.record['preclaim_only'] is True
    assert all(value.record[name] is expected for name, expected in reader._FLAGS.items())
    assert client.executions == [('PING',)]
    assert _dump(box.client) == before and len(box.s3.puts) == puts and len(box.wire.calls) == calls
    commitment = value.commitments
    assert commitment['journal_keys'] == list(completion.STORY_KEYS)
    assert commitment['cut_manifest'] == box.cut_receipt['manifest']
    assert commitment['sampled_link_anchor_sha256'] == box.link_receipt['anchor_sha256']
    assert value.record['story_diagnostic']['qa_approved'] is False
    commitment['journal_keys'].append('arbitrary namespace')
    assert value.commitments['journal_keys'] == list(completion.STORY_KEYS)
    with pytest.raises(TypeError): reader.RetainedStoryVisualEvidence()
    with pytest.raises(TypeError): copy(value)
    with pytest.raises(reader.RetainedStoryVisualEvidenceError):
        object.__new__(reader.RetainedStoryVisualEvidence).record
    snapshots = {key: box.client.dump(key) for key in box.client.scan_iter('*')}
    objects = deepcopy(box.s3.objects)
    for fault in ('link_missing', 'link_ttl', 'story_anchor', 'visual_anchor', 'cut_blob', 'raw_blob',
                  'metadata_blob', 'audit_core', 'sample_manifest', 'legacy_history', 'child_rollback',
                  'source_claim', 'source_race', 'lost_ack', 'malformed_ack', 'fake_cap', 'wrong_manifest'):
        with subtests.test(fault=fault):
            for key in list(box.client.scan_iter('*')): box.client.delete(key)
            for key, raw in snapshots.items(): box.client.restore(key, 0, raw)
            box.s3.objects = deepcopy(objects)
            options, client = {}, Intercept(box.client)
            if fault == 'link_missing': box.client.delete(box.link_receipt['anchor_key'])
            elif fault == 'link_ttl': box.client.pexpire(box.link_receipt['anchor_key'], 100000)
            elif fault == 'story_anchor': box.client.delete(box.first['anchor_key'])
            elif fault == 'visual_anchor': box.client.delete(box.second['anchor_key'])
            elif fault in ('cut_blob', 'raw_blob', 'metadata_blob'):
                if fault == 'cut_blob': key = value.commitments['cuts'][0]['key']
                elif fault == 'metadata_blob': key = json.loads(box.client.get(continuity._JOB + continuity.LEAF_ID))['audio_candidate_checkpoint']['metadata_key']
                else: key = json.loads(box.client.get(continuity._JOB + continuity.LEAF_ID))['generated_asset_candidates']['entries'][0]['raw_key']
                raw, mime = box.s3.objects[key]; box.s3.objects[key] = (raw + b'changed', mime)
            elif fault == 'audit_core':
                audit = json.loads(box.s3.objects[box.audit_pointer['key']][0]); audit['package']['title'] = 'substituted'
                raw = cuts._raw(audit); digest = reader._sha(raw)
                key = f'recovery/{continuity.LEAF_ID}/preserved_visual/audit-{digest}.json'
                put_fixture(box.s3, key, raw)
                options['audit_pointer'] = {**box.audit_pointer, 'key': key, 'sha256': digest, 'size': len(raw)}
            elif fault == 'sample_manifest':
                pointer = box.link_receipt['pointer']; raw, mime = box.s3.objects[pointer['key']]
                record = json.loads(raw); record['events'][0]['moment_index'] = 0
                box.s3.objects[pointer['key']] = (cuts._raw(record), mime)
            elif fault == 'legacy_history': box.client.set(completion.HISTORICAL_KEYS[0], '{}')
            elif fault == 'child_rollback':
                state = json.loads(box.client.get(completion.STORY_KEYS[0])); state['slots'] = {}
                box.client.set(completion.STORY_KEYS[0], cuts._raw(state).decode())
            elif fault in ('source_claim', 'source_race'):
                def claim(*unused):
                    key = continuity._JOB + continuity.LEAF_ID
                    job = json.loads(box.client.get(key)); job['retry_claimed'] = True
                    box.client.set(key, cuts._raw(job).decode())
                if fault == 'source_claim': claim()
                else: client = Intercept(box.client, before=claim)
            elif fault == 'lost_ack':
                def lose(commands, result): raise ConnectionError('PRIVATE_BACKEND_TEXT')
                client = Intercept(box.client, after=lose)
            elif fault == 'malformed_ack': client = Intercept(box.client, after=lambda *args: [1])
            elif fault == 'fake_cap': options['completion_plan'] = object()
            elif fault == 'wrong_manifest':
                other = completion._authorization(box.completion_cap._manifest_bytes)
                manifest = json.loads(other._manifest_bytes)
                manifest['attestation']['owner_authorization_sha256'] = '9'*64
                object.__setattr__(other, '_manifest_bytes', cuts._raw(manifest))
                options['completion_plan'] = other
            with pytest.raises(reader.RetainedStoryVisualEvidenceError, match=ERROR) as caught:
                read(box, client=client, **options)
            assert 'PRIVATE_BACKEND_TEXT' not in str(caught.value)
            assert all(command == ('PING',) for command in client.executions)
            assert len(box.s3.puts) == puts and len(box.wire.calls) == calls


def test_actual_completion_unknown_and_unselected_legacy_never_read_storage(completed_probe):
    box = completed_probe
    box.s3 = box.source.s3
    cap = commission(box)
    ledger = journal.RouterReviewJournal(box.client, clock=lambda: NOW, completion_plan=cap)
    ledger.reserve(STORY, box.new_request)
    before = _dump(box.client)
    for authority in (cap, None, object()):
        with pytest.raises(reader.RetainedStoryVisualEvidenceError, match=ERROR):
            reader.read_retained_story_visual_evidence(box.client, box.s3, bucket=BUCKET,
                completion_plan=authority, audit_pointer={})
    assert box.s3.reads == [] and not box.s3.puts and _dump(box.client) == before


@pytest.fixture
def component_contracts(planning_case):
    """Pure semantic fixtures only; the test above owns real storage authority."""
    package = deepcopy(planning_case.package)
    for scene in package['scenes']:
        scene['ai_prompt'] = SHORT
    contract = story_contract.derive_immutable_story_review_contract(package,
        'Explain warehouse membership.', .5, 'tr', planning_case.options,
        immutable_candidate_narrations=[row['narration'] for row in package['scenes']])
    prompt = contract['request']['parts'][0]['text']
    result = json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])[0]
    from test_visual_qc import JPEG_BYTES
    visual_contract = reader.visual_semantics.derive_strict_visual_review_contract(package['scenes'],
        [[{'path': f'{index}.mp4', 'generated': True, 'source_type': 'generated', 'generation_provider': 'runway'}]
         for index in range(6)], topic='Explain warehouse membership.', story_scenes=package['scenes'],
        content_style='documentary', evidence_sources=package['sources'], samples=[
            {'scene_index': index, 'candidate_index': 0, 'moment_index': moment, 'jpeg': JPEG_BYTES}
            for index in range(6) for moment in (3, 0, 1, 2, 4)])
    return contract, result, visual_contract


@pytest.mark.parametrize('failure', ['causal', 'natural', 'ending', 'boolean', 'extra_approval'])
def test_validated_parsed_story_still_requires_complete_semantics(component_contracts, failure):
    contract, result, _ = component_contracts
    assert not reader._story_component(result, contract)['story_failure']
    if failure == 'causal': result['story_review']['causal_claim_supported'] = False
    elif failure == 'natural':
        result['story_review']['natural_spoken_language'] = False
        result['story_review']['natural_spoken_language_evidence'] = 'Scene 1 “Members pay” sounds unnatural.'
    elif failure == 'ending': result['ending_pair']['everyday_benefit_visible'] = False
    elif failure == 'boolean': result['story_review']['causal_claim_supported'] = 1
    elif failure == 'extra_approval': result['qa_approved'] = True
    with pytest.raises(reader.RetainedStoryVisualEvidenceError, match=ERROR):
        reader._story_component(result, contract)


@pytest.mark.parametrize('failure', ['score', 'editorial', 'identity', 'subject', 'missing',
                                    'duplicate', 'moment_bool', 'positive_rejection', 'extra_approval'])
def test_validated_parsed_visual_still_requires_every_scene_and_hard_gate(component_contracts, failure):
    _, _, contract = component_contracts
    result = {'reviews': [_review(index, recurring_identity_continuity_matches=True)
                          for index in range(6)]}
    assert len(reader._visual_component(result, contract, 86)) == 6
    if failure == 'score': result['reviews'][2]['score'] = 85
    elif failure == 'editorial': result['reviews'][2]['major_visual_artifact_visible'] = True
    elif failure == 'identity': result['reviews'][2]['authored_identity_or_material_conflict_visible'] = True
    elif failure == 'subject': result['reviews'][2]['subject_visible'] = False
    elif failure == 'missing': result['reviews'].pop()
    elif failure == 'duplicate': result['reviews'][2] = deepcopy(result['reviews'][1])
    elif failure == 'moment_bool': result['reviews'][2]['evidence_moment_indices'] = [False]
    elif failure == 'positive_rejection':
        result['reviews'][2].update(score=40, reason='The clip matches the narration and scene requirements.')
    elif failure == 'extra_approval': result['qa_approved'] = True
    with pytest.raises(reader.RetainedStoryVisualEvidenceError, match=ERROR):
        reader._visual_component(result, contract, 86)


def timeline_fixture():
    # Six equal fractional scenes must use cumulative rounding: independently
    # rounding 145.5 six times incorrectly allocates876 rather than873 frames.
    scenes = [{'transition': 'dip' if index == 3 else 'cut'} for index in range(6)]
    voice = {'scene_durations': [4.15] * 6}
    duration = 4.15 * (29.1 / sum(voice['scene_durations']))
    record = {'measured_voice_duration': 29.1, 'cuts': [
        {'timeline_duration': duration, 'transition': scenes[index]['transition'],
         'target_frames': count, 'actual_frames': count}
        for index, count in enumerate((145, 146, 145, 146, 146, 145))]}
    return record, scenes, voice


def test_source_timeline_preserves_cumulative_fractional_frame_rounding():
    record, scenes, voice = timeline_fixture()
    reader._validate_timeline(record, scenes, voice)
    assert sum(row['target_frames'] for row in record['cuts']) == 873
    assert sum(round(row['timeline_duration'] * 30) for row in record['cuts']) != 873


@pytest.mark.parametrize('damage', ['equal_total_frames', 'equal_total_durations', 'source_durations', 'transition'])
def test_same_total_cannot_conceal_changed_source_scene_allocation(damage):
    record, scenes, voice = timeline_fixture()
    if damage == 'equal_total_frames':
        for name in ('target_frames', 'actual_frames'):
            record['cuts'][0][name] += 1
            record['cuts'][1][name] -= 1
    elif damage == 'equal_total_durations':
        record['cuts'][0]['timeline_duration'] += .25
        record['cuts'][1]['timeline_duration'] -= .25
    elif damage == 'source_durations':
        voice['scene_durations'][0] += .25
        voice['scene_durations'][1] -= .25
    else:
        scenes[3]['transition'] = 'cut'
    assert sum(row['target_frames'] for row in record['cuts']) == 873
    with pytest.raises(reader.RetainedStoryVisualEvidenceError, match='^retained_story_visual_timeline_changed$'):
        reader._validate_timeline(record, scenes, voice)
