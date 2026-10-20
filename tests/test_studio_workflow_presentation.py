"""Read-only Studio presentation; no provider, queue or live storage calls."""
import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
COOKIE = 'studio-fixture'
NOW = 1_800_000_000.0


def task_id(index):
    return str(UUID(int=index + 1))


def job(index=0, state='FAILURE', **fields):
    return {
        'task_id': task_id(index), 'kind': 'render', 'state': state,
        'stage': 'failed' if state == 'FAILURE' else 'complete',
        'updated_ts': NOW - 10, 'progress': 100 if state == 'SUCCESS' else 30,
        'spec': {'topic': 'Banknot kâğıdının hikâyesi', 'language': 'tr',
                 'production_channel_id': 'channel', 'production_profile_revision': 'frozen'},
        **fields,
    }


def ready(index=0):
    return job(index, 'SUCCESS', result={
        'video_key': 'private/final.mp4', 'download_url': 'https://media.example/final.mp4',
        'quality_disposition': 'automated_qc_pass', 'manual_qa_required': False,
    })


def retry_chain(hops=2, final_state='FAILURE'):
    jobs = [job(i) for i in range(hops + 1)]
    for parent, child in zip(jobs, jobs[1:]):
        parent.update(retry_child_task_id=child['task_id'], retry_claimed=True)
        child['parent_id'] = parent['task_id']
    jobs[-1]['state'] = final_state
    jobs[-1]['stage'] = 'complete' if final_state == 'SUCCESS' else 'failed'
    if final_state == 'SUCCESS':
        jobs[-1]['result'] = ready()['result']
    return jobs


@pytest.fixture
def ui(monkeypatch):
    source = ROOT / 'app' / 'studio.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and (node.module or '').startswith('app.')
    )]
    records = {}
    lookup = Mock(side_effect=lambda value: deepcopy(records.get(value)))
    ledger_lookup = Mock(return_value=None)
    forbidden = Mock(side_effect=AssertionError('Presentation must not write or enqueue'))
    namespace = {
        'settings': SimpleNamespace(factory_api_token=COOKIE),
        'get_job': lookup, 'list_jobs': lambda _limit: deepcopy(list(records.values())),
        '_stored_get_job': lookup, '_stored_list_jobs': lambda _limit: deepcopy(list(records.values())),
        'get_upload_record': ledger_lookup,
        'youtube_router': APIRouter(), 'update_job': forbidden,
        'create_job': forbidden, 'mark_success': forbidden, 'mark_failure': forbidden,
        'run_video_pipeline': SimpleNamespace(delay=forbidden, apply_async=forbidden),
    }
    from app.services import studio_operations
    monkeypatch.setattr(studio_operations, 'held_task_ids', lambda rows: {
        row['task_id'] for row in rows if records.get(row.get('task_id'), {}).get('quality_held') is True})
    exec(compile(tree, str(source), 'exec'), namespace)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(NOW, tz=tz)

    namespace['datetime'] = FixedDateTime
    namespace['_sync_job'] = lookup
    app = FastAPI()
    app.include_router(namespace['router'])
    client = TestClient(app)
    client.cookies.set('youtube_studio_token', COOKIE)
    return SimpleNamespace(ns=namespace, records=records, lookup=lookup,
                           ledger_lookup=ledger_lookup, forbidden=forbidden, client=client)


@pytest.mark.parametrize('age,expected', [
    (0, True), (1, True), (86399, True), (86400, False), (86401, False), (-1, False),
])
def test_recent_failure_exact_24_hour_boundary(ui, age, expected):
    record = job(updated_ts=NOW-age)
    assert ui.ns['_job_is_recent_failed_leaf'](record) is expected
    assert ui.ns['_console_bucket'](record) == ('attention' if expected else 'archive')
    assert ui.ns['_history_matches'](record, 'failed') is not expected
    assert ui.ns['_archive_counts']([record])['failed'] == int(not expected)
    assert 'method="post"' in ui.ns['_job_primary_action'](record)


@pytest.mark.parametrize('fields', [
    {'retry_claimed': True}, {'repair_claimed': True}, {'retry_child_task_id': task_id(2)},
    {'state': 'SUCCESS'}, {'kind': 'publish'}, {'updated_ts': 'invalid'},
    {'updated_ts': float('nan')}, {'updated_ts': None},
])
def test_recent_failure_requires_leaf_and_valid_time(ui, fields):
    assert not ui.ns['_job_is_recent_failed_leaf'](job(**fields))


def test_recent_failure_uses_activity_time_not_original_retry_age(ui):
    record = job(created_ts=NOW-3*86400, updated_ts=None,
                 updated_at=datetime.fromtimestamp(NOW-60, timezone.utc).isoformat())
    assert ui.ns['_job_is_recent_failed_leaf'](record)


def test_recent_failures_visible_in_attention_without_deleting_old_records(ui):
    recent = job(0)
    old = job(1, updated_ts=NOW-2*86400, spec={'topic': 'Önceki video'})
    ui.records.update({j['task_id']: j for j in [recent, old]})
    before = deepcopy(ui.records)
    response = ui.client.get('/studio/history?status=attention')
    assert response.status_code == 200
    assert 'Banknot kâğıdının hikâyesi' in response.text
    assert 'Önceki video' not in response.text
    assert 'data-status-count="attention">1</span>' in response.text
    assert 'Başarısız denemeler <b>1</b>' in response.text
    archived = ui.client.get('/studio/history?status=failed')
    assert 'Önceki video' in archived.text
    assert ui.records == before
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('hops', [1, 2, 16])
@pytest.mark.parametrize('state', ['SUCCESS', 'FAILURE', 'AWAITING_APPROVAL'])
def test_terminal_retry_only_borrows_verified_terminal_summary(ui, hops, state):
    records = retry_chain(hops, state)
    ui.records.update({r['task_id']: r for r in records})
    before = deepcopy(records)
    summary = ui.ns['_terminal_retry_presentation'](records[0], ui.lookup)
    assert summary['task_id'] == records[-1]['task_id']
    assert summary['hops'] == hops
    assert summary['ui_status'] != 'running'
    assert set(summary) == {'task_id', 'hops', 'ui_status', 'display_status', 'stage_label', 'message'}
    assert ui.lookup.call_count == hops
    assert records == before
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('damage', [
    'missing', 'wrong_parent', 'wrong_kind', 'wrong_id', 'cycle', 'invalid_id',
    'noncanonical_id', 'live_leaf', 'claimed_without_child', 'intermediate_success',
    'too_many', 'lookup_error',
])
def test_retry_chain_fails_closed_on_unverified_or_nonterminal_edges(ui, damage):
    records = retry_chain(17 if damage == 'too_many' else 2)
    if damage == 'wrong_parent': records[-1]['parent_id'] = task_id(90)
    if damage == 'wrong_kind': records[-1]['kind'] = 'publish'
    if damage == 'wrong_id': records[-1]['task_id'] = task_id(90)
    if damage == 'cycle': records[-1]['retry_child_task_id'] = records[0]['task_id']
    if damage == 'invalid_id': records[0]['retry_child_task_id'] = '../../wrong'
    if damage == 'noncanonical_id': records[0]['retry_child_task_id'] = records[1]['task_id'].replace('-', '')
    if damage == 'live_leaf': records[-1]['state'] = 'PROGRESS'
    if damage == 'claimed_without_child': records[-1]['retry_claimed'] = True
    if damage == 'intermediate_success': records[1]['state'] = 'SUCCESS'
    ui.records.update({r['task_id']: r for r in records})
    if damage == 'missing': ui.records.pop(records[-1]['task_id'])
    if damage == 'lookup_error': ui.lookup.side_effect = RuntimeError('unavailable')
    assert ui.ns['_terminal_retry_presentation'](records[0], ui.lookup) is None
    assert ui.lookup.call_count <= 16
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('state,expected', [('SUCCESS', 'ready'), ('FAILURE', 'failed')])
def test_original_detail_and_poll_preserve_original_evidence_with_direct_latest_link(ui, state, expected):
    records = retry_chain(3, state)
    original = records[0]
    original['error'] = 'Önceki ses kontrolü tamamlanamadı'
    original['failure_stage'] = 'audio_qc'
    ui.records.update({r['task_id']: r for r in records})
    before = deepcopy(ui.records)
    response = ui.client.get(f'/studio/job/{original["task_id"]}', follow_redirects=False)
    assert response.status_code == 200
    assert 'location' not in response.headers
    assert 'Bu sayfa önceki denemenin kaydıdır.' in response.text
    assert f'href="/studio/job/{records[-1]["task_id"]}">Güncel sonucu aç' in response.text
    assert original['error'] in response.text and f'<code>{original["task_id"]}</code>' in response.text
    assert '<code id="technical-stage">audio_qc</code>' in response.text
    body_without_js = response.text.split('<script>')[0]
    assert 'method="post"' not in body_without_js
    assert '<video' not in body_without_js  # Never lend child media/QA to ancestor.
    payload = ui.client.get(f'/studio/api/job/{original["task_id"]}').json()
    assert payload['state'] == 'FAILURE'
    assert payload['spec'] == original['spec']
    assert payload['error'] == original['error']
    assert payload['ui_status'] == expected
    assert payload['upload_allowed'] is False
    assert payload['retry_presentation']['task_id'] == records[-1]['task_id']
    assert 'result' not in payload
    assert ui.records == before
    ui.forbidden.assert_not_called()


def test_detail_poll_handles_summary_and_publication_before_any_retry_form(ui):
    ui.records[task_id(0)] = job()
    script = ui.client.get(f'/studio/job/{task_id(0)}').text.split('async function poll(){', 1)[1]
    assert script.index('if(j.retry_presentation)') < script.index("if(ui==='repair')")
    assert script.index('if(j.publication_status)') < script.index("if(ui==='failed')")
    assert 'window.location' not in script


@pytest.mark.parametrize('path', [f'/studio/job/{task_id(0)}', f'/studio/api/job/{task_id(0)}'])
def test_presentation_reads_remain_owner_authenticated(ui, path):
    ui.client.cookies.clear()
    assert ui.client.get(path).status_code == 401
    ui.lookup.assert_not_called()
    ui.ledger_lookup.assert_not_called()
    ui.forbidden.assert_not_called()


def publisher(source, index=5, state='FAILURE'):
    return job(index, state, kind='publish', parent_id=source['task_id'],
               spec={'source_task_id': source['task_id'], 'privacy_status': 'private',
                     'target_channel_id': 'channel', 'connection_id': 'connection',
                     'profile_revision': 'frozen'})


@pytest.mark.parametrize('status', [
    'quality_blocked', 'no_unique_route', 'connection_missing', 'connection_changed',
    'profile_changed', 'metadata_blocked', 'reservation_blocked', 'queue_blocked',
    'queue_error', 'preflight_failed', 'failed_preflight', 'uncertain',
])
def test_publication_errors_show_attention_and_review_never_retry_or_upload(ui, status):
    source = ready()
    source['result']['youtube_automation'] = {'status': status}
    before = deepcopy(source)
    assert ui.ns['_console_bucket'](source) == 'attention'
    assert ui.ns['_job_quality_approved'](source) is True
    assert ui.ns['_job_upload_allowed'](source) is False
    action = ui.ns['_job_primary_action'](source)
    assert 'Mevcut yüklemeyi kontrol et' in action
    assert 'href="/studio/youtube"' in action
    assert 'method="post"' not in action and 'Gizli yükle' not in action
    assert 'yüklemeye hazır' not in ui.ns['_job_status_message'](source)
    assert source == before


@pytest.mark.parametrize('release', ['blocked', 'uncertain'])
def test_completed_private_video_with_release_problem_moves_to_attention(ui, release):
    source = ready()
    source['result']['youtube'] = {'video_id': 'existing-video', 'privacy_status': 'private',
                                   'release_status': release}
    assert ui.ns['_console_bucket'](source) == 'attention'
    assert ui.ns['_job_ui_status'](source) == 'completed'
    assert ui.ns['_ready_privacy_label'](source) == 'Gizli'
    assert ui.ns['_job_upload_allowed'](source) is False


@pytest.mark.parametrize('release', ['private', 'scheduled', 'public'])
def test_successful_publication_keeps_current_library_and_privacy(ui, release):
    source = ready()
    source['result']['youtube'] = {'video_id': 'existing-video', 'release_status': release,
                                   'privacy_status': 'public' if release == 'public' else 'private'}
    assert ui.ns['_console_bucket'](source) == 'library'
    assert ui.ns['_publication_status'](source) == ''
    assert ui.ns['_job_upload_allowed'](source) is False


def test_failed_publisher_rolls_up_to_source_keeps_preview_and_existing_record_link(ui):
    source = ready()
    child = publisher(source)
    source['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': child['task_id']}
    records = [source, child]
    before = deepcopy(records)
    collapsed = ui.ns['_collapse_retry_sources'](records)
    assert len(collapsed) == 1 and collapsed[0]['task_id'] == source['task_id']
    assert ui.ns['_console_bucket'](collapsed[0]) == 'attention'
    assert 'Yüklemeyi kontrol et' in ui.ns['_ready_video_card'](collapsed[0])
    assert f'/studio/youtube/publish-status/{child["task_id"]}' in ui.ns['_job_primary_action'](collapsed[0])
    assert records == before
    ui.records.update({r['task_id']: r for r in records})
    response = ui.client.get('/studio/history?status=attention')
    assert response.status_code == 200
    assert '<video controls playsinline' in response.text
    assert 'https://media.example/final.mp4' in response.text
    assert 'data-status-count="attention">1</span>' in response.text
    detail = ui.client.get(f'/studio/job/{source["task_id"]}')
    assert 'Mevcut yüklemeyi kontrol et' in detail.text
    assert '<video class="result-video"' in detail.text
    payload = ui.client.get(f'/studio/api/job/{source["task_id"]}').json()
    assert payload['publication_status'] == 'failed'
    assert payload['publication_review_path'] == f'/studio/youtube/publish-status/{child["task_id"]}'
    assert payload['upload_allowed'] is False
    assert payload['result'] == source['result']
    assert payload['spec'] == source['spec']
    assert ui.records == {r['task_id']: r for r in before}
    ui.forbidden.assert_not_called()


def test_manual_failed_publish_rollup_requires_both_parent_and_source(ui):
    source = ready()
    child = publisher(source)
    collapsed = ui.ns['_collapse_retry_sources']([source, child])
    assert ui.ns['_console_bucket'](collapsed[0]) == 'attention'
    child['spec']['source_task_id'] = task_id(90)
    collapsed = ui.ns['_collapse_retry_sources']([source, child])
    assert ui.ns['_console_bucket'](collapsed[0]) == 'library'


@pytest.mark.parametrize('damage', ['missing', 'wrong_parent', 'wrong_source', 'wrong_id', 'exception'])
def test_bad_publisher_binding_never_borrows_status_or_direct_link(ui, damage):
    source = ready()
    child = publisher(source)
    source['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': child['task_id']}
    if damage == 'wrong_parent': child['parent_id'] = task_id(90)
    if damage == 'wrong_source': child['spec']['source_task_id'] = task_id(90)
    if damage == 'wrong_id': child['task_id'] = task_id(90)
    ui.lookup.side_effect = RuntimeError('private') if damage == 'exception' else None
    ui.lookup.return_value = None if damage == 'missing' else child
    view = ui.ns['_with_publication_presentation'](source, ui.lookup)
    assert ui.ns['_publication_status'](view) == 'uncertain'
    assert ui.ns['_job_upload_allowed'](view) is False
    assert '_publication_task_id' not in view
    assert 'publish-status' not in ui.ns['_job_primary_action'](view)
    ui.lookup.assert_called_once()


def test_old_failed_publisher_does_not_override_explicit_current_publisher(ui):
    source = ready()
    old = publisher(source, 5)
    current = publisher(source, 6, 'SUCCESS')
    source['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': current['task_id']}
    source['result']['youtube'] = {'video_id': 'existing', 'release_status': 'private'}
    collapsed = ui.ns['_collapse_retry_sources']([source, old, current])
    assert len(collapsed) == 1
    assert ui.ns['_console_bucket'](collapsed[0]) == 'library'


def test_pending_publisher_disables_duplicate_upload_action_without_failure_claim(ui):
    source = ready()
    child = publisher(source, state='PROGRESS')
    source['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': child['task_id']}
    ui.lookup.return_value = child
    ui.lookup.side_effect = None
    view = ui.ns['_with_publication_presentation'](source, ui.lookup)
    assert ui.ns['_publication_status'](view) == 'pending'
    assert ui.ns['_job_upload_allowed'](view) is False
    assert 'Gizli yükle' not in ui.ns['_job_primary_action'](view)
    assert f'/studio/youtube/publish-status/{child["task_id"]}' in ui.ns['_job_primary_action'](view)
    assert ui.ns['_job_display_status'](view) != 'attention'


def test_failed_publisher_own_detail_never_offers_render_retry(ui):
    child = publisher(ready())
    action = ui.ns['_job_primary_action'](child)
    assert 'Mevcut yüklemeyi kontrol et' in action
    assert 'method="post"' not in action
    assert '/studio/retry/' not in action


def upload_ledger(source, child):
    return {
        'source_task_id': source['task_id'], 'publish_task_id': child['task_id'],
        'target_channel_id': 'channel', 'connection_id': 'connection',
        'status': 'failed_preflight', 'publish_plan': {'profile_revision': 'frozen'},
    }


@pytest.mark.parametrize('api', [False, True])
def test_manual_upload_detail_uses_one_bound_read_only_ledger_lookup(ui, api):
    source = ready()
    child = publisher(source)
    source['spec']['production_connection_id'] = 'connection'
    ledger = upload_ledger(source, child)
    before = deepcopy(ledger)
    ui.ledger_lookup.return_value = ledger
    ui.records.update({j['task_id']: j for j in [source, child]})
    path = f'/studio/{"api/" if api else ""}job/{source["task_id"]}'
    response = ui.client.get(path)
    assert response.status_code == 200
    assert f'/studio/youtube/publish-status/{child["task_id"]}' in response.text
    if api:
        assert response.json()['display_status'] == 'attention'
        assert response.json()['upload_allowed'] is False
    else:
        assert '<video class="result-video"' in response.text
    ui.ledger_lookup.assert_called_once_with(source['task_id'])
    assert ui.lookup.call_count == 2  # source then exact ledger-bound publisher
    assert ledger == before
    ui.forbidden.assert_not_called()


@pytest.mark.parametrize('damage', [
    'source', 'publish_id', 'channel', 'connection', 'revision', 'missing_plan',
    'missing_channel', 'missing_connection', 'publisher_channel', 'publisher_connection',
    'publisher_revision', 'publisher_missing_revision',
    'attribution_channel', 'attribution_connection', 'exception',
])
def test_manual_ledger_binding_corruption_never_borrows_another_upload(ui, damage):
    source = ready()
    source['spec']['production_connection_id'] = 'connection'
    child = publisher(source)
    ledger = upload_ledger(source, child)
    if damage == 'source': ledger['source_task_id'] = task_id(90)
    if damage == 'publish_id': ledger['publish_task_id'] = '../other'
    if damage == 'channel': ledger['target_channel_id'] = 'other'
    if damage == 'connection': ledger['connection_id'] = 'other'
    if damage == 'revision': ledger['publish_plan']['profile_revision'] = 'changed'
    if damage == 'missing_plan': ledger['publish_plan'] = None
    if damage == 'missing_channel': ledger.pop('target_channel_id')
    if damage == 'missing_connection': ledger.pop('connection_id')
    if damage == 'publisher_channel': child['spec']['target_channel_id'] = 'other'
    if damage == 'publisher_connection': child['spec']['connection_id'] = 'other'
    if damage == 'publisher_revision': child['spec']['profile_revision'] = 'other'
    if damage == 'publisher_missing_revision': child['spec'].pop('profile_revision')
    if damage == 'attribution_channel': source['result']['youtube'] = {'target_channel_id': 'other'}
    if damage == 'attribution_connection': source['result']['youtube'] = {'connection_id': 'other'}
    ui.ledger_lookup.return_value = ledger
    if damage == 'exception': ui.ledger_lookup.side_effect = RuntimeError('private details')
    ui.records.update({j['task_id']: j for j in [source, child]})
    payload = ui.client.get(f'/studio/api/job/{source["task_id"]}').json()
    assert payload['publication_status'] == 'uncertain'
    assert payload['publication_review_path'] == '/studio/youtube'
    assert payload['upload_allowed'] is False
    ui.ledger_lookup.assert_called_once()
    assert ui.lookup.call_count == (2 if damage.startswith('publisher_') else 1)
    assert 'private details' not in str(payload)
    ui.forbidden.assert_not_called()


def test_history_cards_never_read_individual_upload_ledgers(ui):
    source = ready()
    child = publisher(source)
    ui.records.update({j['task_id']: j for j in [source, child]})
    assert ui.client.get('/studio/history?status=attention').status_code == 200
    ui.ledger_lookup.assert_not_called()
    ui.lookup.assert_not_called()


@pytest.mark.parametrize('record', [job(), job(state='PENDING'), ready(index=4) | {'task_id': 'legacy-id'}])
def test_ledger_lookup_only_for_canonical_successful_render(ui, record):
    ui.records[record['task_id']] = record
    assert ui.client.get(f'/studio/job/{record["task_id"]}').status_code == 200
    ui.ledger_lookup.assert_not_called()


def test_automatic_publisher_pointer_needs_no_fallback_ledger_lookup(ui):
    source = ready()
    child = publisher(source)
    source['result']['youtube_automation'] = {'status': 'queued', 'publish_task_id': child['task_id']}
    ui.records.update({j['task_id']: j for j in [source, child]})
    payload = ui.client.get(f'/studio/api/job/{source["task_id"]}').json()
    assert payload['publication_status'] == 'failed'
    ui.ledger_lookup.assert_not_called()


@pytest.mark.parametrize('damage', [None, 'video_id', 'target_channel_id', 'connection_id', 'source_task_id'])
def test_manual_failure_then_verified_success_does_not_leave_obsolete_attention(ui, damage):
    source = ready()
    old = publisher(source, 5)
    current = publisher(source, 6, 'SUCCESS')
    source['result']['youtube'] = {
        'video_id': 'existing-video', 'target_channel_id': 'channel',
        'connection_id': 'connection', 'release_status': 'private', 'privacy_status': 'private',
    }
    current['result'] = {
        'source_task_id': source['task_id'], 'youtube_video_id': 'existing-video',
        'target_channel_id': 'channel', 'connection_id': 'connection', 'release_status': 'private',
    }
    if damage:
        current['result']['youtube_video_id' if damage == 'video_id' else damage] = 'different'
    records = [source, old, current]
    before = deepcopy(records)
    collapsed = ui.ns['_collapse_retry_sources'](records)
    assert len(collapsed) == 1
    assert ui.ns['_console_bucket'](collapsed[0]) == ('attention' if damage else 'library')
    assert records == before
    ui.ledger_lookup.assert_not_called()


def test_manual_obsolete_upload_failure_does_not_hide_current_release_warning(ui):
    source = ready()
    old = publisher(source, 5)
    current = publisher(source, 6, 'SUCCESS')
    source['result']['youtube'] = {
        'video_id': 'existing', 'target_channel_id': 'channel', 'connection_id': 'connection',
        'release_status': 'uncertain', 'privacy_status': 'private',
    }
    current['result'] = {
        'source_task_id': source['task_id'], 'youtube_video_id': 'existing',
        'target_channel_id': 'channel', 'connection_id': 'connection', 'release_status': 'private',
    }
    collapsed = ui.ns['_collapse_retry_sources']([source, old, current])
    assert ui.ns['_console_bucket'](collapsed[0]) == 'attention'
    assert ui.ns['_publication_status'](collapsed[0]) == 'uncertain'


@pytest.mark.parametrize('old_status', ['no_unique_route', 'queue_error'])
@pytest.mark.parametrize('release', ['private', 'blocked', 'uncertain'])
def test_verified_manual_completion_supersedes_old_queue_warning_not_release_problem(ui, old_status, release):
    source = ready()
    child = publisher(source, state='SUCCESS')
    source['result']['youtube_automation'] = {'status': old_status}
    source['result']['youtube'] = {
        'video_id': 'existing', 'target_channel_id': 'channel', 'connection_id': 'connection',
        'release_status': release, 'privacy_status': 'private',
    }
    child['result'] = {
        'source_task_id': source['task_id'], 'youtube_video_id': 'existing',
        'target_channel_id': 'channel', 'connection_id': 'connection', 'release_status': release,
    }
    ledger = {**upload_ledger(source, child), 'status': 'complete',
              'youtube_video_id': 'existing', 'release_status': release}
    ui.ledger_lookup.return_value = ledger
    ui.records.update({j['task_id']: j for j in [source, child]})
    before = deepcopy(ui.records)
    collapsed = ui.ns['_collapse_retry_sources']([source, child])
    expected = 'library' if release == 'private' else 'attention'
    assert ui.ns['_console_bucket'](collapsed[0]) == expected
    payload = ui.client.get(f'/studio/api/job/{source["task_id"]}').json()
    assert payload['publication_status'] == ('' if release == 'private' else release)
    assert payload['upload_allowed'] is False
    assert payload['result']['youtube_automation']['status'] == old_status
    assert ui.records == before
    ui.forbidden.assert_not_called()
