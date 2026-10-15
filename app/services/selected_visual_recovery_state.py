"""Stage a private V6 repair candidate; no generation or dispatch admission.

The existing generic retry remains fenced until exact multimodal QA and funding
admission are commissioned. Preserving a checkpoint never consumes a child claim
or turns historic candidate reviews into approval.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import re

from app.services import selected_visual_checkpoint as selected, selected_visual_context as context
from app.services import storage, studio_state as state
from app.services.generated_asset_checkpoint import _put_immutable
from app.services.source_publication_hold import HOLD_PREFIX
from app.services.youtube_automation import PROFILE_PREFIX
from app.services.youtube_auth import AUTH_EPOCH_KEY, CHANNEL_PREFIX, CHANNEL_INDEX_KEY, CREDENTIAL_PREFIX
from app.services.youtube_publish_state import UPLOAD_PREFIX


PUBLICATION_PREFIX = state.SELECTED_VISUAL_RECOVERY_PREFIX
MAX_RETRY_HOPS = 16
MAX_RECORD_BYTES = 2 * 1024 * 1024
_FLAGS = {'qa_approved': False, 'requires_full_qa': True, 'publish_eligible': False,
          'dispatch_eligible': False, 'new_paid_create_requests': 0, 'new_tts_requests': 0}
_ADVISORY = {'updated_at', 'repair_available', 'repair_claimed', 'retry_claimed',
             'retry_child_task_id', 'retry_dispatch_state', 'selected_visual_recovery'}
_POINTER_FIELDS = {'version', 'source_task_id', 'key', 'sha256', 'size'}
_RECORD_FIELDS = {'version', 'status', 'source_task_id', 'binding', 'source_state_sha256',
                  'source_spec_sha256', 'profile_sha256', 'channel_sha256',
                  'selected_checkpoint_sha256', 'selected_checkpoint', 'package_sha256',
                  'repair_scene_indices', 'approved_package', *_FLAGS}


class SelectedVisualRecoveryStateError(RuntimeError):
    """A fixed private-state error, without secret values or provider responses."""


def _require(value):
    if not value:
        raise SelectedVisualRecoveryStateError('selected_visual_recovery_unavailable')


def _confirmed(receipt, count):
    _require(type(receipt) is list and len(receipt) == count and all(item is True for item in receipt))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _object(raw):
    _require(type(raw) is str and 0 < len(raw.encode()) <= MAX_RECORD_BYTES)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value)
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    return value


def _id(value):
    _require(type(value) is str and context._TASK.fullmatch(value))
    return value


def _read(pipe, key, kind='string'):
    pipe.watch(key)
    return pipe.hgetall(key) if kind == 'hash' else pipe.get(key)


def _empty(pipe, key):
    pipe.watch(key)
    _require(not pipe.exists(key))


def _source_digest(source):
    return _sha({key: value for key, value in source.items() if key not in _ADVISORY})


def _retry_edge(pipe, parent, child, *, repair_only=False, checkpoint_sha256=None):
    source_id, child_id = parent['task_id'], child['task_id']
    dispatch = _read(pipe, state.RETRY_DISPATCH_PREFIX + source_id, 'hash')
    claim = _read(pipe, state.RETRY_CHILD_CLAIM_PREFIX + child_id, 'hash')
    token = dispatch.get('token')
    _require(type(token) is str and 0 < len(token) <= 4096
             and dispatch.get('child_task_id') == child_id
             and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
             and dispatch.get('mode') in ({'repair'} if repair_only else {'full', 'repair'})
             and parent.get('retry_claimed') is True and parent.get('retry_child_task_id') == child_id
             and parent.get('retry_dispatch_state') == dispatch['state']
             and child.get('parent_id') == source_id
             and claim.get('source_task_id') == source_id and claim.get('token') == token
             and _read(pipe, state.RETRY_CHILD_EXECUTION_PREFIX + child_id) == token)
    if checkpoint_sha256 is not None:
        _require(dispatch.get('selected_visual_recovery_sha256') == checkpoint_sha256)
    if dispatch['mode'] == 'repair':
        _require(parent.get('repair_claimed') is True
                 and _read(pipe, state.REPAIR_CHECKPOINT_CLAIM_PREFIX + source_id) == token)


def _owner_fences(pipe, job):
    task_id = job['task_id']
    _require('publication_hold' not in job and 'owner_cancellation' not in job)
    for prefix in (HOLD_PREFIX, state.RENDER_CANCELLATION_PREFIX, state.EXTERNAL_EPISODE_LEAF_PREFIX,
                   UPLOAD_PREFIX):
        _empty(pipe, prefix + task_id)


def _source_state(pipe, source_id):
    source_id = _id(source_id)
    current, child, nodes, seen, source = source_id, None, [], set(), None
    while current is not None:
        _id(current)
        _require(current not in seen and len(seen) <= MAX_RETRY_HOPS)
        seen.add(current)
        job = _object(_read(pipe, state.JOB_PREFIX + current))
        _require(job.get('task_id') == current and job.get('kind') == 'render' and job.get('state') == 'FAILURE')
        channel_id, connection_id = context._identity(job)
        spec = job['spec']
        _require(spec.get('music') == 'off' and spec.get('language') in {'tr', 'en'}
                 and type(spec.get('topic')) is str and bool(spec['topic'].strip())
                 and type(spec.get('production_profile_revision')) is str and bool(spec['production_profile_revision']))
        _owner_fences(pipe, job)
        if job.get('parent_id') is None:
            # A detached child cannot silently acquire a fresh root identity.
            _empty(pipe, state.RETRY_CHILD_CLAIM_PREFIX + current)
            _empty(pipe, state.RETRY_CHILD_EXECUTION_PREFIX + current)
        if source is None:
            source = job
            _require(job.get('failure_stage') in {'final_visual_qc', 'final_visual_qc_rescue'})
        else:
            _require(context._identity(source) == (channel_id, connection_id))
            def frozen(value):
                return {k: v for k, v in value.items() if k not in {'workflow', 'repair_source_task_id'}}
            _require(frozen(spec) == frozen(source['spec']))
            _retry_edge(pipe, job, child)
        nodes.append({'task_id': current, 'parent_id': job.get('parent_id'), 'spec_sha256': context._digest(spec)})
        child, current = job, job.get('parent_id')
    binding = {'source_task_id': source_id, 'lineage_id': child['task_id'], 'channel_id': channel_id,
               'connection_id': connection_id, 'ancestry_sha256': context._digest({'version': 1, 'nodes': nodes})}
    profile = _object(_read(pipe, PROFILE_PREFIX + channel_id))
    channel = _object(_read(pipe, CHANNEL_PREFIX + channel_id))
    pipe.watch(CHANNEL_INDEX_KEY, CREDENTIAL_PREFIX + channel_id, AUTH_EPOCH_KEY)
    _require(channel.get('id') == channel_id and channel.get('connection_id') == connection_id
             and channel.get('requires_reconnect') is not True and pipe.sismember(CHANNEL_INDEX_KEY, channel_id)
             and pipe.exists(CREDENTIAL_PREFIX + channel_id)
             and profile.get('channel_id') == channel_id and profile.get('production_enabled') is True
             and profile.get('profile_revision') == source['spec']['production_profile_revision']
             and (source['spec'].get('publish_after_render') is not True or profile.get('auto_publish') is True))
    # The epoch is watched but is not an API credential and is never returned.
    epoch = pipe.get(AUTH_EPOCH_KEY)
    _require(epoch is None or type(epoch) is str and re.fullmatch(r'[0-9]+', epoch))
    pointer = source.get('selected_visual_checkpoint')
    _require(type(pointer) is dict and pointer.get('binding') == binding)
    return source, binding, profile, channel


def _unclaimed(pipe, source):
    _require(not any(source.get(field) for field in ('retry_child_task_id', 'retry_claimed', 'repair_claimed')))
    for prefix in (state.RETRY_DISPATCH_PREFIX, state.REPAIR_CHECKPOINT_CLAIM_PREFIX):
        _empty(pipe, prefix + source['task_id'])


def _manifest(source, binding):
    pointer = source['selected_visual_checkpoint']
    return selected.load_selected_visual_checkpoint(pointer, expected_binding=binding,
                                                    expected_package_sha256=pointer.get('package_sha256'))


def _record(source, binding, profile, channel, manifest):
    from app.services.selected_visual_recovery import validate_selected_visual_recovery, validate_selected_voice_recovery

    pointer, package = deepcopy(source['selected_visual_checkpoint']), deepcopy(manifest['package'])
    count, package_sha = len(package['scenes']), manifest['package_sha256']
    rejects = list(manifest['rejected_scene_indices'])
    common = {'version': 6, 'source_task_id': source['task_id'], 'package_sha256': package_sha,
              'selected_checkpoint': pointer}
    media = {**common, 'repair_only': True, 'repair_scene_indices': rejects}
    voice = dict(common)
    _require(validate_selected_visual_recovery(media, count, package_sha) == media
             and validate_selected_voice_recovery(voice, count, package_sha) == voice)
    package.update(_recovered_generated_media=media, _recovered_voice=voice)
    return {'version': 1, 'status': 'staged_private_checkpoint', **_FLAGS,
            'source_task_id': source['task_id'], 'binding': binding,
            'source_state_sha256': _source_digest(source), 'source_spec_sha256': _sha(source['spec']),
            'profile_sha256': _sha(profile), 'channel_sha256': _sha(channel),
            'selected_checkpoint_sha256': _sha(pointer), 'selected_checkpoint': pointer,
            'package_sha256': package_sha, 'repair_scene_indices': rejects, 'approved_package': package}


def _validate_record(record):
    _require(type(record) is dict and set(record) == _RECORD_FIELDS
             and type(record.get('version')) is int and record['version'] == 1
             and record.get('status') == 'staged_private_checkpoint'
             and all(type(record.get(k)) is type(v) and record[k] == v for k, v in _FLAGS.items()))
    _id(record['source_task_id'])
    return record


def _unchanged_source(record, source, binding, profile, channel):
    """Check control-plane identity without downloading original media again.

    Prepare/publish verified all immutable assets. The worker materializer and
    exact-cut helpers independently recheck bytes; this is only the current
    owner/claim fence before each paid or final operation.
    """
    from app.services.selected_visual_recovery import validate_selected_visual_recovery, validate_selected_voice_recovery

    pointer = source['selected_visual_checkpoint']
    expected = {'source_task_id': source['task_id'], 'binding': binding,
                'source_state_sha256': _source_digest(source), 'source_spec_sha256': _sha(source['spec']),
                'profile_sha256': _sha(profile), 'channel_sha256': _sha(channel),
                'selected_checkpoint_sha256': _sha(pointer), 'selected_checkpoint': pointer}
    _require(all(record.get(key) == value for key, value in expected.items()))
    package = record['approved_package']
    original = selected._package(package)
    checksum, count = record['package_sha256'], len(original['scenes'])
    _require(_sha(original) == checksum == pointer['package_sha256'])
    media = validate_selected_visual_recovery(package['_recovered_generated_media'], count, checksum)
    voice = validate_selected_voice_recovery(package['_recovered_voice'], count, checksum)
    _require(media['source_task_id'] == voice['source_task_id'] == source['task_id']
             and media['selected_checkpoint'] == voice['selected_checkpoint'] == pointer
             and media['repair_scene_indices'] == record['repair_scene_indices'])


def _prepared_pointer(source_id, payload):
    checksum = hashlib.sha256(payload).hexdigest()
    return {'version': 1, 'source_task_id': source_id,
            'key': f'selected_recovery/{source_id}/prepared/{checksum}.json', 'sha256': checksum, 'size': len(payload)}


def _load_prepared(pointer):
    _require(type(pointer) is dict and set(pointer) == _POINTER_FIELDS
             and type(pointer.get('version')) is int and pointer['version'] == 1
             and type(pointer.get('size')) is int and 0 < pointer['size'] <= MAX_RECORD_BYTES
             and type(pointer.get('sha256')) is str and re.fullmatch(r'[0-9a-f]{64}', pointer['sha256']))
    source_id = _id(pointer['source_task_id'])
    _require(pointer['key'] == f"selected_recovery/{source_id}/prepared/{pointer['sha256']}.json")
    response = storage._client().get_object(Bucket=storage.settings.bucket, Key=pointer['key'])
    body = response['Body']
    try:
        _require(response.get('ContentType') == 'application/json'
                 and type(response.get('ContentLength')) is int and response['ContentLength'] == pointer['size'])
        payload = body.read(pointer['size'] + 1)
    finally:
        body.close()
    _require(len(payload) == pointer['size'] and _prepared_pointer(source_id, payload) == pointer)
    record = _validate_record(_object(payload.decode('utf-8')))
    _require(_json(record).encode() == payload and record['source_task_id'] == source_id)
    return record


def prepare_selected_visual_recovery(source_task_id):
    """Verify existing bytes and store a private candidate; never evaluate QA."""
    try:
        with state._client().pipeline() as pipe:
            source, binding, profile, channel = _source_state(pipe, source_task_id)
            _unclaimed(pipe, source)
            record = _record(source, binding, profile, channel, _manifest(source, binding))
            payload = _json(record).encode()
            _require(len(payload) <= MAX_RECORD_BYTES)
            pointer = _prepared_pointer(source_task_id, payload)
            _put_immutable(storage._client(), pointer['key'], io.BytesIO(payload), pointer['sha256'],
                           pointer['size'], 'application/json')
            pipe.multi(); pipe.ping()
            _confirmed(pipe.execute(), 1)
        return pointer
    except Exception:
        raise SelectedVisualRecoveryStateError('selected_visual_recovery_unavailable') from None


def publish_selected_visual_recovery(prepared_pointer):
    """Create-only private publication; V6 is explicitly not runnable yet."""
    try:
        record = _load_prepared(prepared_pointer)
        source_id = record['source_task_id']
        with state._client().pipeline() as pipe:
            source, binding, profile, channel = _source_state(pipe, source_id)
            _unclaimed(pipe, source)
            _require(_record(source, binding, profile, channel, _manifest(source, binding)) == record)
            publication_key, checkpoint_key = PUBLICATION_PREFIX + source_id, state.REPAIR_CHECKPOINT_PREFIX + source_id
            prior = _read(pipe, publication_key)
            checkpoint = _read(pipe, checkpoint_key)
            publication = {'version': 1, 'status': 'staged_private_checkpoint', 'source_task_id': source_id,
                           'prepared_pointer': prepared_pointer, 'checkpoint_sha256': _sha(record)}
            if prior is not None:
                _require(_object(prior) == publication and checkpoint == _json(record))
                pipe.multi(); pipe.ping()
                _confirmed(pipe.execute(), 1)
                return {'status': 'already_staged_private_checkpoint', 'source_task_id': source_id, **_FLAGS}
            _require(checkpoint is None and not source.get('repair_available'))
            source.update(repair_available=False, selected_visual_recovery={
                'status': 'staged_private_checkpoint', 'dispatch_eligible': False, 'prepared_pointer': prepared_pointer})
            pipe.multi()
            pipe.set(publication_key, _json(publication), nx=True)
            pipe.set(checkpoint_key, _json(record), nx=True, ex=state.REPAIR_CHECKPOINT_TTL_SECONDS)
            pipe.set(state.JOB_PREFIX + source_id, _json(source), keepttl=True)
            _confirmed(pipe.execute(), 3)
        return {'status': 'staged_private_checkpoint', 'source_task_id': source_id, **_FLAGS}
    except Exception:
        # A lost reply may have committed; replay compares the immutable receipt
        # and never deletes an existing claim or reopens a consumed checkpoint.
        raise SelectedVisualRecoveryStateError('selected_visual_recovery_unavailable') from None


def verify_selected_recovery_child(child_task_id, source_task_id, runtime_spec, approved_package):
    """Read-only proof for the worker; no spending or dispatch authority."""
    try:
        child_task_id, source_task_id = _id(child_task_id), _id(source_task_id)
        _require(child_task_id != source_task_id and type(runtime_spec) is dict and type(approved_package) is dict)
        with state._client().pipeline() as pipe:
            source, binding, profile, channel = _source_state(pipe, source_task_id)
            publication = _object(_read(pipe, PUBLICATION_PREFIX + source_task_id))
            _require(set(publication) == {'version', 'status', 'source_task_id', 'prepared_pointer', 'checkpoint_sha256'}
                     and type(publication['version']) is int and publication['version'] == 1
                     and publication['status'] == 'staged_private_checkpoint' and publication['source_task_id'] == source_task_id)
            record = _load_prepared(publication['prepared_pointer'])
            _require(_sha(record) == publication['checkpoint_sha256']
                     and _json(approved_package) == _json(record['approved_package']))
            _unchanged_source(record, source, binding, profile, channel)
            child = _object(_read(pipe, state.JOB_PREFIX + child_task_id))
            _require(child.get('task_id') == child_task_id and child.get('kind') == 'render'
                     and child.get('state') in {'PENDING', 'RECEIVED', 'STARTED', 'PROGRESS', 'RETRY'})
            _owner_fences(pipe, child)
            expected_spec = {**source['spec'], 'workflow': 'scene_repair', 'repair_source_task_id': source_task_id}
            _require(child.get('spec') == expected_spec and runtime_spec == expected_spec)
            # The future commissioned claim must atomically bind the consumed
            # V6 checkpoint, in addition to the existing token/child CAS proof.
            _retry_edge(pipe, source, child, repair_only=True,
                        checkpoint_sha256=publication['checkpoint_sha256'])
            _empty(pipe, state.REPAIR_CHECKPOINT_PREFIX + source_task_id)
            pipe.multi(); pipe.ping()
            _confirmed(pipe.execute(), 1)
        return {'binding': binding}
    except Exception:
        raise SelectedVisualRecoveryStateError('selected_visual_recovery_unavailable') from None
