"""Synthetic real completion/capture histories; never replay a production reply.

The actual runtime captures a complete positive synthetic reply with conflicting
HTTP framing headers. Its live observer rejects it, leaving the real reservation
unknown. Independent interpretation deliberately proves less than that observer.
"""
import base64
from copy import copy, deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from redis.exceptions import ConnectionError

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_runtime as runtime
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_transport_capture as capture
from app.services import retained_review_completion_plan as completion
from app.services import retained_transport_story_evidence as reader
from app.services import retained_story_visual_evidence as shared
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity
from app.services import immutable_story_review_contract as story_contract
from app.services import retained_cut_evidence as cuts
from app.services.production_spend import SpendBlocked, SpendPolicy
from test_retained_story_visual_evidence import source, case, planning_case, real_media
from test_retained_review_credential_successor import prepared, Intercept, records, NEW_KEY
from test_retained_router_protocol_probe import frozen_three
from test_retained_review_completion_plan import completed_probe, commission
from test_abacus_router_protocol_diagnostic import wire, forbid_live_transport
from test_abacus_router_audio_review_journal import NOW
from test_retained_cut_evidence import BUCKET
from test_abacus_router_review_runtime import payload
from test_production_connection_continuity import _dump

STORY = journal.PURPOSES[0]
ERROR = '^retained_transport_story_'


@pytest.fixture
def captured(completed_probe, monkeypatch, request):
    box = completed_probe
    box.s3 = box.source.s3
    box.cap = commission(box)
    box.config.studio_abacus_router_retained_review_enabled = True
    monkeypatch.setattr(reader, 'settings', box.config)
    monkeypatch.setattr(cuts.storage, '_client', lambda *, single_attempt=False: box.s3)
    foundation = SimpleNamespace(client=box.client, clock=lambda: NOW,
        policy=SpendPolicy(**{name: 0 for name in runtime._CAPS}))
    monkeypatch.setattr(runtime.spending, 'configured_ledger', Mock(return_value=foundation))
    original, _ = recovery._state(continuity.LEAF_ID, box.client)
    options = recovery._options(original)
    package = recovery._immutable_shooting_package(recovery._manifests(box.s3, original)[0]['package'], {}, options)
    box.contract = story_contract.derive_immutable_story_review_contract(package, original['spec']['topic'], .5,
        original['spec']['language'], options,
        immutable_candidate_narrations=[scene['narration'] for scene in package['scenes']])
    prompt = box.contract['request']['parts'][0]['text']
    result = json.JSONDecoder().raw_decode(prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])[0]
    response = payload(model=NEW_KEY)  # Valid identifier; must never be echoed publicly.
    response.pop('id'); response.pop('object')
    response['choices'][0]['native_finish_reason'] = 'STOP'
    response['choices'][0]['message']['content'] = json.dumps(result)
    response['usage'] = {'input_tokens': 217, 'output_tokens': 9, 'raw_input_tokens': 217}
    box.wire.chunks = [reader._raw(response)]
    box.wire.headers = {'content-type': 'application/json', 'content-length': str(len(box.wire.chunks[0])),
                        'transfer-encoding': 'chunked'}
    sent_contract = deepcopy(box.contract['request'])
    if getattr(request, 'param', None) == 'different_request':
        sent_contract['system_instruction'] += ' Extra instruction absent from the original source contract.'
    with runtime.retained_router_review_scope(continuity.LEAF_ID,
            completion_plan=box.cap, capture_transport=True) as scope:
        with pytest.raises(SpendBlocked):
            runtime.generate_retained_router_review(**sent_contract)
        box.receipt = scope.transport_captures[STORY]
        assert scope.evidence == {} and scope._artifacts == {}
    assert box.receipt['capture_acknowledged'] is True
    box.intent_key = box.receipt['anchor_key'].rsplit(':', 1)[0] + ':intent'
    with box.client.pipeline() as pipe:
        pipe.watch(*completion.STORY_KEYS)
        box.state = journal.RouterReviewJournal(box.client, completion_plan=box.cap)._read(pipe)
        completion._ping(pipe)
    assert set(box.state['slots']) == {STORY} and box.state['slots'][STORY]['response'] is None
    assert len(box.wire.calls) == 2 and len(box.s3.puts) == 1
    box.before = _dump(box.client)
    # Any operation below is a saved-byte read. It cannot send, observe, settle,
    # normalize, acquire freshness or mutate storage even by accident.
    forbidden = Mock(side_effect=AssertionError('PRIVATE forbidden operation'))
    for owner, name in ((adapter, 'observe_router_response'), (runtime, 'generate_retained_router_review'),
            (journal.RouterReviewJournal, 'settle'), (journal.RouterReviewJournal, 'reserve'),
            (journal.RouterReviewJournal, '_fresh'), (capture, '_send'), (box.s3, 'put_object')):
        monkeypatch.setattr(owner, name, forbidden)
    box.forbidden = forbidden
    return box


def read(box, client=None, cap=None):
    return reader.read_retained_transport_story_evidence(client or box.client, box.s3,
        bucket=BUCKET, completion_plan=box.cap if cap is None else cap)


def snapshot(box):
    return records(box.client, list(box.client.scan_iter())), deepcopy(box.s3.objects)


def restore(box, saved):
    keys = list(box.client.scan_iter())
    if keys:
        box.client.delete(*keys)
    for key, raw in saved[0].items():
        box.client.restore(key, 0, raw)
    box.s3.objects = deepcopy(saved[1])
    box.s3.damage_read = None
    box.s3.extra_grants = []


def decrypt(box):
    pointer = box.receipt['encrypted_blob']
    cipher = box.s3.objects[pointer['key']][0]
    key = base64.urlsafe_b64encode(reader.hmac.new(box.config.app_encryption_key.encode(),
        capture._DOMAIN, reader.hashlib.sha256).digest())
    packet = reader.Fernet(key).decrypt(cipher)
    offset = len(capture._DOMAIN)
    size = int.from_bytes(packet[offset:offset + 4], 'big'); offset += 4
    context = json.loads(packet[offset:offset + size]); offset += size
    bodies = {}
    for name in ('prepared', 'wire', 'response'):
        size = context[name + '_size']; bodies[name] = packet[offset:offset + size]; offset += size
    assert offset == len(packet)
    return key, context, bodies


def reseal(box, mutate, *, refresh=True, suffix=b'', wrong_domain=False):
    """Adversarial storage fixture with actual original capture as its preimage."""
    key, context, bodies = decrypt(box)
    mutate(context, bodies)
    if refresh:
        for name, body in bodies.items():
            context[name + '_size'] = len(body)
            context[name + '_sha256'] = reader._sha(body)
        context['summary'].update(response_bytes=len(bodies['response']),
            response_sha256=reader._sha(bodies['response']),
            root_shape=capture._shape(bodies['response'], True))
        context['summary']['headers']['content_length'] = len(bodies['response'])
    encoded = reader._raw(context)
    domain = b'wrong-domain\0' if wrong_domain else capture._DOMAIN
    packet = domain + len(encoded).to_bytes(4, 'big') + encoded
    packet += b''.join(bodies[name] for name in ('prepared', 'wire', 'response')) + suffix
    ciphertext = reader.Fernet(key).encrypt(packet)
    anchor = json.loads(box.client.get(box.receipt['anchor_key']))
    digest = reader._sha(ciphertext)
    old = anchor['encrypted_blob']
    pointer = {**old, 'key': old['key'].rsplit('/', 1)[0] + '/' + digest + '.fernet',
               'sha256': digest, 'size': len(ciphertext)}
    box.s3.objects[pointer['key']] = (ciphertext, 'application/octet-stream')
    anchor.update(encrypted_blob=pointer, summary=context['summary'])
    box.client.set(box.receipt['anchor_key'], reader._raw(anchor).decode())


def test_actual_capture_independent_semantics_is_historical_read_only_and_private(captured, monkeypatch):
    box = captured
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'b' * 40)
    box.config.studio_abacus_router_retained_review_enabled = False
    box.config.abacus_api_key = 'different-current-key-no-send-permission'
    client = Intercept(box.client)
    value = read(box, client)
    assert type(value) is reader.RetainedTransportStoryEvidence
    record = value.record
    assert record['component_pass'] is True and record['read_acknowledged'] is True
    assert record['provenance'] == 'authenticated_preobserver_transport'
    assert record['saved_header_summary_only'] is True
    assert record['selected_unknown_count'] == record['selected_occupied_count'] == 1
    assert record['prior_unknown_count'] == 3 and record['historical_record_count'] == 27
    assert all(record[name] is expected for name, expected in reader._FLAGS.items())
    assert value.qa_approved is value.publish_eligible is False
    assert value.commitments['response_sha256'] == reader._sha(box.wire.chunks[0])
    assert value.commitments['wire_sha256'] == reader._sha(box.wire.calls[-1].content)
    assert value.commitments['request_sha256'] == box.receipt['binding']['request_sha256']
    assert client.executions == [('PING',)] and _dump(box.client) == box.before
    assert 'transfer-encoding' not in box.receipt['summary']['headers']
    assert box.receipt['summary']['headers']['content_length'] == len(box.wire.chunks[0])
    # The saved summary deliberately cannot prove the live observer's rejection
    # condition; this return must not silently upgrade its provenance.
    assert record['live_observer_verified'] is record['settlement_observed'] is False
    text = json.dumps(record) + repr(value)
    for secret in (NEW_KEY, box.config.app_encryption_key, box.config.abacus_api_key,
                   box.contract['candidate']['scenes'][0]['narration']):
        assert secret not in text
    record['component_pass'] = False
    assert value.record['component_pass'] is True
    with pytest.raises(TypeError): reader.RetainedTransportStoryEvidence()
    with pytest.raises((TypeError, reader.RetainedTransportStoryEvidenceError)): copy(value).record
    with pytest.raises(reader.RetainedTransportStoryEvidenceError):
        object.__new__(reader.RetainedTransportStoryEvidence).record
    box.forbidden.assert_not_called()


def test_actual_capture_rejects_storage_history_source_capability_and_ack_faults(captured, subtests):
    box = captured
    saved = snapshot(box)
    faults = ('anchor_missing', 'intent_missing', 'anchor_ttl', 'intent_ttl', 'intent_changed',
        'pointer_namespace', 'cipher_changed', 'public_acl', 'metadata_changed', 'manifest_changed',
        'audio_changed', 'old_history', 'probe_changed', 'selected_rollback', 'controller_changed',
        'archive_missing', 'source_claim', 'source_race', 'anchor_race', 'lost_ack', 'bad_ack',
        'wrong_cap', 'well_formed_wrong_cap', 'wrong_key')
    material = box.config.app_encryption_key
    for fault in faults:
        with subtests.test(fault=fault):
            restore(box, saved); box.config.app_encryption_key = material
            client, cap = Intercept(box.client), box.cap
            anchor_key = box.receipt['anchor_key']
            if fault == 'anchor_missing': box.client.delete(anchor_key)
            elif fault == 'intent_missing': box.client.delete(box.intent_key)
            elif fault == 'anchor_ttl': box.client.expire(anchor_key, 300)
            elif fault == 'intent_ttl': box.client.expire(box.intent_key, 300)
            elif fault == 'intent_changed':
                value = json.loads(box.client.get(box.intent_key)); value['storage_bucket_sha256'] = '0' * 64
                box.client.set(box.intent_key, reader._raw(value).decode())
            elif fault == 'pointer_namespace':
                value = json.loads(box.client.get(anchor_key)); value['encrypted_blob']['key'] = 'foreign/capture.fernet'
                box.client.set(anchor_key, reader._raw(value).decode())
            elif fault == 'cipher_changed':
                key = box.receipt['encrypted_blob']['key']; raw, mime = box.s3.objects[key]
                box.s3.objects[key] = (raw[:-1] + b'X', mime)
            elif fault == 'public_acl':
                box.s3.extra_grants = [{'Grantee': {'Type': 'Group',
                    'URI': 'http://acs.amazonaws.com/groups/global/AllUsers'}, 'Permission': 'READ'}]
            elif fault in ('metadata_changed', 'manifest_changed', 'audio_changed'):
                original, _ = recovery._state(continuity.LEAF_ID, box.client)
                key = (original['audio_candidate_checkpoint']['metadata_key'] if fault == 'metadata_changed'
                    else original['audio_candidate_checkpoint']['audio_key'] if fault == 'audio_changed'
                    else original['generated_asset_candidates']['entries'][0]['manifest_key'])
                raw, mime = box.s3.objects[key]; box.s3.objects[key] = (raw + b' ', mime)
            elif fault == 'old_history': box.client.set(completion.HISTORICAL_KEYS[0], '{}')
            elif fault == 'probe_changed': box.client.set(completion.HISTORICAL_KEYS[-3], '{}')
            elif fault == 'selected_rollback': box.client.set(completion.STORY_KEYS[0], '{}')
            elif fault == 'controller_changed': box.client.set(completion.ANCHOR_KEY, '0' * 64)
            elif fault == 'archive_missing': box.client.delete(box.archive_key)
            elif fault in ('source_claim', 'source_race'):
                def claim(*unused):
                    key = continuity._JOB + continuity.LEAF_ID
                    job = json.loads(box.client.get(key)); job['retry_claimed'] = True
                    box.client.set(key, reader._raw(job).decode())
                if fault == 'source_claim': claim()
                else: client = Intercept(box.client, before=claim)
            elif fault == 'anchor_race':
                client = Intercept(box.client, before=lambda *args: box.client.delete(anchor_key))
            elif fault == 'lost_ack':
                def lose(*args): raise ConnectionError('PRIVATE backend secret')
                client = Intercept(box.client, after=lose)
            elif fault == 'bad_ack': client = Intercept(box.client, after=lambda *args: [1])
            elif fault == 'wrong_cap': cap = object()
            elif fault == 'well_formed_wrong_cap':
                cap = completion._authorization(box.cap._manifest_bytes)
                manifest = json.loads(cap._manifest_bytes)
                manifest['attestation']['owner_authorization_sha256'] = '9' * 64
                object.__setattr__(cap, '_manifest_bytes', reader._raw(manifest))
            elif fault == 'wrong_key': box.config.app_encryption_key = 'wrong-application-key'
            with pytest.raises(reader.RetainedTransportStoryEvidenceError, match=ERROR) as caught:
                read(box, client, cap)
            assert 'PRIVATE' not in str(caught.value)
            assert all(commands == ('PING',) for commands in client.executions)
            assert len(box.s3.puts) == 1 and len(box.wire.calls) == 2
    box.forbidden.assert_not_called()


def test_authenticated_packet_does_not_replace_request_headers_or_full_semantics(captured, subtests):
    box = captured
    saved = snapshot(box)
    faults = ('extra_packet_bytes', 'wrong_domain', 'missing_bytes', 'bool_size', 'body_hash',
        'reservation', 'policy', 'source', 'binding', 'context_extra', 'wire_changed', 'prepared_and_wire',
        'header_extra', 'header_encoding', 'header_redirect', 'header_length', 'http_status',
        'incomplete', 'unbound', 'transport_failure', 'summary_hash', 'root_shape',
        'native_finish', 'canonical_finish', 'mixed_usage', 'global_semantics', 'natural_language', 'positive_language_contradiction',
        'ending_semantics', 'bool_schema', 'extra_approval')
    for fault in faults:
        with subtests.test(fault=fault):
            restore(box, saved)
            def damage(context, bodies):
                if fault == 'missing_bytes': bodies['response'] = bodies['response'][:-1]
                elif fault == 'bool_size': context['response_size'] = True
                elif fault == 'body_hash': context['response_sha256'] = '0' * 64
                elif fault == 'reservation': context['reservation']['reservation_sha256'] = '0' * 64
                elif fault == 'policy': context['policy']['credential_sha256'] = '0' * 64
                elif fault == 'source': context['source']['profile_revision'] = '0' * 64
                elif fault == 'binding': context['binding']['credential_sha256'] = '0' * 64
                elif fault == 'context_extra': context['extra'] = 'PRIVATE provider key'
                elif fault in ('wire_changed', 'prepared_and_wire'):
                    body = json.loads(bodies['wire']); body['messages'][0]['content'] += ' changed'
                    bodies['wire'] = reader._raw(body)
                    if fault == 'prepared_and_wire': bodies['prepared'] = bodies['wire']
                elif fault == 'header_extra': context['summary']['headers']['request-id'] = 'PRIVATE'
                elif fault == 'header_encoding': context['summary']['headers']['content_encoding'] = 'other'
                elif fault == 'header_redirect': context['summary']['headers']['redirect_history'] = True
                elif fault == 'header_length': context['summary']['headers']['content_length'] = True
                elif fault == 'http_status': context['summary']['http_status'] = 201
                elif fault == 'incomplete': context['summary']['response_complete'] = False
                elif fault == 'unbound': context['summary']['response_binding_verified'] = False
                elif fault == 'transport_failure': context['summary']['transport_outcome'] = 'stream_failed'
                elif fault == 'summary_hash': context['summary']['response_sha256'] = '0' * 64
                elif fault == 'root_shape': context['summary']['root_shape']['key_count'] = 999
                elif fault in ('native_finish', 'canonical_finish', 'mixed_usage', 'global_semantics', 'positive_language_contradiction',
                        'natural_language', 'ending_semantics', 'bool_schema', 'extra_approval'):
                    reply = json.loads(bodies['response']); choice = reply['choices'][0]
                    result = json.loads(choice['message']['content'])
                    if fault == 'native_finish': choice['native_finish_reason'] = 'stop'
                    elif fault == 'canonical_finish': choice['finish_reason'] = 'length'
                    elif fault == 'mixed_usage': reply['usage']['total_tokens'] = 226
                    elif fault == 'global_semantics': result['story_review']['causal_claim_supported'] = False
                    elif fault == 'natural_language':
                        result['story_review']['natural_spoken_language'] = False
                        result['story_review']['natural_spoken_language_evidence'] = 'Scene 1 “Members pay” sounds unnatural.'
                    elif fault == 'positive_language_contradiction':
                        result['story_review']['natural_spoken_language_evidence'] = 'Scene 1 “Members pay” sounds unnatural.'
                    elif fault == 'ending_semantics': result['ending_pair']['everyday_benefit_visible'] = False
                    elif fault == 'bool_schema': result['story_review']['causal_claim_supported'] = 1
                    elif fault == 'extra_approval': result['qa_approved'] = True
                    choice['message']['content'] = json.dumps(result)
                    bodies['response'] = reader._raw(reply)
            refresh = fault in ('wire_changed', 'prepared_and_wire', 'native_finish', 'canonical_finish',
                'mixed_usage', 'global_semantics', 'natural_language', 'positive_language_contradiction', 'ending_semantics', 'bool_schema', 'extra_approval')
            reseal(box, damage, refresh=refresh,
                suffix=b'PRIVATE trailing plaintext' if fault == 'extra_packet_bytes' else b'',
                wrong_domain=fault == 'wrong_domain')
            client = Intercept(box.client)
            with pytest.raises(reader.RetainedTransportStoryEvidenceError, match=ERROR) as caught:
                read(box, client)
            assert 'PRIVATE' not in str(caught.value)
            assert all(commands == ('PING',) for commands in client.executions)
    box.forbidden.assert_not_called()


def test_unknown_without_capture_and_unselected_namespaces_fail_before_storage(completed_probe, monkeypatch):
    box = completed_probe
    cap = commission(box)
    box.s3 = box.source.s3
    monkeypatch.setattr(reader, 'settings', box.config)
    journal.RouterReviewJournal(box.client, clock=lambda: NOW, completion_plan=cap).reserve(STORY, box.new_request)
    before = _dump(box.client)
    reads = len(box.s3.reads)
    for authority in (cap, None, object()):
        with pytest.raises(reader.RetainedTransportStoryEvidenceError, match=ERROR):
            reader.read_retained_transport_story_evidence(box.client, box.s3,
                bucket=BUCKET, completion_plan=authority)
    assert len(box.s3.reads) == reads and _dump(box.client) == before and not box.s3.puts


@pytest.mark.parametrize('captured', ['different_request'], indirect=True)
def test_genuine_capture_of_a_different_request_cannot_borrow_source_eligibility(captured):
    # This is an actual runtime-issued reservation and encrypted capture, not a
    # detached forged body whose wrapped request hash merely fails validation.
    box = captured
    client = Intercept(box.client)
    with pytest.raises(reader.RetainedTransportStoryEvidenceError,
            match='^retained_transport_story_request_changed$'):
        read(box, client)
    assert client.executions == [] and _dump(box.client) == box.before
    box.forbidden.assert_not_called()


def test_authenticated_private_values_never_escape_and_wrong_decryption_key_rejects(captured, monkeypatch):
    box = captured
    secrets = [NEW_KEY, box.config.app_encryption_key, 'SYNTHETIC_UNTRUSTED_BODY_VALUE']
    variants = [variant for value in secrets for variant in (
        value, base64.b64encode(value.encode()).decode(), base64.urlsafe_b64encode(value.encode()).decode(),
        ''.join('\\u%04x' % ord(char) for char in value))]
    def echoed(context, bodies):
        response = json.loads(bodies['response'])
        result = json.loads(response['choices'][0]['message']['content'])
        result['story_review']['reason'] += ' ' + ' '.join(variants)
        response['choices'][0]['message']['content'] = json.dumps(result)
        bodies['response'] = reader._raw(response)
    reseal(box, echoed)
    before = _dump(box.client)
    value = read(box)
    public = json.dumps(value.record) + repr(value)
    assert all(variant not in public for variant in variants)
    assert value.record['component_pass'] is True and _dump(box.client) == before
    # Keep the archived-candidate APP key intact so this specifically exercises
    # the capture packet's Fernet authentication, not earlier history rejection.
    monkeypatch.setattr(reader, 'settings', SimpleNamespace(app_encryption_key='wrong-private-capture-key'))
    with pytest.raises(reader.RetainedTransportStoryEvidenceError, match=ERROR) as caught:
        read(box)
    assert all(variant not in str(caught.value) for variant in variants)
    assert _dump(box.client) == before
    box.forbidden.assert_not_called()
