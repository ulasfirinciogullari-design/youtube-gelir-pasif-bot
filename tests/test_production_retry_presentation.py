"""Loaded-job-only retry status; never change scheduler or publication authority."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from test_studio_workflow_presentation import ui, NOW


CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'current-oauth-connection'
REVISION = 'current-profile-revision'


def _id(index):
    return str(UUID(int=index + 100))


@pytest.fixture
def case(ui, monkeypatch):
    topic = 'Verified history of the first barcode scan'
    profile = {'channel_id': CHANNEL, 'route_label': 'capital-corrupt', 'profile_revision': REVISION,
               'auto_publish': True, 'production_enabled': True, 'release_mode': 'public',
               'production_topics': ['Earlier topic', topic, 'Next topic'], 'default_language': 'tr'}
    state = {'paused_reason': 'previous_render_failed', 'last_result': 'FAILURE',
             'dispatch_status': 'finished', 'last_task_id': _id(0), 'cursor': '2', 'next_due': '1',
             'connection_id': CONNECTION, 'profile_revision': REVISION}
    spec = {'topic': topic, 'duration_minutes': .5, 'language': 'tr', 'channel_id': 'capital-corrupt',
            'mode': 'production', 'format': 'shorts', 'workflow': 'auto', 'music': 'off',
            'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
            'production_profile_revision': REVISION, 'production_topic_index': 1,
            'production_scheduled': True, 'publish_after_render': True}
    jobs = []
    for index in range(4):
        leaf = index == 3
        job = {'task_id': _id(index), 'kind': 'render', 'state': 'PROGRESS' if leaf else 'FAILURE',
               'stage': 'director_qc' if leaf else 'failed', 'spec': deepcopy(spec), 'result': None,
               'parent_id': _id(index - 1) if index else None, 'updated_ts': NOW - 10}
        if not leaf:
            job.update(retry_child_task_id=_id(index + 1), retry_claimed=True, retry_dispatch_state='dispatched')
        jobs.append(job)
    ui.records.update({job['task_id']: job for job in jobs})
    metrics = ModuleType('app.services.youtube_metrics')
    model = {'channels': [{'channel_id': CHANNEL, 'title': 'Capital Corrupt', 'view_count': 1000}],
             'videos': {}, 'updated_at': None, 'refresh_after_seconds': 300}
    metrics.get_dashboard_metrics = Mock(return_value=model)
    metrics.refresh_dashboard_metrics = ui.forbidden
    production = ModuleType('app.services.channel_production')
    production.get_production_state = Mock(return_value=state)
    monkeypatch.setitem(sys.modules, metrics.__name__, metrics)
    monkeypatch.setitem(sys.modules, production.__name__, production)
    ui.ns['list_channel_profiles'] = Mock(return_value=[profile])
    path = Path(__file__).resolve().parents[1] / 'app/youtube_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in {'_production_status_text', '_produce_now_form', '_profile_form'}]
    routes = {'escape': escape, 'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone,
              '_studio_presentation': lambda: SimpleNamespace(**ui.ns)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), routes)
    return SimpleNamespace(ui=ui, profile=profile, state=state, jobs=jobs, spec=spec,
                           model=model, metrics=metrics, production=production, routes=routes)


def _project(c):
    return c.ui.ns['_active_production_retry'](c.profile, c.state,
                                              {job['task_id']: job for job in c.jobs}, now=NOW)


def test_running_retry_projection_is_pure_and_preserves_real_pause(case):
    before = deepcopy((case.profile, case.state, case.jobs))
    projected = _project(case)
    assert projected == {'status': 'retry_active', 'task_id': _id(3), 'stage': 'director_qc'}
    assert (case.profile, case.state, case.jobs) == before
    case.ui.lookup.assert_not_called()
    case.ui.ledger_lookup.assert_not_called()
    case.ui.forbidden.assert_not_called()
    case.production.get_production_state.assert_not_called()


@pytest.mark.parametrize('state,dispatch,expected', [
    ('PROGRESS', 'dispatched', 'retry_active'), ('STARTED', 'reserved', 'retry_active'),
    ('PROGRESS', 'uncertain', 'retry_active'), ('PENDING', 'dispatched', 'retry_queued'),
    ('RECEIVED', 'dispatched', 'retry_queued'), ('RETRY', 'dispatched', 'retry_queued'),
    ('PENDING', 'reserved', 'retry_reserved'), ('PENDING', 'uncertain', 'retry_uncertain'),
])
def test_distinguishes_execution_queue_reservation_and_uncertain_send(case, state, dispatch, expected):
    case.jobs[-1]['state'] = state
    case.jobs[-2]['retry_dispatch_state'] = dispatch
    assert _project(case)['status'] == expected
    model = case.ui.ns['_dashboard_metrics'](case.jobs)
    html = case.ui.ns['_channel_overview'](model['channels'])
    assert case.ui.ns['PRODUCTION_RETRY_LABELS'][expected] in html
    assert ('Senaryo yönetmeni' in html) is (expected == 'retry_active')
    assert 'Üretim durdu' not in html


@pytest.mark.parametrize('target,key,value', [
    ('state', 'paused_reason', 'previous_publication_blocked'), ('state', 'last_result', 'SUCCESS'),
    ('state', 'last_task_id', _id(3)), ('state', 'dispatch_status', 'uncertain'),
    ('state', 'active_task_id', _id(9)), ('state', 'cursor', '1'), ('state', 'unavailable', True),
    ('state', 'connection_id', 'different-connection'), ('state', 'profile_revision', 'changed'),
    ('profile', 'production_enabled', False), ('profile', 'auto_publish', False),
    ('profile', 'release_mode', 'private'), ('profile', 'channel_id', 'UCdifferent-channel'),
    ('profile', 'profile_revision', 'changed'), ('profile', 'default_language', 'en'),
    ('profile', 'route_label', 'different-route'), ('profile', 'production_topics', ['changed', 'topic']),
])
def test_other_pause_or_changed_current_schedule_profile_never_claims_retry_active(case, target, key, value):
    getattr(case, target)[key] = value
    assert _project(case) is None


@pytest.mark.parametrize('index,key,value', [
    (0, 'parent_id', _id(9)), (0, 'retry_claimed', 1), (1, 'retry_claimed', False),
    (1, 'retry_dispatch_state', 'unknown'), (1, 'state', 'SUCCESS'), (1, 'kind', 'publish'),
    (1, 'result', {'video_key': 'another-video'}), (2, 'parent_id', _id(9)),
    (2, 'retry_child_task_id', _id(0)), (2, 'retry_child_task_id', _id(8)),
    (3, 'retry_claimed', True), (3, 'state', 'SUCCESS'), (3, 'state', 'FAILURE'),
    (3, 'state', 'AWAITING_APPROVAL'), (3, 'stage', 'complete'), (3, 'stage', 'failed'),
    (3, 'result', {'video_key': 'complete-video'}), (3, 'updated_ts', NOW - 21600),
    (3, 'updated_ts', NOW + 1), (3, 'updated_ts', None),
])
def test_invalid_lineage_terminal_or_stale_leaf_is_not_active(case, index, key, value):
    case.jobs[index][key] = value
    assert _project(case) is None


@pytest.mark.parametrize('key,value', [('topic', 'Changed'), ('production_channel_id', 'UCdifferent-channel'),
                                      ('production_profile_revision', 'changed'), ('production_connection_id', 'changed'),
                                      ('quality_threshold', 0), ('publish_after_render', 1)])
def test_descendant_must_keep_all_frozen_spec_fields(case, key, value):
    case.jobs[2]['spec'][key] = value
    assert _project(case) is None


def test_transport_only_repair_fields_do_not_hide_genuine_running_retry(case):
    case.jobs[2]['spec'].update(workflow='scene_repair', repair_source_task_id=_id(1))
    case.jobs[3]['spec'].update(workflow='scene_repair', repair_source_task_id=_id(1))
    assert _project(case)['status'] == 'retry_active'


def test_lookup_identity_missing_parent_and_hop_bound_are_conservative(case):
    by_id = {job['task_id']: job for job in case.jobs}
    by_id[_id(0)] = {**case.jobs[0], 'task_id': _id(9)}
    assert case.ui.ns['_active_production_retry'](case.profile, case.state, by_id, now=NOW) is None
    case.jobs.pop(1)
    assert _project(case) is None


def test_retry_walk_stops_at_existing_bound(case):
    case.ui.ns['RETRY_PRESENTATION_MAX_HOPS'] = 2
    assert _project(case) is None


def test_initial_channel_card_and_authenticated_poll_share_readonly_projection(case):
    before = deepcopy((case.state, case.jobs, case.model))
    model = case.ui.ns['_dashboard_metrics'](case.jobs)
    expected = case.ui.ns['_channel_overview'](model['channels'])
    response = case.ui.client.get('/studio/api/youtube-metrics')
    assert response.status_code == 200
    payload = response.json()
    assert payload['channel_overview_html'] == expected
    assert f'href="/studio/job/{_id(3)}"' in expected
    assert 'Yeniden üretim sürüyor · takvim sonucu bekliyor' in expected
    assert payload['channels'][0]['remaining_topics'] == 1
    assert (case.state, case.jobs, case.model) == before
    case.metrics.refresh_dashboard_metrics.assert_not_called()
    case.ui.lookup.assert_not_called()
    case.ui.ledger_lookup.assert_not_called()
    case.ui.forbidden.assert_not_called()


def test_management_badge_uses_projection_but_produce_now_keeps_real_pause(case):
    projection = _project(case)
    before = deepcopy(case.state)
    text = case.routes['_production_status_text'](case.profile, case.state, projection)
    assert text == case.ui.ns['PRODUCTION_RETRY_LABELS']['retry_active']
    html = case.routes['_profile_form']({'id': CHANNEL, 'connection_id': CONNECTION}, case.profile,
                                      case.state, projection)
    assert text in html
    assert 'Hemen sıradaki videoyu üret' not in html
    assert case.routes['_produce_now_form'](case.profile, case.state) == ''
    assert case.state == before
    assert case.routes['_production_status_text'](case.profile, case.state) == 'Duraklatıldı · kontrol gerekiyor'


def test_failure_or_missing_chain_leaves_existing_pause_label(case):
    case.jobs[-1]['state'] = 'FAILURE'
    model = case.ui.ns['_dashboard_metrics'](case.jobs)
    assert model['channels'][0]['production_status'] == 'paused'
    assert 'production_retry' not in model['channels'][0]
    assert 'Üretim durdu · kontrol gerekiyor' in case.ui.ns['_channel_overview'](model['channels'])


def test_cached_projection_is_not_trusted_and_links_cannot_inject_html(case):
    case.model['channels'][0]['production_retry'] = {'task_id': '<script>', 'status': 'retry_active'}
    case.jobs[-1]['state'] = 'FAILURE'
    model = case.ui.ns['_dashboard_metrics'](case.jobs)
    assert 'production_retry' not in model['channels'][0]
    html = case.ui.ns['_channel_overview']([{'channel_id': CHANNEL, 'production_status': 'retry_active',
                                         'production_retry': {'task_id': '<script>', 'status': 'retry_active'}}])
    assert '<script>' not in html and 'Yeniden üretim sürüyor' not in html
