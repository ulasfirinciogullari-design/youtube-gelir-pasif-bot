from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from uuid import uuid4

import fakeredis
import pytest

from app.services import content_plan as plan, content_plan_attention as attention
from app.services import channel_cadence as cadence, studio_state as jobs
from app.services import commissioning_reasoning as reasoning, commissioning_video as video
from app.services.youtube_publish_state import UPLOAD_PREFIX
from test_content_plan import CHANNEL, OTHER


@pytest.fixture
def case(monkeypatch):
    c = fakeredis.FakeRedis(decode_responses=True); monkeypatch.setattr(plan, '_client', lambda: c)
    previous = plan.item('Bitmiş seri', 'Tamamlanmış seri bölümü')
    item = plan.item('Bağımsız belgesel', 'Kaynaklı yeni uzun film', 'long', depends_on=[previous['id']])
    task = str(uuid4()); now = datetime.now(timezone.utc).isoformat()
    spec = {'content_plan_item_id': item['id'], 'production_channel_id': CHANNEL,
        'mode': 'production', 'format': 'landscape', 'duration_minutes': 3}
    source = {'task_id': task, 'kind': 'render', 'parent_id': None, 'state': 'FAILURE', 'spec': spec,
        'failure_stage': 'director_qc', 'error': 'included_factual_audit_invalid', 'paid_create_slots_used': 0}
    dispatch = {'task_id': task, 'channel_id': CHANNEL, 'item': item,
        'profile_revision': 'current-profile', 'spec_sha256': plan._sha(spec)}
    doc = {'version': 1, 'channel_id': CHANNEL, 'revision': str(uuid4()), 'enabled': True,
        'after_queue': 'auto_shorts', 'items': [previous, item], 'updated_at': now}
    terminal = {'status': 'FAILURE', 'task_id': task, 'date_done': now,
        'result': {'exc_type': 'ValueError', 'exc_message': [source['error']]},
        'traceback': 'File "/app/app/tasks.py", line 1, in run_video_pipeline\n'}
    for k, v in {plan.PLAN_PREFIX + CHANNEL: doc, plan.DISPATCH_PREFIX + item['id']: dispatch,
        jobs.JOB_PREFIX + task: source, plan.ACTIVE_KEY: {CHANNEL: item['id'], OTHER: str(uuid4())},
        'celery-task-meta-' + task: terminal, plan.COMPLETION_PREFIX + previous['id']: {
            'url': 'https://www.youtube.com/watch?v=abcdefghijk', 'source_task_id': str(uuid4())},
        plan.production.PROFILE_PREFIX + CHANNEL: {'production_enabled': True, 'auto_publish': True,
            'release_mode': 'public', 'profile_revision': 'current-profile'}}.items():
        c.set(k, plan._raw(v))
    c.hset(cadence.keys(CHANNEL)[0], task, 'long')
    c.set('previous_paid_media_receipt', 'immutable'); c.set('previous_unknown_financial_hold', 'immutable')
    return c, source, doc, item, previous


def dump(c): return {k: c.dump(k) for k in c.scan_iter()}


def test_terminal_film_moves_to_attention_without_fake_completion_or_releasing_daily_capacity(case):
    c, source, doc, item, previous = case
    before = dump(c); active = json.loads(c.get(plan.ACTIVE_KEY))
    assert attention.isolate(source, client=c, observe_only=True)['state_writes'] == 0
    assert dump(c) == before
    assert attention.isolate(source, client=c) == 'attention_archived'
    assert plan.read(CHANNEL)['items'] == [previous]
    assert not plan.owns_channel(CHANNEL)
    assert json.loads(c.get(plan.ACTIVE_KEY)) == {OTHER: active[OTHER]}
    assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
    assert cadence.snapshot(CHANNEL, client=c)['counts']['produced']['long'] == 1
    assert cadence.daily_editorial(CHANNEL, {}, client=c)['format'] == 'shorts'
    for key, value in before.items():
        if key not in {plan.PLAN_PREFIX + CHANNEL, plan.ACTIVE_KEY}: assert c.dump(key) == value
    record = json.loads(c.get(attention.PREFIX + item['id']))
    assert record['original_plan'] == plan._raw(doc)
    assert record['original_jobs'][source['task_id']] == plan._raw(source)
    assert not record['qa_approved'] and not record['publish_eligible']
    assert plan.project(plan.read(CHANNEL))['attention'][0]['task_id'] == source['task_id']
    stable = dump(c)
    assert attention.isolate(source, client=c) == 'attention_archived'
    assert dump(c) == stable


@pytest.mark.parametrize('damage', ['live', 'unknown_terminal', 'wrong_terminal', 'cancel', 'upload',
    'child', 'retry_claim', 'owner_hold', 'disabled', 'series', 'dependent', 'unpublished_predecessor',
    'profile_changed', 'audio_exists', 'unknown_model', 'unknown_video'])
def test_unfinished_unknown_or_order_sensitive_work_never_moves(case, damage):
    c, source, doc, item, previous = case; task = source['task_id']
    if damage == 'live': source['state'] = 'PROGRESS'
    if damage == 'unknown_terminal': c.delete('celery-task-meta-' + task)
    if damage == 'wrong_terminal':
        terminal = json.loads(c.get('celery-task-meta-' + task)); terminal['task_id'] = str(uuid4())
        c.set('celery-task-meta-' + task, plan._raw(terminal))
    if damage == 'cancel': c.set(jobs.RENDER_CANCELLATION_PREFIX + task, 'owner')
    if damage == 'upload': c.set(UPLOAD_PREFIX + task, plan._raw({'status': 'uncertain'}))
    if damage == 'child': source['retry_child_task_id'] = str(uuid4())
    if damage == 'retry_claim': c.set(jobs.RETRY_DISPATCH_PREFIX + task, 'uncertain')
    if damage == 'owner_hold': source['publication_hold'] = {'reason': 'owner'}
    if damage == 'disabled': doc['enabled'] = False
    if damage == 'series': item['series'] = {'id': 'series-1', 'name': 'Series', 'number': 1, 'total': 2}
    if damage == 'dependent': doc['items'].append(plan.item('Sonraki', 'Devam bölümü', depends_on=[item['id']]))
    if damage == 'unpublished_predecessor': c.delete(plan.COMPLETION_PREFIX + previous['id'])
    if damage == 'profile_changed': c.set(plan.production.PROFILE_PREFIX + CHANNEL, plan._raw({'profile_revision': 'new'}))
    if damage == 'audio_exists': source['audio_candidate_checkpoint'] = {'actual': True}
    if damage == 'unknown_model':
        c.sadd(reasoning.PREFIX + 'lineage:' + task, 'unknown-intent')
        c.set(reasoning.PREFIX + 'request:unknown-intent', plan._raw({'context': {'lineage_id': task}}))
    if damage == 'unknown_video':
        c.set(video.PREFIX + task, plan._raw({'context': {'lineage_id': task},
            'requests': {'native': {'create': {'http_status': 200}, 'result': None}}}))
    c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(doc)); c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    before = dump(c)
    try: assert attention.isolate(source, client=c) is None
    except (plan.ContentPlanError, ValueError, TypeError): pass
    assert dump(c) == before


def test_source_changes_during_archive_transaction_leave_original_plan_and_all_history(case, monkeypatch):
    from redis.exceptions import WatchError
    c, source, doc, item, _ = case
    execute = type(c.pipeline()).execute
    def change_before_commit(pipe, *args, **kwargs):
        c.set(jobs.RENDER_CANCELLATION_PREFIX + source['task_id'], 'owner-cancel')
        return execute(pipe, *args, **kwargs)
    monkeypatch.setattr(type(c.pipeline()), 'execute', change_before_commit)
    with pytest.raises(WatchError): attention.isolate(source, client=c)
    assert plan.read(CHANNEL) == doc and not c.exists(attention.PREFIX + item['id'])
    assert c.get(jobs.JOB_PREFIX + source['task_id']) == plan._raw(source)


def test_numbered_animation_channel_is_out_of_scope(case):
    c, source, *_ = case
    source['spec']['production_channel_id'] = 'UCs93z6wf134H5_BL9pkQX4Q'
    before = dump(c); assert attention.isolate(source, client=c) is None; assert dump(c) == before


def test_exhausted_retained_visual_attempt_keeps_actual_private_workprint_and_all_ancestors(case):
    from test_qa_workprint_access import pointer
    c, source, _, item, _ = case; root = source['task_id']; leaf_id = str(uuid4())
    leaf = deepcopy(source); leaf.update(task_id=leaf_id, parent_id=root,
        failure_stage='final_visual_qc_rescue', error='Final visual quality gate rejected: failed scenes',
        paid_create_slots_used=11, retained_long_media={'stock_only': True, 'new_tts_requests': 0})
    leaf['failure_classification'] = {'category': 'content_rejected', 'code': 'visual_quality_exhausted',
        'error_sha256': hashlib.sha256(leaf['error'].encode()).hexdigest()}
    p = pointer(); old = p['task_id']
    p.update(task_id=leaf_id, version=4, width=1920, height=1080, duration_seconds=180., frame_count=5400)
    p['key'] = p['key'].replace(old, leaf_id); p['metadata_key'] = p['metadata_key'].replace(old, leaf_id)
    leaf['qa_workprint'] = p
    source['retry_child_task_id'] = leaf_id
    terminal = json.loads(c.get('celery-task-meta-' + root))
    terminal.update(task_id=leaf_id, result={'exc_type': 'FinalVisualQualityError', 'exc_message': [leaf['error']]})
    c.set('celery-task-meta-' + leaf_id, plan._raw(terminal))
    c.set(jobs.JOB_PREFIX + root, plan._raw(source)); c.set(jobs.JOB_PREFIX + leaf_id, plan._raw(leaf))
    assert attention.isolate(leaf, client=c) == 'attention_archived'
    assert json.loads(c.get(jobs.JOB_PREFIX + leaf_id)) == leaf
    assert json.loads(c.get(jobs.JOB_PREFIX + root)) == source
    assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
    assert len(json.loads(c.get(attention.PREFIX + item['id']))['original_jobs']) == 2


def test_large_existing_router_journal_is_preserved_and_unrelated_unknowns_do_not_block(case):
    from app.services import production_included_router as included
    c, source, *_ = case
    journal = {'requests': {'old': {'context': {'lineage_id': 'another-root'},
        'outcome': None, 'retained_history': 'x' * 600000}}}
    original = plan._raw(journal); c.set(included.JOURNAL_KEY, original)
    assert attention.isolate(source, client=c) == 'attention_archived'
    assert c.get(included.JOURNAL_KEY) == original


@pytest.mark.parametrize('damage', [None, 'unknown_tts', 'changed_failure', 'different_audio_error', 'candidate'])
def test_completed_voice_duration_failure_frees_shorts_without_retrying_tts_or_resetting_capacity(case, damage):
    from app.services import production_credit_ledger as credit
    c, source, doc, item, previous = case; task = source['task_id']
    error = attention.VOICE_DURATION_ERROR + json.dumps({'generation_attempt': 0,
        'reason': 'Narration needs 0.855x tempo to fit 180.0s; rewrite the script instead of distorting the voice'})
    source.update(failure_stage='voice_and_visuals', error=error,
        failure_classification={'category': 'content_rejected', 'code': 'audio_quality_exhausted',
            'error_sha256': hashlib.sha256(error.encode()).hexdigest()})
    intent = {'reservation': {'intent': {'root_lineage_id': task}},
              'settlement': {'credits_used': 3210, 'terminal': True}}
    if damage == 'unknown_tts': intent['settlement'] = None
    if damage == 'changed_failure': source['failure_classification']['error_sha256'] = '0' * 64
    if damage == 'different_audio_error': source['error'] = 'Voice provider response was uncertain'
    if damage == 'candidate': source['audio_candidate_checkpoint'] = {'preserve_for_review': True}
    c.hset(credit.STATE_KEY, 'state', plan._raw({'intents': {'original-paid-voice': intent}}))
    c.set(jobs.JOB_PREFIX + task, plan._raw(source))
    terminal = json.loads(c.get('celery-task-meta-' + task))
    terminal['result'] = {'exc_type': 'FinalAudioQualityError', 'exc_message': [source['error']]}
    c.set('celery-task-meta-' + task, plan._raw(terminal))
    before = dump(c)
    if damage:
        try: assert attention.isolate(source, client=c) is None
        except plan.ContentPlanError: pass
        assert dump(c) == before
    else:
        assert attention.isolate(source, client=c) == 'attention_archived'
        assert plan.read(CHANNEL)['items'] == [previous]
        assert cadence.snapshot(CHANNEL, client=c)['counts']['produced']['long'] == 1
        assert cadence.daily_editorial(CHANNEL, {}, client=c)['format'] == 'shorts'
        assert not c.exists(plan.COMPLETION_PREFIX + item['id'])
        for key, value in before.items():
            if key not in {plan.PLAN_PREFIX + CHANNEL, plan.ACTIVE_KEY}: assert c.dump(key) == value
