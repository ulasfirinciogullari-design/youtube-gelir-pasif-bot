"""Genuine retained final; public responses are explicitly synthetic transports."""
import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import retained_delivery_completion as completion
from app.services import retained_delivery_runtime as runtime
from app.services import retained_publication_transport as transport
from app.services import retained_publication_plan as publication
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from app.services import studio_state, youtube_publish_state
from test_retained_publication_plan import (
    executing, admitted, corrected_visual, eligible, rejected, qualified, captured,
    source, case, planning_case, real_media, prepared, frozen_three, completed_probe,
    wire, forbid_live_transport, prepare,
)
from test_production_connection_continuity import _dump
from test_retained_review_credential_successor import Intercept
from test_retained_delivery_dispatch import generic_fences


def synthetic_youtube(box, monkeypatch):
    calls = []
    credentials = object()
    video_id = 'Synthetic01'
    monkeypatch.setattr(transport.youtube_auth, 'load_credentials', lambda *a, **k: credentials)
    monkeypatch.setattr(transport.youtube_auth, '_channel_from_credentials',
                        lambda actual: {'id': admission.continuity.CHANNEL_ID} if actual is credentials else {})
    def effect(phase):
        assert box.client.pttl(transport._key(phase, 'intent')) == -1
        assert not box.client.exists(transport._key(phase, 'result'))
        calls.append(phase)
    def upload(actual, path, title, description, **kw):
        effect('upload')
        assert actual is credentials and Path(path).is_file()
        assert kw['privacy_status'] == 'private' and kw['contains_synthetic_media'] is True
        return {'id': video_id}
    def status(actual, video):
        phase = 'public_status' if 'release' in calls else 'private_status'
        effect(phase)
        assert actual is credentials and video == video_id
        return {'privacyStatus': 'public' if phase == 'public_status' else 'private',
                'uploadStatus': 'processed', 'containsSyntheticMedia': True}
    def captions(actual, video, path, language):
        effect('captions')
        assert actual is credentials and video == video_id and Path(path).is_file()
        return {'id': 'Synthetic/Caption+' + 'a' * 180 + '==',
                'snippet': {'videoId': video, 'language': language}}
    def release(actual, video, mode, **kw):
        effect('release')
        assert actual is credentials and video == video_id and mode == 'public'
        assert kw == {'contains_synthetic_media': True}
        return {'id': video_id, 'status': {'privacyStatus': 'public', 'containsSyntheticMedia': True}}
    for name, function in (('upload_video_with_credentials', upload), ('get_video_status_with_credentials', status),
            ('upload_caption_with_credentials', captions), ('set_video_release_with_credentials', release)):
        monkeypatch.setattr(transport.youtube, name, function)
    monkeypatch.setattr(transport.youtube, 'upload_thumbnail_with_credentials',
                        lambda *a, **k: pytest.fail('Thumbnail is optional for this exact profile'))
    return calls


def restore(client, snapshot):
    client.flushdb()
    for key, value in snapshot.items():
        client.restore(key, 0, value)


def test_only_public_receipts_can_complete_and_lost_ack_is_read_only(executing, monkeypatch, subtests):
    box = executing
    value = prepare(box)
    calls = synthetic_youtube(box, monkeypatch)
    initial = _dump(box.client)
    with pytest.raises(completion.RetainedCompletionError): completion.complete_retained_publication(value)
    assert _dump(box.client) == initial and calls == []
    transport.publish_retained_final(value)
    before = _dump(box.client)
    assert calls == ['upload', 'private_status', 'captions', 'release', 'public_status']
    for key in (transport.PUBLIC_RECEIPT_KEY, transport._key('release', 'result'),
            admission.continuity._AUTH_EPOCH,
            'youtube_studio:source_publication_hold:v1:' + box.child):
        with subtests.test(before_completion=key):
            box.client.set(key, '{}')
            damaged = _dump(box.client)
            with pytest.raises(completion.RetainedCompletionError): completion.complete_retained_publication(value)
            assert _dump(box.client) == damaged
            restore(box.client, before)
    with subtests.test(expired_receipt=True):
        box.client.expire(transport.PUBLIC_RECEIPT_KEY, 600)
        with pytest.raises(completion.RetainedCompletionError): completion.complete_retained_publication(value)
        box.client.persist(transport.PUBLIC_RECEIPT_KEY)
    def lost(commands, reply):
        if commands == ('SET', 'SET', 'SET', 'ZADD'):
            raise RuntimeError('PRIVATE completion acknowledgement')
        return reply
    running = delivery._execution(box.execution)
    running['client'] = Intercept(box.client, after=lost)
    with pytest.raises(completion.RetainedCompletionError) as error:
        completion.complete_retained_publication(value)
    assert 'PRIVATE' not in str(error.value)
    running['client'] = box.client
    receipt = completion.read_retained_delivery_completion(box.client, box.child, box.manifest_sha)
    assert receipt['video_id'] == 'Synthetic01'
    assert receipt['resume_authorized'] is receipt['next_production_authorized'] is False
    child = json.loads(box.client.get(admission.continuity._JOB + box.child))
    assert child['state'] == 'SUCCESS' and child['result']['youtube']['privacy_status'] == 'public'
    assert child['result']['quality_disposition'] == 'retained_component_review_pass'
    assert child['result']['quality_snapshot']['final_aac_independently_listened'] is False
    upload_key = youtube_publish_state._key(box.child)
    upload = json.loads(box.client.get(upload_key))
    assert upload['status'] == 'complete' and upload['youtube_video_id'] == 'Synthetic01'
    assert upload['release_status'] == 'public' and upload['contains_synthetic_media'] is True
    changed = {completion.COMPLETION_KEY, admission.continuity._JOB + box.child, upload_key, studio_state.JOB_INDEX}
    assert all(box.client.dump(key) == raw for key, raw in before.items() if key not in changed)
    assert all(box.client.pttl(key) == -1 for key in changed - {studio_state.JOB_INDEX})
    after = _dump(box.client)
    with pytest.raises(completion.RetainedCompletionError): completion.complete_retained_publication(value)
    with pytest.raises(delivery.RetainedDispatchError): delivery.verify_execution(box.execution)
    generic_fences(box)
    assert _dump(box.client) == after
    for key in (completion.COMPLETION_KEY, upload_key, admission.continuity._JOB + box.child,
            transport.PUBLIC_RECEIPT_KEY, value.record['series_keys'][0]):
        with subtests.test(completed_read=key):
            box.client.set(key, '{}')
            damaged = _dump(box.client)
            with pytest.raises(completion.RetainedCompletionError):
                completion.read_retained_delivery_completion(box.client, box.child, box.manifest_sha)
            assert _dump(box.client) == damaged
            restore(box.client, after)
    assert len(calls) == 5 and len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    box.no_new_work.assert_not_called()


def test_actual_worker_finishes_before_queue_ack_and_duplicate_has_no_effect(admitted, monkeypatch, tmp_path):
    box = admitted
    calls = synthetic_youtube(box, monkeypatch)
    monkeypatch.setattr(runtime.storage, '_client',
        lambda **kw: box.s3 if kw == {'single_attempt': True} else pytest.fail('Storage retries forbidden'))
    completed = []
    def send(**kw):
        assert kw == {'kwargs': {'manifest_sha256': box.manifest_sha}, 'task_id': box.child, 'retry': False}
        assert not box.client.exists(delivery.DISPATCH_ACK_KEY)
        completed.append(runtime.run_retained_delivery(box.child, box.manifest_sha, work_root=tmp_path / 'worker'))
        assert completed[-1]['status'] == 'complete', completed[-1]
        return SimpleNamespace(id=box.child)
    ack = delivery.dispatch_retained_child(box.permit, send)
    assert ack['child_id'] == box.child and completed[0]['video_id'] == 'Synthetic01'
    before = _dump(box.client)
    assert runtime.run_retained_delivery(box.child, box.manifest_sha, work_root=tmp_path / 'worker') == completed[0]
    assert _dump(box.client) == before and calls == ['upload', 'private_status', 'captions', 'release', 'public_status']
    assert not box.client.exists(runtime.STOP_KEY)
    assert len(box.wire.calls) == box.sends and box.s3.objects == box.objects
    box.no_new_work.assert_not_called()


def test_celery_entry_rejects_retry_and_uses_dedicated_task_id(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'app/production_tasks.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'finalize_retained_child')
    options = {kw.arg: ast.literal_eval(kw.value) for kw in node.decorator_list[0].keywords}
    assert options['name'] == delivery.TASK_NAME and options['acks_late'] is False
    assert options['reject_on_worker_lost'] is False and options['autoretry_for'] == () and options['max_retries'] == 0
    node = deepcopy(node)
    node.decorator_list = []
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    run = Mock(return_value={'status': 'synthetic'})
    monkeypatch.setattr(runtime, 'run_retained_delivery', run)
    for retries in (1, 2, True, '0'):
        result = namespace['finalize_retained_child'](SimpleNamespace(request=SimpleNamespace(id='child', retries=retries)), 'digest')
        assert result['automatic_retry_permitted'] is False
    run.assert_not_called()
    namespace['finalize_retained_child'](SimpleNamespace(request=SimpleNamespace(id='child', retries=0)), 'digest')
    run.assert_called_once_with('child', 'digest')
