"""Native visual review, real offline budget/funding/usage, and fake transport."""
import ast
import base64
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import httpx
import pytest

from spending_test_support import test_funding_policy as _funding_policy
from app.services import abacus_generation as transport
from app.services import abacus_visual_generation as generation
from app.services import abacus_visual_spend_quotes as visual_quotes
from app.services import production_spend_runtime as runtime
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy


ROOT = '11111111-1111-4111-8111-111111111111'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
URL = 'https://routellm.abacus.ai/v1/messages'
MODEL = 'claude-haiku-4-5-20251001'
KEY = 'private-test-key'
SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)


@lru_cache(maxsize=3)
def _jpeg(color='red'):
    image = subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=64x114:d=0.04',
        '-frames:v', '1', '-threads', '1', '-c:v', 'mjpeg', '-f', 'image2pipe', 'pipe:1',
    ], capture_output=True, check=True, timeout=10).stdout
    return base64.b64encode(image).decode('ascii')


def _parts():
    return [
        {'type': 'text', 'text': 'Private frozen story. SCENE 0 CANDIDATE 0 MOMENT 0'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': _jpeg()}},
        {'type': 'text', 'text': 'SCENE 0 CANDIDATE 0 MOMENT 1'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': _jpeg('blue')}},
    ]


def _response(**patch):
    return {
        'id': 'msg_visual_123', 'type': 'message', 'role': 'assistant', 'model': MODEL,
        'content': [{'type': 'text', 'text': '{"ok": true}'}], 'stop_reason': 'end_turn',
        'usage': {'input_tokens': 170_000, 'output_tokens': 30}, **patch,
    }


def _run(parts=None, **kwargs):
    return generation.generate_abacus_visual_json(
        _parts() if parts is None else parts,
        **{'system_instruction': 'Full unchanged rubric: no identity, motion or artifact gate waived.',
           'api_key': KEY, 'json_schema': deepcopy(SCHEMA), **kwargs},
    )


@pytest.fixture
def case(monkeypatch, request):
    client = fakeredis.FakeRedis(decode_responses=True)
    ledger = SpendLedger(client, SpendPolicy(
        10_000_000, 4_000_000, 8_000_000, 800_000, 8_000_000, 500_000,
    ), clock=lambda: NOW)
    ledger.initialize()
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=True))
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)

    class Clock(datetime):
        @classmethod
        def now(cls, _):
            return cls(2026, 9, 9, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(visual_quotes, 'datetime', Clock)
    policy = _funding_policy(ledger)
    account = next(account for account in policy['accounts'] if account['provider'] == 'abacus')
    account['routes'] = [{'route': URL, 'model': MODEL,
                          'price_revision': 'abacus-vision-2026-09-09-v1'}]
    if getattr(request, 'param', None) == 'covered':
        account['mode'] = 'covered_only'
        account['funding'] = {'covered_list_allowance_micro': 240_960,
                              'coverage_basis': 'verified_route_list_cost_usd',
                              'no_auto_overage': True}
    ledger.initialize_funding(policy)
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA',
    }))
    client.set(runtime._JOB_PREFIX + ROOT, json.dumps({
        'task_id': ROOT, 'parent_id': None, 'kind': 'render',
        'spec': {'production_channel_id': CHANNEL,
                 'production_connection_id': 'connection_AAAAA', 'duration_minutes': 0.5},
    }))
    record = Mock(wraps=runtime.record_abacus_usage)
    monkeypatch.setattr(runtime, 'record_abacus_usage', record)
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=_response())))
    monkeypatch.setattr(transport.httpx, 'stream', sender)
    token = runtime._TASK_ID.set(ROOT)
    try:
        yield SimpleNamespace(client=client, ledger=ledger, record=record, sender=sender)
    finally:
        runtime._TASK_ID.reset(token)


def _observations(case):
    return [json.loads(value) for name, value in case.client.hgetall(LEDGER_KEY).items()
            if name.startswith('usage:')]


def test_full_context_cash_reserved_before_one_send_and_usage_durable(case):
    def send(method, url, **kwargs):
        assert (method, url) == ('POST', URL)
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        assert kwargs['headers'] == {'x-api-key': KEY, 'Content-Type': 'application/json',
                                     'anthropic-version': '2023-06-01'}
        assert case.ledger.snapshot()['period']['used_micro'] == 240_960
        assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 240_960
        body = kwargs['json']
        assert body['thinking'] == {'type': 'disabled'}
        assert body['max_tokens'] == 8192 and body['service_tier'] == 'standard_only'
        assert set(body) == {'model', 'system', 'messages', 'max_tokens', 'thinking',
                             'stream', 'service_tier'}
        return nullcontext(httpx.Response(200, json=_response()))
    case.sender.side_effect = send
    assert _run() == {'ok': True}
    assert case.sender.call_count == 1
    case.record.assert_called_once()
    record = _observations(case)[0]
    assert record['input_tokens'] == 170_000
    assert record['output_tokens'] == 30 and record['actual_micro'] == 170_150
    assert record['accounting'] == 'observed_list_cost_not_settlement'
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    saved = json.dumps(case.client.hgetall(LEDGER_KEY))
    assert KEY not in saved and 'Private frozen story' not in saved and _jpeg() not in saved


def test_all_interleaved_frames_story_and_rubric_are_preserved_and_detached(case):
    parts = _parts()
    original = deepcopy(parts)
    rubric = 'Full rubric. Türkçe metin. Continuity, temporal evidence and threshold 86.'
    schema = deepcopy(SCHEMA)
    def send(_method, _url, **kwargs):
        parts[0]['text'] = 'Changed caller story'
        parts[1]['source']['data'] = 'Changed caller image'
        schema['properties'].clear()
        assert kwargs['json']['messages'][0]['content'] == original
        assert kwargs['json']['system'].startswith(rubric + '\n\n')
        assert 'additionalProperties' in kwargs['json']['system']
        return nullcontext(httpx.Response(200, json=_response()))
    case.sender.side_effect = send
    assert _run(parts, system_instruction=rubric, json_schema=schema) == {'ok': True}
    assert case.record.call_args.args[0]['messages'][0]['content'] == original


def test_off_switch_prevents_quote_transport_and_usage(case, monkeypatch):
    monkeypatch.setattr(runtime, 'settings', SimpleNamespace(studio_spend_enforcement=False))
    with pytest.raises(SpendBlocked, match='spend_not_enabled'):
        _run()
    case.sender.assert_not_called()
    case.record.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('patch', [
    {'model': 'claude-sonnet-4-6'}, {'model': 'claude-haiku-4-5'},
    {'max_tokens': 0}, {'max_tokens': 8193}, {'max_tokens': True},
    {'system_instruction': ''}, {'system_instruction': '\ud800'},
    {'system_instruction': 'x' * 100_001}, {'api_key': ''}, {'api_key': KEY + '\n'},
    {'api_key': 'different-private-key'},
    {'json_schema': None}, {'json_schema': {'type': 'array'}},
    {'json_schema': {'type': 'object', '$ref': 'https://example.com'}},
    {'json_schema': {'type': 'object', 'properties': {'x': {'type': 'string', 'enum': ['\ud800']}}}},
])
def test_invalid_inputs_block_before_any_cash_or_send(case, patch):
    with pytest.raises((SpendBlocked, generation.AbacusConfigurationError)):
        _run(**patch)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('parts', [[], 'not blocks', [{'type': 'text', 'text': 'No images'}],
    [{'type': 'image', 'source': {'type': 'url', 'url': 'https://example.com/private.jpg'}}],
    [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'not-jpeg'}}],
    [{'type': 'tool_result', 'content': 'unpriced tool'}],
])
def test_unpriced_media_shapes_never_send(case, parts):
    with pytest.raises((SpendBlocked, generation.AbacusConfigurationError)):
        _run(parts)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('text', ['{invalid', '[]', 'null', '{"ok":true,"ok":false}',
                                 '{"ok":NaN}', '{"ok":1e999}', '{"ok":"true"}',
                                 '{"unexpected":true}'])
def test_invalid_paid_output_keeps_real_usage_and_reservation(case, text):
    case.sender.return_value = nullcontext(httpx.Response(200, json=_response(
        content=[{'type': 'text', 'text': text}],
    )))
    with pytest.raises(generation.AbacusGenerationError) as caught:
        _run()
    assert caught.value.usage.actual_micro == 170_150
    assert len(_observations(case)) == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    with pytest.raises(SpendBlocked, match='already_reserved'):
        _run()
    assert case.sender.call_count == 1


@pytest.mark.parametrize('patch', [
    {'usage': None}, {'usage': {}}, {'usage': {'input_tokens': 1}},
    {'usage': {'input_tokens': True, 'output_tokens': 1}},
    {'usage': {'input_tokens': -1, 'output_tokens': 1}},
    {'usage': {'input_tokens': 200_001, 'output_tokens': 1}},
    {'usage': {'input_tokens': 1, 'output_tokens': 8193}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'cache_creation_input_tokens': 1}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'cache_read_input_tokens': 1}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'service_tier': 'priority'}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'speed': 'fast'}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'inference_geo': 'us'}},
    {'usage': {'input_tokens': 1, 'output_tokens': 1, 'server_tool_use': {'search': 1}}},
    {'id': 'x' * 161}, {'id': 'secret\nline'}, {'model': 'claude-sonnet-4-6'},
    {'role': 'user'}, {'type': 'completion'},
])
def test_missing_or_unquoted_usage_never_becomes_zero_cost(case, patch):
    case.sender.return_value = nullcontext(httpx.Response(200, json=_response(**patch)))
    with pytest.raises(generation.AbacusGenerationError):
        _run()
    case.record.assert_not_called()
    assert _observations(case) == []
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960
    assert case.sender.call_count == 1


@pytest.mark.parametrize('patch', [
    {'stop_reason': 'max_tokens'}, {'stop_reason': 'refusal'}, {'stop_reason': None},
    {'stop_reason': 'tool_use'}, {'content': []},
    {'content': [{'type': 'thinking', 'thinking': 'hidden'}]},
    {'content': [{'type': 'text', 'text': '{}'}, {'type': 'text', 'text': '{}'}]},
])
def test_incomplete_or_unexpected_output_is_terminal_after_usage(case, patch):
    case.sender.return_value = nullcontext(httpx.Response(200, json=_response(**patch)))
    with pytest.raises(generation.AbacusGenerationError):
        _run()
    assert len(_observations(case)) == 1 and case.sender.call_count == 1


@pytest.mark.parametrize('status', [301, 400, 401, 429, 500])
def test_http_failure_never_retries_or_exposes_response(case, status):
    case.sender.return_value = nullcontext(httpx.Response(status, text=KEY + ' Private evidence'))
    with pytest.raises(generation.AbacusGenerationError) as caught:
        _run()
    assert str(caught.value) == 'abacus_request_rejected' and caught.value.__cause__ is None
    assert len(_observations(case)) == 0 and case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


def test_timeout_retains_full_reservation_and_replay_fence(case):
    case.sender.side_effect = httpx.ReadTimeout(KEY + ' Private evidence')
    with pytest.raises(generation.AbacusGenerationError, match='abacus_generation_failed'):
        _run()
    with pytest.raises(SpendBlocked, match='already_reserved'):
        _run()
    assert case.sender.call_count == 1 and _observations(case) == []
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


@pytest.mark.parametrize('content', [b'{invalid', b'x' * (2 * 1024 * 1024 + 1)],
                         ids=['invalid_json', 'oversized_response'])
def test_invalid_transport_json_and_bounded_response_are_terminal(case, content):
    case.sender.return_value = nullcontext(httpx.Response(200, content=content))
    with pytest.raises(generation.AbacusGenerationError):
        _run()
    assert _observations(case) == [] and case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


def test_usage_write_failure_preserves_original_terminal_error(case):
    failure = SpendBlocked('spend_usage_write_uncertain')
    case.record.side_effect = failure
    with pytest.raises(SpendBlocked) as caught:
        _run()
    assert caught.value is failure
    assert case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 240_960


def test_budget_block_preserves_original_error_without_transport(case, monkeypatch):
    failure = SpendBlocked('spend_lineage_limit')
    monkeypatch.setattr(runtime, 'reserve_request', Mock(side_effect=failure))
    with pytest.raises(SpendBlocked) as caught:
        _run()
    assert caught.value is failure
    case.sender.assert_not_called()


def test_actual_text_and_image_bytes_both_change_replay_identity(case):
    original = _parts()
    assert _run(original) == {'ok': True}
    changed_text = deepcopy(original)
    changed_text[0]['text'] += ' Additional frozen evidence.'
    assert _run(changed_text) == {'ok': True}
    changed_image = deepcopy(original)
    changed_image[1]['source']['data'] = _jpeg('green')
    assert _run(changed_image) == {'ok': True}
    assert case.sender.call_count == 3 and len(_observations(case)) == 3
    assert case.ledger.snapshot()['period']['used_micro'] == 3 * 240_960


def test_maximum_usage_matches_full_context_quote_without_million_unit_error(case):
    case.sender.return_value = nullcontext(httpx.Response(200, json=_response(
        usage={'input_tokens': 200_000, 'output_tokens': 8192},
    )))
    assert _run() == {'ok': True}
    assert _observations(case)[0]['actual_micro'] == 240_960


def test_requested_smaller_output_limit_is_independently_enforced(case):
    with pytest.raises(generation.AbacusGenerationError, match='usage_exceeded_quote'):
        _run(max_tokens=29)
    assert case.ledger.snapshot()['period']['used_micro'] == 200_145
    assert case.sender.call_count == 1 and _observations(case) == []


@pytest.mark.parametrize('case', ['covered'], indirect=True)
def test_covered_only_allowance_is_reserved_without_cash_fallback(case):
    assert _run() == {'ok': True}
    summary = case.ledger.funding_snapshot()
    assert summary['cash_reserved_micro'] == 0
    account = next(item for item in summary['providers'] if item['provider'] == 'abacus')
    assert account['covered_reserved_micro'] == 240_960
    changed = _parts()
    changed[0]['text'] += ' Another distinct request.'
    with pytest.raises(SpendBlocked, match='covered'):
        _run(changed)
    assert case.sender.call_count == 1
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 0


def test_provider_transport_reference_only_passes_through_spend_guard():
    tree = ast.parse(Path(generation.__file__).read_text())
    direct = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == '_post_bounded']
    assert direct == []
    guards = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == 'paid_post']
    assert len(guards) == 1
    assert isinstance(guards[0].args[0], ast.Name) and guards[0].args[0].id == '_post_bounded'
