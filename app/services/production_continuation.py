"""Owner-authorized commissioning, separate from final budget and cadence.

The durable authorization survives deactivation so past attempts can still be
verified. Only the active pointer enables new work. Provider funding, one-shot
execution, editorial checks and publication evidence remain mandatory.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

PREFIX = 'youtube_studio:continuous_commissioning:v1:'
AUTHORIZATION_KEY = PREFIX + 'authorization'
ANCHOR_KEY = PREFIX + 'anchor'
ACTIVE_KEY = PREFIX + 'active'
# A storage/transaction bound, matching the included router's maximum daily
# request capacity. This is not an owner-selected publication or cash budget.
MAX_DAILY_IDENTITIES = 240


def _require(value):
    if not value:
        raise ValueError('continuous_commissioning_unverified')


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _validate(value):
    _require(type(value) is dict and set(value) == {
        'version', 'kind', 'allowed_channels', 'authorized_at', 'owner_evidence_sha256'})
    _require(type(value['version']) is int and value['version'] == 1
        and value['kind'] == 'continuous_commissioning'
        and type(value['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', value['owner_evidence_sha256']))
    channels = value['allowed_channels']
    _require(type(channels) is list and 1 <= len(channels) <= 8
        and all(type(v) is str and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', v) for v in channels)
        and len(set(channels)) == len(channels))
    instant = datetime.fromisoformat(value['authorized_at'])
    _require(instant.tzinfo is not None and instant.utcoffset().total_seconds() == 0
        and instant <= datetime.now(timezone.utc))


def authority(reader, channel_id, *, active=True):
    """Read under WATCH or an existing promotion snapshot; never initialize."""
    reader.watch(AUTHORIZATION_KEY, ANCHOR_KEY, ACTIVE_KEY)
    raw, anchor, pointer = (reader.get(key) for key in (AUTHORIZATION_KEY, ANCHOR_KEY, ACTIVE_KEY))
    if raw is None and anchor is None:
        _require(pointer is None)
        return None
    _require(type(raw) is str and len(raw) <= 4096 and anchor == _sha(raw)
        and reader.pttl(AUTHORIZATION_KEY) == reader.pttl(ANCHOR_KEY) == -1)
    value = json.loads(raw)
    _validate(value)
    _require(pointer is None or pointer == anchor and reader.pttl(ACTIVE_KEY) == -1)
    if channel_id not in value['allowed_channels'] or active and pointer is None:
        return None
    return anchor


def initialize(client, value):
    """Explicit owner operation only. No scheduler or web GET calls this."""
    _validate(value)
    raw, digest = _raw(value), _sha(_raw(value))
    with client.pipeline() as pipe:
        pipe.watch(AUTHORIZATION_KEY, ANCHOR_KEY, ACTIVE_KEY)
        prior = pipe.get(AUTHORIZATION_KEY)
        if prior is not None:
            _require(prior == raw and authority(pipe, value['allowed_channels'][0]) == digest)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return False
        _require(not pipe.exists(ANCHOR_KEY, ACTIVE_KEY))
        pipe.multi()
        for key, content in ((AUTHORIZATION_KEY, raw), (ANCHOR_KEY, digest), (ACTIVE_KEY, digest)):
            pipe.set(key, content, nx=True)
        _require(pipe.execute() == [True, True, True])
    return True


def validate_extended_hold_day(reader, roots, channel_id, legacy_limit, hold_prefix):
    """A longer day never invalidates its original immutable hold receipts."""
    _require(type(roots) is list and len(roots) <= MAX_DAILY_IDENTITIES
        and len(set(roots)) == len(roots))
    if len(roots) <= legacy_limit:
        return
    proof = authority(reader, channel_id, active=False)
    _require(proof is not None)
    for root in roots[legacy_limit:]:
        key = hold_prefix + root
        reader.watch(key)
        record = json.loads(reader.get(key))
        _require(reader.pttl(key) == -1 and record.get('root_task_id') == root
            and record.get('channel_id') == channel_id
            and record.get('continuation_authority_sha256') == proof)
