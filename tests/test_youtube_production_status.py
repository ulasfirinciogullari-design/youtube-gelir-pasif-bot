"""Owner scheduler diagnostics are read-only, bounded and credential-free."""
import ast
from copy import deepcopy
from pathlib import Path
import re
import sys
from types import ModuleType
from unittest.mock import Mock

from fastapi import APIRouter, Cookie, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

CHANNEL = 'UCgvESYtYbn2w9R2ExBOF_cw'
CONNECTION = 'OhQayRxWxIOCBffD_ocwQnocEwmQSA3C'
CURRENT = '669c40ecf39a40e0bff60781cee20c2c'
OLD = '21503daa264b441ab95365fdec550d9f'
JOB = 'd3f768c8-f506-44b0-a3ad-1bf85850ae68'


@pytest.fixture
def case(monkeypatch):
    channel = {'id': CHANNEL, 'connection_id': CONNECTION, 'refresh_token': 'NEVER-RETURN'}
    profile = {'channel_id': CHANNEL, 'profile_revision': CURRENT, 'production_enabled': True,
        'auto_publish': True, 'release_mode': 'public', 'production_topics': ['IKEA', 'Costco', 'LEGO', 'Nintendo']}
    state = {'cursor': '2', 'next_due': '1788680761.549314', 'last_task_id': JOB,
        'paused_reason': 'previous_render_failed', 'profile_revision': OLD, 'connection_id': CONNECTION,
        'last_result': 'FAILURE', 'dispatch_status': 'finished', 'consumed_prefix': 'a' * 64,
        'refresh_token': 'NEVER-RETURN'}
    getter = Mock(side_effect=lambda _: deepcopy(state))
    module = ModuleType('app.services.channel_production')
    module.get_production_state = getter
    monkeypatch.setitem(sys.modules, 'app.services.channel_production', module)
    connection = Mock(side_effect=lambda **_: {'channel': deepcopy(channel), 'requires_reconnect': False})
    get_profile = Mock(side_effect=lambda _: deepcopy(profile))
    def auth(token):
        if token != 'owner':
            raise HTTPException(401, 'unauthorized')
    path = Path(__file__).parents[1] / 'app/youtube_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == 'youtube_production_status']
    namespace = {'router': APIRouter(), 'Cookie': Cookie, 'COOKIE_NAME': 'youtube_studio_token',
        'HTTPException': HTTPException, '_require_auth': auth, 're': re,
        'connection_status': connection, 'get_channel_profile': get_profile}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    app = FastAPI(); app.include_router(namespace['router'])
    return TestClient(app), state, profile, channel, getter, connection


def read(case, owner=True):
    client = case[0]
    return client.get('/studio/youtube/production-status/' + CHANNEL,
                      headers={'Cookie': 'youtube_studio_token=owner'} if owner else {})


def test_exact_read_only_pause_and_mismatched_revisions_are_visible(case):
    before = deepcopy(case[1:4])
    response = read(case)
    assert response.status_code == 200
    result = response.json()
    assert result['paused_reason'] == 'previous_render_failed'
    assert result['cursor'] == result['next_topic_index'] == 2
    assert result['topic_count'] == 4 and result['profile_revision'] == CURRENT
    assert result['state_profile_revision'] == OLD and result['last_task_id'] == JOB
    assert result['series_epoch_present'] is False
    assert response.headers['cache-control'] == 'no-store'
    assert 'NEVER-RETURN' not in response.text and 'refresh_token' not in response.text
    assert case[1:4] == before
    case[4].assert_called_once_with(CHANNEL)
    case[5].assert_called_once_with(channel_id=CHANNEL, verify=False)


def test_unauthenticated_read_calls_no_getter(case):
    assert read(case, owner=False).status_code == 401
    case[4].assert_not_called(); case[5].assert_not_called()


def test_unconnected_channel_calls_no_state_getter(case):
    case[3]['id'] = 'different'
    assert read(case).status_code == 404
    case[4].assert_not_called()


def test_bad_channel_identifier_rejected_before_lookup(case):
    response = case[0].get('/studio/youtube/production-status/not-a-channel',
                          headers={'Cookie': 'youtube_studio_token=owner'})
    assert response.status_code == 422
    case[5].assert_not_called()


@pytest.mark.parametrize('value', ['-1', '02', '99', None, 2])
def test_corrupt_cursor_returns_sanitized_failure(case, value):
    case[1]['cursor'] = value
    response = read(case)
    assert response.status_code == 503 and response.json()['detail'] == 'production_state_unavailable'


def test_unknown_values_are_not_echoed(case):
    for key in ('paused_reason', 'last_result', 'dispatch_status', 'last_task_id',
                'active_task_id', 'next_due', 'consumed_prefix', 'profile_revision'):
        case[1][key] = 'sk-NEVER-ECHO-FROM-STATE!'
    response = read(case)
    assert response.status_code == 200 and response.json()['paused_reason'] == 'unrecognized_pause'
    assert 'NEVER-ECHO' not in response.text


def test_completed_topics_report_no_next_index(case):
    case[1]['cursor'] = '4'
    assert read(case).json()['next_topic_index'] is None


def test_getter_failure_does_not_leak_exception(case):
    case[4].side_effect = RuntimeError('redis://password@private-host')
    response = read(case)
    assert response.status_code == 503 and 'password' not in response.text


def test_post_is_not_allowed(case):
    assert case[0].post('/studio/youtube/production-status/' + CHANNEL).status_code == 405
