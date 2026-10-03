from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_runway_seconds():
    assert cost_meter.estimate_runway({'model': 'gen4.5', 'duration': 5})['usd'] == pytest.approx(0.6)


@pytest.mark.parametrize('model,seconds', [
    ('veo_lite', 8), ('veo_lite', 4), ('seedance_pro', 5), ('seedance_pro', 10), ('seedance_fast', 5), ('seedance_fast', 9),
])
def test_fal_video_matches_the_reviewed_admission_quote(model, seconds, monkeypatch):
    from datetime import datetime as real_datetime
    from app.services import fal_video_catalog as catalog

    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 27, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(catalog, 'datetime', Frozen)
    name = catalog.MODELS[model]
    body = catalog.build_request(name, 'A calm harbor at dawn', seconds, '9:16')
    expected = catalog.quote_request(name, body).maximum_micro / 1_000_000
    entry = cost_meter.estimate_http('https://queue.fal.run/' + name, {'json': body}, None)
    assert entry['priced'] is True and entry['usd'] == pytest.approx(expected)


def test_gemini_veo_uses_the_requested_resolution():
    url = 'https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-lite-generate-preview:predictLongRunning'
    body = {'instances': [{'prompt': 'x'}], 'parameters': {'durationSeconds': 8, 'resolution': '1080p'}}
    assert cost_meter.estimate_http(url, {'json': body}, None)['usd'] == pytest.approx(0.64)
    body['parameters']['resolution'] = '720p'
    assert cost_meter.estimate_http(url, {'json': body}, None)['usd'] == pytest.approx(0.40)


def test_gemini_interactions_are_counted_unpriced():
    url = 'https://generativelanguage.googleapis.com/v1beta/interactions'
    image = cost_meter.estimate_http(url, {'json': {'model': 'img-model', 'response_format': {'type': 'image'}}}, None)
    assert image == {'provider': 'gemini', 'model': 'img-model', 'operation': 'image', 'usd': 0.0, 'priced': False}
    audio = cost_meter.estimate_http(url, {'json': {'model': 'm', 'generation_config': {'transcription_config': {}}}}, None)
    assert audio['operation'] == 'transcription' and audio['priced'] is False


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


@pytest.mark.parametrize('usage', [
    {'input_tokens': 100_000, 'output_tokens': 20_000, 'raw_input_tokens': 100_000},
    {'prompt_tokens': 100_000, 'completion_tokens': 20_000, 'total_tokens': 120_000},
])
def test_abacus_prices_native_and_openai_compatible_usage(usage):
    response = httpx.Response(200, json={'usage': usage})
    entry = cost_meter.estimate_http('https://routellm.abacus.ai/v1/messages',
                                     {'json': {'model': 'claude-haiku-4-5-20251001'}}, response)
    # 100k in * $1/M + 20k out * $5/M
    assert entry['priced'] is True and entry['usd'] == pytest.approx(0.2)


def test_abacus_without_usable_counters_is_unpriced():
    response = httpx.Response(200, json={'usage': {'total_tokens': 5}})
    entry = cost_meter.estimate_http('https://routellm.abacus.ai/v1/messages',
                                     {'json': {'model': 'claude-haiku-4-5-20251001'}}, response)
    assert entry['priced'] is False and entry['usd'] == 0.0


def test_unknown_paid_route_stays_visible():
    entry = cost_meter.estimate_http('https://api.example.com/v1/render', {'json': {'model': 'x'}}, None)
    assert entry == {'provider': 'api.example.com', 'model': 'x', 'operation': '/v1/render',
                     'usd': 0.0, 'priced': False}


def test_rejected_requests_are_not_spend(client):
    url = 'https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-lite-generate-preview:predictLongRunning'
    body = {'parameters': {'durationSeconds': 8, 'resolution': '720p'}}
    cost_meter.observe_http(url, {'json': body}, httpx.Response(429, json={'error': {}}))
    kie = {'json': {'model': 'elevenlabs/text-to-speech-turbo-2-5', 'input': {'text': 'merhaba'}}}
    cost_meter.observe_http('https://api.kie.ai/api/v1/jobs/createTask', kie, httpx.Response(200, json={'code': 402}))
    assert client.llen(cost_meter.EVENTS_KEY) == 0
    cost_meter.observe_http(url, {'json': body}, httpx.Response(200, json={'name': 'operations/1'}))
    cost_meter.observe_http('https://api.kie.ai/api/v1/jobs/createTask', kie,
                            httpx.Response(200, json={'code': 200, 'data': {'taskId': 'abcdefgh'}}))
    assert client.llen(cost_meter.EVENTS_KEY) == 2


def test_record_and_summary_group_by_day_provider_and_job(client):
    _record_for('job-1', {'provider': 'runway', 'model': 'gen4.5', 'usd': 0.6, 'priced': True})
    _record_for('job-1', {'provider': 'openai', 'model': 'x', 'usd': 0.0, 'priced': False})
    data = cost_meter.summary(now=NOW, jobs=[], videos={})
    assert data['projected_month'] > data['month']['total']
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


def test_task_unknown_to_studio_is_not_shown_as_running(client):
    _record_for('series-research', {'provider': 'openai', 'usd': 0.1, 'priced': True})
    data = cost_meter.summary(now=NOW, jobs=[], videos={})
    assert data['videos'][0]['status'] == 'unknown'
    assert data['wasted_total'] == 0


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


@pytest.fixture
def studio_client(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from app import cost_routes, studio
    monkeypatch.setattr(studio.settings, 'factory_api_token', 'studio-secret', raising=False)
    app = FastAPI()
    app.include_router(cost_routes.router)
    app.add_exception_handler(HTTPException, studio.studio_auth_exception)
    return TestClient(app, base_url='https://studio.example.test')


def test_signed_out_browser_goes_to_login_before_any_cost_read(studio_client, monkeypatch):
    monkeypatch.setattr(cost_meter, 'summary', Mock(side_effect=AssertionError('read before auth')))
    page = studio_client.get('/studio/costs', headers={'accept': 'text/html'}, follow_redirects=False)
    assert page.status_code == 303 and page.headers['location'] == '/studio/access'
    api = studio_client.get('/studio/api/costs', headers={'accept': 'text/html'}, follow_redirects=False)
    assert api.status_code == 401 and 'location' not in api.headers


def test_cost_page_says_so_when_records_cannot_be_read(studio_client, monkeypatch):
    def broken(**kwargs):
        raise ConnectionError('redis down')
    monkeypatch.setattr(cost_meter, 'summary', broken)
    studio_client.cookies.set('youtube_studio_token', 'studio-secret')
    page = studio_client.get('/studio/costs')
    assert page.status_code == 503 and 'okunamıyor' in page.text and '/studio/costs' in page.text
    api = studio_client.get('/studio/api/costs')
    assert api.status_code == 503 and api.json() == {'detail': 'Maliyet kayıtları şu an okunamıyor.'}


def test_daily_cap_blocks_new_paid_calls_once_reached(client, monkeypatch):
    from app.services.production_spend import SpendBlocked
    monkeypatch.setattr(runtime.settings, 'cost_daily_cap_usd', 1.0, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_elevenlabs_native_credits', False, raising=False)
    sender = Mock(return_value=httpx.Response(200, content=b'audio'))
    url = 'https://api.elevenlabs.io/v1/text-to-speech/v'
    cost_meter.record({'provider': 'runway', 'usd': 0.6, 'priced': True})
    runtime.paid_post(sender, url, json={'text': 'a', 'model_id': 'm'})
    cost_meter.record({'provider': 'runway', 'usd': 0.6, 'priced': True})
    with pytest.raises(SpendBlocked, match='cost_daily_cap_reached'):
        runtime.paid_post(sender, url, json={'text': 'a', 'model_id': 'm'})
    with pytest.raises(SpendBlocked, match='cost_daily_cap_reached'):
        runtime.paid_response(Mock(), model='gpt-5', input='x')
    with pytest.raises(SpendBlocked, match='cost_daily_cap_reached'):
        runtime.paid_runway_create(Mock(), model='gen4.5', duration=5)
    assert sender.call_count == 1


def test_daily_cap_is_off_at_zero_and_never_blocks_on_unreadable_records(monkeypatch):
    monkeypatch.setattr(runtime.settings, 'cost_daily_cap_usd', 0, raising=False)
    fake = fakeredis.FakeRedis(decode_responses=True)
    cost_meter.record({'provider': 'runway', 'usd': 50.0, 'priced': True}, client=fake)
    cost_meter.check_daily_cap(client=fake)

    def broken():
        raise ConnectionError('down')
    monkeypatch.setattr(runtime.settings, 'cost_daily_cap_usd', 1.0, raising=False)
    monkeypatch.setattr(cost_meter, '_client', broken)
    cost_meter.check_daily_cap()


def test_new_system_defaults_are_the_cheaper_models_and_a_cap():
    from app.config import Settings
    fields = Settings.model_fields
    assert fields['studio_fresh_plan_openai_model'].default == 'gpt-5'
    assert fields['studio_visual_qc_openai_model'].default == 'gpt-5'
    assert fields['cost_daily_cap_usd'].default == 8.0
    assert fields['cost_planning_task_cap_usd'].default == 2.5


def test_gpt5_web_searches_are_priced():
    response = SimpleNamespace(usage={'input_tokens': 0, 'output_tokens': 0},
                               output=[{'type': 'web_search_call'}, {'type': 'web_search_call'}])
    entry = cost_meter.estimate_openai_response({'model': 'gpt-5'}, response)
    assert entry['priced'] is True and entry['usd'] == pytest.approx(0.02)
    assert '2 web araması' in entry['units']


def test_flash_and_turbo_voices_cost_half_a_credit_per_character():
    text = 'a' * 1000
    for model in ('eleven_flash_v2_5', 'eleven_turbo_v2_5'):
        entry = cost_meter.estimate_http('https://api.elevenlabs.io/v1/text-to-speech/v/with-timestamps',
                                         {'json': {'text': text, 'model_id': model}}, None)
        assert entry['usd'] == pytest.approx(0.09)
    multilingual = cost_meter.estimate_http('https://api.elevenlabs.io/v1/text-to-speech/v',
                                            {'json': {'text': text, 'model_id': 'eleven_multilingual_v2'}}, None)
    assert multilingual['usd'] == pytest.approx(0.18)


def _json_response(payload):
    return httpx.Response(200, json=payload)


def test_whisper_is_priced_from_the_reported_audio_length():
    url = 'https://api.openai.com/v1/audio/transcriptions'
    request = {'data': {'model': 'whisper-1', 'response_format': 'verbose_json'}, 'files': {'file': object()}}
    entry = cost_meter.estimate_http(url, request, _json_response({'text': 'x', 'duration': 29.4}))
    # 30 billed seconds at $0.006 per minute.
    assert entry['priced'] is True and entry['usd'] == pytest.approx(0.003)
    assert entry['model'] == 'whisper-1' and entry['operation'] == 'transcription'
    usage = cost_meter.estimate_http(url, request, _json_response({'usage': {'type': 'duration', 'seconds': 60}}))
    assert usage['usd'] == pytest.approx(0.006)
    unknown = cost_meter.estimate_http(url, request, _json_response({'text': 'x'}))
    assert unknown['priced'] is False


def test_scribe_is_priced_from_the_last_word_end():
    url = 'https://api.elevenlabs.io/v1/speech-to-text'
    request = {'data': {'model_id': 'scribe_v2', 'language_code': 'tur'}, 'files': {'file': object()}}
    payload = {'text': 'a b', 'words': [{'text': 'a', 'end': 1.0}, {'text': 'b', 'end': 3599.2}]}
    entry = cost_meter.estimate_http(url, request, _json_response(payload))
    assert entry['priced'] is True and entry['usd'] == pytest.approx(0.22)
    assert cost_meter.estimate_http(url, request, _json_response({'text': ''}))['priced'] is False


def test_planning_task_cap_stops_a_looping_script_stage_only(client, monkeypatch):
    from app.services.production_spend import SpendBlocked
    from app.services.planning_model_routing import _FRESH_ROUTE
    monkeypatch.setattr(runtime.settings, 'cost_daily_cap_usd', 0, raising=False)
    monkeypatch.setattr(runtime.settings, 'cost_planning_task_cap_usd', 1.5, raising=False)
    monkeypatch.setattr(runtime.settings, 'studio_spend_enforcement', False, raising=False)
    sdk = Mock()
    sdk.responses.create.return_value = SimpleNamespace(usage=None, output=[])
    _record_for('task-a', {'provider': 'openai', 'usd': 1.6, 'priced': True})
    task = runtime._TASK_ID.set('task-a')
    try:
        runtime.paid_response(sdk, model='gpt-5', input='x')  # outside planning: never capped
        route = _FRESH_ROUTE.set(('openai', 'gpt-5'))
        try:
            with pytest.raises(SpendBlocked, match='cost_planning_task_cap_reached'):
                runtime.paid_response(sdk, model='gpt-5', input='x')
            with pytest.raises(SpendBlocked, match='cost_planning_task_cap_reached'):
                runtime.paid_post(Mock(), 'https://generativelanguage.googleapis.com/v1beta/models/m:generateContent',
                                  json={})
            other = runtime._TASK_ID.set('task-b')
            try:
                runtime.paid_response(sdk, model='gpt-5', input='x')  # another Short is unaffected
            finally:
                runtime._TASK_ID.reset(other)
            monkeypatch.setattr(runtime.settings, 'cost_planning_task_cap_usd', 0, raising=False)
            runtime.paid_response(sdk, model='gpt-5', input='x')
        finally:
            _FRESH_ROUTE.reset(route)
    finally:
        runtime._TASK_ID.reset(task)
    assert sdk.responses.create.call_count == 3


def test_planning_task_cap_never_blocks_on_unreadable_records(monkeypatch):
    from app.services.planning_model_routing import _FRESH_ROUTE

    def broken():
        raise ConnectionError('down')
    monkeypatch.setattr(runtime.settings, 'cost_planning_task_cap_usd', 0.01, raising=False)
    monkeypatch.setattr(cost_meter, '_client', broken)
    task = runtime._TASK_ID.set('task-a')
    route = _FRESH_ROUTE.set(('openai', 'gpt-5'))
    try:
        cost_meter.check_planning_task_cap()
    finally:
        _FRESH_ROUTE.reset(route)
        runtime._TASK_ID.reset(task)


def test_cost_page_shows_the_daily_limit_a_hold_and_the_price_list_deadline(client, monkeypatch):
    from app import cost_routes
    from app.config import settings
    from app.services import admission_hold, fal_video_catalog
    monkeypatch.setattr(settings, 'cost_daily_cap_usd', 8.0)
    monkeypatch.setattr(settings, 'studio_video_provider', 'fal')
    monkeypatch.setattr(admission_hold, 'hold_reason', lambda **_: 'daily_cap_near')
    state = cost_routes.guards(now=fal_video_catalog.VALID_UNTIL - timedelta(days=3))
    assert state['daily_cap_usd'] == 8.0 and state['video_prices_days_left'] == 3
    assert state['hold']['reason_code'] == 'daily_cap_near'
    html = cost_routes.render({**cost_meter.summary(now=NOW, jobs=[], videos={}), 'guards': state}).body.decode()
    assert 'günlük sınır $8.00' in html
    assert 'yeni video yarın başlayacak' in html
    assert 'Video fiyat listesi' in html and 'sona eriyor' in html
    far = cost_routes.guards(now=fal_video_catalog.VALID_UNTIL - timedelta(days=20))
    html = cost_routes.render({**cost_meter.summary(now=NOW, jobs=[], videos={}), 'guards': far}).body.decode()
    assert 'sona eriyor' not in html


def test_guard_state_never_breaks_the_page(monkeypatch):
    from app import cost_routes
    from app.services import admission_hold

    def broken(**_kwargs):
        raise RuntimeError('boom')
    monkeypatch.setattr(admission_hold, 'hold_reason', broken)
    assert 'hold' not in cost_routes.guards()


def test_cost_page_lists_the_settings_that_decide_cost(client, monkeypatch):
    from app import cost_routes
    from app.config import settings
    monkeypatch.setattr(settings, 'studio_fresh_plan_openai_model', 'gpt-5')
    monkeypatch.setattr(settings, 'studio_video_provider', 'fal')
    monkeypatch.setattr(settings, 'cost_daily_cap_usd', 8.0)
    monkeypatch.setattr(settings, 'cost_planning_task_cap_usd', 0)
    monkeypatch.setattr(settings, 'studio_shorts_policy_json', '')
    monkeypatch.setattr(settings, 'studio_default_shorts_per_day', 5, raising=False)
    rows = {row['label']: row['value'] for row in cost_routes.active_settings()}
    assert rows['Senaryo modeli'] == 'gpt-5' and rows['AI video servisi'] == 'Fal'
    assert rows['Günlük harcama sınırı'] == '$8.00' and rows['Bir Short için senaryo sınırı'] == 'kapalı'
    assert rows['Günlük video'] == '5 Short' and rows['Video üretilen kanal'].endswith(' kanal')
    html = cost_routes.render({**cost_meter.summary(now=NOW, jobs=[], videos={}),
                               'settings': cost_routes.active_settings()}).body.decode()
    assert 'Etkin ayarlar' in html and '5 Short' in html
    assert 'Etkin ayarlar' not in cost_routes.render(cost_meter.summary(now=NOW, jobs=[], videos={})).body.decode()


def test_settings_list_survives_a_broken_value(monkeypatch):
    from app import cost_routes
    from app.services import channel_ids
    monkeypatch.delattr(channel_ids, 'MANAGED')
    rows = {row['label']: row['value'] for row in cost_routes.active_settings()}
    assert rows['Video üretilen kanal'] == '—'
