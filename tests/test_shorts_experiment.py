from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4

import fakeredis
import pytest

from app.config import settings
from app.services import channel_cadence as cadence, channel_formats as formats
from app.services import content_plan as plan, shorts_experiment as batch, studio_state as jobs

C, M = tuple(batch.COUNTS)
NOW = datetime(2026, 9, 25, 22, tzinfo=timezone.utc).timestamp()
DAY = '2026-09-26'


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, 'studio_shorts_policy_json', json.dumps({
        'version': 1, 'id': 'shorts-20260925-4-1', 'daily_limits': {C: 4, M: 1}}))
    client = fakeredis.FakeRedis(decode_responses=True)
    entries = {channel: [plan.item('Distinct test ' + str(n), 'Cited independent story ' + str(n))
                        for n in range(count)] for channel, count in batch.COUNTS.items()}
    approval = {'version': 1, 'id': 'owner-ten-20260926', 'day': DAY, 'items': [
        {'item_id': item['id'], 'channel_id': channel, 'item_sha256': plan._sha(item)}
        for channel, items in entries.items() for item in items]}
    client.set(batch.approval_key(DAY), plan._raw(approval))
    for channel, count in ((C, 4), (M, 1)):
        client.hset(cadence.keys(channel, now=NOW)[1], mapping={str(uuid4()): 'shorts' for _ in range(count)})
    return client, entries, approval


def admitted(client, channel, item):
    task = batch.root_id(channel, item['id'])
    spec = {'production_channel_id': channel, 'content_plan_item_id': item['id'], 'format': 'shorts'}
    source = {'task_id': task, 'parent_id': None, 'spec': spec}
    with client.pipeline() as pipe:
        key = cadence.production_slot(pipe, channel, 'shorts', task, now=NOW, item=item)
        assert key == batch.produced_key(DAY, channel)
        pipe.multi(); pipe.hset(key, task, 'shorts'); pipe.execute()
    client.set(jobs.JOB_PREFIX + task, plan._raw(source))
    client.set(plan.DISPATCH_PREFIX + item['id'], plan._raw({
        'task_id': task, 'channel_id': channel, 'item': item, 'spec_sha256': plan._sha(spec)}))
    return source


def ordinary(channel):
    return {'task_id': str(uuid4()), 'parent_id': None,
            'spec': {'production_channel_id': channel, 'format': 'shorts'}}


def test_exact_eight_plus_two_without_reset_or_extra_ordinary_admissions(setup):
    client, entries, approval = setup
    originals = {c: client.hgetall(cadence.keys(c, now=NOW)[1]) for c in entries}
    for channel, items in entries.items():
        assert not cadence.publication_slot(ordinary(channel), client=client, now=NOW)
        for item in items:
            source = admitted(client, channel, item)
            assert cadence.publication_slot(source, client=client, now=NOW)
            assert cadence.publication_slot(source, client=client, now=NOW)
            cadence.publication_completed(source, client=client, now=NOW)
            cadence.publication_completed(source, client=client, now=NOW)
        assert not cadence.publication_slot(ordinary(channel), client=client, now=NOW)
        view = cadence.snapshot(channel, client=client, now=NOW)
        base = 4 if channel == C else 1
        assert view['limits'] == {'long': 0, 'shorts': base}
        assert view['counts']['published']['shorts'] == base + len(items)
        assert view['experiment']['published'] == len(items)
        assert view['experiment']['produced'] == len(items)
        assert view['display_limits']['shorts'] == base + len(items)
        assert originals[channel].items() <= client.hgetall(cadence.keys(channel, now=NOW)[1]).items()
    assert client.get(batch.approval_key(DAY)) == plan._raw(approval)
    assert formats.policy_id(C) == 'shorts-20260925-4-1'


def test_grant_is_not_a_general_production_increase(setup):
    client, entries, _ = setup
    item = plan.item('Unapproved', 'Not in this exact batch')
    with client.pipeline() as pipe:
        assert cadence.production_slot(pipe, C, 'shorts', str(uuid4()), now=NOW, item=item) is False
        assert cadence.production_slot(pipe, C, 'shorts', str(uuid4()), now=NOW) is False
        assert cadence.production_slot(pipe, C, 'long', str(uuid4()), now=NOW, item=entries[C][0]) is False


@pytest.mark.parametrize('change', ['brief', 'channel', 'task', 'format'])
def test_changed_item_or_routing_cannot_use_approval(setup, change):
    client, entries, _ = setup
    item = deepcopy(entries[C][0]); channel = C; task = batch.root_id(C, item['id'])
    if change == 'brief': item['brief'] += ' New claims'
    if change == 'format': item['format'] = 'long'
    if change == 'channel': channel = M
    if change == 'task': task = str(uuid4())
    with pytest.raises(ValueError), client.pipeline() as pipe:
        batch.production_slot(pipe, channel, item, task, now=NOW)
    assert not client.exists(batch.produced_key(DAY, C))


def test_expired_batch_does_not_expand_tomorrows_four_plus_one(setup):
    client, entries, _ = setup
    source = admitted(client, C, entries[C][0])
    tomorrow = NOW + 86400
    with client.pipeline() as pipe:
        assert batch.production_slot(pipe, C, entries[C][1], batch.root_id(C, entries[C][1]['id']), now=tomorrow) is None
    for _ in range(4):
        assert cadence.publication_slot(ordinary(C), client=client, now=tomorrow)
    assert not cadence.publication_slot(source, client=client, now=tomorrow)
    assert 'experiment' not in cadence.snapshot(C, client=client, now=tomorrow)


def test_unknown_upload_remains_reserved_across_midnight(setup):
    client, entries, _ = setup
    source = admitted(client, C, entries[C][0])
    assert cadence.publication_slot(source, client=client, now=NOW)
    for _ in range(3): assert cadence.publication_slot(ordinary(C), client=client, now=NOW + 86400)
    assert not cadence.publication_slot(ordinary(C), client=client, now=NOW + 86400)
    assert cadence.publication_slot(source, client=client, now=NOW + 86400)
    assert len(client.hgetall(cadence.keys(C, now=NOW + 86400)[2])) == 4


@pytest.mark.parametrize('change', ['spec', 'dispatch', 'receipt'])
def test_publication_requires_original_dispatch_spec_and_production_receipt(setup, change):
    client, entries, _ = setup
    source = admitted(client, C, entries[C][0])
    if change == 'spec': source['spec']['extra'] = 'not in original'
    if change == 'dispatch': client.delete(plan.DISPATCH_PREFIX + entries[C][0]['id'])
    if change == 'receipt': client.hdel(batch.produced_key(DAY, C), source['task_id'])
    with pytest.raises(ValueError): cadence.publication_slot(source, client=client, now=NOW)
    assert not client.exists(cadence.PREFIX + 'publication:' + source['task_id'])


def test_only_one_child_can_publish_an_admitted_root(setup):
    client, entries, _ = setup
    source = admitted(client, C, entries[C][0]); child = deepcopy(source)
    child['task_id'] = str(uuid4()); child['parent_id'] = source['task_id']
    source['retry_child_task_id'] = child['task_id']
    client.set(jobs.JOB_PREFIX + source['task_id'], plan._raw(source))
    assert cadence.publication_slot(child, client=client, now=NOW)
    with pytest.raises(ValueError): cadence.publication_slot(source, client=client, now=NOW)
    assert client.hgetall(cadence.keys(C, now=NOW)[2]) == {source['task_id']: 'shorts'}


def test_plan_projects_the_correct_channel_cadence(setup, monkeypatch):
    client, entries, _ = setup
    original = cadence.snapshot
    monkeypatch.setattr(cadence, 'snapshot', lambda channel_id, **kw: original(channel_id, now=NOW, **kw))
    document = {'version': 1, 'channel_id': C, 'revision': str(uuid4()), 'enabled': True,
                'after_queue': 'auto_shorts', 'items': entries[C], 'updated_at': entries[C][0]['created_at']}
    view = plan.project(document, client=client)
    assert view['daily_cadence']['counts']['published']['shorts'] == 4
    assert view['daily_cadence']['experiment']['limit'] == 8
    assert view['daily_cadence']['display_limits']['shorts'] == 12


def test_batch_does_not_override_funding_preflight(setup, monkeypatch):
    from app.services import production_spend_runtime as spend
    client, entries, _ = setup
    document = {'version': 1, 'channel_id': C, 'revision': str(uuid4()), 'enabled': True,
                'after_queue': 'auto_shorts', 'items': entries[C], 'updated_at': entries[C][0]['created_at']}
    client.set(plan.PLAN_PREFIX + C, plan._raw(document))
    monkeypatch.setattr(plan, '_client', lambda: client)
    def blocked(*args, **kwargs): raise spend.SpendBlocked('budget_exhausted')
    monkeypatch.setattr(spend, 'preflight_scheduled_production', blocked)
    with pytest.raises(spend.SpendBlocked, match='budget_exhausted'): plan._reserve(C, now=NOW)
    assert not client.exists(batch.produced_key(DAY, C), plan.DISPATCH_PREFIX + entries[C][0]['id'])


@pytest.mark.parametrize('change', ['eleven', 'duplicate', 'wrong_split', 'day', 'hash'])
def test_malformed_approval_is_rejected(setup, change):
    _, _, approval = setup
    if change == 'eleven': approval['items'].append(deepcopy(approval['items'][0]))
    if change == 'duplicate': approval['items'][1] = deepcopy(approval['items'][0])
    if change == 'wrong_split': approval['items'][0]['channel_id'] = M
    if change == 'day': approval['day'] = '2026-09-27'
    if change == 'hash': approval['items'][0]['item_sha256'] = 'invalid'
    with pytest.raises(ValueError): batch.validate(approval, DAY)
