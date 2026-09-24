"""Owner's ordered editorial queue, separate from historical series receipts.

Only an acknowledged immutable dispatch may enqueue a render. A completed
render is not a completed queue item: its exact publisher must report public
delivery. Unknown sends retain their claim. Browsing never advances the queue.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

import redis

from app.config import settings
from app.services import studio_state as jobs, channel_production as production
from app.services.youtube_automation import automated_quality_approved
from app.services.youtube_publish_state import UPLOAD_PREFIX

PREFIX = 'youtube_studio:content_plan:v1:'
PLAN_PREFIX = PREFIX + 'channel:'
DISPATCH_PREFIX = PREFIX + 'dispatch:'
DELIVERY_PREFIX = PREFIX + 'delivery:'
COMPLETION_PREFIX = PREFIX + 'completion:'
EXECUTION_PREFIX = PREFIX + 'execution:'
ACTIVE_KEY = PREFIX + 'active'
MAX_ITEMS = 80
# Three connected channels may each own a queued/held item. Execution remains
# limited to the existing two render processes; queue ownership is not a CPU slot.
MAX_OPEN_CHANNEL_JOBS = 3
CHANNEL = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
FORMATS = {'shorts': ('Shorts', .5), 'long': ('Uzun video', 3),
           'animation': ('Animasyon pilotu', .5)}


class ContentPlanError(ValueError):
    pass


def _require(value, code='plan_invalid'):
    if not value:
        raise ContentPlanError(code)


def _client():
    return redis.Redis.from_url(settings.redis_url, decode_responses=True,
                               socket_timeout=3, socket_connect_timeout=3)


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_raw(value).encode()).hexdigest()


def dispatch_spec_matches(dispatch, spec):
    """Redis Lua encodes the integral duration 3.0 as 3; no other edits qualify.

    Preserve the original dispatch and its hash. Only its exact former floating
    point encoding is accepted alongside the actual unchanged three-minute spec.
    """
    if _sha(spec) == dispatch.get('spec_sha256'):
        return True
    return bool(type(spec.get('duration_minutes')) in (int, float)
        and spec['duration_minutes'] == 3 and spec.get('format') == 'landscape'
        and dispatch.get('item', {}).get('format') == 'long'
        and _sha({**spec, 'duration_minutes': 3.0}) == dispatch.get('spec_sha256'))


def _object(raw):
    _require(type(raw) is str and 0 < len(raw.encode()) <= 512_000)
    value = json.loads(raw)
    _require(type(value) is dict)
    return value


def _id(value):
    _require(type(value) is str and str(UUID(value)) == value)
    return value


def _text(value, maximum):
    _require(type(value) is str and 1 <= len(value.strip()) <= maximum
             and not any(ord(c) < 32 and c not in '\n\r\t' for c in value))
    return value.strip()


def item(title, brief, format='shorts', *, series=None, depends_on=(), item_id=None):
    _require(format in FORMATS)
    result = {'id': _id(item_id or str(uuid4())), 'title': _text(title, 140),
              'brief': _text(brief, 4000), 'format': format, 'series': series,
              'depends_on': list(depends_on), 'created_at': datetime.now(timezone.utc).isoformat()}
    _validate_item(result)
    return result


def _validate_item(value):
    _require(type(value) is dict and set(value) == {
        'id', 'title', 'brief', 'format', 'series', 'depends_on', 'created_at'})
    _id(value['id']); _text(value['title'], 140); _text(value['brief'], 4000)
    _require(value['format'] in FORMATS and type(value['depends_on']) is list
             and len(value['depends_on']) <= MAX_ITEMS
             and len(set(value['depends_on'])) == len(value['depends_on']))
    for dependency in value['depends_on']:
        _id(dependency)
        _require(dependency != value['id'])
    series = value['series']
    if series is not None:
        _require(type(series) is dict and set(series) == {'id', 'name', 'number', 'total'}
                 and type(series['id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,80}', series['id'])
                 and type(series['number']) is int and type(series['total']) is int
                 and 1 <= series['number'] <= series['total'] <= 80)
        _text(series['name'], 100)
    instant = datetime.fromisoformat(value['created_at'])
    _require(instant.tzinfo is not None)


def _plan(raw, channel_id):
    value = _object(raw)
    _require(set(value) == {'version', 'channel_id', 'revision', 'enabled', 'after_queue', 'items', 'updated_at'}
             and type(value['version']) is int and value['version'] == 1
             and value['channel_id'] == channel_id and CHANNEL.fullmatch(channel_id)
             and type(value['enabled']) is bool and value['after_queue'] in {'pause', 'auto_shorts'}
             and type(value['items']) is list and len(value['items']) <= MAX_ITEMS)
    _id(value['revision'])
    seen = set(); positions = {}
    for entry in value['items']:
        _validate_item(entry)
        _require(entry['id'] not in seen and all(d in seen for d in entry['depends_on']), 'plan_order_invalid')
        seen.add(entry['id'])
        if entry['series']:
            series = entry['series']
            _require(series['number'] > positions.get(series['id'], 0), 'plan_series_order_invalid')
            positions[series['id']] = series['number']
    return value


def read(channel_id, *, client=None):
    _require(type(channel_id) is str and CHANNEL.fullmatch(channel_id))
    raw = (client or _client()).get(PLAN_PREFIX + channel_id)
    return _plan(raw, channel_id) if raw is not None else None


def owns_channel(channel_id, *, client=None):
    """An installed paused/blocked plan still owns its editorial position."""
    client = client or _client()
    raw = client.get(PLAN_PREFIX + channel_id)
    if raw is None:
        return False
    plan = _plan(raw, channel_id)
    if plan['after_queue'] == 'auto_shorts' and plan['enabled']:
        return any(not client.exists(COMPLETION_PREFIX + row['id']) for row in plan['items'])
    return True


def change(channel_id, expected_revision, action, *, payload=None, client=None):
    """CAS edits only future editorial intent; no provider, dispatch or profile edits."""
    client = client or _client(); key = PLAN_PREFIX + channel_id
    _require(type(channel_id) is str and CHANNEL.fullmatch(channel_id))
    try:
        with client.pipeline() as pipe:
            pipe.watch(key, production.OAUTH_CHANNEL_INDEX, production.PROFILE_PREFIX + channel_id)
            _require(pipe.sismember(production.OAUTH_CHANNEL_INDEX, channel_id)
                     and pipe.get(production.PROFILE_PREFIX + channel_id), 'plan_channel_missing')
            raw = pipe.get(key)
            if raw is None:
                _require(expected_revision == 'new', 'plan_changed')
                plan = {'version': 1, 'channel_id': channel_id, 'revision': str(uuid4()),
                        'enabled': True, 'after_queue': 'pause', 'items': [], 'updated_at': ''}
            else:
                plan = _plan(raw, channel_id)
                _require(plan['revision'] == expected_revision, 'plan_changed')
            payload = payload or {}
            if action in {'add', 'append'}:
                additions = [payload] if action == 'add' else payload
                _require(type(additions) is list and 1 <= len(additions) <= MAX_ITEMS)
                plan['items'].extend(additions)
            elif action == 'settings':
                _require(set(payload) == {'enabled', 'after_queue'} and type(payload['enabled']) is bool
                         and payload['after_queue'] in {'pause', 'auto_shorts'})
                plan.update(payload)
            elif action in {'up', 'down', 'remove'}:
                index = next((i for i, row in enumerate(plan['items']) if row['id'] == payload.get('id')), None)
                _require(index is not None, 'plan_item_missing')
                selected = plan['items'][index]
                keys = [DISPATCH_PREFIX + selected['id'], COMPLETION_PREFIX + selected['id']]
                pipe.watch(*keys)
                _require(not pipe.exists(*keys), 'plan_item_started')
                if action == 'remove':
                    plan['items'].pop(index)
                else:
                    target = index + (-1 if action == 'up' else 1)
                    _require(0 <= target < len(plan['items']), 'plan_order_invalid')
                    other = plan['items'][target]
                    pipe.watch(DISPATCH_PREFIX + other['id'], COMPLETION_PREFIX + other['id'])
                    _require(not pipe.exists(DISPATCH_PREFIX + other['id'], COMPLETION_PREFIX + other['id']),
                             'plan_item_started')
                    plan['items'][index], plan['items'][target] = other, selected
            else:
                raise ContentPlanError('plan_action_invalid')
            plan.update(revision=str(uuid4()), updated_at=datetime.now(timezone.utc).isoformat())
            _plan(_raw(plan), channel_id)
            pipe.multi(); pipe.set(key, _raw(plan))
            _require(pipe.execute() == [True], 'plan_save_unverified')
            return plan
    except redis.WatchError:
        raise ContentPlanError('plan_changed') from None


def _leaf(client, dispatch):
    root = dispatch['task_id']; current = root; seen = set(); parent = None
    # A final child can follow twelve independently authorized recovery nodes.
    # This only bounds traversal; each recovery and paid-work limit is separate.
    for _ in range(16):
        _require(current not in seen, 'plan_lineage_invalid'); seen.add(current)
        job = _object(client.get(jobs.JOB_PREFIX + current))
        _require(job.get('task_id') == current and job.get('parent_id') == parent
                 and (job.get('spec') or {}).get('content_plan_item_id') == dispatch['item']['id'],
                 'plan_lineage_invalid')
        child = job.get('retry_child_task_id')
        if not child:
            return job
        parent, current = current, _id(child)
    raise ContentPlanError('plan_lineage_invalid')


def publication_proof(client, dispatch):
    source = _leaf(client, dispatch)
    if not automated_quality_approved(source):
        return None
    task = source['task_id']; raw = client.get(UPLOAD_PREFIX + task)
    if raw is None:
        return None
    receipt = _object(raw); publisher_id = receipt.get('publish_task_id')
    if not publisher_id:
        return None
    publisher = _object(client.get(jobs.JOB_PREFIX + publisher_id))
    plan = receipt.get('publish_plan') or {}; youtube = (source.get('result') or {}).get('youtube') or {}
    result = publisher.get('result') or {}; video = receipt.get('youtube_video_id')
    if not (receipt.get('status') == 'complete' and receipt.get('release_status') == 'public'
            and receipt.get('source_task_id') == task and publisher.get('state') == 'SUCCESS'
            and publisher.get('parent_id') == task and publisher.get('kind') == 'publish'
            and result.get('privacy_status') == result.get('release_status') == 'public'
            and youtube.get('privacy_status') == youtube.get('release_status') == 'public'
            and type(video) is str and re.fullmatch('[A-Za-z0-9_-]{11}', video)
            and youtube.get('video_id') == result.get('youtube_video_id') == video
            and all(v.get('target_channel_id') == dispatch['channel_id'] for v in (receipt, result, youtube, plan))
            and all(v.get('connection_id') == dispatch['connection_id'] for v in (receipt, result, youtube))
            and plan.get('profile_revision') == dispatch['profile_revision']
            and plan.get('series') == dispatch['item']['series']):
        return None
    return {'version': 1, 'item_id': dispatch['item']['id'], 'channel_id': dispatch['channel_id'],
            'root_task_id': dispatch['task_id'], 'source_task_id': task, 'publisher_task_id': publisher_id,
            'video_id': video, 'url': 'https://www.youtube.com/watch?v=' + video,
            'dispatch_sha256': _sha(dispatch), 'receipt_sha256': _sha(receipt),
            'completed_at': datetime.now(timezone.utc).isoformat()}


def project(plan, *, client=None):
    """Bounded owner view, no credentials, storage URLs or state changes."""
    client = client or _client(); rows = []
    for entry in plan['items']:
        row = {**entry, 'status': 'queued', 'label': 'Sırada', 'task_id': None, 'progress': 0}
        raw = client.get(DISPATCH_PREFIX + entry['id'])
        completion = client.get(COMPLETION_PREFIX + entry['id'])
        if completion:
            proof = _object(completion)
            row.update(status='published', label='Yayımlandı', url=proof['url'],
                       task_id=proof['source_task_id'], progress=100)
        elif raw:
            dispatch = _object(raw); row['task_id'] = dispatch['task_id']
            try:
                leaf = _leaf(client, dispatch)
                row.update(task_id=leaf['task_id'], progress=leaf.get('progress') or 0, stage=leaf.get('stage'))
                if leaf.get('state') == 'SUCCESS':
                    row.update(status='publishing', label='Yayın bekliyor')
                    automation = (leaf.get('result') or {}).get('youtube_automation') or {}
                    if automation.get('status') == 'daily_limit_wait':
                        row.update(status='daily_limit_wait', label='Yarın otomatik yayımlanacak')
                    delivery = (leaf.get('result') or {}).get('youtube') or {}
                    if delivery.get('release_status') in {'blocked', 'uncertain'} or automation.get('status') in {
                            'metadata_blocked', 'profile_changed', 'connection_changed', 'quality_blocked', 'reservation_blocked'}:
                        row.update(status='blocked', label='Yayın kontrolü gerekli')
                elif leaf.get('state') in {'FAILURE', 'CANCELLED'}:
                    row.update(status='blocked', label='Üretim kontrolü gerekli')
                    from app.services.content_plan_recovery import STATUS
                    recovery = client.get(STATUS + leaf['task_id'])
                    if recovery and _object(recovery).get('state') == 'preparing':
                        row.update(status='running', label='Hazır kayıtlarla onarılıyor')
                    from app.services.content_plan_retained_completion import deferred_quota
                    waiting = deferred_quota(client, leaf, require_current_credential=False)
                    if waiting and not leaf.get('retry_child_task_id'):
                        when = datetime.fromisoformat(waiting['retry_at']).astimezone(timezone.utc)
                        row.update(status='waiting', label='Görüntü kotası bekleniyor · ' + when.strftime('%d.%m %H:%M UTC'),
                                   retry_at=waiting['retry_at'], progress=0)
                else:
                    row.update(status='running', label='Üretiliyor')
            except (ContentPlanError, ValueError, TypeError):
                row.update(status='blocked', label='Kayıt doğrulanıyor')
        elif entry['format'] == 'animation':
            from app.services.framecase_cadence import CHANNEL_ID
            if plan['channel_id'] != CHANNEL_ID:
                row.update(status='preparation', label='Üretim hazırlığı')
        elif not plan['enabled']:
            row.update(status='paused', label='Duraklatıldı')
        rows.append(row)
    from app.services.framecase_cadence import snapshot
    from app.services.content_plan_attention import recent
    return {**plan, 'items': rows, 'daily_cadence': snapshot(plan['channel_id'], client=client),
        'attention': recent(plan['channel_id'], client=client)}


def _active(client):
    raw = client.get(ACTIVE_KEY)
    result = _object(raw) if raw else {}
    _require(len(result) <= MAX_OPEN_CHANNEL_JOBS and all(CHANNEL.fullmatch(k) for k in result))
    for v in result.values():
        _id(v)
    return result


def _reserve(channel_id, *, now=None):
    from app.services.production_spend_runtime import preflight_scheduled_production
    client = _client(); now = time.time() if now is None else now
    plan = read(channel_id, client=client)
    if plan is None or not plan['enabled']:
        return {'status': 'paused'}
    entry = next((v for v in plan['items'] if not client.exists(COMPLETION_PREFIX + v['id'])), None)
    if entry is None:
        return {'status': 'complete'}
    from app.services.framecase_cadence import CHANNEL_ID as framecase_channel
    animated = channel_id == framecase_channel and entry['format'] in {'animation', 'long'}
    if entry['format'] == 'animation' and not animated:
        return {'status': 'format_preparation'}
    # Never reserve a queue entry on a funding failure.
    kind = 'shorts' if entry['format'] == 'animation' else entry['format']
    duration = FORMATS[kind][1]
    preflight_scheduled_production(channel_id, kind=kind)
    key, dispatch_key = PLAN_PREFIX + channel_id, DISPATCH_PREFIX + entry['id']
    profile_key, channel_key = production.PROFILE_PREFIX + channel_id, production.OAUTH_CHANNEL_PREFIX + channel_id
    credential_key = production.OAUTH_CREDENTIAL_PREFIX + channel_id
    task = str(uuid5(NAMESPACE_URL, 'youtube-owner-plan:' + channel_id + ':' + entry['id']))
    with client.pipeline() as pipe:
        pipe.watch(key, dispatch_key, ACTIVE_KEY, production.ACTIVE_KEY, profile_key, channel_key,
                   credential_key, production.OAUTH_CHANNEL_INDEX, jobs.JOB_PREFIX + task)
        _require(pipe.get(key) == _raw(plan), 'plan_changed')
        for previous in plan['items']:
            if previous['id'] == entry['id']:
                break
            pipe.watch(COMPLETION_PREFIX + previous['id'])
            _require(pipe.exists(COMPLETION_PREFIX + previous['id']), 'plan_previous_not_public')
        if pipe.exists(dispatch_key):
            return {'status': 'already_dispatched'}
        active = _active(pipe)
        legacy_raw = pipe.get(production.ACTIVE_KEY)
        legacy = production._decode_active_claims(legacy_raw) if legacy_raw else []
        if (channel_id in active or any(v['channel_id'] == channel_id for v in legacy)
                or len(active) + len(legacy) >= MAX_OPEN_CHANNEL_JOBS):
            return {'status': 'capacity_wait'}
        _require(not pipe.exists(jobs.JOB_PREFIX + task), 'plan_dispatch_conflict')
        profile, channel = _object(pipe.get(profile_key)), _object(pipe.get(channel_key))
        _require(profile.get('auto_publish') is True and profile.get('production_enabled') is True
                 and profile.get('release_mode') == 'public' and channel.get('id') == channel_id
                 and channel.get('requires_reconnect') is not True and pipe.get(credential_key)
                 and pipe.sismember(production.OAUTH_CHANNEL_INDEX, channel_id), 'plan_channel_unavailable')
        language = profile.get('default_language'); _require(language in {'tr', 'en'})
        identity = str(profile.get('channel_identity') or '').strip()[:240]
        brief = entry['brief'] + ('\n\nChannel editorial direction: ' + identity if identity else '')
        if entry['format'] == 'long' and not animated:
            brief += (f'\nProduction direction: EXACTLY 30 scenes. Each scene has 8-{12 if language == "tr" else 14} spoken words, '
                      'one natural sentence and one concrete visual action. Maintain a continuous '
                      'three-minute documentary arc, with an opening question and a clear final answer. '
                      'Avoid listicle structure, generic closing advice and unaudited current claims.')
        options = {'mode': 'production', 'format': 'landscape' if kind == 'long' else 'shorts', 'workflow': 'auto',
                   'content_style': 'documentary', 'pace': 'balanced', 'visual_mix': 'real_first',
                   'music': 'off', 'subtitles': 'sidecar', 'quality_threshold': 86,
                   'reference_url': None, 'production_scheduled': True,
                   'publish_after_render': True, 'production_channel_id': channel_id,
                   'production_connection_id': channel['connection_id'],
                   'production_profile_revision': profile['profile_revision'],
                   'content_plan_item_id': entry['id']}
        if animated:
            options.update(content_style='original_animation', visual_mix='ai_first', framecase_animation=True)
        route = profile.get('route_label') or channel_id
        spec = {'topic': brief, 'duration_minutes': duration, 'language': language, 'channel_id': route, **options}
        instant = datetime.fromtimestamp(now, timezone.utc).isoformat()
        job = {'task_id': task, 'kind': 'render', 'parent_id': None, 'spec': spec, 'state': 'PENDING',
               'stage': 'queued', 'progress': 0, 'message': 'Yayın planındaki sıradaki bölüm hazırlanıyor.',
               'created_ts': now, 'created_at': instant, 'updated_at': instant, 'result': None, 'error': None}
        dispatch = {'version': 1, 'item': entry, 'channel_id': channel_id, 'task_id': task,
                    'connection_id': channel['connection_id'], 'profile_revision': profile['profile_revision'],
                    'spec_sha256': _sha(spec), 'reserved_at': instant}
        active[channel_id] = entry['id']
        from app.services import framecase_cadence, channel_cadence
        cadence = framecase_cadence if channel_id == framecase_cadence.CHANNEL_ID else channel_cadence
        cadence_key = cadence.production_slot(pipe, channel_id, kind, task, now=now)
        if cadence_key is False:
            return {'status': 'daily_limit_wait'}
        pipe.multi(); pipe.set(dispatch_key, _raw(dispatch), nx=True)
        pipe.set(jobs.JOB_PREFIX + task, _raw(job), nx=True)
        pipe.zadd(jobs.JOB_INDEX, {task: now}); pipe.set(ACTIVE_KEY, _raw(active))
        if cadence_key:
            pipe.hset(cadence_key, task, kind)
        response = pipe.execute()
        if cadence_key:
            response = response[:-1]
        _require(response[0:2] == [True, True] and len(response) == 4 and response[-1] is True,
                 'plan_dispatch_uncertain')
        return {'status': 'reserved', 'task_id': task, 'item_id': entry['id'],
                'args': (brief, duration, language, route, options, None)}


def maintain(profiles, enqueue, *, repair_enqueue=None):
    """Server tick: acknowledge exact public output, then dispatch the next item."""
    client = _client(); statuses = {}
    for profile in profiles:
        channel_id = profile.get('channel_id')
        try:
            plan = read(channel_id, client=client)
            if not plan:
                continue
            active = _active(client); entry_id = active.get(channel_id)
            if entry_id:
                dispatch = _object(client.get(DISPATCH_PREFIX + entry_id))
                proof = publication_proof(client, dispatch)
                if proof is None:
                    statuses[channel_id] = 'working_or_blocked'
                    leaf = _leaf(client, dispatch)
                    if (leaf.get('spec') or {}).get('framecase_animation') is True:
                        from app.services.framecase_recovery import schedule as framecase_schedule
                        statuses[channel_id] = framecase_schedule(leaf)
                        continue
                    from app.services import content_plan_factual_resume
                    if repair_enqueue is not None and content_plan_factual_resume.eligible(leaf):
                        statuses[channel_id] = content_plan_factual_resume.schedule(leaf, repair_enqueue, client=client)
                        continue
                    from app.services.content_plan_attention import isolate
                    isolated = isolate(leaf, client=client)
                    if isolated is not None:
                        statuses[channel_id] = isolated
                        continue
                    from app.services.content_plan_unstarted_resume import schedule as resume_unstarted
                    resumed = resume_unstarted(leaf, enqueue, client=client)
                    if resumed is not None:
                        statuses[channel_id] = resumed
                        continue
                    if repair_enqueue is not None:
                        from app.services.content_plan_recovery import schedule
                        statuses[channel_id] = schedule(_leaf(client, dispatch), repair_enqueue, client=client)
                    continue
                with client.pipeline() as pipe:
                    pipe.watch(ACTIVE_KEY, DISPATCH_PREFIX + entry_id, COMPLETION_PREFIX + entry_id,
                               jobs.JOB_PREFIX + proof['source_task_id'], jobs.JOB_PREFIX + proof['publisher_task_id'],
                               UPLOAD_PREFIX + proof['source_task_id'])
                    active = _active(pipe)
                    _require(active.get(channel_id) == entry_id and _object(pipe.get(DISPATCH_PREFIX + entry_id)) == dispatch)
                    current = publication_proof(pipe, dispatch)
                    _require(current and current['receipt_sha256'] == proof['receipt_sha256'])
                    _require(not pipe.exists(COMPLETION_PREFIX + entry_id), 'plan_completion_conflict')
                    del active[channel_id]
                    pipe.multi(); pipe.set(COMPLETION_PREFIX + entry_id, _raw(proof), nx=True)
                    pipe.set(ACTIVE_KEY, _raw(active)); _require(pipe.execute() == [True, True])
            reservation = _reserve(channel_id)
            statuses[channel_id] = reservation['status']
            if reservation['status'] != 'reserved':
                continue
            outcome = 'enqueued'
            try:
                enqueue(args=reservation['args'], task_id=reservation['task_id'], retry=False)
            except Exception:
                outcome = 'uncertain'
            client.set(DELIVERY_PREFIX + reservation['item_id'], _raw({'status': outcome}), nx=True)
            statuses[channel_id] = outcome
        except Exception as error:
            from app.services.production_spend import SpendBlocked
            statuses[channel_id] = str(error) if isinstance(error, (ContentPlanError, SpendBlocked)) else 'plan_unavailable'
    return {'status': 'checked', 'channels': statuses}


def publication_series(source, profile, *, client=None):
    """Validate the immutable queue assignment without touching old series counters."""
    client = client or _client(); spec = source.get('spec') or {}
    entry_id = _id(spec.get('content_plan_item_id'))
    dispatch = _object(client.get(DISPATCH_PREFIX + entry_id))
    _require(dispatch['channel_id'] == profile.get('channel_id') == spec.get('production_channel_id')
             and profile.get('auto_publish') is True and profile.get('release_mode') == 'public'
             and dispatch['connection_id'] == spec.get('production_connection_id')
             and dispatch['profile_revision'] == profile.get('profile_revision') == spec.get('production_profile_revision'),
             'plan_publication_binding_changed')
    root = _object(client.get(jobs.JOB_PREFIX + dispatch['task_id']))
    _require(dispatch_spec_matches(dispatch, root['spec'])
             and _leaf(client, dispatch)['task_id'] == source.get('task_id'), 'plan_publication_binding_changed')
    plan = read(dispatch['channel_id'], client=client)
    _require(plan and any(row == dispatch['item'] for row in plan['items']), 'plan_publication_binding_changed')
    for previous in plan['items']:
        if previous['id'] == entry_id:
            break
        _require(client.exists(COMPLETION_PREFIX + previous['id']), 'plan_previous_not_public')
    return dispatch['item']['series']


def check_publication(source, frozen_plan):
    if 'content_plan_item_id' not in (source.get('spec') or {}):
        return
    from app.services.youtube_automation import get_channel_profile
    profile = get_channel_profile(source['spec']['production_channel_id'])
    expected = publication_series(source, profile or {})
    _require(frozen_plan.get('series') == expected and frozen_plan.get('caption_required') is False,
             'plan_publication_series_changed')


def observe_execution(request):
    """A duplicated queue delivery cannot re-enter paid production."""
    from celery.exceptions import Ignore
    client = _client(); task = request.id
    raw = client.get(jobs.JOB_PREFIX + task)
    if not raw:
        return
    job = _object(raw); entry = (job.get('spec') or {}).get('content_plan_item_id')
    if not entry:
        return
    dispatch = _object(client.get(DISPATCH_PREFIX + _id(entry)))
    _require(_leaf(client, dispatch)['task_id'] == task, 'plan_execution_changed')
    key = EXECUTION_PREFIX + task + ':' + str(request.retries)
    with client.pipeline() as pipe:
        pipe.watch(key, jobs.JOB_PREFIX + task)
        _require(pipe.get(jobs.JOB_PREFIX + task) == raw, 'plan_execution_changed')
        if pipe.exists(key) or job.get('state') in {'SUCCESS', 'FAILURE', 'CANCELLED'}:
            raise Ignore()
        _require(type(request.retries) is int and 0 <= request.retries <= 2)
        pipe.multi(); pipe.set(key, _raw({'task_id': task, 'attempt': request.retries}), nx=True)
        _require(pipe.execute() == [True], 'plan_execution_uncertain')
