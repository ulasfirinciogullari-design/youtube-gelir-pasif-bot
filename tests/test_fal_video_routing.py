"""Silent model selection and actual queue contracts, with no provider traffic."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import fal_video_catalog as catalog, production_spend_quotes as quotes, runway
from app.services.production_spend import SpendBlocked
from test_fal_video import _Client, _Response, _load_namespace, REQUEST_ID


@pytest.fixture(autouse=True)
def reviewed_date(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 23, tzinfo=timezone.utc)
    monkeypatch.setattr(catalog, 'datetime', Clock)


@pytest.mark.parametrize('seconds,alias', [(2, 'veo_lite'), (5, 'veo_lite'), (8, 'veo_lite'),
                                          (9, 'seedance_pro'), (10, 'seedance_pro')])
def test_auto_preserves_veo_and_covers_longer_shots(seconds, alias):
    assert catalog.select_model(seconds) == catalog.MODELS[alias]


@pytest.mark.parametrize('mode,key,expected', [
    ('auto', '', False), ('auto', 'configured', True), ('legacy', 'configured', False),
    ('fal', '', True),
])
def test_key_enables_auto_and_legacy_remains_explicit_rollback(mode, key, expected):
    assert catalog.primary_enabled(SimpleNamespace(studio_video_provider=mode, fal_key=key)) is expected


@pytest.mark.parametrize('alias,seconds,duration,cost', [
    ('veo_lite', 5, '6s', 180000), ('veo_lite', 7, '8s', 240000),
    ('seedance_pro', 5, '5', 130000), ('seedance_pro', 10, '10', 260000),
    ('seedance_fast', 5, '5', 110000),
])
def test_exact_silent_shape_reaches_real_pricing_boundary(alias, seconds, duration, cost):
    model = catalog.MODELS[alias]
    body = catalog.build_request(model, 'Approved shot', seconds, '9:16')
    assert body['duration'] == duration and body['resolution'] == '720p'
    assert body.get('generate_audio', False) is False
    provider, operation, quote = quotes.quote_http_request(catalog.ORIGIN + '/' + model, {'json': body})
    assert (provider, quote.model, quote.maximum_micro) == ('fal', model, cost)
    descriptor = quotes.describe_video_request(provider, operation, body, quote)
    assert descriptor['audio'] is False and descriptor['duration_seconds'] >= seconds


@pytest.mark.parametrize('change', [
    {'generate_audio': True}, {'generate_audio': 0}, {'auto_fix': True},
    {'auto_fix': 0}, {'num_frames': 9999}, {'resolution': '1080p'},
    {'duration': '9s'}, {'duration': 6}, {'aspect_ratio': 'auto'}, {'prompt': ''},
])
def test_unpriced_fields_cannot_reach_dispatch(change):
    model = catalog.MODELS['veo_lite']
    body = catalog.build_request(model, 'Approved shot', 5, '9:16')
    with pytest.raises(SpendBlocked):
        quotes.quote_http_request(catalog.ORIGIN + '/' + model, {'json': {**body, **change}})


def test_expired_price_blocks_and_too_short_veo_cannot_buy_a_clip(monkeypatch):
    with pytest.raises(SpendBlocked): catalog.select_model(9, 'veo_lite')
    with pytest.raises(SpendBlocked): catalog.select_model(5, 'unreviewed_model')
    monkeypatch.setattr(catalog, 'VALID_UNTIL', datetime(2026, 9, 23, tzinfo=timezone.utc))
    model = catalog.MODELS['veo_lite']
    with pytest.raises(SpendBlocked, match='price_review_expired'):
        catalog.quote_request(model, catalog.build_request(model, 'Approved shot', 5, '9:16'))


@pytest.mark.parametrize('alias', list(catalog.MODELS))
@pytest.mark.parametrize('application_queue', [False, True])
def test_reviewed_models_submit_once_and_poll_only_the_accepted_queue(alias, application_queue):
    model = catalog.MODELS[alias]
    root = '/'.join(model.split('/')[:2]) if application_queue else model
    queue = f'{catalog.ORIGIN}/{root}/requests/{REQUEST_ID}'
    client = _Client(_Response(payload={'request_id': REQUEST_ID,
        'status_url': queue + '/status', 'response_url': queue}), [
        _Response(payload={'status': 'COMPLETED', 'request_id': REQUEST_ID}),
        _Response(payload={'video': {'url': 'https://v3.fal.media/files/approved.mp4'}}),
    ])
    namespace = _load_namespace(client)
    result = namespace['generate_fal_video']('Approved shot', 5, model=model, aspect_ratio='9:16')
    assert result['provider'] == catalog.PROVIDERS[model]
    assert len(client.post_calls) == 1
    assert client.post_calls[0][0] == catalog.ORIGIN + '/' + model
    assert [url for url, _ in client.get_calls] == [queue + '/status', queue]


def test_new_provider_is_selected_before_runway_key_requirement(monkeypatch):
    settings = SimpleNamespace(studio_video_provider='fal', studio_fal_video_model='auto',
                               runwayml_api_secret='')
    monkeypatch.setattr(runway, 'settings', settings)
    sender = Mock(return_value={'provider': 'fal_veo_lite', 'url': 'test'})
    monkeypatch.setattr(runway, 'generate_fal_video', sender)
    assert runway.generate_scene('Approved shot', duration=5)['provider'] == 'fal_veo_lite'
    sender.assert_called_once_with('Approved shot', 5, aspect_ratio='16:9',
                                   model=catalog.MODELS['veo_lite'])


def test_primary_fal_failure_never_starts_another_paid_provider(monkeypatch):
    from app.services.fal_video import FalVideoQuotaError
    settings = SimpleNamespace(studio_video_provider='fal', studio_fal_video_model='auto')
    monkeypatch.setattr(runway, 'settings', settings)
    sender = Mock(side_effect=FalVideoQuotaError('Unavailable', safe_to_fallback=True))
    other = Mock(side_effect=AssertionError('No additional purchase'))
    monkeypatch.setattr(runway, 'generate_fal_video', sender)
    monkeypatch.setattr(runway, 'RunwayML', other)
    with pytest.raises(FalVideoQuotaError): runway.generate_scene('Approved shot', duration=5)
    other.assert_not_called()


def test_explicit_omni_preview_keeps_its_continuity_reference(monkeypatch):
    settings = SimpleNamespace(studio_video_provider='fal', studio_fal_video_model='auto')
    monkeypatch.setattr(runway, 'settings', settings)
    reference = object()
    omni = Mock(return_value={'provider': 'gemini_omni', 'url': 'test'})
    fal = Mock(side_effect=AssertionError('Do not discard the requested reference'))
    monkeypatch.setattr(runway, '_generate_gemini_omni_video', omni)
    monkeypatch.setattr(runway, 'generate_fal_video', fal)
    assert runway.generate_scene('Approved shot', duration=5, aspect_ratio='9:16',
        prefer_gemini_omni=True, continuity_reference_image=reference)['provider'] == 'gemini_omni'
    omni.assert_called_once_with('Approved shot', 5, continuity_reference_image=reference)
    fal.assert_not_called()


def test_saved_clip_provider_tags_are_supported_end_to_end():
    from app.services.generated_asset_checkpoint import PROVIDERS
    from app.services.selected_visual_checkpoint import _PROVIDERS
    from app.services.production_connection_continuity import _PROVIDERS as continuity
    from app.services.qa_workprint import _PROVIDERS as workprint
    for accepted in (PROVIDERS, _PROVIDERS, continuity, workprint):
        assert set(catalog.PROVIDERS.values()) <= accepted
