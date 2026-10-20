"""Cloud-only fan-out with one bounded recovery of a known CPU export failure.

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
from app.services.production_delivery import CONTRACT, DeliveryPlanError, digest, delivery_requested, file_sha256
from app.services.production_derivatives import iter_candidates, validate_manifest
from app.services.studio_state import (
    JOB_INDEX, JOB_PREFIX, JOB_TTL_SECONDS, list_jobs, RENDER_CANCELLATION_PREFIX,
)
from app.services.channel_production import (
    PROFILE_PREFIX, OAUTH_CHANNEL_PREFIX, OAUTH_CREDENTIAL_PREFIX, OAUTH_CHANNEL_INDEX,
)
from app.services.storage import download_file, upload_file, presigned_download_url


FAMILY_PREFIX = 'youtube_studio:delivery:v1:'
TASK_PATTERN = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
MAX_EXECUTION_ATTEMPTS = 2


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


def _execution_id(binding, attempt=1):
    name = 'youtube-delivery:' + binding['source_task_id'] + ':' + binding['manifest_sha256']
    if attempt != 1:
        name += ':recovery:' + str(attempt)
    return str(uuid5(NAMESPACE_URL, name))


def _child_id(binding, number):
    # Child identity survives a new CPU execution, including an uncertain upload.
    return str(uuid5(NAMESPACE_URL, 'youtube-delivery-short:' + _execution_id(binding) + ':' + str(number)))


def _child_keys(record):
    keys = []
    for child in record.get('children', []):
        child_id = child['task_id']
        keys.extend([JOB_PREFIX + child_id, RENDER_CANCELLATION_PREFIX + child_id])
    return keys


def _retained_children(client, record, binding):
    """Redis snapshots are immutable recovery evidence, never a QA approval."""
    children = record.get('children')
    _require(type(children) is list and len(children) <= 3, 'delivery_children_invalid')
    jobs = []
    for number, child in enumerate(children, 1):
        _require(type(child) is dict and set(child) == {'number', 'task_id', 'job_sha256', 'video_sha256'}
                 and type(child['number']) is int and child['number'] == number
                 and child['task_id'] == _child_id(binding, number), 'delivery_children_invalid')
        _require(not client.exists(RENDER_CANCELLATION_PREFIX + child['task_id']), 'delivery_child_cancelled')
        job = json.loads(client.get(JOB_PREFIX + child['task_id']) or '{}')
        _require(digest(job) == child['job_sha256'], 'delivery_child_changed')
        result, spec = job.get('result') or {}, job.get('spec') or {}
        _require(job.get('task_id') == child['task_id'] and job.get('parent_id') == binding['source_task_id']
                 and job.get('state') == 'SUCCESS' and job.get('stage') == 'derived_review_pending'
                 and spec.get('production_channel_id') == binding['channel_id']
                 and spec.get('production_connection_id') == binding['connection_id']
                 and spec.get('delivery_manifest_sha256') == binding['manifest_sha256']
                 and spec.get('publish_after_render') is False
                 and result.get('sha256') == child['video_sha256']
                 and re.fullmatch(r'[0-9a-f]{64}', child['video_sha256'])
                 and result.get('video_key') == f'videos/{child["task_id"]}/candidates/{child["video_sha256"]}.mp4'
                 and result.get('status') == 'awaiting_automated_review'
                 and result.get('quality_disposition') == 'derived_portrait_review_required'
                 and result.get('manual_qa_required') is True and result.get('publish_eligible') is False,
                 'delivery_child_changed')
        jobs.append(job)
    _require(record.get('child_task_ids', []) == [child['task_id'] for child in children],
             'delivery_children_invalid')
    return jobs


def queue_delivery_family(source_id, enqueue=None):
    if not enabled():
        return {'status': 'disabled'}
    client = _client()
    try:
        _, binding, keys = _source(client, source_id)
        key = FAMILY_PREFIX + source_id
        task_id = _execution_id(binding)
        record = {'version': 2, 'task_id': task_id, 'binding': binding, 'status': 'reserved',
                  'attempt': 1, 'children': [], 'child_task_ids': []}
        with client.pipeline() as pipe:
            pipe.watch(key, *keys)
            raw_previous = pipe.get(key)
            previous = json.loads(raw_previous or '{}')
            if raw_previous is not None:
                _require(type(previous) is dict and previous, 'delivery_claim_invalid')
                # Only a worker that has exited its caught failure path can
                # authorize recovery. Timeouts and hard crashes stay held.
                if (previous.get('version') != 2 or previous.get('status') != 'render_or_export_failed'
                        or previous.get('execution_claimed') is not True
                        or type(previous.get('attempt')) is not int
                        or not 1 <= previous['attempt'] < MAX_EXECUTION_ATTEMPTS):
                    return {'status': 'already_claimed'}
                _require(previous.get('binding') == binding
                         and previous.get('task_id') == _execution_id(binding, previous['attempt']))
                pipe.watch(key, *_child_keys(previous))
                _retained_children(pipe, previous, binding)
                record = {**previous, 'attempt': previous['attempt'] + 1,
                          'status': 'reserved', 'execution_claimed': False}
                task_id = _execution_id(binding, record['attempt'])
                record['task_id'] = task_id
            _require(_source(pipe, source_id)[1] == binding)
            pipe.multi()
            pipe.set(key, json.dumps(record))
            pipe.execute()
        # A timeout after the reservation is not permission to send again.
        if enqueue is None:
            from app.production_tasks import render_delivery_family

            enqueue = render_delivery_family.apply_async
        try:
            enqueue(args=[source_id], task_id=task_id, retry=False)
        except Exception:
            _set_status(client, key, binding, 'dispatch_uncertain', expected_status='reserved',
                        execution_task_id=task_id)
            return {'status': 'dispatch_uncertain'}
        # The worker may already have advanced the record. Do not regress it.
        _set_status(client, key, binding, 'queued', expected_status='reserved', execution_task_id=task_id)
        return {'status': 'queued', 'task_id': task_id}
    except Exception:
        return {'status': 'blocked'}


def _set_status(client, key, binding, status, *, expected_status=None, execution_task_id=None, **fields):
    with client.pipeline() as pipe:
        pipe.watch(key)
        record = json.loads(pipe.get(key) or '{}')
        _require(record.get('binding') == binding)
        if execution_task_id is not None and record.get('task_id') != execution_task_id:
            return False
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
            # A never-started v1 dispatch may adopt the stronger write protocol;
            # its already-claimed failures cannot be retroactively recovered.
            if record.get('version') == 1:
                _require(not record.get('child_task_ids'))
                record.update(version=2, attempt=1, children=[], child_task_ids=[])
            _require(record.get('version') == 2 and type(record.get('attempt')) is int
                     and 1 <= record['attempt'] <= MAX_EXECUTION_ATTEMPTS
                     and execution_task_id == _execution_id(binding, record['attempt']))
            pipe.watch(key, *_child_keys(record))
            _retained_children(pipe, record, binding)
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
            retained = _retained_children(client, record, binding)
            for child in retained:
                _require(_source(client, source_id)[1] == binding)
                path = work / (child['task_id'] + '.mp4')
                download_file(child['result']['video_key'], path)
                _require(file_sha256(path) == child['result']['sha256'], 'delivery_child_asset_changed')
                path.unlink()
            missing = list(range(len(retained) + 1, 4))
            cuts = []
            if missing:
                _require(_source(client, source_id)[1] == binding
                         and json.loads(client.get(key) or '{}') == record)
                _retained_children(client, record, binding)
                master = work / 'master.mp4'
                download_file(manifest['master_key'], master)
                cuts = iter_candidates(master, manifest, work / 'cuts', only_numbers=missing, **kwargs)
            child_ids = list(record['child_task_ids'])
            for cut in cuts:
                # Re-check owner disable/disconnect/cancel/source edits before
                # persisting each candidate. No upload-to-YouTube call exists here.
                _require(_source(client, source_id)[1] == binding
                         and json.loads(client.get(key) or '{}') == record)
                _retained_children(client, record, binding)
                _require(type(cut.get('number')) is int and cut['number'] == len(child_ids) + 1)
                child_id = _child_id(binding, cut['number'])
                _require(not client.exists(RENDER_CANCELLATION_PREFIX + child_id), 'delivery_child_cancelled')
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
                    pipe.watch(key, JOB_PREFIX + child_id, RENDER_CANCELLATION_PREFIX + child_id,
                               *keys, *_child_keys(record))
                    latest = json.loads(pipe.get(key) or '{}')
                    _require(latest == record and latest.get('task_id') == execution_task_id
                             and latest.get('status') == 'rendering'
                             and _source(pipe, source_id)[1] == binding
                             and pipe.get(JOB_PREFIX + child_id) is None
                             and not pipe.exists(RENDER_CANCELLATION_PREFIX + child_id))
                    _retained_children(pipe, latest, binding)
                    latest['children'].append({'number': cut['number'], 'task_id': child_id,
                                               'job_sha256': digest(job), 'video_sha256': cut['sha256']})
                    latest['child_task_ids'].append(child_id)
                    pipe.multi()
                    pipe.set(JOB_PREFIX + child_id, json.dumps(job, ensure_ascii=False), ex=JOB_TTL_SECONDS)
                    pipe.zadd(JOB_INDEX, {child_id: now.timestamp()})
                    # The child and its recovery receipt have one commit point.
                    pipe.set(key, json.dumps(latest))
                    pipe.execute()
                record = latest
                child_ids.append(child_id)
        with client.pipeline() as pipe:
            pipe.watch(key, *keys, *_child_keys(record))
            _require(len(child_ids) == 3 and _source(pipe, source_id)[1] == binding
                     and json.loads(pipe.get(key) or '{}') == record)
            _retained_children(pipe, record, binding)
            record.update(status='awaiting_automated_review')
            pipe.multi()
            pipe.set(key, json.dumps(record))
            pipe.execute()
        return {'status': 'awaiting_automated_review', 'child_task_ids': child_ids}
    except Exception:
        if owns_execution and binding is not None:
            try:
                _set_status(client, key, binding, 'render_or_export_failed',
                            expected_status='rendering', execution_task_id=execution_task_id)
            except Exception:
                pass
        return {'status': 'blocked'}
