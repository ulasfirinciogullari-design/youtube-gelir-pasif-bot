"""Owner format changes stop future long work without erasing past delivery."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock
from uuid import uuid4

import fakeredis
import pytest

from app.services import channel_formats as formats, channel_cadence as cadence
from app.services import content_plan as plan, production_spend_runtime as spending
from app.services import youtube_automation as automation
from test_content_plan import case, CHANNEL, OTHER
from test_content_plan_routes import ui
from test_youtube_synthetic_disclosure import worker
from test_channel_production import production, _profile, _save, CONNECTION

PEER = 'UCs93z6wf134H5_BL9pkQX4Q'
NOW = datetime(2026, 9, 25, 10, tzinfo=timezone.utc).timestamp()
POLICY = {'version': 1, 'id': 'shorts-20260925-4-1', 'daily_limits': {CHANNEL: 4, OTHER: 1}}


@pytest.fixture
def only_shorts(monkeypatch):
    monkeypatch.setattr(formats.settings, 'studio_shorts_policy_json', json.dumps(POLICY), raising=False)


def snapshot(client):
    return {key: client.dump(key) for key in client.scan_iter()}


@pytest.mark.parametrize('channel', [CHANNEL, OTHER])
def test_daily_and_advance_planning_never_create_long_and_history_stays_intact(only_shorts, channel):
    client = fakeredis.FakeRedis(decode_responses=True)
    base = cadence.PREFIX + channel + ':'
    produced, published = base + 'produced:2026-09-25', base + 'published:2026-09-25'
    client.hset(produced, str(uuid4()), 'long')
    client.hset(published, mapping={str(uuid4()): kind for kind in ['long', *(['shorts'] * 5)]})
    before = snapshot(client)
    for instant in (NOW, NOW + 86400):
        for defer in (False, True):
            selected = cadence.daily_editorial(channel, {'format': 'landscape'}, client=client,
                now=instant, defer_daily_long=defer)
            assert selected['format'] == 'shorts' and selected['duration_minutes'] == .5
            assert selected['reason_code'] == 'owner_shorts_only'
        for advance in (False, True):
            assert cadence.install_daily_long({'channel_id': channel}, ['Existing source-backed topic'],
                client=client, now=instant, advance=advance) == {'status': 'format_disabled'}
    current = cadence.snapshot(channel, client=client, now=NOW)
    assert current['limits'] == {'long': 0, 'shorts': POLICY['daily_limits'][channel]}
    assert current['counts']['published'] == {'long': 0, 'shorts': 0}
    assert current['previously_published_today'] == {'long': 1, 'shorts': 5}
    with client.pipeline() as pipe:
        assert cadence.production_slot(pipe, channel, 'shorts', str(uuid4()), now=NOW)
    assert snapshot(client) == before


def test_four_and_one_shorts_admitted_independently_and_peer_format_unchanged(only_shorts):
    client = fakeredis.FakeRedis(decode_responses=True)
    for channel in (CHANNEL, OTHER):
        def source(kind='shorts'):
            return {'task_id': str(uuid4()), 'parent_id': None,
                'spec': {'production_channel_id': channel, 'format': kind}}
        for _ in range(POLICY['daily_limits'][channel]):
            assert cadence.publication_slot(source(), client=client, now=NOW)
        assert not cadence.publication_slot(source(), client=client, now=NOW)
        before = snapshot(client)
        assert not cadence.publication_slot(source('landscape'), client=client, now=NOW + 86400)
        with client.pipeline() as pipe:
            assert cadence.production_slot(pipe, channel, 'long', str(uuid4()), now=NOW + 86400) is False
        assert snapshot(client) == before
    assert formats.allows(PEER, 'landscape') and formats.allows(PEER, 'animation')
    fallback = {'format': 'landscape', 'duration_minutes': 3}
    assert cadence.daily_editorial(PEER, fallback, client=client, now=NOW) == fallback


@pytest.mark.parametrize('channel', [CHANNEL, OTHER])
def test_new_plan_quota_survives_restart_and_rolls_at_turkey_midnight(only_shorts, channel):
    client = fakeredis.FakeRedis(decode_responses=True)
    before = datetime(2026, 9, 25, 20, 59, tzinfo=timezone.utc).timestamp()
    after = before + 120
    old_rows = {str(uuid4()): kind for kind in ['long', *(['shorts'] * 5)]}
    old_key = cadence.PREFIX + channel + ':published:2026-09-25'
    client.hset(old_key, mapping=old_rows)
    rows = []
    for _ in range(POLICY['daily_limits'][channel]):
        source = {'task_id': str(uuid4()), 'spec': {'production_channel_id': channel, 'format': 'shorts'}}
        assert cadence.publication_slot(source, client=client, now=before)
        rows.append(source)
    def available(instant):
        with client.pipeline() as pipe:
            return cadence.production_slot(pipe, channel, 'shorts', str(uuid4()), now=instant)
    formats._parse.cache_clear()  # Re-read persisted config as a fresh process does.
    assert available(before) is False and available(after) is False
    for row in rows:
        cadence.publication_completed(row, client=client, now=after)
        cadence.publication_completed(row, client=client, now=after)  # Lost response replay.
    assert available(after) is False and available(after + 86400)
    assert cadence.snapshot(channel, client=client, now=after)['counts']['published']['shorts'] == len(rows)
    assert client.hgetall(old_key) == old_rows


def test_previous_policy_pending_upload_does_not_gain_an_extra_slot(only_shorts, monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    source = {'task_id': str(uuid4()), 'spec': {'production_channel_id': OTHER, 'format': 'shorts'}}
    monkeypatch.setattr(formats.settings, 'studio_shorts_policy_json', '')
    assert cadence.publication_slot(source, client=client, now=NOW)
    monkeypatch.setattr(formats.settings, 'studio_shorts_policy_json', json.dumps(POLICY))
    fresh = {**source, 'task_id': str(uuid4())}
    assert not cadence.publication_slot(fresh, client=client, now=NOW)
    cadence.publication_completed(source, client=client, now=NOW)
    assert not cadence.publication_slot(fresh, client=client, now=NOW)


@pytest.mark.parametrize('channel', [CHANNEL, OTHER])
def test_normal_scheduler_admits_short_despite_old_full_day_then_enforces_new_cap(production, only_shorts, channel):
    module, client = production
    profile = _profile(channel_id=channel)
    connection = {**CONNECTION, 'id': channel}
    _save(module, client, profile, connection)
    old = cadence.PREFIX + channel + ':produced:2026-09-25'
    original = {str(uuid4()): kind for kind in ['long', *(['shorts'] * 5)]}
    client.hset(old, mapping=original)
    produced = cadence.keys(channel, now=NOW)[0]
    # Leave exactly one slot: the real scheduler's Lua must take it once.
    for _ in range(3 * POLICY['daily_limits'][channel] - 1): client.hset(produced, str(uuid4()), 'shorts')
    enqueue = Mock()
    result = module.dispatch_due_productions([profile], [connection], enqueue, now=NOW)
    assert result['status'] == 'queued' and enqueue.call_count == 1
    spec = enqueue.call_args.kwargs['args'][4]
    assert spec['format'] == 'shorts' and spec['production_editorial']['reason_code'] == 'owner_shorts_only'
    assert len(client.hgetall(produced)) == 3 * POLICY['daily_limits'][channel]
    assert client.hgetall(old) == original
    with client.pipeline() as pipe:
        assert cadence.production_slot(pipe, channel, 'shorts', str(uuid4()), now=NOW) is False


@pytest.mark.parametrize('policy', [
    {**POLICY, 'daily_limits': {CHANNEL: True}},
    {**POLICY, 'daily_limits': {PEER: 4}},
    {**POLICY, 'id': '../invalid'},
])
def test_invalid_policy_cannot_fall_back_to_unlimited_production(monkeypatch, policy):
    monkeypatch.setattr(formats.settings, 'studio_shorts_policy_json', json.dumps(policy))
    with pytest.raises(ValueError, match='channel_shorts_policy_invalid'):
        cadence.lua_arguments(CHANNEL, 'shorts', now=NOW)


def test_disabled_long_lua_is_not_the_legacy_zero_limit_bypass(only_shorts):
    client = fakeredis.FakeRedis(decode_responses=True)
    def execute(channel, kind):
        keys = cadence.lua_arguments(channel, kind, now=NOW)
        args = [''] * 16
        args[8] = str(uuid4()); args[13:] = list(keys[3:])
        return client.eval(cadence.PRODUCTION_LUA + "return 'reserved'", 13,
            *[f'unused:{i}' for i in range(10)], *keys[:3], *args)
    assert execute(CHANNEL, 'long') == 'format_disabled'
    assert snapshot(client) == {}
    assert execute(PEER, 'long') == 'reserved'
    for _ in range(12): assert execute(CHANNEL, 'shorts') == 'reserved'
    before = snapshot(client)
    assert execute(CHANNEL, 'shorts') == 'production_attempt_limit_wait' and snapshot(client) == before
    state = cadence.snapshot(CHANNEL, client=client, now=NOW)
    assert state['wait_reason'] == 'production_attempt_limit'
    assert state['counts']['published']['shorts'] == 0 and state['limits']['shorts'] == 4


@pytest.mark.parametrize('channel', [CHANNEL, OTHER])
def test_failed_attempt_does_not_consume_publication_target_but_pending_and_public_do(only_shorts, channel):
    client = fakeredis.FakeRedis(decode_responses=True)
    produced, published, pending = cadence.keys(channel, now=NOW)
    rejected = str(uuid4()); client.hset(produced, rejected, 'shorts')
    def execute():
        selected = cadence.lua_arguments(channel, 'shorts', now=NOW)
        args = [''] * 16; args[8] = str(uuid4()); args[13:] = list(selected[3:])
        return client.eval(cadence.PRODUCTION_LUA + "return 'reserved'", 13,
            *[f'unused:{i}' for i in range(10)], *selected[:3], *args)
    assert execute() == 'reserved'
    assert client.hget(produced, rejected) == 'shorts'
    public = {str(uuid4()): 'shorts' for _ in range(POLICY['daily_limits'][channel])}
    client.hset(pending, mapping=public)
    before = snapshot(client)
    assert execute() == 'daily_limit_wait' and snapshot(client) == before
    with client.pipeline() as pipe:
        assert cadence.production_slot(pipe, channel, 'shorts', str(uuid4()), now=NOW) is False
    client.hset(published, mapping=public); client.delete(pending)
    assert execute() == 'daily_limit_wait'
    assert client.hget(produced, rejected) == 'shorts'


def test_stale_long_plan_cannot_reach_funding_or_dispatch(case, only_shorts, monkeypatch):
    document = deepcopy(case.document)
    document['items'] = [plan.item('Old long', 'Already stored topic', 'long')]
    case.client.set(plan.PLAN_PREFIX + CHANNEL, plan._raw(document))
    before = snapshot(case.client)
    assert plan._reserve(CHANNEL, now=NOW) == {'status': 'format_disabled'}
    case.funding.assert_not_called()
    assert snapshot(case.client) == before
    funding = Mock(side_effect=AssertionError('No financial admission for disabled format'))
    monkeypatch.setattr(spending, 'configured_ledger', funding)
    # Use the actual function; the generic queue fixture mocks its caller.
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parents[1] / 'app/services/production_spend_runtime.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'preflight_scheduled_production')
    ns = {'SpendBlocked': spending.SpendBlocked, 'enforcement_enabled': funding}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-preflight>', 'exec'), ns)
    with pytest.raises(spending.SpendBlocked, match='channel_format_disabled'):
        ns[node.name](CHANNEL, kind='long')
    funding.assert_not_called()


def test_studio_only_offers_shorts_and_rejects_stale_long_posts(ui, only_shorts):
    case, client = ui
    before = snapshot(case.client)
    page = client.get('/studio/plan')
    assert page.status_code == 200 and 'Yalnız Shorts' in page.text and '/4' in page.text
    assert '<option value="long">' not in page.text and '<option value="animation">' not in page.text
    assert '<option value="shorts">' in page.text
    for route, data in [('/studio/plan/add', {'title': 'No long', 'brief': 'Full brief'}),
                        ('/studio/plan/series', {'name': 'Old long series', 'episodes': 'First episode\nFinal episode'})]:
        response = client.post(route, data={**data, 'channel_id': CHANNEL,
            'revision': case.document['revision'], 'format': 'long'},
            headers={'Origin': 'https://studio.example'}, follow_redirects=False)
        assert response.status_code == 303 and 'problem=plan_format_disabled' in response.headers['location']
    assert snapshot(case.client) == before
    case.funding.assert_not_called()


def test_publish_metadata_rejects_long_before_series_numbers_or_source_mutation(only_shorts, monkeypatch):
    source = {'spec': {'format': 'landscape'}, 'result': {}}
    before = deepcopy(source)
    reserve = Mock(side_effect=AssertionError('No series number may be allocated'))
    monkeypatch.setattr(automation, 'reserve_series_number', reserve)
    with pytest.raises(automation.MetadataValidationError, match='Shorts only'):
        automation.build_publish_plan('existing-source', source, {'channel_id': CHANNEL})
    reserve.assert_not_called(); assert source == before


def test_already_queued_long_publisher_stops_before_credentials_upload_and_release(worker, only_shorts):
    reservation = worker.ns['get_upload_record'].return_value
    reservation['target_channel_id'] = CHANNEL
    source = worker.ns['get_job'].return_value
    source['spec'].update(production_channel_id=CHANNEL, format='landscape')
    original = deepcopy(source)
    with pytest.raises(RuntimeError, match='private upload preflight failed'):
        worker.run()
    for name in ('load_credentials', 'download_file', 'upload_video_with_credentials', 'set_video_release_with_credentials'):
        worker.ns[name].assert_not_called()
    assert source == original
