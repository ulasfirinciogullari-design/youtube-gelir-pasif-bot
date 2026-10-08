"""Actual offline sampled-image production; diagnostic linkage never approves QA."""
import base64
from contextlib import contextmanager
from contextvars import copy_context
from copy import copy, deepcopy
import json
from pathlib import Path
import struct
import subprocess
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import retained_sampled_input_linkage as link
from app.services import retained_cut_evidence as cuts, visual_qc as visual, render
from app.services import preserved_visual_recovery as recovery
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_journal as journal
from app.services import production_connection_continuity as continuity
from test_retained_cut_evidence import (real_media, box as local_cut_box, no_network,
    prepare, BUCKET, ENDPOINT)
from test_abacus_router_review_runtime import sandbox, case, generate, payload, STORY, VISUAL, Chunks
from test_production_connection_continuity import _dump
from test_production_credit_ledger import InterceptClient
from test_visual_qc import _review


REAL_NORMALIZE = render.normalize_clip
REAL_RUN = subprocess.run
ERROR = '^retained_sampled_input_unverified$'


@pytest.fixture(scope='module')
def sample_jpeg(real_media):
    target = real_media.box.work / 'negative-fixture.jpg'
    REAL_RUN(['ffmpeg', '-v', 'error', '-i', real_media.artifact.inputs[0][0]['path'],
              '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '5', str(target)], check=True)
    return target.read_bytes()


@pytest.fixture
def capture_box(local_cut_box, case, request, monkeypatch, sample_jpeg):
    box = local_cut_box
    # Distinct real MP4 containers preserve the original six-pointer contract.
    for index, (path, provider) in enumerate(box.paths):
        with path.open('ab') as out:
            out.write(struct.pack('>I4s', 9, b'free') + bytes([index]))
        box.raw[index] = {**cuts._file(path)[0], 'provider': provider}
    key = continuity._JOB + continuity.LEAF_ID
    job = json.loads(case.client.get(key))
    audio = box.source['audio']
    job['audio_candidate_checkpoint'].update(audio_sha256=audio['sha256'], size=audio['size'],
        audio_key=f"audio_candidates/{continuity.LEAF_ID}/{audio['sha256']}/candidate.mp3",
        metadata_key=f"audio_candidates/{continuity.LEAF_ID}/{audio['sha256']}/metadata-{'b'*64}.json")
    for pointer in job['generated_asset_candidates']['entries']:
        raw = box.raw[pointer['scene_index']]
        pointer.update(raw_sha256=raw['sha256'], raw_size=raw['size'], audio_sha256=audio['sha256'],
                       raw_key=f"generated_candidates/{continuity.LEAF_ID}/raw/{raw['sha256']}.mp4")
    case.client.set(key, json.dumps(job))
    source, fingerprint = recovery._state(continuity.LEAF_ID, case.client)
    box.source.update(source_task_id=continuity.LEAF_ID, source_state_sha256=fingerprint,
        source_spec_sha256=recovery._digest(source['spec']),
        source_journal_sha256=recovery._digest(source['generated_asset_candidates']),
        source_metadata_sha256='b'*64)
    box.runtime = request.getfixturevalue('sandbox')
    box.client = case.client
    monkeypatch.setattr(cuts.storage, '_client', lambda *, single_attempt: box.s3)
    monkeypatch.setattr(recovery.studio_state, '_client', lambda: case.client)
    if request.node.name == 'test_actual_six_cuts_thirty_jpegs_exact_acknowledged_wire_and_private_anchor':
        # The positive producer path performs real normalization AND real sampling.
        monkeypatch.setattr(render, 'normalize_clip', REAL_NORMALIZE)
    box.derived = prepare(box)
    box.cut_receipt = cuts.persist_retained_cuts(box.derived, box.s3, bucket=BUCKET)
    box.directory = Path(box.derived.inputs[0][0]['path']).parent
    if request.node.name != 'test_actual_six_cuts_thirty_jpegs_exact_acknowledged_wire_and_private_anchor':
        # Only failure-boundary cases replay a real JPEG at command execution.
        # The positive test above proves the actual producer with no such stub.
        def replay(command, **kwargs):
            if Path(command[0]).name == 'ffmpeg' and '-frames:v' in command:
                Path(command[-1]).write_bytes(sample_jpeg)
                return subprocess.CompletedProcess(command, 0)
            return REAL_RUN(command, **kwargs)
        monkeypatch.setattr(visual.subprocess, 'run', replay)
    base_handler = box.runtime.handler
    def handler(request):
        body = json.loads(request.content)
        if body['max_tokens'] != 8192:
            return base_handler(request)
        box.runtime.calls.append(request)
        response = payload()
        response['choices'][0]['message']['content'] = json.dumps({'reviews': [
            _review(i, score=0, reason='The sampled synthetic subject and action are absent.',
                    subject_visible=False, spoken_action_visible=False) for i in range(6)]})
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'},
                              stream=Chunks([json.dumps(response).encode()]))
    box.runtime.handler = handler
    return box


def persist_artifact(box, artifact, prior=None):
    return artifacts.RetainedRouterReviewArtifactSink(box.s3, bucket=BUCKET).persist(
        artifact, prior_story_anchor=prior)


@contextmanager
def sampling(box):
    with runtime.retained_router_review_scope(continuity.LEAF_ID) as scope:
        generate()
        first = persist_artifact(box, runtime.retained_router_review_artifacts()[STORY])
        collector = link.begin_sampled_input_capture(box.derived, box.package)
        yield collector, scope, first


def actual_review(box, collector):
    return visual.review_scene_visuals(box.package['scenes'], box.derived.inputs, box.directory, 6,
        _missing_review_attempts=0, _score_reason_consistency_attempts=0,
        topic='Synthetic original episode', story_scenes=box.package['scenes'],
        content_style='documentary', _retained_sample_capture=collector)


def capture_visual(box, collector, prior):
    result = actual_review(box, collector)
    artifact = runtime.retained_router_review_artifacts()[VISUAL]
    second = persist_artifact(box, artifact, prior)
    return result, artifact, second


def link_keys(box):
    return list(box.client.scan_iter('*:sampled_cut_link:v1:*'))


def test_actual_six_cuts_thirty_jpegs_exact_acknowledged_wire_and_private_anchor(capture_box, monkeypatch):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        # A stale cached probe may not replace the actual opt-in duration probe.
        monkeypatch.setattr(visual, '_duration', Mock(side_effect=AssertionError('cached probe forbidden')))
        result, artifact, second = capture_visual(box, collector, first)
        before = _dump(box.client)
        receipt = link.persist_sampled_input_link(collector, artifact, second)
        record = json.loads(box.s3.objects[receipt['pointer']['key']][0])
        assert len(link_keys(box)) == 1 and box.client.pttl(receipt['anchor_key']) == -1
        assert {k: v for k, v in _dump(box.client).items() if k != receipt['anchor_key']} == before
        assert record['journal_state_sha256'] == artifacts._hash(scope.journal._read(box.client))
        assert record['cut_manifest'] == box.cut_receipt['manifest']
        assert record['visual_artifact_manifest'] == second.receipt['manifest']
        assert len(record['events']) == 30 and len(box.runtime.calls) == 2
        wire = json.loads(artifact.request_body_bytes)
        assert artifacts._raw(wire, artifacts._LIMITS['wire']) == artifact.prepared_body_bytes
        parts = wire['messages'][1]['content']
        images = [(parts[i-1]['text'], base64.b64decode(part['image_url']['url'].split(',')[1]))
                  for i, part in enumerate(parts) if part['type'] == 'image_url']
        for number, (event, (label, image)) in enumerate(zip(record['events'], images)):
            scene, order = divmod(number, 5)
            moment, fraction = link._ORDER[order]
            assert (event['scene_index'], event['candidate_index'], event['moment_index'], event['fraction']) == (scene, 0, moment, fraction)
            assert event['image'] == {'sha256': cuts._sha(image), 'size': len(image)}
            assert event['label_sha256'] == cuts._sha(label.encode())
            assert event['cut_sha256'] == box.derived.record['cuts'][scene]['cut']['sha256']
            recipe = event['recipe']
            assert recipe['argv'][0] == '$ffmpeg' and '$cut' in recipe['argv'] and recipe['argv'][-1] == '$jpeg'
            assert recipe['argv'][recipe['argv'].index('-ss')+1] == f"{recipe['source_duration'] * fraction:.3f}"
            assert recipe['duration_probe_argv'][0] == '$ffprobe'
        assert result['reviews'] and all(row['score'] == 0 for row in result['reviews'])
        assert record['sampled_wire_linkage_verified'] is True and record['actual_sampling_events_verified'] is True
        assert all(record[name] is expected for name, expected in link._FLAGS.items())
        assert all(receipt[name] is expected for name, expected in link._FLAGS.items())
        assert str(box.work) not in json.dumps(record) and 'private-runtime-router-key' not in json.dumps(record)
        audit = {}
        monkeypatch.setattr(link, 'persist_sampled_input_link', Mock(return_value=receipt))
        recovery._capture_sampled_input_link(audit, {'sampled_collector': collector}, {VISUAL: second})
        assert audit['retained_sampled_input_link'] == receipt
        link.persist_sampled_input_link.assert_called_once_with(collector, artifact, second)
    with pytest.raises(Exception):
        link._checked(collector)


@pytest.mark.parametrize('change', ['cut', 'order', 'path', 'package', 'detached', 'thread'])
def test_invalid_or_changed_sampling_context_rejects_before_visual_send(capture_box, change):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        if change == 'package':
            with pytest.raises(link.SampledInputLinkageError, match=ERROR):
                link.begin_sampled_input_capture(box.derived, {**box.package, 'changed': True})
        elif change == 'detached':
            with pytest.raises(TypeError):
                copy(collector)
            with pytest.raises(link.SampledInputLinkageError, match=ERROR):
                link._checked(object.__new__(link.SampledInputCollector))
        elif change == 'thread':
            failures = []
            def read():
                try:
                    link._checked(collector)
                except Exception as error:
                    failures.append(error)
            worker = Thread(target=copy_context().run, args=(read,))
            worker.start(); worker.join()
            assert len(failures) == 1
        else:
            link._enter(collector, box.package['scenes'], box.derived.inputs, box.directory)
            source = Path(box.derived.inputs[0][0]['path'])
            if change == 'cut':
                source.write_bytes(source.read_bytes() + b'changed')
            target = box.directory / 'visual_qc' / 'scene_00_candidate_00_moment_03.jpg'
            with pytest.raises(link.SampledInputLinkageError, match=ERROR):
                link._before_frame(collector, str(source), target if change != 'path' else box.work / 'other.jpg',
                                   .18 if change == 'order' else .06)
        assert len(box.runtime.calls) == 1 and not link_keys(box)
        assert set(scope.journal._read(box.client)['slots']) == {STORY}


@pytest.mark.parametrize('change', ['jpeg', 'oversize', 'label', 'image_order', 'schema'])
def test_changed_recorded_image_or_actual_request_cannot_be_linked(capture_box, monkeypatch, change):
    box = capture_box
    if change == 'oversize':
        original = link._read_frame
        def enlarged(collector, path):
            Path(path).write_bytes(b'x' * (link.adapter.MAX_IMAGE_BYTES + 1))
            return original(collector, path)
        monkeypatch.setattr(link, '_read_frame', enlarged)
    elif change == 'jpeg':
        original = link._record_frame
        def corrupt(collector, label, raw):
            return original(collector, label, raw + b'changed')
        monkeypatch.setattr(link, '_record_frame', corrupt)
    elif change in {'label', 'image_order'}:
        original = link._bind_request
        def corrupt(collector, parts, instruction, schema):
            parts = deepcopy(parts)
            indices = [i for i, part in enumerate(parts) if part['type'] == 'image_url']
            if change == 'label':
                parts[indices[0]-1]['text'] += 'changed'
            else:
                # Swap labels too; even equal solid-color JPEGs cannot conceal event order.
                a, b = indices[0]-1, indices[1]-1
                parts[a:a+2], parts[b:b+2] = parts[b:b+2], parts[a:a+2]
            return original(collector, parts, instruction, schema)
        monkeypatch.setattr(link, '_bind_request', corrupt)
    else:
        original = runtime.generate_retained_router_review
        def changed_request(parts, **kw):
            if kw['purpose'] == VISUAL:
                kw['system_instruction'] += '\nChanged after extraction binding.'
            return original(parts, **kw)
        monkeypatch.setattr(runtime, 'generate_retained_router_review', changed_request)
    with sampling(box) as (collector, scope, first):
        if change == 'schema':
            _, artifact, second = capture_visual(box, collector, first)
            with pytest.raises(link.SampledInputLinkageError, match=ERROR):
                link.persist_sampled_input_link(collector, artifact, second)
            assert scope.failed
        else:
            with pytest.raises(link.SampledInputLinkageError, match=ERROR):
                actual_review(box, collector)
            assert len(box.runtime.calls) == 1
            assert scope.failed
            with pytest.raises(Exception):
                generate(VISUAL)
        assert not link_keys(box)


@pytest.mark.parametrize('mutation', ['input', 'filter', 'seek', 'probe', 'duration'])
def test_exact_executed_commands_and_seek_are_bound_to_the_cut(capture_box, monkeypatch, mutation):
    box, original = capture_box, link._after_frame
    def changed(collector, duration, seconds, argv, probe):
        if mutation == 'input':
            argv[argv.index('-i')+1] = '/different/source.mp4'
        elif mutation == 'filter':
            argv[argv.index('-vf')+1] = 'scale=32:32'
        elif mutation == 'seek':
            argv[argv.index('-ss')+1] = '0.000'
        elif mutation == 'probe':
            probe[-1] = '/different/source.mp4'
        else:
            duration += 1.0
        return original(collector, duration, seconds, argv, probe)
    monkeypatch.setattr(link, '_after_frame', changed)
    with sampling(box) as (collector, scope, first):
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            actual_review(box, collector)
        assert scope.failed and len(box.runtime.calls) == 1 and not link_keys(box)


def test_required_router_link_cannot_skip_a_missing_collector(monkeypatch):
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: True)
    with pytest.raises(Exception):
        recovery._capture_sampled_input_link({}, {}, {}, required=True)
    recovery._capture_sampled_input_link({}, None, {}, required=True)
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: False)
    recovery._capture_sampled_input_link({}, {}, {}, required=True)


@pytest.mark.parametrize('failure', ['source', 'cut_blob', 'visual_blob', 'public_acl', 's3_ack',
                                     'redis_ack', 'redis_readback', 'source_race', 'forged_receipt'])
def test_persistence_uncertainty_is_terminal_and_never_retried(capture_box, monkeypatch, failure):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        _, artifact, second = capture_visual(box, collector, first)
        original_count = len(box.s3.puts)
        def change_source():
            key = continuity._JOB + continuity.LEAF_ID
            job = json.loads(box.client.get(key)); job['updated_at'] = 'changed'
            box.client.set(key, json.dumps(job))
        if failure == 'source':
            change_source()
        elif failure == 'cut_blob':
            box.s3.objects.pop(box.cut_receipt['manifest']['key'])
        elif failure == 'visual_blob':
            box.s3.objects.pop(second.receipt['manifest']['key'])
        elif failure == 'public_acl':
            box.s3.extra_grants = [{'Grantee': {'Type': 'Group', 'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}]
        elif failure == 's3_ack':
            def lost(kw, response):
                raise ConnectionError('PRIVATE lost S3 ACK')
            box.s3.after_put = lost
        elif failure == 'forged_receipt':
            second = second.receipt
        else:
            def before(call):
                if failure == 'source_race' and call == 2:
                    change_source()
            def after(call, result):
                if (failure == 'redis_ack' and call == 2) or (failure == 'redis_readback' and call == 3):
                    raise ConnectionError('PRIVATE lost Redis ACK')
                return result
            scope.journal.client = InterceptClient(box.client, before=before, after=after)
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            link.persist_sampled_input_link(collector, artifact, second)
        assert scope.failed and len(box.runtime.calls) == 2
        attempts = len(box.s3.puts)
        assert attempts <= original_count + 1
        with pytest.raises(Exception):
            link.persist_sampled_input_link(collector, artifact, second)
        assert len(box.s3.puts) == attempts and len(box.runtime.calls) == 2
        assert bool(link_keys(box)) is (failure in {'redis_ack', 'redis_readback'})


def test_opt_in_preparation_stores_cuts_before_issuing_collector_and_reviewer(capture_box, monkeypatch):
    box, audit = capture_box, {}
    with sampling(box) as (collector, scope, first):
        monkeypatch.setattr(cuts, 'prepare_retained_cuts', lambda *a, **kw: box.derived)
        monkeypatch.setattr(cuts, 'persist_retained_cuts', lambda *a, **kw: box.cut_receipt)
        monkeypatch.setattr(link, 'begin_sampled_input_capture', lambda *a: collector)
        def reviewer(*args, **kwargs):
            assert audit['retained_cut_evidence'] == box.cut_receipt
            assert kwargs['_retained_sample_capture'] is collector
            return {'reviews': []}
        monkeypatch.setattr(recovery, '_review_runtime', lambda: (render, reviewer))
        context = {'source': box.source, 'raw_bindings': box.raw, 'audit': audit}
        result, counts = recovery._exact_review(box.package, box.voice, box.paths, box.work, 'Synthetic', cut_context=context)
        assert context['sampled_collector'] is collector and counts == box.derived.frame_counts
        assert result == {'reviews': []} and len(box.runtime.calls) == 1


def test_collector_cannot_be_issued_after_visual_ack(capture_box):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        generate(VISUAL)
        before, writes = _dump(box.client), len(box.s3.puts)
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            link.begin_sampled_input_capture(box.derived, box.package)
        assert scope.failed and _dump(box.client) == before and len(box.s3.puts) == writes
        assert len(box.runtime.calls) == 2 and not link_keys(box)


def test_actual_images_cannot_be_bound_retroactively_after_visual_ack(capture_box, monkeypatch):
    box, original, late = capture_box, link._bind_request, []
    def defer(*args):
        late.append(args)
    monkeypatch.setattr(link, '_bind_request', defer)
    with sampling(box) as (collector, scope, first):
        actual_review(box, collector)
        assert len(late) == 1 and len(box.runtime.calls) == 2
        before = _dump(box.client)
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            original(*late[0])
        assert scope.failed and _dump(box.client) == before and not link_keys(box)


@pytest.mark.parametrize('phase', ['begin', 'bind'])
def test_lost_read_ack_at_pre_visual_phase_never_authorizes_sampling_or_send(capture_box, phase):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        before = _dump(box.client)
        def lost(call, result):
            raise ConnectionError('PRIVATE lost phase ACK')
        scope.journal.client = InterceptClient(box.client, after=lost)
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            if phase == 'begin':
                link.begin_sampled_input_capture(box.derived, box.package)
            else:
                actual_review(box, collector)
        assert scope.failed and len(box.runtime.calls) == 1 and _dump(box.client) == before
        assert not link_keys(box)


def test_source_watch_race_at_request_binding_prevents_visual_reservation(capture_box):
    box = capture_box
    with sampling(box) as (collector, scope, first):
        before = {key: box.client.dump(key) for key in scope.journal.keys}
        def change(call):
            key = continuity._JOB + continuity.LEAF_ID
            job = json.loads(box.client.get(key)); job['updated_at'] = 'concurrent source change'
            box.client.set(key, json.dumps(job))
        scope.journal.client = InterceptClient(box.client, before=change)
        with pytest.raises(link.SampledInputLinkageError, match=ERROR):
            actual_review(box, collector)
        assert scope.failed and len(box.runtime.calls) == 1 and not link_keys(box)
        assert {key: box.client.dump(key) for key in scope.journal.keys} == before
