"""Explicit Framecase long narration grant using qualified full English audio.

No new allocation or edits to the original documentary/channel activations.
Shorts keep their own voice route, and every long film still runs its own QA.
"""
import json
import re

from app.services import fal_voice_adapter as api, kie_voice_ledger as ledger
from app.services.framecase_cadence import CHANNEL_ID

KEY = 'youtube_studio:commissioning:v1:fal_voice_production:framecase_channel'
ANCHOR_KEY = KEY + ':anchor'


def read(pipe, active):
    if active is None or active['language'] != 'en':
        return None
    pipe.watch(KEY, ANCHOR_KEY)
    encoded, anchor = pipe.get(KEY), pipe.get(ANCHOR_KEY)
    if encoded is None and anchor is None:
        return None
    api.require(type(encoded) is str and len(encoded) <= 4096
        and anchor == ledger.sha(encoded) and pipe.pttl(KEY) == pipe.pttl(ANCHOR_KEY) == -1,
        'framecase_fal_grant_unverified')
    record = json.loads(encoded)
    api.require(type(record) is dict and set(record) == {
        'version', 'purpose', 'channel_id', 'connection_id', 'language', 'kind',
        'activation_sha256', 'voice', 'continuation_sha256', 'authorized_at',
        'owner_evidence_sha256', 'allocation_added_micro_usd'}
        and type(record['version']) is int and record['version'] == 1
        and record['purpose'] == 'framecase_qualified_long_animation_voice'
        and record['channel_id'] == CHANNEL_ID and record['language'] == 'en'
        and record['kind'] == 'long' and active['model'] == api.MODEL
        and record['activation_sha256'] == ledger.sha(ledger.raw(active))
        and record['voice'] == active['voice'] == api.VOICES['en']
        and type(record['allocation_added_micro_usd']) is int
        and record['allocation_added_micro_usd'] == 0
        and type(record['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', record['owner_evidence_sha256']),
        'framecase_fal_grant_unverified')
    ledger._stamp(record['authorized_at'])
    api.require(ledger._connected_owner(pipe, CHANNEL_ID, record['connection_id'])
        == record['continuation_sha256'], 'framecase_fal_authority_changed')
    return record


def authorize_context(pipe, context):
    """The shared long-form authorizer separately verifies order and dispatch."""
    from app.services import content_plan as plan, production_spend_runtime as runtime
    api.require(context['channel_id'] == CHANNEL_ID and context['kind'] == 'long',
                'framecase_fal_context_unverified')
    key = runtime._JOB_PREFIX + context['lineage_id']; pipe.watch(key)
    source = plan._object(pipe.get(key)); spec = source.get('spec') or {}
    api.require(source.get('task_id') == context['lineage_id'] and source.get('parent_id') is None
        and source.get('kind') == 'render' and spec.get('mode') == 'production'
        and spec.get('framecase_animation') is True and spec.get('content_style') == 'original_animation'
        and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
        and spec.get('language') == 'en' and spec.get('format') == 'landscape'
        and spec.get('duration_minutes') == 3 and spec.get('production_channel_id') == CHANNEL_ID
        and spec.get('production_connection_id') == context['connection_id']
        and not source.get('owner_cancellation') and not source.get('publication_hold'),
        'framecase_fal_context_unverified')


def commission(foundation, *, owner_evidence_sha256):
    """Operator only: recheck full qualification, add two immutable records."""
    from app.services import fal_voice_production as production, fal_voice_trial as trial
    from app.services import fal_voice_qualification as qualification
    from app.services import content_plan as plan, production_spend_runtime as runtime
    api.require(type(owner_evidence_sha256) is str
        and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    with foundation.client.pipeline() as pipe:
        active = production.activation(pipe, 'en')
        api.require(active is not None, 'framecase_fal_english_not_qualified')
        rows = trial._read(pipe, trial._key('en'))
        pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
    # The activation may rely on the separately recorded full-audio name
    # reassessment. Reuse the validated active proof without changing either
    # its old negative assessment or the original documentary channel scope.
    candidates = [rows[k] for k in ('qualification', 'reassessment') if k in rows
        and ledger.sha(ledger.raw(rows[k])) == active['qualification']['qualification_sha256']]
    api.require(len(candidates) == 1, 'framecase_fal_qualification_changed')
    record = candidates[0]
    journal = trial.Journal(foundation, rows['grant']['source_task_id'], record['body'], production.settings.fal_key)
    proof = qualification.verified(journal)
    api.require(proof == active['qualification'], 'framecase_fal_qualification_changed')
    with foundation.client.pipeline() as pipe:
        pipe.watch(KEY, ANCHOR_KEY)
        api.require(not pipe.exists(KEY, ANCHOR_KEY), 'framecase_fal_already_configured')
        ledger._foundation(pipe, foundation)
        api.require(production.activation(pipe, 'en') == active)
        api.require(trial._read(pipe, trial._key('en')) == rows)
        key = runtime._CHANNEL_PREFIX + CHANNEL_ID; pipe.watch(key)
        connection = json.loads(pipe.get(key))['connection_id']
        authority = ledger._connected_owner(pipe, CHANNEL_ID, connection)
        keys = (plan.PLAN_PREFIX + CHANNEL_ID, plan.production.PROFILE_PREFIX + CHANNEL_ID)
        pipe.watch(*keys); document, profile = [json.loads(pipe.get(k)) for k in keys]
        api.require(document['enabled'] is True and profile['production_enabled'] is True
            and profile['auto_publish'] is True and profile['language'] == 'en')
        value = {'version': 1, 'purpose': 'framecase_qualified_long_animation_voice',
            'channel_id': CHANNEL_ID, 'connection_id': connection, 'language': 'en', 'kind': 'long',
            'activation_sha256': ledger.sha(ledger.raw(active)), 'voice': active['voice'],
            'continuation_sha256': authority, 'authorized_at': ledger.now().isoformat(),
            'owner_evidence_sha256': owner_evidence_sha256, 'allocation_added_micro_usd': 0}
        encoded = ledger.raw(value)
        pipe.multi(); pipe.set(KEY, encoded, nx=True); pipe.set(ANCHOR_KEY, ledger.sha(encoded), nx=True)
        api.require(pipe.execute() == [True, True], 'framecase_fal_grant_write_uncertain')
    return value
