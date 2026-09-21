"""Status telemetry is bounded, read-only on view, and never dispatch authority."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services import studio_operations as operations
from test_studio_workflow_presentation import ui

NOW = 1789980000.0


def store(value):
    client = Mock()
    client.get.return_value = json.dumps(value) if value is not None else None
    return client


@pytest.mark.parametrize('age,status', [(0, 'checked'), (180, 'checked'), (181, 'stale'), (86400, 'stale')])
def test_worker_freshness_is_based_on_actual_completed_tick(age, status):
    client = store({'version': 1, 'observed_ts': NOW - age, 'status': 'checked'})
    result = operations.read_tick(client=client, now=NOW)
    assert result['status'] == status and result['observed_at']
    client.get.assert_called_once_with(operations.TICK_KEY)
    client.set.assert_not_called()


@pytest.mark.parametrize('value', [None, {}, [], {'version': 1, 'observed_ts': NOW+1, 'status': 'checked'},
    {'version': 1, 'observed_ts': True, 'status': 'checked'},
    {'version': 1, 'observed_ts': NOW, 'status': '<script>'}])
def test_missing_invalid_or_future_telemetry_never_claims_a_healthy_worker(value):
    client = store(value)
    assert operations.read_tick(client=client, now=NOW) == {'status': 'unavailable', 'observed_at': None}
    client.set.assert_not_called()


@pytest.mark.parametrize('state,result,expected', [('SUCCESS', {'status':'idle'}, 'checked'),
    ('SUCCESS', {'status':'queued'}, 'checked'), ('SUCCESS', {'status':'blocked'}, 'blocked'),
    ('FAILURE', {'status':'idle'}, 'blocked'), ('SUCCESS', None, 'blocked')])
def test_tick_records_only_minimal_expiring_observation(state, result, expected):
    client = Mock()
    operations.record_tick(state, result, client=client, now=NOW)
    args, kwargs = client.set.call_args
    assert args[0] == operations.TICK_KEY and kwargs == {'ex': 86400}
    assert json.loads(args[1]) == {'version': 1, 'observed_ts': NOW, 'status': expected}
    client.get.assert_not_called()


def test_store_outage_never_changes_a_completed_task_result():
    client = Mock()
    client.set.side_effect = client.get.side_effect = RuntimeError('offline')
    assert operations.record_tick('SUCCESS', {'status':'idle'}, client=client, now=NOW) is None
    assert operations.read_tick(client=client, now=NOW)['status'] == 'unavailable'


def test_held_job_observation_batches_root_and_child_fences_without_writing():
    from app.services.studio_state import QUALITY_HOLD_PREFIX, QUALITY_HOLD_JOB_FENCE_PREFIX
    from test_studio_workflow_presentation import job
    rows = [job(0), job(1), job(2), job(3, 'SUCCESS'), job(4, kind='publish')]
    client = Mock(); client.mget.return_value = ['root hold', None, None, 'child fence', None, None]
    assert operations.held_task_ids(rows, client=client) == {rows[0]['task_id'], rows[1]['task_id']}
    client.mget.assert_called_once_with([prefix + row['task_id'] for row in rows[:3]
        for prefix in (QUALITY_HOLD_PREFIX, QUALITY_HOLD_JOB_FENCE_PREFIX)])
    assert len(client.mock_calls) == 1


def test_hold_observation_failure_grants_no_retry_or_publication_authority():
    from test_studio_workflow_presentation import job
    client = Mock(); client.mget.side_effect = RuntimeError('offline')
    assert operations.held_task_ids([job()], client=client) == set()
    assert len(client.mock_calls) == 1


def test_held_failure_remains_reviewable_without_dead_retry_or_voice_actions(ui, monkeypatch):
    from copy import deepcopy
    from test_studio_workflow_presentation import job
    record = job(repair_available=True,
        failure_stage='director_qc', error='included_factual_audit_invalid')
    ui.records[record['task_id']] = record
    before = deepcopy(record)
    monkeypatch.setattr(operations, 'held_task_ids', lambda rows: {record['task_id']})
    ui.ns['_sync_job'] = ui.ns['get_job']
    html = ui.client.get('/studio/job/' + record['task_id'])
    assert html.status_code == 200
    assert 'Bu deneme inceleme için saklanıyor.' in html.text
    assert 'Üretim planını aç' in html.text
    assert f'action="/studio/retry/{record["task_id"]}"' not in html.text
    payload = ui.client.get('/studio/api/job/' + record['task_id']).json()
    assert payload['quality_held'] is True and payload['upload_allowed'] is False
    assert payload['voice_replacement_available'] is False
    assert payload['state'] == 'FAILURE'
    assert payload['display_status'] == 'held' and payload['display_status_label'] == 'Deneme saklandı'
    assert 'bazı iddiaları doğrulanamadı' in payload['ui_status_message']
    assert 'İnceleme için saklandı.' in payload['ui_status_message']
    assert ui.ns['_console_bucket'](payload) == 'archive'
    assert ui.ns['_history_matches'](payload, 'failed') is True
    assert ui.ns['_history_matches'](payload, 'attention') is False
    assert ui.records[record['task_id']] == before
    ui.forbidden.assert_not_called()


def test_held_retry_root_and_child_do_not_look_active_or_await_owner_approval(ui):
    from copy import deepcopy
    from test_studio_workflow_presentation import retry_chain
    rows = retry_chain(1)
    for row in rows:
        row['quality_held'] = True
        ui.records[row['task_id']] = row
    before = deepcopy(ui.records)
    ui.ns['_sync_job'] = ui.ns['get_job']
    for row in rows:
        html = ui.client.get('/studio/job/' + row['task_id'])
        assert html.status_code == 200 and 'Üretim planını aç' in html.text
        assert '>Deneme saklandı</span>' in html.text
        response = ui.client.get('/studio/api/job/' + row['task_id'])
        payload = response.json()
        assert payload['state'] == 'FAILURE' and payload['ui_status'] == 'failed'
        assert payload['display_status'] == 'held' and payload['upload_allowed'] is False
        assert ui.ns['_console_bucket'](row) == 'archive'
        assert 'method="post"' not in ui.ns['_job_primary_action'](row)
    assert ui.records == before
    ui.forbidden.assert_not_called()


CHANNEL = 'UC' + 'a' * 22
ROOT_TASK = '10000000-0000-4000-8000-000000000001'
RETRY_AFTER = '2026-09-22T00:00:00+00:00'


def wait_row(**changes):
    return {'status': 'daily_hold_limit', 'root_task_id': ROOT_TASK,
            'profile_revision': 'current', 'retry_after': RETRY_AFTER, **changes}


def tick_row(**changes):
    return {'version': 1, 'observed_ts': NOW, 'status': 'checked',
            'quality_waits': {CHANNEL: wait_row()}, **changes}


def test_completed_tick_exposes_only_valid_bound_wait_and_no_other_worker_data():
    client = Mock()
    result = {'status': 'idle', 'quality_holds': {'status': 'checked', 'channels': {
        CHANNEL: wait_row(), 'UC' + 'b' * 22: {'status': 'held_unpublished', 'secret': 'private'},
        'UC' + 'c' * 22: wait_row(secret='private'), '<script>': wait_row()}}, 'secret': 'private'}
    operations.record_tick('SUCCESS', result, client=client, now=NOW)
    encoded = client.set.call_args.args[1]
    assert 'private' not in encoded and '<script>' not in encoded
    assert json.loads(encoded) == tick_row()
    assert client.set.call_args.kwargs == {'ex': 86400}
    client.get.assert_not_called()


@pytest.mark.parametrize('age', [0, 180])
def test_wait_requires_same_paused_root_profile_and_fresh_worker(age):
    client = store(tick_row())
    assert operations.read_quality_wait(CHANNEL, 'current', ROOT_TASK, client=client, now=NOW + age) == {
        'status': 'daily_hold_limit', 'retry_after': RETRY_AFTER}
    client.get.assert_called_once_with(operations.TICK_KEY)
    client.set.assert_not_called()


@pytest.mark.parametrize('damage', ['stale', 'future', 'wrong_root', 'wrong_profile', 'wrong_channel',
    'blocked', 'yesterday', 'malformed_time', 'unknown_status', 'extra_field', 'too_many',
    'boolean_version', 'boolean_time', 'missing', 'oversize'])
def test_uncertain_or_obsolete_wait_never_claims_automatic_continuation(damage):
    value, clock, channel, root, revision = tick_row(), NOW, CHANNEL, ROOT_TASK, 'current'
    if damage == 'stale': clock += 181
    if damage == 'future': clock -= 1
    if damage == 'wrong_root': root = ROOT_TASK[:-1] + '2'
    if damage == 'wrong_profile': revision = 'changed'
    if damage == 'wrong_channel': channel = 'UC' + 'b' * 22
    if damage == 'blocked': value['status'] = 'blocked'
    if damage == 'yesterday': value['quality_waits'][CHANNEL]['retry_after'] = '2026-09-21T00:00:00+00:00'
    if damage == 'malformed_time': value['quality_waits'][CHANNEL]['retry_after'] = '<script>'
    if damage == 'unknown_status': value['quality_waits'][CHANNEL]['status'] = 'approved'
    if damage == 'extra_field': value['quality_waits'][CHANNEL]['approved'] = True
    if damage == 'too_many': value['quality_waits'].update({str(i): wait_row() for i in range(8)})
    if damage == 'boolean_version': value['version'] = True
    if damage == 'boolean_time': value['observed_ts'] = True
    if damage == 'missing': value.pop('quality_waits')
    if damage == 'oversize': value['unexpected'] = 'a' * operations.MAX_TICK_BYTES
    client = store(value)
    assert operations.read_quality_wait(channel, revision, root, client=client, now=clock) is None
    client.set.assert_not_called()


def test_midnight_discards_previous_wait_even_if_tick_is_still_fresh():
    from datetime import datetime
    midnight = datetime.fromisoformat(RETRY_AFTER).timestamp()
    client = store(tick_row(observed_ts=midnight - 1))
    assert operations.read_quality_wait(CHANNEL, 'current', ROOT_TASK, client=client, now=midnight) is None
    client.set.assert_not_called()


def test_dashboard_reports_automatic_wait_without_requesting_owner_action(ui):
    metrics = {'channels': [{'channel_id': CHANNEL, 'title': 'Capital',
        'production_status': 'daily_wait', 'production_wait': {
            'status': 'daily_hold_limit', 'retry_after': RETRY_AFTER}}], 'videos': {}}
    ui.ns['_dashboard_metrics'] = lambda _: metrics
    ui.ns['_production_budget_notice'] = lambda: ''
    ui.ns['_operations_status'] = lambda: ''
    response = ui.client.get('/studio')
    assert response.status_code == 200
    assert 'Günlük deneme sınırı · otomatik bekleme' in response.text
    assert '22 Eyl · 03:00 (Türkiye saati)' in response.text
    assert 'Diğer kanallar kendi takvimine göre ilerler' in response.text
    assert 'Otomasyon kontrol bekliyor' not in response.text
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('pause,enabled,expected', [
    ('previous_render_failed', True, 'daily_wait'), ('owner_hold', True, 'paused'),
    ('previous_render_failed', False, 'paused'), ('', True, 'scheduled')])
def test_wait_presentation_uses_current_profile_and_job_without_changing_schedule(ui, monkeypatch, pause, enabled, expected):
    from copy import deepcopy
    from app.services import channel_production, youtube_metrics
    profile = {'channel_id': CHANNEL, 'profile_revision': 'current',
        'production_enabled': enabled, 'auto_publish': True, 'production_topics': ['one', 'two']}
    state = {'paused_reason': pause, 'last_task_id': ROOT_TASK, 'cursor': '1', 'next_due': str(NOW + 3600)}
    before = deepcopy(state)
    metrics = {'channels': [{'channel_id': CHANNEL, 'production_wait': {'status': 'injected'}}], 'videos': {}}
    monkeypatch.setattr(youtube_metrics, 'get_dashboard_metrics', lambda _: deepcopy(metrics))
    monkeypatch.setattr(channel_production, 'get_production_state', lambda _: deepcopy(state))
    lookup = Mock(return_value={'status': 'daily_hold_limit', 'retry_after': RETRY_AFTER})
    monkeypatch.setattr(operations, 'read_quality_wait', lookup)
    monkeypatch.setattr(operations, 'read_series_preparation', lambda *_: None)
    ui.ns['list_channel_profiles'] = lambda: [deepcopy(profile)]
    row = ui.ns['_dashboard_metrics']([])['channels'][0]
    assert row['production_status'] == expected
    if expected == 'daily_wait':
        lookup.assert_called_once_with(CHANNEL, 'current', ROOT_TASK)
        assert row['production_wait']['retry_after'] == RETRY_AFTER
    else:
        lookup.assert_not_called()
        assert 'production_wait' not in row
    assert state == before
    ui.forbidden.assert_not_called()


def test_real_signal_handler_observes_only_the_tick_and_never_replays_it(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'app/production_tasks.py'
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'observe_production_tick')
    assert node.decorator_list
    node.decorator_list = []
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
    record = Mock()
    monkeypatch.setattr(operations, 'record_tick', record)
    ns['observe_production_tick'](sender=SimpleNamespace(name='app.tasks.run_video_pipeline'), state='SUCCESS', retval={})
    record.assert_not_called()
    result = {'status': 'idle'}
    ns['observe_production_tick'](sender=SimpleNamespace(name='app.production_tasks.production_tick'), state='SUCCESS', retval=result)
    record.assert_called_once_with('SUCCESS', result)
    record.side_effect = RuntimeError('offline')
    assert ns['observe_production_tick'](sender=SimpleNamespace(name='app.production_tasks.production_tick')) is None


@pytest.mark.parametrize('revision,day,status,expected', [
    ('current','2026-09-21','failed',{'status':'failed','daily_wait':False,'attempt_number':1}),
    ('current','2026-09-20','failed',{'status':'failed','daily_wait':False,'attempt_number':1}),
    ('old','2026-09-21','ready',None), ('current','2026-09-22','ready',None),
    ('current','invalid','failed',None), ('current','2026-09-21','injected',None)])
def test_planner_status_is_bound_to_current_profile_and_utc_day(revision, day, status, expected):
    client = store({'channel_id':'UC_fixture','profile_revision':revision,'day':day,'status':status,'secret':'never exposed'})
    assert operations.read_series_preparation('UC_fixture','current',client=client,now=NOW) == expected
    client.set.assert_not_called()


def test_dashboard_explicitly_shows_stale_worker_and_failed_series(ui, monkeypatch):
    monkeypatch.setattr(operations, 'read_tick', lambda: {'status':'stale','observed_at':'2026-09-21T06:00:00Z'})
    assert 'Sunucudan güncel otomasyon sinyali gelmedi' in ui.ns['_operations_status']()
    html = ui.ns['_channel_overview']([{'channel_id':'UC_fixture','title':'Margin',
        'production_status':'exhausted','remaining_topics':0,
        'series_preparation':{'status':'failed','daily_wait':True,'attempt_number':3}}])
    assert 'Yeni konu planı hazırlanamadı' in html and 'Günlük deneme sınırı bekleniyor' in html
    assert 'Planlama denemesi 3/3' in html
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('slot,expected', [(1,False), (2,False), (3,True), (4,None), (True,None)])
def test_same_day_failure_waits_for_tomorrow_only_after_third_attempt(slot, expected):
    client = store({'channel_id':'UC_fixture','profile_revision':'current','day':'2026-09-21',
                    'status':'uncertain','preparation_slot':slot})
    result = operations.read_series_preparation('UC_fixture','current',client=client,now=NOW)
    assert result is None if expected is None else result['daily_wait'] is expected
    client.set.assert_not_called()
