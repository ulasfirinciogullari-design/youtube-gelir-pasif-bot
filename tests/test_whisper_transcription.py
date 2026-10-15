"""Bounded audio, real offline funding/replay fences, and zero provider traffic."""
import ast
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import fakeredis
import httpx
import pytest

from app.services import production_spend_runtime as runtime
from app.services import whisper_transcription as whisper
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy


ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
KEY = 'offline-whisper-key'
NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)


def _wav(samples=48_000, *, amplitude=0):
    raw = BytesIO()
    with wave.open(raw, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(struct.pack('<h', amplitude) * samples)
    return raw.getvalue()


@lru_cache(maxsize=4)
def _mp3(duration=1):
    return subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        f'sine=frequency=440:sample_rate=48000:duration={duration}',
        '-c:a', 'libmp3lame', '-b:a', '128k', '-threads', '1', '-write_xing', '0',
        '-f', 'mp3', 'pipe:1',
    ], capture_output=True, timeout=10, check=True).stdout


def _payload(**patch):
    return {'task': 'transcribe', 'language': 'english', 'duration': 1.0,
            'text': 'Hello there.', 'words': [
                {'word': 'Hello', 'start': 0.0, 'end': 0.4},
                {'word': 'there.', 'start': 0.4, 'end': 0.9},
            ], 'usage': {'type': 'duration', 'seconds': 1}, **patch}


@pytest.fixture
def case(monkeypatch, tmp_path, request):
    client = fakeredis.FakeRedis(decode_responses=True)
    ledger = SpendLedger(client, SpendPolicy(*([10_000_000] * 6)), clock=lambda: NOW)
    ledger.initialize()
    config = SimpleNamespace(studio_spend_enforcement=True)
    monkeypatch.setattr(runtime, 'settings', config)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda: ledger)

    class Clock(datetime):
        @classmethod
        def now(cls, _):
            return cls(2026, 9, 9, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(whisper, 'datetime', Clock)
    mode = getattr(request, 'param', 'cash_only')
    funding = ({'covered_list_allowance_micro': 6000,
                'coverage_basis': 'verified_route_list_cost_usd', 'no_auto_overage': True}
               if mode == 'covered_only' else
               {'cash_factor_numerator': 11, 'cash_factor_denominator': 10,
                'cash_bound_verified': True})
    ledger.initialize_funding({
        'version': 1, 'currency': 'USD', 'month': '2026-09',
        'valid_from': '2026-09-09T00:00:00Z', 'valid_until': '2026-10-01T00:00:00Z',
        'cash_cap_micro': 10_000_000, 'opening_cash_micro': 0,
        'reconciliation_sha256': 'a' * 64,
        'accounts': [{
            'provider': 'openai', 'account_sha256': 'b' * 64,
            'credential_sha256': hashlib.sha256(('openai\0' + KEY).encode()).hexdigest(),
            'evidence_sha256': 'c' * 64, 'valid_until': '2026-10-01T00:00:00Z',
            'mode': mode, 'funding': funding,
            'routes': [{'route': whisper.WHISPER_ROUTE, 'model': 'whisper-1',
                        'price_revision': whisper.WHISPER_PRICE_REVISION}],
        }],
    })
    client.sadd(runtime._CHANNEL_INDEX, CHANNEL)
    client.set(runtime._CHANNEL_PREFIX + CHANNEL, json.dumps({
        'id': CHANNEL, 'connection_id': 'connection_AAAAA',
    }))
    for task, parent in ((ROOT, None), (CHILD, ROOT)):
        client.set(runtime._JOB_PREFIX + task, json.dumps({
            'task_id': task, 'parent_id': parent, 'kind': 'render',
            'spec': {'production_channel_id': CHANNEL,
                     'production_connection_id': 'connection_AAAAA', 'duration_minutes': 0.5},
        }))
    path = tmp_path / 'private-narration.wav'
    path.write_bytes(_wav())
    sender = Mock(return_value=nullcontext(httpx.Response(200, json=_payload())))
    monkeypatch.setattr(whisper.httpx, 'stream', sender)
    token = runtime._TASK_ID.set(ROOT)
    try:
        yield SimpleNamespace(client=client, ledger=ledger, path=path, sender=sender, config=config)
    finally:
        runtime._TASK_ID.reset(token)


def _run(case, **kwargs):
    return whisper.transcribe_whisper_bounded(case.path, **{'api_key': KEY, 'language': 'en', **kwargs})


def _receipts(case):
    return [json.loads(value) for name, value in case.client.hgetall(LEDGER_KEY).items()
            if name.startswith('request:')]


@pytest.mark.parametrize('extension', ['wav', 'mp3'])
def test_actual_audio_and_fixed_fields_are_reserved_before_one_send(case, extension):
    raw = _wav() if extension == 'wav' else _mp3()
    case.path = case.path.with_suffix('.' + extension)
    case.path.write_bytes(raw)
    mime = 'audio/wav' if extension == 'wav' else 'audio/mpeg'
    def send(method, url, **kwargs):
        assert (method, url) == ('POST', whisper.WHISPER_ROUTE)
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        assert kwargs['headers'] == {'Authorization': 'Bearer ' + KEY, 'Accept': 'application/json'}
        assert kwargs['data'] == {'model': 'whisper-1', 'language': 'en',
                                  'response_format': 'verbose_json',
                                  'timestamp_granularities[]': 'word', 'temperature': '0'}
        assert kwargs['files'] == {'file': ('narration.' + extension, raw, mime)}
        assert case.ledger.snapshot()['period']['used_micro'] == 6000
        assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 6600
        return nullcontext(httpx.Response(200, json=_payload(), headers={
            'Set-Cookie': 'private-cookie', 'Location': 'https://private.invalid/' + KEY,
        }))
    case.sender.side_effect = send
    result = _run(case)
    assert result.status_code == 200 and result.json() == _payload()
    assert 'set-cookie' not in result.headers and 'location' not in result.headers
    with pytest.raises(RuntimeError):
        _ = result.request  # Response carries no outbound URL or credentials.
    assert case.sender.call_count == 1
    receipt = _receipts(case)[0]
    assert receipt['quote'] == {'provider': 'openai', 'model': 'whisper-1',
                               'maximum_micro': 6000, 'price_revision': whisper.WHISPER_PRICE_REVISION}
    assert receipt['funding']['cash_micro'] == 6600
    saved = json.dumps(case.client.hgetall(LEDGER_KEY))
    assert KEY not in saved and case.path.name not in saved and 'Hello there.' not in saved


def test_snapshot_sends_original_bytes_after_source_file_changes(case):
    raw = case.path.read_bytes()
    def send(_method, _url, **kwargs):
        case.path.write_bytes(_wav(amplitude=1000))
        assert kwargs['files']['file'][1] == raw
        return nullcontext(httpx.Response(200, json=_payload()))
    case.sender.side_effect = send
    assert _run(case).status_code == 200
    assert case.path.read_bytes() != raw and case.sender.call_count == 1


def test_same_bytes_new_filename_or_child_task_cannot_create_another_charge(case):
    assert _run(case).status_code == 200
    copied = case.path.with_name('another-name.wav')
    copied.write_bytes(case.path.read_bytes())
    case.path = copied
    child_token = runtime._TASK_ID.set(CHILD)
    try:
        with pytest.raises(SpendBlocked, match='already_reserved'):
            _run(case)
    finally:
        runtime._TASK_ID.reset(child_token)
    assert case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


def test_actual_bytes_and_language_are_both_in_replay_identity(case):
    assert _run(case).status_code == 200
    case.path.write_bytes(_wav(amplitude=1000))
    assert _run(case).status_code == 200
    assert _run(case, language='tr').status_code == 200
    assert case.sender.call_count == 3 and len(_receipts(case)) == 3


@pytest.mark.parametrize('case', ['covered_only'], indirect=True)
def test_covered_minute_is_shared_and_exhaustion_never_falls_back_to_cash(case):
    assert _run(case).status_code == 200
    case.path.write_bytes(_wav(amplitude=1000))
    with pytest.raises(SpendBlocked, match='covered_limit'):
        _run(case)
    assert case.sender.call_count == 1
    assert case.ledger.funding_snapshot()['cash_reserved_micro'] == 0


@pytest.mark.parametrize('patch', [{'api_key': ''}, {'api_key': KEY + '\n'},
                                  {'api_key': 'another-key'}, {'language': 'en-US'},
                                  {'language': 'fr'}, {'language': True}])
def test_wrong_key_or_unsupported_request_never_sends(case, patch):
    with pytest.raises(SpendBlocked):
        _run(case, **patch)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


@pytest.mark.parametrize('patch', [
    {'model': 'gpt-transcribe'}, {'response_format': 'json'}, {'temperature': '0.2'},
    {'timestamp_granularities[]': 'segment'}, {'prompt': 'unreviewed context'}, {'stream': 'true'},
])
def test_quote_independently_rejects_unreviewed_multipart_profile(case, monkeypatch, patch):
    monkeypatch.setattr(whisper, '_FIELDS', {**whisper._FIELDS, **patch})
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        _run(case)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


def test_enforcement_off_never_opens_audio_or_provider(case, monkeypatch):
    case.config.studio_spend_enforcement = False
    read = Mock()
    monkeypatch.setattr(whisper, '_read_audio', read)
    with pytest.raises(SpendBlocked, match='spend_not_enabled'):
        _run(case)
    read.assert_not_called()
    case.sender.assert_not_called()


@pytest.mark.parametrize('scenario', ['empty', 'oversize', 'missing', 'directory', 'symlink',
                                     'wrong_suffix', 'wav_trailing', 'wav_bad_data_length',
                                     'mp3_truncated', 'mp3_concatenated', 'mp3_corrupt_scan'])
def test_unbounded_or_invalid_audio_never_reserves(case, scenario):
    if scenario == 'empty':
        case.path.write_bytes(b'')
    elif scenario == 'oversize':
        with case.path.open('wb') as handle:
            handle.truncate(whisper.WHISPER_MAX_AUDIO_BYTES + 1)
    elif scenario == 'missing':
        case.path.unlink()
    elif scenario == 'directory':
        case.path = case.path.parent
    elif scenario == 'symlink':
        link = case.path.with_name('link.wav')
        link.symlink_to(case.path)
        case.path = link
    elif scenario == 'wrong_suffix':
        case.path = case.path.rename(case.path.with_suffix('.mp3'))
    elif scenario == 'wav_trailing':
        case.path.write_bytes(_wav() + _wav())
    elif scenario == 'wav_bad_data_length':
        raw = bytearray(_wav())
        raw[40:44] = struct.pack('<I', 2)
        case.path.write_bytes(raw)
    else:
        case.path = case.path.with_suffix('.mp3')
        raw = _mp3()
        if scenario == 'mp3_truncated':
            raw = raw[:-10]
        elif scenario == 'mp3_concatenated':
            raw += _mp3()
        else:
            raw = bytearray(raw)
            offset = (10 + sum(value << (7 * (3 - index)) for index, value in enumerate(raw[6:10]))
                      if raw.startswith(b'ID3') else 0)
            raw[offset + 4:offset + 36] = b'\xff' * 32
        case.path.write_bytes(raw)
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        _run(case)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


def test_exact_3008_second_wav_is_not_truncated(case):
    raw = _wav(whisper.WHISPER_MAX_SAMPLES)
    case.path.write_bytes(raw)
    assert _run(case).status_code == 200
    assert case.sender.call_args.kwargs['files']['file'][1] == raw
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


@pytest.mark.parametrize('kind', ['wav', 'mp3'])
def test_long_audio_is_blocked_using_actual_full_audio_not_only_headers(case, kind):
    case.path = case.path.with_suffix('.' + kind)
    case.path.write_bytes(_wav(whisper.WHISPER_MAX_SAMPLES + 1) if kind == 'wav' else _mp3(31.2))
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        _run(case)
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


def test_decoder_failure_is_not_treated_as_free_or_unknown_duration(case, monkeypatch):
    monkeypatch.setattr(whisper.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('ffmpeg', 10)))
    with pytest.raises(SpendBlocked, match='request_not_priced'):
        _run(case)
    case.sender.assert_not_called()


@pytest.mark.parametrize('scenario', ['timeout', 'http_error', 'redirect', 'invalid_json',
                                     'oversized_response'])
def test_transport_uncertainty_retains_reservation_and_never_retries(case, scenario):
    if scenario == 'timeout':
        case.sender.side_effect = httpx.ReadTimeout(KEY + ' private provider error')
    else:
        code = 503 if scenario == 'http_error' else 307 if scenario == 'redirect' else 200
        raw = b'x' * (2 * 1024 * 1024 + 1) if scenario == 'oversized_response' else b'{invalid'
        case.sender.return_value = nullcontext(httpx.Response(code, content=raw))
    with pytest.raises(whisper.WhisperTranscriptionError) as caught:
        _run(case)
    assert KEY not in str(caught.value) and caught.value.__cause__ is None
    with pytest.raises(SpendBlocked, match='already_reserved'):
        _run(case)
    assert case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


@pytest.mark.parametrize('patch', [
    {'text': None}, {'text': ['bad']}, {'words': None}, {'words': []},
    {'task': 'translate'}, {'duration': 61}, {'duration': True},
    {'language': None}, {'words': [{'word': 'hi', 'start': 0, 'end': 61}]},
    {'words': [{'word': 'hi', 'start': True, 'end': 1}]},
    {'words': [{'word': 'hi', 'start': 1, 'end': 0}]},
    {'usage': {'type': 'tokens', 'total_tokens': 123}},
    {'usage': {'type': 'duration', 'seconds': 61}},
])
def test_malformed_paid_transcript_is_terminal_without_zero_cost_or_fallback(case, patch):
    case.sender.return_value = nullcontext(httpx.Response(200, json=_payload(**patch)))
    with pytest.raises(whisper.WhisperTranscriptionError, match='whisper_response_invalid'):
        _run(case)
    assert case.sender.call_count == 1
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


@pytest.mark.parametrize('raw', [b'{"text":"a","text":"b"}', b'{"duration":1e999}', b'{"x":NaN}'])
def test_duplicate_or_nonfinite_json_is_terminal(case, raw):
    case.sender.return_value = nullcontext(httpx.Response(200, content=raw))
    with pytest.raises(whisper.WhisperTranscriptionError, match='whisper_response_invalid'):
        _run(case)
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


def test_missing_usage_does_not_refund_and_silence_remains_a_real_qa_input(case):
    payload = _payload(text='', words=[])
    del payload['usage']
    case.sender.return_value = nullcontext(httpx.Response(200, json=payload))
    assert _run(case).json() == payload
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


def test_budget_failure_keeps_original_exception_and_zero_send(case, monkeypatch):
    failure = SpendBlocked('spend_funding_cash_limit')
    monkeypatch.setattr(runtime, 'reserve_request', Mock(side_effect=failure))
    with pytest.raises(SpendBlocked) as caught:
        _run(case)
    assert caught.value is failure
    case.sender.assert_not_called()


def test_price_review_expires_before_audio_is_read(case, monkeypatch):
    class Expired(datetime):
        @classmethod
        def now(cls, _):
            return whisper._VALID_UNTIL
    monkeypatch.setattr(whisper, 'datetime', Expired)
    read = Mock()
    monkeypatch.setattr(whisper, '_read_audio', read)
    with pytest.raises(SpendBlocked, match='price_review_expired'):
        _run(case)
    read.assert_not_called()
    case.sender.assert_not_called()


def test_only_fixed_transport_follows_unconditional_reservation():
    tree = ast.parse(Path(whisper.__file__).read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    main = functions['transcribe_whisper_bounded']
    reservations = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'reserve_request']
    calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_post_bounded']
    assert len(reservations) == len(calls) == 1 and reservations[0].lineno < calls[0].lineno
    assert any(isinstance(node, ast.Expr) and node.value is reservations[0] for node in main.body)
    streams = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == 'stream']
    assert len(streams) == 1 and streams[0] in list(ast.walk(functions['_post_bounded']))
