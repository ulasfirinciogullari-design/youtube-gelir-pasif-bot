"""Explicit, bounded repair of one preserved failed production Short.

Preparation reviews existing raw clips; it never synthesizes or dispatches.
The private receipt is not final QA approval. The ordinary v2/v4 repair worker
must still run every audio, visual, rendering and publication gate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re

from botocore.exceptions import ClientError

from app.services import audio_checkpoint, storage, studio_state
from app.services.paid_render_recovery import _canonical_id, _copy_voice_create_only, _digest, _runtime
from app.services.visual_allocation_checkpoint import _review, _text
from app.services.voice_candidate_recovery import (
    _download_bounded, load_voice_retry_candidate, require_unchanged_voice_narration,
)


SOURCE = 'cb476d47-8ccc-4a55-8da8-e82ca8db3272'
WORKPRINT_SHA = '6e828c8d528963d287503edf109fba88369d51f392da4bfce6dbe72e9ac12dd2'
WORKPRINT_SIZE = 12802
VOICE_SHA = 'b98f0388c9fd30e0370f71fce743bc7d33f7868f01d96dd5d0b161eb2d9266ce'
VOICE_SIZE = 691820
SELECTED = {
    0: ('45a3a884311b53aaa6ed6e138220ae0e4fe9549e5428fb9345ac429bc05db700', 2367477),
    1: ('37745f0f576ae5dd8eb493dcb71ad6603bea22ae16df70155b2ba04a0ce5e895', 3102488),
    2: ('eb07e3ddfb228b8792af9533a9a5330b4c27df1978551a46337f97a9039dd17f', 1368667),
    3: ('449e4cb9e6faeace3ed5389eb7b608f6d9187af39ec2d4bc4c23d91b2ce06523', 1669242),
    4: ('e6507387dba89a592bdc8023ca29d37e87bc482d64c457d23335cfdc753eeb64', 1451822),
    5: ('c727528701f29fd10e947936b1a8306356f3fcb0102c465905f21a7da13e6f63', 14652547),
}
RETAINED = (1, 2, 3, 4)
REPAIRS = (0, 5)
THREE_REPAIRS = (0, 2, 5)
# Exact-source, operator-authorized shooting contracts. Only these three
# prompts change, before the independent critic; speech and retained scenes do not.
REPAIR_SHOTS = {
    0: ('Documentary reenactment, United States grocery checkout in 1974. Close view of a cashier hand '
        'moving a grocery item across a period red-laser glass barcode scanner, with a period mechanical '
        'cash register. The hand, barcode side and scanner action are the subjects; no front-label hero shot, '
        'invented branding, gibberish print, modern PIN pad or LCD screen. Not authentic archival footage.'),
    2: ('Documentary reenactment of the 1974 United States first UPC checkout. A ten-pack of Wrigley\'s '
        'Juicy Fruit chewing gum passes over a period NCR glass scanner beside a mechanical register. '
        'Preserve the exact product identity and ten-pack quantity; show the side or back barcode and '
        'cashier hand, not a front-label hero shot. No modern PIN pad, touchscreen, LCD, fabricated labels '
        'or modern payment terminal. Not authentic archival footage.'),
    5: ('Clearly present-day documentary B-roll of an active fulfillment line: a barcode-bearing box '
        'passes a scanner and continues along an operating conveyor among other shipping boxes. '
        'Make scanner, barcode side, boxes and active movement visibly readable as the context for '
        'global supply-chain speed, without claiming a measured speedup. Not an empty belt or 1974 archive; '
        'no fake brands, invented labels, overlays or garbled print.'),
}
_SHA = re.compile(r'[0-9a-f]{64}')
_OPERATION = re.compile(r'models/veo-3\.1(?:-lite|-fast)?-generate-preview/operations/[A-Za-z0-9_-]{1,128}')
_RETRIEVAL_SOURCE_FIELDS = (
    'task_id', 'kind', 'state', 'stage', 'failure_stage', 'spec',
    'audio_candidate_checkpoint', 'qa_workprint', 'paid_create_slots_used',
    'preview_total_paid_create_cap', 'retry_child_task_id', 'retry_claimed',
    'repair_claimed', 'repair_available',
)
MAX_JSON = 256 * 1024
MAX_CLIP = 16 * 1024 * 1024


class FailedVisualRecoveryError(RuntimeError):
    """Fixed error message with optional bounded, secret-safe scene evidence."""

    def __init__(self, *, diagnostics=None, diagnostic_pointer=None):
        super().__init__('Failed visual recovery stopped; no new media or retry was created')
        self.diagnostics = deepcopy(diagnostics or [])
        self.diagnostic_pointer = deepcopy(diagnostic_pointer)


def _require(condition):
    if not condition:
        raise ValueError('Invalid failed visual recovery evidence')


def _keys():
    return tuple(prefix + SOURCE for prefix in (
        studio_state.JOB_PREFIX, studio_state.PAID_CREATE_BUDGET_PREFIX,
        studio_state.RETRY_DISPATCH_PREFIX, studio_state.REPAIR_CHECKPOINT_PREFIX,
        studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
    ))


def _state(client):
    source = json.loads(client.get(studio_state.JOB_PREFIX + SOURCE) or 'null')
    _require(isinstance(source, dict) and source.get('task_id') == SOURCE
             and source.get('kind') == 'render' and source.get('state') == 'FAILURE'
             and source.get('failure_stage') == 'final_visual_qc_rescue'
             and all(not source.get(key) for key in ('retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available')))
    _require(not client.exists(*_keys()[2:]))
    ledger = client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + SOURCE)
    _require(ledger == {'cap': '6', 'used': '6'}
             and type(source.get('paid_create_slots_used')) is int and source['paid_create_slots_used'] == 6
             and type(source.get('preview_total_paid_create_cap')) is int and source['preview_total_paid_create_cap'] == 6)
    spec = source.get('spec')
    _require(isinstance(spec, dict) and spec.get('mode') == 'production'
             and spec.get('format') == 'shorts' and type(spec.get('duration_minutes')) in (int, float)
             and spec['duration_minutes'] == 0.5 and spec.get('music') == 'off'
             and spec.get('language') == 'tr'
             and isinstance(spec.get('topic'), str) and bool(spec['topic'].strip()))
    if spec.get('publish_after_render') is True:
        _require(all(isinstance(spec.get(key), str) and bool(spec[key].strip()) for key in (
            'production_channel_id', 'production_connection_id', 'production_profile_revision')))
    pointer = source.get('qa_workprint')
    _require(isinstance(pointer, dict) and type(pointer.get('version')) is int and pointer['version'] == 1
             and pointer.get('task_id') == SOURCE and pointer.get('status') == 'qa_workprint'
             and all(pointer.get(key) is False for key in ('qa_approved', 'reusable', 'publish_eligible'))
             and pointer.get('metadata_key') == f'qa_workprints/{SOURCE}/{WORKPRINT_SHA}.json'
             and pointer.get('metadata_sha256') == WORKPRINT_SHA and pointer.get('metadata_size') == WORKPRINT_SIZE)
    audio = source.get('audio_candidate_checkpoint')
    _require(isinstance(audio, dict) and audio.get('audio_sha256') == VOICE_SHA
             and type(audio.get('size')) is int and audio['size'] == VOICE_SIZE
             and audio.get('qa_approved') is False and audio.get('requires_full_qa') is True)
    return source, _digest({'source': source, 'ledger': ledger})


def _options(source):
    return {key: value for key, value in source['spec'].items()
            if key not in {'topic', 'language', 'duration_minutes', 'channel_id'}}


def _read_json(client, key, checksum, size, maximum=MAX_JSON):
    _require(isinstance(checksum, str) and _SHA.fullmatch(checksum)
             and type(size) is int and 1 <= size <= maximum)
    response = client.get_object(Bucket=storage.settings.bucket, Key=key)
    body = response.get('Body')
    try:
        _require(type(response.get('ContentLength')) is int and response['ContentLength'] == size)
        payload = body.read(size + 1)
        _require(len(payload) == size and hashlib.sha256(payload).hexdigest() == checksum)
        value = json.loads(payload)
        _require(isinstance(value, dict))
        return value
    finally:
        if body is not None:
            body.close()


def _evidence(client, pointer, source):
    _require(isinstance(pointer, dict) and set(pointer) == {'manifest_key', 'manifest_sha256', 'manifest_size'}
             and pointer.get('manifest_key') == f"recovery/{SOURCE}/provider_retrieval_v1-{pointer.get('manifest_sha256')}.json")
    manifest = _read_json(client, pointer['manifest_key'], pointer['manifest_sha256'], pointer['manifest_size'], 65536)
    old_fingerprint = _digest({'source': {key: source.get(key) for key in _RETRIEVAL_SOURCE_FIELDS},
                               'ledger': {'cap': '6', 'used': '6'}})
    expected = {
        'version': 1, 'source_task_id': SOURCE, 'status': 'unapproved_preservation',
        'diagnostic_only': True, 'qa_approved': False, 'reusable': False, 'requires_full_qa': True,
        'source_state_sha256': old_fingerprint, 'source_spec_sha256': _digest(source['spec']),
        'workprint_metadata_key': f'qa_workprints/{SOURCE}/{WORKPRINT_SHA}.json',
        'workprint_metadata_sha256': WORKPRINT_SHA, 'workprint_metadata_size': WORKPRINT_SIZE,
        'voice_sha256': VOICE_SHA, 'voice_size': VOICE_SIZE,
        'source_audio_metadata_sha256': source['audio_candidate_checkpoint']['metadata_sha256'],
        'source_audio_package_sha256': source['audio_candidate_checkpoint']['package_sha256'],
        'source_paid_create_slots_used': 6, 'source_paid_create_cap': 6,
        'new_paid_create_requests': 0, 'new_tts_requests': 0,
        'preserved_raw_scene_indices': list(RETAINED), 'rejected_selected_scene_indices': list(REPAIRS),
        'stock_scene_five_not_downloaded': True, 'voice_not_downloaded': True,
    }
    _require(set(manifest) == set(expected) | {'operations', 'selection_proof'})
    _require(all(type(manifest[key]) is type(value) and manifest[key] == value for key, value in expected.items()))
    metadata = _read_json(client, expected['workprint_metadata_key'], WORKPRINT_SHA, WORKPRINT_SIZE)
    _require(metadata.get('task_id') == SOURCE and metadata.get('status') == 'qa_workprint'
             and type(metadata.get('version')) is int and metadata['version'] == 1
             and all(metadata.get(key) is False for key in ('qa_approved', 'reusable', 'publish_eligible'))
             and metadata.get('voice') == {'sha256': VOICE_SHA, 'size': VOICE_SIZE, 'existing_voice_quality_passed': True})
    rows = metadata.get('scenes')
    _require(isinstance(rows, list) and len(rows) == 6)
    for index, row in enumerate(rows):
        _require(isinstance(row, dict) and type(row.get('scene_index')) is int and row['scene_index'] == index)
        selected = row.get('selection')
        _require(isinstance(selected, dict) and selected.get('sha256') == SELECTED[index][0]
                 and type(selected.get('size')) is int and selected['size'] == SELECTED[index][1]
                 and type(selected.get('selected_spec_index')) is int
                 and selected['selected_spec_index'] == (1 if index in REPAIRS else 0))
        if index < 5:
            _require(selected.get('generation_provider') == 'gemini_veo'
                     and selected.get('start_fraction') == 0 and selected.get('forbid_loop') is True)
        else:
            _require(selected.get('stock_provider') == 'pexels' and selected.get('pexels_id') == 38052460
                     and selected.get('start_fraction') == 0.5)
    _require(manifest['selection_proof'] == [{'scene_index': index, 'selection': row['selection']}
                                            for index, row in enumerate(rows)])
    operations = manifest['operations']
    _require(isinstance(operations, list) and len(operations) == 6)
    mapped, names, hashes = {}, set(), set()
    for row in operations:
        _require(isinstance(row, dict) and set(row) == {
            'operation_name', 'provider', 'matched_scene_index', 'clip_key', 'clip_sha256', 'clip_size',
            'duration_seconds', 'video_frames', 'role', 'qa_approved', 'reusable'})
        index, checksum = row['matched_scene_index'], row['clip_sha256']
        _require((index is None or type(index) is int and 0 <= index <= 4)
                 and index not in mapped and isinstance(row['operation_name'], str)
                 and _OPERATION.fullmatch(row['operation_name']) and row['operation_name'] not in names
                 and isinstance(checksum, str) and _SHA.fullmatch(checksum) and checksum not in hashes
                 and row['provider'] == 'gemini_veo' and row['qa_approved'] is False and row['reusable'] is False
                 and type(row['clip_size']) is int and 1024 <= row['clip_size'] <= MAX_CLIP
                 and type(row['duration_seconds']) in (int, float) and math.isfinite(row['duration_seconds'])
                 and 5 <= row['duration_seconds'] <= 8.1
                 and type(row['video_frames']) is int and 120 <= row['video_frames'] <= 1000)
        if index is not None:
            _require((checksum, row['clip_size']) == SELECTED[index])
        retained = index in RETAINED
        _require(row['role'] == ('preserved_raw_candidate' if retained else 'diagnostic_only')
                 and row['clip_key'] == (f'recovery/{SOURCE}/raw/scene-{index:02d}-initial.mp4' if retained
                                         else f'recovery/{SOURCE}/diagnostic/{checksum}.mp4'))
        mapped[index] = row
        names.add(row['operation_name'])
        hashes.add(checksum)
    _require(set(mapped) == {None, 0, 1, 2, 3, 4})
    return metadata, {index: mapped[index] for index in RETAINED}


def _review_runtime():
    from app.services import render
    from app.services.visual_qc import review_scene_visuals
    return render, review_scene_visuals


def _review_existing(package, voice_result, paths, work, threshold, retained=RETAINED):
    render, review_visuals = _review_runtime()
    scenes, durations = package['scenes'], voice_result['scene_durations']
    measured = float(render.media_duration(voice_result['path']))
    _require(math.isfinite(measured) and 28.7 <= measured <= 30.08
             and abs(measured - voice_result['duration_after_fit']) <= 0.12)
    # Repair slots contribute timing only. Their placeholder paths are never
    # opened, normalized or sent to a reviewer; no workprint is a media input.
    pools = [[{'path': str(paths.get(index, work / f'not-reviewed-{index}.mp4')),
               'generated': True, 'generation_provider': 'gemini_veo',
               'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True}]
             for index in range(6)]
    timeline = render._scene_timeline(scenes, pools, durations, measured, [])
    _require(len(timeline) == 6 and [shot[3] for shot in timeline] == list(range(6)))
    frame_counts = render._timeline_frame_counts(timeline, measured)
    review_dir = work / 'retained_exact_review'
    review_dir.mkdir(exist_ok=False)
    visual_inputs = []
    for index in retained:
        spec, _duration, transition, _scene = timeline[index]
        target = review_dir / f'scene-{index:02d}.mp4'
        render.normalize_clip(spec, target, frame_counts[index] / render.FPS, index, transition, '1080x1920')
        visual_inputs.append([{**spec, 'path': str(target)}])
    output = review_visuals(
        [scenes[index] for index in retained], visual_inputs, review_dir, len(retained),
        _missing_review_attempts=0, _score_reason_consistency_attempts=0,
        topic=package['_recovery_topic'], story_scenes=scenes,
        content_style=package['studio_options'].get('content_style', ''), evidence_sources=package.get('sources') or [],
    )
    _require(isinstance(output, dict) and not output.get('missing_review_indices'))
    reviews = output.get('reviews')
    _require(isinstance(reviews, list) and len(reviews) == len(retained))
    by_index = {}
    for review in reviews:
        _require(isinstance(review, dict) and type(review.get('scene_index')) is int
                 and 0 <= review['scene_index'] < len(retained) and review['scene_index'] not in by_index)
        by_index[review['scene_index']] = review
    evidence = []
    for local, index in enumerate(retained):
        raw = by_index[local]
        safe = {'scene_index': index, 'raw_sha256': SELECTED[index][0],
                'review': _review(raw), 'exact_cut_frames': frame_counts[index]}
        evidence.append(safe)
    return evidence


def _review_passes(review, threshold):
    return (isinstance(review, dict) and type(review.get('score')) in (int, float)
            and threshold <= review['score'] <= 100 and review.get('best_candidate_index') == 0
            and all(review.get('gates', {}).get(key) is True for key in (
                'evidence_gate_passed', 'identity_gate_passed', 'editorial_gate_passed')))


def _store_prepared(client, receipt):
    payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(1 <= len(payload) <= MAX_JSON)
    checksum = hashlib.sha256(payload).hexdigest()
    key = f'recovery/{SOURCE}/failed_visual_prepared_v1-{checksum}.json'
    try:
        response = client.put_object(Bucket=storage.settings.bucket, Key=key, Body=io.BytesIO(payload),
                                     ContentLength=len(payload), ContentType='application/json',
                                     CacheControl='private, no-store', IfNoneMatch='*')
        _require(response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200)
    except ClientError as exc:
        if str(exc.response.get('Error', {}).get('Code')) not in {'412', 'PreconditionFailed'}:
            raise
        _require(_read_json(client, key, checksum, len(payload)) == receipt)
    return {'version': 1, 'source_task_id': SOURCE, 'prepared_key': key,
            'prepared_sha256': checksum, 'prepared_size': len(payload)}


def _store_audit(client, work, audit):
    payload = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(1 <= len(payload) <= MAX_JSON)
    checksum = hashlib.sha256(payload).hexdigest()
    # Local evidence survives a failed/uncertain private Storage response.
    with (work / f'failed-visual-audit-{checksum}.json').open('xb') as output:
        output.write(payload)
    key = f'recovery/{SOURCE}/failed_visual_audit_v1-{checksum}.json'
    try:
        response = client.put_object(Bucket=storage.settings.bucket, Key=key, Body=io.BytesIO(payload),
            ContentLength=len(payload), ContentType='application/json', CacheControl='private, no-store', IfNoneMatch='*')
        _require(response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200)
    except ClientError as exc:
        if str(exc.response.get('Error', {}).get('Code')) not in {'412', 'PreconditionFailed'}:
            raise
        _require(_read_json(client, key, checksum, len(payload)) == audit)
    return {'version': 1, 'source_task_id': SOURCE, 'key': key, 'sha256': checksum, 'size': len(payload)}


def _read_audit(client, pointer):
    _require(isinstance(pointer, dict) and set(pointer) == {'version', 'source_task_id', 'key', 'sha256', 'size'}
             and type(pointer.get('version')) is int and pointer['version'] == 1 and pointer.get('source_task_id') == SOURCE
             and pointer.get('key') == f"recovery/{SOURCE}/failed_visual_audit_v1-{pointer.get('sha256')}.json")
    audit = _read_json(client, pointer['key'], pointer['sha256'], pointer['size'])
    _require(audit.get('source_task_id') == SOURCE and audit.get('diagnostic_only') is True
             and audit.get('qa_approved') is False and audit.get('reusable') is False
             and audit.get('requires_full_qa') is True and audit.get('status') == 'retained_visuals_passed')
    return audit


def _shooting_candidate(package, repairs):
    candidate = deepcopy(package)
    if repairs == THREE_REPAIRS:
        for index in repairs:
            candidate['scenes'][index]['ai_prompt'] = REPAIR_SHOTS[index]
    return candidate


def _require_visual_partition(original, reviewed, repairs):
    require_unchanged_voice_narration(original, reviewed)
    if repairs == THREE_REPAIRS:
        for index, before in enumerate(original['scenes']):
            expected = {**before, 'ai_prompt': REPAIR_SHOTS[index]} if index in repairs else before
            _require(reviewed['scenes'][index] == expected)


def prepare_failed_visual_repair(source_task_id, retrieval_pointer, work_dir, *, repair_scene_indices=(0, 5)):
    """Review exact retained cuts once, then store a private v2 or v4 receipt.

    Call only explicitly with a fresh existing /tmp/youtube_factory/<UUID>_attempt_0
    directory. A rejected retained clip reports sanitized evidence and returns
    no checkpoint. Only (0,5) or explicit (0,2,5) is accepted. No media is created.
    """
    audit_pointer = None
    try:
        repairs = tuple(repair_scene_indices)
        _require(source_task_id == SOURCE and repairs in (REPAIRS, THREE_REPAIRS)
                 and all(type(index) is int for index in repair_scene_indices))
        retained = tuple(index for index in range(6) if index not in repairs)
        source, fingerprint = _state(studio_state._client())
        work = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', work.name)
        _require(match is not None and _canonical_id(match[1]) != SOURCE and not any(work.iterdir()))
        candidate = load_voice_retry_candidate(SOURCE, match[1], source['audio_candidate_checkpoint'], work)
        client = storage._client()
        metadata, mapped = _evidence(client, retrieval_pointer, source)
        mapped = {index: mapped[index] for index in retained}
        scenes, voice_result = candidate['package']['scenes'], candidate['voice_result']
        _require(len(scenes) == 6 and candidate['audio_sha256'] == VOICE_SHA)
        _require(all(row['narration'] == _text(scene['narration'], 4000)
                     and row['duration_seconds'] == voice_result['scene_durations'][index]
                     for index, (row, scene) in enumerate(zip(metadata['scenes'], scenes))))
        tasks, director, voice = _runtime()
        options = _options(source)
        _require(tasks._normalized_options(options, 0.5) == options)
        threshold = options.get('quality_threshold')
        _require(type(threshold) is int and 1 <= threshold <= 100)
        _require(voice_result['spoken_texts'] == [voice.normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
                                                for index, scene in enumerate(scenes)])
        paths = {}
        for index, row in mapped.items():
            target = work / f'existing-scene-{index:02d}.mp4'
            checksum, _size = _download_bounded(client, row['clip_key'], target, MAX_CLIP, expected_size=row['clip_size'])
            _require(checksum == row['clip_sha256'])
            tasks._validate_recovered_generated_clip(target,
                minimum_duration=max(5.0, float(voice_result['scene_durations'][index]) + 0.35),
                expected_size=row['clip_size'], expected_sha256=row['clip_sha256'])
            _require(abs(float(tasks.media_duration(target)) - row['duration_seconds']) <= 0.04
                     and tasks.video_frame_count(target) == row['video_frames'])
            paths[index] = target
        audit = {'version': 1, 'source_task_id': SOURCE, 'diagnostic_only': True, 'qa_approved': False,
                 'reusable': False, 'requires_full_qa': True, 'status': 'story_review_pending',
                 'source_state_sha256': fingerprint, 'source_spec_sha256': _digest(source['spec']),
                 'source_package': deepcopy(candidate['package']), 'repair_scene_indices': list(repairs),
                 'retrieval_pointer': deepcopy(retrieval_pointer), 'workprint_metadata_sha256': WORKPRINT_SHA,
                 'voice_sha256': VOICE_SHA, 'retained_visual_reviews': [], 'new_paid_create_requests': 0, 'new_tts_requests': 0}
        try:
            reviewed = director.revalidate_immutable_short_story(_shooting_candidate(candidate['package'], repairs),
                source['spec']['topic'], 0.5, 'tr', options,
                immutable_candidate_narrations=[scene['narration'] for scene in scenes])
            _require_visual_partition(candidate['package'], reviewed, repairs)
            _require(reviewed.get('studio_options') == options and director.short_story_package_is_approved(reviewed, source['spec']['topic']))
        except Exception:
            audit['status'] = 'story_review_rejected_or_unavailable'
            audit_pointer = _store_audit(client, work, audit)
            raise FailedVisualRecoveryError(diagnostic_pointer=audit_pointer) from None
        audit['reviewed_package'] = deepcopy(reviewed)
        try:
            evidence = _review_existing({**reviewed, '_recovery_topic': source['spec']['topic']}, voice_result, paths, work, threshold, retained)
        except Exception:
            audit['status'] = 'visual_review_unavailable'
            audit_pointer = _store_audit(client, work, audit)
            raise FailedVisualRecoveryError(diagnostic_pointer=audit_pointer) from None
        rejected = [row for row in evidence if not _review_passes(row['review'], threshold)]
        audit.update(status='retained_visuals_rejected' if rejected else 'retained_visuals_passed', retained_visual_reviews=evidence)
        audit_pointer = _store_audit(client, work, audit)
        if rejected:
            raise FailedVisualRecoveryError(diagnostics=rejected, diagnostic_pointer=audit_pointer)
        package = deepcopy(reviewed)
        package_hash = tasks._recovery_package_sha256(package)
        voice_path = Path(voice_result['path'])
        audio = {'version': 1, 'source_task_id': SOURCE, 'package_sha256': package_hash,
                 'key': f'recovery/{SOURCE}/raw/voice.mp3', 'sha256': VOICE_SHA, 'size': VOICE_SIZE,
                 **{key: voice_result.get(key) for key in ('scene_durations', 'spoken_texts', 'duration_before_fit',
                    'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')}}
        media = {'version': 4 if repairs == THREE_REPAIRS else 2, 'repair_only': True, 'source_task_id': SOURCE, 'package_sha256': package_hash,
                 'repair_scene_indices': list(repairs), 'scenes': {str(index): [{
                     'key': row['clip_key'], 'sha256': row['clip_sha256'], 'size': row['clip_size'],
                     'provider': 'gemini_veo', 'provider_attempts': 1, 'synthetic_motion_only': False,
                     'motion_recipe_version': None, 'source_media_type': 'video'}] for index, row in mapped.items()}}
        tasks._validated_recovered_voice(audio, 6, package_hash)
        tasks._validated_recovered_generated_media(media, 6, package_hash)
        _require(_state(studio_state._client())[1] == fingerprint)
        _copy_voice_create_only(client, voice_path, audio['key'], VOICE_SHA, work)
        copied_sha, _size = _download_bounded(client, audio['key'], work / 'verified_recovery_voice.mp3',
                                              VOICE_SIZE, expected_size=VOICE_SIZE)
        _require(copied_sha == VOICE_SHA)
        package.update(_recovered_voice=audio, _recovered_generated_media=media)
        receipt = {'version': 1, 'source_task_id': SOURCE, 'status': 'prepared_visual_repair',
                   'qa_approved': False, 'requires_full_qa': True, 'new_paid_create_requests': 0, 'new_tts_requests': 0,
                   'source_state_sha256': fingerprint, 'source_spec_sha256': _digest(source['spec']),
                   'retrieval_pointer': deepcopy(retrieval_pointer), 'workprint_metadata_sha256': WORKPRINT_SHA,
                   'package_sha256': package_hash, 'approved_package': package, 'retained_visual_reviews': evidence,
                   'audit_pointer': audit_pointer}
        return _store_prepared(client, receipt)
    except FailedVisualRecoveryError:
        raise
    except Exception:
        raise FailedVisualRecoveryError(diagnostic_pointer=audit_pointer) from None


def publish_failed_visual_repair(prepared_receipt):
    """CAS-publish one stored receipt; ordinary Studio must separately claim it."""
    try:
        pointer = prepared_receipt
        _require(isinstance(pointer, dict) and set(pointer) == {'version', 'source_task_id', 'prepared_key', 'prepared_sha256', 'prepared_size'}
                 and type(pointer.get('version')) is int and pointer['version'] == 1 and pointer.get('source_task_id') == SOURCE
                 and pointer.get('prepared_key') == f"recovery/{SOURCE}/failed_visual_prepared_v1-{pointer.get('prepared_sha256')}.json")
        storage_client = storage._client()
        receipt = _read_json(storage_client, pointer['prepared_key'], pointer['prepared_sha256'], pointer['prepared_size'])
        _require(set(receipt) == {'version', 'source_task_id', 'status', 'qa_approved', 'requires_full_qa',
                    'new_paid_create_requests', 'new_tts_requests', 'source_state_sha256', 'source_spec_sha256',
                    'retrieval_pointer', 'workprint_metadata_sha256', 'package_sha256', 'approved_package', 'retained_visual_reviews', 'audit_pointer'}
                 and type(receipt.get('version')) is int and receipt['version'] == 1 and receipt.get('source_task_id') == SOURCE
                 and receipt.get('status') == 'prepared_visual_repair' and receipt.get('qa_approved') is False
                 and receipt.get('requires_full_qa') is True and receipt.get('workprint_metadata_sha256') == WORKPRINT_SHA
                 and all(type(receipt.get(key)) is int and receipt[key] == 0 for key in ('new_paid_create_requests', 'new_tts_requests')))
        with studio_state._client().pipeline() as transaction:
            transaction.watch(*_keys())
            source, fingerprint = _state(transaction)
            _require(fingerprint == receipt['source_state_sha256'] and _digest(source['spec']) == receipt['source_spec_sha256'])
            metadata, mapped = _evidence(storage_client, receipt['retrieval_pointer'], source)
            tasks, director, voice = _runtime()
            package, package_hash = receipt['approved_package'], receipt['package_sha256']
            _require(isinstance(package, dict) and len(package.get('scenes', [])) == 6
                     and tasks._recovery_package_sha256(package) == package_hash
                     and package.get('studio_options') == _options(source)
                     and director.short_story_package_is_approved(package, source['spec']['topic']))
            audio = tasks._validated_recovered_voice(package.get('_recovered_voice'), 6, package_hash)
            media = tasks._validated_recovered_generated_media(package.get('_recovered_generated_media'), 6, package_hash)
            repairs = tuple(media.get('repair_scene_indices') or ()) if media else ()
            retained = tuple(index for index in range(6) if index not in repairs)
            _require(audio and media and repairs in (REPAIRS, THREE_REPAIRS)
                     and media.get('version') == (4 if repairs == THREE_REPAIRS else 2) and media.get('repair_only') is True
                     and media.get('source_task_id') == audio.get('source_task_id') == SOURCE
                     and set(media['scenes']) == set(retained)
                     and audio['sha256'] == VOICE_SHA and audio['size'] == VOICE_SIZE)
            audit = _read_audit(storage_client, receipt['audit_pointer'])
            _require(audit.get('source_state_sha256') == fingerprint
                     and audit.get('source_spec_sha256') == receipt['source_spec_sha256']
                     and audit.get('retrieval_pointer') == receipt['retrieval_pointer']
                     and audit.get('workprint_metadata_sha256') == WORKPRINT_SHA and audit.get('voice_sha256') == VOICE_SHA
                     and audit.get('repair_scene_indices') == list(repairs)
                     and audit.get('retained_visual_reviews') == receipt['retained_visual_reviews']
                     and audit.get('reviewed_package') == {key: value for key, value in package.items()
                         if key not in {'_recovered_voice', '_recovered_generated_media'}}
                     and _digest(audio_checkpoint._candidate_package(audit.get('source_package'))) == source['audio_candidate_checkpoint']['package_sha256'])
            _require_visual_partition(audit['source_package'], package, repairs)
            _require(audio['spoken_texts'] == [voice.normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
                                              for index, scene in enumerate(package['scenes'])])
            _require(all(row['narration'] == _text(scene['narration'], 4000)
                         and row['duration_seconds'] == audio['scene_durations'][index]
                         for index, (row, scene) in enumerate(zip(metadata['scenes'], package['scenes']))))
            reviews = receipt['retained_visual_reviews']
            _require(isinstance(reviews, list) and len(reviews) == len(retained))
            for index, evidence in zip(retained, reviews):
                row, entries = mapped[index], media['scenes'][index]
                _require(len(entries) == 1 and entries[0] == {
                    'key': row['clip_key'], 'sha256': row['clip_sha256'], 'size': row['clip_size'],
                    'provider': 'gemini_veo', 'provider_attempts': 1, 'synthetic_motion_only': False,
                    'motion_recipe_version': None, 'source_media_type': 'video'})
                _require(isinstance(evidence, dict) and evidence.get('scene_index') == index
                         and evidence.get('raw_sha256') == SELECTED[index][0]
                         and type(evidence.get('exact_cut_frames')) is int and evidence['exact_cut_frames'] > 0
                         and _review_passes(evidence.get('review'), _options(source)['quality_threshold']))
            source['repair_available'] = True
            source['updated_at'] = datetime.now(timezone.utc).isoformat()
            transaction.multi()
            transaction.set(studio_state.REPAIR_CHECKPOINT_PREFIX + SOURCE,
                            json.dumps(receipt, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(studio_state.JOB_PREFIX + SOURCE,
                            json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            ex=studio_state.JOB_TTL_SECONDS)
            _require(transaction.execute() == [True, True])
        return {'status': 'checkpoint_published', 'source_task_id': SOURCE, 'requires_full_qa': True,
                'repair_scene_indices': list(repairs), 'new_paid_create_requests': 0}
    except Exception:
        raise FailedVisualRecoveryError() from None
