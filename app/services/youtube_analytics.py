"""Bounded owner Analytics reads and cautious, cached editorial feedback.

No production, money, credential or publication mutation. Missing reports are
unknown, not zero. Reports use a fixed 28-day window ending three days ago;
Google may still return a shorter window. Successful reports refresh every six
hours; unavailable services are checked again after fifteen minutes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
import time

from app.services import youtube_metrics as metrics, youtube_analytics_auth as access

PREFIX = 'youtube_studio:analytics:report:v1:'
LOCK_PREFIX = 'youtube_studio:analytics:refresh:v1:'
REFRESH_SECONDS = 6 * 3600
RECOVERY_SECONDS = 15 * 60
STALE_SECONDS = 36 * 3600
TTL_SECONDS = 7 * 86400
MAX_VIDEOS = 50
METRICS = ('views', 'engagedViews', 'estimatedMinutesWatched', 'averageViewDuration',
           'averageViewPercentage', 'subscribersGained')
_HEADERS = ('video', 'creatorContentType', *METRICS)
_TYPES = ('STRING', 'STRING', 'INTEGER', 'INTEGER', 'FLOAT', 'FLOAT', 'FLOAT', 'INTEGER')
_CONTENT_TYPES = {spelling: canonical
    for canonical in ('SHORTS', 'VIDEO_ON_DEMAND', 'LIVE_STREAM', 'STORY', 'UNSPECIFIED')
    for spelling in (canonical, canonical.lower())}
_COMMIT = '''
if redis.call('GET', KEYS[2]) ~= ARGV[1] or redis.call('GET', KEYS[3]) ~= ARGV[2]
 or (redis.call('GET', KEYS[5]) or '0') ~= ARGV[3]
 or redis.call('SISMEMBER', KEYS[4], ARGV[4]) ~= 1
 or redis.call('GET', KEYS[6]) ~= ARGV[5] then return 0 end
local incoming = cjson.decode(ARGV[6])
local previous = redis.call('GET', KEYS[1])
if previous then
 local ok, old = pcall(cjson.decode, previous)
 if ok and type(old) == 'table' and type(old['last_attempt_at']) == 'number'
   and old['last_attempt_at'] >= incoming['last_attempt_at'] then return 0 end
end
redis.call('SETEX', KEYS[1], ARGV[7], ARGV[6])
return 1
'''


def _key(context):
    return PREFIX + context['channel_id'] + ':' + context['connection_id']


def _cache(client, context):
    value = metrics._object(client.get(_key(context)))
    if (not value or type(value.get('version')) is not int or value['version'] != 1
            or value.get('channel_id') != context['channel_id']
            or value.get('connection_id') != context['connection_id']):
        return {}
    return value


def _valid(condition):
    if not condition:
        raise metrics.YouTubeMetricsError('invalid_response')


def _number(value, *, integer=False):
    _valid(type(value) in ((int,) if integer else (int, float))
        and math.isfinite(value) and 0 <= value <= 2**53 - 1)
    return value


def _table(response, headers, types, maximum):
    _valid(type(response) is dict and response.get('kind') == 'youtubeAnalytics#resultTable')
    columns = response.get('columnHeaders')
    _valid(type(columns) is list and len(columns) == len(headers))
    for column, name, kind in zip(columns, headers, types):
        _valid(type(column) is dict and column.get('name') == name
            and column.get('dataType') in (('INTEGER', 'FLOAT') if kind == 'FLOAT' else (kind,))
            and column.get('columnType') == ('DIMENSION' if kind == 'STRING' or name == 'elapsedVideoTimeRatio' else 'METRIC'))
    rows = response.get('rows', [])
    _valid(type(rows) is list and len(rows) <= maximum
        and all(type(row) is list and len(row) == len(headers) for row in rows))
    return rows


def _videos(response, ids):
    videos = {}
    for row in _table(response, _HEADERS, _TYPES, MAX_VIDEOS):
        video, content_type = row[:2]
        _valid(type(video) is str and video in ids and video not in videos
            and type(content_type) is str and content_type in _CONTENT_TYPES)
        # Actual v2 reports return lowercase values although the dimension
        # reference documents uppercase names. Both map to one stored enum.
        content_type = _CONTENT_TYPES[content_type]
        values = {key: _number(value, integer=kind == 'INTEGER')
            for key, value, kind in zip(METRICS, row[2:], _TYPES[2:])}
        videos[video] = {'content_type': content_type, **values}
    return videos


def _retention(response):
    rows = _table(response, ('elapsedVideoTimeRatio', 'audienceWatchRatio'), ('FLOAT', 'FLOAT'), 100)
    points, previous = [], -1.0
    for position, ratio in rows:
        position, ratio = _number(position), _number(ratio)
        _valid(previous < position <= 1)
        points.append([position, ratio]); previous = position
    return points


def _service(credentials):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    return build('youtubeAnalytics', 'v2', http=AuthorizedHttp(credentials,
        http=httplib2.Http(timeout=20), max_refresh_attempts=0), cache_discovery=False, static_discovery=True)


def _read(context, encrypted, titles, now):
    end = datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=3)
    start = end - timedelta(days=27)
    query = {'ids': 'channel==' + context['channel_id'], 'startDate': start.isoformat(), 'endDate': end.isoformat()}
    credentials = access.read_credentials(context, encrypted)
    if titles is None:
        from app.services.youtube_analytics_inventory import public_uploads
        titles = public_uploads(credentials, context['channel_id'])
    if not titles:
        return {'videos': {}, 'inventory_count': 0, 'requested_start': start.isoformat(),
            'requested_end': end.isoformat(), 'fetched_at': now, 'last_error': None}
    service = _service(credentials)
    try:
        response = service.reports().query(**query, dimensions='video,creatorContentType',
            metrics=','.join(METRICS), filters='video==' + ','.join(titles),
            maxResults=MAX_VIDEOS, sort='-views').execute(num_retries=0)
        videos = _videos(response, titles)
        for video, row in videos.items():
            row['title'] = titles[video]
        selected = sorted((video for video, row in videos.items() if _sample(row) >= 100),
            key=lambda video: _sample(videos[video]), reverse=True)[:2]
        for video in selected:
            try:
                response = service.reports().query(**query, dimensions='elapsedVideoTimeRatio',
                    metrics='audienceWatchRatio', filters='video==' + video).execute(num_retries=0)
                videos[video]['retention'] = _retention(response)
            except Exception:
                videos[video]['retention'] = None  # Aggregate report is still useful.
        return {'videos': videos, 'inventory_count': len(titles), 'requested_start': start.isoformat(), 'requested_end': end.isoformat(),
            'fetched_at': now, 'last_error': None}
    finally:
        try: service.close()
        except Exception: pass


def _sample(row):
    return row['engagedViews'] if row['content_type'] == 'SHORTS' else row['views']


def _error(error):
    try:
        body = getattr(error, 'content', b'')
        if type(body) is bytes and len(body) <= 16384:
            reasons = {item.get('reason') for item in json.loads(body).get('error', {}).get('errors', [])
                if type(item) is dict}
            if 'accessNotConfigured' in reasons:
                return 'api_disabled'
    except Exception:
        pass
    return metrics._error(error)


def _interval(previous):
    return (RECOVERY_SECONDS if previous.get('last_error') in {'api_disabled', 'api_unavailable'}
        else REFRESH_SECONDS)


def _refresh(context):
    if context['blocked']:
        return
    client, now = metrics._redis(), time.time()
    encrypted = client.get(access.PREFIX + context['channel_id'])
    if not encrypted:
        return  # No grant: no refresh token exchange or Google request.
    previous = _cache(client, context)
    attempt = metrics._timestamp(previous.get('last_attempt_at'))
    if attempt is not None and (attempt > now or now - attempt < _interval(previous)):
        return
    if not client.set(LOCK_PREFIX + context['channel_id'], '1', nx=True, ex=300):
        return
    try:
        payload = _read(context, encrypted, None, now)
    except Exception as error:
        payload = {**previous, 'last_error': _error(error)}
    payload.update(version=1, channel_id=context['channel_id'], connection_id=context['connection_id'], last_attempt_at=now)
    auth = metrics._auth()
    client.eval(_COMMIT, 6, _key(context), auth.CHANNEL_PREFIX + context['channel_id'],
        auth.CREDENTIAL_PREFIX + context['channel_id'], auth.CHANNEL_INDEX_KEY, auth.AUTH_EPOCH_KEY,
        access.PREFIX + context['channel_id'], context['raw'], context['cipher'], context['epoch'],
        context['channel_id'], encrypted, json.dumps(payload, ensure_ascii=False, allow_nan=False), TTL_SECONDS)


def refresh(jobs):
    """Server observer only. One aggregate and up to two retention reads/channel."""
    del jobs  # Published video ownership comes from the authenticated channel.
    try:
        contexts = metrics._contexts(metrics._redis())
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_refresh, context) for context in contexts]
            for future in futures:
                try: future.result()
                except Exception: pass
    except Exception:
        return {'status': 'unavailable'}
    return {'status': 'checked'}


def dashboard():
    """Cached owner view only. Never decrypt credentials or contact Google."""
    result = {'channels': [], 'error': None}
    try:
        client, now = metrics._redis(), time.time()
        for context in metrics._contexts(client):
            value = _cache(client, context)
            at = metrics._timestamp(value.get('fetched_at'))
            attempted = metrics._timestamp(value.get('last_attempt_at'))
            has_grant = bool(client.get(access.PREFIX + context['channel_id']))
            reason = ('permission' if context['blocked'] else value.get('last_error'))
            status = ('needs_permission' if not has_grant or reason == 'permission' else
                'unavailable' if reason else 'waiting' if at is None else
                'fresh' if 0 <= now - at <= STALE_SECONDS else 'stale')
            proofs = value.get('videos') if type(value.get('videos')) is dict else {}
            # Validate cached observations as strictly as their original report.
            safe = {}
            for video, row in list(proofs.items())[:MAX_VIDEOS]:
                try:
                    _valid(type(video) is str and metrics._VIDEO.fullmatch(video) and type(row) is dict)
                    _videos({'kind': 'youtubeAnalytics#resultTable', 'columnHeaders': [
                        {'name': name, 'dataType': kind, 'columnType': 'DIMENSION' if kind == 'STRING' else 'METRIC'}
                        for name, kind in zip(_HEADERS, _TYPES)],
                        'rows': [[video, row.get('content_type'), *[row.get(name) for name in METRICS]]]}, [video])
                    safe[video] = {name: row[name] for name in ('content_type', *METRICS)}
                    safe[video]['title'] = metrics._text(row.get('title'), 100)
                    points = row.get('retention')
                    if type(points) is list:
                        try:
                            safe[video]['retention'] = _retention({'kind': 'youtubeAnalytics#resultTable',
                                'columnHeaders': [
                                    {'name': 'elapsedVideoTimeRatio', 'dataType': 'FLOAT', 'columnType': 'DIMENSION'},
                                    {'name': 'audienceWatchRatio', 'dataType': 'FLOAT', 'columnType': 'METRIC'}],
                                'rows': points})
                        except Exception: pass
                except Exception: continue
            result['channels'].append({'channel_id': context['channel_id'], 'title': context['title'],
                'status': status, 'reason': reason, 'fetched_at': metrics._iso(at),
                'last_attempt_at': metrics._iso(attempted),
                'next_check_at': metrics._iso(attempted + _interval(value))
                    if attempted is not None and has_grant and not context['blocked'] else None,
                'inventory_count': value.get('inventory_count')
                    if type(value.get('inventory_count')) is int and 0 <= value['inventory_count'] <= MAX_VIDEOS else None,
                'requested_start': value.get('requested_start'), 'requested_end': value.get('requested_end'),
                'videos': safe if has_grant and not context['blocked'] else {}})
    except Exception:
        result['error'] = 'cache_unavailable'
    return result


def editorial_guidance(channel_id, *, content_type='SHORTS'):
    """Observational hints only; small samples, stale or mixed formats never rank."""
    channel = next((row for row in dashboard()['channels'] if row['channel_id'] == channel_id), None)
    if not channel or channel['status'] != 'fresh':
        return None
    if content_type not in {'SHORTS', 'VIDEO_ON_DEMAND'}:
        return None
    eligible = [row for row in channel['videos'].values() if row['content_type'] == content_type and _sample(row) >= 100]
    if len(eligible) < 3:
        return None
    ordered = sorted(eligible, key=lambda row: row['averageViewPercentage'], reverse=True)
    drops = []
    for row in eligible:
        points = row.get('retention') or []
        if len(points) < 3:
            continue
        # A measured local drop is a revision hypothesis, not causal proof.
        first, last = max(zip(points, points[1:]), key=lambda pair: pair[0][1] - pair[1][1])
        if first[1] - last[1] >= .1:
            drops.append({'title': row['title'], 'from_fraction': first[0], 'to_fraction': last[0],
                'watch_ratio_drop': round(first[1] - last[1], 3)})
    return {'basis': ('observational_28_day_shorts_only_minimum_100_engaged_views_each' if content_type == 'SHORTS'
            else 'observational_28_day_long_videos_only_minimum_100_views_each'),
        'content_type': content_type,
        'sample_size': len(eligible), 'higher_retention_examples': [
            {'title': row['title'], 'average_view_percentage': row['averageViewPercentage'],
             'average_view_seconds': row['averageViewDuration']} for row in ordered[:2]],
        'observed_drop_examples': drops[:3],
        'instruction': 'Use these as tentative format/hook inspiration, never repeat their topics. '
            'Review pacing near observed drops; test clearer setup and earlier evidence without copying topics. '
            'Do not infer an optimal duration or automatic ranking from these averages. '
            'This small observational sample does not establish causality or predict reach. '
            'Keep primary-source, language, stock-footage, quality and spending requirements unchanged.'}
