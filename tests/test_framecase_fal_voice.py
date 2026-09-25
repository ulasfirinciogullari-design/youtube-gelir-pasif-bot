"""Channel grant and actual fixed-slot submit transactions; no external calls."""
from copy import deepcopy
import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.services import framecase_fal_voice as grant, fal_voice_production as production
from app.services import fal_voice_qualification as qualification, fal_voice_adapter as api
from app.services import fal_voice_trial as trial, kie_voice_ledger as ledger
from app.services import kie_voice_production as old, narrator_rotation
from app.services import channel_commissioning, content_plan as plan, production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_fal_voice_production import enabled, send
from test_kie_voice_production import active
from test_kie_gemini_voice import kie, box

AUTHORIZE = production._authorize
HOLD_GUARD = production._hold_guard


@pytest.fixture
def animation(enabled, monkeypatch):
    client = enabled.client; channel = grant.CHANNEL_ID; connection = 'framecase-connection'
    client.set(runtime._CHANNEL_PREFIX + channel, ledger.raw({'id': channel, 'connection_id': connection}))
    client.sadd(runtime._CHANNEL_INDEX, channel)
    channel_commissioning.initialize(client, {'version': 1, 'channel_id': channel,
        'connection_id': connection, 'authorized_at': ledger.now().isoformat(), 'owner_evidence_sha256': 'c'*64})
    enabled.context.update(channel_id=channel, connection_id=connection, kind='long')
    entry = plan.item('An original mystery', 'The complete fictional animated mystery.', format='long')
    document = {'version': 1, 'channel_id': channel, 'revision': str(uuid4()), 'enabled': True,
        'after_queue': 'pause', 'items': [entry], 'updated_at': ledger.now().isoformat()}
    spec = {'mode': 'production', 'framecase_animation': True, 'content_style': 'original_animation',
        'production_scheduled': True, 'publish_after_render': True, 'language': 'en',
        'production_channel_id': channel, 'production_connection_id': connection,
        'content_plan_item_id': entry['id'], 'format': 'landscape', 'duration_minutes': 3}
    root = enabled.context['lineage_id']
    source = {'task_id': root, 'parent_id': None, 'kind': 'render', 'spec': spec}
    dispatch = {'task_id': root, 'channel_id': channel, 'connection_id': connection,
        'item': entry, 'spec_sha256': plan._sha(spec)}
    for key, value in ((runtime._JOB_PREFIX+root, source), (plan.DISPATCH_PREFIX+entry['id'], dispatch),
            (plan.PLAN_PREFIX+channel, document), (plan.production.PROFILE_PREFIX+channel,
                {'production_enabled': True, 'auto_publish': True, 'default_language': 'en'})):
        client.set(key, plan._raw(value))
    active = json.loads(client.get(production._activation_key('en')))
    enabled.proof = Mock(return_value=active['qualification'])
    monkeypatch.setattr(qualification, 'verified', enabled.proof)
    monkeypatch.setattr(production, '_authorize', AUTHORIZE)
    monkeypatch.setattr(production, '_hold_guard', HOLD_GUARD)
    return enabled


def enable(animation):
    return grant.commission(animation.foundation, owner_evidence_sha256='d'*64)


def test_default_off_separate_grant_preserves_original_funding_and_channel_activation(animation):
    keys = (ledger.POLICY_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY, ledger.ACTIVE_KEY,
            production._activation_key('en'), production._activation_key('en')+':anchor')
    before = {k: animation.client.get(k) for k in keys}; cash = animation.client.hgetall(LEDGER_KEY)
    assert production.select('en') is None
    result = enable(animation)
    assert result['allocation_added_micro_usd'] == 0 and result['kind'] == 'long'
    animation.proof.assert_called_once()
    assert {k: animation.client.get(k) for k in keys} == before
    assert animation.client.hgetall(LEDGER_KEY) == cash
    choice = production.select('en')
    assert choice['voice_id'] == 'George' and choice['mode'] == 'new_long'
    assert production.select('en') == choice
    assert not animation.client.exists(old.ROOT_PREFIX+animation.context['lineage_id'])
    with animation.client.pipeline() as pipe:
        assert production.capacity(pipe, animation.foundation, grant.CHANNEL_ID, kind='long')['voice_provider'] == 'fal'
        assert production.capacity(pipe, animation.foundation, grant.CHANNEL_ID, kind='shorts') is None
    with pytest.raises(api.FalVoiceError, match='already_configured'): enable(animation)
    assert [r.method for r in animation.requests] == ['GET']


@pytest.mark.parametrize('language,kind', [('tr', 'long'), ('en', 'shorts')])
def test_no_short_or_other_language_can_inherit_full_english_permission(animation, language, kind):
    enable(animation); animation.context['kind'] = kind
    assert production.select(language) is None
    assert not animation.client.exists(production.ROOT_PREFIX+animation.context['lineage_id'])


@pytest.mark.parametrize('prior', ['native_narrator', 'native_unknown', 'kie'])
def test_previous_voice_commitment_never_changes_providers(animation, prior):
    enable(animation); root = animation.context['lineage_id']
    if prior == 'native_narrator': animation.client.set(narrator_rotation.PREFIX+'root:'+root, 'retained')
    elif prior == 'native_unknown':
        animation.native.append({'reservation': {'intent': {'root_lineage_id': root}}, 'settlement': None})
    else: animation.client.set(old.ROOT_PREFIX+root, 'retained')
    native = deepcopy(animation.native)
    assert production.select('en') is None and animation.native == native
    assert not animation.client.exists(production.ROOT_PREFIX+root)


@pytest.mark.parametrize('damage', ['voice_proof', 'missing_activation', 'failed_qualification'])
def test_channel_cannot_be_enabled_without_actual_matching_english_qualification(animation, damage):
    if damage == 'voice_proof': animation.proof.return_value = {'different': 'proof'}
    elif damage == 'missing_activation':
        key = production._activation_key('en'); animation.client.delete(key, key+':anchor')
    else:
        key = trial._key('en'); q = json.loads(animation.client.hget(key, 'qualification')); q['pass'] = False
        q = ledger.raw(q); animation.client.hset(key, 'qualification', q)
        animation.client.hset(key+':anchor', 'qualification', ledger.sha(q))
    with pytest.raises(api.FalVoiceError): enable(animation)
    assert not animation.client.exists(grant.KEY, grant.ANCHOR_KEY)


@pytest.mark.parametrize('damage', ['paused_plan', 'paused_profile', 'wrong_language', 'nonfiction',
    'changed_connection', 'missing_anchor', 'held', 'wrong_dispatch', 'unfinished_predecessor'])
def test_paid_boundary_rechecks_authority_and_original_animation_scope(animation, damage):
    enable(animation); production.select('en'); c = animation.client; root = animation.context['lineage_id']
    key = runtime._JOB_PREFIX+root
    if damage == 'paused_plan':
        key = plan.PLAN_PREFIX+grant.CHANNEL_ID; row = json.loads(c.get(key)); row['enabled'] = False
    elif damage == 'paused_profile':
        key = plan.production.PROFILE_PREFIX+grant.CHANNEL_ID
        row = json.loads(c.get(key)); row['auto_publish'] = False
    elif damage == 'changed_connection':
        key = runtime._CHANNEL_PREFIX+grant.CHANNEL_ID; row = json.loads(c.get(key)); row['connection_id'] = 'different'
    elif damage == 'missing_anchor':
        c.delete(grant.ANCHOR_KEY); row = json.loads(c.get(key))
    elif damage == 'unfinished_predecessor':
        key = plan.PLAN_PREFIX+grant.CHANNEL_ID; row = json.loads(c.get(key))
        row['items'].insert(0, plan.item('Earlier episode', 'A required previous episode.', format='animation'))
    else:
        row = json.loads(c.get(key))
        if damage == 'wrong_language': row['spec']['language'] = 'tr'
        elif damage == 'nonfiction': row['spec']['content_style'] = 'documentary'
        elif damage == 'held': row['publication_hold'] = True
        else:
            key = plan.DISPATCH_PREFIX+row['spec']['content_plan_item_id']
            row = json.loads(c.get(key)); row['spec_sha256'] = 'f'*64
    c.set(key, ledger.raw(row)); sender = Mock()
    with pytest.raises((api.FalVoiceError, SpendBlocked, ValueError)):
        send(animation, sender=sender)
    sender.assert_not_called()
    assert not c.exists(production.PREFIX+'take:'+root+':0')


def test_lost_or_captured_submit_stays_in_its_original_fixed_slot(animation):
    enable(animation); production.select('en')
    sender = Mock(side_effect=httpx.ReadTimeout('unknown outcome'))
    with pytest.raises(httpx.ReadTimeout): send(animation, sender=sender)
    with pytest.raises(api.FalVoiceError, match='submit_unknown'): send(animation, sender=sender)
    with pytest.raises(api.FalVoiceError, match='submit_unknown'): send(animation, attempt=1, sender=sender)
    assert sender.call_count == 1


def test_known_response_replays_without_spending_on_same_narration_twice(animation):
    enable(animation); production.select('en')
    sender = Mock(return_value=httpx.Response(202, json={'accepted': True}))
    assert send(animation, sender=sender).json() == {'accepted': True}
    assert send(animation, sender=sender).json() == {'accepted': True}
    with pytest.raises(api.FalVoiceError): send(animation, sender=sender, body={**animation.fal_body, 'text':'Changed.'})
    with pytest.raises(api.FalVoiceError): send(animation, sender=sender, attempt=3)
    assert sender.call_count == 1
