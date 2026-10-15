"""Read a stable server-owned lineage for private candidate preservation.

This snapshot is not a recovery claim, spending permit or proof of current
Google authorization. It never initializes or modifies a ledger or job.
"""
from __future__ import annotations

import hashlib
import json
import re

import redis

from app.services import studio_state


_TASK = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
_CHANNEL = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
_CONNECTION = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
_CHANNEL_PREFIX = 'youtube_studio:oauth:channel:v3:'
_CHANNEL_INDEX = 'youtube_studio:oauth:channels:v3'
MAX_LINEAGE_JOBS = 64
MAX_RECORD_BYTES = 2 * 1024 * 1024


class SelectedVisualContextError(RuntimeError):
    pass


def _require(condition):
    if not condition:
        raise SelectedVisualContextError('selected_visual_context_unavailable')


def _object(raw):
    _require(type(raw) in (str, bytes) and 0 < len(raw) <= MAX_RECORD_BYTES)
    value = json.loads(raw)
    _require(type(value) is dict)
    return value


def _identity(job):
    spec = job.get('spec')
    _require(job.get('kind') == 'render' and type(spec) is dict)
    _require(spec.get('mode') == 'production' and spec.get('format') == 'shorts'
             and type(spec.get('duration_minutes')) in (int, float)
             and spec['duration_minutes'] == 0.5)
    channel = spec.get('production_channel_id') or spec.get('youtube_channel_id')
    connection = spec.get('production_connection_id') or spec.get('youtube_connection_id')
    _require(type(channel) is str and _CHANNEL.fullmatch(channel)
             and type(connection) is str and _CONNECTION.fullmatch(connection))
    # Explicitly conflicting aliases cannot silently select another account.
    for field, expected in (('production_channel_id', channel), ('youtube_channel_id', channel),
                            ('production_connection_id', connection), ('youtube_connection_id', connection)):
        _require(spec.get(field) in (None, '', expected))
    return channel, connection


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def resolve_selected_visual_binding(task_id: str, *, client=None) -> dict:
    """Bounded WATCH/EXEC/PING reads; a changed ancestry never yields a snapshot.

    The caller must still revalidate this binding and all required quality,
    spending and single-use claim evidence before any future paid repair.
    """
    try:
        _require(type(task_id) is str and _TASK.fullmatch(task_id))
        if client is None:
            client = studio_state._client()
        for _ in range(3):
            try:
                with client.pipeline() as pipe:
                    current, seen, identity, nodes = task_id, set(), None, []
                    while current is not None:
                        _require(type(current) is str and _TASK.fullmatch(current)
                                 and current not in seen and len(seen) < MAX_LINEAGE_JOBS)
                        seen.add(current)
                        key = studio_state.JOB_PREFIX + current
                        pipe.watch(key)
                        job = _object(pipe.get(key))
                        _require(job.get('task_id') == current)
                        found = _identity(job)
                        _require(identity is None or found == identity)
                        # Full server-authored spec includes episode/profile
                        # identity; progress/error/checkpoint fields do not.
                        nodes.append({'task_id': current, 'parent_id': job.get('parent_id'),
                                      'spec_sha256': _digest(job['spec'])})
                        identity, root = found, current
                        current = job.get('parent_id')
                    channel, connection = identity
                    pipe.watch(_CHANNEL_PREFIX + channel, _CHANNEL_INDEX)
                    owned = _object(pipe.get(_CHANNEL_PREFIX + channel))
                    _require(owned.get('id') == channel and owned.get('connection_id') == connection
                             and pipe.sismember(_CHANNEL_INDEX, channel))
                    pipe.multi()
                    pipe.ping()
                    receipt = pipe.execute()
                    _require(type(receipt) is list and len(receipt) == 1 and receipt[0] is True)
                    return {'source_task_id': task_id, 'lineage_id': root,
                            'channel_id': channel, 'connection_id': connection,
                            'ancestry_sha256': _digest({'version': 1, 'nodes': nodes})}
            except redis.exceptions.WatchError:
                continue
        raise SelectedVisualContextError('selected_visual_context_unavailable')
    except Exception:
        raise SelectedVisualContextError('selected_visual_context_unavailable') from None
