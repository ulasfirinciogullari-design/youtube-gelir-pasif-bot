"""One immutable, unpublished three-minute landscape voice recovery.

This is separate from the thirty-second Shorts contract. It never synthesizes,
retimes, approves audio, changes a series, or falls back to a fresh screenplay.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from app.config import settings
from app.services import gemini_critic, render, studio_state, voice, voice_candidate_recovery


class LongformVoiceRetryError(RuntimeError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _require(condition):
    if not condition:
        raise LongformVoiceRetryError('Long-form saved narration binding is invalid')


def _voice_contract(value):
    return {key: value[key] for key in ('spoken_texts', 'scene_durations',
        'duration_before_fit', 'duration_after_fit', 'tempo_rate')}


def preserved_voice(candidate):
    """Measure the original bytes, returning their existing cues without fitting."""
    try:
        value = candidate['voice_result']
        _require(_digest(_voice_contract(value)) == candidate['voice_contract_sha256'])
        checksum = hashlib.sha256()
        total = 0
        with Path(value['path']).open('rb') as stream:
            while chunk := stream.read(64 * 1024):
                total += len(chunk)
                _require(total <= 14 * 1024 * 1024)
                checksum.update(chunk)
        _require(total >= 1024 and checksum.hexdigest() == candidate['source_audio_sha256'])
        duration = render.media_duration(value['path'])
        recorded = value['duration_after_fit']
        # The checkpoint loader already requires cues to sum within 350 ms.
        # Apply that same mux/measurement bound to the actual stored file.
        _require(type(duration) in (int, float) and math.isfinite(duration)
            and duration > 0 and abs(duration - recorded) <= .35)
        return value
    except Exception:
        raise LongformVoiceRetryError(
            'Long-form saved audio or cues changed; no replacement voice was generated') from None


def _revalidate_story(package, topic):
    """Mandatory existing read-only critic; no rewriting or fabricated approval."""
    from app.services.source_evidence import normalize_evidence_sources

    original = deepcopy(package)
    normalize_evidence_sources(original.get('sources'), min_count=2, max_count=5)
    scenes = original['scenes']
    contract = {
        'story': {
            'claims_supported_by_supplied_source_evidence': True,
            'no_materially_misleading_causality_or_absolute_claims': True,
            'one_coherent_documentary_with_clear_hook_and_payoff': True,
            'natural_turkish_narration_without_filler': True,
            'visual_plan_does_not_present_generic_or_generated_footage_as_authentic_archive': True,
        },
        'scenes': [{'scene_index': index,
            'narration_is_accurate_in_context': True,
            'visual_queries_and_prompts_support_the_spoken_subject': True}
            for index in range(len(scenes))],
    }
    verdict = gemini_critic.run_optional_gemini_critic(
        {'requested_topic': topic, 'format': '16:9 landscape documentary',
         'duration_minutes': 3, 'language': 'tr',
         'candidate_story_in_order': deepcopy(scenes),
         'sources': deepcopy(original['sources'])},
        contract, enabled=True, api_key=str(settings.gemini_api_key or ''),
        model=str(settings.gemini_model or gemini_critic.GEMINI_DEFAULT_MODEL),
        content_style='documentary', fresh_scheduled=False,
    )
    _require(isinstance(verdict, dict) and verdict.get('accepted') is True
        and verdict.get('reviewed_scene_count') == len(scenes))
    voice_candidate_recovery.require_unchanged_voice_narration(package, original)
    original['longform_story_qc'] = {
        'version': 1, 'accepted': True, 'provider': 'gemini',
        'model': verdict['model'], 'reviewed_scene_count': len(scenes),
        'immutable_package_sha256': _digest(package), 'requires_full_media_qa': True,
    }
    return original


def prepare(task_id, source_task_id, runtime_spec, work):
    """Only a same-spec ordinary retry after a pre-media audio failure."""
    phase = 'binding'
    binding_verified = False
    package = None
    try:
        _require(runtime_spec.get('mode') == 'production'
            and runtime_spec.get('format') == 'landscape'
            and type(runtime_spec.get('duration_minutes')) in (int, float)
            and runtime_spec['duration_minutes'] == 3
            and runtime_spec.get('language') == 'tr'
            and runtime_spec.get('publish_after_render') is False
            and runtime_spec.get('workflow') == 'auto'
            and runtime_spec.get('content_style') == 'documentary'
            and runtime_spec.get('visual_mix') == 'real_first'
            and runtime_spec.get('channel_id') is None
            and not any(key.startswith(('production_', 'series_', 'repair_')) for key in runtime_spec))
        source, child = studio_state.get_job(source_task_id), studio_state.get_job(task_id)
        _require(isinstance(source, dict) and isinstance(child, dict)
            and source.get('task_id') == source_task_id and child.get('task_id') == task_id
            and source.get('state') == 'FAILURE' and source.get('kind') == 'render'
            and source.get('failure_stage') in {'audio_qc', 'audio_qc_retry'}
            and source.get('retry_child_task_id') == task_id and source.get('retry_claimed') is True
            and child.get('parent_id') == source_task_id and child.get('kind') == 'render'
            and child.get('state') in {'PENDING', 'STARTED', 'PROGRESS'}
            and source.get('spec') == runtime_spec and child.get('spec') == runtime_spec)
        binding_verified = True
        phase = 'pre_media_boundary'
        # These failures precede every paid-video path. Old long-form jobs do
        # not have a preview ledger, so absence is not mislabeled as cap=zero.
        _require(all(not record.get(key) for record in (source, child) for key in (
            'result', 'publication_hold', 'repair_checkpoint', 'generated_asset_candidates',
            'voice_replacement', 'audio_pause_repair')))
        _require(all(key not in record for record in (source, child) for key in (
            'paid_create_slots_used', 'paid_create_slots_remaining', 'preview_total_paid_create_cap')))
        checkpoint = source.get('audio_candidate_checkpoint')
        phase = 'checkpoint'
        _require(isinstance(checkpoint, dict))  # Never return None and buy a new voice.
        candidate = voice_candidate_recovery.load_voice_retry_candidate(
            source_task_id, task_id, checkpoint, work)
        package, saved_voice = candidate['package'], candidate['voice_result']
        phase = 'immutable_spoken_contract'
        _require(sum(bool(scene.get('ai_prompt')) for scene in package['scenes']) <= 2)
        expected = [voice.normalize_turkish_tts(scene['narration'],
            ensure_terminal=index + 1 == len(package['scenes']))
            for index, scene in enumerate(package['scenes'])]
        _require(saved_voice.get('spoken_texts') == expected)
        prepared = {'voice_result': saved_voice, 'source_audio_sha256': candidate['audio_sha256'],
            'voice_contract_sha256': _digest(_voice_contract(saved_voice)),
            'preserve_audio_bytes': True}
        phase = 'audio_bytes_and_cues'
        preserved_voice(prepared)
        phase = 'source_and_independent_story_review'
        prepared['package'] = _revalidate_story(package, runtime_spec['topic'])
        studio_state.update_job(task_id, voice_candidate_reuse={
            'source_task_id': source_task_id, 'new_tts_requests': 0,
            'requires_full_qa': True, 'preserve_audio_bytes': True,
            'source_audio_sha256': prepared['source_audio_sha256'],
            'voice_contract_sha256': prepared['voice_contract_sha256'],
        })
        return prepared
    except Exception as exc:
        if binding_verified:
            try:
                diagnostics = {'status': 'failed', 'stage': phase,
                    'reason_code': 'independent_critic_rejected' if isinstance(
                        exc, gemini_critic.GeminiCriticRejected) else phase + '_unverified',
                    'new_tts_requests': 0, 'requires_full_qa': True}
                fields = {'longform_voice_revalidation': diagnostics}
                if isinstance(exc, gemini_critic.GeminiCriticRejected):
                    from app.services.planning_diagnostics import planning_failure_diagnostics

                    fields['prepaid_story_diagnostics'] = planning_failure_diagnostics(exc, package)
                    # The shared legacy sanitizer omits unknown review keys.
                    # Retain only this contract's exact false-check paths too,
                    # not raw model prose or an arbitrary exception message.
                    diagnostics['failed_checks'] = re.findall(
                        r'\$\.(?:story\.(?:claims_supported_by_supplied_source_evidence|'
                        r'no_materially_misleading_causality_or_absolute_claims|'
                        r'one_coherent_documentary_with_clear_hook_and_payoff|'
                        r'natural_turkish_narration_without_filler|'
                        r'visual_plan_does_not_present_generic_or_generated_footage_as_authentic_archive)'
                        r'|scenes\[[0-9]{1,3}\]\.(?:narration_is_accurate_in_context|'
                        r'visual_queries_and_prompts_support_the_spoken_subject))', str(exc))[:12]
                studio_state.update_job(task_id, **fields)
            except Exception:
                pass  # Safe diagnostic availability never grants approval.
        raise LongformVoiceRetryError(
            'Long-form saved narration revalidation failed; no replacement voice was generated') from None
