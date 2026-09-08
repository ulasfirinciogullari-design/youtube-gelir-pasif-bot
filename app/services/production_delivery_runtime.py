"""Cloud-only, once-claimed fan-out to three private review candidates.

An unknown enqueue/render outcome stays visible and is never interpreted as a
reason to repurchase the parent. Publication remains a separate reviewed step.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

import redis

from app.config import settings
from app.services.production_delivery import CONTRACT, DeliveryPlanError, digest, delivery_requested
from app.services.production_derivatives import render_candidates, validate_manifest
from app.services.studio_state import (
    JOB_INDEX, JOB_PREFIX, JOB_TTL_SECONDS, list_jobs, RENDER_CANCELLATION_PREFIX,
)
from app.services.channel_production import (
    PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX, OAUTH_CREDENTIAL_PREFIX, OAUTH_CHANNEL_INDEX,
)
from app.services.storage import download_file, upload_file, presigned_download_url


FAMILY_PREFIX = 'youtube_studio:delivery:v1:'
TASK_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def _require(value, reason='delivery_source_unavailable'):
    if not value:
        raise DeliveryPlanError(reason)


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def enabled():
    return (getattr(settings, 'studio_longform_delivery_enabled', False) is True
            and getattr(settings, 'studio_spend_enforcement', False) is True)


def _source(client, source_id):
    _require(type(source_id) is str and TASK_PATTERN.fullmatch(source_id))
    job = json.loads(client.get(JOB_PREFIX + source_id) or '{}')
    spec, result = job.get('spec') or {}, job.get('result') or {}
    _require(job.get('task_id') == source_id and job.get('state') == 'SUCCESS'
             and job.get('kind') == 'render' and not job.get('parent_id')
             and type(spec) is dict and type(result) is dict
             and spec.get('production_delivery') == CONTRACT
             and spec.get('duration_minutes') == 8 and spec.get('format') == 'landscape'
             and spec.get('mode') == 'production' and spec.get('production_scheduled') is True
             and result.get('quality_disposition') == 'automated_qc_pass'
             and result.get('manual_qa_required') is False
             and result.get('delivery_status') == 'awaiting_portrait_render_and_review')
    delivery_requested(spec, spec['duration_minutes'])
    _require(not client.exists(RENDER_CANCELLATION_PREFIX + source_id), 'delivery_source_cancelled')
    channel_id, connection_id = spec.get('production_channel_id'), spec.get('production_connection_id')
    _require(type(channel_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', channel_id)
             and type(connection_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', connection_id))
    profile = json.loads(client.get(PROFILE_PREFIX + channel_id) or '{}')
    channel = json.loads(client.get(OAUTH_CHANNEL_PREFIX + channel_id) or '{}')
    _require(profile.get('channel_id') == channel_id and profile.get('production_enabled') is True
             and profile.get('auto_publish') is True
             and profile.get('profile_revision') == spec.get('production_profile_revision')
             and channel.get('id') == channel_id and channel.get('connection_id') == connection_id
             and client.exists(OAUTH_CREDENTIAL_PREFIX + channel_id)
             and client.sismember(OAUTH_CHANNEL_INDEX, channel_id), 'delivery_channel_changed')
    sha, master_sha = result.get('delivery_manifest_sha256'), result.get('delivery_master_sha256')
    _require(all(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) for value in (sha, master_sha)))
    _require(result.get('video_key') == f'videos/{source_id}/final.mp4'
             and result.get('delivery_manifest_key') == f'videos/{source_id}/delivery/{sha}.json')
    binding = {
        'source_task_id': source_id, 'spec_sha256': digest(spec),
        'channel_id': channel_id, 'connection_id': connection_id,
        'manifest_sha256': sha, 'master_sha256': master_sha,
    }
    keys = [JOB_PREFIX + source_id, RENDER_CANCELLATION_PREFIX + source_id,
            PROFILE_PREFIX + channel_id, OAUTH_CHANNEL_PREFIX + channel_id,
            OAUTH_CREDENTIAL_PREFIX + channel_id, OAUTH_CHANNEL_INDEX]
    return job, binding, keys


def queue_delivery_family(source_id, enqueue=None):
    if not enabled():
        return {'status': 'disabled'}
    client = _client()
    try:
        _, binding, keys = _source(client, source_id)
        key = FAMILY_PREFIX + source_id
        task_id = str(uuid5(NAMESPACE_URL, 'youtube-delivery:' + source_id + ':' + binding['manifest_sha256']))
        record = {'version': 1, 'task_id': task_id, 'binding': binding, 'status': 'reserved'}
        with client.pipeline() as pipe:
            pipe.watch(key, *keys)
            if pipe.get(key) is not None:
                return {'status': 'already_claimed'}
            _require(_source(pipe, source_id)[1] == binding)
            pipe.multi()
            pipe.set(key, json.dumps(record), nx=True)
            pipe.execute()
        # A timeout after the reservation is not permission to send again.
        if enqueue is None:
            from app.production_tasks import render_delivery_family

            enqueue = render_delivery_family.apply_async
        try:
            enqueue(args=[source_id], task_id=task_id, retry=False)
        except Exception:
            _set_status(client, key, binding, 'dispatch_uncertain', expected_status='reserved')
            return {'status': 'dispatch_uncertain'}
        # The worker may already have advanced the record. Do not regress it.
        _set_status(client, key, binding, 'queued', expected_status='reserved')
        return {'status': 'queued', 'task_id': task_id}
    except Exception:
        return {'status': 'blocked'}


def _set_status(client, key, binding, status, *, expected_status=None, **fields):
    with client.pipeline() as pipe:
        pipe.watch(key)
        record = json.loads(pipe.get(key) or '{}')
        _require(record.get('binding') == binding)
        if expected_status is not None and record.get('status') != expected_status:
            return False
        record.update(status=status, **fields)
        pipe.multi()
        pipe.set(key, json.dumps(record))
        pipe.execute()
    return True


def maintain_delivery_families():
    if not enabled():
        return {'status': 'disabled'}
    outcomes = []
    for job in list_jobs(limit=100):
        result = job.get('result') or {}
        if type(result) is dict and result.get('delivery_manifest_key'):
            outcome = queue_delivery_family(job.get('task_id'))
            if outcome['status'] == 'already_claimed':
                continue
            outcomes.append(outcome)
            if len(outcomes) == 4:
                break
    return {'status': 'checked', 'families': outcomes}


def render_delivery_family(source_id, execution_task_id):
    if not enabled():
        return {'status': 'disabled'}
    client = _client()
    key = FAMILY_PREFIX + source_id
    binding = None
    owns_execution = False
    try:
        source, binding, keys = _source(client, source_id)
        with client.pipeline() as pipe:
            pipe.watch(key, *keys)
            record = json.loads(pipe.get(key) or '{}')
            _require(record.get('task_id') == execution_task_id and record.get('binding') == binding)
            if record.get('execution_claimed') is True:
                return {'status': 'already_executed'}
            _require(record.get('status') in {'reserved', 'queued', 'dispatch_uncertain'}
                     and _source(pipe, source_id)[1] == binding)
            record.update(status='rendering', execution_claimed=True)
            pipe.multi()
            pipe.set(key, json.dumps(record))
            pipe.execute()
        owns_execution = True
        with TemporaryDirectory(prefix='youtube-delivery-') as directory:
            work = Path(directory)
            manifest_path = work / 'delivery.json'
            download_file(source['result']['delivery_manifest_key'], manifest_path)
            _require(manifest_path.stat().st_size <= 128 * 1024)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            kwargs = {'source_task_id': source_id, 'expected_digest': binding['manifest_sha256'],
                      'expected_master_sha256': binding['master_sha256']}
            validate_manifest(manifest, **kwargs)
            master = work / 'master.mp4'
            download_file(manifest['master_key'], master)
            cuts = render_candidates(master, manifest, work / 'cuts', **kwargs)
            child_ids = []
            for cut in cuts:
                # Re-check owner disable/disconnect/cancel/source edits before
                # persisting each candidate. No upload-to-YouTube call exists here.
                _require(_source(client, source_id)[1] == binding)
                child_id = str(uuid5(NAMESPACE_URL, 'youtube-delivery-short:' + execution_task_id + ':' + str(cut['number'])))
                object_key = f'videos/{child_id}/candidates/{cut["sha256"]}.mp4'
                upload_file(cut['path'], object_key, 'video/mp4')
                now = datetime.now(timezone.utc)
                result = {k: v for k, v in cut.items() if k not in {'path', 'description', 'narration'}}
                result.update(task_id=child_id, video_key=object_key, status='awaiting_automated_review',
                              download_url=presigned_download_url(object_key, 86400),
                              publish_metadata={'title': cut['title'], 'description': cut['description'],
                                                'sources': source['result'].get('publish_metadata', {}).get('sources', [])})
                spec = {k: v for k, v in source['spec'].items()
                        if k not in {'production_delivery', 'production_editorial', 'production_topic_index'}}
                spec.update(format='shorts', duration_minutes=cut['duration'] / 60,
                            publish_after_render=False, production_derived_from=source_id,
                            delivery_manifest_sha256=binding['manifest_sha256'])
                job = {'task_id': child_id, 'parent_id': source_id, 'kind': 'render',
                       'state': 'SUCCESS', 'stage': 'derived_review_pending', 'progress': 100,
                       'message': 'Uzun videodan üretildi; dikey kurgu ve ses kontrolü bekleniyor.',
                       'spec': spec, 'result': result, 'error': None,
                       'created_ts': now.timestamp(), 'created_at': now.isoformat(), 'updated_at': now.isoformat()}
                with client.pipeline() as pipe:
                    pipe.watch(key, JOB_PREFIX + child_id, *keys)
                    _require(_source(pipe, source_id)[1] == binding and pipe.get(JOB_PREFIX + child_id) is None)
                    pipe.multi()
                    pipe.set(JOB_PREFIX + child_id, json.dumps(job, ensure_ascii=False), ex=JOB_TTL_SECONDS)
                    pipe.zadd(JOB_INDEX, {child_id: now.timestamp()})
                    pipe.execute()
                child_ids.append(child_id)
                _set_status(client, key, binding, 'rendering', child_task_ids=child_ids)
        _set_status(client, key, binding, 'awaiting_automated_review', child_task_ids=child_ids)
        return {'status': 'awaiting_automated_review', 'child_task_ids': child_ids}
    except Exception:
        if owns_execution and binding is not None:
            try:
                _set_status(client, key, binding, 'render_or_export_failed')
            except Exception:
                pass
        return {'status': 'blocked'}
