"""Private, bounded fresh-story correction without resetting an episode budget.

A correction note is an operator's reason to create a new unapproved story,
never a factual, media or publication approval. The old audio and every request
receipt remain intact. Normal Studio retries cannot request this path.
"""
from copy import deepcopy
import json
import re


def validate_correction(client, source, spec, root_id, snapshots, correction, *, reserved=False):
    from app.services import full_video_rebuild as rebuild
    from app.services import production_spend_runtime as runtime
    from app.services import production_included_router as included
    from app.services import production_prepaid_audio as audio
    from app.services import production_credit_ledger as voice
    from app.services.production_spend import LEDGER_KEY
    from app.services.production_cash_disabled import ANCHOR_KEY
    from app.services.audio_checkpoint import _plain_text, _public_sources

    require, snapshot = rebuild._require, rebuild._snapshot
    require(runtime.enforcement_enabled() and included.enabled(), 'native_story_correction_not_enabled')
    fields = {'version', 'kind', 'source_audio_sha256', 'source_package_sha256',
              'correction_note', 'evidence_url', 'evidence_sha256'}
    require(type(correction) is dict and set(correction) == fields
        and type(correction['version']) is int and correction['version'] == 1
        and correction['kind'] == 'native_story_correction', 'native_story_correction_invalid')
    require(all(type(correction[k]) is str and re.fullmatch('[0-9a-f]{64}', correction[k])
        for k in ('source_audio_sha256', 'source_package_sha256', 'evidence_sha256')),
        'native_story_correction_invalid')
    note = _plain_text(correction['correction_note'], 2000).strip()
    # This helper checks public citation syntax only, not the note's truth.
    require(len(note) >= 12 and len(_public_sources([
        {'url': correction['evidence_url'], 'evidence': note[:600]}])) == 1,
        'native_story_correction_invalid')
    require(source.get('failure_stage') in {'director_qc', 'visual_qc', 'ai_scene',
        'pre_runway_budget_rescue', 'final_visual_qc'}, 'native_story_correction_pre_media_only')
    require(not any(source.get(k) for k in ('generated_asset_candidates', 'repair_checkpoint',
        'qa_workprint', 'voice_replacement')), 'native_story_correction_pre_media_only')
    pointer = source.get('audio_candidate_checkpoint')
    require(type(pointer) is dict and set(pointer) == rebuild._AUDIO_POINTER_FIELDS
        and type(pointer['version']) is int and pointer['version'] == 1
        and pointer['status'] == 'unapproved_candidate' and pointer['qa_approved'] is False
        and pointer['requires_full_qa'] is True
        and type(pointer['size']) is int and 1024 <= pointer['size'] <= 14 * 1024 * 1024
        and pointer['audio_sha256'] == correction['source_audio_sha256']
        and pointer['package_sha256'] == correction['source_package_sha256']
        and type(pointer['metadata_sha256']) is str and rebuild._SHA.fullmatch(pointer['metadata_sha256']),
        'native_story_correction_candidate_changed')
    prefix = 'audio_candidates/' + source['task_id'] + '/' + pointer['audio_sha256']
    require(pointer['audio_key'] == prefix + '/candidate.mp3'
        and pointer['metadata_key'] == prefix + '/metadata-' + pointer['metadata_sha256'] + '.json',
        'native_story_correction_candidate_changed')

    # Independent root witness: deleting a child's grant cannot make the
    # episode appear eligible for another new narration.
    root_grant = snapshot(client, rebuild.NATIVE_CORRECTION_ROOT_PREFIX + root_id,
        snapshots, optional=True)
    if reserved:
        root_grant = rebuild._object(root_grant)
        require(root_grant.get('source_task_id') == source['task_id']
            and root_grant.get('native_story_correction') == correction
            and root_grant == rebuild._object(snapshot(client,
                rebuild.SOURCE_PREFIX + source['task_id'], snapshots)),
            'native_story_correction_witness_changed')
    else:
        require(root_grant is None, 'native_story_correction_already_used')

    # Only one fresh-voice correction in this existing retry lineage. The
    # current grant lives on the new child, outside the source's ancestry.
    task_id, seen = source['task_id'], set()
    while task_id is not None:
        require(task_id not in seen and len(seen) < rebuild.MAX_RETRY_HOPS)
        seen.add(task_id)
        job = rebuild._object(snapshot(client, rebuild.JOB_PREFIX + task_id, snapshots))
        grant = snapshot(client, rebuild.POLICY_PREFIX + task_id, snapshots, optional=True)
        require(grant is None or 'native_story_correction' not in rebuild._object(grant),
            'native_story_correction_already_used')
        task_id = job.get('parent_id')

    kinds = {LEDGER_KEY: 'hash', ANCHOR_KEY: 'string',
        voice.STATE_KEY: 'hash', voice.JOURNAL_KEY: 'hash'}
    for ledger in (included, audio):
        kinds.update({key: 'string' for key in (ledger.STATE_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY)})
    for key, kind in kinds.items():
        snapshot(client, key, snapshots, kind=kind)
    context = {'channel_id': spec['production_channel_id'],
        'connection_id': spec['production_connection_id'], 'lineage_id': root_id, 'kind': 'shorts'}
    money = snapshots[LEDGER_KEY]['value']
    require(all(json.loads(money.get('binding:' + key, 'null')) == context
        for key in (root_id, source['task_id'])), 'native_story_correction_context_unbound')
    # Read the existing, anchored entitlements; no initialization, refund,
    # credit reservation or USD permit occurs in this preparation step.
    included.preflight_production(context['channel_id'], kind='shorts')
    foundation = runtime.configured_ledger(read_timeout=2)
    foundation.client = client
    for cls, minimum in ((included.IncludedRouterLedger, 8), (audio.PrepaidAudioLedger, 4)):
        ledger = cls(foundation)
        with client.pipeline() as pipe:
            state, journal = ledger._read(pipe)
            used = sum(row['context']['lineage_id'] == root_id for row in journal['requests'].values())
            require(used + minimum <= state['policy']['max_requests_per_lineage'],
                'native_story_correction_episode_limit')
            pipe.multi(); pipe.ping(); ledger._ack(pipe, [True])
    for key, kind in kinds.items():
        snapshot(client, key, snapshots, kind=kind)
    return {'native_story_correction': deepcopy(correction),
        'retained_audio_checkpoint_sha256': rebuild._digest(pointer)}
