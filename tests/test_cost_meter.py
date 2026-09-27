from datetime import datetime, timezone
from types import SimpleNamespace

import fakeredis
import httpx
import pytest

from app.services import cost_meter, production_spend_runtime as runtime

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cost_meter, '_client', lambda: fake)
    return fake


def test_openai_uses_reported_tokens_and_cached_input():
    response = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=10_000, output_tokens=2_000,
        input_tokens_details=SimpleNamespace(cached_tokens=4_000)), output=[])
    entry = cost_meter.estimate_openai_response({'model': 'gpt-6-astra'}, response)
    # 6k fresh * $10/M + 4k cached * $1/M + 2k out * $50/M
    assert entry['priced'] is True
    assert entry['usd'] == pytest.approx(0.06 + 0.004 + 0.1)


def test_unknown_model_is_visible_not_free():
    entry = cost_meter.estimate_openai_response({'model': 'mystery'}, SimpleNamespace(usage=None))
    assert entry['priced'] is False and entry['usd'] == 0.0


def test_runway_and_fal_seconds():
    assert cost_meter.estimate_runway({'model': 'gen4.5', 'duration': 5})['usd'] == pytest.approx(0.6)
    fal = cost_meter.estimate_http('https://queue.fal.run/fal-ai/veo3.1/lite',
                                   {'json': {'duration': '8s'}}, None)
    assert fal['usd'] == pytest.approx(0.4)


def test_elevenlabs_characters_and_price_override(monkeypatch):
    request = {'json': {'text': 'a' * 1000, 'model_id': 'eleven_v3'}}
    url = 'https://api.elevenlabs.io/v1/text-to-speech/voice'
    assert cost_meter.estimate_http(url, request, None)['usd'] == pytest.approx(0.18)
    monkeypatch.setattr(cost_meter, '_prices', lambda: {'elevenlabs': {'*': {'character': 0.0003}}})
    assert cost_meter.estimate_http(url, request, None)['usd'] == pytest.approx(0.3)


def test_gemini_text_reads_usage_metadata():
    response = httpx.Response(200, json={'usageMetadata': {
        'promptTokenCount': 1_000_000, 'candidatesTokenCount': 100_000, 'thoughtsTokenCount': 100_000}})
    entry = cost_meter.estimate_http(
        'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent',
        {'json': {}}, response)
    assert entry['usd'] == pytest.approx(0.75 + 0.75)


def test_unrelated_url_is_ignored():
    assert cost_meter.estimate_http('https://oauth2.googleapis.com/token', {}, None) is None


def test_record_and_summary_group_by_day_provider_and_job(client):
    token = runtime._TASK_ID.set('job-1')
    try:
        cost_meter.record({'provider': 'runway', 'model': 'gen4.5', 'usd': 0.6, 'priced': True}, now=NOW)
        cost_meter.record({'provider': 'openai', 'model': 'x', 'usd': 0.0, 'priced': False}, now=NOW)
    finally:
        runtime._TASK_ID.reset(token)
    data = cost_meter.summary(now=NOW)
    assert data['today']['total'] == pytest.approx(0.6)
    assert data['today']['calls'] == 2
    assert data['month']['unpriced_calls'] == 1
    assert data['month']['providers'] == {'runway': 0.6, 'openai': 0.0}
    assert data['jobs'][0]['task_id'] == 'job-1' and data['jobs'][0]['total'] == pytest.approx(0.6)
    assert data['recent'][0]['provider'] == 'openai'


def test_record_never_raises_when_redis_is_down(monkeypatch):
    def broken():
        raise ConnectionError('down')
    monkeypatch.setattr(cost_meter, '_client', broken)
    cost_meter.record({'provider': 'runway', 'usd': 1.0, 'priced': True})
    cost_meter.observe_http('https://api.elevenlabs.io/v1/text-to-speech/x', {'json': {'text': 'hi'}}, None)


def test_paid_post_records_after_the_request_and_returns_same_response(client, monkeypatch):
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_elevenlabs_native_credits', False, raising=False)
    sent = httpx.Response(200, content=b'audio')

    def sender(url, **kwargs):
        return sent

    result = runtime.paid_post(sender, 'https://api.elevenlabs.io/v1/text-to-speech/v',
                               json={'text': 'a' * 100, 'model_id': 'm'})
    assert result is sent
    assert cost_meter.summary()['today']['providers'] == {'elevenlabs': pytest.approx(0.018)}


def test_cost_page_renders(client, monkeypatch):
    from app import cost_routes
    cost_meter.record({'provider': 'runway', 'model': 'gen4.5', 'operation': 'text_to_video',
                       'usd': 0.6, 'priced': True, 'units': '5 sn video'}, now=NOW)
    html = cost_routes.render(cost_meter.summary(now=NOW)).body.decode()
    assert 'Maliyet' in html and '$0.60' in html and 'AI video klibi' in html
