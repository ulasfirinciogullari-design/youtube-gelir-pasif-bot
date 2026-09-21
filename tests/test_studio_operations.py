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
