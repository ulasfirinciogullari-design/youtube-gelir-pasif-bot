"""CB-only mixed recovery: one real stock, one saved AI shot, four repairs.

Preparation reviews existing media and writes private immutable evidence only.
Publication reserves no render: the ordinary one-shot Studio claim is separate.
Neither path buys speech/video or imports an old score as current approval.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from app.services import audio_checkpoint, curated_stock as stock, failed_visual_recovery as cb
from app.services import preserved_visual_recovery as primitives, storage, studio_state
from app.services.voice_candidate_recovery import (
    _download_bounded, _work_directory, load_voice_retry_candidate, require_unchanged_voice_narration,
)


REPAIRS = (0, 2, 3, 4)
STOCK_QUERIES = ['warehouse worker scanning parcel barcode', 'active parcel conveyor barcode scanner warehouse']
_FLAGS = {'version': 1, 'diagnostic_only': True, 'qa_approved': False,
          'reusable': False, 'requires_full_qa': True}
_MEDIA_FIELDS = {'version', 'repair_only', 'source_task_id', 'package_sha256',
                 'repair_scene_indices', 'scenes', 'stock_scenes'}


class MixedVisualRecoveryError(RuntimeError):
    def __init__(self, diagnostic_pointer=None):
        super().__init__('Mixed visual recovery unavailable; no new media or retry was created')
        self.diagnostic_pointer = deepcopy(diagnostic_pointer)


def _require(value):
    if not value:
        raise ValueError('Mixed recovery evidence is invalid')


def _one(mapping, index):
    _require(type(mapping) is dict and len(mapping) == 1
             and (set(mapping) == {str(index)} or all(type(key) is int for key in mapping) and set(mapping) == {index}))
    return mapping.get(str(index), mapping.get(index))


def validate_mixed_visual_recovery(raw, scene_count, expected_package_sha256):
    """Strict server-only V5; preserve genuine stock provenance separately."""
    try:
        _require(type(raw) is dict and set(raw) == _MEDIA_FIELDS
                 and type(raw.get('version')) is int and raw['version'] == 5
                 and raw.get('repair_only') is True and raw.get('source_task_id') == cb.SOURCE
                 and type(scene_count) is int and scene_count == 6
                 and isinstance(expected_package_sha256, str) and cb._SHA.fullmatch(expected_package_sha256)
                 and raw.get('package_sha256') == expected_package_sha256
                 and type(raw.get('repair_scene_indices')) is list
                 and all(type(index) is int for index in raw['repair_scene_indices'])
                 and raw['repair_scene_indices'] == list(REPAIRS))
        generated = _one(raw.get('scenes'), 1)
        _require(type(generated) is list and len(generated) == 1)
        tasks, _, _ = cb._runtime()
        # The existing v3 validator checks the actual generated entry, never
        # an invented/generated placeholder for the stock asset.
        checked = tasks._validated_recovered_generated_media({
            'version': 3, 'recovery_only': True, 'source_task_id': cb.SOURCE,
            'package_sha256': expected_package_sha256, 'scenes': {'1': generated},
        }, 6, expected_package_sha256)
        _require(checked['scenes'][1][0]['sha256'] == cb.SELECTED[1][0]
                 and checked['scenes'][1][0]['size'] == cb.SELECTED[1][1]
                 and checked['scenes'][1][0]['provider'] == 'gemini_veo'
                 and checked['scenes'][1][0]['synthetic_motion_only'] is False)
        entry = deepcopy(stock._entry(_one(raw.get('stock_scenes'), 5), cb.SOURCE))
        _require(entry['pexels_id'] != 38052460 and entry['source_duration'] >= 5.0
                 and entry['sha256'] not in {value[0] for value in cb.SELECTED.values()})
        return {**deepcopy(raw), 'scenes': checked['scenes'], 'stock_scenes': {5: entry}}
    except Exception:
        raise MixedVisualRecoveryError() from None


def _stock_spec(entry, path):
    return {'path': str(path), 'pexels_id': entry['pexels_id'], 'start_fraction': entry['start_fraction'],
            'preserve_start_fraction': True, 'source_duration': entry['source_duration'],
            'source_type': 'stock', 'stock_provider': 'pexels', 'generated': False,
            'curated_pinned': True, 'forbid_loop': True}


def load_mixed_stock(raw, work_dir):
    """Private Storage only. Caller also checks duration against saved speech."""
    try:
        media = validate_mixed_visual_recovery(raw, 6, raw.get('package_sha256'))
        path = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_[0-9]{1,2}', path.name)
        _require(match is not None and stock._uuid(match[1]) != cb.SOURCE)
        work = _work_directory(match[1], path)
        entry = media['stock_scenes'][5]
        output = work / 'mixed_stock_05.mp4'
        _require(not output.exists() and not output.is_symlink())
        checksum, size = _download_bounded(storage._client(), entry['key'], output,
                                           stock.MAX_CLIP_BYTES, expected_size=entry['size'])
        _require(checksum == entry['sha256'] and size == entry['size'])
        stock._file(output, work, stock.MAX_CLIP_BYTES)
        probed = stock._probe(output)
        _require(all(probed[key] == entry[key] for key in ('width', 'height'))
                 and abs(probed['source_duration'] - entry['source_duration']) <= .04)
        return {'scene_visuals': {5: [_stock_spec(entry, output)]}, 'credits': [
            {**entry['attribution'], 'scene_index': 5, 'pexels_id': entry['pexels_id'],
             'selected_by': 'mixed_visual_recovery'}]}
    except Exception:
        raise MixedVisualRecoveryError() from None


def _lineage(source, client):
    parent_id = stock._uuid(source.get('parent_id'))
    _require(parent_id != cb.SOURCE)
    keys = [studio_state.JOB_PREFIX + parent_id, studio_state.RETRY_DISPATCH_PREFIX + parent_id,
            studio_state.RETRY_CHILD_CLAIM_PREFIX + cb.SOURCE, studio_state.RETRY_CHILD_EXECUTION_PREFIX + cb.SOURCE]
    parent = json.loads(client.get(keys[0]) or 'null')
    dispatch, claim, execution = client.hgetall(keys[1]), client.hgetall(keys[2]), client.get(keys[3])
    token = dispatch.get('token')
    _require(isinstance(parent, dict) and parent.get('task_id') == parent_id
             and parent.get('kind') == 'render' and parent.get('state') == 'FAILURE'
             and parent.get('retry_child_task_id') == cb.SOURCE and parent.get('spec') == source['spec']
             and dispatch.get('child_task_id') == cb.SOURCE and dispatch.get('mode') == 'full'
             and dispatch.get('state') == 'dispatched' and isinstance(token, str) and 16 <= len(token) <= 256
             and claim.get('source_task_id') == parent_id and claim.get('token') == token and execution == token)
    return keys, cb._digest({'parent': parent, 'dispatch': dispatch, 'claim': claim, 'execution': execution})


def _shooting_package(original, overrides):
    _require(type(overrides) is dict and set(overrides) == set(REPAIRS)
             and all(type(index) is int for index in overrides))
    primitives._repair_request(REPAIRS, overrides)
    candidate = primitives._shooting_package(original, overrides)
    candidate['scenes'][5] = {**candidate['scenes'][5], 'ai_prompt': None, 'visual_queries': list(STOCK_QUERIES)}
    return candidate


def _validate_reviewed_story(shooting, reviewed):
    """Check the critic's output without changing its attested fingerprint.

    The stock writer may refine the immutable stock-routed retained shots.
    A generated fallback can still have an authored stock route (scene 1).
    Its derived tts_text is not a narration edit when it is an exact echo.
    The same pinned bytes still require a fresh exact-cut visual review.
    """
    require_unchanged_voice_narration(shooting, reviewed)
    _require(type(reviewed) is dict and type(reviewed.get('scenes')) is list
             and len(reviewed['scenes']) == len(shooting['scenes']) == 6
             and reviewed.get('sources') == shooting.get('sources')
             and reviewed.get('title') == shooting.get('title'))
    joined = ' '.join(scene['narration'] for scene in shooting['scenes'])
    _require(reviewed.get('narration') == joined
             and ('tts_narration' not in reviewed or reviewed['tts_narration'] == joined))
    stock_positions = {index for index, scene in enumerate(shooting['scenes'])
                       if not str(scene.get('ai_prompt') or '').strip()}
    _require(5 in stock_positions and stock_positions <= {1, 5})
    for index, (before, after) in enumerate(zip(shooting['scenes'], reviewed['scenes'])):
        _require(type(before) is dict and type(after) is dict)
        if index not in stock_positions:
            _require(after == before)
            continue
        expected, actual = deepcopy(before), deepcopy(after)
        for scene in (expected, actual):
            _require('tts_text' not in scene or scene['tts_text'] == scene['narration'])
            scene.pop('tts_text', None)
        queries = actual.get('visual_queries')
        _require(type(queries) is list and 2 <= len(queries) <= 3
                 and all(type(query) is str and query == query.strip()
                         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '\-]*", query)
                         and 3 <= len(re.findall(r"[A-Za-z0-9'-]+", query)) <= 9
                         and len(query) <= 600 for query in queries)
                 and len({query.casefold() for query in queries}) == len(queries))
        actual['visual_queries'] = deepcopy(expected['visual_queries'])
        _require(actual == expected)


def exact_mixed_retained_visuals(scenes, scene_visuals, voice_result, work_dir):
    """Reviewable real 1/5 cuts; missing repair slots supply timing, no images."""
    render, _ = cb._review_runtime()
    work = Path(work_dir)
    voice = voice_result
    _require(type(scenes) is list and len(scenes) == 6 and type(scene_visuals) is list and len(scene_visuals) == 6
             and all(not scene_visuals[index] for index in REPAIRS)
             and all(type(scene_visuals[index]) is list and len(scene_visuals[index]) == 1
                     and type(scene_visuals[index][0]) is dict for index in (1, 5)))
    generated, actual_stock = scene_visuals[1][0], scene_visuals[5][0]
    _require(generated.get('generated') is True and generated.get('generation_provider') == 'gemini_veo'
             and actual_stock.get('generated') is False and actual_stock.get('source_type') == 'stock'
             and actual_stock.get('stock_provider') == 'pexels'
             and all(scene_visuals[index][0].get('preserve_start_fraction') is True for index in (1, 5)))
    measured = float(render.media_duration(voice['path']))
    _require(math.isfinite(measured) and 28.7 <= measured <= 30.08
             and abs(measured - voice['duration_after_fit']) <= .12)
    pools = [[{'path': str(work / f'not-reviewed-{index}.mp4'),
               'generated': True, 'source_type': 'generated', 'generation_provider': 'gemini_veo',
               'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True}]
             for index in range(6)]
    for index in (1, 5): pools[index] = deepcopy(scene_visuals[index])
    timeline = render._scene_timeline(scenes, pools, voice['scene_durations'], measured, [])
    _require(len(timeline) == 6 and [shot[3] for shot in timeline] == list(range(6)))
    counts = render._timeline_frame_counts(timeline, measured)
    directory = work / 'mixed_exact_review'
    directory.mkdir(exist_ok=False)
    inputs = {}
    for index in (1, 5):
        spec, _duration, transition, _owner = timeline[index]
        output = directory / f'scene-{index:02d}.mp4'
        render.normalize_clip(spec, output, counts[index] / render.FPS, index, transition, '1080x1920')
        inputs[index] = [{**spec, 'path': str(output), 'start_fraction': 0.0, 'forbid_loop': True}]
    return {'scene_visuals': inputs, 'frame_counts': counts}


def _review(package, voice, paths, entry, work):
    _, reviewer = cb._review_runtime()
    pools = [[] for _ in range(6)]
    pools[1] = [{'path': str(paths[1]), 'generated': True, 'source_type': 'generated',
                 'generation_provider': 'gemini_veo', 'start_fraction': 0.0,
                 'preserve_start_fraction': True, 'forbid_loop': True}]
    pools[5] = [_stock_spec(entry, paths[5])]
    exact = exact_mixed_retained_visuals(package['scenes'], pools, voice, work)
    inputs = [exact['scene_visuals'][index] for index in (1, 5)]
    directory = work / 'mixed_exact_review'
    counts = exact['frame_counts']
    result = reviewer([package['scenes'][index] for index in (1, 5)], inputs, directory, 2,
                      _missing_review_attempts=0, _score_reason_consistency_attempts=0,
                      topic=package['_recovery_topic'], story_scenes=package['scenes'],
                      content_style=package['studio_options'].get('content_style', ''),
                      evidence_sources=package.get('sources') or [])
    _require(isinstance(result, dict) and not result.get('missing_review_indices')
             and type(result.get('reviews')) is list and len(result['reviews']) == 2)
    reports = {}
    for row in result['reviews']:
        _require(type(row) is dict and type(row.get('scene_index')) is int
                 and row['scene_index'] in (0, 1) and row['scene_index'] not in reports)
        reports[row['scene_index']] = row
    return [{'scene_index': index, 'source_type': 'generated' if index == 1 else 'stock',
             'raw_sha256': cb.SELECTED[1][0] if index == 1 else entry['sha256'],
             'exact_cut_frames': counts[index], 'review': cb._review(reports[local])}
            for local, index in enumerate((1, 5))]


def _store(client, work, kind, value):
    return primitives._store(client, cb.SOURCE, work, 'mixed_' + kind, value)


def _read(client, pointer, kind):
    _require(type(pointer) is dict and pointer.get('source_task_id') == cb.SOURCE)
    return primitives._read_record(client, pointer, 'mixed_' + kind)


def prepare_mixed_visual_recovery(source_task_id, retrieval_pointer, work_dir, *, stock_candidate, shot_prompt_overrides):
    """Fetch one exact real stock candidate and freshly review retained 1/5."""
    audit_pointer = None
    try:
        _require(source_task_id == cb.SOURCE)
        client = studio_state._client()
        source, fingerprint = cb._state(client)
        _, lineage_hash = _lineage(source, client)
        path = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', path.name)
        _require(match is not None and stock._uuid(match[1]) != cb.SOURCE)
        work = _work_directory(match[1], path)
        _require(not any(work.iterdir()))
        _require(type(stock_candidate) is dict and set(stock_candidate) in (
                     {'pexels_id', 'start_fraction'}, {'pexels_id', 'start_fraction', 'sha256', 'size'})
                 and type(stock_candidate.get('pexels_id')) is int and 1 <= stock_candidate['pexels_id'] <= 10**12
                 and stock_candidate['pexels_id'] != 38052460)
        if 'sha256' in stock_candidate:
            stock._sha(stock_candidate['sha256'])
            _require(type(stock_candidate.get('size')) is int and 1024 <= stock_candidate['size'] <= stock.MAX_CLIP_BYTES)
        stock._number(stock_candidate['start_fraction'], 0, .95)
        candidate = load_voice_retry_candidate(cb.SOURCE, match[1], source['audio_candidate_checkpoint'], work)
        package, voice = candidate['package'], candidate['voice_result']
        shooting = _shooting_package(package, shot_prompt_overrides)
        s3 = storage._client()
        metadata, mapped = cb._evidence(s3, retrieval_pointer, source)
        tasks, director, voice_module = cb._runtime()
        options = cb._options(source)
        _require(tasks._normalized_options(options, .5) == options
                 and type(options.get('quality_threshold')) is int and 1 <= options['quality_threshold'] <= 100
                 and len(package['scenes']) == 6 and candidate['audio_sha256'] == cb.VOICE_SHA
                 and voice['spoken_texts'] == [voice_module.normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
                                               for index, scene in enumerate(package['scenes'])]
                 and all(row['narration'] == scene['narration'] and row['duration_seconds'] == voice['scene_durations'][index]
                         for index, (row, scene) in enumerate(zip(metadata['scenes'], package['scenes']))))
        paths = {1: work / 'saved-scene-01.mp4', 5: work / 'pexels-scene-05.mp4'}
        row = mapped[1]
        checksum, _size = _download_bounded(s3, row['clip_key'], paths[1], cb.MAX_CLIP, expected_size=row['clip_size'])
        _require(checksum == row['clip_sha256'])
        tasks._validate_recovered_generated_clip(paths[1], max(5.0, voice['scene_durations'][1] + .35),
                                                expected_size=row['clip_size'], expected_sha256=row['clip_sha256'])
        _require(abs(tasks.media_duration(paths[1]) - row['duration_seconds']) <= .04
                 and tasks.video_frame_count(paths[1]) == row['video_frames'])
        entry, attribution = stock._pexels_by_id(stock_candidate['pexels_id'], paths[5])
        _require(all(entry[key] == stock_candidate[key] for key in ('pexels_id', 'sha256', 'size') if key in stock_candidate)
                 and stock._file_digest(paths[5], stock.MAX_CLIP_BYTES) == (entry['sha256'], entry['size'])
                 and entry['source_duration'] >= max(5.0, voice['scene_durations'][5] + .35))
        entry.update(key=f'curated_stock/{cb.SOURCE}/raw/{entry["sha256"]}.mp4',
                     start_fraction=stock_candidate['start_fraction'], attribution=attribution)
        stock._entry(entry, cb.SOURCE)
        audit = {**_FLAGS, 'source_task_id': cb.SOURCE, 'status': 'story_review_pending',
                 'source_state_sha256': fingerprint, 'source_spec_sha256': cb._digest(source['spec']),
                 'lineage_sha256': lineage_hash, 'retrieval_pointer': deepcopy(retrieval_pointer),
                 'source_package': deepcopy(package), 'voice_sha256': cb.VOICE_SHA,
                 'shot_prompt_overrides': {str(key): value for key, value in shot_prompt_overrides.items()},
                 'stock_candidate': deepcopy(stock_candidate), 'stock_entry': entry,
                 'repair_scene_indices': list(REPAIRS), 'retained_visual_reviews': [],
                 'new_paid_create_requests': 0, 'new_tts_requests': 0}
        story_stage = 'independent_story_review'
        try:
            reviewed = director.revalidate_immutable_short_story(shooting, source['spec']['topic'], .5, 'tr', options,
                          immutable_candidate_narrations=[scene['narration'] for scene in package['scenes']])
            story_stage = 'immutable_story_contract'
            _validate_reviewed_story(shooting, reviewed)
            _require(reviewed.get('studio_options') == options)
            story_stage = 'story_attestation'
            _require(director.short_story_package_is_approved(reviewed, source['spec']['topic']))
        except Exception:
            audit['status'] = 'story_review_rejected_or_unavailable'
            audit['failure_stage'] = story_stage
            audit_pointer = _store(s3, work, 'audit', audit)
            raise MixedVisualRecoveryError(audit_pointer) from None
        audit['reviewed_package'] = deepcopy(reviewed)
        try:
            evidence = _review({**reviewed, '_recovery_topic': source['spec']['topic']}, voice, paths, entry, work)
        except Exception:
            audit['status'] = 'visual_review_unavailable'
            audit_pointer = _store(s3, work, 'audit', audit)
            raise MixedVisualRecoveryError(audit_pointer) from None
        passed = all(cb._review_passes(row['review'], options['quality_threshold']) for row in evidence)
        audit.update(status='retained_visuals_passed' if passed else 'retained_visuals_rejected', retained_visual_reviews=evidence)
        audit_pointer = _store(s3, work, 'audit', audit)
        if not passed:
            raise MixedVisualRecoveryError(audit_pointer)
        _require(cb._state(client)[1] == fingerprint and _lineage(source, client)[1] == lineage_hash)
        package_hash = tasks._recovery_package_sha256(reviewed)
        audio = {'version': 1, 'source_task_id': cb.SOURCE, 'package_sha256': package_hash,
                 'key': f'recovery/{cb.SOURCE}/raw/voice.mp3', 'sha256': cb.VOICE_SHA, 'size': cb.VOICE_SIZE,
                 **{key: voice.get(key) for key in ('scene_durations', 'spoken_texts', 'duration_before_fit',
                     'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')}}
        media = {'version': 5, 'repair_only': True, 'source_task_id': cb.SOURCE, 'package_sha256': package_hash,
                 'repair_scene_indices': list(REPAIRS), 'stock_scenes': {'5': entry}, 'scenes': {'1': [{
                     'key': row['clip_key'], 'sha256': row['clip_sha256'], 'size': row['clip_size'],
                     'provider': 'gemini_veo', 'provider_attempts': 1, 'synthetic_motion_only': False,
                     'motion_recipe_version': None, 'source_media_type': 'video'}]}}
        validate_mixed_visual_recovery(media, 6, package_hash)
        tasks._validated_recovered_voice(audio, 6, package_hash)
        cb._copy_voice_create_only(s3, Path(voice['path']), audio['key'], cb.VOICE_SHA, work)
        with paths[5].open('rb') as incoming:
            primitives.assets._put_immutable(s3, entry['key'], incoming, entry['sha256'], entry['size'], 'video/mp4')
        receipt = {**audit, 'status': 'prepared_mixed_repair', 'package_sha256': package_hash,
                   'audit_pointer': audit_pointer, 'approved_package': {
                       **reviewed, '_recovered_generated_media': media, '_recovered_voice': audio}}
        return _store(s3, work, 'prepared', receipt)
    except MixedVisualRecoveryError:
        raise
    except Exception:
        raise MixedVisualRecoveryError(audit_pointer) from None


def publish_mixed_visual_recovery(pointer):
    """Install one validated private checkpoint; no dispatch or paid work."""
    try:
        s3, client = storage._client(), studio_state._client()
        receipt = _read(s3, pointer, 'prepared')
        _require(all(receipt.get(key) == value and type(receipt.get(key)) is type(value) for key, value in _FLAGS.items())
                 and receipt.get('status') == 'prepared_mixed_repair' and receipt.get('source_task_id') == cb.SOURCE
                 and all(type(receipt.get(key)) is int and receipt[key] == 0 for key in ('new_paid_create_requests', 'new_tts_requests')))
        discovered, _ = cb._state(client)
        lineage_keys, _ = _lineage(discovered, client)
        with client.pipeline() as transaction:
            transaction.watch(*cb._keys(), *lineage_keys)
            source, fingerprint = cb._state(transaction)
            checked_keys, lineage_hash = _lineage(source, transaction)
            _require(checked_keys == lineage_keys and fingerprint == receipt['source_state_sha256']
                     and lineage_hash == receipt['lineage_sha256'] and cb._digest(source['spec']) == receipt['source_spec_sha256'])
            metadata, mapped = cb._evidence(s3, receipt['retrieval_pointer'], source)
            tasks, director, voice = cb._runtime()
            package, package_hash = receipt['approved_package'], receipt['package_sha256']
            _require(tasks._recovery_package_sha256(package) == package_hash
                     and package.get('studio_options') == cb._options(source)
                     and director.short_story_package_is_approved(package, source['spec']['topic']))
            audio = tasks._validated_recovered_voice(package.get('_recovered_voice'), 6, package_hash)
            media = validate_mixed_visual_recovery(package.get('_recovered_generated_media'), 6, package_hash)
            _require(audio and audio['source_task_id'] == cb.SOURCE and audio['sha256'] == cb.VOICE_SHA
                     and audio['size'] == cb.VOICE_SIZE and media['stock_scenes'][5] == receipt['stock_entry'])
            audit = _read(s3, receipt['audit_pointer'], 'audit')
            _require(audit.get('status') == 'retained_visuals_passed'
                     and {key: value for key, value in receipt.items() if key not in {
                         'status', 'package_sha256', 'audit_pointer', 'approved_package'}} == {
                         key: value for key, value in audit.items() if key != 'status'}
                     and audit['reviewed_package'] == {key: value for key, value in package.items()
                         if key not in {'_recovered_voice', '_recovered_generated_media'}}
                     and cb._digest(audio_checkpoint._candidate_package(audit['source_package']))
                         == source['audio_candidate_checkpoint']['package_sha256'])
            overrides = {int(key): value for key, value in audit['shot_prompt_overrides'].items()}
            _validate_reviewed_story(_shooting_package(audit['source_package'], overrides), package)
            _require(audio['spoken_texts'] == [voice.normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
                                              for index, scene in enumerate(package['scenes'])]
                     and all(row['narration'] == scene['narration'] and row['duration_seconds'] == audio['scene_durations'][index]
                             for index, (row, scene) in enumerate(zip(metadata['scenes'], package['scenes']))))
            entry, row = media['scenes'][1][0], mapped[1]
            _require(all(entry[key] == row[other] for key, other in (
                ('key', 'clip_key'), ('sha256', 'clip_sha256'), ('size', 'clip_size'))))
            evidence = receipt['retained_visual_reviews']
            _require(type(evidence) is list and len(evidence) == 2)
            for index, report in zip((1, 5), evidence):
                expected = cb.SELECTED[1][0] if index == 1 else media['stock_scenes'][5]['sha256']
                _require(type(report) is dict and type(report.get('scene_index')) is int and report['scene_index'] == index
                         and report.get('raw_sha256') == expected and report.get('source_type') == ('generated' if index == 1 else 'stock')
                         and type(report.get('exact_cut_frames')) is int and report['exact_cut_frames'] > 0
                         and cb._review_passes(report.get('review'), cb._options(source)['quality_threshold']))
            _require(media['stock_scenes'][5]['source_duration'] >= max(5.0, audio['scene_durations'][5] + .35))
            # Re-read exact immutable bytes before checkpoint publication.
            for asset in (audio, entry, media['stock_scenes'][5]):
                response = s3.get_object(Bucket=storage.settings.bucket, Key=asset['key'])
                body = response.get('Body')
                try:
                    _require(type(response.get('ContentLength')) is int and response['ContentLength'] == asset['size'])
                    digest, size = hashlib.sha256(), 0
                    while chunk := body.read(min(65536, asset['size'] - size + 1)):
                        size += len(chunk); _require(size <= asset['size']); digest.update(chunk)
                    _require(size == asset['size'] and digest.hexdigest() == asset['sha256'])
                finally:
                    if body is not None: body.close()
            source.update(repair_available=True, updated_at=datetime.now(timezone.utc).isoformat())
            transaction.multi()
            transaction.set(studio_state.REPAIR_CHECKPOINT_PREFIX + cb.SOURCE,
                            json.dumps(receipt, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(studio_state.JOB_PREFIX + cb.SOURCE,
                            json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            ex=studio_state.JOB_TTL_SECONDS)
            _require(transaction.execute() == [True, True])
        return {'status': 'checkpoint_published', 'source_task_id': cb.SOURCE, 'requires_full_qa': True,
                'repair_scene_indices': list(REPAIRS), 'stock_scene_indices': [5], 'new_paid_create_requests': 0}
    except Exception:
        raise MixedVisualRecoveryError() from None
