"""Actual route/form code with injected services; no OAuth or network calls."""
import ast
from copy import deepcopy
from html import escape
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
import pytest


@pytest.fixture
def ui(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'app' / 'youtube_routes.py'
    names = {'_produce_now_form', '_existing_release_form', 'youtube_release_existing',
             'youtube_produce_next_now'}
    nodes = [n for n in ast.parse(path.read_text(encoding='utf-8-sig')).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    events = []
    namespace = {
        'escape': escape, 'Form': Form, 'Cookie': Cookie, 'Request': Request,
        'HTTPException': HTTPException, 'RedirectResponse': RedirectResponse,
        'COOKIE_NAME': 'test-cookie',
        '_require_auth': Mock(side_effect=lambda *_: events.append('auth')),
        '_require_same_origin': Mock(side_effect=lambda *_: events.append('origin')),
        'automated_quality_approved': Mock(return_value=True),
    }
    services = {}
    for module_name, function, error in (
        ('existing_video_release', 'release_existing_private_video', 'ExistingVideoReleaseError'),
        ('production_schedule_control', 'expedite_next_production', 'ProductionScheduleControlError'),
    ):
        module = ModuleType('app.services.' + module_name)
        setattr(module, error, type(error, (RuntimeError,), {}))
        setattr(module, function, Mock(side_effect=lambda *_a, **_k: events.append('service')))
        monkeypatch.setitem(sys.modules, module.__name__, module)
        services[module_name] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return SimpleNamespace(ns=namespace, events=events, services=services)


def _profile():
    return {'channel_id': 'channel-123', 'profile_revision': 'revision-one',
            'auto_publish': True, 'production_enabled': True, 'release_mode': 'public',
            'production_topics': ['First', 'Second']}


def _job():
    return {'task_id': 'source-123', 'result': {'youtube': {
        'video_id': 'video-123', 'target_channel_id': 'channel-123',
        'privacy_status': 'private', 'release_status': 'private',
    }}}


def test_release_form_uses_existing_identity_and_explicit_post(ui):
    job, profile = _job(), _profile()
    before = deepcopy((job, profile))
    html = ui.ns['_existing_release_form'](job, profile)
    assert 'method="post" action="/studio/youtube/release/source-123"' in html
    assert 'name="expected_video_id" value="video-123"' in html
    assert 'name="youtube_channel_id" value="channel-123"' in html
    assert 'name="expected_profile_revision" value="revision-one"' in html
    assert 'Herkese aç' in html and '/publish/' not in html
    assert (job, profile) == before


@pytest.mark.parametrize('release', ['public', 'scheduled', 'uncertain', 'releasing', 'blocked', None])
def test_no_repeat_release_button_on_nonprivate_outcome(ui, release):
    job = _job()
    job['result']['youtube']['release_status'] = release
    assert ui.ns['_existing_release_form'](job, _profile()) == ''


@pytest.mark.parametrize('updates', [
    {'channel_id': 'another-channel'}, {'auto_publish': False},
    {'release_mode': 'private'}, {'profile_revision': ''},
])
def test_release_requires_current_public_profile_binding(ui, updates):
    assert ui.ns['_existing_release_form'](_job(), {**_profile(), **updates}) == ''


def test_release_form_requires_automated_quality(ui):
    ui.ns['automated_quality_approved'].return_value = False
    assert ui.ns['_existing_release_form'](_job(), _profile()) == ''


@pytest.mark.parametrize('updates', [
    {'active_task_id': 'active'}, {'paused_reason': 'previous_render_failed'},
    {'cursor': '2'}, {'cursor': 'invalid'}, {'dispatch_status': 'uncertain'},
])
def test_start_now_never_offered_for_busy_paused_exhausted_or_uncertain_state(ui, updates):
    state = {'cursor': '1', 'dispatch_status': 'finished', **updates}
    assert ui.ns['_produce_now_form'](_profile(), state) == ''


def test_start_now_form_preserves_profile_revision_and_posts_only(ui):
    html = ui.ns['_produce_now_form'](_profile(), {'cursor': '1', 'dispatch_status': 'finished'})
    assert 'method="post" action="/studio/youtube/produce-now/channel-123"' in html
    assert 'name="expected_revision" value="revision-one"' in html
    assert 'Hemen sıradaki videoyu üret' in html


def test_newly_promoted_first_episode_has_start_control_but_unproven_initial_queue_does_not(ui):
    profile = {**_profile(), 'series_epoch': 1}
    state = {'cursor': '0', 'dispatch_status': 'series_promoted', 'profile_revision': 'revision-one',
             'series_epoch': '1', 'last_series_promotion': 'server-promotion-receipt'}
    assert 'Hemen sıradaki videoyu üret' in ui.ns['_produce_now_form'](profile, state)
    for field, value in [('paused_reason', 'owner_paused'), ('active_task_id', 'active'),
                         ('last_task_id', 'prior'), ('series_epoch', '2'),
                         ('profile_revision', 'changed'), ('last_series_promotion', None)]:
        assert ui.ns['_produce_now_form'](profile, {**state, field: value}) == ''
    assert ui.ns['_produce_now_form'](profile, {'cursor': '0', 'dispatch_status': 'finished'}) == ''


def _invoke(ui, route):
    if route == 'youtube_release_existing':
        return ui.ns[route]('source-123', object(), expected_video_id='video-123',
                            youtube_channel_id='channel-123', expected_profile_revision='revision-one',
                            studio_token='owner-token')
    return ui.ns[route]('channel-123', object(), expected_revision='revision-one', studio_token='owner-token')


@pytest.mark.parametrize('route', ['youtube_release_existing', 'youtube_produce_next_now'])
def test_owner_and_same_origin_are_checked_before_service_action(ui, route):
    result = _invoke(ui, route)
    assert ui.events == ['auth', 'origin', 'service']
    assert result.status_code == 303 and result.headers['location'] == '/studio/youtube'


@pytest.mark.parametrize('route', ['youtube_release_existing', 'youtube_produce_next_now'])
@pytest.mark.parametrize('guard', ['_require_auth', '_require_same_origin'])
def test_authentication_or_cross_origin_failure_never_calls_mutation(ui, route, guard):
    ui.ns[guard].side_effect = HTTPException(status_code=403, detail='blocked')
    with pytest.raises(HTTPException) as caught:
        _invoke(ui, route)
    assert caught.value.status_code == 403 and 'service' not in ui.events


def test_release_endpoint_passes_exact_existing_video_and_current_authorization(ui):
    _invoke(ui, 'youtube_release_existing')
    ui.services['existing_video_release'].release_existing_private_video.assert_called_once_with(
        'source-123', expected_video_id='video-123', expected_channel_id='channel-123',
        expected_profile_revision='revision-one')


@pytest.mark.parametrize(('route', 'module', 'function', 'error'), [
    ('youtube_release_existing', 'existing_video_release', 'release_existing_private_video', 'ExistingVideoReleaseError'),
    ('youtube_produce_next_now', 'production_schedule_control', 'expedite_next_production', 'ProductionScheduleControlError'),
])
def test_service_failure_is_safe_conflict_and_never_retried(ui, route, module, function, error):
    service = ui.services[module]
    call = getattr(service, function)
    call.side_effect = getattr(service, error)('sensitive-underlying-details')
    with pytest.raises(HTTPException) as caught:
        _invoke(ui, route)
    assert caught.value.status_code == 409
    assert 'sensitive-underlying-details' not in caught.value.detail
    call.assert_called_once()


def test_new_operations_have_only_post_routes_in_actual_source():
    path = Path(__file__).resolve().parents[1] / 'app' / 'youtube_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for name in ('youtube_release_existing', 'youtube_produce_next_now'):
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert len(node.decorator_list) == 1
        assert node.decorator_list[0].func.attr == 'post'
