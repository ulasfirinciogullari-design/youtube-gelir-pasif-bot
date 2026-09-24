"""Explicit editorial replacement; old media, refusals and budgets stay intact."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid5, NAMESPACE_URL
from app.services import content_plan as plan, studio_state as jobs
from app.services.framecase_cadence import CHANNEL_ID

PREFIX = 'youtube_studio:framecase_editorial_revision:v2:'
SOURCE_ID = '093514e3-fefb-5cd4-b664-3a0087c3fd16'
PREVIEW_ID = '27507bbc-1798-58f5-96bc-5028aff6b4bf'
APPROVED_MASTER = 'e7653fe13e3f2253268d21cdecb38d43487cdc99f4178ec0bf6e50e366b13f46'
OWNER_FEEDBACK = 'tmm okey izledim iyi devam et'


def _require(value):
    plan._require(value, 'framecase_editorial_revision_unverified')


def retained_source(dispatch):
    from app.services.framecase_pipeline import PREFIX as cp_prefix
    client = plan._client(); encoded = client.get(PREFIX + 'seed:' + dispatch['item']['id'])
    if encoded is None:
        return None
    seed = json.loads(encoded)
    _require(seed.get('channel_id') == CHANNEL_ID and seed.get('item_sha256') == plan._sha(dispatch['item'])
             and seed.get('source_task_id') == SOURCE_ID and seed.get('approved_master_sha256') == APPROVED_MASTER)
    original = client.get(cp_prefix + 'checkpoint:' + SOURCE_ID)
    _require(original is not None and hashlib.sha256(original.encode()).hexdigest() == seed['checkpoint_sha256'])
    cp = json.loads(original)
    _require(cp['voice']['asset'] == seed['voice_asset'] and cp.get('audio_qc'))
    return {'source_task_id': SOURCE_ID, 'seed_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
            'voice': deepcopy(cp['voice']), 'audio_qc': deepcopy(cp['audio_qc']),
            'scenes': [s['narration'] for s in cp['package']['scenes']]}


def activate(expected_plan_revision, *, owner_feedback, client=None):
    """Operator-only CAS; do not call this from automated recovery."""
    from app.services.framecase_art import VERSION, approved_reference
    from app.services.framecase_pipeline import PREFIX as cp_prefix
    from app.services.commissioning_video import PREFIX as video_prefix
    client = client or plan._client(); _require(owner_feedback == OWNER_FEEDBACK)
    approval = {'owner_feedback': owner_feedback, 'preview_task_id': PREVIEW_ID,
        'master_sha256': APPROVED_MASTER, 'art_direction': VERSION,
        'reference_sha256': hashlib.sha256(approved_reference()).hexdigest()}
    archive_key = PREFIX + 'activation'
    with client.pipeline() as pipe:
        pipe.watch(plan.PLAN_PREFIX + CHANNEL_ID, plan.ACTIVE_KEY, jobs.JOB_PREFIX + SOURCE_ID,
            jobs.JOB_PREFIX + PREVIEW_ID, cp_prefix + 'checkpoint:' + SOURCE_ID,
            video_prefix + SOURCE_ID, archive_key)
        if pipe.exists(archive_key):
            saved = json.loads(pipe.get(archive_key)); _require(saved['approval'] == approval)
            return {'status': 'already_activated', 'item_id': saved['replacement_item']['id']}
        document = plan.read(CHANNEL_ID, client=pipe)
        source = json.loads(pipe.get(jobs.JOB_PREFIX + SOURCE_ID) or '{}')
        preview = json.loads(pipe.get(jobs.JOB_PREFIX + PREVIEW_ID) or '{}')
        cp_raw = pipe.get(cp_prefix + 'checkpoint:' + SOURCE_ID); cp = json.loads(cp_raw or '{}')
        native_raw = pipe.get(video_prefix + SOURCE_ID); native = json.loads(native_raw or '{}')
        _require(document is not None and document['revision'] == expected_plan_revision and document['enabled'] is False
            and source.get('state') == 'FAILURE' and source.get('publication_hold')
            and source['spec'].get('publish_after_render') is False and source['spec'].get('production_channel_id') == CHANNEL_ID)
        _require(preview.get('state') == 'SUCCESS' and preview.get('result', {}).get('video_sha256') == APPROVED_MASTER
            and preview['result'].get('creative_qc', {}).get('pass') is True)
        _require(cp.get('voice', {}).get('asset') and cp.get('audio_qc') and native.get('requests')
            and all(r.get('create') is not None and r.get('result') is not None for r in native['requests'].values()))
        old_id = source['spec']['content_plan_item_id']
        index = next((i for i, v in enumerate(document['items']) if v['id'] == old_id), None)
        _require(index == 1 and len(document['items']) == 6)
        old_item = document['items'][index]
        _require(old_item['series']['number'] == 2 and old_item['format'] == 'animation')
        pipe.watch(plan.COMPLETION_PREFIX + old_id, plan.COMPLETION_PREFIX + document['items'][0]['id'])
        _require(not pipe.exists(plan.COMPLETION_PREFIX + old_id) and pipe.exists(plan.COMPLETION_PREFIX + document['items'][0]['id']))
        new_id = str(uuid5(NAMESPACE_URL, 'framecase-owner-art-revision:' + old_id + ':' + VERSION))
        replacement = deepcopy(old_item); replacement['id'] = new_id
        replacement['brief'] = ('OWNER-APPROVED VISUAL REVISION ' + VERSION + '. Preserve the canonical episode and exact accepted '
            'narration. Draw every new shot from the approved cast reference.\n' + old_item['brief'])[:4000]
        current = deepcopy(document); current['items'][index] = replacement
        for row in current['items'][index + 1:]:
            pipe.watch(plan.DISPATCH_PREFIX + row['id'], plan.COMPLETION_PREFIX + row['id'])
            _require(not pipe.exists(plan.DISPATCH_PREFIX + row['id'], plan.COMPLETION_PREFIX + row['id']))
            row['depends_on'] = [new_id if value == old_id else value for value in row['depends_on']]
        current.update(enabled=True, revision=str(uuid5(NAMESPACE_URL, 'framecase-art-plan:' + document['revision'])),
                       updated_at=datetime.now(timezone.utc).isoformat())
        plan._plan(plan._raw(current), CHANNEL_ID)
        seed_key = PREFIX + 'seed:' + new_id
        pipe.watch(seed_key, plan.DISPATCH_PREFIX + new_id, plan.COMPLETION_PREFIX + new_id)
        _require(not pipe.exists(seed_key, plan.DISPATCH_PREFIX + new_id, plan.COMPLETION_PREFIX + new_id))
        active = plan._active(pipe); _require(active.get(CHANNEL_ID) in (None, old_id)); active.pop(CHANNEL_ID, None)
        seed = {'version': 2, 'channel_id': CHANNEL_ID, 'source_task_id': SOURCE_ID,
            'item_sha256': plan._sha(replacement), 'approved_master_sha256': APPROVED_MASTER,
            'checkpoint_sha256': hashlib.sha256(cp_raw.encode()).hexdigest(), 'voice_asset': cp['voice']['asset']}
        archive = {'approval': approval, 'old_plan': document, 'old_source': source,
            'old_checkpoint_sha256': seed['checkpoint_sha256'], 'old_native_sha256': hashlib.sha256(native_raw.encode()).hexdigest(),
            'replacement_item': replacement, 'new_plan_revision': current['revision'], 'activated_at': current['updated_at']}
        pipe.multi(); pipe.set(archive_key, plan._raw(archive), nx=True); pipe.set(seed_key, plan._raw(seed), nx=True)
        pipe.set(plan.PLAN_PREFIX + CHANNEL_ID, plan._raw(current)); pipe.set(plan.ACTIVE_KEY, plan._raw(active))
        _require(pipe.execute() == [True, True, True, True])
    return {'status': 'activated', 'item_id': new_id, 'plan_revision': current['revision'],
            'old_source_held': True, 'reused_voice': True, 'provider_requests': 0}
