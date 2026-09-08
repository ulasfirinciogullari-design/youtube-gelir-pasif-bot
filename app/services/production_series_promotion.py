"""Explicit, proof-bound series rotation; never plan, render, or publish.

The v1 preparation wire format is deliberately validated here without importing
the unintegrated preparation draft. A ready batch is NOT a QA or spending grant.
Only its pending pointer is consumed; its durable daily attempt fence remains.
Old profiles, queues, state and publication proof are archived, while all job,
retry, upload, numbering and spending ledgers remain read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import redis

from app.config import settings
from app.services.channel_production import (
    ACTIVE_KEY, CHANNEL_STATE_PREFIX, OAUTH_CHANNEL_INDEX, OAUTH_CHANNEL_PREFIX,
    OAUTH_CREDENTIAL_PREFIX, PROFILE_PREFIX, PRODUCTION_PREFIX, _decode_active_claims, _prefix_digest,
)
from app.services.studio_state import (
    JOB_PREFIX, JOB_INDEX, MAX_INDEXED_JOBS, RETRY_CHILD_CLAIM_PREFIX,
    RETRY_DISPATCH_PREFIX, RETRY_CHILD_EXECUTION_PREFIX, REPAIR_CHECKPOINT_CLAIM_PREFIX,
)
from app.services.youtube_auth import AUTH_EPOCH_KEY
from app.services.youtube_automation import (
    SERIES_COUNTER_PREFIX, SERIES_ASSIGNMENT_PREFIX, automated_quality_approved, contains_synthetic_media,
)
from app.services.youtube_publish_state import UPLOAD_PREFIX


PENDING_PREFIX = 'youtube_studio:next_series:v1:pending:'
DAILY_PREFIX = 'youtube_studio:next_series:v1:daily:'
EPOCH_PREFIX = PRODUCTION_PREFIX + 'series_epoch:'
ARCHIVE_PREFIX = PRODUCTION_PREFIX + 'series_archive:'
RECEIPT_PREFIX = PRODUCTION_PREFIX + 'series_promotion:'
TOPIC_HISTORY_PREFIX = PRODUCTION_PREFIX + 'series_topic_history:'
_PUBLIC_RESUME_PREFIX = PRODUCTION_PREFIX + 'resume_public:'
_FLAGS = {'qa_approved': False, 'publish_eligible': False, 'media_budget_approved': False,
          'requires_full_research_and_critic': True, 'evidence_validation': 'source_shape_only'}
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')
_TASK = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_ACTIVE = {'PENDING', 'RECEIVED', 'STARTED', 'RETRY', 'PROGRESS'}
_PRIVATE = re.compile(r'(?i)(?:bearer\s+\S+|AIza[\w-]{20,}|\bsk-[\w-]{16,}|'
                      r'(?:api[_ -]?key|authorization|access_token|refresh_token|secret)\s*[:=])')


class SeriesPromotionError(RuntimeError):
    """Sanitized fail-closed rejection; no provider operation is performed."""


def _require(ok, code='series_promotion_invalid'):
    if not ok:
        raise SeriesPromotionError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _object(raw, *, limit=2 * 1024 * 1024):
    _require(isinstance(raw, str) and 0 < len(raw.encode('utf-8')) <= limit)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value)
            value[key] = item
        return value
    def invalid(_):
        raise SeriesPromotionError('series_promotion_invalid')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    _require(isinstance(value, dict))
    return value


def _plain(value, limit, *, empty=False):
    _require(isinstance(value, str) and len(value) <= limit and not _PRIVATE.search(value)
             and not any(ord(c) < 32 and c not in '\r\n\t' for c in value))
    value = value.strip()
    _require(empty or bool(value))
    return value


def _public_url(value):
    value = _plain(value, 1500)
    _require(not any(c.isspace() for c in value))
    parsed = urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    _require(parsed.scheme in {'http', 'https'} and host and parsed.username is None
             and parsed.password is None and not parsed.query and not parsed.fragment
             and parsed.port in (None, 80, 443))
    _require(not any(host == item or host.endswith('.' + item) for item in (
        'localhost', 'local', 'internal', 'storageapi.dev', 'up.railway.app',
        'googleusercontent.com', 'vertexaisearch.cloud.google.com')))
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        numeric = all(re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)', p) for p in host.split('.'))
        _require('.' in host and re.fullmatch(r'[a-z0-9.-]+', host) and not numeric)
    else:
        _require(address.is_global)
    return value


def _text_key(value):
    value = re.sub(r'https?://\S+', '', value)
    value = unicodedata.normalize('NFKD', value.replace('I', 'ı').replace('İ', 'i').casefold())
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c)), re.UNICODE))


def _batch(record, profile, channel, now):
    from app.services.production_next_series import _planning_channel_identity
    fields = {'version', 'status', 'attempt_id', 'day', 'channel_id', 'connection_id',
              'profile_revision', 'context_sha256', 'profile_sha256', 'channel_sha256',
              'provider', 'model', 'created_at', 'language', 'series_title', 'briefs', *_FLAGS}
    _require(set(record) == fields and type(record['version']) is int and record['version'] == 1
             and record['status'] == 'ready' and re.fullmatch(r'[0-9a-f]{32}', str(record['attempt_id']))
             and all(type(record[k]) is type(v) and record[k] == v for k, v in _FLAGS.items()),
             'series_batch_unapproved_contract_invalid')
    context = {
        'language': profile.get('default_language'),
        'channel_identity': _plain(profile.get('channel_identity') or '', 240, empty=True),
        'channel_title': _plain(channel.get('title') or '', 200, empty=True),
        'channel_bio': _plain(channel.get('description') or '', 5000, empty=True),
        'current_series_title': _plain(profile.get('series_name') or '', 100, empty=True),
        'existing_topics': [_plain(t, 240) for t in profile['production_topics']],
    }
    _require(context['language'] in {'tr', 'en', 'de', 'es', 'ar'}
             and len(_json(context).encode('utf-8')) <= 24000)
    for text in [*context['existing_topics'], *(v for v in context.values() if isinstance(v, str))]:
        for url in re.findall(r'https?://[^\s<>]+', text, flags=re.IGNORECASE):
            _public_url(url)
    created = datetime.fromisoformat(record['created_at'])
    _require(created.tzinfo is not None and created.utcoffset().total_seconds() == 0
             and created.date().isoformat() == record['day'] and 0 <= created.timestamp() <= now
             and record['provider'] in {'openai', 'gemini'}
             and re.fullmatch(r'[A-Za-z0-9._-]{1,100}', str(record['model']))
             and record['channel_id'] == profile['channel_id']
             and record['connection_id'] == channel['connection_id']
             and record['profile_revision'] == profile['profile_revision']
             and record['context_sha256'] == _digest(context)
             and record['profile_sha256'] == _digest(profile)
             and record['channel_sha256'] == _digest(_planning_channel_identity(channel)),
             'series_batch_context_changed')
    title = _plain(record['series_title'], 100)
    _require(record['language'] == context['language'] and _text_key(title)
             and _text_key(title) != _text_key(context['current_series_title'])
             and isinstance(record['briefs'], list) and 1 <= len(record['briefs']) <= 4)
    seen = [_text_key(t) for t in context['existing_topics']]
    topics = []
    for row in record['briefs']:
        _require(isinstance(row, dict) and set(row) == {'brief', 'sources'})
        brief = _plain(row['brief'], 240)
        _require(brief == row['brief'] and isinstance(row['sources'], list) and 1 <= len(row['sources']) <= 2)
        urls = []
        for source in row['sources']:
            _require(isinstance(source, dict) and set(source) == {'url', 'evidence'})
            urls.append(_public_url(source['url']))
            _plain(source['evidence'], 600)
        _require(len(set(urls)) == len(urls) and urls[0] in brief)
        for url in re.findall(r'https?://[^\s<>]+', brief):
            _require(_public_url(url) in urls)
        key, words = _text_key(brief), set(_text_key(brief).split())
        _require(len(key) >= 15 and len(words) >= 3 and key not in seen
                 and all(len(words & set(old.split())) / max(1, len(words | set(old.split()))) < .85 for old in seen))
        seen.append(key)
        topics.append(brief)
    return topics


class _Snapshot:
    def __init__(self, client):
        self.client, self.values = client, {}

    def read(self, key, kind='string'):
        value = getattr(self.client, {'string': 'get', 'hash': 'hgetall', 'zset': 'zrange'}[kind])(
            key, *([0, MAX_INDEXED_JOBS] if kind == 'zset' else []))
        if key in self.values:
            _require(self.values[key] == (kind, value), 'series_state_changed')
        else:
            self.values[key] = kind, value
        return value

    def object(self, key):
        return _object(self.read(key))

    def compare(self, pipe):
        for key, (kind, expected) in self.values.items():
            actual = getattr(pipe, {'string': 'get', 'hash': 'hgetall', 'zset': 'zrange'}[kind])(
                key, *([0, MAX_INDEXED_JOBS] if kind == 'zset' else []))
            _require(actual == expected, 'series_state_changed')


def _idle(snapshot, channel_id, route):
    raw = snapshot.read(ACTIVE_KEY)
    _require(raw is None or all(c['channel_id'] != channel_id for c in _decode_active_claims(raw)),
             'series_channel_busy')
    indexed = snapshot.read(JOB_INDEX, 'zset')
    _require(len(indexed) <= MAX_INDEXED_JOBS, 'series_registry_unbounded')
    for task_id in indexed:
        _require(isinstance(task_id, str) and _TASK.fullmatch(task_id))
        raw = snapshot.read(JOB_PREFIX + task_id)
        if raw is None:
            continue
        job = _object(raw)
        _require(job.get('task_id') == task_id and isinstance(job.get('spec'), dict))
        spec = job['spec']
        result = job.get('result')
        destination = spec.get('production_channel_id') or spec.get('youtube_channel_id') or spec.get('target_channel_id')
        delivery_family = ('production_delivery' in spec or 'production_derived_from' in spec
                           or isinstance(result, dict) and 'delivery_manifest_key' in result)
        if delivery_family:
            # A successful long master or portrait export is not a completed
            # delivery family. Even an earlier episode can still own private
            # candidates when the final ordinary Short becomes public. There
            # is no commissioned family-publication receipt yet, so neither a
            # terminal job state nor a mutable status label may authorize a
            # profile revision change that would orphan those candidates.
            _require(isinstance(destination, str) and destination, 'series_delivery_channel_unknown')
            _require(destination != channel_id and spec.get('channel_id') not in {channel_id, route},
                     'series_delivery_family_pending')
        if job.get('state') in _ACTIVE:
            _require(isinstance(destination, str) and destination, 'series_active_channel_unknown')
            _require(destination != channel_id and spec.get('channel_id') not in {channel_id, route},
                     'series_channel_busy')


def _publication(snapshot, source, profile, channel):
    """The same bound PUBLIC/assets proof required by production reconciliation.

    A prior continuation marker alone is insufficient: recheck live records and
    ciphertext/authorization epoch in the enclosing snapshot transaction.
    """
    task_id, channel_id = source['task_id'], profile['channel_id']
    _require(automated_quality_approved(source) and not source.get('retry_child_task_id'), 'series_qa_unapproved')
    result, spec = source['result'], source['spec']
    _require(result.get('task_id') == task_id and result.get('status') == 'complete'
             and result.get('video_key') == f'videos/{task_id}/final.mp4'
             and result.get('caption_key') == f'videos/{task_id}/captions.{spec["language"]}.srt')
    automation, attribution = result.get('youtube_automation'), result.get('youtube')
    _require(isinstance(automation, dict) and isinstance(attribution, dict)
             and automation.get('status') in {'queued', 'reserved', 'uploading', 'complete'})
    publish_id = automation.get('publish_task_id')
    _require(isinstance(publish_id, str) and _TASK.fullmatch(publish_id) and publish_id != task_id)
    publisher = snapshot.object(JOB_PREFIX + publish_id)
    ledger = snapshot.object(UPLOAD_PREFIX + task_id)
    delivered, pubspec, plan = publisher.get('result'), publisher.get('spec'), ledger.get('publish_plan')
    _require(all(isinstance(v, dict) for v in (delivered, pubspec, plan)))
    video_id = delivered.get('youtube_video_id')
    _require(isinstance(video_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id))
    binding = {'target_channel_id': channel_id, 'connection_id': channel['connection_id'],
               'profile_revision': profile['profile_revision']}
    disclosure = plan.get('contains_synthetic_media')
    _require(type(disclosure) is bool and (not contains_synthetic_media(source) or disclosure is True),
             'series_disclosure_unverified')
    for row in (attribution, delivered):
        replay = row is delivered and row.get('idempotent_replay') is True
        _require(all(row.get(k) == v or replay and k == 'profile_revision' and k not in row for k, v in binding.items())
                 and row.get('privacy_status') == row.get('release_status') == 'public'
                 and all(not row.get(k) for k in ('release_error_code', 'scheduled_publish_at',
                                                 'caption_error_code', 'thumbnail_error_code')),
                 'series_publication_unverified')
        for key, expected in {'caption_uploaded': True, 'contains_synthetic_media': disclosure}.items():
            _require(row.get(key) is expected or replay and key not in row, 'series_assets_unverified')
        if plan.get('require_thumbnail') is True or profile.get('require_thumbnail') is True:
            _require(row.get('thumbnail_uploaded') is True or replay and 'thumbnail_uploaded' not in row)
        if replay:
            _require(row.get('stage') == 'complete' and row.get('progress') == 100
                     and ('thumbnail_uploaded' not in row or row['thumbnail_uploaded'] == attribution.get('thumbnail_uploaded')))
    _require(publisher.get('task_id') == publish_id and publisher.get('kind') == 'publish'
             and publisher.get('state') == 'SUCCESS' and publisher.get('parent_id') == task_id
             and delivered.get('task_id') == publish_id and delivered.get('source_task_id') == task_id
             and delivered.get('status') == 'complete' and attribution.get('video_id') == video_id
             and pubspec.get('source_task_id') == task_id and pubspec.get('release_mode') == 'public'
             and pubspec.get('privacy_status') == 'private' and all(pubspec.get(k) == v for k, v in binding.items())
             and automation.get('target_channel_id') == channel_id
             and automation.get('profile_revision') == profile['profile_revision']
             and automation.get('release_mode') == 'public')
    _require(type(ledger.get('version')) is int and ledger['version'] == 2
             and ledger.get('status') == 'complete' and ledger.get('source_task_id') == task_id
             and ledger.get('publish_task_id') == publish_id and ledger.get('youtube_video_id') == video_id
             and ledger.get('target_channel_id') == channel_id and ledger.get('connection_id') == channel['connection_id']
             and ledger.get('requested_release_mode') == ledger.get('privacy_status') == ledger.get('release_status') == 'public'
             and ledger.get('side_effect_possible') is True and ledger.get('release_side_effect_possible') is True
             and isinstance(ledger.get('release_completed_at'), str) and ledger['release_completed_at']
             and not ledger.get('requested_publish_at') and not ledger.get('scheduled_publish_at') and not ledger.get('release_error_code')
             and plan.get('source_task_id') == task_id and plan.get('target_channel_id') == channel_id
             and plan.get('profile_revision') == profile['profile_revision'] and plan.get('release_mode') == 'public'
             and not plan.get('publish_at'), 'series_upload_unverified')
    series = plan.get('series')
    total = profile['series_total']
    _require(isinstance(series, dict) and series.get('id') == profile['series_id']
             and series.get('name') == profile['series_name'] and type(series.get('number')) is int
             and type(series.get('total')) is int and series['number'] == series['total'] == total,
             'series_numbering_unverified')
    scope = channel_id + ':' + profile['series_id']
    _require(snapshot.read(SERIES_COUNTER_PREFIX + scope) == str(total)
             and snapshot.read(SERIES_ASSIGNMENT_PREFIX + scope + ':' + task_id) == str(total),
             'series_numbering_unverified')
    return {'source_task_id': task_id, 'publish_task_id': publish_id, 'youtube_video_id': video_id,
            **binding, 'privacy_status': 'public', 'release_status': 'public', 'caption_uploaded': True,
            'contains_synthetic_media': disclosure, 'source_sha256': _digest(source),
            'publisher_sha256': _digest(publisher), 'upload_sha256': _digest(ledger), 'series': series}


def _last_public(snapshot, profile, channel, state, now):
    original_id = state.get('last_task_id')
    _require(isinstance(original_id, str) and _TASK.fullmatch(original_id))
    original = snapshot.object(JOB_PREFIX + original_id)
    expected_brief = profile['production_topics'][-1].strip()
    identity = str(profile.get('channel_identity') or '').strip()[:240]
    if identity:
        expected_brief += '\n\nChannel editorial direction: ' + identity
    spec = original.get('spec')
    _require(original.get('task_id') == original_id and original.get('kind') == 'render'
             and not original.get('parent_id') and isinstance(spec, dict)
             and spec.get('topic') == expected_brief and spec.get('mode') == 'production'
             and spec.get('format') == 'shorts' and spec.get('duration_minutes') == .5
             and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
             and spec.get('production_channel_id') == profile['channel_id']
             and spec.get('production_connection_id') == channel['connection_id']
             and spec.get('production_profile_revision') == profile['profile_revision']
             and type(spec.get('production_topic_index')) is int
             and spec['production_topic_index'] == len(profile['production_topics']) - 1
             and spec.get('language') == profile['default_language']
             and spec.get('channel_id') == str(profile.get('route_label') or profile['channel_id']).strip(),
             'series_final_episode_changed')
    source, lineage = original, [original_id]
    audit = None
    if original.get('state') == 'SUCCESS':
        continued_at = float(state.get('last_public_continued_at', 'nan'))
        _require(state.get('last_result') == 'SUCCESS' and state.get('last_public_task_id') == original_id
                 and math.isfinite(continued_at) and 0 <= continued_at <= now,
                 'series_public_receipt_missing')
    else:
        # Reuse the existing resume receipt contract; it does not replace the
        # reciprocal lineage, original frozen spec or actual PUBLIC proof.
        from app.services.production_recovery import _audit_result
        raw = snapshot.read(_PUBLIC_RESUME_PREFIX + profile['channel_id'] + ':' + original_id)
        audit = _object(raw)
        target = audit.get('recovered_task_id')
        _require(isinstance(target, str) and _TASK.fullmatch(target) and target != original_id
                 and type(audit.get('continue_immediately')) is bool)
        _audit_result(raw, 'already_resumed', profile['channel_id'], original_id, target,
                      profile['profile_revision'], 'public', False, audit.get('continue_immediately'))
        _require(state.get('last_result') == 'FAILURE' and audit.get('cursor') == len(profile['production_topics'])
                 and audit.get('connection_id') == channel['connection_id']
                 and audit.get('resumed_at') <= now and float(state['next_due']) == audit.get('next_due'),
                 'series_public_receipt_invalid')
        frozen = {k: v for k, v in spec.items() if k not in {'workflow', 'repair_source_task_id'}}
        for _ in range(16):
            parent_id, parent = source['task_id'], source
            child_id = parent.get('retry_child_task_id')
            _require(parent.get('state') == 'FAILURE' and parent.get('retry_claimed') is True
                     and isinstance(child_id, str) and _TASK.fullmatch(child_id) and child_id not in lineage)
            dispatch = snapshot.read(RETRY_DISPATCH_PREFIX + parent_id, 'hash')
            claim = snapshot.read(RETRY_CHILD_CLAIM_PREFIX + child_id, 'hash')
            token = dispatch.get('token')
            _require(token and dispatch.get('child_task_id') == child_id
                     and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                     and parent.get('retry_dispatch_state') == dispatch['state']
                     and claim.get('source_task_id') == parent_id and claim.get('token') == token
                     and snapshot.read(RETRY_CHILD_EXECUTION_PREFIX + child_id) == token
                     and dispatch.get('mode') in {'full', 'repair'}, 'series_retry_claim_invalid')
            if dispatch['mode'] == 'repair':
                _require(parent.get('repair_claimed') is True
                         and snapshot.read(REPAIR_CHECKPOINT_CLAIM_PREFIX + parent_id) == token)
            prior_upload = snapshot.read(UPLOAD_PREFIX + parent_id)
            if prior_upload is not None:
                prior = _object(prior_upload)
                _require(prior.get('source_task_id') == parent_id and prior.get('status') == 'failed_preflight'
                         and prior.get('side_effect_possible') is False and not prior.get('youtube_video_id')
                         and not prior.get('release_side_effect_possible'))
            source = snapshot.object(JOB_PREFIX + child_id)
            _require(source.get('task_id') == child_id and source.get('kind') == 'render'
                     and source.get('parent_id') == parent_id and isinstance(source.get('spec'), dict)
                     and {k: v for k, v in source['spec'].items() if k not in {'workflow', 'repair_source_task_id'}} == frozen)
            lineage.append(child_id)
            if child_id == target:
                break
        _require(source['task_id'] == target, 'series_retry_lineage_unbounded')
    proof = _publication(snapshot, source, profile, channel)
    if audit is not None:
        _require(all(audit.get(k) == proof.get(k) for k in ('publish_task_id', 'youtube_video_id',
                                                          'contains_synthetic_media', 'caption_uploaded')))
        proof['resume_receipt_sha256'] = _digest(audit)
    return {**proof, 'original_task_id': original_id, 'lineage': lineage}


def _receipt(raw, channel_id, revision, attempt_id):
    value = _object(raw)
    fields = {'version', 'status', 'channel_id', 'attempt_id', 'old_profile_revision', 'new_profile_revision',
              'old_series_id', 'new_series_id', 'epoch', 'archive_key', 'archive_sha256', 'pending_sha256',
              'new_profile_sha256', 'promoted_at', 'topic_count', *_FLAGS}
    _require(set(value) == fields and type(value.get('version')) is int and value['version'] == 1
             and value.get('channel_id') == channel_id and value.get('old_profile_revision') == revision
             and value.get('attempt_id') == attempt_id and value.get('status') == 'promoted'
             and type(value.get('epoch')) is int and value['epoch'] > 0
             and isinstance(value.get('old_profile_revision'), str) and _ID.fullmatch(value['old_profile_revision'])
             and isinstance(value.get('new_profile_revision'), str) and _TASK.fullmatch(value['new_profile_revision'])
             and isinstance(value.get('old_series_id'), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value['old_series_id'])
             and isinstance(value.get('new_series_id'), str) and re.fullmatch(r'[0-9a-f]{32}', value['new_series_id'])
             and value['new_series_id'] != value['old_series_id']
             and isinstance(value.get('attempt_id'), str) and re.fullmatch(r'[0-9a-f]{32}', value['attempt_id'])
             and value.get('archive_key') == ARCHIVE_PREFIX + channel_id + ':' + str(value['epoch'] - 1) + ':' + attempt_id
             and all(isinstance(value.get(k), str) and re.fullmatch(r'[0-9a-f]{64}', value[k])
                     for k in ('archive_sha256', 'pending_sha256', 'new_profile_sha256'))
             and type(value.get('promoted_at')) in (int, float) and math.isfinite(value['promoted_at'])
             and value['promoted_at'] >= 0 and type(value.get('topic_count')) is int and 1 <= value['topic_count'] <= 4
             and all(type(value.get(k)) is type(v) and value[k] == v for k, v in _FLAGS.items()),
             'series_receipt_conflict')
    return {**value, 'status': 'already_promoted'}


def promote_ready_series(channel_id, expected_profile_revision, expected_attempt_id, *, now=None):
    """Promote once after exhaustion and proven PUBLIC delivery, without enqueue.

    Duplicate calls describe the previous transition, not current eligibility.
    A lost transaction reply never reserves again. Another channel may remain
    active; its claim, jobs and capacity are not mutated by this transaction.
    """
    _require(isinstance(channel_id, str) and _ID.fullmatch(channel_id)
             and isinstance(expected_profile_revision, str) and _ID.fullmatch(expected_profile_revision)
             and isinstance(expected_attempt_id, str) and re.fullmatch(r'[0-9a-f]{32}', expected_attempt_id))
    now = time.time() if now is None else now
    _require(type(now) in (int, float) and math.isfinite(now) and now >= 0)
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    receipt_key = RECEIPT_PREFIX + channel_id + ':' + expected_attempt_id
    try:
        prior = client.get(receipt_key)
        if prior is not None:
            return _receipt(prior, channel_id, expected_profile_revision, expected_attempt_id)
        snapshot = _Snapshot(client)
        _require(snapshot.read(receipt_key) is None, 'series_state_changed')
        profile_key, channel_key = PROFILE_PREFIX + channel_id, OAUTH_CHANNEL_PREFIX + channel_id
        state_key, pending_key = CHANNEL_STATE_PREFIX + channel_id, PENDING_PREFIX + channel_id
        profile, channel = snapshot.object(profile_key), snapshot.object(channel_key)
        state = snapshot.read(state_key, 'hash')
        credential = snapshot.read(OAUTH_CREDENTIAL_PREFIX + channel_id)
        authorization_epoch = snapshot.read(AUTH_EPOCH_KEY)
        _require(profile.get('channel_id') == channel_id and profile.get('profile_revision') == expected_profile_revision
                 and profile.get('production_enabled') is True and profile.get('auto_publish') is True
                 and profile.get('release_mode') == 'public' and channel.get('id') == channel_id
                 and isinstance(channel.get('connection_id'), str) and _ID.fullmatch(channel['connection_id'])
                 and channel.get('requires_reconnect') is not True and isinstance(credential, str) and credential
                 and client.sismember(OAUTH_CHANNEL_INDEX, channel_id)
                 and (authorization_epoch is None or re.fullmatch(r'[0-9]+', authorization_epoch)),
                 'series_authorization_changed')
        topics, total, series_id = profile.get('production_topics'), profile.get('series_total'), profile.get('series_id')
        _require(isinstance(topics, list) and 1 <= len(topics) <= 60
                 and all(isinstance(t, str) and t.strip() and len(t) <= 240 for t in topics)
                 and type(total) is int and total == len(topics)
                 and isinstance(series_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', series_id)
                 and isinstance(profile.get('series_name'), str)
                 and type(profile.get('production_interval_hours')) is int
                 and 6 <= profile['production_interval_hours'] <= 168, 'series_profile_invalid')
        _require(state.get('cursor') == str(len(topics))
                 and state.get('consumed_prefix') == _prefix_digest([t.strip() for t in topics])
                 and state.get('profile_revision') == expected_profile_revision
                 and state.get('connection_id') == channel['connection_id']
                 and not state.get('active_task_id') and not state.get('paused_reason')
                 and state.get('dispatch_status') == 'finished', 'series_not_exhausted_or_idle')
        next_due = float(state.get('next_due', 'nan'))
        _require(math.isfinite(next_due) and next_due >= 0, 'series_schedule_invalid')
        _idle(snapshot, channel_id, str(profile.get('route_label') or channel_id).strip())
        proof = _last_public(snapshot, profile, channel, state, now)
        pending_raw = snapshot.read(pending_key)
        pending = _object(pending_raw, limit=32768)
        _require(pending.get('attempt_id') == expected_attempt_id, 'series_batch_changed')
        new_topics = _batch(pending, profile, channel, now)
        daily_key = DAILY_PREFIX + channel_id + ':' + pending['day']
        _require(snapshot.read(daily_key) == pending_raw, 'series_attempt_receipt_missing')
        epoch_key, history_key = EPOCH_PREFIX + channel_id, TOPIC_HISTORY_PREFIX + channel_id
        old_epoch_raw = snapshot.read(epoch_key)
        old_epoch = _object(old_epoch_raw) if old_epoch_raw is not None else None
        history_kind = client.type(history_key)
        _require(history_kind in {'none', 'set'}, 'series_history_invalid')
        history_count = client.scard(history_key)
        old_topic_hashes = {_digest(_text_key(t)) for t in topics}
        if old_epoch is None:
            _require('series_epoch' not in profile and 'series_epoch' not in state
                     and not state.get('last_series_promotion') and history_kind == 'none', 'series_epoch_missing')
            epoch = 1
        else:
            _require(type(old_epoch.get('version')) is int and old_epoch['version'] == 1
                     and old_epoch.get('channel_id') == channel_id
                     and type(old_epoch.get('epoch')) is int and old_epoch['epoch'] > 0
                     and type(profile.get('series_epoch')) is int and profile['series_epoch'] == old_epoch['epoch']
                     and state.get('series_epoch') == str(old_epoch['epoch'])
                     and old_epoch.get('series_id') == series_id
                     and old_epoch.get('profile_revision') == expected_profile_revision
                     and history_kind == 'set' and type(old_epoch.get('history_count')) is int
                     and old_epoch['history_count'] == history_count
                     and all(client.sismember(history_key, h) for h in old_topic_hashes), 'series_epoch_changed')
            previous_key = old_epoch.get('receipt_key')
            _require(isinstance(previous_key, str) and previous_key.startswith(RECEIPT_PREFIX + channel_id + ':')
                     and state.get('last_series_promotion') == previous_key)
            previous = snapshot.object(previous_key)
            _receipt(_json(previous), channel_id, previous.get('old_profile_revision'), previous_key.rsplit(':', 1)[-1])
            _require(previous.get('new_profile_revision') == expected_profile_revision
                     and previous.get('new_series_id') == series_id and previous.get('epoch') == old_epoch['epoch']
                     and previous.get('new_profile_sha256') == _digest(profile))
            prior_archive_key = previous.get('archive_key')
            _require(isinstance(prior_archive_key, str) and prior_archive_key.startswith(ARCHIVE_PREFIX + channel_id + ':'))
            prior_archive = snapshot.object(prior_archive_key)
            archived_profile, archived_batch = prior_archive.get('profile'), prior_archive.get('pending_batch')
            _require(_digest(prior_archive) == previous.get('archive_sha256')
                     and prior_archive.get('channel_id') == channel_id
                     and type(prior_archive.get('epoch')) is int and prior_archive['epoch'] == old_epoch['epoch'] - 1
                     and isinstance(archived_profile, dict) and isinstance(archived_batch, dict)
                     and archived_profile.get('profile_revision') == previous['old_profile_revision']
                     and archived_profile.get('series_id') == previous['old_series_id']
                     and archived_batch.get('attempt_id') == previous['attempt_id']
                     and _digest(archived_batch) == previous['pending_sha256'],
                     'series_archive_changed')
            epoch = old_epoch['epoch'] + 1
        hashes = [_digest(_text_key(t)) for t in new_topics]
        _require(all(not client.sismember(history_key, value) for value in hashes), 'series_topic_already_used')
        seed = f'youtube-series:{channel_id}:{expected_attempt_id}:{_digest(pending)}'
        new_id, revision = uuid5(NAMESPACE_URL, seed).hex, str(uuid5(NAMESPACE_URL, seed + ':profile'))
        _require(new_id != series_id and snapshot.read(SERIES_COUNTER_PREFIX + channel_id + ':' + new_id) is None)
        new_profile = {**profile, 'series_id': new_id, 'series_name': pending['series_title'],
                       'series_total': len(new_topics), 'production_topics': new_topics,
                       'profile_revision': revision, 'series_epoch': epoch,
                       'updated_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}
        new_state = {'cursor': '0', 'consumed_prefix': _prefix_digest([]), 'next_due': str(now),
                     'profile_revision': revision, 'connection_id': channel['connection_id'],
                     'dispatch_status': 'series_promoted', 'series_epoch': str(epoch),
                     'last_series_promotion': receipt_key}
        archive_key = ARCHIVE_PREFIX + channel_id + ':' + str(epoch - 1) + ':' + expected_attempt_id
        _require(snapshot.read(archive_key) is None, 'series_archive_conflict')
        archive = {'version': 1, 'channel_id': channel_id, 'epoch': epoch - 1, 'archived_at': now,
                   'profile': profile, 'state': state, 'pending_batch': pending, 'public_proof': proof,
                   'authorization_epoch_sha256': _digest(authorization_epoch),
                   'credential_sha256': _digest(credential), 'previous_epoch': old_epoch}
        receipt = {'version': 1, 'status': 'promoted', 'channel_id': channel_id,
                   'attempt_id': expected_attempt_id, 'old_profile_revision': expected_profile_revision,
                   'new_profile_revision': revision, 'old_series_id': series_id, 'new_series_id': new_id,
                   'epoch': epoch, 'archive_key': archive_key, 'archive_sha256': _digest(archive),
                   'pending_sha256': _digest(pending), 'new_profile_sha256': _digest(new_profile),
                   'promoted_at': now, 'topic_count': len(new_topics), **_FLAGS}
        epoch_record = {'version': 1, 'channel_id': channel_id, 'epoch': epoch, 'series_id': new_id,
                        'profile_revision': revision, 'receipt_key': receipt_key,
                        'history_count': history_count + len(hashes) + (len(old_topic_hashes) if old_epoch is None else 0)}
        # This is the sole write transaction. No provider, queue, claim, old
        # series numbering or daily paid-attempt ledger is modified here.
        with client.pipeline() as pipe:
            pipe.watch(*snapshot.values, OAUTH_CHANNEL_INDEX, history_key)
            snapshot.compare(pipe)
            _require(pipe.sismember(OAUTH_CHANNEL_INDEX, channel_id)
                     and pipe.type(history_key) == history_kind and pipe.scard(history_key) == history_count
                     and (old_epoch is None or all(pipe.sismember(history_key, h) for h in old_topic_hashes))
                     and all(not pipe.sismember(history_key, value) for value in hashes), 'series_state_changed')
            pipe.multi()
            pipe.set(archive_key, _json(archive))
            pipe.set(receipt_key, _json(receipt))
            pipe.set(epoch_key, _json(epoch_record))
            pipe.set(profile_key, _json(new_profile))
            pipe.delete(state_key)
            pipe.hset(state_key, mapping=new_state)
            pipe.sadd(history_key, *hashes, *[_digest(_text_key(t)) for t in topics])
            pipe.delete(pending_key)
            pipe.execute()
        return receipt
    except Exception as exc:
        # A committed-but-unacknowledged transaction is completed once, never
        # replayed. This result grants no dispatch or public release authority.
        try:
            prior = client.get(receipt_key)
            if prior is not None:
                return _receipt(prior, channel_id, expected_profile_revision, expected_attempt_id)
        except Exception:
            pass
        if isinstance(exc, SeriesPromotionError):
            raise
        raise SeriesPromotionError('series_promotion_unavailable') from None
