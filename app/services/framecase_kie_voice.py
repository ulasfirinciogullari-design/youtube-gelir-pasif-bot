"""A separate Framecase channel grant within the existing prepaid voice pool.

Default off. The operator rechecks the retained English listening evidence;
this grant adds no funds, changes no prior policy and never moves native roots.
Every film still requires its own transcript, performance and final-mix QA.
"""
import json
import re

from app.services import kie_voice_ledger as ledger
from app.services.framecase_cadence import CHANNEL_ID

KEY = ledger.PREFIX + 'framecase_channel'
ANCHOR_KEY = KEY + ':anchor'


def read(pipe, policy=None, active=None):
    pipe.watch(KEY, ANCHOR_KEY)
    encoded, anchor = pipe.get(KEY), pipe.get(ANCHOR_KEY)
    if encoded is None and anchor is None:
        return None
    ledger.require(type(encoded) is str and len(encoded) <= 4096
        and anchor == ledger.sha(encoded) and pipe.pttl(KEY) == pipe.pttl(ANCHOR_KEY) == -1,
        'framecase_kie_grant_unverified')
    if policy is None:
        pipe.watch(ledger.POLICY_KEY)
        policy = json.loads(pipe.get(ledger.POLICY_KEY))
    if active is None:
        from app.services.kie_voice_production import activation
        active = activation(pipe, policy)
    ledger.require(active is not None, 'framecase_kie_activation_missing')
    record = json.loads(encoded)
    ledger.require(type(record) is dict and set(record) == {
        'version', 'purpose', 'channel_id', 'connection_id', 'language',
        'policy_sha256', 'activation_sha256', 'voice', 'continuation_sha256',
        'authorized_at', 'owner_evidence_sha256', 'allocation_added_microcredits'}
        and type(record['version']) is int and record['version'] == 1
        and record['purpose'] == 'framecase_original_animation_voice'
        and record['channel_id'] == CHANNEL_ID and record['language'] == 'en'
        and record['policy_sha256'] == ledger.sha(ledger.raw(policy))
        and record['activation_sha256'] == ledger.sha(ledger.raw(active))
        and record['voice'] == active['voices']['en']
        and type(record['allocation_added_microcredits']) is int
        and record['allocation_added_microcredits'] == 0
        and type(record['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', record['owner_evidence_sha256']),
        'framecase_kie_grant_unverified')
    ledger._stamp(record['authorized_at'])
    authority = ledger._connected_owner(pipe, CHANNEL_ID, record['connection_id'])
    ledger.require(authority == record['continuation_sha256'], 'framecase_kie_authority_changed')
    return record


def authorize_context(pipe, context):
    """Only the channel's frozen, owner-enabled original-animation queue."""
    from app.services import content_plan as plan, production_spend_runtime as runtime
    ledger.require(context['channel_id'] == CHANNEL_ID and context['kind'] in {'shorts', 'long'})
    key = runtime._JOB_PREFIX + context['lineage_id']; pipe.watch(key)
    source = plan._object(pipe.get(key)); spec = source.get('spec') or {}
    ledger.require(source.get('task_id') == context['lineage_id'] and source.get('parent_id') is None
        and source.get('kind') == 'render' and spec.get('mode') == 'production'
        and spec.get('framecase_animation') is True and spec.get('content_style') == 'original_animation'
        and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
        and spec.get('language') == 'en' and spec.get('production_channel_id') == CHANNEL_ID
        and spec.get('production_connection_id') == context['connection_id']
        and not source.get('owner_cancellation') and not source.get('publication_hold'),
        'framecase_kie_context_unverified')
    item = plan._id(spec.get('content_plan_item_id'))
    pipe.watch(plan.DISPATCH_PREFIX + item, plan.PLAN_PREFIX + CHANNEL_ID)
    dispatch = plan._object(pipe.get(plan.DISPATCH_PREFIX + item))
    document = plan._plan(pipe.get(plan.PLAN_PREFIX + CHANNEL_ID), CHANNEL_ID)
    ledger.require(document['enabled'] is True and dispatch.get('task_id') == context['lineage_id']
        and dispatch.get('channel_id') == CHANNEL_ID and plan.dispatch_spec_matches(dispatch, spec)
        and dispatch['item']['format'] == ('long' if context['kind'] == 'long' else 'animation')
        and dispatch['item'] in document['items'], 'framecase_kie_context_unverified')


def commission(foundation, *, owner_evidence_sha256):
    """One private operator action; no provider request or new allocation."""
    from app.services import kie_voice_production as production, kie_gemini_voice as gemini
    from app.services import production_spend_runtime as runtime
    ledger.require(type(owner_evidence_sha256) is str
        and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    with foundation.client.pipeline() as pipe:
        policy, journal, _ = ledger._read(pipe)
        ledger.require(policy is not None)
        ledger._foundation(pipe, foundation)
        active = production.activation(pipe, policy)
        ledger.require(active is not None and read(pipe, policy, active) is None,
                       'framecase_kie_already_configured')
        ledger.require(not any(r['scope'].get('channel_id') == CHANNEL_ID
            for r in journal['requests'].values()), 'framecase_kie_history_already_exists')
        voice = active['voices']['en']
        proof, _, _ = production._proof(pipe, 'en', voice['probe_identity'])
        ledger.require(proof == voice['proof_sha256'], 'framecase_kie_qualification_changed')
        key = runtime._CHANNEL_PREFIX + CHANNEL_ID; pipe.watch(key)
        connection = json.loads(pipe.get(key))['connection_id']
        authority = ledger._connected_owner(pipe, CHANNEL_ID, connection)
        ledger.require(ledger._used(journal) + 3 * gemini.SPEC['maximum_microcredits']
            <= policy['allocation_microcredits'], 'kie_voice_balance_exhausted')
        record = {'version': 1, 'purpose': 'framecase_original_animation_voice',
            'channel_id': CHANNEL_ID, 'connection_id': connection, 'language': 'en',
            'policy_sha256': ledger.sha(ledger.raw(policy)), 'activation_sha256': ledger.sha(ledger.raw(active)),
            'voice': voice, 'continuation_sha256': authority, 'authorized_at': ledger.now().isoformat(),
            'owner_evidence_sha256': owner_evidence_sha256, 'allocation_added_microcredits': 0}
        encoded = ledger.raw(record)
        pipe.multi(); pipe.set(KEY, encoded, nx=True); pipe.set(ANCHOR_KEY, ledger.sha(encoded), nx=True)
        ledger.require(pipe.execute() == [True, True], 'framecase_kie_grant_write_uncertain')
    return {'status': 'active', 'channel_id': CHANNEL_ID, 'language': 'en',
            'voice': voice['voice_id'], 'allocation_added_microcredits': 0}
