"""Retain exact unapproved stock pools before subscription-funded reviews.

Only private, content-addressed storage is used. No model request, spending
permit or quality approval occurs here. A missing or damaged saved asset must
not silently become a different clip behind a cached review.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import logging
import traceback
import sys
from functools import wraps

from redis.exceptions import WatchError

from app.services.production_spend import SpendBlocked, LEDGER_KEY

PREFIX = 'youtube_studio:included_stock_pool:v1:'
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_MANIFEST = 512 * 1024
PHASES = frozenset({'initial', 'before_generation', 'budget_rescue'})


def _uncommitted_conflict(error, caller_exception):
    # Python attaches the caller's active exception to a new exception raised
    # during an editorial repair. That inherited context is not a Redis
    # transport failure. A newly chained connection/ACK error remains unsafe.
    return (type(error) is WatchError and str(error) == 'Watched variable changed.'
        and error.__cause__ is None
        and (error.__context__ is None or error.__context__ is caller_exception))


def _local_transaction(operation):
    """Repeat only Redis's definite uncommitted CAS conflict, never transport."""
    @wraps(operation)
    def run(*args, **kwargs):
        caller_exception = sys.exc_info()[1]
        for attempt in range(8):
            try:
                return operation(*args, **kwargs)
            except WatchError as error:
                if not _uncommitted_conflict(error, caller_exception) or attempt == 7:
                    raise
    return run
SPEC_FIELDS = frozenset({'pexels_id', 'start_fraction', 'source_duration', 'source_type', 'stock_provider'})


def _require(value):
    if not value:
        raise SpendBlocked('included_stock_pool_unverified')


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _ack(pipe):
    result = pipe.execute()
    _require(type(result) is list and len(result) == 1 and result[0] is True)


def _digest(path):
    h = hashlib.sha256()
    total = 0
    with path.open('rb') as incoming:
        while chunk := incoming.read(1024 * 1024):
            total += len(chunk)
            _require(total <= MAX_FILE)
            h.update(chunk)
    _require(total >= 1024)
    return h.hexdigest(), total


def _spec(value):
    _require(type(value) is dict and set(value) == SPEC_FIELDS
        and value['source_type'] == 'stock' and value['stock_provider'] == 'pexels'
        and type(value['pexels_id']) is int and value['pexels_id'] > 0)
    for name, limit in (('start_fraction', .95), ('source_duration', 3600)):
        item = value[name]
        _require(type(item) in (int, float) and math.isfinite(item) and 0 <= item <= limit)
    _require(value['source_duration'] > 0)
    return deepcopy(value)


def _authority(pipe, context):
    from app.services import production_spend_runtime as runtime
    key = runtime._CHANNEL_PREFIX + context['channel_id']
    pipe.watch(key, runtime._CHANNEL_INDEX, LEDGER_KEY)
    channel = runtime._object(pipe.get(key))
    _require(channel.get('id') == context['channel_id']
        and channel.get('connection_id') == context['connection_id']
        and channel.get('requires_reconnect') is not True
        and pipe.sismember(runtime._CHANNEL_INDEX, context['channel_id'])
        and json.loads(pipe.hget(LEDGER_KEY, 'binding:' + context['lineage_id'])) == context)


def _bookmarks(pipe, task_id, context, key):
    """The job lineage is a second durable witness that a pool already existed."""
    from app.services.production_spend_runtime import _JOB_PREFIX
    current, seen, known, first = task_id, set(), None, None
    scope_id = key.removeprefix(PREFIX)
    while current is not None:
        _require(type(current) is str and re.fullmatch('[0-9a-f-]{36}', current)
            and current not in seen and len(seen) < 64)
        seen.add(current)
        job_key = _JOB_PREFIX + current; pipe.watch(job_key)
        job = json.loads(pipe.get(job_key))
        _require(type(job) is dict and job.get('task_id') == current)
        if first is None:
            first = job
        records = job.get('included_stock_pools', {})
        _require(type(records) is dict and len(records) <= 24)
        record = records.get(scope_id)
        if record is not None:
            _require(type(record) is dict and set(record) == {'key', 'sha256'}
                and record['key'] == key and type(record['sha256']) is str
                and re.fullmatch('[0-9a-f]{64}', record['sha256'])
                and (known is None or known == record))
            known = record
        parent = job.get('parent_id')
        if parent is None:
            _require(current == context['lineage_id'])
        current = parent
    return first, known


@_local_transaction
def _bookmark(client, task_id, context, key, encoded):
    from app.services.production_spend_runtime import _JOB_PREFIX
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with client.pipeline() as pipe:
        _authority(pipe, context); pipe.watch(key)
        _require(pipe.get(key) == encoded and pipe.pttl(key) == -1)
        job, known = _bookmarks(pipe, task_id, context, key)
        _require(known is None or known['sha256'] == digest)
        records = dict(job.get('included_stock_pools', {}))
        records[key.removeprefix(PREFIX)] = {'key': key, 'sha256': digest}
        _require(len(records) <= 24)
        job['included_stock_pools'] = records
        pipe.multi(); pipe.set(_JOB_PREFIX + task_id, _raw(job)); _ack(pipe)


@_local_transaction
def _read_saved(client, task_id, context, key):
    with client.pipeline() as pipe:
        _authority(pipe, context)
        pipe.watch(key)
        saved = pipe.get(key)
        _, known = _bookmarks(pipe, task_id, context, key)
        _require(known is None or type(saved) is str
            and hashlib.sha256(saved.encode()).hexdigest() == known['sha256'])
        if saved is not None:
            _require(pipe.pttl(key) == -1 and type(saved) is str and len(saved) <= MAX_MANIFEST * 2)
        pipe.multi(); pipe.ping(); _ack(pipe)
    return saved


@_local_transaction
def _store_manifest(client, task_id, context, key, encoded):
    with client.pipeline() as pipe:
        _authority(pipe, context); pipe.watch(key)
        _, known = _bookmarks(pipe, task_id, context, key)
        _require(known is None and not pipe.exists(key))
        pipe.multi(); pipe.set(key, encoded, nx=True)
        _ack(pipe)


def _download(key, output, size, digest):
    from app.services.storage import _client, settings
    body = None
    try:
        response = _client(single_attempt=True).get_object(Bucket=settings.bucket, Key=key)
        body = response['Body']
        _require(response.get('ContentLength') == size)
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        h, total = hashlib.sha256(), 0
        with os.fdopen(fd, 'wb') as out:
            while chunk := body.read(min(1024 * 1024, size - total + 1)):
                total += len(chunk)
                _require(total <= size)
                h.update(chunk)
                out.write(chunk)
        _require(total == size and h.hexdigest() == digest)
    finally:
        if body is not None:
            body.close()


def _manifest(value, scope):
    _require(type(value) is dict and set(value) == {'scope', 'status', 'qa_approved', 'pools', 'credits', 'seen_ids'}
        and value['scope'] == scope and value['status'] == 'unapproved_stock_pool'
        and value['qa_approved'] is False and type(value['credits']) is list and len(value['credits']) <= 300
        and type(value['seen_ids']) is list and len(value['seen_ids']) <= 300
        and all(type(v) is int and v > 0 for v in value['seen_ids'])
        and type(value['pools']) is list and len(value['pools']) == len(scope['package']['scenes']))
    total = 0
    for row in value['pools']:
        _require(type(row) is list and len(row) <= 3)
        for item in row:
            _require(type(item) is dict and set(item) == {'spec', 'sha256', 'size', 'key'}
                and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256'])
                and type(item['size']) is int and 1024 <= item['size'] <= MAX_FILE
                and item['key'] == 'included-stock-pools/v1/' + scope['context']['lineage_id'] + '/' + item['sha256'] + '.mp4')
            _spec(item['spec'])
            total += item['size']
    _require(total <= MAX_TOTAL and len(_raw(value).encode()) <= MAX_MANIFEST)
    return value


def retain_stock_pool(task_id, package, scene_visuals, credits, seen_ids, work, *, phase):
    """Restore/save exact clips and their attribution before any visual review."""
    from app.services.production_included_router import enabled, _cipher
    if not enabled():
        return
    from app.services import production_spend_runtime as runtime
    from app.services.audio_checkpoint import _candidate_package
    from app.services.storage import upload_file
    try:
        _require(runtime.enforcement_enabled() and runtime._TASK_ID.get() == task_id and phase in PHASES)
        foundation = runtime.configured_ledger()
        context = runtime.resolve_context(foundation.client, task_id)
        _require(context['kind'] == 'shorts')
        scope = {'version': 1, 'context': context, 'phase': phase, 'package': _candidate_package(package)}
        _require(1 <= len(scope['package']['scenes']) <= 12 and type(scene_visuals) is list
            and len(scene_visuals) == len(scope['package']['scenes'])
            and type(credits) is list and type(seen_ids) is set)
        key = PREFIX + hashlib.sha256(_raw(scope).encode()).hexdigest()
        client = foundation.client
        saved = _read_saved(client, task_id, context, key)
        root = Path(work).resolve(strict=True)
        if saved is not None:
            value = _manifest(json.loads(_cipher().decrypt(saved.encode())), scope)
            restored = []
            for scene_idx, row in enumerate(value['pools']):
                specs = []
                for candidate_idx, item in enumerate(row):
                    output = root / f'stock_pool_{phase}_{scene_idx}_{candidate_idx}_{item["sha256"]}.mp4'
                    if output.exists():
                        _require(not output.is_symlink() and _digest(output) == (item['sha256'], item['size']))
                    else:
                        _download(item['key'], output, item['size'], item['sha256'])
                    specs.append({**item['spec'], 'path': str(output)})
                restored.append(specs)
            # Bookmark before returning; a lost cache cannot become a new pool
            # after this point, even in a later child task or another worker.
            _bookmark(client, task_id, context, key, saved)
            scene_visuals[:] = restored
            credits[:] = deepcopy(value['credits'])
            seen_ids.clear(); seen_ids.update(value['seen_ids'])
            return
        pools, files, total = [], {}, 0
        for row in scene_visuals:
            _require(type(row) is list and len(row) <= 3)
            items = []
            for spec in row:
                _require(type(spec) is dict and set(spec) == SPEC_FIELDS | {'path'})
                source = Path(spec['path'])
                _require(source.is_absolute() and source.suffix == '.mp4' and not source.is_symlink()
                    and source.resolve(strict=True) == source and stat.S_ISREG(source.stat().st_mode))
                source.relative_to(root)
                digest, size = _digest(source)
                total += size; _require(total <= MAX_TOTAL)
                asset_key = 'included-stock-pools/v1/' + context['lineage_id'] + '/' + digest + '.mp4'
                items.append({'spec': _spec({k: v for k, v in spec.items() if k != 'path'}),
                              'sha256': digest, 'size': size, 'key': asset_key})
                files[asset_key] = (source, digest, size)
            pools.append(items)
        value = _manifest({'scope': scope, 'status': 'unapproved_stock_pool', 'qa_approved': False,
            'pools': pools, 'credits': deepcopy(credits), 'seen_ids': sorted(seen_ids)}, scope)
        for asset_key, (source, digest, size) in files.items():
            upload_file(source, asset_key, 'video/mp4')
            _require(_digest(source) == (digest, size))
        encoded = _cipher().encrypt(_raw(value).encode()).decode()
        _store_manifest(client, task_id, context, key, encoded)
        _require(client.get(key) == encoded)
        _bookmark(client, task_id, context, key, encoded)
    except Exception as error:
        # Exception messages and local/request paths may contain sensitive
        # details. Retain only fixed phase, exception class and code locations.
        logging.getLogger(__name__).warning('Stock pool preservation stopped: %s', _raw({
            'phase': phase if phase in PHASES else 'invalid', 'error_type': type(error).__name__,
            'locations': [(frame.name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)[-5:]]}))
        raise SpendBlocked('included_stock_pool_unverified') from None
