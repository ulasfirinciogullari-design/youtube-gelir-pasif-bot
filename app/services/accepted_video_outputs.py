"""Read already completed Fal outputs after a local candidate-save conflict.

This module cannot submit generation requests or change provider receipts.
The ordinary retained-media pipeline rechecks every recovered clip and voice.
"""
import hashlib
import json

from app.services import content_plan as plan, commissioning_video as native
from app.services import fal_video as fal


def failed_scenes(source):
    from app.services.content_plan_retained_completion import VISUAL_ERROR
    diagnostic = json.loads(source['error'][len(VISUAL_ERROR):])
    rejected = diagnostic.get('rejected')
    failures = (diagnostic.get('provider_generation_failures') or {}).get('failures')
    plan._require(source.get('failure_stage') == 'final_visual_qc_rescue'
        and diagnostic.get('stage') == 'after_rescue' and diagnostic.get('total') == 30
        and type(rejected) is dict and 1 <= len(rejected) <= 7
        and diagnostic.get('accepted') == 30 - len(rejected)
        and diagnostic.get('repair_checkpoint_available') is False
        and all(str(int(i)) == i and 0 <= int(i) < 30 and type(r.get('score')) is int
                and 0 <= r['score'] < 86 for i, r in rejected.items())
        and type(failures) is list and len(failures) == len(rejected)
        and {r.get('scene_index') for r in failures} == {int(i) for i in rejected}
        and all(r.get('exception_class') == 'WatchError' and r.get('stage') == 'final_repair' for r in failures)
        and (source.get('retained_long_media') or {}).get('stock_only') is False
        and (source.get('retained_long_media') or {}).get('new_tts_requests') == 0)
    classification = source.get('failure_classification') or {}
    plan._require(classification.get('category') == 'content_rejected'
        and classification.get('code') == 'visual_quality_exhausted'
        and classification.get('error_sha256') == hashlib.sha256(source['error'].encode()).hexdigest())
    return sorted(int(i) for i in rejected)


def completed(row):
    """A conclusive result is either an exact media URL or a retained filter."""
    from app.services.fal_video_catalog import PROVIDERS
    from app.services.youtube_auth import _decrypt_json
    descriptor = row['request']; model = descriptor['model']
    plan._require(model in PROVIDERS and row.get('create') and row.get('result'))
    accepted = native._payload(row['create']); request_id = accepted['request_id']
    fal._validate_queue_url(accepted['response_url'], request_id=request_id, suffix='', model=model)
    result = row['result']; raw = _decrypt_json(result['encrypted_response'])['response'].encode()
    plan._require(native._sha(raw) == result['response_sha256'])
    value = json.loads(raw)
    if result['http_status'] == 422:
        plan._require(fal._fal_error_is_policy(fal._fal_error_types(value)))
        return None  # Retained as a filter; never a different-provider retry permit.
    plan._require(result['http_status'] == 200)
    return fal._parse_video_result(value, request_id, model=model)


def selected(journal, source, prior):
    indices = failed_scenes(source); found = {}; filtered = set()
    packages = {p['package_sha256'] for p in source['generated_asset_candidates']['entries']}
    plan._require(len(packages) == 1 and source['audio_candidate_checkpoint']['audio_sha256']
        == prior['evidence']['transcript']['audio_sha256'])
    for identity, row in journal['requests'].items():
        descriptor = row['request']
        if descriptor.get('continuation_task_id') != source['task_id']:
            continue
        index = descriptor.get('scene_index')
        plan._require(identity == native._sha(native._raw(descriptor).encode())
            and index in prior['evidence']['generation_scenes']
            and descriptor.get('package_sha256') in packages)
        media = completed(row)
        if media is None:
            filtered.add(index)
        elif index in indices:
            plan._require(index not in found)
            found[index] = identity
    plan._require(set(found) == set(indices) and not filtered.intersection(indices))
    return found


def load(client, root, source, proof, prepared, work):
    """Download exact existing URLs. Full content/motion QA still follows."""
    from app import tasks
    from app.services import generated_asset_checkpoint as assets
    from app.services.runway import download_generated_scene
    from app.services.content_plan_retained_completion import _metadata
    journal = plan._object(client.get(native.PREFIX + root))
    canonical = assets._candidate_package(_metadata(source)['package'])
    plan._require(canonical == assets._candidate_package(prepared['package'])
        and prepared['source_audio_sha256'] == source['audio_candidate_checkpoint']['audio_sha256'])
    clips = {}
    for index, identity in proof['accepted_outputs'].items():
        index = int(index); row = journal['requests'][identity]
        plan._require(plan._sha(row) == proof['video_records'][identity]
            and row['request']['scene_index'] == index
            and row['request']['continuation_task_id'] == source['task_id'])
        result = completed(row); plan._require(result is not None)
        target = work / f'accepted_fal_s{index:02d}.mp4'
        download_generated_scene(result, target)
        tasks._validate_recovered_generated_clip(target,
            minimum_duration=max(5., prepared['voice_result']['scene_durations'][index] + .35))
        clips[index] = {**tasks._generated_visual_spec(target, provider=result['provider'], provider_attempts=1),
            'retained_source_task_id': source['task_id'], 'retained_native_request_sha256': identity}
    return clips
