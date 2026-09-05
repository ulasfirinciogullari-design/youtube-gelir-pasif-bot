"""Authenticated, one-shot Studio dispatch, isolated from app imports."""
import ast
from copy import deepcopy
from html import escape
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'studio.py'
PARENT = '813d8313-db44-4273-9178-a6b951405378'
CHILD = 'ed33ced3-cf8b-442e-a908-4fc036874fac'
CHECKSUM = 'a' * 64


class HTTPException(RuntimeError):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class ReservationError(RuntimeError):
    pass


def _load(name, namespace):
    node = next(n for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace[name]


@pytest.fixture
def ui(monkeypatch):
    spec = dict(topic='Frozen story', duration_minutes=.5, language='tr', channel_id='channel123',
                mode='production', format='shorts', production_channel_id='channel123',
                production_connection_id='connection123', production_profile_revision='revision123',
                production_scheduled=True, publish_after_render=True)
    record = dict(task_id=PARENT, state='FAILURE', kind='render', failure_stage='audio_pause_recheck', spec=deepcopy(spec),
                  audio_candidate_checkpoint=dict(status='unapproved_candidate', qa_approved=False,
                                                  requires_full_qa=True, audio_sha256=CHECKSUM))
    events = []
    auth = Mock(side_effect=lambda _: events.append('auth'))
    origin = Mock(side_effect=lambda _: events.append('origin'))
    reserve = Mock(side_effect=lambda *_a: events.append('reserve') or dict(
        claimed=True, child_task_id=CHILD, spec=deepcopy(spec), voice_replacement={'max_attempts': 1}))
    monkeypatch.setitem(sys.modules, 'app.youtube_routes', SimpleNamespace(_require_same_origin=origin))
    monkeypatch.setitem(sys.modules, 'app.services.voice_replacement', SimpleNamespace(
        VoiceReplacementError=ReservationError, reserve_voice_replacement=reserve))
    n = dict(Request=object, Cookie=lambda **_: None, Form=lambda *_: None, COOKIE_NAME='studio',
             HTTPException=HTTPException, _require_auth=auth, get_job=Mock(side_effect=lambda _: events.append('read') or record),
             uuid4=lambda: CHILD, secrets=SimpleNamespace(token_urlsafe=lambda _: 'dispatch-token'),
             _canonical_task_id=lambda value: value if value == CHILD else None,
             RedirectResponse=lambda url, status_code: SimpleNamespace(url=url, status_code=status_code),
             create_job=Mock(), update_job=Mock(), run_video_pipeline=SimpleNamespace(apply_async=Mock()),
             mark_retry_dispatch=Mock(), re=re, escape=escape,
             _shell=Mock(side_effect=lambda body, **_: SimpleNamespace(body=body, headers={})))
    candidate = _load('_voice_replacement_candidate', n)
    post = _load('studio_voice_replacement', n)
    get = _load('studio_voice_replacement_page', n)
    return SimpleNamespace(**locals())


def test_route_authorizes_origin_before_read_and_reserves_before_dispatch(ui):
    u = ui
    result = u.post(PARENT, object(), CHECKSUM, 'authenticated-cookie')
    assert u.events == ['auth', 'origin', 'read', 'reserve']
    assert result.url == '/studio/job/' + CHILD and result.status_code == 303
    u.n['create_job'].assert_called_once_with(CHILD, u.spec, kind='render', parent_id=PARENT)
    args = u.n['run_video_pipeline'].apply_async.call_args.kwargs['args']
    assert args[:4] == ('Frozen story', .5, 'tr', 'channel123')
    assert args[5:] == (None, PARENT, None, PARENT)
    assert args[4]['publish_after_render'] is True and args[4]['production_connection_id'] == 'connection123'
    assert 'voice_replacement_source_id' not in args[4]
    u.n['mark_retry_dispatch'].assert_called_once_with(PARENT, 'dispatch-token', 'dispatched')


def test_route_uses_only_atomic_returned_frozen_spec_not_earlier_read(ui):
    ui.record['spec']['topic'] = 'Stale earlier snapshot'
    ui.post(PARENT, object(), CHECKSUM, 'cookie')
    assert ui.n['create_job'].call_args.args[1]['topic'] == 'Frozen story'
    assert ui.n['run_video_pipeline'].apply_async.call_args.kwargs['args'][0] == 'Frozen story'


@pytest.mark.parametrize('gate', ['auth', 'origin'])
def test_failed_auth_or_origin_cannot_read_reserve_or_enqueue(ui, gate):
    getattr(ui, gate).side_effect = HTTPException(403, 'denied')
    with pytest.raises(HTTPException):
        ui.post(PARENT, object(), CHECKSUM, 'cookie')
    ui.n['get_job'].assert_not_called()
    ui.reserve.assert_not_called()
    ui.n['run_video_pipeline'].apply_async.assert_not_called()


@pytest.mark.parametrize('error,status', [(ReservationError('private detail'), 409), (RuntimeError('secret body'), 503)])
def test_reservation_failure_is_safe_terminal_and_does_not_create_child(ui, error, status):
    ui.reserve.side_effect = error
    with pytest.raises(HTTPException) as caught:
        ui.post(PARENT, object(), CHECKSUM, 'cookie')
    assert caught.value.status_code == status and str(error) not in caught.value.detail
    ui.n['create_job'].assert_not_called()
    ui.n['run_video_pipeline'].apply_async.assert_not_called()


def test_existing_claim_redirects_without_enqueuing_again(ui):
    ui.reserve.side_effect = None
    ui.reserve.return_value = {'claimed': False, 'child_task_id': CHILD}
    result = ui.post(PARENT, object(), CHECKSUM, 'cookie')
    assert result.url.endswith(CHILD)
    ui.n['create_job'].assert_not_called()
    ui.n['run_video_pipeline'].apply_async.assert_not_called()


def test_ambiguous_broker_result_does_not_release_or_repeat_claim(ui):
    ui.n['run_video_pipeline'].apply_async.side_effect = RuntimeError('Possibly accepted')
    ui.post(PARENT, object(), CHECKSUM, 'cookie')
    assert ui.n['run_video_pipeline'].apply_async.call_count == 1
    ui.n['mark_retry_dispatch'].assert_called_once_with(PARENT, 'dispatch-token', 'uncertain')
    assert ui.n['update_job'].call_args.kwargs['stage'] == 'dispatch_uncertain'


def test_page_is_authenticated_only_and_provides_same_origin_form_proof(ui):
    result = ui.get(PARENT, 'cookie')
    assert ui.events == ['auth', 'read']
    assert result.headers['Referrer-Policy'] == 'same-origin'
    assert 'method="post"' in result.body and CHECKSUM in result.body
    assert 'yalnız bir yeni kayıt' in result.body
    ui.reserve.assert_not_called()


@pytest.mark.parametrize('change', [dict(state='SUCCESS'), dict(kind='publish'), dict(retry_claimed=True),
                                   dict(retry_child_task_id=CHILD), dict(voice_replacement={'max_attempts': 1}),
                                   dict(failure_stage='final_visual_qc'), dict(audio_candidate_checkpoint=None)])
def test_ui_never_offers_replacement_for_ineligible_or_already_consumed_parent(ui, change):
    ui.record.update(change)
    assert ui.candidate(ui.record) is False
    with pytest.raises(HTTPException) as caught:
        ui.get(PARENT, 'cookie')
    assert caught.value.status_code == 409


@pytest.mark.parametrize('field,value', [('language', 'en'), ('duration_minutes', 3), ('format', 'landscape'), ('mode', 'preview')])
def test_ui_stays_within_explicit_turkish_short_production(ui, field, value):
    ui.record['spec'][field] = value
    assert ui.candidate(ui.record) is False
