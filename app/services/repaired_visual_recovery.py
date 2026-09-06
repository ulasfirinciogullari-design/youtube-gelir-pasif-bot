"""Two explicit continuations of Margin's failed V4 repairs.

The prior checkpoint stays consumed. Six actual selected clips are reconstructed
from the trusted parent receipt and the leaf's private raw manifests.
Fresh immutable-story and exact-cut reviews are diagnostic evidence, never a
waiver of the ordinary worker's voice, visual, render or publication gates.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.services import audio_checkpoint, preserved_visual_recovery as base, storage, studio_state
from app.services.production_shot_prompt import build_production_shot_prompt
from app.services.voice_candidate_recovery import _work_directory, load_voice_retry_candidate, require_unchanged_voice_narration


SOURCE = 'faf6e4a2-aeb5-4441-9ed5-69aebdb87bd7'
PARENT = '5e0470df-ed41-44a5-a947-5e11bd3681a0'
PARENT_RECEIPT_SHA = 'b923ec8aff48df4e7eaa4dc31c1ede2f60537cf9f6b938af55173ee12b88983b'
PARENT_RECEIPT_SIZE = 19276
WORKPRINT_SHA = '648d1e7c5e13cd9acf4c73cc32d91434efcd50eaf16fbaba497e07f1a0356795'
WORKPRINT_SIZE = 13284
PREVIOUS_REPAIRS = (1, 3, 4, 5)
REPAIRS = (1, 4, 5)
LATEST_SOURCE = '6282ec56-f6e4-4189-9093-b09595fd2f57'
LATEST_PARENT_RECEIPT_SHA = '0d2b779b46122b24eabb2395b866be9afe69b3a07fe4373606f865e3f4101cfd'
LATEST_PARENT_RECEIPT_SIZE = 52187
LATEST_WORKPRINT_SHA = 'b4e84abd70e3c2dbf4c9dc1376f5ba54435033c82d38105f432f4007d2066cce'
LATEST_WORKPRINT_SIZE = 12705


class RepairedVisualRecoveryError(RuntimeError):
    def __init__(self, diagnostic_pointer=None):
        super().__init__('Repaired visual continuation stopped; no new media or retry was created')
        self.diagnostic_pointer = deepcopy(diagnostic_pointer)


def _require(value):
    if not value:
        raise ValueError('Invalid repaired visual continuation evidence')


def _case(source_id=None):
    """Only these reviewed identities are eligible; no caller-defined profile."""
    if source_id is None or source_id == SOURCE:
        return {'source': SOURCE, 'parent': PARENT, 'kind': 'prepared',
                'status': 'prepared_bounded_repair', 'receipt_sha': PARENT_RECEIPT_SHA,
                'receipt_size': PARENT_RECEIPT_SIZE, 'workprint_sha': WORKPRINT_SHA,
                'workprint_size': WORKPRINT_SIZE, 'previous_repairs': PREVIOUS_REPAIRS,
                'repairs': REPAIRS, 'source_paid': 4, 'parent_paid': 6}
    _require(source_id == LATEST_SOURCE)
    return {'source': LATEST_SOURCE, 'parent': SOURCE, 'kind': 'repaired_prepared',
            'status': 'prepared_repaired_visuals', 'receipt_sha': LATEST_PARENT_RECEIPT_SHA,
            'receipt_size': LATEST_PARENT_RECEIPT_SIZE, 'workprint_sha': LATEST_WORKPRINT_SHA,
            'workprint_size': LATEST_WORKPRINT_SIZE, 'previous_repairs': REPAIRS,
            'repairs': (), 'source_paid': 3, 'parent_paid': 4}


def _keys(case=None):
    case = _case() if case is None else case
    return (*base._keys(case['source']), *base._keys(case['parent']),
            studio_state.RETRY_CHILD_CLAIM_PREFIX + case['source'],
            studio_state.RETRY_CHILD_EXECUTION_PREFIX + case['source'])


def _state(client, case=None):
    case = _case() if case is None else case
    source_id, parent_id = case['source'], case['parent']
    leaf, parent = [json.loads(client.get(studio_state.JOB_PREFIX + task) or 'null')
                    for task in (source_id, parent_id)]
    _require(all(isinstance(job, dict) and job.get('task_id') == task and job.get('kind') == 'render'
                 and job.get('state') == 'FAILURE' and not job.get('result')
                 for job, task in ((leaf, source_id), (parent, parent_id))))
    _require(leaf.get('parent_id') == parent_id and parent.get('retry_child_task_id') == source_id
             and parent.get('repair_claimed') is True and parent.get('retry_claimed') is True
             and parent.get('repair_available') is False
             and leaf.get('failure_stage') == 'final_visual_qc'
             and not any(leaf.get(key) for key in ('retry_child_task_id', 'retry_claimed', 'repair_claimed', 'repair_available'))
             and not client.exists(*base._keys(source_id)[2:], studio_state.REPAIR_CHECKPOINT_PREFIX + parent_id)
             and isinstance(leaf.get('spec'), dict) and leaf['spec'] == parent.get('spec'))
    spec = leaf['spec']
    _require(spec.get('mode') == 'production' and spec.get('format') == 'shorts'
             and type(spec.get('duration_minutes')) in (int, float) and spec['duration_minutes'] == .5
             and spec.get('music') == 'off' and spec.get('language') == 'en'
             and isinstance(spec.get('topic'), str) and bool(spec['topic'].strip()))
    if spec.get('publish_after_render') is True:
        _require(all(isinstance(spec.get(key), str) and bool(spec[key].strip()) for key in (
            'production_channel_id', 'production_connection_id', 'production_profile_revision')))
    ledgers = []
    for job, task, used in ((leaf, source_id, case['source_paid']), (parent, parent_id, case['parent_paid'])):
        ledger = client.hgetall(studio_state.PAID_CREATE_BUDGET_PREFIX + task)
        _require(ledger == {'cap': '6', 'used': str(used)}
                 and type(job.get('paid_create_slots_used')) is int and job['paid_create_slots_used'] == used
                 and type(job.get('preview_total_paid_create_cap')) is int and job['preview_total_paid_create_cap'] == 6)
        ledgers.append(ledger)
    dispatch = client.hgetall(studio_state.RETRY_DISPATCH_PREFIX + parent_id)
    claim = client.hgetall(studio_state.RETRY_CHILD_CLAIM_PREFIX + source_id)
    execution = client.get(studio_state.RETRY_CHILD_EXECUTION_PREFIX + source_id)
    consumed = client.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX + parent_id)
    token = dispatch.get('token')
    _require(dispatch.get('child_task_id') == source_id and dispatch.get('state') == 'dispatched'
             and dispatch.get('mode') == 'repair' and isinstance(token, str) and 16 <= len(token) <= 256
             and claim.get('source_task_id') == parent_id and claim.get('token') == execution == consumed == token)
    audio = leaf.get('audio_candidate_checkpoint')
    _require(isinstance(audio, dict) and audio.get('qa_approved') is False and audio.get('requires_full_qa') is True
             and not leaf.get('audio_candidate_checkpoint_error')
             and isinstance(parent.get('audio_candidate_checkpoint'), dict)
             and audio.get('audio_sha256') == parent['audio_candidate_checkpoint'].get('audio_sha256'))
    _journal(leaf, case['previous_repairs'])
    if case['kind'] == 'repaired_prepared':
        _journal(parent, PREVIOUS_REPAIRS)
    return leaf, parent, base._digest({'leaf': leaf, 'parent': parent, 'ledgers': ledgers,
        'dispatch': dispatch, 'claim': claim, 'execution': execution, 'consumed': consumed})


def _journal(job, expected_indices):
    source_id, audio = job['task_id'], job['audio_candidate_checkpoint']
    journal, count = job.get('generated_asset_candidates'), len(expected_indices)
    _require(base._flags(journal) and journal.get('source_task_id') == source_id and journal.get('status') == 'candidate_journal'
             and all(type(journal.get(key)) is int and journal[key] == value
                     for key, value in {'attempted_count': count, 'preserved_count': count, 'failed_count': 0}.items())
             and type(journal.get('entries')) is list and len(journal['entries']) == count)
    indices, raw_hashes, manifest_hashes, packages = set(), set(), set(), set()
    for pointer in journal['entries']:
        _require(base._flags(pointer) and set(pointer) == base._POINTER_FIELDS
                 and pointer.get('source_task_id') == source_id and pointer.get('status') == 'preserved_candidate'
                 and type(pointer.get('scene_index')) is int and pointer['scene_index'] in expected_indices
                 and pointer['scene_index'] not in indices and pointer.get('phase') == 'initial_generation'
                 and pointer.get('provider') == 'gemini_veo'
                 and all(isinstance(pointer.get(key), str) and base._SHA.fullmatch(pointer[key])
                         for key in ('raw_sha256', 'manifest_sha256', 'package_sha256', 'audio_sha256'))
                 and pointer['audio_sha256'] == audio['audio_sha256']
                 and pointer['raw_key'] == f"generated_candidates/{source_id}/raw/{pointer['raw_sha256']}.mp4"
                 and pointer['manifest_key'] == f"generated_candidates/{source_id}/manifests/{pointer['manifest_sha256']}.json"
                 and type(pointer['raw_size']) is int and 1024 <= pointer['raw_size'] <= base.assets.MAX_RAW_BYTES
                 and type(pointer['manifest_size']) is int and 1 <= pointer['manifest_size'] <= base.assets.MAX_MANIFEST_BYTES)
        indices.add(pointer['scene_index']); raw_hashes.add(pointer['raw_sha256'])
        manifest_hashes.add(pointer['manifest_sha256']); packages.add(pointer['package_sha256'])
    _require(indices == set(expected_indices) and len(raw_hashes) == len(manifest_hashes) == count and len(packages) == 1)


def _evidence(client, source, parent, parent_pointer, case=None):
    case = _case() if case is None else case
    source_id, parent_id = case['source'], case['parent']
    expected_pointer = {'version': 1, 'source_task_id': parent_id, 'kind': case['kind'],
        'key': f"recovery/{parent_id}/preserved_visual/{case['kind']}-{case['receipt_sha']}.json",
        'sha256': case['receipt_sha'], 'size': case['receipt_size']}
    _require(parent_pointer == expected_pointer)
    receipt = base._read_record(client, parent_pointer, case['kind'])
    _require(base._flags(receipt) and receipt.get('source_task_id') == parent_id
             and receipt.get('status') == case['status'])
    if case['kind'] == 'prepared':
        _require(receipt.get('source_spec_sha256') == base._digest(source['spec'])
                 and receipt.get('journal_sha256') == base._digest(parent.get('generated_asset_candidates')))
    tasks, _, _ = base._runtime()
    original, package_hash = receipt['approved_package'], receipt['package_sha256']
    _require(tasks._recovery_package_sha256(original) == package_hash
             and original.get('studio_options') == base._options(source))
    media = tasks._validated_recovered_generated_media(original.get('_recovered_generated_media'), 6, package_hash)
    voice = tasks._validated_recovered_voice(original.get('_recovered_voice'), 6, package_hash)
    retained = set(range(6)) - set(case['previous_repairs'])
    _require(media and voice and media['version'] == 4 and media['source_task_id'] == voice['source_task_id'] == parent_id
             and media['repair_scene_indices'] == list(case['previous_repairs']) and set(media['scenes']) == retained
             and voice['sha256'] == source['audio_candidate_checkpoint']['audio_sha256']
             and voice['size'] == source['audio_candidate_checkpoint']['size'])
    if case['kind'] == 'repaired_prepared':
        parent_manifests = base._manifests(client, parent)
        parent_clean = receipt.get('source_package')
        _require(isinstance(parent_clean, dict) and audio_checkpoint._candidate_package(parent_clean) == parent_clean
                 and base._digest(parent_clean) == parent['audio_candidate_checkpoint']['package_sha256']
                 and all(row['package'] == parent_clean for row in parent_manifests)
                 and receipt.get('repair_scene_indices') == list(case['previous_repairs'])
                 and type(receipt.get('assets')) is list and len(receipt['assets']) == 6
                 and receipt.get('package') == {key: value for key, value in original.items()
                                              if key not in {'_recovered_voice', '_recovered_generated_media'}})
        require_unchanged_voice_narration(parent_clean, original)
        for row in parent_manifests:
            asset = receipt['assets'][row['scene_index']]
            _require(asset.get('origin_task_id') == parent_id
                     and all(asset.get(key) == row['raw'][key] for key in ('key', 'sha256', 'size', 'provider', 'provider_attempts'))
                     and row['audio']['sha256'] == voice['sha256']
                     and all(row['voice'].get(key) == voice.get(key) for key in ('spoken_texts', 'scene_durations',
                         'duration_before_fit', 'duration_after_fit', 'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds')))
        for index in retained:
            _require(len(media['scenes'][index]) == 1 and all(media['scenes'][index][0].get(key)
                     == receipt['assets'][index].get(key) for key in ('sha256', 'size', 'provider', 'provider_attempts')))
    manifests = base._manifests(client, source)
    clean = audio_checkpoint._candidate_package(original)
    _require(base._digest(clean) == source['audio_candidate_checkpoint']['package_sha256']
             and all(row['package'] == clean and row['package_sha256'] == package_hash for row in manifests))
    for key in ('spoken_texts', 'scene_durations', 'duration_before_fit', 'duration_after_fit',
                'tempo_rate', 'content_target_seconds', 'reserved_tail_seconds'):
        _require(all(row['voice'].get(key) == voice.get(key) for row in manifests))
    assets = {}
    for index in sorted(retained):
        _require(len(media['scenes'][index]) == 1)
        entry = media['scenes'][index][0]
        _require(entry['provider'] == 'gemini_veo' and entry['synthetic_motion_only'] is False)
        assets[index] = {**entry, 'origin_task_id': parent_id}
    for manifest in manifests:
        raw = manifest['raw']
        assets[manifest['scene_index']] = {key: raw[key] for key in ('key', 'sha256', 'size', 'provider', 'provider_attempts')}
        assets[manifest['scene_index']].update(origin_task_id=source_id, synthetic_motion_only=False,
                                              motion_recipe_version=None, source_media_type='video')
    _require(set(assets) == set(range(6)) and len({entry['sha256'] for entry in assets.values()}) == 6)
    pointer = source.get('qa_workprint')
    _require(isinstance(pointer, dict) and pointer.get('task_id') == source_id and pointer.get('status') == 'qa_workprint'
             and all(pointer.get(key) is False for key in ('qa_approved', 'publish_eligible', 'reusable'))
             and pointer.get('metadata_sha256') == case['workprint_sha'] and pointer.get('metadata_size') == case['workprint_size']
             and pointer.get('metadata_key') == f"qa_workprints/{source_id}/{case['workprint_sha']}.json")
    metadata = base._read_json(client, pointer['metadata_key'], case['workprint_sha'], case['workprint_size'], 1024 * 1024)
    _require(metadata.get('task_id') == source_id and metadata.get('status') == 'qa_workprint'
             and all(metadata.get(key) is False for key in ('qa_approved', 'publish_eligible', 'reusable'))
             and metadata.get('voice') == {'sha256': voice['sha256'], 'size': voice['size'], 'existing_voice_quality_passed': True}
             and type(metadata.get('scenes')) is list and len(metadata['scenes']) == 6)
    for index, row in enumerate(metadata['scenes']):
        selected = row.get('selection')
        _require(type(row.get('scene_index')) is int and row['scene_index'] == index
                 and row.get('narration') == clean['scenes'][index]['narration']
                 and row.get('duration_seconds') == voice['scene_durations'][index]
                 and isinstance(selected, dict) and selected.get('source_type') == 'generated'
                 and selected.get('generation_provider') == assets[index]['provider']
                 and selected.get('sha256') == assets[index]['sha256'] and selected.get('size') == assets[index]['size']
                 and selected.get('start_fraction') == 0.0 and selected.get('forbid_loop') is True)
    return clean, manifests[0]['voice'], voice, [assets[index] for index in range(6)]


def _request(indices, overrides, case=None):
    case = _case() if case is None else case
    indices, overrides = base._repair_request(indices, overrides)
    _require(indices == case['repairs'] and set(overrides) == set(case['repairs']))
    for prompt in overrides.values():
        build_production_shot_prompt({'ai_prompt': prompt})
    return overrides


def _contracts(tasks, package, saved_voice, assets, case=None):
    case = _case() if case is None else case
    source_id, repairs = case['source'], case['repairs']
    checksum = tasks._recovery_package_sha256(package)
    voice = {**deepcopy(saved_voice), 'source_task_id': source_id, 'package_sha256': checksum,
             'key': f'recovery/{source_id}/raw/voice.mp3'}
    media = {**({'version': 4, 'repair_only': True, 'repair_scene_indices': list(repairs)}
               if repairs else {'version': 3, 'recovery_only': True}),
             'source_task_id': source_id, 'package_sha256': checksum, 'scenes': {str(index): [{
                 **{key: value for key, value in asset.items() if key != 'origin_task_id'},
                 'key': f'recovery/{source_id}/raw/scene-{index:02d}-initial.mp4'}]
                 for index, asset in enumerate(assets) if index not in repairs}}
    tasks._validated_recovered_voice(voice, 6, checksum)
    tasks._validated_recovered_generated_media(media, 6, checksum)
    return checksum, voice, media


def prepare_repaired_visual_recovery(source_task_id, parent_prepared_pointer, work_dir, *,
                                     repair_scene_indices=REPAIRS, shot_prompt_overrides):
    audit_pointer = None
    try:
        case = _case(source_task_id)
        source_id, parent_id, repairs = case['source'], case['parent'], case['repairs']
        _require(source_task_id == source_id)
        overrides = _request(repair_scene_indices, shot_prompt_overrides, case)
        source, parent, fingerprint = _state(studio_state._client(), case)
        client = storage._client()
        clean, candidate_voice, saved_voice, assets = _evidence(client, source, parent, parent_prepared_pointer, case)
        path = Path(work_dir)
        match = re.fullmatch(r'([0-9a-f-]{36})_attempt_0', path.name)
        _require(match is not None and base._canonical_id(match[1]) not in (source_id, parent_id))
        work = _work_directory(match[1], path)
        _require(not any(work.iterdir()))
        candidate = load_voice_retry_candidate(source_id, match[1], source['audio_candidate_checkpoint'], work)
        voice, options = candidate['voice_result'], base._options(source)
        _require(audio_checkpoint._candidate_package(candidate['package']) == clean
                 and audio_checkpoint._candidate_voice(voice, 6) == candidate_voice
                 and voice['spoken_texts'] == [scene['narration'].strip() for scene in clean['scenes']])
        require_unchanged_voice_narration(clean, candidate['package'])
        tasks, director, _ = base._runtime()
        _require(tasks._normalized_options(options, .5) == options
                 and type(options.get('quality_threshold')) is int and 1 <= options['quality_threshold'] <= 100)
        paths = []
        for index, entry in enumerate(assets):
            output = work / f'actual-raw-{index:02d}.mp4'
            checksum, size = base._download_bounded(client, entry['key'], output, base.assets.MAX_RAW_BYTES, expected_size=entry['size'])
            _require(checksum == entry['sha256'] and size == entry['size'])
            tasks._validate_recovered_generated_clip(output, minimum_duration=max(5.0, voice['scene_durations'][index] + .35),
                                                      expected_size=entry['size'], expected_sha256=entry['sha256'])
            paths.append((output, entry['provider']))
        audit = {**base._FLAGS, 'source_task_id': source_id, 'status': 'story_review_pending',
                 'state_sha256': fingerprint, 'parent_prepared_pointer': deepcopy(parent_prepared_pointer),
                 'source_package': clean, 'assets': assets, 'audio_sha256': candidate['audio_sha256'],
                 'repair_scene_indices': list(repairs), 'shot_prompt_overrides': {str(key): value for key, value in overrides.items()},
                 'retained_visual_reviews': [], 'new_paid_create_requests': 0, 'new_tts_requests': 0}
        story_stage = 'independent_story_review'
        try:
            shooting = base._shooting_package(clean, overrides)
            reviewed = director.revalidate_immutable_short_story(shooting, source['spec']['topic'], .5, 'en', options,
                         immutable_candidate_narrations=[scene['narration'] for scene in clean['scenes']])
            story_stage = 'immutable_story_contract'
            require_unchanged_voice_narration(clean, reviewed)
            _require(reviewed['scenes'] == shooting['scenes'] and reviewed.get('sources') == clean.get('sources')
                     and reviewed.get('title') == clean.get('title') and reviewed.get('studio_options') == options)
            story_stage = 'story_attestation'
            _require(director.short_story_package_is_approved(reviewed, source['spec']['topic']))
        except Exception:
            audit['status'] = 'story_review_rejected_or_unavailable'
            audit['failure_stage'] = story_stage
            audit_pointer = base._store(client, source_id, work, 'repaired_audit', audit)
            raise RepairedVisualRecoveryError(audit_pointer) from None
        audit['package'] = deepcopy(reviewed)
        try:
            result, counts = base._exact_review(reviewed, voice, paths, work, source['spec']['topic'])
            reports, passed = base._reports(result, counts, [{'raw': entry} for entry in assets], options['quality_threshold'], repairs)
        except Exception:
            audit['status'] = 'visual_review_unavailable'
            audit['failure_stage'] = 'exact_cut_visual_review'
            audit_pointer = base._store(client, source_id, work, 'repaired_audit', audit)
            raise RepairedVisualRecoveryError(audit_pointer) from None
        audit.update(status='retained_visuals_passed' if passed else 'retained_visuals_rejected', retained_visual_reviews=reports)
        audit_pointer = base._store(client, source_id, work, 'repaired_audit', audit)
        if not passed:
            raise RepairedVisualRecoveryError(audit_pointer)
        _require(_state(studio_state._client(), case)[2] == fingerprint)
        checksum, audio, media = _contracts(tasks, reviewed, saved_voice, assets, case)
        for index, (path, _) in enumerate(paths):
            if index not in repairs:
                entry = media['scenes'][str(index)][0]
                with path.open('rb') as incoming:
                    base.assets._put_immutable(client, entry['key'], incoming, entry['sha256'], entry['size'], 'video/mp4')
        with Path(voice['path']).open('rb') as incoming:
            base.assets._put_immutable(client, audio['key'], incoming, audio['sha256'], audio['size'], 'audio/mpeg')
        receipt = {**audit, 'status': 'prepared_repaired_visuals', 'audit_pointer': audit_pointer,
                   'package_sha256': checksum, 'approved_package': {**reviewed, '_recovered_voice': audio, '_recovered_generated_media': media}}
        return base._store(client, source_id, work, 'repaired_prepared', receipt)
    except RepairedVisualRecoveryError:
        raise
    except Exception:
        raise RepairedVisualRecoveryError(audit_pointer) from None


def publish_repaired_visual_recovery(prepared_pointer):
    try:
        _require(isinstance(prepared_pointer, dict))
        case = _case(prepared_pointer.get('source_task_id'))
        source_id, repairs = case['source'], case['repairs']
        _require(prepared_pointer.get('source_task_id') == source_id)
        client = storage._client()
        receipt = base._read_record(client, prepared_pointer, 'repaired_prepared')
        audit = base._read_record(client, receipt['audit_pointer'], 'repaired_audit')
        _require(base._flags(receipt) and base._flags(audit) and receipt.get('source_task_id') == audit.get('source_task_id') == source_id
                 and receipt.get('status') == 'prepared_repaired_visuals' and audit.get('status') == 'retained_visuals_passed'
                 and all(type(receipt.get(key)) is int and receipt[key] == 0 for key in ('new_paid_create_requests', 'new_tts_requests'))
                 and {key: value for key, value in receipt.items() if key not in {'status', 'audit_pointer', 'package_sha256', 'approved_package'}}
                    == {key: value for key, value in audit.items() if key != 'status'})
        with studio_state._client().pipeline() as transaction:
            transaction.watch(*_keys(case))
            source, parent, fingerprint = _state(transaction, case)
            _require(fingerprint == receipt['state_sha256'])
            clean, _, saved_voice, assets = _evidence(client, source, parent, receipt['parent_prepared_pointer'], case)
            _require(clean == audit['source_package'] and assets == audit['assets'])
            overrides = _request(tuple(receipt['repair_scene_indices']),
                                 {int(key): value for key, value in receipt['shot_prompt_overrides'].items()}, case)
            tasks, director, _ = base._runtime()
            package = receipt['approved_package']
            _require({key: value for key, value in package.items() if key not in {'_recovered_voice', '_recovered_generated_media'}} == audit['package']
                     and package['scenes'] == base._shooting_package(clean, overrides)['scenes']
                     and package.get('sources') == clean.get('sources') and package.get('title') == clean.get('title')
                     and package.get('studio_options') == base._options(source)
                     and director.short_story_package_is_approved(package, source['spec']['topic']))
            require_unchanged_voice_narration(clean, package)
            checksum, voice, media = _contracts(tasks, audit['package'], saved_voice, assets, case)
            _require(receipt['package_sha256'] == checksum and package['_recovered_voice'] == voice
                     and package['_recovered_generated_media'] == media)
            reports = audit.get('retained_visual_reviews')
            _require(type(reports) is list and len(reports) == 6)
            for index, report in enumerate(reports):
                _require(type(report) is dict and type(report.get('scene_index')) is int and report['scene_index'] == index
                         and report.get('raw_sha256') == assets[index]['sha256'] and report.get('missing_review') is False
                         and type(report.get('exact_cut_frames')) is int and report['exact_cut_frames'] > 0)
                _require(base._observed_review(report.get('review')) if index in repairs
                         else base._review_passes(report.get('review'), base._options(source)['quality_threshold']))
            for entry in [voice, *(entries[0] for entries in media['scenes'].values())]:
                base.assets._verify_existing(client, entry['key'], entry['sha256'], entry['size'],
                                             'audio/mpeg' if entry is voice else 'video/mp4')
            source.update(repair_available=True, updated_at=datetime.now(timezone.utc).isoformat())
            transaction.multi()
            transaction.set(studio_state.REPAIR_CHECKPOINT_PREFIX + source_id, json.dumps(receipt, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            nx=True, ex=studio_state.REPAIR_CHECKPOINT_TTL_SECONDS)
            transaction.set(studio_state.JOB_PREFIX + source_id, json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False),
                            ex=studio_state.JOB_TTL_SECONDS)
            _require(transaction.execute() == [True, True])
        return {'status': 'checkpoint_published', 'source_task_id': source_id, 'repair_scene_indices': list(repairs),
                'requires_full_qa': True, 'new_paid_create_requests': 0, 'new_tts_requests': 0}
    except Exception:
        raise RepairedVisualRecoveryError() from None
