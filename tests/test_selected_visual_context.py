"""Private preservation ancestry reads cannot initialize or reset spending."""
import json
from unittest.mock import Mock
from uuid import UUID

import fakeredis
import pytest

from app.services import selected_visual_context as context


ROOT = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
CONNECTION = 'connection_AAAAA'


def job(task_id, parent=None):
    return {'task_id': task_id, 'parent_id': parent, 'kind': 'render', 'state': 'PROGRESS',
            'spec': {'production_channel_id': CHANNEL, 'production_connection_id': CONNECTION,
                     'mode': 'production', 'format': 'shorts', 'duration_minutes': 0.5}}


def put(client, value):
    client.set(context.studio_state.JOB_PREFIX + value['task_id'], json.dumps(value))


@pytest.fixture
def client():
    value = fakeredis.FakeRedis(decode_responses=True)
    value.set(context._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': CONNECTION}))
    value.sadd(context._CHANNEL_INDEX, CHANNEL)
    put(value, job(ROOT))
    put(value, job(CHILD, ROOT))
    return value


def resolve(client, source=CHILD):
    return context.resolve_selected_visual_binding(source, client=client)


def test_preservation_reads_do_not_require_initialize_or_mutate_any_ledger(client):
    before = {key: client.dump(key) for key in client.keys('*')}
    expected = {'source_task_id': CHILD, 'lineage_id': ROOT, 'channel_id': CHANNEL, 'connection_id': CONNECTION}
    first = resolve(client)
    assert len(first['ancestry_sha256']) == 64
    assert {key: value for key, value in first.items() if key != 'ancestry_sha256'} == expected
    assert resolve(client) == first
    assert {key: client.dump(key) for key in client.keys('*')} == before
    assert not client.keys('*spend*') and not client.keys('*claim*')


def test_root_source_and_ordinary_progress_changes_preserve_identity(client):
    expected = resolve(client, ROOT)
    root = job(ROOT)
    root.update(state='FAILURE', error='Final quality rejected', selected_visual_checkpoint={'private': True})
    put(client, root)
    assert resolve(client, ROOT) == expected


@pytest.mark.parametrize('field,value', [('topic', 'another episode'), ('production_topic_index', 7),
                                       ('production_profile_revision', 'new_revision'), ('language', 'en')])
def test_same_account_and_root_cannot_hide_changed_episode_spec(client, field, value):
    before = resolve(client)
    changed = job(CHILD, ROOT)
    changed['spec'][field] = value
    put(client, changed)
    after = resolve(client)
    assert after['ancestry_sha256'] != before['ancestry_sha256']
    assert {key: value for key, value in after.items() if key != 'ancestry_sha256'} == {
        key: value for key, value in before.items() if key != 'ancestry_sha256'}


def test_changed_intermediate_parent_changes_binding_even_with_same_source_and_root(client):
    before = resolve(client)
    intermediate = '33333333-3333-4333-8333-333333333333'
    put(client, job(intermediate, ROOT))
    put(client, job(CHILD, intermediate))
    after = resolve(client)
    assert after['lineage_id'] == before['lineage_id'] and after['source_task_id'] == before['source_task_id']
    assert after['ancestry_sha256'] != before['ancestry_sha256']


@pytest.mark.parametrize('damage', ['missing_parent', 'cycle', 'invalid_parent', 'wrong_task', 'wrong_kind',
                                  'wrong_channel', 'wrong_connection', 'alias_conflict', 'preview',
                                  'landscape', 'long', 'boolean_duration', 'missing_spec', 'invalid_json',
                                  'oversized_record', 'wrong_type', 'missing_channel', 'not_member',
                                  'rotated_connection', 'wrong_owned_channel'])
def test_missing_or_conflicting_server_evidence_blocks_preservation(client, damage):
    source = job(CHILD, ROOT)
    if damage == 'missing_parent': client.delete(context.studio_state.JOB_PREFIX + ROOT)
    elif damage == 'cycle': source['parent_id'] = CHILD
    elif damage == 'invalid_parent': source['parent_id'] = 'not-a-job'
    elif damage == 'wrong_task':
        client.set(context.studio_state.JOB_PREFIX + CHILD, json.dumps(job(ROOT)))
    elif damage == 'wrong_kind': source['kind'] = 'publish'
    elif damage == 'wrong_channel': source['spec']['production_channel_id'] = 'UC' + 'x' * 22
    elif damage == 'wrong_connection': source['spec']['production_connection_id'] = 'another_connection'
    elif damage == 'alias_conflict': source['spec']['youtube_connection_id'] = 'another_connection'
    elif damage == 'preview': source['spec']['mode'] = 'preview'
    elif damage == 'landscape': source['spec']['format'] = 'landscape'
    elif damage == 'long': source['spec']['duration_minutes'] = 8
    elif damage == 'boolean_duration': source['spec']['duration_minutes'] = True
    elif damage == 'missing_spec': source.pop('spec')
    elif damage == 'invalid_json': client.set(context.studio_state.JOB_PREFIX + CHILD, '{invalid')
    elif damage == 'oversized_record': client.set(context.studio_state.JOB_PREFIX + CHILD, ' ' * (context.MAX_RECORD_BYTES + 1))
    elif damage == 'wrong_type':
        client.delete(context.studio_state.JOB_PREFIX + CHILD)
        client.hset(context.studio_state.JOB_PREFIX + CHILD, mapping={'invalid': 'hash'})
    elif damage == 'missing_channel': client.delete(context._CHANNEL_PREFIX + CHANNEL)
    elif damage == 'not_member': client.srem(context._CHANNEL_INDEX, CHANNEL)
    elif damage == 'rotated_connection':
        client.set(context._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'rotated_connection'}))
    elif damage == 'wrong_owned_channel':
        client.set(context._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': 'UC' + 'x' * 22, 'connection_id': CONNECTION}))
    if damage not in {'wrong_task', 'invalid_json', 'oversized_record', 'wrong_type'}:
        put(client, source)
    with pytest.raises(context.SelectedVisualContextError, match='^selected_visual_context_unavailable$'):
        resolve(client)


def test_bounded_ancestry_never_accepts_an_unread_tail(client):
    ids = [str(UUID(int=index + 1)) for index in range(context.MAX_LINEAGE_JOBS + 1)]
    for index, task_id in enumerate(ids):
        put(client, job(task_id, ids[index + 1] if index + 1 < len(ids) else None))
    with pytest.raises(context.SelectedVisualContextError): resolve(client, ids[0])
    assert resolve(client, ids[1])['lineage_id'] == ids[-1]


@pytest.mark.parametrize('receipt', [[], [1], [False], [True, True], None])
def test_uncertain_read_transaction_does_not_return_binding(client, monkeypatch, receipt):
    original = client.pipeline
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        monkeypatch.setattr(pipe, 'execute', Mock(return_value=receipt))
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(context.SelectedVisualContextError): resolve(client)


def test_connection_rotation_during_snapshot_is_detected_before_return(client, monkeypatch):
    original = client.pipeline
    attempts = []
    def pipeline(*args, **kwargs):
        pipe = original(*args, **kwargs)
        execute = pipe.execute
        def mutate_then_execute(*args, **kwargs):
            attempts.append(True)
            client.set(context._CHANNEL_PREFIX + CHANNEL, json.dumps({'id': CHANNEL, 'connection_id': 'rotated_connection'}))
            return execute(*args, **kwargs)
        monkeypatch.setattr(pipe, 'execute', mutate_then_execute)
        return pipe
    monkeypatch.setattr(client, 'pipeline', pipeline)
    with pytest.raises(context.SelectedVisualContextError): resolve(client)
    assert len(attempts) == 1


@pytest.mark.parametrize('source', [None, True, 'invalid', ROOT.upper()])
def test_invalid_task_does_not_contact_redis(source):
    # Numeric-only ROOT has no casing difference; use a genuinely noncanonical ID.
    if source == ROOT: source = 'aaaaaaaa-AAAA-4aaa-8aaa-aaaaaaaaaaaa'
    client = Mock()
    with pytest.raises(context.SelectedVisualContextError): resolve(client, source)
    client.pipeline.assert_not_called()


def test_store_error_is_sanitized():
    client = Mock()
    client.pipeline.side_effect = RuntimeError('secret provider connection data')
    with pytest.raises(context.SelectedVisualContextError) as exc: resolve(client)
    assert str(exc.value) == 'selected_visual_context_unavailable'
