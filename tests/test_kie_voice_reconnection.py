import json

import pytest
from redis.exceptions import WatchError

from app.services import kie_voice_reconnection as renewal, kie_voice_production as production
from app.services import kie_voice_ledger as ledger, youtube_auth as auth, youtube_automation as profiles
from app.services.production_spend import SpendBlocked
from test_kie_voice_production import active
from test_kie_voice import kie, box

NEW = 'renewed_connection_BBBBB'


def reconnect(active, connection=NEW):
    channel = active.context['channel_id']
    active.client.set(auth.CHANNEL_PREFIX + channel, json.dumps({'id': channel, 'connection_id': connection}))
    active.client.set(auth.CREDENTIAL_PREFIX + channel, 'synthetic-current-oauth-credential')
    active.client.set(profiles.PROFILE_PREFIX + channel, json.dumps({
        'channel_id': channel, 'production_enabled': True, 'auto_publish': True}))
    return channel


def grant(active, channel):
    return renewal.commission(active.foundation, channel,
        expected_connection_id=NEW, owner_evidence_sha256='e' * 64)


def test_owner_reconnect_keeps_funding_and_history_and_allows_only_new_roots(active):
    old_choice = production.select('tr')
    root_key = production.ROOT_PREFIX + active.context['lineage_id']
    preserved = {k: active.client.get(k) for k in (
        ledger.POLICY_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY, ledger.ACTIVE_KEY, root_key)}
    channel = reconnect(active)
    with pytest.raises(SpendBlocked, match='connection_changed'):
        production.select('tr')
    assert grant(active, channel)['allocation_added_microcredits'] == 0
    # A formerly pinned root remains tied to its original connection.
    with pytest.raises(SpendBlocked, match='connection_changed'):
        production.select('tr')
    active.context.update(connection_id=NEW, lineage_id='cf890b6e-2ec5-4959-ae67-060d99710f46')
    choice = production.select('tr')
    assert choice['context']['connection_id'] == NEW and choice['voice_id'] == old_choice['voice_id']
    assert all(active.client.get(k) == v for k, v in preserved.items())
    assert grant(active, channel)['status'] == 'already_active'
    assert [r.method for r in active.requests] == ['GET']


@pytest.mark.parametrize('damage', ['anchor', 'ttl', 'credential', 'revoked', 'unindexed', 'authority'])
def test_damaged_or_revoked_reconnection_stops_before_voice_or_refund(active, damage):
    channel = reconnect(active); grant(active, channel)
    key, anchor = renewal._keys(channel, NEW)
    if damage == 'anchor': active.client.set(anchor, 'f' * 64)
    if damage == 'ttl': active.client.expire(key, 120)
    if damage == 'credential': active.client.set(auth.CREDENTIAL_PREFIX + channel, 'changed')
    if damage == 'revoked':
        record = json.loads(active.client.get(auth.CHANNEL_PREFIX + channel))
        record['requires_reconnect'] = True
        active.client.set(auth.CHANNEL_PREFIX + channel, json.dumps(record))
    if damage == 'unindexed': active.client.srem(auth.CHANNEL_INDEX_KEY, channel)
    if damage == 'authority':
        from app.services.production_continuation import ACTIVE_KEY
        active.client.delete(ACTIVE_KEY)
    before = active.client.get(ledger.JOURNAL_KEY)
    active.context.update(connection_id=NEW)
    with pytest.raises(SpendBlocked): production.select('tr')
    assert active.client.get(ledger.JOURNAL_KEY) == before
    assert [r.method for r in active.requests] == ['GET']


def test_another_oauth_reconnect_needs_its_own_grant(active):
    channel = reconnect(active); grant(active, channel)
    reconnect(active, 'later_connection_CCCCC')
    active.context.update(connection_id='later_connection_CCCCC')
    with pytest.raises(SpendBlocked, match='connection_changed'):
        production.select('tr')


def test_paused_channel_cannot_get_new_production_authority(active):
    channel = reconnect(active)
    active.client.set(profiles.PROFILE_PREFIX + channel, json.dumps({
        'channel_id': channel, 'production_enabled': False, 'auto_publish': False}))
    with pytest.raises(SpendBlocked, match='channel_not_authorized'):
        grant(active, channel)
    assert not active.client.exists(*renewal._keys(channel, NEW))


def test_authorization_watch_fences_concurrent_oauth_replacement(active, monkeypatch):
    channel = reconnect(active)
    original = renewal.read
    def changed(pipe, policy, requested):
        value = original(pipe, policy, requested)
        active.client.set(auth.CREDENTIAL_PREFIX + channel, 'replaced-during-authorization')
        return value
    monkeypatch.setattr(renewal, 'read', changed)
    with pytest.raises(WatchError): grant(active, channel)
    assert not active.client.exists(*renewal._keys(channel, NEW))


def test_old_root_cannot_be_migrated_by_replacing_its_context(active):
    production.select('tr')
    channel = reconnect(active); grant(active, channel)
    active.context['connection_id'] = NEW
    with pytest.raises(SpendBlocked, match='provider_pinned'):
        production.select('tr')


@pytest.mark.parametrize('outcome', ['settled', 'unknown'])
def test_reconnect_preserves_paid_or_unknown_request_and_its_full_accounting(active, outcome):
    import httpx
    from app.services import kie_voice_adapter as api, kie_gemini_voice as gemini
    production.select('tr')
    body, ceiling = gemini.request_body('Bu makine çalışıyor.', language='tr')
    journal = ledger.Journal(active.foundation, active.context, body, ceiling)
    if outcome == 'unknown':
        def lost(request):
            active.requests.append(request)
            raise httpx.ReadTimeout('synthetic lost response')
        active.handler = lost
        with pytest.raises(api.KieVoiceError):
            api.generate(body, active.config.openai_api_key, journal, sleep=lambda _: None)
    else:
        api.generate(body, active.config.openai_api_key, journal, sleep=lambda _: None)
    keys = (ledger.POLICY_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY, ledger.ACTIVE_KEY)
    before = {key:active.client.get(key) for key in keys}
    used = ledger._used(json.loads(before[ledger.JOURNAL_KEY]))
    sends = len(active.requests)
    assert used > 0
    channel = reconnect(active); grant(active, channel)
    assert all(active.client.get(key) == value for key, value in before.items())
    assert ledger._used(json.loads(active.client.get(ledger.JOURNAL_KEY))) == used
    assert len(active.requests) == sends
    with pytest.raises(SpendBlocked, match='connection_changed'):
        production.select('tr')
