"""One permanent queue attempt and one execution for an admitted retained file.

This route never regenerates media or enters the ordinary paid pipeline. The
claim stays unchanged; queue and execution receipts occupy separate permanent
records. Lost acknowledgements cannot authorize a second submission/execution.
Publication and final job transitions are separate from these receipts.
"""
from copy import deepcopy
from datetime import datetime, timezone
import os
import secrets
import threading
import weakref

from app.services import retained_production_admission as admission

DISPATCH_KEY = admission.PREFIX + ':queue_intent'
DISPATCH_ACK_KEY = admission.PREFIX + ':queue_ack'
EXECUTION_KEY = admission.PREFIX + ':execution'
_KEYS = (DISPATCH_KEY, DISPATCH_ACK_KEY, EXECUTION_KEY)
TASK_NAME = 'app.production_tasks.finalize_retained_child'
_EXECUTING = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()


class RetainedDispatchError(RuntimeError):
    """An occupied or unverifiable attempt; never a retry instruction."""


def _require(value):
    if not value:
        raise RetainedDispatchError('retained_delivery_dispatch_unverified')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _stored(pipe, key):
    pipe.watch(key)
    ttl = pipe.pttl(key)
    _require(type(ttl) is int and ttl == -1)
    return admission._object(pipe.get(key))


def _intent(manifest):
    return {'version': 1, 'kind': 'retained_delivery_queue_intent',
        'root_id': manifest['root_id'], 'child_id': manifest['child_id'],
        'manifest_sha256': admission._hash(manifest), 'task_name': TASK_NAME,
        'source_head_sha': manifest['source_head_sha'], 'automatic_retry_permitted': False}


def dispatch_retained_child(permit, sender):
    """Reserve before calling the broker exactly once with a fixed task ID."""
    try:
        state = admission._permit(permit)
        _require(callable(sender))
        with admission._LOCK:
            _require(state['phase'] == 'reserved')
            state['phase'] = 'queue_attempted'
        client = state['client']
        with client.pipeline() as pipe:
            manifest, _ = admission._read_claim(pipe)
            _require(admission._hash(manifest) == state['receipt']['manifest_sha256'])
            _require(os.environ.get('RAILWAY_GIT_COMMIT_SHA') == manifest['source_head_sha'])
            pipe.watch(*_KEYS)
            _require(pipe.exists(*_KEYS) == 0)
            intent = _intent(manifest)
            pipe.multi()
            pipe.set(DISPATCH_KEY, admission._raw(intent).decode(), nx=True)
            admission._ack(pipe.execute(), [True])
        with client.pipeline() as pipe:
            current, _ = admission._read_claim(pipe)
            _require(current == manifest and _stored(pipe, DISPATCH_KEY) == intent)
            admission._read_ack(pipe)
        reply = sender(kwargs={'manifest_sha256': intent['manifest_sha256']}, task_id=manifest['child_id'], retry=False)
        _require(getattr(reply, 'id', None) == manifest['child_id'])
        # A worker may already be executing. This receipt acknowledges only the
        # broker result and never rewrites the child, claim or execution phase.
        receipt = {'version': 1, 'kind': 'retained_delivery_queue_ack',
            'intent_sha256': admission._hash(intent), 'child_id': manifest['child_id'],
            'acknowledged_at': _now(), 'automatic_retry_permitted': False}
        with client.pipeline() as pipe:
            _require(_stored(pipe, admission.MANIFEST_KEY) == manifest
                     and _stored(pipe, DISPATCH_KEY) == intent)
            pipe.watch(DISPATCH_ACK_KEY)
            _require(pipe.exists(DISPATCH_ACK_KEY) == 0)
            pipe.multi()
            pipe.set(DISPATCH_ACK_KEY, admission._raw(receipt).decode(), nx=True)
            admission._ack(pipe.execute(), [True])
        with client.pipeline() as pipe:
            _require(_stored(pipe, DISPATCH_ACK_KEY) == receipt)
            admission._read_ack(pipe)
        state['phase'] = 'queued'
        return deepcopy(receipt)
    except RetainedDispatchError:
        raise
    except Exception:
        raise RetainedDispatchError('retained_delivery_dispatch_unverified') from None


class RetainedDeliveryExecution:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_delivery_execution_private')

    def __repr__(self):
        return '<RetainedDeliveryExecution original-files-only>'


def _execution(value):
    _require(type(value) is RetainedDeliveryExecution)
    with _LOCK:
        state = _EXECUTING.get(value)
    _require(state is not None and state['owner'] == threading.get_ident())
    return state


def acquire_retained_execution(client, task_id, manifest_sha256):
    """An exact admitted broker message may claim one durable execution."""
    try:
        admission._uuid(task_id)
        with client.pipeline() as pipe:
            manifest, _ = admission._read_claim(pipe)
            _require(task_id == manifest['child_id'] and manifest_sha256 == admission._hash(manifest)
                     and os.environ.get('RAILWAY_GIT_COMMIT_SHA') == manifest['source_head_sha'])
            intent = _stored(pipe, DISPATCH_KEY)
            _require(intent == _intent(manifest))
            pipe.watch(EXECUTION_KEY)
            _require(pipe.exists(EXECUTION_KEY) == 0)
            record = {'version': 1, 'kind': 'retained_delivery_execution',
                'child_id': task_id, 'manifest_sha256': manifest_sha256,
                'queue_intent_sha256': admission._hash(intent), 'owner_token': secrets.token_urlsafe(32),
                'started_at': _now(), 'automatic_retry_permitted': False}
            pipe.multi()
            pipe.set(EXECUTION_KEY, admission._raw(record).decode(), nx=True)
            admission._ack(pipe.execute(), [True])
        with client.pipeline() as pipe:
            current, _ = admission._read_claim(pipe)
            _require(current == manifest and _stored(pipe, DISPATCH_KEY) == intent
                     and _stored(pipe, EXECUTION_KEY) == record)
            admission._read_ack(pipe)
        execution = object.__new__(RetainedDeliveryExecution)
        with _LOCK:
            _EXECUTING[execution] = {'client': client, 'owner': threading.get_ident(),
                'manifest': manifest, 'intent': intent, 'execution': record}
        return execution
    except RetainedDispatchError:
        raise
    except Exception:
        raise RetainedDispatchError('retained_delivery_dispatch_unverified') from None


def verify_execution(value):
    """Read current source and owner records; this grants no publication."""
    try:
        state = _execution(value)
        with state['client'].pipeline() as pipe:
            manifest, _ = admission._read_claim(pipe)
            _require(manifest == state['manifest'] and _stored(pipe, DISPATCH_KEY) == state['intent']
                     and _stored(pipe, EXECUTION_KEY) == state['execution']
                     and os.environ.get('RAILWAY_GIT_COMMIT_SHA') == manifest['source_head_sha'])
            admission._read_ack(pipe)
        return deepcopy(manifest)
    except RetainedDispatchError:
        raise
    except Exception:
        raise RetainedDispatchError('retained_delivery_dispatch_unverified') from None
