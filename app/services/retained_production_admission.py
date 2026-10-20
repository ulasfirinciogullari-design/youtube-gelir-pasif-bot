"""Permanent single-child reservation for the fixed retained Capital episode.

Only an actual private staged final may reserve a child. The original paid
family and all review history stay unchanged. This module neither dispatches
work nor grants a publisher permission to insert or release a YouTube video.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import threading
from uuid import UUID, uuid4
import weakref

from app.services import retained_final_artifacts as staging
from app.services import retained_cut_evidence as cuts
from app.services import retained_render_consumer as render
from app.services import production_connection_continuity as continuity

PREFIX = 'youtube_studio:retained_delivery:v1:' + continuity.ROOT_ID
MANIFEST_KEY, JOURNAL_KEY, ANCHOR_KEY = (PREFIX + suffix for suffix in (':manifest', ':journal', ':anchor'))
ROOT_KEYS = (MANIFEST_KEY, JOURNAL_KEY, ANCHOR_KEY)
CHILD_PREFIX = 'youtube_studio:retained_delivery_child:v1:'
_ISSUED = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()
_AUTHORITY = {'retained_file_delivery_only': True, 'new_model_calls': 0,
              'new_media_creates': 0, 'new_tts_calls': 0, 'new_cash_micro': 0,
              'publication_authorized': False, 'resume_authorized': False}


class RetainedAdmissionError(RuntimeError):
    """Fixed local reason without source, backend or credential text."""


def _require(value):
    if not value:
        raise RetainedAdmissionError('retained_delivery_admission_unverified')


def _raw(value):
    return cuts._raw(value)


def _hash(value):
    return hashlib.sha256(_raw(value)).hexdigest()


def _uuid(value):
    _require(type(value) is str and str(UUID(value)) == value)
    return value


def _object(raw):
    _require(type(raw) is str)
    value = staging.artifacts._object(raw)
    _require(_raw(value) == raw.encode())
    return value


def _ack(value, expected):
    _require(type(value) is list and len(value) == len(expected)
             and all(type(a) is type(b) and a == b for a, b in zip(value, expected)))


def _read_ack(pipe):
    pipe.multi()
    pipe.ping()
    _ack(pipe.execute(), [True])


class RetainedChildDispatchPermit:
    """One owner-thread dispatch capability, issued only after durable readback."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_delivery_permit_private')

    @property
    def receipt(self):
        state = _permit(self)
        return deepcopy(state['receipt'])

    def __repr__(self):
        return '<RetainedChildDispatchPermit retained-files-only>'


def _permit(value):
    _require(type(value) is RetainedChildDispatchPermit)
    with _LOCK:
        state = _ISSUED.get(value)
    _require(state is not None and state['owner'] == threading.get_ident()
             and state['phase'] == 'reserved')
    return state


def _record(pipe, key):
    pipe.watch(key)
    kind = pipe.type(key)
    _require(kind in ('none', 'string', 'hash'))
    value = pipe.get(key) if kind == 'string' else pipe.hgetall(key) if kind == 'hash' else None
    return {'type': kind, 'value_sha256': _hash(value)}


def _read_claim(pipe):
    """Validate the exact initial claim; completed jobs cannot re-enter it."""
    return _read_projection(pipe)


def _read_projection(pipe, *, completed_child=None, completed_upload=None):
    """Internal projection reader; completion derives both values from receipts."""
    _require((completed_child is None) == (completed_upload is None))
    pipe.watch(*ROOT_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in ROOT_KEYS)))
    manifest, journal = _object(pipe.get(MANIFEST_KEY)), _object(pipe.get(JOURNAL_KEY))
    digest = _hash(manifest)
    _require(manifest['version'] == 1 and type(manifest['version']) is int
             and manifest['kind'] == 'retained_final_delivery'
             and manifest['root_id'] == continuity.ROOT_ID and manifest['leaf_id'] == continuity.LEAF_ID
             and manifest['authority'] == _AUTHORITY
             and journal == {'version': 1, 'phase': 'claimed', 'manifest_sha256': digest,
                 'child_sha256': _hash(manifest['pending_child']),
                 'leaf_sha256': _hash(manifest['claimed_leaf']),
                 'dispatch_sha256': _hash(manifest['dispatch'])}
             and pipe.get(ANCHOR_KEY) == _hash(journal))
    child = _uuid(manifest['child_id'])
    keys = (CHILD_PREFIX + child, continuity._JOB + child,
            continuity._CHILD_CLAIM + child, continuity._EXECUTION + child)
    pipe.watch(*keys)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in keys[:3]))
             and pipe.exists(keys[3]) == 0)
    absent = (continuity._PAID_CAP + child, continuity._DISPATCH + child,
              continuity._REPAIR_CLAIM + child, *(prefix + child for prefix in continuity._ABSENT_PREFIXES))
    if completed_upload is not None:
        upload_key = 'youtube_studio:youtube_upload:v2:' + child
        absent = tuple(key for key in absent if key != upload_key)
        pipe.watch(upload_key)
        _require(type(pipe.pttl(upload_key)) is int and pipe.pttl(upload_key) == -1
                 and _object(pipe.get(upload_key)) == completed_upload)
    pipe.watch(*absent)
    _require(pipe.exists(*absent) == 0)
    _require(_object(pipe.get(keys[0])) == {'version': 1, 'root_id': continuity.ROOT_ID,
                'child_id': child, 'manifest_sha256': digest}
             and _object(pipe.get(keys[1])) == (manifest['pending_child']
                 if completed_child is None else completed_child)
             and pipe.hgetall(keys[2]) == {'source_task_id': continuity.LEAF_ID,
                'token': manifest['dispatch']['token']})
    snapshot = manifest['prepared_final']['source_snapshot']
    permanent = snapshot['permanent_record_keys']
    _require(type(permanent) is list and permanent == sorted(set(permanent))
             and set(permanent) <= snapshot['records'].keys())
    for key, expected in snapshot['records'].items():
        if key == continuity._JOB + continuity.LEAF_ID:
            expected = {'type': 'string', 'value_sha256': _hash(_raw(manifest['claimed_leaf']).decode())}
        elif key == continuity._DISPATCH + continuity.LEAF_ID:
            expected = {'type': 'hash', 'value_sha256': _hash(manifest['dispatch'])}
        _require(_record(pipe, key) == expected)
        if key in permanent:
            ttl = pipe.pttl(key)
            _require(type(ttl) is int and ttl == -1)
    pipe.watch(continuity._CHANNEL_INDEX)
    member = pipe.sismember(continuity._CHANNEL_INDEX, continuity.CHANNEL_ID)
    _require(type(member) in (bool, int) and member == 1)
    return manifest, journal


def reserve_retained_child(client, prepared, *, clock=None):
    """Atomically preserve one permanent child; no queue or provider operation."""
    try:
        state = staging._checked(prepared)
        _require(state['client'] is client)
        with staging._LOCK:
            _require(state['phase'] == 'prepared')
            state['phase'] = 'claim_attempted'
        prepared_record = json.loads(state['record'])
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        _require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None)
        now = now.astimezone(timezone.utc)
        stamp = now.isoformat()
        child, token = str(uuid4()), secrets.token_urlsafe(32)
        head = os.environ.get('RAILWAY_GIT_COMMIT_SHA', '')
        _require(re.fullmatch('[0-9a-f]{40}', head) is not None)
        child_keys = (CHILD_PREFIX + child, continuity._JOB + child,
            continuity._CHILD_CLAIM + child, continuity._EXECUTION + child,
            continuity._PAID_CAP + child, continuity._DISPATCH + child,
            *(prefix + child for prefix in continuity._ABSENT_PREFIXES))
        with client.pipeline() as pipe:
            pipe.watch(*ROOT_KEYS, *child_keys)
            _require(pipe.exists(*ROOT_KEYS, *child_keys) == 0)
            snapshot = staging._snapshot(pipe, state)
            _require(snapshot == prepared_record['source_snapshot'])
            source = snapshot['source']
            metadata = json.loads(state['metadata'])
            _require(metadata['source_snapshot'] == snapshot
                     and metadata['local_render'] == state['rendered'].record
                     and metadata['local_render']['assembly_build'] == render._assembly_build())
            for name, pointer in prepared_record['artifacts'].items():
                data = cuts._read_private(state['s3'], state['bucket'], pointer)
                if name == 'metadata':
                    _require(data == state['metadata'])
            for path, identity in ((metadata['local_render']['path'], metadata['local_render']['final']),
                    (metadata['local_render']['render_metrics']['srt'], metadata['local_render']['captions'])):
                render._file(path, identity)
            leaf = _object(pipe.get(continuity._JOB + continuity.LEAF_ID))
            _require(leaf['retry_claimed'] is False and leaf.get('retry_child_task_id') is None)
            spec = {**deepcopy(leaf['spec']), 'workflow': 'retained_final',
                'repair_source_task_id': continuity.LEAF_ID,
                'current_delivery_connection_id': source['current_connection_id'],
                'delivery_authorization_epoch': source['authorization_epoch'],
                'retained_delivery_root_id': continuity.ROOT_ID}
            _require(spec['production_connection_id'] == source['old_connection_id'])
            pending = {'task_id': child, 'kind': 'render', 'parent_id': continuity.LEAF_ID,
                'spec': spec, 'state': 'PENDING', 'stage': 'retained_delivery_queued', 'progress': 0,
                'message': 'Kontrol edilen bölümün yayını hazırlanıyor.',
                'created_ts': now.timestamp(), 'created_at': stamp, 'result': None, 'error': None}
            claimed = {**leaf, 'retry_claimed': True, 'repair_claimed': False, 'repair_available': False,
                'retry_child_task_id': child, 'retry_dispatch_state': 'reserved', 'updated_at': stamp}
            dispatch = {'token': token, 'child_task_id': child, 'mode': 'retained_final',
                        'state': 'reserved', 'created_at': stamp}
            manifest = {'version': 1, 'kind': 'retained_final_delivery', 'root_id': continuity.ROOT_ID,
                'leaf_id': continuity.LEAF_ID, 'child_id': child, 'created_at': stamp,
                'source_head_sha': head, 'authority': _AUTHORITY,
                'prepared_final': prepared_record, 'pending_child': pending,
                'claimed_leaf': claimed, 'dispatch': dispatch}
            digest = _hash(manifest)
            journal = {'version': 1, 'phase': 'claimed', 'manifest_sha256': digest,
                'child_sha256': _hash(pending), 'leaf_sha256': _hash(claimed), 'dispatch_sha256': _hash(dispatch)}
            _require(staging._snapshot(pipe, state) == snapshot)
            pipe.multi()
            pipe.set(MANIFEST_KEY, _raw(manifest).decode(), nx=True)
            pipe.set(JOURNAL_KEY, _raw(journal).decode(), nx=True)
            pipe.set(ANCHOR_KEY, _hash(journal), nx=True)
            pipe.set(CHILD_PREFIX + child, _raw({'version': 1, 'root_id': continuity.ROOT_ID,
                'child_id': child, 'manifest_sha256': digest}).decode(), nx=True)
            pipe.hset(continuity._DISPATCH + continuity.LEAF_ID, mapping=dispatch)
            pipe.hset(continuity._CHILD_CLAIM + child, mapping={'source_task_id': continuity.LEAF_ID, 'token': token})
            pipe.set(continuity._JOB + child, _raw(pending).decode(), nx=True)
            pipe.set(continuity._JOB + continuity.LEAF_ID, _raw(claimed).decode())
            _ack(pipe.execute(), [True, True, True, True, 5, 2, True, True])
        with client.pipeline() as pipe:
            actual, current = _read_claim(pipe)
            _require(actual == manifest and current == journal)
            _read_ack(pipe)
        permit = object.__new__(RetainedChildDispatchPermit)
        with _LOCK:
            _ISSUED[permit] = {'client': client, 'owner': threading.get_ident(), 'phase': 'reserved',
                'receipt': {'version': 1, 'root_id': continuity.ROOT_ID, 'leaf_id': continuity.LEAF_ID,
                    'child_id': child, 'manifest_sha256': digest, 'dispatch_acknowledged': False,
                    'publication_authorized': False, 'resume_authorized': False}}
        state['phase'] = 'claimed'
        return permit
    except RetainedAdmissionError:
        raise
    except Exception:
        raise RetainedAdmissionError('retained_delivery_admission_unverified') from None
