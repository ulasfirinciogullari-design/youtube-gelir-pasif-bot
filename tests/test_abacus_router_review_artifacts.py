"""Actual offline HTTPX captures, fake Redis WATCH and private immutable S3."""
from contextlib import contextmanager
from contextvars import copy_context
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from io import BytesIO
import json
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_review_artifacts as sink
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_review_runtime as runtime
from app.services import production_connection_continuity as continuity
from test_abacus_router_review_runtime import sandbox, case, generate, STORY, VISUAL, KEY, Chunks, payload
from test_production_connection_continuity import _dump
from test_production_credit_ledger import InterceptClient
from test_retained_review_credential_successor import (
    prepared as successor_prepared, source, commission as commission_successor,
    NOW as SUCCESSOR_NOW, OLD_KEYS, records as predecessor_records,
)


ENDPOINT = 'https://t3.storageapi.dev'
BUCKET = 'private-fixture'


class FakeS3:
    def __init__(self):
        self.meta = SimpleNamespace(endpoint_url=ENDPOINT,
            config=SimpleNamespace(retries={'total_max_attempts': 1}))
        self.objects, self.puts, self.gets, self.acls, self.streams = {}, [], [], [], []
        self.after_put = self.patch_get = self.patch_acl = None
        self.extra_grants = []

    def put_object(self, **kw):
        self.puts.append(deepcopy(kw))
        assert set(kw) == {'Bucket', 'Key', 'Body', 'ContentLength', 'ContentType',
                           'CacheControl', 'Metadata', 'ACL', 'IfNoneMatch'}
        assert kw['Bucket'] == BUCKET and kw['ContentType'] == 'application/json'
        assert kw['ACL'] == 'private' and kw['IfNoneMatch'] == '*'
        assert kw['ContentLength'] == len(kw['Body']) and kw['CacheControl'] == 'private, no-store'
        assert kw['Metadata'] == {'sha256': sink._sha(kw['Body'])}
        if kw['Key'] in self.objects:
            raise RuntimeError('PRIVATE existing object ' + KEY)
        self.objects[kw['Key']] = kw['Body']
        result = {'ResponseMetadata': {'HTTPStatusCode': 200}}
        if self.after_put:
            self.after_put(kw, result)
        return result

    def get_object(self, *, Bucket, Key):
        assert Bucket == BUCKET
        self.gets.append(Key)
        raw = self.objects[Key]
        body = BytesIO(raw)
        self.streams.append(body)
        result = {'ResponseMetadata': {'HTTPStatusCode': 200}, 'Body': body,
                  'ContentLength': len(raw), 'ContentType': 'application/json'}
        if self.patch_get:
            self.patch_get(Key, result)
        return result

    def get_object_acl(self, *, Bucket, Key):
        assert Bucket == BUCKET
        self.acls.append(Key)
        result = {'Owner': {'ID': 'fixture-owner'}, 'Grants': [
            {'Grantee': {'Type': 'CanonicalUser', 'ID': 'fixture-owner'}, 'Permission': 'FULL_CONTROL'},
            *deepcopy(self.extra_grants)], 'ResponseMetadata': {'HTTPStatusCode': 200}}
        if self.patch_acl:
            self.patch_acl(Key, result)
        return result


@pytest.fixture
def box(sandbox, monkeypatch):
    monkeypatch.setattr(sink.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    sandbox.s3 = FakeS3()
    return sandbox


@contextmanager
def captured(box):
    with runtime.retained_router_review_scope(continuity.LEAF_ID) as scope:
        generate()
        yield runtime.retained_router_review_artifacts()[STORY], scope


def persist(box, artifact, prior=None):
    # A new Sink must not escape the fence on a repeated same-scope attempt.
    return sink.RetainedRouterReviewArtifactSink(box.s3, bucket=BUCKET).persist(
        artifact, prior_story_anchor=prior)


def manifest(box, receipt):
    return json.loads(box.s3.objects[receipt.receipt['manifest']['key']])


def unchanged_except(box, before, *keys):
    return {k: v for k, v in _dump(box.case.client).items() if k not in keys} == before


def test_story_then_visual_full_private_capture_and_durable_anchor_chain(box):
    with captured(box) as (artifact, scope):
        before = _dump(box.case.client)
        first = persist(box, artifact)
        assert type(first) is sink.PersistedRouterReviewArtifact
        assert first.diagnostic_only and not first.qa_approved and not first.publish_eligible
        assert KEY not in repr(first) and 'PRIVATE' not in repr(first)
        assert unchanged_except(box, before, sink.STORY_ANCHOR_KEY)
        record = manifest(box, first)
        assert record['journal_keys'] == list(scope.journal.keys)
        assert record['journal_state'] == json.loads(box.case.client.get(journal.STATE_KEY))
        assert record['reservation'] == artifact.reservation and record['evidence'] == artifact.evidence
        source = record['source']['legacy_candidates']
        assert len(source['generated_pointers']) == 6
        assert source['original_full_package_sha256'] != source['audio_candidate_package_sha256']
        assert source['audio_pointer']['audio_sha256'] == source['generated_pointers'][0]['audio_sha256']
        assert source['manifest_content_verified'] is False and source['selected_edit_identity_verified'] is False
        expected = {'prepared': artifact.prepared_body_bytes, 'wire': artifact.request_body_bytes,
                    'response': artifact.response_body_bytes, 'result': sink._raw(artifact.result)}
        assert set(record['bodies']) == set(expected)
        assert {kind: box.s3.objects[pointer['key']] for kind, pointer in record['bodies'].items()} == expected
        assert json.loads(expected['result']) == {'accepted': False}  # Honest negative, never QA.
        for field, value in sink._FLAGS.items():
            assert record[field] is value and first.receipt[field] is value
        projected = first.receipt
        projected['qa_approved'] = True
        assert first.receipt['qa_approved'] is False
        with pytest.raises(FrozenInstanceError): first._receipt_bytes = b'{}'
        with pytest.raises(TypeError): sink.PersistedRouterReviewArtifact()
        generate(VISUAL)
        visual = runtime.retained_router_review_artifacts()[VISUAL]
        before = _dump(box.case.client)
        second = persist(box, visual, first)
        assert unchanged_except(box, before, sink.VISUAL_ANCHOR_KEY)
        final = manifest(box, second)
        assert final['source'] == record['source'] and final['prior_story_anchor_sha256'] == first.receipt['anchor_sha256']
        assert set(final['journal_state']['slots']) == {STORY, VISUAL}
        assert box.case.client.pttl(first.receipt['anchor_key']) == box.case.client.pttl(second.receipt['anchor_key']) == -1
        assert len(box.s3.puts) == 10 and len(box.calls) == 2
    assert all(stream.closed for stream in box.s3.streams)
    assert not box.forbidden.called and not box.commission.called
    assert all(KEY.encode() not in raw for raw in box.s3.objects.values())


def test_reconfirmed_journal_keeps_complete_predecessor_history(box):
    later = datetime(2026, 9, 10, 1, tzinfo=timezone.utc)
    policy = {**box.policy, 'valid_from': '2026-09-10T00:00:00Z',
              'valid_until': '2026-09-11T00:00:00Z', 'entitlement_evidence_sha256': 'f' * 64}
    journal.RouterReviewJournal(box.case.client, clock=lambda: later).reconfirm_unused(policy)
    box.foundation.clock = lambda: later
    with captured(box) as (artifact, scope):
        first = persist(box, artifact)
        previous = manifest(box, first)['journal_state']
        assert len(previous['history']) == 1
        generate(VISUAL)
        second = persist(box, runtime.retained_router_review_artifacts()[VISUAL], first)
        final = manifest(box, second)['journal_state']
        assert final['history'] == previous['history']
        assert sink._hash(previous) == json.loads(box.case.client.get(first.receipt['anchor_key']))['journal_state_sha256']


def test_expired_settled_capture_never_reopens_entitlement_or_uses_a_fake_clock(box, monkeypatch):
    with captured(box) as (artifact, scope):
        forbidden = Mock(side_effect=AssertionError('No new entitlement or request'))
        for name in ('_fresh', '_now', 'commission', 'reserve', 'settle'):
            monkeypatch.setattr(scope.journal, name, forbidden)
        box.foundation.clock = lambda: datetime(2036, 1, 1, tzinfo=timezone.utc)
        assert persist(box, artifact).qa_approved is False
        assert not forbidden.called and len(box.calls) == 1


@pytest.mark.parametrize('mode', ['dict', 'forged', 'closed', 'failed', 'thread'])
def test_genuine_current_owning_scope_capture_is_required_before_io(box, mode):
    with captured(box) as (artifact, scope):
        context = copy_context()
        if mode == 'dict': artifact = {'result': {'accepted': True}}
        if mode == 'forged': artifact = object.__new__(runtime.AcknowledgedRouterReview)
        if mode == 'failed': scope.failed = True
        if mode == 'closed': scope.closed = True
        if mode == 'thread':
            errors = []
            def other():
                try: context.run(persist, box, artifact)
                except sink.RouterReviewArtifactError: errors.append(True)
            thread = Thread(target=other); thread.start(); thread.join(timeout=5)
            assert not thread.is_alive() and errors == [True]
        else:
            with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
    assert not box.s3.puts and not box.s3.gets and not box.s3.acls


@pytest.mark.parametrize('operation', ['reserve', 'settle'])
def test_unknown_review_never_supplies_a_persistable_capture(box, operation):
    def after(number, ack):
        if number == (1 if operation == 'reserve' else 2): raise ConnectionError('PRIVATE ' + KEY)
        return ack
    box.foundation.client = InterceptClient(box.case.client, after=after)
    with runtime.retained_router_review_scope(continuity.LEAF_ID):
        with pytest.raises(Exception): generate()
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, {})
    assert not box.s3.puts and box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


@pytest.mark.parametrize('stage', ['preflight', 'anchor', 'readback'])
@pytest.mark.parametrize('damage', ['lost', 'partial', 'race'])
def test_redis_ack_loss_partial_ack_or_watch_race_never_returns_or_retries(box, stage, damage):
    with captured(box) as (artifact, scope):
        number = {'preflight': 1, 'anchor': 2, 'readback': 3}[stage]
        def before(call):
            if call == number and damage == 'race':
                raw = box.case.client.get(journal.ANCHOR_KEY)
                box.case.client.set(journal.ANCHOR_KEY, raw)  # Real WATCH invalidation, unchanged bytes.
        def after(call, ack):
            if call == number and damage == 'lost': raise ConnectionError('PRIVATE ' + KEY)
            if call == number and damage == 'partial': return []
            return ack
        scope.journal.client = InterceptClient(box.case.client, before=before, after=after)
        with pytest.raises(sink.RouterReviewArtifactError) as caught: persist(box, artifact)
        assert KEY not in str(caught.value) and scope.failed
        count = len(box.s3.puts)
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        with pytest.raises(Exception): generate(VISUAL)
        assert len(box.s3.puts) == count == (0 if stage == 'preflight' else 5)
        assert len(box.calls) == 1


@pytest.mark.parametrize('kind', ['prepared', 'wire', 'response', 'result', 'manifest'])
def test_s3_put_ack_loss_preserves_orphans_but_never_anchor_or_second_sink_retry(box, kind):
    def after(kw, response):
        if '/' + kind + '/' in kw['Key']: raise ConnectionError('PRIVATE ' + KEY)
    box.s3.after_put = after
    with captured(box) as (artifact, scope):
        before = _dump(box.case.client)
        with pytest.raises(sink.RouterReviewArtifactError) as caught: persist(box, artifact)
        assert KEY not in str(caught.value) and scope.failed
        count = len(box.s3.puts)
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        assert len(box.s3.puts) == count and _dump(box.case.client) == before
        assert box.s3.objects and len(box.calls) == 1


@pytest.mark.parametrize('damage', ['wrong_hash', 'length', 'bool_length', 'mime', 'status', 'too_many_bytes', 'read_error'])
def test_real_storage_readback_is_bounded_and_exact_before_anchor(box, damage):
    def patch(key, response):
        if damage == 'wrong_hash': response['Body'] = BytesIO(b'x' * response['ContentLength'])
        if damage == 'length': response['ContentLength'] += 1
        if damage == 'bool_length': response['ContentLength'] = True
        if damage == 'mime': response['ContentType'] = 'text/plain'
        if damage == 'status': response['ResponseMetadata']['HTTPStatusCode'] = 206
        if damage == 'too_many_bytes': response['Body'] = BytesIO(box.s3.objects[key] + b'x')
        if damage == 'read_error': response['Body'] = SimpleNamespace(
            read=Mock(side_effect=ConnectionError('PRIVATE ' + KEY)), close=lambda: None)
    box.s3.patch_get = patch
    with captured(box) as (artifact, scope):
        with pytest.raises(sink.RouterReviewArtifactError) as caught: persist(box, artifact)
        assert KEY not in str(caught.value) and scope.failed
    assert len(box.s3.puts) == 1 and box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


@pytest.mark.parametrize('grants', [
    [{'Grantee': {'Type': 'Group', 'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}],
    [{'Grantee': {'Type': 'CanonicalUser', 'ID': 'another-owner'}, 'Permission': 'FULL_CONTROL'}],
    [{**sink._TIGRIS_ADMINS, 'Permission': 'READ'}],
    [sink._TIGRIS_ADMINS, sink._TIGRIS_ADMINS],
])
def test_public_unknown_or_duplicate_acl_grants_do_not_create_an_anchor(box, grants):
    box.s3.extra_grants = grants
    with captured(box) as (artifact, scope):
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
    assert len(box.s3.puts) == 1 and box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


def test_exact_configured_tigris_owner_plus_admin_acl_is_accepted(box):
    box.s3.extra_grants = [sink._TIGRIS_ADMINS]
    with captured(box) as (artifact, scope):
        assert persist(box, artifact).diagnostic_only


@pytest.mark.parametrize('retries', [None, {}, {'total_max_attempts': 2}, {'total_max_attempts': True},
                                   {'max_attempts': 1}, {'max_attempts': False}])
def test_unknown_or_retrying_storage_sdk_rejected_without_io(box, retries):
    box.s3.meta.config.retries = retries
    with pytest.raises(sink.RouterReviewArtifactError): sink.RetainedRouterReviewArtifactSink(box.s3, bucket=BUCKET)
    assert not box.s3.puts and not box.s3.gets


@pytest.mark.parametrize('damage', ['source', 'flag', 'endpoint', 'retry_policy'])
def test_source_or_scope_drift_during_storage_readback_never_anchors(box, damage):
    def patch(key, response):
        if damage == 'source': box.case.client.set(continuity._AUTH_EPOCH, '13')
        if damage == 'flag': box.config.studio_abacus_router_retained_review_enabled = False
        if damage == 'endpoint': box.s3.meta.endpoint_url = 'https://attacker.invalid'
        if damage == 'retry_policy': box.s3.meta.config.retries = {'total_max_attempts': 2}
    box.s3.patch_get = patch
    with captured(box) as (artifact, scope):
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        assert scope.failed
    assert box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0 and len(box.calls) == 1


@pytest.mark.parametrize('damage', ['missing_prior', 'dict_prior', 'prior_blob', 'prior_acl', 'prior_ttl', 'prior_anchor'])
def test_visual_requires_complete_actual_private_durable_prior_story(box, damage):
    with captured(box) as (artifact, scope):
        first = persist(box, artifact)
        generate(VISUAL)
        visual = runtime.retained_router_review_artifacts()[VISUAL]
        prior = first
        if damage == 'missing_prior': prior = None
        if damage == 'dict_prior': prior = first.receipt
        if damage == 'prior_blob': box.s3.objects[first.receipt['manifest']['key']] = b'{}'
        if damage == 'prior_acl': box.s3.extra_grants = [{'Grantee': {'Type': 'Group', 'URI': 'public'}, 'Permission': 'READ'}]
        if damage == 'prior_ttl': box.case.client.expire(first.receipt['anchor_key'], 100)
        if damage == 'prior_anchor': box.case.client.set(first.receipt['anchor_key'], '{}')
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, visual, prior)
        assert len(box.s3.puts) == 5 and box.case.client.exists(sink.VISUAL_ANCHOR_KEY) == 0


def test_existing_anchor_is_never_overwritten_even_in_a_fresh_sink(box):
    with captured(box) as (artifact, scope):
        box.case.client.set(sink.STORY_ANCHOR_KEY, 'existing-private-anchor')
        before = _dump(box.case.client)
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        assert not box.s3.puts and _dump(box.case.client) == before


@pytest.mark.parametrize('status', [None, True, 201])
def test_successful_put_with_nonexact_ack_never_reads_or_anchors(box, status):
    box.s3.after_put = lambda kw, response: response['ResponseMetadata'].update(HTTPStatusCode=status)
    with captured(box) as (artifact, scope):
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
    assert len(box.s3.puts) == 1 and not box.s3.gets and not box.s3.acls
    assert box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


@pytest.mark.parametrize('damage', ['ttl', 'bytes', 'scope_after_read_ack'])
def test_anchor_write_ack_requires_exact_durable_readback_and_final_scope(box, damage):
    with captured(box) as (artifact, scope):
        def after(number, ack):
            if number == 2 and damage == 'ttl': box.case.client.expire(sink.STORY_ANCHOR_KEY, 60)
            if number == 2 and damage == 'bytes': box.case.client.set(sink.STORY_ANCHOR_KEY, '{}')
            if number == 3 and damage == 'scope_after_read_ack': scope.failed = True
            return ack
        scope.journal.client = InterceptClient(box.case.client, after=after)
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        assert scope.failed and len(box.s3.puts) == 5
        assert box.case.client.exists(sink.STORY_ANCHOR_KEY) == 1


def test_existing_blob_is_never_overwritten_or_treated_as_a_new_ack(box):
    with captured(box) as (artifact, scope):
        pointer = sink._pointer(STORY, 'prepared', artifact.prepared_body_bytes, scope.journal.keys)
        box.s3.objects[pointer['key']] = artifact.prepared_body_bytes
        previous = deepcopy(box.s3.objects)
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
        assert box.s3.objects == previous and len(box.s3.puts) == 1
        assert not box.s3.gets and box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


def test_tigris_admin_grant_on_another_configured_endpoint_is_not_private(box):
    box.s3.meta.endpoint_url = sink.storage.settings.endpoint = 'https://s3.example.invalid'
    box.s3.extra_grants = [sink._TIGRIS_ADMINS]
    with captured(box) as (artifact, scope):
        with pytest.raises(sink.RouterReviewArtifactError): persist(box, artifact)
    assert len(box.s3.puts) == 1 and box.case.client.exists(sink.STORY_ANCHOR_KEY) == 0


def test_actual_successor_scope_persists_only_selected_namespace_and_preserves_old_unknowns(
        successor_prepared, monkeypatch):
    from app.services.production_spend import SpendLedger, SpendPolicy
    prepared = successor_prepared
    authorization = commission_successor(prepared)
    original = predecessor_records(prepared.client, OLD_KEYS)
    prepared.config.studio_abacus_router_retained_review_enabled = True
    monkeypatch.setattr(runtime.spending, 'settings', prepared.config)
    foundation = SpendLedger(prepared.client, SpendPolicy(**json.loads(
        prepared.config.studio_spend_policy_json)), clock=lambda: SUCCESSOR_NOW)
    monkeypatch.setattr(runtime.spending, 'configured_ledger', lambda: foundation)
    monkeypatch.setattr(sink.storage, 'settings', SimpleNamespace(bucket=BUCKET, endpoint=ENDPOINT))
    s3, calls = FakeS3(), []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, request=request, headers={'content-type': 'application/json'},
                              stream=Chunks([json.dumps(payload()).encode()]))
    monkeypatch.setattr(httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    with runtime.retained_router_review_scope(continuity.LEAF_ID, successor=authorization) as scope:
        target = sink.RetainedRouterReviewArtifactSink(s3, bucket=BUCKET)
        generate()
        first = target.persist(runtime.retained_router_review_artifacts()[STORY])
        generate(VISUAL)
        second = target.persist(runtime.retained_router_review_artifacts()[VISUAL], prior_story_anchor=first)
        assert scope.journal.keys != (journal.STATE_KEY, journal.JOURNAL_KEY, journal.ANCHOR_KEY)
        for receipt, purpose in ((first, STORY), (second, VISUAL)):
            assert receipt.receipt['anchor_key'] == sink._anchor_keys(scope.journal.keys)[purpose]
            assert sink._hash(list(scope.journal.keys)) in receipt.receipt['manifest']['key']
            record = json.loads(s3.objects[receipt.receipt['manifest']['key']])
            assert record['journal_keys'] == list(scope.journal.keys)
            assert record['journal_state']['policy']['credential_sha256'] == prepared.story_policy['credential_sha256']
    assert len(calls) == 2 and len(s3.puts) == 10
    assert predecessor_records(prepared.client, OLD_KEYS) == original == prepared.legacy
    assert prepared.client.exists(sink.STORY_ANCHOR_KEY) == prepared.client.exists(sink.VISUAL_ANCHOR_KEY) == 0
