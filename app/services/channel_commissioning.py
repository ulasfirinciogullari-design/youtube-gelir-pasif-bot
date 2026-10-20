"""Explicit additional-channel grants; original commissioning receipts never change."""
from datetime import datetime, timezone
import hashlib
import json
import re

PREFIX = 'youtube_studio:channel_commissioning:v1:'


def _require(value):
    if not value:
        raise ValueError('channel_commissioning_unverified')


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _validate(value):
    _require(type(value) is dict and set(value) == {
        'version', 'channel_id', 'connection_id', 'authorized_at', 'owner_evidence_sha256'})
    _require(type(value['version']) is int and value['version'] == 1
        and type(value['channel_id']) is str and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value['channel_id'])
        and type(value['connection_id']) is str and 8 <= len(value['connection_id']) <= 128
        and re.fullmatch('[0-9a-f]{64}', value['owner_evidence_sha256']))
    instant = datetime.fromisoformat(value['authorized_at'])
    _require(instant.tzinfo is not None and instant.utcoffset().total_seconds() == 0
             and instant <= datetime.now(timezone.utc))


def authority(reader, channel_id, *, active=True):
    key = PREFIX + channel_id
    keys = (key, key + ':anchor', key + ':active')
    reader.watch(*keys)
    raw, anchor, pointer = (reader.get(k) for k in keys)
    if raw is None and anchor is None and pointer is None:
        return None
    _require(type(raw) is str and len(raw) <= 2048
        and anchor == hashlib.sha256(raw.encode()).hexdigest()
        and reader.pttl(key) == reader.pttl(keys[1]) == -1)
    value = json.loads(raw); _validate(value)
    _require(value['channel_id'] == channel_id
        and (pointer is None or pointer == anchor and reader.pttl(keys[2]) == -1))
    if active:
        if pointer is None:
            return None
        from app.services.youtube_auth import CHANNEL_PREFIX
        channel_key = CHANNEL_PREFIX + channel_id
        reader.watch(channel_key)
        channel = json.loads(reader.get(channel_key) or '{}')
        _require(channel.get('id') == channel_id
            and channel.get('connection_id') == value['connection_id']
            and channel.get('requires_reconnect') is not True)
    return anchor


def initialize(client, value):
    """Private owner operation; never called by a scheduler or a web request."""
    _validate(value)
    raw = _raw(value); digest = hashlib.sha256(raw.encode()).hexdigest()
    key = PREFIX + value['channel_id']
    with client.pipeline() as pipe:
        pipe.watch(key, key + ':anchor', key + ':active')
        previous = pipe.get(key)
        if previous is not None:
            _require(previous == raw and authority(pipe, value['channel_id']) == digest)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return False
        _require(not pipe.exists(key + ':anchor', key + ':active'))
        from app.services.youtube_auth import CHANNEL_PREFIX
        channel_key = CHANNEL_PREFIX + value['channel_id']; pipe.watch(channel_key)
        channel = json.loads(pipe.get(channel_key) or '{}')
        _require(channel.get('id') == value['channel_id']
            and channel.get('connection_id') == value['connection_id']
            and channel.get('requires_reconnect') is not True)
        pipe.multi()
        for k, v in ((key, raw), (key + ':anchor', digest), (key + ':active', digest)):
            pipe.set(k, v, nx=True)
        _require(pipe.execute() == [True, True, True])
    return True
