"""Real captured review -> preparation audit -> private durable storage, offline."""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import preserved_visual_recovery as recovery, storage
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_artifacts as artifacts
from app.services.production_connection_continuity import LEAF_ID
from test_abacus_router_review_artifacts import box, sandbox, case, captured, manifest
from test_abacus_router_review_runtime import generate, STORY, VISUAL


def client_for(box, monkeypatch):
    factory = Mock(side_effect=lambda *, single_attempt: box.s3 if single_attempt is True else None)
    monkeypatch.setattr(recovery.storage, '_client', factory)
    return factory


def test_preparation_audit_saves_full_negative_story_and_then_visual_once(box, monkeypatch):
    client_for(box, monkeypatch)
    audit, local = {}, {}
    with captured(box) as (capture, scope):
        assert capture.result == {'accepted': False}
        recovery._capture_included_router_review(audit, RuntimeError('private error'), artifact_state=local)
        assert set(local) == {STORY}
        assert manifest(box, local[STORY])['evidence'] == capture.evidence
        stored = audit['included_router_artifact_anchors'][STORY]
        assert stored == local[STORY].receipt and stored['qa_approved'] is False
        assert 'private error' not in json.dumps(audit)
        puts = len(box.s3.puts)
        recovery._capture_included_router_review(audit, artifact_state=local)
        assert len(box.s3.puts) == puts
        generate(VISUAL)
        recovery._capture_included_router_review(audit, artifact_state=local)
        assert set(local) == {STORY, VISUAL} and len(box.s3.puts) == puts * 2
        final = manifest(box, local[VISUAL])
        assert final['prior_story_anchor_sha256'] == stored['anchor_sha256']
        assert audit['included_router_artifact_anchors'][VISUAL] == local[VISUAL].receipt
        assert all(row['publish_eligible'] is False for row in audit['included_router_artifact_anchors'].values())
        assert scope.failed is False
    assert len(box.calls) == 2 and not box.forbidden.called


def test_unknown_storage_write_stops_scope_and_cannot_send_visual(box, monkeypatch):
    client_for(box, monkeypatch)
    def lost_ack(*args):
        raise ConnectionError('private storage details')
    box.s3.after_put = lost_ack
    audit, local = {}, {}
    with captured(box) as (_, scope):
        with pytest.raises(artifacts.RouterReviewArtifactError):
            recovery._capture_included_router_review(audit, artifact_state=local)
        assert scope.failed is True and local == {} and len(box.s3.puts) == 1
        recovery._capture_included_router_review(audit, RuntimeError('private error'), artifact_state=local)
        assert len(box.s3.puts) == 1
        with pytest.raises(Exception):
            generate(VISUAL)
        assert len(box.calls) == 1


def test_failed_visual_scope_preserves_already_stored_story_receipt(box, monkeypatch):
    client_for(box, monkeypatch)
    audit, local = {}, {}
    with captured(box) as (_, scope):
        recovery._capture_included_router_review(audit, artifact_state=local)
        before = deepcopy(audit['included_router_artifact_anchors'])
        scope.failed = True
        recovery._capture_included_router_review(audit, RuntimeError('private'), artifact_state=local)
        assert audit['included_router_artifact_anchors'] == before
        assert len(box.s3.puts) == 5


def test_inactive_preparation_never_constructs_artifact_storage(monkeypatch):
    factory = Mock(side_effect=AssertionError('No artifact I/O outside retained scope'))
    monkeypatch.setattr(recovery.storage, '_client', factory)
    audit = {}
    recovery._capture_included_router_review(audit)
    assert audit == {}
    factory.assert_not_called()


def test_explicit_successor_is_forwarded_to_scope_without_new_commission(monkeypatch, tmp_path):
    token, events = object(), []
    @contextmanager
    def scope(source, *, successor):
        assert source == LEAF_ID and successor is token
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')
    monkeypatch.setattr(runtime, 'retained_router_review_scope', scope)
    def prepare(source, directory):
        assert source == LEAF_ID and directory == tmp_path and events == ['enter']
        return 'prepared'
    monkeypatch.setattr(recovery, 'prepare_preserved_visual_recovery', prepare)
    assert recovery.prepare_subscription_router_recovery(LEAF_ID, tmp_path, successor=token) == 'prepared'
    assert events == ['enter', 'exit']


def test_actual_storage_factory_sets_explicit_single_attempt_only_when_requested(monkeypatch):
    monkeypatch.setattr(storage, 'settings', SimpleNamespace(bucket='private', endpoint='https://storage.invalid',
        access_key_id='fixture-id', secret_access_key='fixture-secret', region='auto'))
    client = Mock(return_value=object())
    monkeypatch.setattr(storage.boto3, 'client', client)
    storage._client(single_attempt=True)
    bounded = client.call_args.kwargs['config']
    assert bounded.retries == {'total_max_attempts': 1}
    assert bounded.signature_version == 's3v4' and bounded.s3 == {'addressing_style': 'virtual'}
    storage._client()
    assert client.call_args.kwargs['config'].retries is None
    with pytest.raises(ValueError):
        storage._client(single_attempt=1)
    assert client.call_count == 2
