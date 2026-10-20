"""Real visual schema, HTTPX observation and durable two-request accounting."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import production_included_router as included
from app.services import production_included_transport as transport
from app.services import production_spend_runtime as runtime
from app.services import included_visual_completion as completion
from app.services.production_spend import SpendBlocked
from test_production_included_router import commissioned, CONTEXT, CHANNEL
from test_production_credit_ledger import client
from test_production_cash_disabled import dump
from test_abacus_router_adapter import KEY, image, jpeg, response, envelope
from test_visual_cross_provider_review import _namespace, _review, EVIDENCE


@pytest.fixture
def case(commissioned, monkeypatch):
    ledger, _, _ = commissioned
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', True)
    monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', True)
    monkeypatch.setattr(runtime.settings, 'abacus_api_key', KEY)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *_: deepcopy(CONTEXT))
    ns = _namespace()
    ns['settings'].studio_abacus_included_production = True
    complete = {'reviews': [
        _review(ns, index=0, score=35, subject_visible=False,
                reason='Unrelated steel machinery instead of the narrated banknote.'),
        _review(ns, index=1, score=90)]}
    partial = deepcopy(complete)
    for row in partial['reviews']:
        for field in completion.OMITTABLE:
            row.pop(field)
    fields = {'reviews': [{field: row[field] for field in ('scene_index', *completion.OMITTABLE)}
                          for row in complete['reviews']]}
    values = [partial, fields]
    calls = []
    def send(prepared):
        calls.append(prepared)
        value = values[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        payload = envelope()
        payload['choices'][0]['message']['content'] = json.dumps(value)
        return response(prepared, payload=payload)
    sender = Mock(side_effect=send)
    monkeypatch.setattr(transport, 'send_once', sender)
    return SimpleNamespace(ledger=ledger, ns=ns, complete=complete, partial=partial, fields=fields,
                           values=values, calls=calls, sender=sender)


def run(case):
    url = image()['image_url']['url']
    content = [{'type': 'input_text', 'text': 'System rubric is separate.'},
               {'type': 'input_text', 'text': 'Original scene 0 and 1, unaltered frames.'},
               {'type': 'input_image', 'image_url': url}]
    return case.ns['_request_visual_review']('abacus_included', True, 'Full original rubric.',
        content, [], [0, 1], {0: {0: {0, 1, 2}}, 1: {0: {0, 1, 2}}}, None, 'low')


def journal(case):
    return json.loads(case.ledger.client.get(included.JOURNAL_KEY))


def test_missing_flags_complete_once_on_exact_frames_and_reuse_actual_receipt(case):
    assert run(case) == case.complete
    initial, repair = case.calls
    assert initial.request_sha256 != repair.request_sha256
    assert initial.payload['messages'][1]['content'][:-1] == repair.payload['messages'][1]['content'][:-1]
    assert initial.payload['messages'][0]['content'] in repair.payload['messages'][0]['content']
    assert 'INCLUDED_VISUAL_REQUIRED_FIELDS_V1' in initial.payload['messages'][0]['content']
    assert 'VISUAL_MISSING_FIELDS_ONLY_V2' in repair.payload['messages'][0]['content']
    rows = list(journal(case)['requests'].values())
    assert len(rows) == 2
    source = next(row for row in rows if row['outcome'] is None)
    target = next(row for row in rows if row['outcome'] is not None)
    assert source['completion']['format'] == 'missing_fields_v2'
    assert included._result(repair, target['outcome']) == case.fields
    assert source['completion']['request_sha256'] == target['request_sha256'] == repair.request_sha256
    assert included._LAST_OBSERVED.get()['evidence']['request_sha256'] == repair.request_sha256
    failures = list(case.ledger.client.scan_iter(match=included.PREFIX + 'failure:*'))
    assert len(failures) == 1
    failure = json.loads(case.ledger.client.get(failures[0]))
    assert failure['http_status'] == 200 and failure['retry_allowed'] is False
    assert failure['request_sha256'] == initial.request_sha256
    before = dump(case.ledger.client)
    assert run(case) == case.complete
    assert dump(case.ledger.client) == before and len(case.calls) == 2
    cash = case.ledger.foundation.snapshot()
    assert cash['historical_cash_micro'] is None and cash['cash_spending_enabled'] is False


@pytest.mark.parametrize('field,value', [('score', 99), ('reason', 'Actually acceptable'),
    ('best_candidate_index', 1), ('subject_visible', True), ('retry_queries', []),
    ('evidence_moment_indices', [0, 1])])
def test_completion_cannot_change_any_original_judgment(case, field, value):
    # Candidate 1 is outside this request's schema and is rejected even earlier.
    case.fields['reviews'][0][field] = value
    with pytest.raises(SpendBlocked): run(case)
    assert len(case.calls) == 2 and len(journal(case)['requests']) == 2
    assert all('completion' not in row for row in journal(case)['requests'].values())
    before = dump(case.ledger.client)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert dump(case.ledger.client) == before and len(case.calls) == 2


@pytest.mark.parametrize('problem', ['unrelated_missing', 'duplicate_scene', 'missing_scene',
    'wrong_scene', 'duplicate_moments', 'wrong_boolean', 'nan_score', 'full_response'])
def test_incomplete_judgments_and_invalid_values_are_not_eligible(case, problem):
    row = case.partial['reviews'][0]
    if problem == 'unrelated_missing': row.pop('reason')
    elif problem == 'duplicate_scene': case.partial['reviews'][1]['scene_index'] = 0
    elif problem == 'missing_scene': case.partial['reviews'].pop()
    elif problem == 'wrong_scene': row['scene_index'] = 7
    elif problem == 'duplicate_moments': row['evidence_moment_indices'] = [1, 1]
    elif problem == 'wrong_boolean': row['receiving_interface_visible'] = 'false'
    elif problem == 'nan_score': row['score'] = float('nan')
    else: case.values[0] = case.complete
    if problem == 'full_response':
        assert run(case) == case.complete
        assert run(case) == case.complete
    else:
        with pytest.raises(SpendBlocked, match='response_unverified'): run(case)
        with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 1


@pytest.mark.parametrize('second', ['incomplete', 'timeout'])
def test_second_failure_is_terminal_without_a_third_request(case, second):
    case.values[1] = case.partial if second == 'incomplete' else TimeoutError('Unknown response')
    with pytest.raises(SpendBlocked): run(case)
    before = dump(case.ledger.client)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 2 and dump(case.ledger.client) == before
    assert all(row['outcome'] is None for row in journal(case)['requests'].values())


@pytest.mark.parametrize('commit', [False, True])
def test_failure_capture_ack_must_be_certain_before_completion(case, monkeypatch, commit):
    original = included.IncludedRouterLedger.record_failure
    def uncertain(self, *args):
        if commit: original(self, *args)
        raise ConnectionError('Lost capture ACK')
    monkeypatch.setattr(included.IncludedRouterLedger, 'record_failure', uncertain)
    with pytest.raises(SpendBlocked, match='response_unverified'): run(case)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 1


@pytest.mark.parametrize('commit', [False, True])
def test_link_ack_loss_never_replays_either_provider_request(case, monkeypatch, commit):
    original = completion.link_completed_review
    def uncertain(*args):
        if commit: original(*args)
        raise ConnectionError('Lost completion link ACK')
    monkeypatch.setattr(completion, 'link_completed_review', uncertain)
    with pytest.raises(SpendBlocked, match='included_visual_completion_unverified'): run(case)
    monkeypatch.setattr(completion, 'link_completed_review', original)
    assert run(case) == case.complete
    assert len(case.calls) == 2


@pytest.mark.parametrize('corruption', ['failure', 'target', 'proof', 'source_outcome', 'channel'])
def test_cached_completion_requires_intact_pair_failure_and_current_channel(case, corruption):
    run(case)
    client = case.ledger.client
    record = journal(case)
    source = next(row for row in record['requests'].values() if 'completion' in row)
    target_id = next(key for key, row in record['requests'].items() if row['outcome'] is not None)
    if corruption == 'failure':
        key = next(client.scan_iter(match=included.PREFIX + 'failure:*'))
        client.set(key, client.get(key) + ' ')
    elif corruption == 'channel':
        client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL,
            'connection_id': 'new-google-connection', 'requires_reconnect': False}))
    else:
        if corruption == 'target': record['requests'].pop(target_id)
        elif corruption == 'proof': source['completion']['response_proof_sha256'] = '0' * 64
        else: source['outcome'] = record['requests'][target_id]['outcome']
        client.set(included.JOURNAL_KEY, included._raw(record))
        # Even a recomputed anchor cannot make a malformed edge valid.
        state = json.loads(client.get(included.STATE_KEY))
        client.set(included.ANCHOR_KEY, included._sha({'state': state, 'journal': record}))
    before = dump(client)
    with pytest.raises(SpendBlocked): run(case)
    assert len(case.calls) == 2 and dump(client) == before


def test_actual_visual_normalizer_keeps_low_scores_rejected(case, tmp_path):
    case.ns['_frame'] = Mock(side_effect=lambda *_: SimpleNamespace(read_bytes=lambda: jpeg()))
    scenes = [{'index': i, 'narration': 'Banknote material.',
               'visual_queries': ['dollar banknote close up'], 'ai_prompt': None} for i in range(2)]
    result = case.ns['review_scene_visuals'](scenes,
        [[{'path': f'scene-{i}.mp4', 'source_type': 'stock', 'source_id': str(i)}] for i in range(2)],
        tmp_path, 2, topic='US banknotes', story_scenes=scenes,
        evidence_sources=EVIDENCE, provider_override='abacus_included', _missing_review_attempts=0)
    assert len(case.calls) == 2
    assert result['reviews'][0]['score'] <= 35 and result['reviews'][0]['subject_visible'] is False
    assert all(field in result['reviews'][0] for field in completion.OMITTABLE)
    assert 'steel machinery' in result['reviews'][0]['reason']


def test_both_requests_consume_capacity_even_after_successful_completion(case):
    from test_production_included_router import request
    run(case)
    case.ledger.reserve(CONTEXT, 'research', request('Third request'))
    with pytest.raises(SpendBlocked, match='episode_limit'):
        case.ledger.reserve(CONTEXT, 'research', request('Fourth request'))
    assert len(journal(case)['requests']) == 3 and len(case.calls) == 2


@pytest.mark.parametrize('commit', [False, True])
def test_completion_settlement_ack_loss_is_terminal_without_new_send(case, monkeypatch, commit):
    original = included.IncludedRouterLedger.settle
    def uncertain(self, *args):
        if commit: original(self, *args)
        raise SpendBlocked('included_router_settlement_uncertain')
    monkeypatch.setattr(included.IncludedRouterLedger, 'settle', uncertain)
    with pytest.raises(SpendBlocked, match='settlement_uncertain'): run(case)
    if commit:
        assert run(case) == case.complete
    else:
        with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 2


@pytest.mark.parametrize('problem', ['http_error', 'wrong_wire', 'timeout'])
def test_untrusted_transport_never_becomes_a_completion(case, monkeypatch, problem):
    def send(prepared):
        case.calls.append(prepared)
        payload = envelope()
        payload['choices'][0]['message']['content'] = json.dumps(case.partial)
        if problem == 'timeout': raise TimeoutError('Unknown provider outcome')
        if problem == 'http_error': return response(prepared, payload=payload, status=429)
        from test_abacus_router_adapter import prepare, wire
        return response(prepared, payload=payload, request=wire(prepare()))
    monkeypatch.setattr(transport, 'send_once', send)
    with pytest.raises(SpendBlocked, match='response_unverified'): run(case)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 1


@pytest.mark.parametrize('changed', ['channel', 'binding', 'target_removed', 'target_pending',
                                      'partial_changed', 'target_changed'])
def test_unlinked_completed_receipt_cannot_bypass_current_authority_or_integrity(case, monkeypatch, changed):
    def stop_before_link(*_): raise ConnectionError('Lost ACK before link')
    monkeypatch.setattr(completion, 'link_completed_review', stop_before_link)
    with pytest.raises(SpendBlocked, match='included_visual_completion_unverified'): run(case)
    client = case.ledger.client
    if changed == 'channel':
        client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL,
            'connection_id': 'other-current-connection', 'requires_reconnect': False}))
    elif changed == 'binding':
        from app.services.production_spend import LEDGER_KEY
        client.hdel(LEDGER_KEY, 'binding:' + CONTEXT['lineage_id'])
    elif changed == 'partial_changed':
        key = next(client.scan_iter(match=included.PREFIX + 'failure:*'))
        failure = json.loads(client.get(key))
        payload = envelope()
        partial = deepcopy(case.partial)
        partial['reviews'][0]['score'] = 99
        payload['choices'][0]['message']['content'] = json.dumps(partial)
        raw = json.dumps(payload).encode()
        import hashlib
        failure['encrypted_response'] = included._cipher().encrypt(raw).decode()
        failure['response_sha256'] = hashlib.sha256(raw).hexdigest()
        client.set(key, included._raw(failure))
    else:
        record = journal(case)
        key = next(key for key, row in record['requests'].items() if row['outcome'] is not None)
        if changed == 'target_removed': record['requests'].pop(key)
        elif changed == 'target_pending': record['requests'][key]['outcome'] = None
        else: record['requests'][key]['outcome']['evidence']['parsed_result_sha256'] = '0' * 64
        state = json.loads(client.get(included.STATE_KEY))
        client.set(included.JOURNAL_KEY, included._raw(record))
        client.set(included.ANCHOR_KEY, included._sha({'state': state, 'journal': record}))
    before = dump(client)
    with pytest.raises(SpendBlocked): run(case)
    assert len(case.calls) == 2 and dump(client) == before


def test_real_watch_conflict_retries_only_local_settlement_without_schema_exception_context(case, monkeypatch):
    import sys
    original = included.IncludedRouterLedger._ack
    injected = []
    def conflict(pipe, expected):
        for command, _ in pipe.command_stack:
            if command[0] != 'SET' or command[1] != included.JOURNAL_KEY:
                continue
            rows = json.loads(command[2])['requests'].values()
            if (not injected and any(row['outcome'] is not None for row in rows)
                    and not any('completion' in row for row in rows)):
                assert sys.exception() is None
                # A separate real Redis write invalidates WATCH, even with
                # identical bytes. This is not a simulated network exception.
                case.ledger.client.set(included.JOURNAL_KEY, case.ledger.client.get(included.JOURNAL_KEY))
                injected.append(True)
        return original(pipe, expected)
    monkeypatch.setattr(included.IncludedRouterLedger, '_ack', staticmethod(conflict))
    assert run(case) == case.complete
    assert injected == [True] and len(case.calls) == 2


def test_partial_existing_flag_cannot_be_changed_by_compact_completion(case):
    case.partial['reviews'][0]['receiving_interface_visible'] = False
    case.fields['reviews'][0]['receiving_interface_visible'] = True
    with pytest.raises(SpendBlocked, match='completion_unverified'): run(case)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'): run(case)
    assert len(case.calls) == 2


def test_prior_full_completion_receipt_remains_reusable_without_a_new_request(case, monkeypatch):
    captured = []
    real = completion.complete_once
    def stop(original):
        captured.append(original)
        raise RuntimeError('Interrupted before completion')
    monkeypatch.setattr(completion, 'complete_once', stop)
    with pytest.raises(RuntimeError): run(case)
    original = captured[0]
    legacy = completion.completion_request(original.prepared, original.data, legacy=True)
    case.values[1] = case.complete
    from app.services.abacus_router_adapter import observe_router_response
    assert included._generate(legacy, 'visual_review', observe_router_response) == case.complete
    monkeypatch.setattr(completion, 'complete_once', real)
    assert run(case) == run(case) == case.complete
    assert len(case.calls) == 2
    row = next(row for row in journal(case)['requests'].values() if 'completion' in row)
    assert 'format' not in row['completion']
