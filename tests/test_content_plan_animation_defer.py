from copy import deepcopy
import json
from uuid import uuid4

import fakeredis
import pytest

from app.services import content_plan as plan, content_plan_animation_defer as defer, studio_state as jobs
from test_content_plan import CHANNEL, OTHER


@pytest.fixture
def case():
    c = fakeredis.FakeRedis(decode_responses=True)
    item = plan.item('Oskarın dünyası', '5 bölümlük animasyon çizgi dizi güzel kurgulu')
    task = str(uuid4())
    spec = {'content_plan_item_id': item['id'], 'production_channel_id': CHANNEL, 'format': 'shorts'}
    job = {'task_id': task, 'kind': 'render', 'state': 'FAILURE', 'spec': spec,
        'failure_stage': 'research', 'error': 'included_research_primary_source_required', 'paid_create_slots_used': 0}
    dispatch = {'task_id': task, 'channel_id': CHANNEL, 'item': item, 'spec_sha256': plan._sha(spec)}
    document = {'version': 1, 'channel_id': CHANNEL, 'revision': str(uuid4()), 'items': [item],
        'enabled': True, 'after_queue': 'auto_shorts', 'updated_at': item['created_at']}
    for k, v in {plan.PLAN_PREFIX + CHANNEL: document, plan.DISPATCH_PREFIX + item['id']: dispatch,
        jobs.JOB_PREFIX + task: job, plan.ACTIVE_KEY: {CHANNEL: item['id'], OTHER: 'other-existing-item'}}.items():
        c.set(k, plan._raw(v))
    c.set('paid_research_receipt', 'immutable')
    return c, document, item, job


def run(case):
    c, doc, item, job = case
    return defer.defer(CHANNEL, doc['revision'], item['id'], job['task_id'], plan._sha(item), client=c)


def test_only_failed_fiction_intent_moves_and_other_channel_keeps_running(case):
    c, doc, item, job = case
    original = {k: c.get(k) for k in (jobs.JOB_PREFIX + job['task_id'], plan.DISPATCH_PREFIX + item['id'])}
    assert run(case)['status'] == 'deferred_to_animation_stock'
    assert plan.read(CHANNEL, client=c)['items'] == []
    assert json.loads(c.get(plan.ACTIVE_KEY)) == {OTHER: 'other-existing-item'}
    assert {k: c.get(k) for k in original} == original
    record = json.loads(c.get(defer.PREFIX + item['id']))
    assert record['original_plan'] == plan._raw(doc)
    assert record['publish_eligible'] is False and record['qa_approved'] is False
    assert c.get('paid_research_receipt') == 'immutable'
    assert run(case)['status'] == 'already_deferred'


@pytest.mark.parametrize('field,value', [('state', 'PROGRESS'), ('failure_stage', 'visuals'),
    ('paid_create_slots_used', 1), ('audio_candidate_checkpoint', {'existing': True}),
    ('generated_asset_candidates', [1]), ('retry_child_task_id', 'existing'), ('result', {'video_key': 'existing'})])
def test_active_or_previously_produced_media_cannot_be_removed(case, field, value):
    c, _, _, job = case; edited = deepcopy(job); edited[field] = value
    c.set(jobs.JOB_PREFIX + job['task_id'], plan._raw(edited))
    before = {k: c.dump(k) for k in c.scan_iter()}
    with pytest.raises(plan.ContentPlanError): run(case)
    assert {k: c.dump(k) for k in c.scan_iter()} == before


def test_changed_owner_queue_is_preserved(case):
    c, doc, _, _ = case
    edited = deepcopy(doc); edited['revision'] = str(uuid4())
    c.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(edited))
    with pytest.raises(plan.ContentPlanError, match='plan_changed'): run(case)
