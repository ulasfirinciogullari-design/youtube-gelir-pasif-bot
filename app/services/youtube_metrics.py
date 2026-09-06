"""Owner-only YouTube statistics; cached GETs never contact Google or change jobs.

Refreshes write only this module's cache and short debounce keys. In particular,
an expired credential is reported, never migrated, revoked or deleted here.
Owner-observed absence retains its cache; expiry alone must not resurrect an
old public label. A newer successful available observation restores normal TTL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import re
import time

import redis

from app.config import settings


CACHE_PREFIX = 'youtube_studio:metrics:v1:'
LOCK_PREFIX = 'youtube_studio:metrics:refresh:v1:'
REFRESH_SECONDS = 300
DEBOUNCE_SECONDS = 60
CACHE_TTL_SECONDS = 7 * 86400
MAX_CHANNELS = 10
MAX_VIDEOS = 50
_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_TASK = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
_VIDEO = re.compile(r'^[A-Za-z0-9_-]{11}$')
_REASONS = {'quota', 'permission', 'connection_changed', 'api_unavailable', 'invalid_response',
            'cache_unavailable', 'not_refreshed', 'video_unavailable'}
_CACHE_COMMIT = '''
if redis.call('GET', KEYS[2]) ~= ARGV[1]
 or redis.call('GET', KEYS[3]) ~= ARGV[2]
 or (redis.call('GET', KEYS[5]) or '0') ~= ARGV[3]
 or redis.call('SISMEMBER', KEYS[4], ARGV[4]) ~= 1 then return 0 end
local incoming = cjson.decode(ARGV[5])
local started = incoming['last_attempt_at']
if type(started) ~= 'number' then return 0 end
local oldraw = redis.call('GET', KEYS[1])
if oldraw then
  local ok, old = pcall(cjson.decode, oldraw)
  if ok and type(old) == 'table' and type(old['last_attempt_at']) == 'number'
     and old['last_attempt_at'] >= started then return 0 end
end
local absent = false
if type(incoming['videos']) == 'table' then
  for _, video in pairs(incoming['videos']) do
    if type(video) == 'table' and video['availability'] == 'unavailable'
       and video['error'] == 'video_unavailable'
       and type(video['availability_checked_at']) == 'number'
       and video['availability_checked_at'] > 0
       and video['availability_checked_at'] <= started + 5 then absent = true end
  end
end
-- Do not resurrect a historical public label merely because its cache expired.
-- A later successful available observation restores the ordinary cache TTL.
if absent then redis.call('SET', KEYS[1], ARGV[5])
else redis.call('SETEX', KEYS[1], ARGV[6], ARGV[5]) end
return 1
'''


class YouTubeMetricsError(RuntimeError):
    """Only fixed reason codes leave this module."""


def _redis():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _auth():
    from app.services import youtube_auth
    return youtube_auth


def _object(raw, limit=512 * 1024):
    if not isinstance(raw, str) or not 0 < len(raw.encode('utf-8')) <= limit:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _text(value, limit=200):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if ord(c) >= 32).strip()[:limit]


def _count(value):
    # Missing/hidden statistics are unknown, never invented zeroes.
    if isinstance(value, str) and re.fullmatch(r'\d{1,20}', value):
        return int(value) if int(value) <= 2**64 - 1 else None
    if type(value) is int and 0 <= value <= 2**64 - 1:
        return value
    return None


def _timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _iso(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if _timestamp(value) else None
    except (ValueError, OverflowError, OSError):
        return None


def _published(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _duration(value):
    return value if (isinstance(value, str) and len(value) <= 50
                     and re.fullmatch(r'P(?:\d+D)?T(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?', value)) else None


def _contexts(client):
    auth = _auth()
    ids = client.smembers(auth.CHANNEL_INDEX_KEY)
    if not isinstance(ids, (set, list, tuple)) or len(ids) > MAX_CHANNELS:
        raise YouTubeMetricsError('connection_changed')
    result = []
    for channel_id in sorted(ids):
        if not isinstance(channel_id, str) or not _ID.fullmatch(channel_id):
            continue
        raw = client.get(auth.CHANNEL_PREFIX + channel_id)
        channel = _object(raw, 32768)
        cipher = client.get(auth.CREDENTIAL_PREFIX + channel_id)
        if (not channel or channel.get('id') != channel_id
                or not isinstance(channel.get('connection_id'), str) or not _ID.fullmatch(channel['connection_id'])
                or not isinstance(cipher, str) or not 1 <= len(cipher) <= 32768):
            continue
        result.append({'channel_id': channel_id, 'connection_id': channel['connection_id'],
                       'title': _text(channel.get('title')) or channel_id,
                       'blocked': channel.get('requires_reconnect') is True,
                       'raw': raw, 'cipher': cipher, 'epoch': client.get(auth.AUTH_EPOCH_KEY) or '0'})
    return result


def _proofs(jobs, contexts):
    """Only exact successful upload attributions become API video queries."""
    bindings = {(c['channel_id'], c['connection_id']) for c in contexts}
    proofs = {}
    if not isinstance(jobs, (list, tuple)):
        return proofs
    for job in jobs[:5000]:
        if not isinstance(job, dict) or job.get('state') != 'SUCCESS':
            continue
        task_id, result = job.get('task_id'), job.get('result')
        if not isinstance(task_id, str) or not _TASK.fullmatch(task_id) or not isinstance(result, dict):
            continue
        if result.get('task_id', task_id) != task_id:
            continue
        if job.get('kind') == 'render':
            source_id, attribution = task_id, result.get('youtube')
            video_field = 'video_id'
        elif job.get('kind') == 'publish' and result.get('status') == 'complete':
            source_id, attribution = result.get('source_task_id'), result
            video_field = 'youtube_video_id'
            if job.get('parent_id') != source_id:
                continue
        else:
            continue
        if not isinstance(source_id, str) or not _TASK.fullmatch(source_id) or not isinstance(attribution, dict):
            continue
        video_id = attribution.get(video_field)
        channel_id, connection_id = attribution.get('target_channel_id'), attribution.get('connection_id')
        if (not isinstance(video_id, str) or not _VIDEO.fullmatch(video_id)
                or (channel_id, connection_id) not in bindings or not _published(attribution.get('uploaded_at'))):
            continue
        proof = {'video_id': video_id, 'channel_id': channel_id, 'connection_id': connection_id}
        if source_id in proofs and proofs[source_id] != proof:
            proofs[source_id] = None  # Conflicting histories cannot select an arbitrary video.
        elif source_id not in proofs:
            proofs[source_id] = proof
    # At most 50 exact IDs per channel, in the caller's most-recent-first order.
    selected, seen = {}, {}
    for source_id, proof in proofs.items():
        if not proof:
            continue
        ids = seen.setdefault(proof['channel_id'], set())
        if proof['video_id'] not in ids and len(ids) >= MAX_VIDEOS:
            continue
        ids.add(proof['video_id'])
        selected[source_id] = proof
    return selected


def _cache_key(context):
    return CACHE_PREFIX + context['channel_id'] + ':' + context['connection_id']


def _cache(client, context):
    cached = _object(client.get(_cache_key(context)))
    if (not cached or type(cached.get('version')) is not int or cached['version'] != 1
            or cached.get('channel_id') != context['channel_id'] or cached.get('connection_id') != context['connection_id']):
        return {}
    return cached


def _state(fetched_at, error, now):
    if not _timestamp(fetched_at) or fetched_at > now + 5:
        return 'unavailable'
    return 'stale' if error or now - fetched_at >= REFRESH_SECONDS else 'fresh'


def get_dashboard_metrics(jobs):
    """Read existing metrics only; no Google request, refresh, migration or write."""
    output = {'channels': [], 'videos': {}, 'updated_at': None, 'refresh_after_seconds': REFRESH_SECONDS}
    try:
        client = _redis()
        contexts = _contexts(client)
        proofs = _proofs(jobs, contexts)
        fetched = []
        now = time.time()
        for context in contexts:
            cached = _cache(client, context)
            error = 'permission' if context['blocked'] else cached.get('last_error')
            error = error if error in _REASONS else None
            channel = cached.get('channel') if isinstance(cached.get('channel'), dict) else {}
            at = _timestamp(channel.get('fetched_at'))
            status = _state(at, error, now)
            hidden = channel.get('subscriber_count_hidden')
            row = {'channel_id': context['channel_id'], 'title': _text(channel.get('title')) or context['title'],
                   'subscriber_count': None if hidden is True else _count(channel.get('subscriber_count')),
                   'subscriber_count_hidden': hidden if type(hidden) is bool else None,
                   'video_count': _count(channel.get('video_count')), 'view_count': _count(channel.get('view_count')),
                   'fetched_at': _iso(at), 'status': status, 'reason': error or ('not_refreshed' if not at else None)}
            if at:
                fetched.append(at)
            output['channels'].append(row)
            stored = cached.get('videos') if isinstance(cached.get('videos'), dict) else {}
            for source_id, proof in proofs.items():
                if proof['channel_id'] != context['channel_id']:
                    continue
                video = stored.get(proof['video_id'])
                video = video if isinstance(video, dict) else {}
                video_at = _timestamp(video.get('fetched_at'))
                reason = error or (video.get('error') if video.get('error') in _REASONS else None)
                duration = video.get('duration')
                checked_at = _timestamp(video.get('availability_checked_at'))
                availability = video.get('availability')
                if (not isinstance(availability, str) or availability not in {'available', 'unavailable'} or not checked_at
                        or checked_at > now + 5):
                    availability, checked_at = None, None
                output['videos'][source_id] = {
                    'video_id': proof['video_id'], 'channel_id': context['channel_id'], 'channel_title': row['title'],
                    'title': _text(video.get('title'), 100), 'view_count': _count(video.get('view_count')),
                    'like_count': _count(video.get('like_count')), 'comment_count': _count(video.get('comment_count')),
                    'privacy_status': video.get('privacy_status') if video.get('privacy_status') in {'private', 'unlisted', 'public'} else None,
                    'published_at': _published(video.get('published_at')),
                    'duration': _duration(duration),
                    'fetched_at': _iso(video_at), 'status': _state(video_at, reason, now),
                    'reason': reason or ('not_refreshed' if not video_at else None),
                    'availability': availability,
                    'availability_checked_at': _iso(checked_at),
                    'availability_evidence': 'owner_api_absent' if availability == 'unavailable' else None,
                }
        output['updated_at'] = _iso(min(fetched)) if fetched else None
    except Exception:
        output['error'] = 'cache_unavailable'
    return output


def _credentials(context):
    auth = _auth()
    payload = auth._decrypt_json(context['cipher'])
    if (not isinstance(payload, dict) or type(payload.get('version')) is not int or payload['version'] != 3
            or payload.get('channel_id') != context['channel_id'] or payload.get('connection_id') != context['connection_id']
            or not isinstance(payload.get('refresh_token'), str) or not 1 <= len(payload['refresh_token']) <= 4096):
        raise YouTubeMetricsError('connection_changed')
    credentials = auth._credential_from_refresh_token(payload['refresh_token'])
    request = auth.GoogleRequest()
    def bounded_request(*args, **kwargs):
        return request(*args, **{**kwargs, 'timeout': 20})
    credentials.refresh(bounded_request)
    if not credentials.token:
        raise YouTubeMetricsError('permission')
    return credentials


def _service(credentials):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    transport = AuthorizedHttp(credentials, http=httplib2.Http(timeout=20), max_refresh_attempts=0)
    return build('youtube', 'v3', http=transport, cache_discovery=False, static_discovery=True)


def _error(exc):
    if isinstance(exc, YouTubeMetricsError) and str(exc) in _REASONS:
        return str(exc)
    status = getattr(getattr(exc, 'resp', None), 'status', None)
    try:
        body = getattr(exc, 'content', None)
        if isinstance(body, bytes) and len(body) <= 16384:
            parsed = json.loads(body)
            codes = {item.get('reason') for item in parsed.get('error', {}).get('errors', []) if isinstance(item, dict)}
            if codes & {'quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded', 'userRateLimitExceeded'}:
                return 'quota'
    except Exception:
        pass
    if status in (401, 403):
        return 'permission'
    # RefreshError is recognized by type, never by returning its secret-bearing message.
    if type(exc).__name__ == 'RefreshError':
        return 'permission'
    return 'api_unavailable'


def _read_google(context, ids, previous, now):
    if context['blocked']:
        raise YouTubeMetricsError('permission')
    service = _service(_credentials(context))
    try:
        response = service.channels().list(part='id,snippet,statistics', mine=True, maxResults=50,
            fields='items(id,snippet/title,statistics(viewCount,subscriberCount,hiddenSubscriberCount,videoCount))').execute(num_retries=0)
        items = response.get('items') if isinstance(response, dict) else None
        if not isinstance(items, list) or len(items) > 50 or len([i for i in items if isinstance(i, dict) and i.get('id') == context['channel_id']]) != 1:
            raise YouTubeMetricsError('connection_changed')
        item = next(i for i in items if isinstance(i, dict) and i.get('id') == context['channel_id'])
        stats = item.get('statistics') if isinstance(item.get('statistics'), dict) else {}
        snippet = item.get('snippet') if isinstance(item.get('snippet'), dict) else {}
        hidden = stats.get('hiddenSubscriberCount')
        channel = {'title': _text(snippet.get('title')) or context['title'], 'fetched_at': now,
                   'view_count': _count(stats.get('viewCount')), 'video_count': _count(stats.get('videoCount')),
                   'subscriber_count_hidden': hidden if type(hidden) is bool else None,
                   'subscriber_count': None if hidden is True else _count(stats.get('subscriberCount'))}
        videos = {}
        if ids:
            response = service.videos().list(part='snippet,statistics,status,contentDetails', id=','.join(ids),
                fields='items(id,snippet(channelId,title,publishedAt),statistics(viewCount,likeCount,commentCount),status/privacyStatus,contentDetails/duration)').execute(num_retries=0)
            items = response.get('items') if isinstance(response, dict) else None
            if not isinstance(items, list) or len(items) > len(ids):
                raise YouTubeMetricsError('invalid_response')
            for item in items:
                if not isinstance(item, dict) or item.get('id') not in ids or item['id'] in videos:
                    raise YouTubeMetricsError('invalid_response')
                snippet = item.get('snippet')
                if not isinstance(snippet, dict) or snippet.get('channelId') != context['channel_id']:
                    raise YouTubeMetricsError('connection_changed')
                statistics = item.get('statistics') if isinstance(item.get('statistics'), dict) else {}
                status = item.get('status') if isinstance(item.get('status'), dict) else {}
                details = item.get('contentDetails') if isinstance(item.get('contentDetails'), dict) else {}
                videos[item['id']] = {'title': _text(snippet.get('title'), 100), 'published_at': _published(snippet.get('publishedAt')),
                    'view_count': _count(statistics.get('viewCount')), 'like_count': _count(statistics.get('likeCount')),
                    'comment_count': _count(statistics.get('commentCount')),
                    'privacy_status': status.get('privacyStatus') if status.get('privacyStatus') in ('private', 'unlisted', 'public') else None,
                    'duration': _duration(details.get('duration')), 'fetched_at': now,
                    'availability': 'available', 'availability_checked_at': now}
            old_videos = previous.get('videos') if isinstance(previous.get('videos'), dict) else {}
            for video_id in ids:
                if video_id not in videos:
                    old = old_videos.get(video_id)
                    # Only a successful owner-channel query can establish absence.
                    # Preserve historical counts/privacy; absence is not an upload
                    # failure or proof of who deleted/restricted the video.
                    videos[video_id] = {**(old if isinstance(old, dict) else {}),
                        'error': 'video_unavailable', 'availability': 'unavailable',
                        'availability_checked_at': now}
        return {'version': 1, 'channel_id': context['channel_id'], 'connection_id': context['connection_id'],
                'channel': channel, 'videos': videos, 'last_error': None, 'last_attempt_at': now}
    finally:
        try:
            service.close()
        except Exception:
            pass


def _refresh_channel(context, proofs):
    client, now = _redis(), time.time()
    previous = _cache(client, context)
    ids = list(dict.fromkeys(p['video_id'] for p in proofs.values() if p['channel_id'] == context['channel_id']))[:MAX_VIDEOS]
    if not client.set(LOCK_PREFIX + context['channel_id'] + ':' + context['connection_id'], '1', nx=True, ex=DEBOUNCE_SECONDS):
        return
    try:
        payload = _read_google(context, ids, previous, now)
    except Exception as exc:
        payload = {**previous, 'version': 1, 'channel_id': context['channel_id'], 'connection_id': context['connection_id'],
                   'last_error': _error(exc), 'last_attempt_at': now}
    auth = _auth()
    client.eval(_CACHE_COMMIT, 5, _cache_key(context), auth.CHANNEL_PREFIX + context['channel_id'],
                auth.CREDENTIAL_PREFIX + context['channel_id'], auth.CHANNEL_INDEX_KEY, auth.AUTH_EPOCH_KEY,
                context['raw'], context['cipher'], context['epoch'], context['channel_id'],
                json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False), CACHE_TTL_SECONDS)


def refresh_dashboard_metrics(jobs):
    """Explicit owner refresh: at most 10 channels / 50 uploaded IDs per channel."""
    try:
        contexts = _contexts(_redis())
        proofs = _proofs(jobs, contexts)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_refresh_channel, context, proofs) for context in contexts]
            for future in futures:
                try:
                    future.result()
                except Exception:
                    pass  # Old cache remains; GET never pretends a refresh succeeded.
    except Exception:
        pass
    return get_dashboard_metrics(jobs)
