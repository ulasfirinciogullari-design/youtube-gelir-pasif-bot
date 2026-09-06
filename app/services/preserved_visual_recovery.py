"""Explicit recovery preparation from a complete private raw-candidate journal.

Only six initial generated video candidates are supported. A journal proves
stored bytes, not quality. Fresh immutable story and exact-cut visual reviews
are durably recorded even on rejection. Only a separate explicit publication
step exposes a one-use v3 checkpoint, or an explicitly selected v4 repair
partition. Preparation creates no media; all ordinary worker QA remains mandatory.
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

from app.services import audio_checkpoint, generated_asset_checkpoint as assets, storage, studio_state
from app.services.failed_visual_recovery import _read_json, _review_passes, _review_runtime
from app.services.paid_render_recovery import _canonical_id, _digest, _runtime
from app.services.visual_allocation_checkpoint import _review
from app.services.voice_candidate_recovery import _download_bounded, load_voice_retry_candidate, require_unchanged_voice_narration


MAX_REPORT_BYTES = 512 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_FLAGS = {'version': 1, 'diagnostic_only': True, 'qa_approved': False,
          'reusable': False, 'requires_full_qa': True}
_POINTER_FIELDS = set(_FLAGS) | {
    'source_task_id', 'status', 'scene_index', 'phase', 'provider', 'raw_key', 'raw_sha256',
    'raw_size', 'audio_sha256', 'package_sha256', 'manifest_key', 'manifest_sha256', 'manifest_size',
}


class PreservedVisualRecoveryError(RuntimeError):
    """Safe failure; diagnostic_pointer is not a retry or publication grant."""

    def __init__(self, diagnostic_pointer=None):
        super().__init__('Preserved visual recovery stopped; no new media or retry was created')
        self.diagnostic_pointer = deepcopy(diagnostic_pointer)


def _require(condition):
    if not condition:
        raise ValueError('Invalid preserved visual recovery evidence')


def _flags(value):
    return isinstance(value, dict) and all(type(value.get(key)) is type(expected) and value[key] == expected
                                          for key, expected in _FLAGS.items())


def _keys(source_id):
    return tuple(prefix + source_id for prefix in (
        studio_state.JOB_PREFIX, studio_state.PAID_CREATE_BUDGET_PREFIX,
        studio_state.RETRY_DISPATCH_PREFIX, studio_state.REPAIR_CHECKPOINT_PREFIX,
        studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX,
    ))


def _options(source):
    return {key: value for key, value in source['spec'].items()
            if key not in {'topic', 'duration_minutes', 'language', 'channel_id'}}


def _repair_request(indices, overrides):
    _require(type(indices) is tuple and len(indices) <= 4
             and all(type(index) is int and 0 <= index < 6 for index in indices)
             and list(indices) == sorted(set(indices)))
    overrides = {} if overrides is None else overrides
    _require(type(overrides) is dict and len(overrides) <= len(indices))
    for index, prompt in overrides.items():
        _require(type(index) is int and index in indices and isinstance(prompt, str)
                 and bool(prompt.strip()) and prompt == prompt.strip()
                 and not any(ord(character) < 32 for character in prompt))
        audio_checkpoint._plain_text(prompt, 4000)
    return indices, dict(overrides)


def _repair_fields(indices, overrides):
    return ({'repair_scene_indices': list(indices),
             'shot_prompt_overrides': {str(index): prompt for index, prompt in overrides.items()}}
            if indices else {})


def _record_repairs(record):
    fields = {'repair_scene_indices', 'shot_prompt_overrides'}
    if not fields.intersection(record):
        return (), {}
    _require(fields <= set(record) and type(record['repair_scene_indices']) is list
             and bool(record['repair_scene_indices']) and type(record['shot_prompt_overrides']) is dict
             and all(type(key) is str and re.fullmatch(r'[0-5]', key)
                     for key in record['shot_prompt_overrides']))
    return _repair_request(tuple(record['repair_scene_indices']),
                           {int(key): value for key, value in record['shot_prompt_overrides'].items()})


def _shooting_package(package, overrides):
    candidate = deepcopy(package)
    for index, prompt in overrides.items():
        candidate['scenes'][index]['ai_prompt'] = prompt
    candidate['narration'] = ' '.join(scene['narration'] for scene in candidate['scenes'])
    return candidate


def _observed_review(review):
    return (isinstance(review, dict) and type(review.get('score')) in (int, float)
            and math.isfinite(review['score']) and 0 <= review['score'] <= 100
            and type(review.get('best_candidate_index')) is int and review['best_candidate_index'] == 0
            and all(type(review.get('gates', {}).get(key)) is bool for key in (
                'evidence_gate_passed', 'identity_gate_passed', 'editorial_gate_passed')))


def _state(source_id, client):
    source = json.loads(client.get(studio_state.JOB_PREFIX + source_id) or 'null')
    _require(isinstance(source, dict) and source.get('task_id') == source_id
             and source.get('kind') == 'render' and source.get('state') == 'FAILURE'
             and source.get('failure_stage') in {'final_visual_qc', 'final_visual_qc_rescue'}
             and not any(source.get(key) for key in ('retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available'))
             and not client.exists(*_keys(source_id)[2:]))
    spec = source.get('spec')
    _require(isinstance(spec, dict) and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
             and type(spec.get('duration_minutes')) in (int, float) and spec['duration_minutes'] == 0.5
             and spec.get('music') == 'off' and spec.get('language') in {'tr', 'en'}
             and isinstance(spec.get('topic'), str) and bool(spec['topic'].strip()))
    if spec.get('publish_after_render') is True:
        _require(all(isinstance(spec.get(key), str) and bool(spec[key].strip()) for key in (
            'production_channel_id', 'production_connection_id', 'production_profile_revision')))
    ledger = client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + source_id)
    _require(isinstance(ledger, dict) and set(ledger) == {'cap', 'used'} and ledger['used'] == '6'
             and isinstance(ledger['cap'], str) and re.fullmatch(r'[0-9]{1,3}', ledger['cap'])
             and 6 <= int(ledger['cap']) <= 100
             and type(source.get('paid_create_slots_used')) is int and source['paid_create_slots_used'] == 6
             and type(source.get('preview_total_paid_create_cap')) is int and source['preview_total_paid_create_cap'] == int(ledger['cap']))
    journal = source.get('generated_asset_candidates')
    _require(_flags(journal) and journal.get('source_task_id') == source_id and journal.get('status') == 'candidate_journal'
             and all(type(journal.get(key)) is int and journal[key] == value
                     for key, value in {'attempted_count': 6, 'preserved_count': 6, 'failed_count': 0}.items())
             and isinstance(journal.get('entries'), list) and len(journal['entries']) == 6)
    seen_raw, seen_manifest, seen_indices, packages, voices = set(), set(), set(), set(), set()
    for pointer in journal['entries']:
        _require(_flags(pointer) and set(pointer) == _POINTER_FIELDS
                 and pointer.get('source_task_id') == source_id and pointer.get('status') == 'preserved_candidate'
                 and type(pointer.get('scene_index')) is int and 0 <= pointer['scene_index'] < 6
                 and pointer['scene_index'] not in seen_indices
                 and pointer.get('phase') == 'initial_generation'
                 and pointer.get('provider') in assets.PROVIDERS - {'gemini_image_motion'}
                 and all(isinstance(pointer.get(key), str) and _SHA.fullmatch(pointer[key])
                         for key in ('raw_sha256', 'audio_sha256', 'package_sha256', 'manifest_sha256'))
                 and pointer.get('raw_key') == f"generated_candidates/{source_id}/raw/{pointer['raw_sha256']}.mp4"
                 and pointer.get('manifest_key') == f"generated_candidates/{source_id}/manifests/{pointer['manifest_sha256']}.json"
                 and type(pointer.get('raw_size')) is int and 1024 <= pointer['raw_size'] <= assets.MAX_RAW_BYTES
                 and type(pointer.get('manifest_size')) is int and 1 <= pointer['manifest_size'] <= assets.MAX_MANIFEST_BYTES
                 and pointer['raw_sha256'] not in seen_raw and pointer['manifest_sha256'] not in seen_manifest)
        seen_raw.add(pointer['raw_sha256']); seen_manifest.add(pointer['manifest_sha256'])
        seen_indices.add(pointer['scene_index'])
        packages.add(pointer['package_sha256']); voices.add(pointer['audio_sha256'])
    audio = source.get('audio_candidate_checkpoint')
    _require(len(packages) == len(voices) == 1 and isinstance(audio, dict)
             and audio.get('audio_sha256') in voices and audio.get('qa_approved') is False
             and audio.get('requires_full_qa') is True and not source.get('audio_candidate_checkpoint_error'))
    return source, _digest({'source': source, 'ledger': ledger})


def _manifests(client, source):
    source_id, records = source['task_id'], []
    for pointer in sorted(source['generated_asset_candidates']['entries'], key=lambda item: item['scene_index']):
        value = _read_json(client, pointer['manifest_key'], pointer['manifest_sha256'], pointer['manifest_size'], assets.MAX_MANIFEST_BYTES)
        _require(_flags(value) and set(value) == set(_FLAGS) | {
            'source_task_id', 'status', 'scene_index', 'phase', 'package_sha256', 'candidate_package_sha256',
            'package', 'voice', 'audio', 'raw'}
            and value.get('source_task_id') == source_id and value.get('status') == 'preserved_candidate'
            and type(value.get('scene_index')) is int and value['scene_index'] == pointer['scene_index']
            and value.get('phase') == 'initial_generation' and value.get('package_sha256') == pointer['package_sha256'])
        package, audio, raw = value['package'], value['audio'], value['raw']
        _require(isinstance(package, dict) and audio_checkpoint._candidate_package(package) == package
                 and len(package['scenes']) == 6 and _digest(package) == value.get('candidate_package_sha256')
                 and isinstance(audio, dict) and set(audio) == {'key', 'sha256', 'size'}
                 and audio['key'] == f"generated_candidates/{source_id}/voice/{pointer['audio_sha256']}.mp3"
                 and audio['sha256'] == pointer['audio_sha256']
                 and type(audio['size']) is int and audio['size'] == source['audio_candidate_checkpoint']['size']
                 and isinstance(raw, dict) and set(raw) == {'key', 'sha256', 'size', 'provider', 'provider_attempts',
                                                          'start_fraction', 'forbid_loop', 'synthetic_motion_only'}
                 and raw['key'] == pointer['raw_key'] and raw['sha256'] == pointer['raw_sha256'] and raw['size'] == pointer['raw_size']
                 and raw['provider'] == pointer['provider'] and type(raw['provider_attempts']) is int
                 and 1 <= raw['provider_attempts'] <= 10 and type(raw['start_fraction']) in (int, float)
                 and raw['start_fraction'] == 0 and raw['forbid_loop'] is True and raw['synthetic_motion_only'] is False)
        if records:
            _require(value['package'] == records[0]['package'] and value['voice'] == records[0]['voice']
                     and value['audio'] == records[0]['audio'])
        records.append(value)
    return records


def _store(client, source_id, work, kind, record):
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(1 <= len(payload) <= MAX_REPORT_BYTES)
    checksum = hashlib.sha256(payload).hexdigest()
    # This local copy survives a Storage failure and is written BEFORE an
    # exception can hide a completed critic response from the operator.
    if work is not None:
        with (work / f'{kind}-{checksum}.json').open('xb') as output:
            output.write(payload)
    key = f'recovery/{source_id}/preserved_visual/{kind}-{checksum}.json'
    assets._put_immutable(client, key, io.BytesIO(payload), checksum, len(payload), 'application/json')
    return {'version': 1, 'source_task_id': source_id, 'kind': kind,
            'key': key, 'sha256': checksum, 'size': len(payload)}


def _read_record(client, pointer, kind):
    _require(isinstance(pointer, dict) and set(pointer) == {'version', 'source_task_id', 'kind', 'key', 'sha256', 'size'}
             and type(pointer.get('version')) is int and pointer['version'] == 1 and pointer.get('kind') == kind)
    source_id = _canonical_id(pointer['source_task_id'])
    _require(pointer.get('key') == f"recovery/{source_id}/preserved_visual/{kind}-{pointer.get('sha256')}.json")
    return _read_json(client, pointer['key'], pointer['sha256'], pointer['size'], MAX_REPORT_BYTES)


def _exact_review(package, voice, paths, work, topic):
    render, reviewer = _review_runtime()
    measured = float(render.media_duration(voice['path']))
    _require(math.isfinite(measured) and 28.7 <= measured <= 30.08
             and abs(measured - voice['duration_after_fit']) <= 0.12)
    pools = [[{'path': str(path), 'generated': True, 'source_type': 'generated',
               'generation_provider': provider, 'start_fraction': 0.0,
               'preserve_start_fraction': True, 'forbid_loop': True}] for path, provider in paths]
    timeline = render._scene_timeline(package['scenes'], pools, voice['scene_durations'], measured, [])
    _require(len(timeline) == 6 and [item[3] for item in timeline] == list(range(6)))
    counts = render._timeline_frame_counts(timeline, measured)
    directory = work / 'preserved_exact_review'
    directory.mkdir(exist_ok=False)
    inputs = []
    for index, (spec, _duration, transition, _owner) in enumerate(timeline):
        target = directory / f'scene-{index:02d}.mp4'
        render.normalize_clip(spec, target, counts[index] / render.FPS, index, transition, '1080x1920')
        inputs.append([{**spec, 'path': str(target)}])
    result = reviewer(package['scenes'], inputs, directory, 6, _missing_review_attempts=0,
                      _score_reason_consistency_attempts=0, topic=topic, story_scenes=package['scenes'],
                      content_style=package['studio_options'].get('content_style', ''), evidence_sources=package.get('sources') or [])
    return result, counts


def _reports(result, counts, manifests, threshold, repairs=()):
    # Preserve every available normalized reviewer result, including a valid
    # negative. Missing/duplicate/out-of-range identities cannot approve it.
    reviews = result.get('reviews') if isinstance(result, dict) else None
    valid_shape = isinstance(reviews, list) and len(reviews) == 6 and not result.get('missing_review_indices')
    by_index = {}
    for raw in reviews or []:
        if not isinstance(raw, dict) or type(raw.get('scene_index')) is not int or not 0 <= raw['scene_index'] < 6:
            valid_shape = False
            continue
        index = raw['scene_index']
        if index in by_index:
            valid_shape = False
        by_index[index] = _review(raw)
    reports = [{'scene_index': index, 'raw_sha256': manifests[index]['raw']['sha256'],
                'exact_cut_frames': counts[index], 'missing_review': index not in by_index,
                'review': by_index.get(index, {})} for index in range(6)]
    return reports, bool(valid_shape and all(
        _observed_review(row['review']) if row['scene_index'] in repairs
        else _review_passes(row['review'], threshold) for row in reports))


def prepare_preserved_visual_recovery(source_task_id, work_dir, *, repair_scene_indices=(), shot_prompt_overrides=None):
    """One explicit, fresh-directory preparation; no TTS, paid video or dispatch."""
    audit_pointer = None
    try:
        repairs, overrides = _repair_request(repair_scene_indices, shot_prompt_overrides)
        source_id = _canonical_id(source_task_id)
        source, fingerprint = _state(source_id, studio_state._client())
        work = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', work.name)
        _require(match is not None and _canonical_id(match[1]) != source_id and not any(work.iterdir()))
        candidate = load_voice_retry_candidate(source_id, match[1], source['audio_candidate_checkpoint'], work)
        client = storage._client()
        manifests = _manifests(client, source)
        voice, options, spec = candidate['voice_result'], _options(source), source['spec']
        _require(audio_checkpoint._candidate_voice(voice, 6) == manifests[0]['voice'])
        require_unchanged_voice_narration(candidate['package'], manifests[0]['package'])
        tasks, director, voice_module = _runtime()
        _require(tasks._normalized_options(options, 0.5) == options
                 and type(options.get('quality_threshold')) is int and 1 <= options['quality_threshold'] <= 100)
        spoken = [voice_module.normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
                  if spec['language'] == 'tr' else scene['narration'].strip()
                  for index, scene in enumerate(candidate['package']['scenes'])]
        _require(voice['spoken_texts'] == spoken)
        paths = []
        for index, manifest in enumerate(manifests):
            raw = manifest['raw']
            path = work / f'preserved-raw-{index:02d}.mp4'
            checksum, _size = _download_bounded(client, raw['key'], path, assets.MAX_RAW_BYTES, expected_size=raw['size'])
            _require(checksum == raw['sha256'])
            tasks._validate_recovered_generated_clip(path,
                minimum_duration=max(5.0, float(voice['scene_durations'][index]) + 0.35),
                expected_size=raw['size'], expected_sha256=raw['sha256'])
            paths.append((path, raw['provider']))
        audit = {**_FLAGS, 'source_task_id': source_id, 'status': 'story_review_pending',
                 'source_state_sha256': fingerprint, 'source_spec_sha256': _digest(spec),
                 'journal_sha256': _digest(source['generated_asset_candidates']),
                 'audio_sha256': candidate['audio_sha256'], 'package': deepcopy(manifests[0]['package']),
                 'retained_visual_reviews': [], 'new_paid_create_requests': 0, 'new_tts_requests': 0,
                 **_repair_fields(repairs, overrides)}
        try:
            shooting_package = _shooting_package(manifests[0]['package'], overrides)
            reviewed = director.revalidate_immutable_short_story(shooting_package, spec['topic'], 0.5, spec['language'], options,
                          immutable_candidate_narrations=[scene['narration'] for scene in candidate['package']['scenes']])
            require_unchanged_voice_narration(candidate['package'], reviewed)
            if repairs:
                _require(reviewed['scenes'] == _shooting_package(manifests[0]['package'], overrides)['scenes'])
            _require(reviewed.get('studio_options') == options and director.short_story_package_is_approved(reviewed, spec['topic']))
        except Exception:
            audit['status'] = 'story_review_rejected_or_unavailable'
            audit_pointer = _store(client, source_id, work, 'audit', audit)
            raise PreservedVisualRecoveryError(audit_pointer) from None
        audit['package'] = deepcopy(reviewed)
        try:
            result, counts = _exact_review(reviewed, voice, paths, work, spec['topic'])
            reports, passed = _reports(result, counts, manifests, options['quality_threshold'], repairs)
        except Exception:
            audit['status'] = 'visual_review_unavailable'
            audit_pointer = _store(client, source_id, work, 'audit', audit)
            raise PreservedVisualRecoveryError(audit_pointer) from None
        audit.update(status=('retained_visual_preparation_passed' if repairs else 'visual_preparation_passed')
                     if passed else 'visual_preparation_rejected', retained_visual_reviews=reports)
        audit_pointer = _store(client, source_id, work, 'audit', audit)
        if not passed:
            raise PreservedVisualRecoveryError(audit_pointer)
        _require(_state(source_id, studio_state._client())[1] == fingerprint)
        package = deepcopy(reviewed)
        package_hash = tasks._recovery_package_sha256(package)
        audio = {'version': 1, 'source_task_id': source_id, 'package_sha256': package_hash,
                 'key': f'recovery/{source_id}/raw/voice.mp3', 'sha256': candidate['audio_sha256'],
                 'size': source['audio_candidate_checkpoint']['size'],
                 **{key: voice.get(key) for key in ('spoken_texts', 'scene_durations', 'duration_before_fit',
                    'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')}}
        media = {**({'version': 4, 'repair_only': True, 'repair_scene_indices': list(repairs)}
                    if repairs else {'version': 3, 'recovery_only': True}), 'source_task_id': source_id,
                 'package_sha256': package_hash, 'scenes': {str(index): [{
                     'key': f'recovery/{source_id}/raw/scene-{index:02d}-initial.mp4',
                     'sha256': row['raw']['sha256'], 'size': row['raw']['size'], 'provider': row['raw']['provider'],
                     'provider_attempts': row['raw']['provider_attempts'], 'synthetic_motion_only': False,
                     'motion_recipe_version': None, 'source_media_type': 'video'}]
                     for index, row in enumerate(manifests) if index not in repairs}}
        tasks._validated_recovered_voice(audio, 6, package_hash)
        tasks._validated_recovered_generated_media(media, 6, package_hash)
        for index, (path, _provider) in enumerate(paths):
            if index in repairs:
                continue
            entry = media['scenes'][str(index)][0]
            with path.open('rb') as incoming:
                assets._put_immutable(client, entry['key'], incoming, entry['sha256'], entry['size'], 'video/mp4')
        with Path(voice['path']).open('rb') as incoming:
            assets._put_immutable(client, audio['key'], incoming, audio['sha256'], audio['size'], 'audio/mpeg')
        _require(_state(source_id, studio_state._client())[1] == fingerprint)
        package.update(_recovered_voice=audio, _recovered_generated_media=media)
        receipt = {**_FLAGS, 'source_task_id': source_id,
                   'status': 'prepared_bounded_repair' if repairs else 'prepared_zero_create',
                   'source_state_sha256': fingerprint, 'source_spec_sha256': _digest(spec),
                   'journal_sha256': _digest(source['generated_asset_candidates']), 'package_sha256': package_hash,
                   'audit_pointer': audit_pointer, 'approved_package': package,
                   'new_paid_create_requests': 0, 'new_tts_requests': 0, **_repair_fields(repairs, overrides)}
        return _store(client, source_id, work, 'prepared', receipt)
    except PreservedVisualRecoveryError:
        raise
    except Exception:
        raise PreservedVisualRecoveryError(audit_pointer) from None


def publish_preserved_visual_recovery(prepared_pointer):
    """Publish a one-use private checkpoint only; never enqueue or reset claims."""
    try:
        client = storage._client()
        receipt = _read_record(client, prepared_pointer, 'prepared')
        source_id = prepared_pointer['source_task_id']
        repairs, overrides = _record_repairs(receipt)
        _require(_flags(receipt) and receipt.get('status') == ('prepared_bounded_repair' if repairs else 'prepared_zero_create')
                 and receipt.get('source_task_id') == source_id
                 and all(type(receipt.get(key)) is int and receipt[key] == 0 for key in ('new_paid_create_requests', 'new_tts_requests')))
        audit = _read_record(client, receipt['audit_pointer'], 'audit')
        _require(_flags(audit) and audit.get('source_task_id') == source_id
                 and audit.get('status') == ('retained_visual_preparation_passed' if repairs else 'visual_preparation_passed')
                 and _record_repairs(audit) == (repairs, overrides))
        with studio_state._client().pipeline() as transaction:
            transaction.watch(*_keys(source_id))
            source, fingerprint = _state(source_id, transaction)
            _require(receipt.get('source_state_sha256') == audit.get('source_state_sha256') == fingerprint
                     and receipt.get('source_spec_sha256') == audit.get('source_spec_sha256') == _digest(source['spec'])
                     and receipt.get('journal_sha256') == audit.get('journal_sha256') == _digest(source['generated_asset_candidates']))
            manifests = _manifests(client, source)
            tasks, director, _voice_module = _runtime()
            package, package_hash = receipt['approved_package'], receipt['package_sha256']
            _require(isinstance(package, dict) and len(package.get('scenes', [])) == 6
                     and tasks._recovery_package_sha256(package) == package_hash
                     and package.get('studio_options') == _options(source)
                     and director.short_story_package_is_approved(package, source['spec']['topic'])
                     and {key: value for key, value in package.items() if key not in {'_recovered_voice', '_recovered_generated_media'}} == audit.get('package'))
            require_unchanged_voice_narration(manifests[0]['package'], package)
            if repairs:
                _require(package['scenes'] == _shooting_package(manifests[0]['package'], overrides)['scenes'])
            media = tasks._validated_recovered_generated_media(package.get('_recovered_generated_media'), 6, package_hash)
            voice = tasks._validated_recovered_voice(package.get('_recovered_voice'), 6, package_hash)
            _require(media and voice and media.get('version') == (4 if repairs else 3)
                     and (media.get('repair_only') is True and media.get('repair_scene_indices') == list(repairs)
                          if repairs else media.get('recovery_only') is True)
                     and media['source_task_id'] == voice['source_task_id'] == source_id
                     and set(media['scenes']) == set(range(6)) - set(repairs)
                     and voice['sha256'] == audit['audio_sha256'] == source['audio_candidate_checkpoint']['audio_sha256']
                     and voice['size'] == source['audio_candidate_checkpoint']['size'])
            for key in ('spoken_texts', 'scene_durations', 'duration_before_fit', 'duration_after_fit',
                        'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds'):
                _require(voice[key] == manifests[0]['voice'].get(key))
            reports = audit.get('retained_visual_reviews')
            _require(isinstance(reports, list) and len(reports) == 6)
            for index, (row, report) in enumerate(zip(manifests, reports)):
                raw = row['raw']
                expected = {'key': f'recovery/{source_id}/raw/scene-{index:02d}-initial.mp4',
                            'sha256': raw['sha256'], 'size': raw['size'], 'provider': raw['provider'],
                            'provider_attempts': raw['provider_attempts'], 'synthetic_motion_only': False,
                            'motion_recipe_version': None, 'source_media_type': 'video'}
                _require(isinstance(report, dict) and type(report.get('scene_index')) is int
                         and report['scene_index'] == index and report.get('raw_sha256') == raw['sha256']
                         and report.get('missing_review') is False and type(report.get('exact_cut_frames')) is int
                         and report['exact_cut_frames'] > 0)
                if index in repairs:
                    _require(_observed_review(report.get('review')))
                    continue
                _require(media['scenes'][index] == [expected]
                         and _review_passes(report.get('review'), _options(source)['quality_threshold']))
                assets._verify_existing(client, expected['key'], expected['sha256'], expected['size'], 'video/mp4')
            assets._verify_existing(client, voice['key'], voice['sha256'], voice['size'], 'audio/mpeg')
            source['repair_available'] = True
            source['updated_at'] = datetime.now(timezone.utc).isoformat()
            transaction.multi()
            transaction.set(studio_state.REPAIR_CHECKPOINT_PREFIX + source_id,
                            json.dumps(receipt, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(studio_state.JOB_PREFIX + source_id,
                            json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            ex=studio_state.JOB_TTL_SECONDS)
            _require(transaction.execute() == [True, True])
        return {'status': 'checkpoint_published', 'source_task_id': source_id,
                'requires_full_qa': True, 'new_paid_create_requests': 0, 'new_tts_requests': 0}
    except Exception:
        raise PreservedVisualRecoveryError() from None
