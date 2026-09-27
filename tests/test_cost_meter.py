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


def _record_for(task_id, entry):
    token = runtime._TASK_ID.set(task_id)
    try:
        cost_meter.record(entry, now=NOW)
    finally:
        runtime._TASK_ID.reset(token)


def test_openai_uses_reported_tokens_and_cached_input():
    response = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=10_000, output_tokens=2_000,
        input_tokens_details=SimpleNamespace(cached_tokens=4_000)), output=[])
    entry = cost_meter.estimate_openai_response({'model': 'gpt-6-astra'}, response)
    # 6k fresh * $10/M + 4k cached * $1/M + 2k out * $50/M
    assert entry['priced'] is True
    assert entry['usd'] == pytest.approx(0.06 + 0.004 + 0.1)


def test_openai_web_search_calls_are_added():
    response = SimpleNamespace(usage={'input_tokens': 0, 'output_tokens': 0},
                               output=[{'type': 'web_search_call'}, {'type': 'message'}])
    entry = cost_meter.estimate_openai_response({'model': 'gpt-4.1-mini'}, response)
    assert entry['usd'] == pytest.approx(0.01)


def test_unknown_model_is_visible_not_free():
    entry = cost_meter.estimate_openai_response({'model': 'mystery'}, SimpleNamespace(usage=None))
    assert entry['priced'] is False and entry['usd'] == 0.0


def test_runway_and_fal_seconds():
    assert cost_meter.estimate_runway({'model': 'gen4.5', 'duration': 5})['usd'] == pytest.approx(0.6)
    fal = cost_meter.estimate_http('https://queue.fal.run/fal-ai/veo3.1/lite',
                                   {'json': {'duration': '8s'}}, None)
    assert fal['usd'] == pytest.approx(0.4)


def test_voice_providers_price_characters():
    text = 'a' * 1000
    eleven = cost_meter.estimate_http('https://api.elevenlabs.io/v1/text-to-speech/voice',
                                      {'json': {'text': text, 'model_id': 'eleven_v3'}}, None)
    assert eleven['usd'] == pytest.approx(0.18)
    fal = cost_meter.estimate_http('https://queue.fal.run/fal-ai/elevenlabs/tts/turbo-v2.5',
                                   {'json': {'text': text, 'voice': 'Adam'}}, None)
    assert fal['usd'] == pytest.approx(0.05)
    kie = cost_meter.estimate_http('https://api.kie.ai/api/v1/jobs/createTask',
                                   {'json': {'model': 'elevenlabs/text-to-speech-turbo-2-5',
                                             'input': {'text': text}}}, None)
    assert kie['priced'] is True and kie['usd'] == pytest.approx(0.03)
    assert '6.0 kredi' in kie['units']


def test_price_override(monkeypatch):
    monkeypatch.setattr(cost_meter, '_prices', lambda: {'elevenlabs': {'*': {'character': 0.0003}}})
    entry = cost_meter.estimate_http('https://api.elevenlabs.io/v1/text-to-speech/v',
                                     {'json': {'text': 'a' * 1000}}, None)
    assert entry['usd'] == pytest.approx(0.3)


def test_gemini_text_reads_usage_metadata():
    response = httpx.Response(200, json={'usageMetadata': {
        'promptTokenCount': 1_000_000, 'candidatesTokenCount': 100_000, 'thoughtsTokenCount': 100_000}})
    entry = cost_meter.estimate_http(
        'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent',
        {'json': {}}, response)
    assert entry['usd'] == pytest.approx(0.75 + 0.75)


def test_abacus_is_counted_as_subscription_not_dollars():
    response = httpx.Response(200, json={'usage': {'input_tokens': 100, 'output_tokens': 50}})
    entry = cost_meter.estimate_http('https://routellm.abacus.ai/v1/messages',
                                     {'json': {'model': 'claude-haiku-4-5-20251001'}}, response)
    assert entry['subscription'] is True and entry['priced'] is True and entry['usd'] == 0.0


def test_unrelated_url_is_ignored():
    assert cost_meter.estimate_http('https://oauth2.googleapis.com/token', {}, None) is None


def test_record_and_summary_group_by_day_provider_and_job(client):
    _record_for('job-1', {'provider': 'runway', 'model': 'gen4.5', 'usd': 0.6, 'priced': True})
    _record_for('job-1', {'provider': 'openai', 'model': 'x', 'usd': 0.0, 'priced': False})
    data = cost_meter.summary(now=NOW, jobs=[], videos={})
    assert data['today']['total'] == pytest.approx(0.6)
    assert data['today']['calls'] == 2
    assert data['month']['unpriced_calls'] == 1
    assert data['month']['providers'] == {'runway': 0.6, 'openai': 0.0}
    assert data['videos'][0]['root_id'] == 'job-1' and data['videos'][0]['total'] == pytest.approx(0.6)
    assert data['recent'][0]['provider'] == 'openai'


def test_video_family_adds_plan_render_and_repair_and_joins_views(client):
    jobs = [
        {'task_id': 'plan', 'parent_id': None, 'state': 'SUCCESS', 'spec': {'title': 'Levi'}},
        {'task_id': 'render', 'parent_id': 'plan', 'state': 'SUCCESS'},
        {'task_id': 'repair', 'parent_id': 'render', 'state': 'SUCCESS'},
        {'task_id': 'lost', 'parent_id': None, 'state': 'FAILURE', 'spec': {'topic': 'IKEA'}},
    ]
    _record_for('plan', {'provider': 'openai', 'usd': 0.5, 'priced': True})
    _record_for('render', {'provider': 'runway', 'usd': 1.2, 'priced': True})
    _record_for('repair', {'provider': 'kie', 'usd': 0.3, 'priced': True})
    _record_for('lost', {'provider': 'runway', 'usd': 0.6, 'priced': True})
    videos = {'render': {'view_count': 1000, 'privacy_status': 'public', 'title': 'Levi Short'}}
    data = cost_meter.summary(now=NOW, jobs=jobs, videos=videos)
    rows = {row['root_id']: row for row in data['videos']}
    assert rows['plan']['total'] == pytest.approx(2.0)
    assert rows['plan']['status'] == 'published' and rows['plan']['views'] == 1000
    assert rows['plan']['usd_per_1000_views'] == pytest.approx(2.0)
    assert rows['plan']['title'] == 'Levi Short'
    assert rows['lost']['status'] == 'failed' and rows['lost']['title'] == 'IKEA'
    assert data['per_published_video'] == pytest.approx(2.0)
    assert data['usd_per_1000_views'] == pytest.approx(2.0)
    assert data['wasted_total'] == pytest.approx(0.6) and data['wasted_count'] == 1


def test_finished_private_video_is_not_counted_as_waste(client):
    jobs = [{'task_id': 'done', 'parent_id': None, 'state': 'SUCCESS'}]
    _record_for('done', {'provider': 'runway', 'usd': 0.6, 'priced': True})
    data = cost_meter.summary(now=NOW, jobs=jobs, videos={})
    assert data['videos'][0]['status'] == 'unpublished'
    assert data['wasted_total'] == 0


def test_record_never_raises_when_redis_is_down(monkeypatch):
    def broken():
        raise ConnectionError('down')
    monkeypatch.setattr(cost_meter, '_client', broken)
    cost_meter.record({'provider': 'runway', 'usd': 1.0, 'priced': True})
    cost_meter.observe_http('https://api.elevenlabs.io/v1/text-to-speech/x', {'json': {'text': 'hi'}}, None)
    cost_meter.observe_openai({'model': 'gpt-5'}, object())
    cost_meter.observe_runway(None)


def test_paid_post_records_after_the_request_and_returns_same_response(client, monkeypatch):
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_elevenlabs_native_credits', False, raising=False)
    sent = httpx.Response(200, content=b'audio')

    def sender(url, **kwargs):
        return sent

    result = runtime.paid_post(sender, 'https://api.elevenlabs.io/v1/text-to-speech/v',
                               json={'text': 'a' * 100, 'model_id': 'm'})
    assert result is sent
    data = cost_meter.summary(jobs=[], videos={})
    assert data['today']['providers'] == {'elevenlabs': pytest.approx(0.018)}


def test_paid_post_failure_is_not_recorded(client, monkeypatch):
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_elevenlabs_native_credits', False, raising=False)

    def sender(url, **kwargs):
        raise httpx.ConnectError('offline')

    with pytest.raises(httpx.ConnectError):
        runtime.paid_post(sender, 'https://api.elevenlabs.io/v1/text-to-speech/v', json={'text': 'x'})
    assert client.llen(cost_meter.EVENTS_KEY) == 0


def test_cost_page_renders(client):
    from app import cost_routes
    jobs = [{'task_id': 'job-9', 'parent_id': None, 'state': 'SUCCESS', 'spec': {'title': 'Test <b>'}}]
    _record_for('job-9', {'provider': 'runway', 'model': 'gen4.5', 'operation': 'text_to_video',
                          'usd': 0.6, 'priced': True, 'units': '5 sn video'})
    html = cost_routes.render(cost_meter.summary(now=NOW, jobs=jobs, videos={})).body.decode()
    assert 'Maliyet' in html and '$0.60' in html and 'AI video klibi' in html
    assert 'Test &lt;b&gt;' in html and 'Hazır, yayında değil' in html
