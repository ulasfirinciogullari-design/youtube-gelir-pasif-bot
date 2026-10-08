"""Dedicated retained-file publication with durable, ordered effect markers.

Every effect starts only after a fresh watched source/plan check. Every observed
response is preserved separately, even if authority changes during the request.
Unknown uploads/releases cannot be replayed. This module does not complete the
child job, change the scheduler or authorize another production.
"""
from copy import deepcopy
from pathlib import Path
import re
import threading
import weakref

from app.services import retained_publication_plan as publication
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from app.services import retained_render_consumer as render
from app.services import youtube, youtube_auth

_PHASES = ('upload', 'private_status', 'captions', 'thumbnail', 'release', 'public_status')
PREFIX = admission.PREFIX + ':publication:'
PUBLIC_RECEIPT_KEY = PREFIX + 'public_receipt'
_ATTEMPTED = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()


class RetainedPublicationError(RuntimeError):
    """An unverified or occupied step; never a replay instruction."""


def _require(value):
    if not value:
        raise RetainedPublicationError('retained_publication_unverified_no_retry')


def _id(value):
    _require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{6,128}', value) is not None)
    return value


def _caption_id(value):
    """Caption IDs are opaque API strings, independent of video ID syntax.

    Preserve padding, punctuation and longer IDs exactly. The bounds here limit
    our receipt size; they do not reinterpret the provider's identifier.
    https://developers.google.com/youtube/v3/docs/captions#id
    """
    _require(type(value) is str and 0 < len(value) <= 4096 and not value.isspace()
             and all(ord(char) >= 32 and ord(char) != 127 for char in value))
    try:
        encoded = value.encode('utf-8')
    except UnicodeError:
        raise RetainedPublicationError('retained_publication_unverified_no_retry') from None
    _require(len(encoded) <= 4096)
    return value


def _key(phase, suffix):
    _require(phase in _PHASES and suffix in ('intent', 'result'))
    return PREFIX + phase + ':' + suffix


def _authority(pipe, state):
    """Same WATCH transaction as the next permanent side-effect intent."""
    current, _ = admission._read_claim(pipe)
    running = state['running']
    record = state['record']
    _require(current == running['manifest']
        and delivery._stored(pipe, publication.PLAN_KEY) == record
        and delivery._stored(pipe, delivery.EXECUTION_KEY) == running['execution']
        and delivery._stored(pipe, delivery.DISPATCH_KEY) == running['intent'])
    keys = record['series_keys']
    pipe.watch(*keys)
    _require(pipe.mget(keys[:2]) == ['5', '5'] and pipe.exists(*keys[2:]) == 0
             and all(type(t) is int and t == -1 for t in (pipe.pttl(k) for k in keys[:2])))
    for row in state['files'].values():
        _require(render._file(row['path'], row['identity']) == row)


def _previous(pipe, state, phase):
    position = _PHASES.index(phase)
    _require(tuple(state['results']) == _PHASES[:position])
    for name in _PHASES[:position]:
        _require(delivery._stored(pipe, _key(name, 'intent')) == state['intents'][name]
                 and delivery._stored(pipe, _key(name, 'result')) == state['results'][name])


def _start(state, phase):
    with state['running']['client'].pipeline() as pipe:
        _authority(pipe, state)
        _previous(pipe, state, phase)
        keys = (_key(phase, 'intent'), _key(phase, 'result'))
        pipe.watch(*keys, PUBLIC_RECEIPT_KEY)
        _require(pipe.exists(*keys, PUBLIC_RECEIPT_KEY) == 0)
        intent = {'version': 1, 'phase': phase, 'child_id': state['running']['manifest']['child_id'],
            'plan_sha256': admission._hash(state['record']),
            'execution_sha256': admission._hash(state['running']['execution']),
            'previous_results_sha256': admission._hash(state['results']),
            'created_at': delivery._now(), 'automatic_retry_permitted': False}
        pipe.multi()
        pipe.set(keys[0], admission._raw(intent).decode(), nx=True)
        admission._ack(pipe.execute(), [True])
    state['intents'][phase] = intent
    with state['running']['client'].pipeline() as pipe:
        _authority(pipe, state)
        _previous(pipe, state, phase)
        _require(delivery._stored(pipe, keys[0]) == intent)
        admission._read_ack(pipe)


def _observe(state, phase, observed):
    """Retain a bounded known outcome without authorizing another action."""
    intent = state['intents'][phase]
    result = {'version': 1, 'phase': phase, 'intent_sha256': admission._hash(intent),
              'observed': deepcopy(observed), 'observed_at': delivery._now()}
    encoded = admission._raw(result)
    _require(len(encoded) <= 4096)
    # Preserve a private local copy before the remote acknowledgement. A lost
    # Redis reply cannot cause another upload; this file is diagnostic only.
    identity = {'sha256': render.cuts._sha(encoded), 'size': len(encoded)}
    render._write(state['records_dir'], phase + '.json', encoded, identity)
    render._sync_directory(state['records_dir'])
    with state['running']['client'].pipeline() as pipe:
        _require(delivery._stored(pipe, admission.MANIFEST_KEY) == state['running']['manifest']
                 and delivery._stored(pipe, delivery.EXECUTION_KEY) == state['running']['execution']
                 and delivery._stored(pipe, publication.PLAN_KEY) == state['record']
                 and delivery._stored(pipe, _key(phase, 'intent')) == intent)
        _previous(pipe, state, phase)
        key = _key(phase, 'result')
        pipe.watch(key)
        _require(pipe.exists(key) == 0)
        pipe.multi()
        pipe.set(key, encoded.decode(), nx=True)
        admission._ack(pipe.execute(), [True])
    with state['running']['client'].pipeline() as pipe:
        _require(delivery._stored(pipe, key) == result)
        admission._read_ack(pipe)
    state['results'][phase] = result


def _status(credentials, video_id, privacy):
    value = youtube.get_video_status_with_credentials(credentials, video_id)
    _require(type(value) is dict and value.get('privacyStatus') == privacy
             and value.get('containsSyntheticMedia') is True
             and value.get('uploadStatus') in ('uploaded', 'processed'))
    return {'video_id': video_id, 'privacy_status': privacy, 'contains_synthetic_media': True,
            'upload_status': value['uploadStatus']}


def publish_retained_final(prepared):
    """One exact private insert → captions/thumbnail → verified public release."""
    try:
        checked = publication._checked(prepared)
        with _LOCK:
            _require(prepared not in _ATTEMPTED)
            _ATTEMPTED[prepared] = True
        record = publication.verify_retained_publication(prepared)
        running = delivery._execution(checked['execution'])
        directory = Path(checked['directory']).parent / 'retained-publication-records'
        directory.mkdir(mode=0o700, exist_ok=False)
        state = {'running': running, 'record': record, 'files': checked['files'],
                 'records_dir': directory, 'intents': {}, 'results': {}}
        plan = record['plan']
        credentials = youtube_auth.load_credentials(plan['target_channel_id'],
            expected_connection_id=record['connection_id'], refresh=True)
        _require(credentials is not None)
        # This existing read verifies Google's current channel identity without
        # rewriting the frozen OAuth record merely to refresh display statistics.
        channel = youtube_auth._channel_from_credentials(credentials)
        _require(channel['id'] == plan['target_channel_id'])
        _start(state, 'upload')
        uploaded = youtube.upload_video_with_credentials(credentials, state['files']['video']['path'],
            plan['title'], plan['description'], privacy_status='private', tags=plan['tags'],
            category_id=plan['category_id'], default_language=plan['default_language'], contains_synthetic_media=True)
        _require(type(uploaded) is dict)
        video_id = _id(uploaded.get('id'))
        _observe(state, 'upload', {'video_id': video_id})
        _start(state, 'private_status')
        _observe(state, 'private_status', _status(credentials, video_id, 'private'))
        _start(state, 'captions')
        caption = youtube.upload_caption_with_credentials(credentials, video_id,
            state['files']['captions']['path'], plan['default_language'])
        _require(type(caption) is dict and type(caption.get('snippet')) is dict
                 and caption['snippet'].get('videoId') == video_id
                 and caption['snippet'].get('language') == plan['default_language'])
        _observe(state, 'captions', {'video_id': video_id, 'caption_id': _caption_id(caption.get('id')),
                                   'language': plan['default_language']})
        _start(state, 'thumbnail')
        if plan['require_thumbnail']:
            thumbnail = youtube.upload_thumbnail_with_credentials(credentials, video_id,
                state['files']['thumbnail']['path'])
            _require(type(thumbnail) is dict and type(thumbnail.get('items')) is list and thumbnail['items'])
            thumbnail_result = {'video_id': video_id, 'status': 'uploaded'}
        else:
            thumbnail_result = {'video_id': video_id, 'status': 'not_required'}
        _observe(state, 'thumbnail', thumbnail_result)
        _start(state, 'release')
        release = youtube.set_video_release_with_credentials(credentials, video_id, 'public', contains_synthetic_media=True)
        _require(type(release) is dict and release.get('id') == video_id
                 and release.get('status', {}).get('privacyStatus') == 'public'
                 and release['status'].get('containsSyntheticMedia') is True)
        _observe(state, 'release', {'video_id': video_id, 'privacy_status': 'public', 'contains_synthetic_media': True})
        _start(state, 'public_status')
        _observe(state, 'public_status', _status(credentials, video_id, 'public'))
        receipt = {'version': 1, 'kind': 'retained_public_delivery',
            'child_id': running['manifest']['child_id'], 'original_task_id': admission.continuity.ROOT_ID,
            'video_id': video_id, 'target_channel_id': plan['target_channel_id'],
            'connection_id': record['connection_id'], 'authorization_epoch': record['authorization_epoch'],
            'plan_sha256': admission._hash(record), 'execution_sha256': admission._hash(running['execution']),
            'manifest_sha256': admission._hash(running['manifest']),
            'result_hashes': {k: admission._hash(v) for k, v in state['results'].items()},
            'privacy_status': 'public', 'contains_synthetic_media': True, 'captions_uploaded': True,
            'thumbnail_status': thumbnail_result['status'], 'completed_at': delivery._now(),
            'resume_authorized': False, 'next_production_authorized': False, 'automatic_retry_permitted': False}
        with running['client'].pipeline() as pipe:
            _authority(pipe, state)
            for name in _PHASES:
                _require(delivery._stored(pipe, _key(name, 'intent')) == state['intents'][name]
                         and delivery._stored(pipe, _key(name, 'result')) == state['results'][name])
            pipe.watch(PUBLIC_RECEIPT_KEY)
            _require(pipe.exists(PUBLIC_RECEIPT_KEY) == 0)
            pipe.multi()
            pipe.set(PUBLIC_RECEIPT_KEY, admission._raw(receipt).decode(), nx=True)
            admission._ack(pipe.execute(), [True])
        with running['client'].pipeline() as pipe:
            _require(delivery._stored(pipe, PUBLIC_RECEIPT_KEY) == receipt)
            admission._read_ack(pipe)
        return deepcopy(receipt)
    except RetainedPublicationError:
        raise
    except Exception:
        raise RetainedPublicationError('retained_publication_unverified_no_retry') from None
