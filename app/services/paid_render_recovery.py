"""Prepare, but never publish, a zero-create paid-render recovery checkpoint.

Only an existing, source-bound provider retrieval and the source's immutable
audio candidate are accepted. The returned package requires all ordinary
audio/visual/render QA; no job, retry claim, spending ledger or upload is changed.
The sole write is a create-only private copy of the already-existing narration.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import UUID

from botocore.exceptions import ClientError

from app.services import audio_checkpoint, storage, studio_state
from app.services.voice_candidate_recovery import (
    _download_bounded,
    load_voice_retry_candidate,
    require_unchanged_voice_narration,
)


_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_OPERATION = re.compile(r'^models/veo-3\.1-lite-generate-preview/operations/[A-Za-z0-9_-]{1,128}$')
_MANIFEST_FIELDS = {
    'version', 'source_task_id', 'status', 'qa_approved', 'requires_full_qa',
    'provider', 'operation_name', 'scene_index', 'clip_key', 'clip_sha256',
    'clip_size', 'duration_seconds', 'video_frames', 'source_audio_metadata_sha256',
    'source_audio_package_sha256', 'source_paid_create_slots_used',
    'new_paid_create_requests', 'provenance',
}
_SOURCE_FIELDS = (
    'task_id', 'kind', 'state', 'stage', 'error', 'result', 'failure_stage', 'spec', 'audio_candidate_checkpoint',
    'prepaid_visual_diagnostics', 'paid_create_slots_used', 'preview_total_paid_create_cap',
    'retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available',
)
MAX_MANIFEST_BYTES = 16 * 1024


class PaidRenderRecoveryError(RuntimeError):
    """Secret-safe, terminal preparation failure; never regenerate on error."""


def _runtime():
    # Delay the worker import so normal app startup cannot create an import cycle.
    from app import tasks
    from app.services import director, voice
    return tasks, director, voice


def _canonical_id(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid source identity')
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _source_state(source_task_id: str, *, client=None) -> tuple[dict, dict]:
    source = (
        json.loads(client.get(studio_state.JOB_PREFIX + source_task_id) or 'null')
        if client is not None else studio_state.get_job(source_task_id)
    )
    if (
        not isinstance(source, dict) or source.get('task_id') != source_task_id
        or source.get('state') != 'FAILURE' or source.get('kind') != 'render'
        or source.get('failure_stage') != 'render'
        or any(source.get(key) for key in ('retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available'))
    ):
        raise ValueError('Source is not an unclaimed failed render')
    spec = source.get('spec')
    if (
        not isinstance(spec, dict) or spec.get('mode') != 'production'
        or spec.get('format') != 'shorts' or type(spec.get('duration_minutes')) not in (int, float)
        or spec['duration_minutes'] != 0.5 or spec.get('music') != 'off'
        or not isinstance(spec.get('topic'), str) or not spec['topic'].strip()
        or not isinstance(spec.get('language'), str) or not spec['language'].strip()
    ):
        raise ValueError('Recovery requires a frozen silent-bed production Short')
    if spec.get('publish_after_render') is True and any(
        not isinstance(spec.get(key), str) or not spec[key].strip()
        for key in ('production_channel_id', 'production_connection_id', 'production_profile_revision')
    ):
        raise ValueError('Frozen publication binding is missing')
    client = client if client is not None else studio_state._client()
    if client.exists(*(
        prefix + source_task_id for prefix in (
            studio_state.RETRY_DISPATCH_PREFIX,
            studio_state.REPAIR_CHECKPOINT_PREFIX,
            studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
        )
    )):
        raise ValueError('Source already has a recovery or retry claim')
    raw_budget = client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + source_task_id)
    if not isinstance(raw_budget, dict) or set(raw_budget) != {'cap', 'used'}:
        raise ValueError('Paid ledger is missing')
    if any(not isinstance(raw_budget[key], str) or not re.fullmatch(r'[0-9]{1,3}', raw_budget[key]) for key in raw_budget):
        raise ValueError('Paid ledger is malformed')
    budget = {key: int(value) for key, value in raw_budget.items()}
    # One manifest can prove exactly one paid source, never account for a
    # second unpreserved or ambiguous create from this failed task.
    if (
        budget['used'] != 1 or not 1 <= budget['cap'] <= 100
        or type(source.get('paid_create_slots_used')) is not int
        or source['paid_create_slots_used'] != budget['used']
        or type(source.get('preview_total_paid_create_cap')) is not int
        or source['preview_total_paid_create_cap'] != budget['cap']
    ):
        raise ValueError('Source paid allocation is not exactly one known create')
    return deepcopy(source), budget


def _source_fingerprint(source: dict, budget: dict) -> str:
    return _digest({'source': {key: source.get(key) for key in _SOURCE_FIELDS}, 'budget': budget})


def _validated_manifest(raw: object, source: dict) -> dict:
    task_id = source['task_id']
    audio = source.get('audio_candidate_checkpoint')
    if not isinstance(audio, dict):
        raise ValueError('Source audio pointer is missing')
    if (
        not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS
        or type(raw.get('version')) is not int or raw['version'] != 1
        or raw.get('source_task_id') != task_id or raw.get('status') != 'unapproved_candidate'
        or raw.get('qa_approved') is not False or raw.get('requires_full_qa') is not True
        or raw.get('provider') != 'gemini_veo'
        or not isinstance(raw.get('operation_name'), str) or not _OPERATION.fullmatch(raw['operation_name'])
        or type(raw.get('scene_index')) is not int or not 0 <= raw['scene_index'] < 12
        or type(raw.get('source_paid_create_slots_used')) is not int or raw['source_paid_create_slots_used'] != 1
        or type(raw.get('new_paid_create_requests')) is not int or raw['new_paid_create_requests'] != 0
        or raw.get('source_audio_metadata_sha256') != audio.get('metadata_sha256')
        or raw.get('source_audio_package_sha256') != audio.get('package_sha256')
        or not isinstance(raw.get('clip_sha256'), str) or not _SHA256.fullmatch(raw['clip_sha256'])
        or type(raw.get('clip_size')) is not int or not 1024 <= raw['clip_size'] <= 100 * 1024 * 1024
        or raw.get('clip_key') != f"recovery/{task_id}/raw/scene-{raw['scene_index']:02d}-initial.mp4"
        or type(raw.get('duration_seconds')) not in (int, float)
        or not math.isfinite(raw['duration_seconds']) or not 5 <= raw['duration_seconds'] <= 8.1
        or type(raw.get('video_frames')) is not int or not 120 <= raw['video_frames'] <= 1000
        or not isinstance(raw.get('provenance'), str) or not 12 <= len(raw['provenance']) <= 500
    ):
        raise ValueError('Provider retrieval does not bind the existing source')
    diagnostics = source.get('prepaid_visual_diagnostics')
    if (
        not isinstance(diagnostics, dict) or diagnostics.get('stage') != 'before_paid_allocation'
        or type(diagnostics.get('paid_slots_used')) is not int or diagnostics['paid_slots_used'] != 0
        or diagnostics.get('paid_create_cap') != source['preview_total_paid_create_cap']
        or not isinstance(diagnostics.get('scenes'), list) or not 1 <= len(diagnostics['scenes']) <= 12
    ):
        raise ValueError('Original paid allocation is unavailable')
    rows = diagnostics['scenes']
    if any(
        not isinstance(row, dict) or type(row.get('scene_index')) is not int or row['scene_index'] != index
        or type(row.get('requires_paid_replacement')) is not bool
        for index, row in enumerate(rows)
    ) or [row['scene_index'] for row in rows if row['requires_paid_replacement']] != [raw['scene_index']]:
        raise ValueError('Provider retrieval scene mapping is ambiguous')
    return raw


def _copy_voice_create_only(client, path: Path, key: str, checksum: str, work: Path) -> None:
    """Use conditional private creation; never overwrite another recovery voice."""
    try:
        with path.open('rb') as audio:
            client.put_object(
                Bucket=storage.settings.bucket, Key=key, Body=audio,
                ContentType='audio/mpeg', IfNoneMatch='*',
            )
    except ClientError as exc:
        code = str(exc.response.get('Error', {}).get('Code') or '')
        if code not in {'PreconditionFailed', '412'}:
            raise
        existing = work / 'existing_recovery_voice.mp3'
        digest, _size = _download_bounded(
            client, key, existing, 20 * 1024 * 1024, expected_size=path.stat().st_size,
        )
        if digest != checksum:
            raise ValueError('Existing recovery voice differs') from None


def prepare_paid_render_recovery(
    source_task_id: str,
    retrieval_pointer: dict,
    work_dir: str | Path,
) -> dict:
    """Return an unpublished private checkpoint after fresh immutable story QC.

    ``work_dir`` must be a fresh ``/tmp/youtube_factory/<prep UUID>_attempt_0``
    directory, distinct from the failed parent. No preparation is run implicitly
    by a Studio GET. Publishing the returned pointer and starting the ordinary
    one-shot retry require a separate, explicit server operation and recheck.
    Full speech/prosody/visual/final QC still run in the normal worker. Their
    failure cannot purchase a replacement voice or generated scene in v3 reuse.
    """
    try:
        source_task_id = _canonical_id(source_task_id)
        source, budget = _source_state(source_task_id)
        fingerprint = _source_fingerprint(source, budget)
        if (
            not isinstance(retrieval_pointer, dict)
            or set(retrieval_pointer) != {'manifest_key', 'manifest_sha256', 'manifest_size'}
            or retrieval_pointer['manifest_key'] != f'recovery/{source_task_id}/provider_retrieval_v1.json'
            or not isinstance(retrieval_pointer['manifest_sha256'], str)
            or not _SHA256.fullmatch(retrieval_pointer['manifest_sha256'])
            or type(retrieval_pointer['manifest_size']) is not int
            or not 1 <= retrieval_pointer['manifest_size'] <= MAX_MANIFEST_BYTES
        ):
            raise ValueError('Invalid trusted retrieval pointer')
        work = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', work.name)
        if not match or _canonical_id(match[1]) == source_task_id:
            raise ValueError('Preparation directory is not distinct')
        # The existing loader performs canonical /tmp, symlink, file-size and
        # audio/package fingerprint checks before any independent critic call.
        candidate = load_voice_retry_candidate(
            source_task_id, match[1], source['audio_candidate_checkpoint'], work,
        )
        client = storage._client()
        manifest_path = work / 'provider_retrieval.json'
        checksum, _size = _download_bounded(
            client, retrieval_pointer['manifest_key'], manifest_path,
            MAX_MANIFEST_BYTES, expected_size=retrieval_pointer['manifest_size'],
        )
        if checksum != retrieval_pointer['manifest_sha256']:
            raise ValueError('Retrieval manifest fingerprint changed')
        manifest = _validated_manifest(json.loads(manifest_path.read_text(encoding='utf-8')), source)
        scenes = candidate['package']['scenes']
        if len(scenes) != len(source['prepaid_visual_diagnostics']['scenes']):
            raise ValueError('Candidate scene mapping changed')
        tasks, director, voice = _runtime()
        spec = source['spec']
        options = {key: value for key, value in spec.items() if key not in {'topic', 'language', 'duration_minutes', 'channel_id'}}
        if tasks._normalized_options(options, 0.5) != options:
            raise ValueError('Frozen options no longer match the worker')
        expected_spoken = [
            voice.normalize_turkish_tts(scene['narration'], ensure_terminal=index == len(scenes)-1)
            if spec['language'].lower().startswith('tr') else scene['narration'].strip()
            for index, scene in enumerate(scenes)
        ]
        voice_result = candidate['voice_result']
        if voice_result['spoken_texts'] != expected_spoken:
            raise ValueError('Saved speech differs from the immutable narration')
        clip = work / 'existing_paid_scene.mp4'
        checksum, _size = _download_bounded(
            client, manifest['clip_key'], clip, 100 * 1024 * 1024,
            expected_size=manifest['clip_size'],
        )
        if checksum != manifest['clip_sha256']:
            raise ValueError('Existing paid clip fingerprint changed')
        tasks._validate_recovered_generated_clip(
            clip, minimum_duration=max(5.0, float(voice_result['scene_durations'][manifest['scene_index']]) + 0.35),
            expected_size=manifest['clip_size'], expected_sha256=manifest['clip_sha256'],
        )
        if (
            abs(float(tasks.media_duration(clip)) - manifest['duration_seconds']) > 0.04
            or tasks.video_frame_count(clip) != manifest['video_frames']
        ):
            raise ValueError('Existing paid clip probe differs from retrieval evidence')
        reviewed = director.revalidate_immutable_short_story(
            candidate['package'], spec['topic'], 0.5, spec['language'], options,
            immutable_candidate_narrations=[scene['narration'] for scene in scenes],
        )
        require_unchanged_voice_narration(candidate['package'], reviewed)
        if reviewed.get('studio_options') != options or not director.short_story_package_is_approved(reviewed, spec['topic']):
            raise ValueError('Fresh independent story approval is required')
        package = deepcopy(reviewed)
        package_hash = tasks._recovery_package_sha256(package)
        voice_path = Path(voice_result['path'])
        voice_contract = {
            'version': 1, 'source_task_id': source_task_id, 'package_sha256': package_hash,
            'key': f'recovery/{source_task_id}/raw/voice.mp3',
            'sha256': candidate['audio_sha256'], 'size': voice_path.stat().st_size,
            **{key: voice_result.get(key) for key in (
                'scene_durations', 'spoken_texts', 'duration_before_fit', 'duration_after_fit',
                'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds',
            )},
        }
        media_contract = {
            'version': 3, 'recovery_only': True, 'source_task_id': source_task_id,
            'package_sha256': package_hash,
            'scenes': {str(manifest['scene_index']): [{
                'key': manifest['clip_key'], 'sha256': manifest['clip_sha256'], 'size': manifest['clip_size'],
                'provider': 'gemini_veo', 'provider_attempts': 1, 'synthetic_motion_only': False,
                'motion_recipe_version': None, 'source_media_type': 'video',
            }]},
        }
        tasks._validated_recovered_voice(voice_contract, len(scenes), package_hash)
        tasks._validated_recovered_generated_media(media_contract, len(scenes), package_hash)
        latest, latest_budget = _source_state(source_task_id)
        if _source_fingerprint(latest, latest_budget) != fingerprint:
            raise ValueError('Source changed during preparation')
        _copy_voice_create_only(client, voice_path, voice_contract['key'], voice_contract['sha256'], work)
        package['_recovered_generated_media'] = media_contract
        package['_recovered_voice'] = voice_contract
        return {
            'version': 1, 'source_task_id': source_task_id, 'package_sha256': package_hash,
            'approved_package': package,
            'source_spec_sha256': _digest(spec), 'source_state_sha256': fingerprint,
            'retrieval_manifest_sha256': retrieval_pointer['manifest_sha256'],
            'recovered_clip_sha256': manifest['clip_sha256'],
            'requires_full_qa': True, 'new_paid_create_requests': 0, 'new_tts_requests': 0,
        }
    except Exception:
        raise PaidRenderRecoveryError('Paid render recovery preparation unavailable; no replacement media was generated') from None


def publish_paid_render_recovery(checkpoint: dict) -> dict:
    """Explicitly publish one reviewed receipt without replacing an old claim.

    This operation reserves no paid slot and dispatches no work. The ordinary
    authenticated Studio retry subsequently consumes this private pointer with
    its existing atomic parent/child claim. Conflicts are terminal, never retried.
    """
    try:
        fields = {
            'version', 'source_task_id', 'package_sha256', 'approved_package',
            'source_spec_sha256', 'source_state_sha256', 'retrieval_manifest_sha256',
            'recovered_clip_sha256', 'requires_full_qa', 'new_paid_create_requests', 'new_tts_requests',
        }
        if (
            not isinstance(checkpoint, dict) or set(checkpoint) != fields
            or type(checkpoint.get('version')) is not int or checkpoint['version'] != 1
            or checkpoint.get('requires_full_qa') is not True
            or any(type(checkpoint.get(key)) is not int or checkpoint[key] != 0 for key in ('new_paid_create_requests', 'new_tts_requests'))
            or any(not isinstance(checkpoint.get(key), str) or not _SHA256.fullmatch(checkpoint[key]) for key in (
                'package_sha256', 'source_spec_sha256', 'source_state_sha256',
                'retrieval_manifest_sha256', 'recovered_clip_sha256',
            ))
        ):
            raise ValueError('Recovery receipt is invalid')
        source_id = _canonical_id(checkpoint['source_task_id'])
        job_key = studio_state.JOB_PREFIX + source_id
        checkpoint_key = studio_state.REPAIR_CHECKPOINT_PREFIX + source_id
        watched = (
            job_key, studio_state.PAID_CREATE_BUDGET_PREFIX + source_id,
            studio_state.RETRY_DISPATCH_PREFIX + source_id, checkpoint_key,
            studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + source_id,
        )
        client = studio_state._client()
        with client.pipeline() as transaction:
            transaction.watch(*watched)
            source, budget = _source_state(source_id, client=transaction)
            if (
                _source_fingerprint(source, budget) != checkpoint['source_state_sha256']
                or _digest(source['spec']) != checkpoint['source_spec_sha256']
            ):
                raise ValueError('Prepared source changed')
            tasks, director, _voice = _runtime()
            package = checkpoint['approved_package']
            spec = source['spec']
            options = {key: value for key, value in spec.items() if key not in {'topic', 'language', 'duration_minutes', 'channel_id'}}
            if (
                not isinstance(package, dict) or package.get('studio_options') != options
                or tasks._recovery_package_sha256(package) != checkpoint['package_sha256']
                or not director.short_story_package_is_approved(package, spec['topic'])
            ):
                raise ValueError('Current approved storyboard is required')
            count = len(package.get('scenes') or [])
            media = tasks._validated_recovered_generated_media(
                package.get('_recovered_generated_media'), count, checkpoint['package_sha256'],
            )
            audio = tasks._validated_recovered_voice(
                package.get('_recovered_voice'), count, checkpoint['package_sha256'],
            )
            if (
                not media or media.get('version') != 3 or media.get('recovery_only') is not True
                or not audio or media['source_task_id'] != source_id or audio['source_task_id'] != source_id
                or len(media['scenes']) != 1
            ):
                raise ValueError('Zero-create source bindings are invalid')
            scene_index, entries = next(iter(media['scenes'].items()))
            required = [row['scene_index'] for row in source['prepaid_visual_diagnostics']['scenes'] if row['requires_paid_replacement']]
            if (
                required != [scene_index] or len(entries) != 1
                or entries[0]['sha256'] != checkpoint['recovered_clip_sha256']
                or audio['sha256'] != source['audio_candidate_checkpoint']['audio_sha256']
            ):
                raise ValueError('Recovery assets changed')
            encoded = json.dumps(checkpoint, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            source['repair_available'] = True
            source['updated_at'] = datetime.now(timezone.utc).isoformat()
            transaction.multi()
            transaction.set(checkpoint_key, encoded, nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(job_key, json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False), ex=studio_state.JOB_TTL_SECONDS)
            result = transaction.execute()
            if result != [True, True]:
                raise ValueError('Recovery publication was not confirmed')
        return {'status': 'checkpoint_published', 'source_task_id': source_id, 'requires_full_qa': True}
    except Exception:
        raise PaidRenderRecoveryError('Paid render recovery publication stopped; existing claims were preserved') from None


_RECEIPT_FIELDS = {
    'version', 'source_task_id', 'package_sha256', 'approved_package',
    'source_spec_sha256', 'source_state_sha256', 'retrieval_manifest_sha256',
    'recovered_clip_sha256', 'requires_full_qa', 'new_paid_create_requests', 'new_tts_requests',
}
_VOICE_TIMING_FIELDS = (
    'scene_durations', 'spoken_texts', 'duration_before_fit', 'duration_after_fit',
    'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds',
)


def _load_original_receipt(pointer: dict) -> dict:
    """Read one operator-supplied, hash-bound private receipt, never a URL."""
    if not isinstance(pointer, dict) or set(pointer) != {
        'source_task_id', 'checkpoint_key', 'checkpoint_sha256', 'checkpoint_size',
    }:
        raise ValueError('Invalid original receipt pointer')
    origin_id = _canonical_id(pointer['source_task_id'])
    if (
        pointer['checkpoint_key'] != f'recovery/{origin_id}/prepared_checkpoint_v1.json'
        or not isinstance(pointer['checkpoint_sha256'], str) or not _SHA256.fullmatch(pointer['checkpoint_sha256'])
        or type(pointer['checkpoint_size']) is not int or not 1 <= pointer['checkpoint_size'] <= 512 * 1024
    ):
        raise ValueError('Invalid original receipt binding')
    response = storage._client().get_object(Bucket=storage.settings.bucket, Key=pointer['checkpoint_key'])
    body = response['Body']
    try:
        if response.get('ContentLength') != pointer['checkpoint_size']:
            raise ValueError('Original receipt size changed')
        raw = body.read(pointer['checkpoint_size'] + 1)
    finally:
        body.close()
    if len(raw) != pointer['checkpoint_size'] or hashlib.sha256(raw).hexdigest() != pointer['checkpoint_sha256']:
        raise ValueError('Original receipt fingerprint changed')
    receipt = json.loads(raw)
    if (
        not isinstance(receipt, dict) or set(receipt) != _RECEIPT_FIELDS
        or type(receipt.get('version')) is not int or receipt['version'] != 1
        or receipt.get('source_task_id') != origin_id or receipt.get('requires_full_qa') is not True
        or any(type(receipt.get(key)) is not int or receipt[key] != 0 for key in ('new_paid_create_requests', 'new_tts_requests'))
        or any(not isinstance(receipt.get(key), str) or not _SHA256.fullmatch(receipt[key]) for key in (
            'package_sha256', 'source_spec_sha256', 'source_state_sha256', 'retrieval_manifest_sha256', 'recovered_clip_sha256',
        ))
    ):
        raise ValueError('Original receipt is invalid')
    return receipt


_MAX_CONTINUATION_EDGES = 8


def _continuation_lineage(origin_id: str, leaf_id: str, client) -> tuple[str, ...]:
    """Discover only canonical parent links to the trusted original receipt."""
    _canonical_id(origin_id)
    current = _canonical_id(leaf_id)
    reverse = []
    for _ in range(_MAX_CONTINUATION_EDGES + 1):
        if current in reverse:
            raise ValueError('Continuation lineage contains a cycle')
        reverse.append(current)
        if current == origin_id:
            if len(reverse) < 2:
                raise ValueError('Continuation source is not distinct')
            return tuple(reversed(reverse))
        job = json.loads(client.get(studio_state.JOB_PREFIX + current) or 'null')
        if not isinstance(job, dict) or job.get('task_id') != current:
            raise ValueError('Continuation lineage is unavailable')
        current = _canonical_id(job.get('parent_id'))
    raise ValueError('Continuation lineage exceeds its bound')


def _continuation_keys(lineage: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(prefix + task_id for task_id in lineage for prefix in (
        studio_state.JOB_PREFIX, studio_state.PAID_CREATE_BUDGET_PREFIX,
        studio_state.RETRY_DISPATCH_PREFIX, studio_state.REPAIR_CHECKPOINT_PREFIX,
        studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
    )) + tuple(prefix + task_id for task_id in lineage[1:] for prefix in (
        studio_state.RETRY_CHILD_CLAIM_PREFIX, studio_state.RETRY_CHILD_EXECUTION_PREFIX,
    ))


def _continuation_state(origin_id: str, leaf_id: str, client, *, lineage=None) -> tuple[dict, dict, str]:
    """Validate every consumed repair edge; never reopen an ancestor claim."""
    discovered = _continuation_lineage(origin_id, leaf_id, client)
    if lineage is not None and discovered != lineage:
        raise ValueError('Continuation lineage changed after discovery')
    lineage = discovered
    jobs, budgets = [], []
    for position, task_id in enumerate(lineage):
        expected_used = 1 if position == 0 else 0
        job = json.loads(client.get(studio_state.JOB_PREFIX + task_id) or 'null')
        if (
            not isinstance(job, dict) or job.get('task_id') != task_id or job.get('kind') != 'render'
            or job.get('state') != 'FAILURE' or job.get('stage') != 'failed'
            or job.get('failure_stage') not in ({'render'} if expected_used else {'render', 'final_visual_qc', 'final_visual_qc_rescue'})
        ):
            raise ValueError('Continuation requires failed renders')
        raw = client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + task_id)
        if set(raw) != {'cap', 'used'} or any(not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,3}', value) for value in raw.values()):
            raise ValueError('Continuation ledger is unavailable')
        budget = {key: int(value) for key, value in raw.items()}
        if (
            budget['used'] != expected_used or not 1 <= budget['cap'] <= 100
            or type(job.get('paid_create_slots_used')) is not int or job['paid_create_slots_used'] != expected_used
            or type(job.get('preview_total_paid_create_cap')) is not int or job['preview_total_paid_create_cap'] != budget['cap']
        ):
            raise ValueError('Continuation spending differs')
        jobs.append(job)
        budgets.append(budget)
    origin, leaf = jobs[0], jobs[-1]
    spec = origin.get('spec')
    if (
        not isinstance(spec, dict) or leaf.get('spec') != spec or spec.get('mode') != 'production'
        or spec.get('format') != 'shorts' or type(spec.get('duration_minutes')) not in (int, float)
        or spec['duration_minutes'] != 0.5 or spec.get('music') != 'off'
        or not isinstance(spec.get('topic'), str) or not spec['topic'].strip()
        or not isinstance(spec.get('language'), str) or not spec['language'].strip()
        or any(leaf.get(key) for key in ('retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available'))
    ):
        raise ValueError('Continuation frozen lineage differs')
    if spec.get('publish_after_render') is True and any(
        not isinstance(spec.get(key), str) or not spec[key].strip()
        for key in ('production_channel_id', 'production_connection_id', 'production_profile_revision')
    ):
        raise ValueError('Frozen publication binding is missing')
    if client.exists(*(studio_state.REPAIR_CHECKPOINT_PREFIX + task_id for task_id in lineage[:-1]), *(
        prefix + leaf_id for prefix in (studio_state.RETRY_DISPATCH_PREFIX, studio_state.REPAIR_CHECKPOINT_PREFIX, studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX)
    )):
        raise ValueError('Continuation already has an outgoing claim')
    edges = []
    for position, (parent, child) in enumerate(zip(jobs, jobs[1:]), start=1):
        parent_id, child_id = parent['task_id'], child['task_id']
        if (
            child.get('spec') != spec or budgets[position]['cap'] != budgets[0]['cap']
            or child.get('parent_id') != parent_id or parent.get('retry_child_task_id') != child_id
            or parent.get('repair_claimed') is not True or parent.get('retry_claimed') is not True
            or parent.get('repair_available') is not False
            or child['audio_candidate_checkpoint']['audio_sha256'] != origin['audio_candidate_checkpoint']['audio_sha256']
            or child['audio_candidate_checkpoint']['package_sha256'] != leaf['audio_candidate_checkpoint']['package_sha256']
        ):
            raise ValueError('Continuation frozen lineage differs')
        dispatch = client.hgetall(studio_state.RETRY_DISPATCH_PREFIX + parent_id)
        child_claim = client.hgetall(studio_state.RETRY_CHILD_CLAIM_PREFIX + child_id)
        claim = client.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + parent_id)
        execution = client.get(studio_state.RETRY_CHILD_EXECUTION_PREFIX + child_id)
        token = dispatch.get('token')
        if (
            not isinstance(token, str) or not 16 <= len(token) <= 256
            or dispatch.get('mode') != 'repair' or dispatch.get('state') != 'dispatched'
            or dispatch.get('child_task_id') != child_id or child_claim.get('source_task_id') != parent_id
            or child_claim.get('token') != token or claim != token or execution != token
        ):
            raise ValueError('Consumed repair execution is unproven')
        edges.append({'dispatch': dispatch, 'child_claim': child_claim, 'claim': claim, 'execution': execution})
    fingerprint = _digest({
        'jobs': [{key: job.get(key) for key in (*_SOURCE_FIELDS, 'parent_id')} for job in jobs],
        'budgets': budgets, 'edges': edges,
    })
    return origin, leaf, fingerprint


def _validate_continuation_package(original: dict, origin: dict, leaf: dict):
    tasks, director, _voice = _runtime()
    spec = origin['spec']
    package = original['approved_package']
    options = {key: value for key, value in spec.items() if key not in {'topic', 'language', 'duration_minutes', 'channel_id'}}
    if (
        _digest(spec) != original['source_spec_sha256'] or tasks._normalized_options(options, 0.5) != options
        or not isinstance(package, dict) or package.get('studio_options') != options
        or tasks._recovery_package_sha256(package) != original['package_sha256']
        or not director.short_story_package_is_approved(package, spec['topic'])
    ):
        raise ValueError('Original story approval is no longer current')
    count = len(package.get('scenes') or [])
    media = tasks._validated_recovered_generated_media(package.get('_recovered_generated_media'), count, original['package_sha256'])
    audio = tasks._validated_recovered_voice(package.get('_recovered_voice'), count, original['package_sha256'])
    if (
        not media or media.get('version') != 3 or media.get('recovery_only') is not True
        or not audio or media['source_task_id'] != origin['task_id'] or audio['source_task_id'] != origin['task_id']
        or len(media['scenes']) != 1
    ):
        raise ValueError('Original zero-create assets are unavailable')
    index, entries = next(iter(media['scenes'].items()))
    required = [row['scene_index'] for row in origin['prepaid_visual_diagnostics']['scenes'] if row['requires_paid_replacement']]
    if (
        required != [index] or len(entries) != 1 or entries[0]['sha256'] != original['recovered_clip_sha256']
        or audio['sha256'] != origin['audio_candidate_checkpoint']['audio_sha256']
        or audio['sha256'] != leaf['audio_candidate_checkpoint']['audio_sha256']
        or audio['size'] != leaf['audio_candidate_checkpoint']['size']
        or _digest(audio_checkpoint._candidate_package(package)) != leaf['audio_candidate_checkpoint']['package_sha256']
    ):
        raise ValueError('Paired continuation assets changed')
    return tasks, audio, entries[0], index


def prepare_paid_recovery_continuation(leaf_task_id: str, original_receipt_pointer: dict, work_dir: str | Path) -> dict:
    """Prepare a failed zero-create leaf, never any consumed ancestor.

    No provider generation, new story review, Storage write or state mutation.
    The original still-current attestation and paired voice/media are retained;
    the normal worker must repeat all real audio, stock, visual and render QA.
    """
    try:
        leaf_id = _canonical_id(leaf_task_id)
        original = _load_original_receipt(original_receipt_pointer)
        origin_id = original['source_task_id']
        client = studio_state._client()
        origin, leaf, fingerprint = _continuation_state(origin_id, leaf_id, client)
        tasks, audio, entry, index = _validate_continuation_package(original, origin, leaf)
        work = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', work.name)
        if not match or _canonical_id(match[1]) in {origin_id, leaf_id}:
            raise ValueError('Continuation preparation directory is not distinct')
        candidate = load_voice_retry_candidate(leaf_id, match[1], leaf['audio_candidate_checkpoint'], work)
        require_unchanged_voice_narration(candidate['package'], original['approved_package'])
        if candidate['audio_sha256'] != audio['sha256'] or any(candidate['voice_result'].get(key) != audio.get(key) for key in _VOICE_TIMING_FIELDS):
            raise ValueError('Continuation voice timing changed')
        asset_client = storage._client()
        for key, size, digest, name, maximum in (
            (audio['key'], audio['size'], audio['sha256'], 'paired_voice.mp3', 20 * 1024 * 1024),
            (entry['key'], entry['size'], entry['sha256'], 'existing_paid_scene.mp4', 100 * 1024 * 1024),
        ):
            path = work / name
            if path.exists() or path.is_symlink():
                raise ValueError('Continuation asset destination already exists')
            checksum, _size = _download_bounded(asset_client, key, path, maximum, expected_size=size)
            if checksum != digest:
                raise ValueError('Continuation asset fingerprint changed')
        tasks._validate_recovered_generated_clip(
            work / 'existing_paid_scene.mp4', minimum_duration=max(5.0, float(audio['scene_durations'][index]) + 0.35),
            expected_size=entry['size'], expected_sha256=entry['sha256'],
        )
        if _continuation_state(origin_id, leaf_id, client)[2] != fingerprint:
            raise ValueError('Continuation changed during preparation')
        checkpoint = deepcopy(original)
        checkpoint.update(
            source_task_id=leaf_id,
            source_state_sha256=_source_fingerprint(leaf, {'cap': leaf['preview_total_paid_create_cap'], 'used': 0}),
            continuation={'version': 1, 'original_receipt_pointer': deepcopy(original_receipt_pointer), 'lineage_sha256': fingerprint},
        )
        return checkpoint
    except Exception:
        raise PaidRenderRecoveryError('Paid recovery continuation preparation unavailable; no replacement media was generated') from None


def publish_paid_recovery_continuation(checkpoint: dict) -> dict:
    """CAS-publish only the unclaimed leaf; leave all consumed claims intact."""
    try:
        if not isinstance(checkpoint, dict) or set(checkpoint) != _RECEIPT_FIELDS | {'continuation'}:
            raise ValueError('Invalid continuation receipt')
        proof = checkpoint['continuation']
        if not isinstance(proof, dict) or set(proof) != {'version', 'original_receipt_pointer', 'lineage_sha256'} or type(proof['version']) is not int or proof['version'] != 1:
            raise ValueError('Invalid continuation proof')
        original = _load_original_receipt(proof['original_receipt_pointer'])
        origin_id, leaf_id = original['source_task_id'], _canonical_id(checkpoint['source_task_id'])
        client = studio_state._client()
        lineage = _continuation_lineage(origin_id, leaf_id, client)
        with client.pipeline() as transaction:
            transaction.watch(*_continuation_keys(lineage))
            origin, leaf, fingerprint = _continuation_state(origin_id, leaf_id, transaction, lineage=lineage)
            _validate_continuation_package(original, origin, leaf)
            expected = deepcopy(original)
            expected.update(
                source_task_id=leaf_id,
                source_state_sha256=_source_fingerprint(leaf, {'cap': leaf['preview_total_paid_create_cap'], 'used': 0}),
                continuation={'version': 1, 'original_receipt_pointer': proof['original_receipt_pointer'], 'lineage_sha256': fingerprint},
            )
            if _digest(checkpoint) != _digest(expected):
                raise ValueError('Prepared continuation changed')
            encoded = json.dumps(checkpoint, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            leaf['repair_available'] = True
            leaf['updated_at'] = datetime.now(timezone.utc).isoformat()
            transaction.multi()
            transaction.set(studio_state.REPAIR_CHECKPOINT_PREFIX + leaf_id, encoded, nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(studio_state.JOB_PREFIX + leaf_id, json.dumps(leaf, ensure_ascii=False, separators=(',', ':'), allow_nan=False), ex=studio_state.JOB_TTL_SECONDS)
            if transaction.execute() != [True, True]:
                raise ValueError('Continuation publication was not confirmed')
        return {'status': 'checkpoint_published', 'source_task_id': leaf_id, 'requires_full_qa': True}
    except Exception:
        raise PaidRenderRecoveryError('Paid recovery continuation publication stopped; existing claims were preserved') from None
