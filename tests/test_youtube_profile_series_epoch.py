"""Profile form edits cannot rewrite server-authored series history."""
import ast
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

from fastapi import APIRouter, Cookie, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
import pytest

from test_channel_production import CHANNEL, CONNECTION, ROOT, production, _save
from test_youtube_automation import FakeRedis, automation, _profile


@pytest.fixture
def saved(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(automation, '_redis', lambda: client)
    profile = automation._profile_payload(CHANNEL, _profile(
        channel_id=CHANNEL, production_enabled=True,
        production_topics=['First topic', 'Second topic'],
    ))
    profile.update(series_epoch=2, server_metadata={'history': 'preserve exactly'})
    # Deliberately noncanonical JSON: a no-op must retain the stored bytes.
    raw = json.dumps(profile, ensure_ascii=False, indent=2)
    key = automation.PROFILE_PREFIX + CHANNEL
    client.values[key] = raw
    client.values['unrelated:epoch-receipt'] = 'immutable receipt'
    return SimpleNamespace(client=client, profile=profile, raw=raw, key=key)


def _save_profile(case, edits, *, revision=None):
    return automation.save_channel_profile(
        CHANNEL, edits,
        expected_revision=revision or case.profile['profile_revision'],
    )


@pytest.mark.parametrize('edits', [
    {}, {'route_label': '  merak-tr  '},
    {'production_topics': ' First topic\nSecond topic\n'},
    {'series_epoch': 999, 'profile_revision': 'untrusted', 'updated_at': 'untrusted'},
    {'server_metadata': {'history': 'forged'}},
])
def test_semantic_noop_keeps_exact_raw_server_epoch_revision_and_metadata(saved, edits):
    before = deepcopy(saved.client.values)
    result = _save_profile(saved, edits)
    assert result == saved.profile
    assert saved.client.values == before
    assert saved.client.values[saved.key] == saved.raw


@pytest.mark.parametrize('edits', [
    {'channel_identity': 'A different identity'}, {'route_label': 'different'},
    {'languages': ['en'], 'default_language': 'en'},
    {'production_topics': ['New topic']}, {'production_interval_hours': 6},
    {'series_id': 'new-series'}, {'series_total': 9}, {'series_name': 'New title'},
    {'release_mode': 'private'}, {'description_footer': 'New footer'},
    {'default_tags': ['different']}, {'topic_keywords': ['different']},
    {'production_enabled': False, 'description_footer': 'Also change this'},
    {'auto_publish': False, 'series_epoch': None, 'series_total': 1},
])
def test_promoted_meaningful_edits_fail_without_writing_or_dropping_epoch(saved, edits):
    before = deepcopy(saved.client.values)
    with pytest.raises(automation.SeriesProfileEditError):
        _save_profile(saved, edits)
    assert saved.client.values == before


@pytest.mark.parametrize('edits', [
    {'production_enabled': False}, {'auto_publish': False},
    {'production_enabled': False, 'auto_publish': False},
])
@pytest.mark.parametrize('epoch', [2, None, {}, False, 'damaged'])
def test_stop_remains_available_with_damaged_epoch_without_rebinding_history(saved, edits, epoch):
    saved.profile['series_epoch'] = epoch
    saved.client.values[saved.key] = json.dumps(saved.profile)
    before = deepcopy(saved.client.values)
    result = _save_profile(saved, {**edits, 'series_epoch': 'forged'})
    assert result['profile_revision'] != saved.profile['profile_revision']
    unchanged = set(saved.profile) - set(edits) - {'profile_revision', 'updated_at'}
    assert {k: result[k] for k in unchanged} == {k: saved.profile[k] for k in unchanged}
    assert all(result[field] is False for field in edits)
    assert {k: v for k, v in saved.client.values.items() if k != saved.key} == {
        k: v for k, v in before.items() if k != saved.key}
    with pytest.raises(automation.SeriesProfileEditError):
        automation.save_channel_profile(
            CHANNEL, {field: True for field in edits}, expected_revision=result['profile_revision'],
        )


def test_stop_cannot_also_reenable_a_different_permission(saved):
    saved.profile['auto_publish'] = False
    saved.client.values[saved.key] = json.dumps(saved.profile)
    with pytest.raises(automation.SeriesProfileEditError):
        _save_profile(saved, {'production_enabled': False, 'auto_publish': True})


def test_form_cannot_create_epoch_and_legacy_meaningful_edits_still_work(saved):
    saved.profile.pop('series_epoch')
    saved.client.values[saved.key] = json.dumps(saved.profile)
    updated = _save_profile(saved, {'description_footer': 'Allowed legacy edit', 'series_epoch': 99})
    assert updated['description_footer'] == 'Allowed legacy edit'
    assert updated['profile_revision'] != saved.profile['profile_revision']
    assert 'series_epoch' not in updated


@pytest.mark.parametrize('edits', [{}, {'production_enabled': False}])
def test_noop_and_stop_keep_revision_and_raw_record_compare_and_swap(saved, edits, monkeypatch):
    with pytest.raises(automation.ProfileConflictError):
        _save_profile(saved, edits, revision='stale')
    with pytest.raises(automation.ProfileConflictError):
        automation.save_channel_profile(CHANNEL, edits)
    real_eval = saved.client.eval
    concurrent = json.dumps({**saved.profile, 'series_epoch': 3, 'profile_revision': 'new-promotion'})

    def race(*args):
        saved.client.values[saved.key] = concurrent
        return real_eval(*args)

    monkeypatch.setattr(saved.client, 'eval', race)
    with pytest.raises(automation.ProfileConflictError):
        _save_profile(saved, edits)
    assert saved.client.values[saved.key] == concurrent


@pytest.mark.parametrize('flag', ['production_enabled', 'auto_publish'])
def test_stop_fences_real_reservation_even_with_a_stale_enabled_hint(production, monkeypatch, flag):
    scheduler, client = production
    monkeypatch.setattr(automation, '_redis', lambda: client)
    profile = automation._profile_payload(CHANNEL, _profile(
        channel_id=CHANNEL, production_enabled=True,
        production_topics=['First topic', 'Second topic'],
    ))
    profile['series_epoch'] = 2
    _save(scheduler, client, profile)
    state_key = scheduler.CHANNEL_STATE_PREFIX + CHANNEL
    state = {'cursor': '1', 'series_epoch': '2', 'last_series_promotion': 'old-receipt',
             'profile_revision': profile['profile_revision']}
    client.hset(state_key, mapping=state)
    client.set('old-receipt', 'unchanged')
    before = client.hgetall(state_key)
    stopped = automation.save_channel_profile(
        CHANNEL, {flag: False}, expected_revision=profile['profile_revision'],
    )
    assert scheduler.reserve_due_production(stopped, CONNECTION, now=1000)['status'] == 'disabled'
    assert scheduler.reserve_due_production(profile, CONNECTION, now=1000)['status'] != 'reserved'
    assert client.hgetall(state_key) == before
    assert client.get('old-receipt') == 'unchanged'
    assert not client.keys(scheduler.JOB_PREFIX + '*')
    assert not client.get(scheduler.ACTIVE_KEY)


@pytest.fixture
def route():
    """Exercise actual auth/origin/connection checks, with no external services."""
    names = {'_valid_token', '_require_auth', '_canonical_origin', '_require_same_origin', 'youtube_save_profile'}
    path = ROOT / 'app/youtube_routes.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    write = Mock(side_effect=automation.SeriesProfileEditError('never expose internal details'))
    ns = {
        'APIRouter': APIRouter, 'Cookie': Cookie, 'Form': Form, 'HTTPException': HTTPException,
        'Request': Request, 'RedirectResponse': RedirectResponse, 'urlparse': urlparse,
        'router': APIRouter(), 'COOKIE_NAME': 'youtube_studio_token',
        'settings': SimpleNamespace(factory_api_token='test-session', google_redirect_uri='https://studio.example/studio/youtube/callback'),
        'connection_status': Mock(return_value={'channel': CONNECTION}),
        'save_channel_profile': write, 'ProfileConflictError': automation.ProfileConflictError,
        'SeriesProfileEditError': automation.SeriesProfileEditError,
        'YouTubeAutomationError': automation.YouTubeAutomationError,
    }
    exec(compile(tree, str(path), 'exec'), ns)
    app = FastAPI()
    app.include_router(ns['router'])
    client = TestClient(app)
    client.cookies.set('youtube_studio_token', 'test-session')
    return SimpleNamespace(client=client, ns=ns, write=write)


def _post(route, **kwargs):
    return route.client.post(
        '/studio/youtube/profile/' + CHANNEL,
        data={'connection_id': CONNECTION['connection_id'], 'expected_revision': 'current'},
        headers=kwargs.pop('headers', {'Origin': 'https://studio.example'}), **kwargs,
    )


def test_promoted_edit_route_has_fixed_visible_reason_without_internal_details(route):
    response = _post(route)
    assert response.status_code == 409
    assert 'otomatik seri geçmişine bağlı' in response.json()['detail']
    assert 'Üretimi veya otomatik yayını kapatabilirsiniz' in response.json()['detail']
    assert 'internal' not in response.text


def test_ordinary_concurrent_edit_keeps_its_existing_route_message(route):
    route.write.side_effect = automation.ProfileConflictError('internal')
    response = _post(route)
    assert response.status_code == 409
    assert response.json()['detail'] == 'Kanal profili başka bir işlemde değişti; sayfayı yenile'


@pytest.mark.parametrize('invalid', ['session', 'origin', 'connection'])
def test_profile_epoch_route_still_requires_session_same_origin_and_current_connection(route, invalid):
    headers = {'Origin': 'https://studio.example'}
    if invalid == 'session':
        route.client.cookies.clear()
    elif invalid == 'origin':
        headers = {'Origin': 'https://other.example'}
    else:
        route.ns['connection_status'].return_value = {'channel': {**CONNECTION, 'connection_id': 'changed'}}
    response = _post(route, headers=headers)
    assert response.status_code == {'session': 401, 'origin': 403, 'connection': 409}[invalid]
    route.write.assert_not_called()
