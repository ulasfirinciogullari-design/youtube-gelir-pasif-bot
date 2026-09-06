"""Cache-only channel zero/subtotal presentation; never invent an aggregate."""
from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from test_studio_workflow_presentation import ui


CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
OTHER = 'UCgvESYtYbn2w9R2ExBOF_cw'
VIDEO = 'hGsspOyWEIU'
SOURCE = '4ec1e176-5575-4e30-90ae-c926e3564a84'
STAMP = '2026-09-07T07:20:00+03:00'
FIELD = 'known_public_video_view_subtotal'
PENDING = 'Kanal toplamı güncelleniyor'
LABEL = 'Kayıtlı herkese açık videoların son ölçümü'


def _video(**changes):
    return {'channel_id': CHANNEL, 'video_id': VIDEO, 'view_count': 1191,
            'privacy_status': 'public', 'status': 'fresh', 'reason': None,
            'fetched_at': STAMP, **changes}


@pytest.fixture
def case(ui, monkeypatch):
    model = {'channels': [{'channel_id': CHANNEL, 'title': 'Capital Corrupt',
                           'view_count': 0, 'video_count': 1, 'subscriber_count': 0,
                           'status': 'fresh', 'fetched_at': STAMP}],
             'videos': {SOURCE: _video()}, 'updated_at': STAMP, 'refresh_after_seconds': 300}
    read = Mock(return_value=model)
    forbidden = Mock(side_effect=AssertionError('No provider, cache write, job write or implicit refresh'))
    metrics = ModuleType('app.services.youtube_metrics')
    metrics.get_dashboard_metrics = read
    metrics.refresh_dashboard_metrics = forbidden
    monkeypatch.setitem(sys.modules, metrics.__name__, metrics)
    production = ModuleType('app.services.channel_production')
    production.get_production_state = Mock(return_value={})
    monkeypatch.setitem(sys.modules, production.__name__, production)
    ui.ns['list_channel_profiles'] = lambda: []
    return SimpleNamespace(ui=ui, model=model, read=read, forbidden=forbidden, production=production)


def _project(case):
    return case.ui.ns['_dashboard_metrics']([])


def _html(case, model):
    return case.ui.ns['_channel_overview'](model['channels'])


@pytest.mark.parametrize('views,formatted', [(1191, '1.191'), (289, '289')])
def test_reported_zero_does_not_claim_actual_channel_total_when_known_public_views_exist(case, views, formatted):
    case.model['videos'][SOURCE]['view_count'] = views
    before = deepcopy(case.model)
    projected = _project(case)
    html = _html(case, projected)
    assert projected['channels'][0]['view_count'] == 0  # raw Google value is not rewritten
    assert projected['channels'][0][FIELD] == views
    assert PENDING in html and f'{LABEL}: {formatted} izlenme' in html
    assert '<b>0</b><span>Toplam izlenme</span>' not in html
    assert f'<b>{formatted}</b><span>Toplam izlenme</span>' not in html
    assert '<b>0</b><span>Abone</span>' in html
    assert '<b>1</b><span>Herkese açık video</span>' in html
    assert 'API gecikmesi' not in html and 'gerçek zamanlı' not in html
    assert case.model == before and projected['videos'] == before['videos']
    case.read.assert_called_once_with([])
    case.forbidden.assert_not_called(); case.ui.forbidden.assert_not_called()


@pytest.mark.parametrize('changes', [
    {'view_count': None}, {'view_count': True}, {'view_count': -1}, {'view_count': '1191'},
    {'view_count': 1.5}, {'view_count': 2**64}, {'view_count': float('nan')},
    {'privacy_status': 'private'}, {'privacy_status': 'unlisted'}, {'privacy_status': None},
    {'status': 'stale'}, {'status': 'unavailable'}, {'status': None},
    {'reason': 'permission'}, {'fetched_at': None}, {'fetched_at': 'bad'},
    {'fetched_at': '2026-09-07T07:20:00'}, {'channel_id': OTHER},
    {'channel_id': '<script>'}, {'video_id': 'not-an-id'},
])
def test_invalid_private_stale_or_wrong_channel_video_never_contributes(case, changes):
    case.model['videos'][SOURCE].update(changes)
    projected = _project(case)
    assert FIELD not in projected['channels'][0]
    assert PENDING not in _html(case, projected) and LABEL not in _html(case, projected)
    case.forbidden.assert_not_called()


@pytest.mark.parametrize('value', [None, True, -1, '0', 0.0])
def test_unknown_channel_counter_is_not_replaced_by_known_video_subtotal(case, value):
    case.model['channels'][0]['view_count'] = value
    projected = _project(case)
    html = _html(case, projected)
    assert FIELD not in projected['channels'][0] and PENDING not in html
    assert '<b class="waiting">Veri bekleniyor</b><span>Toplam izlenme</span>' in html


@pytest.mark.parametrize('value', [1, 1000, 2000])
def test_existing_positive_aggregate_is_unchanged_even_if_lower_than_video_subtotal(case, value):
    case.model['channels'][0].update(view_count=value, known_public_video_view_subtotal=999999)
    projected = _project(case)
    assert projected['channels'][0]['view_count'] == value and FIELD not in projected['channels'][0]
    assert PENDING not in _html(case, projected) and LABEL not in _html(case, projected)


@pytest.mark.parametrize('empty', [False, True])
def test_actual_zero_without_positive_public_observation_remains_zero(case, empty):
    if empty:
        case.model['videos'] = {}
    else:
        case.model['videos'][SOURCE]['view_count'] = 0
    projected = _project(case)
    assert '<b>0</b><span>Toplam izlenme</span>' in _html(case, projected)
    assert FIELD not in projected['channels'][0]


def test_duplicate_source_and_publisher_rows_count_one_exact_video(case):
    case.model['videos']['second-task'] = deepcopy(case.model['videos'][SOURCE])
    case.model['videos']['third-task'] = deepcopy(case.model['videos'][SOURCE])
    projected = _project(case)
    assert projected['channels'][0][FIELD] == 1191


@pytest.mark.parametrize('change', [{'view_count': 289}, {'privacy_status': 'private'}, {'status': 'stale'}])
def test_conflicting_duplicate_observations_are_excluded_not_optimistically_selected(case, change):
    case.model['videos']['second-task'] = _video(**change)
    case.model['videos']['third-task'] = _video()
    assert FIELD not in _project(case)['channels'][0]


def test_subtotals_are_per_channel_and_are_not_a_cross_channel_aggregate(case):
    case.model['channels'].append({**case.model['channels'][0], 'channel_id': OTHER, 'title': 'Margin Verdict'})
    case.model['videos']['margin-task'] = _video(channel_id=OTHER, video_id='SgsK7rcVhQ8', view_count=289)
    case.model['videos']['capital-second'] = _video(video_id='AbCdEfGhI_1', view_count=9)
    projected = _project(case)
    assert {row['channel_id']: row[FIELD] for row in projected['channels']} == {CHANNEL: 1200, OTHER: 289}


def test_subtotal_overflow_is_not_reported_as_an_aggregate(case):
    case.model['videos'][SOURCE]['view_count'] = 2**64 - 1
    case.model['videos']['second-task'] = _video(video_id='AbCdEfGhI_1', view_count=1)
    assert FIELD not in _project(case)['channels'][0]


def test_projection_is_independent_of_scheduler_lookup_availability(case):
    case.production.get_production_state.side_effect = RuntimeError('Unavailable')
    assert PENDING in _html(case, _project(case))
    case.forbidden.assert_not_called()


def test_external_titles_and_injected_subtotal_fields_are_escaped_or_ignored(case):
    case.model['channels'][0].update(title='<script>alert(1)</script>', known_public_video_view_subtotal='<img src=x onerror=alert(1)>')
    html = _html(case, _project(case))
    assert '<script>' not in html and '<img' not in html and PENDING in html
    raw = {**case.model['channels'][0], 'known_public_video_view_subtotal': '<script>alert(1)</script>'}
    assert PENDING not in case.ui.ns['_channel_overview']([raw])


def test_authenticated_cache_poll_uses_same_projection_and_never_refreshes_or_mutates(case):
    before = deepcopy(case.model)
    expected_html = _html(case, _project(case))
    case.read.reset_mock()
    response = case.ui.client.get('/studio/api/youtube-metrics')
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
    payload = response.json()
    assert payload['channel_overview_html'] == expected_html
    assert payload['channels'][0]['view_count'] == 0 and payload['channels'][0][FIELD] == 1191
    assert payload['videos'][SOURCE]['view_count'] == 1191
    assert 'channel-overview-host' in case.ui.ns['_metrics_script']()
    assert case.model == before and not case.ui.records
    case.read.assert_called_once_with([])
    case.forbidden.assert_not_called(); case.ui.forbidden.assert_not_called()
    case.ui.client.cookies.clear()
    case.read.reset_mock()
    assert case.ui.client.get('/studio/api/youtube-metrics').status_code == 401
    case.read.assert_not_called()
