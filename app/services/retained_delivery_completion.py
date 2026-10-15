"""Record one observed public retained delivery without starting new production.

The child and normal YouTube registry change atomically only after all six
ordered transport receipts. A repeated read can recover the durable outcome;
it cannot upload, release, renew a claim or modify the scheduler.
"""
from copy import deepcopy
from datetime import datetime
import os
import re

from app.services import retained_production_admission as admission
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_publication_plan as publication
from app.services import retained_publication_transport as transport
from app.services import youtube_publish_state as uploads
from app.services import studio_state

COMPLETION_KEY = admission.PREFIX + ':complete'


class RetainedCompletionError(RuntimeError):
    pass


def _require(value):
    if not value:
        raise RetainedCompletionError('retained_delivery_completion_unverified')


def _stamp(value):
    _require(type(value) is str)
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed


def _public_receipts(pipe, manifest, plan_record, execution):
    results = {}
    for phase in transport._PHASES:
        intent = delivery._stored(pipe, transport._key(phase, 'intent'))
        result = delivery._stored(pipe, transport._key(phase, 'result'))
        _stamp(intent['created_at'])
        _stamp(result['observed_at'])
        _require(type(intent['version']) is type(result['version']) is int
            and intent == {'version': 1, 'phase': phase, 'child_id': manifest['child_id'],
                'plan_sha256': admission._hash(plan_record),
                'execution_sha256': admission._hash(execution),
                'previous_results_sha256': admission._hash(results),
                'created_at': intent['created_at'], 'automatic_retry_permitted': False}
            and set(result) == {'version', 'phase', 'intent_sha256', 'observed', 'observed_at'}
            and result['version'] == 1 and result['phase'] == phase
            and result['intent_sha256'] == admission._hash(intent)
            and type(result['observed']) is dict)
        results[phase] = result
    video_id = transport._id(results['upload']['observed']['video_id'])
    _require(results['upload']['observed'] == {'video_id': video_id})
    for phase, privacy in (('private_status', 'private'), ('public_status', 'public')):
        value = results[phase]['observed']
        _require(value.get('upload_status') in ('uploaded', 'processed')
            and value.get('contains_synthetic_media') is True
            and value == {'video_id': video_id, 'privacy_status': privacy,
                'contains_synthetic_media': True, 'upload_status': value['upload_status']})
    caption = results['captions']['observed']
    _require(caption == {'video_id': video_id, 'caption_id': transport._caption_id(caption['caption_id']),
        'language': plan_record['plan']['default_language']})
    thumbnail = 'uploaded' if plan_record['plan']['require_thumbnail'] else 'not_required'
    _require(results['thumbnail']['observed'] == {'video_id': video_id, 'status': thumbnail}
        and results['release']['observed'].get('contains_synthetic_media') is True
        and results['release']['observed'] == {'video_id': video_id, 'privacy_status': 'public',
            'contains_synthetic_media': True})
    receipt = delivery._stored(pipe, transport.PUBLIC_RECEIPT_KEY)
    _stamp(receipt['completed_at'])
    _require(type(receipt['version']) is int and receipt.get('contains_synthetic_media') is True
        and receipt.get('captions_uploaded') is True and receipt.get('resume_authorized') is False
        and receipt.get('next_production_authorized') is False and receipt.get('automatic_retry_permitted') is False
        and receipt == {'version': 1, 'kind': 'retained_public_delivery',
            'child_id': manifest['child_id'], 'original_task_id': admission.continuity.ROOT_ID,
            'video_id': video_id, 'target_channel_id': admission.continuity.CHANNEL_ID,
            'connection_id': plan_record['connection_id'], 'authorization_epoch': plan_record['authorization_epoch'],
            'plan_sha256': admission._hash(plan_record), 'execution_sha256': admission._hash(execution),
            'manifest_sha256': admission._hash(manifest),
            'result_hashes': {k: admission._hash(v) for k, v in results.items()},
            'privacy_status': 'public', 'contains_synthetic_media': True, 'captions_uploaded': True,
            'thumbnail_status': thumbnail, 'completed_at': receipt['completed_at'],
            'resume_authorized': False, 'next_production_authorized': False, 'automatic_retry_permitted': False})
    return receipt


def _project(manifest, plan_record, public):
    plan, stamp = plan_record['plan'], public['completed_at']
    artifacts = manifest['prepared_final']['artifacts']
    child_id, video_id = manifest['child_id'], public['video_id']
    attribution = {'video_id': video_id, 'url': 'https://www.youtube.com/watch?v=' + video_id,
        'privacy_status': 'public', 'release_status': 'public', 'contains_synthetic_media': True,
        'uploaded_at': stamp, 'caption_uploaded': True,
        'thumbnail_uploaded': public['thumbnail_status'] == 'uploaded',
        'target_channel_id': plan['target_channel_id'], 'connection_id': plan_record['connection_id'],
        'title': plan['title'], 'series': plan['series'], 'profile_revision': plan['profile_revision'],
        'default_language': plan['default_language'], 'category_id': plan['category_id']}
    result = {'status': 'complete', 'stage': 'complete', 'progress': 100, 'task_id': child_id,
        'channel_id': plan['target_channel_id'], 'title': plan['title'],
        'video_key': artifacts['video']['key'], 'caption_key': artifacts['captions']['key'],
        'thumbnail_key': artifacts['thumbnail']['key'], 'metadata_key': artifacts['metadata']['key'],
        'quality_disposition': 'retained_component_review_pass', 'manual_qa_required': False,
        'quality_snapshot': plan['quality_snapshot'], 'burned_subtitles': False,
        'youtube': attribution, 'retained_public_receipt_sha256': admission._hash(public),
        'resume_authorized': False, 'next_production_authorized': False}
    child = {**deepcopy(manifest['pending_child']), 'state': 'SUCCESS', 'stage': 'complete',
        'progress': 100, 'message': 'Video yayımlandı.', 'result': result, 'error': None,
        'updated_at': stamp, 'finished_at': stamp}
    upload = {'version': 2, 'source_task_id': child_id, 'publish_task_id': child_id,
        'status': 'complete', 'side_effect_possible': True, 'youtube_video_id': video_id,
        'target_channel_id': plan['target_channel_id'], 'connection_id': plan_record['connection_id'],
        'publish_plan': plan, 'privacy_status': 'public', 'release_status': 'public',
        'release_side_effect_possible': True, 'release_mode': 'public', 'scheduled_publish_at': None,
        'contains_synthetic_media': True, 'created_at': stamp, 'updated_at': stamp,
        'completed_at': stamp, 'release_completed_at': stamp,
        'retained_public_receipt_sha256': admission._hash(public)}
    receipt = {'version': 1, 'kind': 'retained_delivery_complete', 'child_id': child_id,
        'manifest_sha256': admission._hash(manifest), 'plan_sha256': admission._hash(plan_record),
        'public_receipt_sha256': admission._hash(public), 'child_sha256': admission._hash(child),
        'upload_record_sha256': admission._hash(upload), 'video_id': video_id,
        'completed_at': stamp, 'resume_authorized': False, 'next_production_authorized': False,
        'automatic_retry_permitted': False}
    return child, upload, receipt


def _completion_evidence(pipe, child_id, manifest_sha256):
    """Verify permanent delivery evidence, independently of today's settings."""
    admission._uuid(child_id)
    _require(type(manifest_sha256) is str and re.fullmatch('[0-9a-f]{64}', manifest_sha256))
    manifest = delivery._stored(pipe, admission.MANIFEST_KEY)
    _require(manifest['child_id'] == child_id and admission._hash(manifest) == manifest_sha256
        and type(manifest['version']) is int and manifest['version'] == 1
        and manifest['kind'] == 'retained_final_delivery'
        and manifest['root_id'] == admission.continuity.ROOT_ID
        and manifest['leaf_id'] == admission.continuity.LEAF_ID
        and manifest['authority'] == admission._AUTHORITY
        and type(manifest['source_head_sha']) is str
        and re.fullmatch('[0-9a-f]{40}', manifest['source_head_sha']))
    journal = delivery._stored(pipe, admission.JOURNAL_KEY)
    _require(type(journal['version']) is int and journal == {
        'version': 1, 'phase': 'claimed', 'manifest_sha256': manifest_sha256,
        'child_sha256': admission._hash(manifest['pending_child']),
        'leaf_sha256': admission._hash(manifest['claimed_leaf']),
        'dispatch_sha256': admission._hash(manifest['dispatch'])})
    pipe.watch(admission.ANCHOR_KEY)
    _require(pipe.pttl(admission.ANCHOR_KEY) == -1
        and pipe.get(admission.ANCHOR_KEY) == admission._hash(journal)
        and delivery._stored(pipe, admission.CHILD_PREFIX + child_id) == {
            'version': 1, 'root_id': admission.continuity.ROOT_ID,
            'child_id': child_id, 'manifest_sha256': manifest_sha256})
    execution = delivery._stored(pipe, delivery.EXECUTION_KEY)
    intent = delivery._stored(pipe, delivery.DISPATCH_KEY)
    plan_record = delivery._stored(pipe, publication.PLAN_KEY)
    _require(intent == delivery._intent(manifest)
        and execution['child_id'] == child_id and execution['manifest_sha256'] == manifest_sha256
        and execution['queue_intent_sha256'] == admission._hash(intent)
        and plan_record['manifest_sha256'] == manifest_sha256
        and plan_record['execution_sha256'] == admission._hash(execution))
    public = _public_receipts(pipe, manifest, plan_record, execution)
    child, upload, expected = _project(manifest, plan_record, public)
    _require(delivery._stored(pipe, COMPLETION_KEY) == expected)
    return manifest, plan_record, child, upload, expected


def _read_completed(pipe, child_id, manifest_sha256):
    manifest, plan_record, child, upload, expected = _completion_evidence(pipe, child_id, manifest_sha256)
    _require(os.environ.get('RAILWAY_GIT_COMMIT_SHA') == manifest['source_head_sha'])
    current, _ = admission._read_projection(pipe, completed_child=child, completed_upload=upload)
    _require(current == manifest)
    keys = plan_record['series_keys']
    pipe.watch(*keys, studio_state.JOB_INDEX)
    _require(pipe.mget(keys[:2]) == ['5', '5'] and pipe.exists(*keys[2:]) == 0
        and all(type(t) is int and t == -1 for t in (pipe.pttl(k) for k in keys[:2]))
        and pipe.zscore(studio_state.JOB_INDEX, child_id) == manifest['pending_child']['created_ts'])
    return expected


def _read_history(pipe, child_id, manifest_sha256):
    manifest, plan_record, child, upload, receipt = _completion_evidence(pipe, child_id, manifest_sha256)
    _require(delivery._stored(pipe, admission.continuity._JOB + child_id) == child
        and delivery._stored(pipe, uploads._key(child_id)) == upload)
    series = plan_record['plan']['series']
    keys = publication._series_keys({'series_id': series['id'], 'series_total': series['total']}, child_id)
    _require(plan_record['series_keys'] == list(keys) and type(series['number']) is int
        and series['number'] == 5)
    # The individual allocation is permanent. The shared counter, profile,
    # OAuth epoch, budget and job-list index legitimately change after delivery.
    pipe.watch(keys[1])
    _require(pipe.pttl(keys[1]) == -1 and pipe.get(keys[1]) == '5')
    return receipt


def read_retained_publication_history(client, child_id, manifest_sha256):
    """Read a committed past publication after deployments or later episodes.

    This proves what was observed at completion, not its current YouTube
    visibility or authority to publish, spend, resume or dispatch another job.
    The strict current-state reader remains mandatory when committing delivery.
    """
    try:
        with client.pipeline() as pipe:
            receipt = _read_history(pipe, child_id, manifest_sha256)
            admission._read_ack(pipe)
        return deepcopy(receipt)
    except Exception:
        raise RetainedCompletionError('retained_delivery_completion_unverified') from None


def read_retained_delivery_completion(client, child_id, manifest_sha256):
    """Read a fully bound known outcome after a missing task acknowledgement."""
    try:
        with client.pipeline() as pipe:
            receipt = _read_completed(pipe, child_id, manifest_sha256)
            admission._read_ack(pipe)
        return deepcopy(receipt)
    except Exception:
        raise RetainedCompletionError('retained_delivery_completion_unverified') from None


def complete_retained_publication(prepared):
    """Commit only the actual public receipt; no external call or scheduler write."""
    try:
        checked = publication._checked(prepared)
        running = delivery._execution(checked['execution'])
        manifest = running['manifest']
        state = {'running': running, 'record': checked['record'], 'files': checked['files']}
        with running['client'].pipeline() as pipe:
            transport._authority(pipe, state)
            public = _public_receipts(pipe, manifest, checked['record'], running['execution'])
            child, upload, receipt = _project(manifest, checked['record'], public)
            upload_key = uploads._key(manifest['child_id'])
            pipe.watch(COMPLETION_KEY, upload_key, studio_state.JOB_INDEX)
            _require(pipe.exists(COMPLETION_KEY, upload_key) == 0
                     and pipe.zscore(studio_state.JOB_INDEX, manifest['child_id']) is None)
            pipe.multi()
            pipe.set(COMPLETION_KEY, admission._raw(receipt).decode(), nx=True)
            pipe.set(admission.continuity._JOB + manifest['child_id'], admission._raw(child).decode())
            pipe.set(upload_key, admission._raw(upload).decode(), nx=True)
            pipe.zadd(studio_state.JOB_INDEX, {manifest['child_id']: manifest['pending_child']['created_ts']})
            admission._ack(pipe.execute(), [True, True, True, 1])
        return read_retained_delivery_completion(running['client'], manifest['child_id'], admission._hash(manifest))
    except Exception:
        raise RetainedCompletionError('retained_delivery_completion_unverified') from None
