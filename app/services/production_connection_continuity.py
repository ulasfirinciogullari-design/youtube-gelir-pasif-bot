"""Private continuity evidence for the one audited Capital failed episode.

This unused staging service grants no retry, QA, funding or publication. It
never changes a source, claim, scheduler, authorization or spending record.
Only the exact legacy three-job/six-candidate shape is admitted. Storage
pointers are inspected as historic metadata; no blob or Google request occurs.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from redis.exceptions import WatchError


CHANNEL_ID = 'UC5v9AvNtD3PTLgo6m1jROOA'
ROOT_ID = 'aad98516-eee0-5f39-b49d-af33f01e688e'
MIDDLE_ID = '69ce7728-acce-4e5d-b30f-d5432cf7f3ac'
LEAF_ID = 'f5315330-e927-44c7-aed7-394a331111c8'
LINEAGE = (ROOT_ID, MIDDLE_ID, LEAF_ID)
CONTINUITY_PREFIX = 'youtube_studio:production_connection_continuity:v1:'
_JOB = 'youtube_studio:job:'
_DISPATCH = 'youtube_studio:retry_dispatch:'
_CHILD_CLAIM = 'youtube_studio:retry_child_claim:'
_EXECUTION = 'youtube_studio:retry_child_execution:'
_REPAIR_CLAIM = 'youtube_studio:repair_checkpoint_claim:'
_PAID_CAP = 'youtube_studio:paid_create_budget:'
_PROFILE = 'youtube_studio:youtube_profile:v1:'
_CHANNEL = 'youtube_studio:oauth:channel:v3:'
_CREDENTIAL = 'youtube_studio:oauth:credential:v3:'
_CHANNEL_INDEX = 'youtube_studio:oauth:channels:v3'
_AUTH_EPOCH = 'youtube_studio:oauth:authorization_epoch:v2'
_STATE = 'youtube_studio:production:v1:channel:'
_ACTIVE = 'youtube_studio:production:v1:active'
_ABSENT_PREFIXES = (
    'youtube_studio:source_publication_hold:v1:',
    'youtube_studio:render_cancellation:v1:',
    'youtube_studio:external_episode_delivery:v1:leaf:',
    'youtube_studio:youtube_upload:v2:',
    'youtube_studio:youtube_upload_lock:v2:',
    'youtube_studio:repair_checkpoint:',
    'youtube_studio:selected_visual_recovery:v6:',
)
_SHA = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')
_MAX_BYTES = 2_000_000
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'runnable': False,
          'requires_new_qa': True, 'requires_funding': True,
          'dispatch_eligible': False, 'publish_eligible': False}
_CANDIDATE_FLAGS = {'version': 1, 'diagnostic_only': True, 'qa_approved': False,
                    'reusable': False, 'requires_full_qa': True}
_POINTER_FIELDS = {*_CANDIDATE_FLAGS, 'source_task_id', 'status', 'scene_index', 'phase',
                   'provider', 'raw_key', 'raw_sha256', 'raw_size', 'audio_sha256',
                   'package_sha256', 'manifest_key', 'manifest_sha256', 'manifest_size'}
_AUDIO_FIELDS = {'version', 'status', 'qa_approved', 'requires_full_qa', 'audio_key',
                 'metadata_key', 'audio_sha256', 'metadata_sha256', 'package_sha256', 'size'}
_PROVIDERS = {'runway', 'fal_seedance_2_fast', 'gemini_omni', 'gemini_veo',
              'fal_veo_lite', 'fal_seedance_15_pro', 'fal_seedance_1_fast',
              'gemini_veo_fast', 'gemini_veo_standard'}


class ConnectionContinuityError(RuntimeError):
    """Fixed local-state reason only; no source, credential or provider text."""


def _require(value, code='continuity_evidence_invalid'):
    if not value:
        raise ConnectionContinuityError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _object(raw):
    _require(type(raw) is str and 0 < len(raw.encode('utf-8')) <= _MAX_BYTES)
    def pairs(values):
        out = {}
        for name, value in values:
            _require(name not in out)
            out[name] = value
        return out
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    return value


def _number(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value))
    return value


def _hash(value):
    _require(type(value) is str and _SHA.fullmatch(value))
    return value


def _read(pipe, key, kind='string'):
    pipe.watch(key)
    return pipe.hgetall(key) if kind == 'hash' else pipe.get(key)


def _absent(pipe, key):
    pipe.watch(key)
    _require(pipe.exists(key) == 0, 'continuity_owner_or_work_fence')


def _safe_spec(spec):
    """Keep the complete JSON spec without importing secret-bearing options."""
    _require(type(spec) is dict and len(_json(spec).encode()) <= 100_000)
    forbidden = re.compile(r'(?:api.?key|token|password|credential|secret|authorization)', re.I)
    remaining = 5000
    def walk(value, depth=0):
        nonlocal remaining
        remaining -= 1
        _require(remaining >= 0 and depth <= 32)
        if type(value) is dict:
            for key, item in value.items():
                _require(type(key) is str and not forbidden.search(key))
                walk(item, depth + 1)
        elif type(value) is list:
            for item in value:
                walk(item, depth + 1)
        elif type(value) is float:
            _require(math.isfinite(value))
        else:
            _require(type(value) in (str, int, bool, type(None)))
    walk(spec)


def _candidates(source):
    journal, audio = source.get('generated_asset_candidates'), source.get('audio_candidate_checkpoint')
    _require(type(journal) is dict and all(type(journal.get(k)) is type(v) and journal[k] == v
                                         for k, v in _CANDIDATE_FLAGS.items()))
    _require(journal.get('source_task_id') == LEAF_ID and journal.get('status') == 'candidate_journal'
             and all(type(journal.get(k)) is int and journal[k] == v
                     for k, v in {'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0}.items()))
    entries = journal.get('entries')
    _require(type(entries) is list and len(entries) == 6 and not source.get('audio_candidate_checkpoint_error'))
    _require(type(audio) is dict and set(audio) == _AUDIO_FIELDS
             and type(audio['version']) is int and audio['version'] == 1
             and audio['status'] == 'unapproved_candidate'
             and audio['qa_approved'] is False and audio['requires_full_qa'] is True)
    for field in ('audio_sha256', 'metadata_sha256', 'package_sha256'):
        _hash(audio[field])
    _number(audio['size'], 1024, 14 * 1024 * 1024)
    base = f"audio_candidates/{LEAF_ID}/{audio['audio_sha256']}"
    _require(audio['audio_key'] == base + '/candidate.mp3'
             and audio['metadata_key'] == base + '/metadata-' + audio['metadata_sha256'] + '.json')
    indices, raws, manifests, packages = set(), set(), set(), set()
    for row in entries:
        _require(type(row) is dict and set(row) == _POINTER_FIELDS
                 and all(type(row.get(k)) is type(v) and row[k] == v for k, v in _CANDIDATE_FLAGS.items())
                 and row.get('source_task_id') == LEAF_ID and row.get('status') == 'preserved_candidate'
                 and row.get('phase') == 'initial_generation' and type(row.get('provider')) is str
                 and row['provider'] in _PROVIDERS)
        index = _number(row['scene_index'], 0, 5)
        for field in ('raw_sha256', 'audio_sha256', 'package_sha256', 'manifest_sha256'):
            _hash(row[field])
        _number(row['raw_size'], 1024, 100 * 1024 * 1024)
        _number(row['manifest_size'], 1, 512 * 1024)
        _require(row['audio_sha256'] == audio['audio_sha256']
                 and row['raw_key'] == f"generated_candidates/{LEAF_ID}/raw/{row['raw_sha256']}.mp4"
                 and row['manifest_key'] == f"generated_candidates/{LEAF_ID}/manifests/{row['manifest_sha256']}.json"
                 and index not in indices and row['raw_sha256'] not in raws and row['manifest_sha256'] not in manifests)
        indices.add(index); raws.add(row['raw_sha256']); manifests.add(row['manifest_sha256']); packages.add(row['package_sha256'])
    _require(indices == set(range(6)) and len(packages) == 1)
    return {'journal_sha256': _sha(_json(journal)), 'audio_pointer': deepcopy(audio),
            'generated_pointers': sorted(deepcopy(entries), key=lambda row: row['scene_index']),
            'original_full_package_sha256': next(iter(packages)),
            'audio_candidate_package_sha256': audio['package_sha256'],
            'manifest_content_verified': False, 'media_bytes_verified': False,
            'selected_edit_identity_verified': False}


def _derive(pipe, expected_profile_revision):
    profile_raw = _read(pipe, _PROFILE + CHANNEL_ID)
    channel_raw = _read(pipe, _CHANNEL + CHANNEL_ID)
    profile, channel = _object(profile_raw), _object(channel_raw)
    credential = _read(pipe, _CREDENTIAL + CHANNEL_ID)
    epoch = _read(pipe, _AUTH_EPOCH)
    pipe.watch(_CHANNEL_INDEX)
    member = pipe.sismember(_CHANNEL_INDEX, CHANNEL_ID)
    _require(type(member) in (int, bool) and member == 1, 'continuity_channel_unavailable')
    _require(channel.get('id') == CHANNEL_ID and channel.get('requires_reconnect') is not True
             and type(credential) is str and 0 < len(credential) <= 65536
             and type(epoch) is str and re.fullmatch(r'[1-9][0-9]{0,19}', epoch),
             'continuity_channel_unavailable')
    current_connection = _identifier(channel.get('connection_id'))
    _require(profile.get('channel_id') == CHANNEL_ID and profile.get('profile_revision') == expected_profile_revision
             and profile.get('production_enabled') is True and profile.get('auto_publish') is True
             and profile.get('release_mode') == 'public', 'continuity_profile_changed')
    topics = profile.get('production_topics')
    _require(type(topics) is list and len(topics) == 8 and all(type(t) is str and t.strip() and len(t) <= 240 for t in topics))
    topics = [t.strip() for t in topics]
    state = _read(pipe, _STATE + CHANNEL_ID, 'hash')
    _require(type(state) is dict and state.get('cursor') == '5' and state.get('last_task_id') == ROOT_ID
             and state.get('last_result') == 'FAILURE' and state.get('dispatch_status') == 'finished'
             and state.get('paused_reason') == 'previous_render_failed' and not state.get('active_task_id')
             and state.get('profile_revision') == expected_profile_revision
             and state.get('consumed_prefix') == _sha(json.dumps(topics[:5], ensure_ascii=False)),
             'continuity_schedule_changed')
    next_due = state.get('next_due')
    _require(type(next_due) is str and len(next_due) <= 32
             and math.isfinite(float(next_due)) and float(next_due) >= 0)
    _absent(pipe, _ACTIVE)
    queue_keys = ('celery', *('celery' + chr(6) + chr(22) + str(n) for n in (3, 6, 9)))
    pipe.watch(*queue_keys, 'unacked', 'unacked_index')
    counters = [*(pipe.llen(key) for key in queue_keys), pipe.hlen('unacked'), pipe.zcard('unacked_index')]
    _require(all(type(value) is int and value == 0 for value in counters), 'continuity_owner_or_work_fence')

    records, jobs, dispatches, claims, executions = [], [], [], [], []
    for index, task in enumerate(LINEAGE):
        raw_job = _read(pipe, _JOB + task)
        job = _object(raw_job)
        _require(job.get('task_id') == task and job.get('kind') == 'render' and job.get('state') == 'FAILURE'
                 and job.get('parent_id') == (LINEAGE[index - 1] if index else None)
                 and job.get('retry_child_task_id') == (LINEAGE[index + 1] if index < 2 else None)
                 and job.get('failure_stage') == ('audio_qc_retry', 'audio_qc', 'final_visual_qc_rescue')[index])
        _require(job.get('retry_claimed') is (index < 2) and job.get('repair_claimed') is False
                 and job.get('repair_available') is False
                 and 'publication_hold' not in job and 'owner_cancellation' not in job
                 and job.get('selected_visual_checkpoint') is None and job.get('selected_visual_recovery') is None
                 and (job.get('result') is None or type(job.get('result')) is dict and not job['result']))
        spec = job.get('spec')
        _safe_spec(spec)
        old = _identifier(spec.get('production_connection_id'))
        _require(spec.get('production_channel_id') == CHANNEL_ID and old != current_connection
                 and spec.get('production_profile_revision') == expected_profile_revision
                 and type(spec.get('production_topic_index')) is int and spec['production_topic_index'] == 4
                 and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
                 and type(spec.get('duration_minutes')) in (int, float) and spec['duration_minutes'] == .5
                 and spec.get('production_scheduled') is True and spec.get('publish_after_render') is True
                 and spec.get('music') == 'off' and spec.get('language') in {'tr', 'en'})
        for alias, value in (('youtube_channel_id', CHANNEL_ID), ('youtube_connection_id', old)):
            _require(spec.get(alias) in (None, '', value))
        _require(spec.get('repair_source_task_id') in (None, '', job.get('parent_id')))
        identity = str(profile.get('channel_identity') or '').strip()[:240]
        brief = topics[4] + ('\n\nChannel editorial direction: ' + identity if identity else '')
        _require(spec.get('topic') == brief and spec.get('language') == profile.get('default_language')
                 and spec.get('channel_id') == str(profile.get('route_label') or CHANNEL_ID).strip()
                 and state.get('connection_id') == old)
        for prefix in _ABSENT_PREFIXES:
            _absent(pipe, prefix + task)
        _absent(pipe, _REPAIR_CLAIM + task)
        paid = _read(pipe, _PAID_CAP + task, 'hash')
        expected_used = 6 if index == 2 else 0
        _require(paid == {'cap': '6', 'used': str(expected_used)}
                 and type(job.get('paid_create_slots_used')) is int and job['paid_create_slots_used'] == expected_used
                 and type(job.get('preview_total_paid_create_cap')) is int and job['preview_total_paid_create_cap'] == 6)
        dispatches.append(_read(pipe, _DISPATCH + task, 'hash'))
        claims.append(_read(pipe, _CHILD_CLAIM + task, 'hash'))
        executions.append(_read(pipe, _EXECUTION + task))
        jobs.append(job)
        records.append({'task_id': task, 'parent_id': job.get('parent_id'),
                        'source_state_sha256': _sha(raw_job), 'spec': deepcopy(spec),
                        'spec_sha256': _sha(_json(spec)), 'legacy_paid_counter': dict(paid)})
    frozen = lambda spec: {k: v for k, v in spec.items() if k not in {'workflow', 'repair_source_task_id'}}
    _require(all(frozen(job['spec']) == frozen(jobs[0]['spec']) for job in jobs))
    _require(not claims[0] and executions[0] is None and not dispatches[2])
    edges = []
    for index in (0, 1):
        dispatch, claim, execution = dispatches[index], claims[index + 1], executions[index + 1]
        token = dispatch.get('token')
        _require(type(token) is str and 16 <= len(token) <= 256
                 and dispatch.get('child_task_id') == LINEAGE[index + 1]
                 and dispatch.get('mode') == 'full' and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                 and jobs[index].get('retry_dispatch_state') == dispatch['state']
                 and claim.get('source_task_id') == LINEAGE[index]
                 and claim.get('token') == token and execution == token)
        edges.append({'source_task_id': LINEAGE[index], 'child_task_id': LINEAGE[index + 1],
                      'dispatch_sha256': _sha(_json(dispatch)), 'child_claim_sha256': _sha(_json(claim)),
                      'execution_sha256': _sha(execution)})
    return {'version': 1, 'status': 'staged_private_continuity', **_FLAGS,
            'scope': 'capital_failed_episode_5_legacy_candidates',
            'channel_id': CHANNEL_ID, 'original_task_id': ROOT_ID, 'leaf_task_id': LEAF_ID,
            'old_connection_id': jobs[0]['spec']['production_connection_id'],
            'current_connection_id': current_connection, 'authorization_epoch': epoch,
            'credential_cipher_sha256': _sha(credential), 'channel_record_sha256': _sha(channel_raw),
            'fresh_google_identity_verified': False,
            'profile_revision': expected_profile_revision, 'profile_sha256': _sha(profile_raw),
            'topic_index': 4, 'cursor': 5, 'topic_count': 8,
            'consumed_prefix_sha256': state['consumed_prefix'], 'scheduler_state_sha256': _sha(_json(state)),
            'lineage': records, 'retry_edges': edges, 'legacy_candidates': _candidates(jobs[-1])}


def _scope(original_task_id, leaf_task_id, expected_channel_id, expected_profile_revision):
    _require(type(original_task_id) is str and type(leaf_task_id) is str and type(expected_channel_id) is str
             and original_task_id == ROOT_ID and leaf_task_id == LEAF_ID and expected_channel_id == CHANNEL_ID,
             'continuity_scope_invalid')
    _identifier(expected_profile_revision)


def _client(client):
    if client is None:
        from app.services.studio_state import _client as configured_client
        return configured_client()
    return client


def _result(receipt, status):
    return {'status': status, 'receipt_sha256': _sha(_json(receipt)), 'receipt': deepcopy(receipt)}


def _confirmed(response):
    return type(response) is list and len(response) == 1 and response[0] is True


def _durable_receipt(pipe):
    ttl = pipe.pttl(CONTINUITY_PREFIX + ROOT_ID)
    _require(type(ttl) is int and ttl == -1, 'continuity_receipt_not_durable')


def _recover_after_uncertain_write(client, expected_revision, expected):
    """A lost acknowledgement permits only a fresh read of the same receipt."""
    try:
        with client.pipeline() as pipe:
            raw = _read(pipe, CONTINUITY_PREFIX + ROOT_ID)
            _require(raw == expected, 'continuity_write_uncertain')
            _durable_receipt(pipe)
            receipt = _derive(pipe, expected_revision)
            _require(_json(receipt) == expected, 'continuity_write_uncertain')
            pipe.multi(); pipe.ping()
            _require(_confirmed(pipe.execute()), 'continuity_write_uncertain')
            return _result(receipt, 'already_staged')
    except Exception:
        raise ConnectionContinuityError('continuity_write_uncertain') from None


def _operate(original_task_id, leaf_task_id, expected_channel_id, expected_profile_revision, client, *, stage):
    try:
        _scope(original_task_id, leaf_task_id, expected_channel_id, expected_profile_revision)
        client = _client(client)
        first_encoded = None
        for _ in range(3):
            try:
                with client.pipeline() as pipe:
                    previous = _read(pipe, CONTINUITY_PREFIX + ROOT_ID)
                    if previous is not None:
                        _durable_receipt(pipe)
                    receipt = _derive(pipe, expected_profile_revision)
                    encoded = _json(receipt)
                    _require(len(encoded.encode()) <= _MAX_BYTES)
                    _require(first_encoded is None or first_encoded == encoded, 'continuity_state_changed')
                    first_encoded = encoded
                    _require(previous is None or previous == encoded, 'continuity_receipt_conflict')
                    pipe.multi()
                    create = stage and previous is None
                    if create:
                        pipe.set(CONTINUITY_PREFIX + ROOT_ID, encoded, nx=True)
                    else:
                        pipe.ping()
                    try:
                        response = pipe.execute()
                    except WatchError:
                        raise
                    except Exception:
                        if create:
                            return _recover_after_uncertain_write(client, expected_profile_revision, encoded)
                        raise
                    if not _confirmed(response):
                        if create:
                            return _recover_after_uncertain_write(client, expected_profile_revision, encoded)
                        raise ConnectionContinuityError('continuity_state_unavailable')
                    return _result(receipt, 'staged' if create else 'already_staged' if stage else 'prepared')
            except WatchError:
                continue
        raise ConnectionContinuityError('continuity_store_contention')
    except ConnectionContinuityError:
        raise
    except Exception:
        raise ConnectionContinuityError('continuity_state_unavailable') from None


def prepare_connection_continuity(original_task_id, leaf_task_id, expected_channel_id,
                                  expected_profile_revision, *, client=None):
    """Stable private observation; only WATCH, reads and EXEC/PING are used."""
    return _operate(original_task_id, leaf_task_id, expected_channel_id,
                    expected_profile_revision, client, stage=False)


def stage_connection_continuity(original_task_id, leaf_task_id, expected_channel_id,
                                expected_profile_revision, *, client=None):
    """Create one unused private receipt; never consumes a recovery claim."""
    return _operate(original_task_id, leaf_task_id, expected_channel_id,
                    expected_profile_revision, client, stage=True)
