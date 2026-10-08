"""Real reservation transactions; provider transport is always an offline fake."""
from copy import deepcopy
import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.services import framecase_kie_voice as grant, kie_voice_production as production
from app.services import kie_voice_ledger as ledger, kie_gemini_voice as gemini
from app.services import kie_voice_adapter as api, channel_commissioning, content_plan as plan
from app.services import production_spend_runtime as runtime, narrator_rotation
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_kie_voice_production import active
from test_kie_voice import kie, box, KEY


@pytest.fixture
def animation(active, monkeypatch):
    client = active.client; channel = grant.CHANNEL_ID; connection = 'framecase-connection'
    client.set(runtime._CHANNEL_PREFIX + channel, ledger.raw({'id': channel, 'connection_id': connection}))
    client.sadd(runtime._CHANNEL_INDEX, channel)
    channel_commissioning.initialize(client, {'version': 1, 'channel_id': channel,
        'connection_id': connection, 'authorized_at': ledger.now().isoformat(), 'owner_evidence_sha256': 'c'*64})
    active.context.update(channel_id=channel, connection_id=connection)
    entry = plan.item('An original mystery', 'A coherent fictional animated mystery.', format='animation')
    document = {'version': 1, 'channel_id': channel, 'revision': str(uuid4()), 'enabled': True,
        'after_queue': 'pause', 'items': [entry], 'updated_at': ledger.now().isoformat()}
    spec = {'mode': 'production', 'framecase_animation': True, 'content_style': 'original_animation',
        'production_scheduled': True, 'publish_after_render': True, 'language': 'en',
        'production_channel_id': channel, 'production_connection_id': connection,
        'content_plan_item_id': entry['id'], 'format': 'shorts', 'duration_minutes': .5}
    root = active.context['lineage_id']
    source = {'task_id': root, 'parent_id': None, 'kind': 'render', 'spec': spec}
    dispatch = {'task_id': root, 'channel_id': channel, 'connection_id': connection,
        'item': entry, 'spec_sha256': plan._sha(spec)}
    for key, value in ((runtime._JOB_PREFIX+root, source), (plan.DISPATCH_PREFIX+entry['id'], dispatch),
                       (plan.PLAN_PREFIX+channel, document)):
        client.set(key, plan._raw(value))
    active.proof = Mock(return_value=(active.activation['voices']['en']['proof_sha256'], {}, {}))
    monkeypatch.setattr(production, '_proof', active.proof)
    return active


def enable(animation):
    return grant.commission(animation.foundation, owner_evidence_sha256='d'*64)


def test_separate_grant_rechecks_listening_proof_without_new_funds_or_calls(animation):
    before = {key: animation.client.get(key) for key in (
        ledger.POLICY_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY, ledger.ACTIVE_KEY)}
    cash = animation.client.hgetall(LEDGER_KEY)
    assert production.select('en') is None
    assert enable(animation)['allocation_added_microcredits'] == 0
    animation.proof.assert_called_once()
    assert {key: animation.client.get(key) for key in before} == before
    assert animation.client.hgetall(LEDGER_KEY) == cash
    assert [r.method for r in animation.requests] == ['GET']
    with pytest.raises(SpendBlocked, match='already_configured'): enable(animation)
    assert production.select('en')['voice_id'] == 'Kore'
    with animation.client.pipeline() as pipe:
        assert production.capacity(pipe, animation.foundation, grant.CHANNEL_ID, kind='shorts')['voice_provider'] == 'kie'


@pytest.mark.parametrize('prior', ['native_intent', 'native_narrator'])
def test_existing_native_episode_keeps_original_voice_and_receipts(animation, prior):
    enable(animation)
    if prior == 'native_intent':
        animation.native.append({'reservation': {'intent': {'root_lineage_id': animation.context['lineage_id']}},
                                 'settlement': None})
    else:
        animation.client.set(narrator_rotation.PREFIX+'root:'+animation.context['lineage_id'], 'original')
    original = deepcopy(animation.native)
    assert production.select('en') is None
    assert animation.native == original and not animation.client.exists(production.ROOT_PREFIX+animation.context['lineage_id'])
    assert [r.method for r in animation.requests] == ['GET']


def test_new_animation_request_shares_original_journal_and_cannot_buy_native(animation):
    enable(animation); production.select('en')
    policy = animation.client.get(ledger.POLICY_KEY)
    body, ceiling = gemini.request_body('Mira found a clue behind the locked door.', 'Kore', language='en')
    first = api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling), sleep=lambda _: None)
    assert api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling), sleep=lambda _: None) == first
    assert animation.client.get(ledger.POLICY_KEY) == policy
    assert ledger.status(animation.client)['requests'] == 1
    assert [r.method for r in animation.requests].count('POST') == 1
    with animation.client.pipeline() as pipe, pytest.raises(SpendBlocked, match='provider_pinned'):
        production.native_guard(pipe, animation.context['lineage_id'])


def test_short_qualification_cannot_admit_long_synthesis_or_claim_long_capacity(animation):
    enable(animation); animation.context['kind'] = 'long'
    before = animation.client.get(ledger.JOURNAL_KEY)
    assert production.select('en') is None
    with animation.client.pipeline() as pipe:
        assert production.capacity(pipe, animation.foundation, grant.CHANNEL_ID, kind='long') is None
        assert production.capacity(pipe, animation.foundation, grant.CHANNEL_ID) is None
    body, ceiling = gemini.request_body('A complete long-form narration.', 'Kore', language='en')
    with pytest.raises(SpendBlocked, match='long_not_qualified'):
        api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling))
    assert animation.client.get(ledger.JOURNAL_KEY) == before
    assert not animation.client.exists(production.ROOT_PREFIX+animation.context['lineage_id'])
    assert [r.method for r in animation.requests] == ['GET']


def test_paid_boundary_rejects_unqualified_language_even_with_same_voice(animation):
    enable(animation); production.select('en')
    body, ceiling = gemini.request_body('Mira kilitli kapının arkasında bir ipucu buldu.', 'Kore', language='tr')
    with pytest.raises(SpendBlocked, match='language_unverified'):
        api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling))
    assert [r.method for r in animation.requests] == ['GET']


@pytest.mark.parametrize('damage', ['missing_grant', 'missing_anchor', 'ttl', 'authority', 'connection', 'proof', 'language', 'paused', 'preview', 'dispatch'])
def test_changed_grant_binding_or_queue_never_falls_back_or_sends(animation, damage):
    enable(animation); production.select('en')
    if damage == 'missing_grant': animation.client.delete(grant.KEY, grant.ANCHOR_KEY)
    elif damage == 'missing_anchor': animation.client.delete(grant.ANCHOR_KEY)
    elif damage == 'ttl': animation.client.expire(grant.KEY, 10)
    elif damage == 'authority': animation.client.delete(channel_commissioning.PREFIX+grant.CHANNEL_ID+':active')
    elif damage == 'connection': animation.client.set(runtime._CHANNEL_PREFIX+grant.CHANNEL_ID,
        ledger.raw({'id': grant.CHANNEL_ID, 'connection_id': 'changed'}))
    elif damage == 'proof': animation.client.set(production.QUALIFICATION_PREFIX+'b'*64+':whisper', 'changed')
    elif damage == 'paused':
        key = plan.PLAN_PREFIX+grant.CHANNEL_ID; value = json.loads(animation.client.get(key))
        value['enabled'] = False; animation.client.set(key, plan._raw(value))
    elif damage in {'preview', 'dispatch'}:
        key = runtime._JOB_PREFIX+animation.context['lineage_id']; source = json.loads(animation.client.get(key))
        source['spec']['mode' if damage == 'preview' else 'duration_minutes'] = 'preview' if damage == 'preview' else .6
        animation.client.set(key, plan._raw(source))
    with pytest.raises((SpendBlocked, ValueError)):
        production.select('tr' if damage == 'language' else 'en')
    assert [r.method for r in animation.requests] == ['GET']


def test_failed_actual_qualification_does_not_create_grant(animation):
    animation.proof.side_effect = SpendBlocked('kie_voice_prosody_rejected')
    with pytest.raises(SpendBlocked, match='prosody_rejected'): enable(animation)
    assert not animation.client.exists(grant.KEY, grant.ANCHOR_KEY)


def test_unknown_animation_post_keeps_full_hold_and_cannot_switch_or_repeat(animation):
    enable(animation); production.select('en')
    def timeout(request):
        animation.requests.append(request); raise httpx.ReadTimeout('unknown')
    animation.handler = timeout
    body, ceiling = gemini.request_body('Mira found the final clue.', 'Kore', language='en')
    with pytest.raises(api.KieVoiceError):
        api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling))
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        api.generate(body, KEY, ledger.Journal(animation.foundation, animation.context, body, ceiling))
    state = ledger.status(animation.client)
    assert state['committed_microcredits'] == ceiling and state['unknown_or_pending'] == 1
    assert [r.method for r in animation.requests].count('POST') == 1
