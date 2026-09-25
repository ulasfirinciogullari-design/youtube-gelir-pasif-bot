"""Owner's per-channel daily production and publication ceiling.

Dates use Europe/Istanbul. Unfinished publication reservations count on every
day until delivery is known, including across midnight and worker restarts.
This prevents yesterday's in-flight upload from bypassing today's ceiling.
"""
from datetime import datetime, timezone, date, timedelta
import json
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo
from redis.client import Pipeline

from app.services import studio_state as jobs
from app.services.channel_formats import CHANNELS, shorts_only, allows, daily_limits, production_limits, policy_id
from app.services.included_stock_pool import _local_transaction

PREFIX = 'youtube_studio:channel_cadence:v1:'
LIMITS = {'long': 1, 'shorts': 5}
ZONE = ZoneInfo('Europe/Istanbul')
WAITING_KEY = PREFIX + 'waiting'
ADVANCE_PREFIX = PREFIX + 'advance:'


def _client():
    from app.services.channel_production import _redis
    return _redis()


def _require(value):
    if not value: raise ValueError('channel_cadence_unverified')


def _now(now=None):
    value = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    return value.astimezone(ZONE)


def kind(spec):
    value = spec.get('format')
    _require(value in {'shorts', 'landscape'})
    return 'shorts' if value == 'shorts' else 'long'


def keys(channel_id, *, now=None):
    day = _now(now).date().isoformat()
    base = PREFIX + channel_id + ':'
    identity = policy_id(channel_id)
    day_base = base + 'policy:' + identity + ':' if identity else base
    # A new owner-approved quota epoch leaves old day receipts intact. Pending
    # uploads stay shared across every epoch and day until their outcome is known.
    return (day_base + 'produced:' + day, day_base + 'published:' + day, base + 'pending')


def _values(client, key):
    value = client.hgetall(key)
    _require(len(value) <= 10_000 and all(v in LIMITS for v in value.values()))
    return value


def _completed_day(reader, channel_id, *, now=None):
    published = keys(channel_id, now=now)[1]
    if isinstance(reader, Pipeline): reader.watch(published)
    rows = _values(reader, published)
    return rows if all(sum(v == kind for v in rows.values()) >= limit
                       for kind, limit in LIMITS.items()) else None


def _stock_day(reader, channel_id, item_id):
    """Verify the advance reservation against its immutable daily plan receipt."""
    if not item_id: return None
    _require(str(UUID(item_id)) == item_id)
    key = ADVANCE_PREFIX + item_id
    if isinstance(reader, Pipeline): reader.watch(key)
    raw = reader.get(key)
    if raw is None: return None
    row = json.loads(raw)
    _require(set(row) == {'version', 'channel_id', 'item_id', 'day', 'source_day', 'item_sha256',
                         'completed_publications'} and type(row['version']) is int and row['version'] == 1
        and row['channel_id'] == channel_id and row['item_id'] == item_id
        and date.fromisoformat(row['source_day']) + timedelta(days=1) == date.fromisoformat(row['day'])
        and item_id == str(uuid5(NAMESPACE_URL, 'owner-daily-long:' + channel_id + ':' + row['day'])))
    receipt_key = PREFIX + 'daily_plan:' + channel_id + ':' + row['day']
    published_key = PREFIX + channel_id + ':published:' + row['source_day']
    if isinstance(reader, Pipeline): reader.watch(receipt_key, published_key)
    receipt = json.loads(reader.get(receipt_key) or '{}')
    _require(receipt.get('channel_id') == channel_id and receipt.get('item_id') == item_id
        and receipt.get('day') == row['day'] and receipt.get('advance') == row
        and receipt.get('item_sha256') == row['item_sha256'])
    original, current = row['completed_publications'], _values(reader, published_key)
    _require(type(original) is dict and original
        and all(kind in LIMITS and current.get(root) == kind for root, kind in original.items())
        and all(sum(v == kind for v in original.values()) >= limit for kind, limit in LIMITS.items()))
    return row


def production_slot(pipe, channel_id, format_kind, task_id, *, now=None, item=None):
    """Read inside the caller's WATCH; the caller writes with its job commit."""
    if channel_id not in CHANNELS: return None
    _require(format_kind in LIMITS and str(UUID(task_id)) == task_id)
    if not allows(channel_id, format_kind): return False
    stock = _stock_day(pipe, channel_id, item['id']) if item is not None else None
    if stock:
        from app.services import content_plan as plan
        _require(format_kind == 'long' and plan._sha(item) == stock['item_sha256'])
        target = date.fromisoformat(stock['day']); today = _now(now).date()
        _require(target <= today + timedelta(days=1))
        # A restart after the intended date consumes today's allowance. It
        # cannot reopen an old production day or reset the original root.
        now = datetime.combine(max(target, today), datetime.min.time(), ZONE).timestamp()
    selected = keys(channel_id, now=now); pipe.watch(*selected)
    if shorts_only(channel_id):
        delivered = _values(pipe, selected[1]); delivered.update(_values(pipe, selected[2]))
        if sum(v == format_kind for v in delivered.values()) >= daily_limits(channel_id, LIMITS)[format_kind]:
            return False
    used = {}
    for key in selected: used.update(_values(pipe, key))
    if sum(v == format_kind for v in used.values()) >= production_limits(channel_id, LIMITS)[format_kind]: return False
    return selected[0]


def lua_arguments(channel_id, format_kind, *, now=None):
    _require(format_kind in LIMITS)
    if not allows(channel_id, format_kind):
        # Zero already means an unmanaged channel in the historical script.
        return (*keys(channel_id, now=now), format_kind, -1, 0)
    return (*keys(channel_id, now=now), format_kind,
        production_limits(channel_id, LIMITS)[format_kind] if channel_id in CHANNELS else 0,
        daily_limits(channel_id, LIMITS)[format_kind] if shorts_only(channel_id) else 0)


def daily_editorial(channel_id, fallback, *, client, now=None, defer_daily_long=False):
    """The new daily mix applies after an existing ordered queue has finished."""
    if channel_id not in CHANNELS: return fallback
    if shorts_only(channel_id):
        return {'version': 1, 'format': 'shorts', 'duration_minutes': .5,
            'reason_code': 'owner_shorts_only',
            'reason': f"Yalnız Shorts; bu kanalda günde en fazla {daily_limits(channel_id, LIMITS)['shorts']} yayın, Türkiye saati.",
            'scope_signals': ['daily_shorts']}
    if defer_daily_long:
        return {'version': 1, 'format': 'shorts', 'duration_minutes': .5,
            'reason_code': 'owner_daily_voice_priority',
            'reason': 'Uzun video ses kredisi bekliyor; uygun Shorts üretimi devam ediyor.',
            'scope_signals': ['daily_shorts', 'long_waiting_for_voice_capacity']}
    if _completed_day(client, channel_id, now=now):
        return {'version': 1, 'format': 'landscape', 'duration_minutes': 3.0,
            'reason_code': 'owner_next_day_stock',
            'reason': 'Günün yayınları tamamlandı; yarının ilk uzun videosu stok için hazırlanıyor.',
            'scope_signals': ['next_day_stock']}
    used = {}
    for key in keys(channel_id, now=now): used.update(_values(client, key))
    long_form = sum(value == 'long' for value in used.values()) < LIMITS['long']
    return {'version': 1, 'format': 'landscape' if long_form else 'shorts',
        'duration_minutes': 3.0 if long_form else .5, 'reason_code': 'owner_daily_mix',
        'reason': 'Günlük plan: bir uzun video ve beş Shorts; Türkiye saati.',
        'scope_signals': ['daily_long' if long_form else 'daily_shorts']}


@_local_transaction
def install_daily_long(profile, topics, *, client, now=None, advance=False):
    """Use the already verified owner-plan long-form engine before five Shorts.

    The five source-backed Short topics and their production cursor stay in
    place. The long-form overview is a real queue entry with normal dispatch,
    frozen authority, full QA and public-delivery checks.
    """
    from app.services import content_plan as plan, channel_production as production
    channel = profile['channel_id']; _require(channel in CHANNELS and topics)
    if shorts_only(channel): return {'status': 'format_disabled'}
    _require(type(advance) is bool)
    today = _now(now).date()
    day = (today + timedelta(days=1) if advance else today).isoformat()
    item_id = str(uuid5(NAMESPACE_URL, 'owner-daily-long:' + channel + ':' + day))
    brief = ('Kaynaklı günlük belgesel; aşağıdaki konuları bağlayan tek bir soru ve tutarlı hikâye kur. '
        'İddiaları verilen kaynaklarla doğrula.\n' if profile.get('default_language') == 'tr' else
        'Source-backed daily documentary: one question and one continuous story connecting these topics. '
        'Verify claims against their cited sources.\n') + '\n'.join(topics[:5])
    entry = plan.item('Günün uzun videosu · ' + day, brief, 'long', item_id=item_id)
    key = plan.PLAN_PREFIX + channel; receipt_key = PREFIX + 'daily_plan:' + channel + ':' + day
    with client.pipeline() as pipe:
        pipe.watch(key, receipt_key, plan.ACTIVE_KEY, production.PROFILE_PREFIX + channel)
        daily_public = _completed_day(pipe, channel, now=now) if advance else None
        if advance: _require(daily_public is not None)
        _require(json.loads(pipe.get(production.PROFILE_PREFIX + channel) or '{}') == profile)
        raw = pipe.get(key)
        if pipe.get(receipt_key):
            current = plan._plan(raw, channel)
            return {'status': 'daily_long_planned' if any(row['id'] == item_id for row in current['items'])
                else 'daily_long_removed_by_owner'}
        document = plan._plan(raw, channel) if raw else {'version': 1, 'channel_id': channel,
            'revision': str(uuid4()), 'enabled': True, 'after_queue': 'auto_shorts', 'items': [], 'updated_at': ''}
        _require(document['enabled'] and document['after_queue'] == 'auto_shorts'
            and channel not in plan._active(pipe))
        history = {}
        for row in document['items']:
            completion_key = plan.COMPLETION_PREFIX + row['id']; pipe.watch(completion_key)
            value = pipe.get(completion_key); _require(value is not None)
            history[completion_key] = value
        # Full completed plans are archived verbatim. All jobs, source/dispatch
        # records and immutable completion receipts remain at their old keys.
        if len(document['items']) >= plan.MAX_ITEMS:
            document['items'] = []
        document['items'].append(entry)
        document.update(revision=str(uuid4()), updated_at=_now(now).isoformat())
        plan._plan(plan._raw(document), channel)
        audit = {'version': 1, 'channel_id': channel, 'day': day, 'item_id': item_id,
            'profile_revision': profile['profile_revision'], 'original_plan': raw,
            'completed_history': history, 'item_sha256': plan._sha(entry), 'new_revision': document['revision']}
        stock = None
        if advance:
            stock = {'version': 1, 'channel_id': channel, 'item_id': item_id,
                'day': day, 'source_day': today.isoformat(), 'item_sha256': plan._sha(entry),
                'completed_publications': daily_public}
            pipe.watch(ADVANCE_PREFIX + item_id)
            _require(pipe.get(ADVANCE_PREFIX + item_id) is None)
            audit['advance'] = stock
        pipe.multi(); pipe.set(receipt_key, plan._raw(audit), nx=True); pipe.set(key, plan._raw(document))
        if stock: pipe.set(ADVANCE_PREFIX + item_id, plan._raw(stock), nx=True)
        _require(pipe.execute() == ([True, True, True] if stock else [True, True]))
    return {'status': 'daily_long_planned', 'item_id': item_id}


# Runs after all existing eligibility checks, inside the original job/cursor
# transaction. No reservation is consumed by not_due or capacity_wait ticks.
PRODUCTION_LUA = r'''
if tonumber(ARGV[15]) < 0 then return 'format_disabled' end
if tonumber(ARGV[16] or '0') > 0 then
  local delivered = {}
  for _, key in ipairs({KEYS[12], KEYS[13]}) do
    local rows = redis.call('HGETALL', key)
    for i = 1, #rows, 2 do
      if rows[i+1] ~= 'shorts' and rows[i+1] ~= 'long' then return 'invalid_state' end
      delivered[rows[i]] = rows[i+1]
    end
  end
  local count = 0
  for _, value in pairs(delivered) do if value == ARGV[14] then count = count + 1 end end
  if count >= tonumber(ARGV[16]) then return 'daily_limit_wait' end
end
if tonumber(ARGV[15]) > 0 then
  local used = {}
  for _, key in ipairs({KEYS[11], KEYS[12], KEYS[13]}) do
    local rows = redis.call('HGETALL', key)
    for i = 1, #rows, 2 do
      if rows[i+1] ~= 'shorts' and rows[i+1] ~= 'long' then return 'invalid_state' end
      used[rows[i]] = rows[i+1]
    end
  end
  local count = 0
  for _, value in pairs(used) do if value == ARGV[14] then count = count + 1 end end
  if count >= tonumber(ARGV[15]) then
    if tonumber(ARGV[16] or '0') > 0 then return 'production_attempt_limit_wait' end
    return 'daily_limit_wait'
  end
  redis.call('HSET', KEYS[11], ARGV[9], ARGV[14])
end
'''


def _root(client, source):
    current = source; seen = set()
    channel = source.get('spec', {}).get('production_channel_id')
    for _ in range(16):
        task = current.get('task_id'); _require(str(UUID(task)) == task and task not in seen)
        seen.add(task)
        parent = current.get('parent_id')
        if not parent: return task
        previous = json.loads(client.get(jobs.JOB_PREFIX + parent) or '{}')
        _require(previous.get('retry_child_task_id') == task
            and previous.get('spec', {}).get('production_channel_id') == channel
            and kind(previous['spec']) == kind(source['spec']))
        current = previous
    raise ValueError('channel_cadence_lineage_unverified')


@_local_transaction
def publication_slot(source, *, client=None, now=None):
    """Reserve once before queuing the first upload; False means ordinary wait."""
    channel = source.get('spec', {}).get('production_channel_id')
    if channel not in CHANNELS: return True
    if not allows(channel, source['spec'].get('format')): return False
    client = client or _client(); format_kind = kind(source['spec'])
    root = _root(client, source); claim_key = PREFIX + 'publication:' + root
    _, day_key, pending_key = keys(channel, now=now)
    with client.pipeline() as pipe:
        pipe.watch(claim_key, day_key, pending_key)
        stock = _stock_day(pipe, channel, source.get('spec', {}).get('content_plan_item_id'))
        if stock and _now(now).date() < date.fromisoformat(stock['day']):
            pipe.multi(); pipe.sadd(WAITING_KEY, source['task_id']); pipe.execute()
            return False
        raw = pipe.get(claim_key)
        if raw:
            claim = json.loads(raw)
            _require(claim.get('source_task_id') == source['task_id'] and claim.get('channel_id') == channel
                and claim.get('kind') == format_kind and claim.get('root_task_id') == root)
            return True
        used = _values(pipe, day_key); used.update(_values(pipe, pending_key))
        if sum(v == format_kind for v in used.values()) >= daily_limits(channel, LIMITS)[format_kind]:
            # Waiting is not a financial or publication authorization.
            pipe.multi(); pipe.sadd(WAITING_KEY, source['task_id']); pipe.execute()
            return False
        claim = {'version': 1, 'source_task_id': source['task_id'], 'root_task_id': root,
            'channel_id': channel, 'kind': format_kind, 'status': 'reserved',
            'reserved_at': _now(now).isoformat()}
        pipe.multi(); pipe.set(claim_key, json.dumps(claim, sort_keys=True), nx=True)
        pipe.hset(pending_key, root, format_kind); pipe.srem(WAITING_KEY, source['task_id'])
        response = pipe.execute(); _require(response[0] is True)
    return True


@_local_transaction
def publication_completed(source, *, client=None, now=None):
    """Called only after a successful public release, never for a render alone."""
    channel = source.get('spec', {}).get('production_channel_id')
    if channel not in CHANNELS: return
    client = client or _client(); root = _root(client, source)
    claim_key = PREFIX + 'publication:' + root
    _, day_key, pending_key = keys(channel, now=now)
    with client.pipeline() as pipe:
        pipe.watch(claim_key, pending_key, day_key)
        claim = json.loads(pipe.get(claim_key) or '{}')
        _require(claim.get('source_task_id') == source['task_id'] and claim.get('channel_id') == channel
            and claim.get('root_task_id') == root and claim.get('kind') == kind(source['spec']))
        if claim.get('status') == 'public': return
        _require(claim.get('status') == 'reserved' and pipe.hget(pending_key, root) == claim['kind'])
        claim.update(status='public', completed_at=_now(now).isoformat(), day_key=day_key)
        pipe.multi(); pipe.hset(day_key, root, claim['kind']); pipe.hdel(pending_key, root)
        pipe.set(claim_key, json.dumps(claim, sort_keys=True)); pipe.execute()


def maintain(*, client=None):
    """A minute beat retries only unqueued, quality-approved publication waits."""
    client = client or _client()
    from app.publish_tasks import queue_automatic_publish
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    outcomes = {}
    # A lost post-release bookkeeping reply must not freeze tomorrow's quota.
    for channel in CHANNELS:
        pending_key = keys(channel)[2]
        for root in _values(client, pending_key):
            try:
                claim = json.loads(client.get(PREFIX + 'publication:' + root) or '{}')
                task = claim['source_task_id']
                source = json.loads(client.get(jobs.JOB_PREFIX + task) or '{}')
                upload = json.loads(client.get(UPLOAD_PREFIX + task) or '{}')
                if not upload:
                    # The slot is only a local claim, never proof an upload was
                    # sent. Normal publisher admission owns its own durable fence.
                    outcomes[task] = queue_automatic_publish(task).get('status')
                    continue
                attribution = (source.get('result') or {}).get('youtube') or {}
                if (upload.get('status') == 'complete' and upload.get('release_status') == 'public'
                    and upload.get('release_side_effect_possible') is True
                    and upload.get('source_task_id') == task and upload.get('target_channel_id') == channel
                    and attribution.get('release_status') == 'public'
                    and attribution.get('video_id') == upload.get('youtube_video_id')):
                    stamp = datetime.fromisoformat(upload['release_completed_at'])
                    _require(stamp.tzinfo is not None)
                    publication_completed(source, client=client, now=stamp.timestamp())
            except Exception:
                pass  # An unverified outcome keeps protecting all future days.
    for task in sorted(client.smembers(WAITING_KEY))[:12]:
        try:
            if client.get(UPLOAD_PREFIX + task):
                client.srem(WAITING_KEY, task)
                continue
            outcomes[task] = queue_automatic_publish(task).get('status')
        except Exception:
            outcomes[task] = 'waiting'
    return {'status': 'checked', 'sources': outcomes}


def snapshot(channel_id, *, client=None, now=None):
    if channel_id not in CHANNELS: return None
    client = client or _client(); produced, published, pending = keys(channel_id, now=now)
    values = {name: _values(client, key) for name, key in
        (('produced', produced), ('published', published), ('pending', pending))}
    limits = daily_limits(channel_id, LIMITS)
    result = {'timezone': 'Europe/Istanbul', 'date': _now(now).date().isoformat(), 'limits': limits,
        'counts': {name: {k: sum(v == k for v in rows.values()) for k in LIMITS}
                   for name, rows in values.items()}}
    if policy_id(channel_id):
        previous = _values(client, PREFIX + channel_id + ':published:' + result['date'])
        public_and_pending = {**values['published'], **values['pending']}
        attempts = {**values['produced'], **public_and_pending}
        publication_full = sum(v == 'shorts' for v in public_and_pending.values()) >= limits['shorts']
        attempts_full = sum(v == 'shorts' for v in attempts.values()) >= production_limits(channel_id, LIMITS)['shorts']
        result.update(policy_id=policy_id(channel_id),
            production_limits=production_limits(channel_id, LIMITS),
            wait_reason=('publication_limit' if publication_full else 'production_attempt_limit' if attempts_full else None),
            previously_published_today={k: sum(v == k for v in previous.values()) for k in LIMITS})
    return result
