"""Actual audio QC and task retry handlers with funded, offline Whisper sends."""
import ast
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from celery import Celery
import httpx
import pytest

from app.services import whisper_transcription as whisper
from app.services.production_spend import SpendBlocked
from test_audio_qc import audio_qc as qc
from test_whisper_transcription import case, _payload, _receipts, KEY
from test_production_spend_terminal import ERRORS


SOURCE = Path(__file__).parents[1] / 'app/tasks.py'
TREE = ast.parse(SOURCE.read_text())
# Bind the real empty production exception declaration in this module so Celery
# can pickle it without degrading the dynamic fixture class to RuntimeError.
exec(compile(ast.Module(body=[deepcopy(next(node for node in TREE.body
    if isinstance(node, ast.ClassDef) and node.name == 'FinalAudioQualityError'))], type_ignores=[]),
    str(SOURCE), 'exec'), globals())


@pytest.fixture
def audio(case, monkeypatch):
    monkeypatch.setattr(qc, 'settings', SimpleNamespace(
        openai_api_key=KEY, gemini_api_key='offline-gemini', elevenlabs_api_key='offline-elevenlabs'))
    helper = Mock(wraps=whisper.transcribe_whisper_bounded)
    monkeypatch.setattr(whisper, 'transcribe_whisper_bounded', helper)
    gemini, elevenlabs = Mock(), Mock()
    monkeypatch.setattr(qc, '_verify_with_gemini', gemini)
    monkeypatch.setattr(qc, '_verify_with_elevenlabs', elevenlabs)
    legacy = Mock(side_effect=AssertionError('Enforced audio must use its bounded helper'))
    monkeypatch.setattr(qc, 'paid_post', legacy)
    return SimpleNamespace(helper=helper, gemini=gemini, elevenlabs=elevenlabs, legacy=legacy)


def _wrapper():
    wanted = {'_verify_audio_narration_with_retry'}
    definitions = [deepcopy(node) for node in TREE.body if isinstance(node, ast.FunctionDef)
                   and node.name in wanted]
    constants = [deepcopy(node) for node in TREE.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in {
                     'AUDIO_QC_PROVIDER_ATTEMPTS', 'AUDIO_QC_PROVIDER_RETRY_DELAY_SECONDS'}
                         for target in node.targets)]
    ns = dict(ERRORS, Path=Path, json=json, AudioQCError=qc.AudioQCError,
              FinalAudioQualityError=FinalAudioQualityError,
              verify_audio_narration=Mock(wraps=qc.verify_audio_narration),
              audio_qc_provider_diagnostics=qc.audio_qc_provider_diagnostics,
              time=SimpleNamespace(sleep=Mock()))
    exec(compile(ast.Module(body=constants + definitions, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


def _run(case, **options):
    return qc.verify_audio_narration(case.path, 'Hello there.', language='en', **options)


def test_actual_qc_reserves_exact_audio_once_and_keeps_word_evidence(case, audio):
    raw = case.path.read_bytes()
    def send(method, url, **kwargs):
        assert method == 'POST' and url == whisper.WHISPER_ROUTE
        assert kwargs['files'] == {'file': ('narration.wav', raw, 'audio/wav')}
        assert case.ledger.snapshot()['period']['used_micro'] == 6000
        assert len(_receipts(case)) == 1
        return nullcontext(httpx.Response(200, json=_payload()))
    case.sender.side_effect = send
    sink = Mock()
    result = _run(case, provider_evidence_sink=sink)
    assert result['available'] is True and result['pass'] is True
    assert result['provider'] == 'openai' and result['transcript'] == 'Hello there.'
    assert len(result['word_timestamps']) == 2 and result['ending_word_time'] == 0.9
    audio.helper.assert_called_once_with(case.path, api_key=KEY, language='en')
    case.sender.assert_called_once()
    sink.assert_called_once()
    assert sink.call_args.kwargs['payload']['words'] == _payload()['words']
    audio.gemini.assert_not_called()
    audio.elevenlabs.assert_not_called()
    audio.legacy.assert_not_called()
    assert case.path.read_bytes() == raw


@pytest.mark.parametrize('problem', ['http_error', 'invalid_json', 'missing_words', 'zero_word_timing', 'transport'])
def test_reserved_protocol_failure_skips_provider_and_same_audio_retry(case, audio, problem):
    if problem == 'http_error': response = httpx.Response(401, text='private remote error')
    elif problem == 'invalid_json': response = httpx.Response(200, text='not-json')
    elif problem == 'missing_words': response = httpx.Response(200, json=_payload(words=[]))
    elif problem == 'zero_word_timing':
        response = httpx.Response(200, json=_payload(words=[
            {'word': 'Hello', 'start': 0, 'end': 0}, {'word': 'there.', 'start': 0.4, 'end': 0.9}]))
    else: response = None
    case.sender.side_effect = (httpx.ReadTimeout('private transport error') if response is None
                               else lambda *args, **kwargs: nullcontext(response))
    ns = _wrapper()
    raw = case.path.read_bytes()
    with pytest.raises(FinalAudioQualityError, match='without another paid request') as caught:
        ns['_verify_audio_narration_with_retry'](case.path, 'Hello there.', language='en')
    assert 'private' not in str(caught.value)
    ns['verify_audio_narration'].assert_called_once()
    ns['time'].sleep.assert_not_called()
    case.sender.assert_called_once()
    audio.helper.assert_called_once()
    audio.gemini.assert_not_called()
    audio.elevenlabs.assert_not_called()
    assert case.path.read_bytes() == raw
    assert case.ledger.snapshot()['period']['used_micro'] == 6000


def test_original_budget_reason_is_not_converted_or_retried(case, audio):
    error = SpendBlocked('spend_funding_covered_limit')
    audio.helper.side_effect = error
    ns = _wrapper()
    with pytest.raises(SpendBlocked) as caught:
        ns['_verify_audio_narration_with_retry'](case.path, 'Hello there.', language='en')
    assert caught.value is error
    audio.helper.assert_called_once()
    ns['time'].sleep.assert_not_called()
    case.sender.assert_not_called()
    audio.gemini.assert_not_called()
    audio.elevenlabs.assert_not_called()


def test_genuine_transcript_mismatch_remains_rejected_without_same_audio_retry(case, audio, monkeypatch):
    monkeypatch.setattr(qc, 'settings', SimpleNamespace(openai_api_key=KEY, gemini_api_key='', elevenlabs_api_key=''))
    ns = _wrapper()
    result = ns['_verify_audio_narration_with_retry'](case.path, 'The missing contract is different.', language='en')
    assert result['available'] is True and result['pass'] is False
    assert result['transcript'] == 'Hello there.' and result['mismatch_details']
    audio.helper.assert_called_once()
    ns['verify_audio_narration'].assert_called_once()
    ns['time'].sleep.assert_not_called()


def test_valid_mismatch_retains_existing_independent_verification_path(case, audio):
    rejected = {'available': True, 'pass': False, 'score': 0, 'provider': 'gemini'}
    audio.gemini.return_value = rejected
    audio.elevenlabs.return_value = {**rejected, 'provider': 'elevenlabs'}
    result = qc.verify_audio_narration(case.path, 'The narration has entirely different words.', language='en')
    assert result['pass'] is False
    audio.helper.assert_called_once()
    audio.gemini.assert_called_once()
    audio.elevenlabs.assert_called_once()
    case.sender.assert_called_once()


def test_existing_celery_declaration_does_not_resend_after_whisper_protocol_failure(case, audio):
    case.sender.return_value = nullcontext(httpx.Response(200, text='not-json'))
    ns = _wrapper()
    pipeline = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'run_video_pipeline')
    options = {item.arg: eval(compile(ast.Expression(item.value), str(SOURCE), 'eval'), ns)
               for item in pipeline.decorator_list[0].keywords}
    app = Celery('whisper-terminal-test', broker='memory://', backend='cache+memory://')
    @app.task(**options)
    def task(self):
        return ns['_verify_audio_narration_with_retry'](case.path, 'Hello there.', language='en')
    try:
        result = task.apply(throw=False)
        assert result.state == 'FAILURE'
        assert isinstance(result.result, FinalAudioQualityError)
        audio.helper.assert_called_once()
        case.sender.assert_called_once()
        ns['time'].sleep.assert_not_called()
    finally:
        app.close()


def test_enforcement_off_preserves_existing_multipart_post(case, audio, monkeypatch):
    case.config.studio_spend_enforcement = False
    raw = case.path.read_bytes()
    def legacy(sender, url, **kwargs):
        assert sender is qc.httpx.post and url == qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL
        filename, handle, mime = kwargs['files']['file']
        assert filename == case.path.name and handle.read() == raw and mime == 'audio/x-wav'
        assert kwargs['data']['model'] == 'whisper-1' and kwargs['data']['language'] == 'en'
        return httpx.Response(200, json=_payload())
    audio.legacy.side_effect = legacy
    assert _run(case)['pass'] is True
    audio.legacy.assert_called_once()
    audio.helper.assert_not_called()
    case.sender.assert_not_called()
    assert case.ledger.snapshot()['period']['used_micro'] == 0


def test_enforcement_off_retains_bounded_transient_same_audio_retry(case, audio):
    case.config.studio_spend_enforcement = False
    audio.legacy.side_effect = [httpx.ReadTimeout('transient'), httpx.Response(200, json=_payload())]
    # No alternate provider is configured, so the existing retry wrapper gets
    # the same legacy AudioQCError and retries the same immutable file once.
    qc.settings.gemini_api_key = qc.settings.elevenlabs_api_key = ''
    ns = _wrapper()
    assert ns['_verify_audio_narration_with_retry'](case.path, 'Hello there.', language='en')['pass'] is True
    assert audio.legacy.call_count == 2
    ns['time'].sleep.assert_called_once()
    audio.helper.assert_not_called()
    case.sender.assert_not_called()
