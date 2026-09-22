"""Worker failure contracts, independent of human-facing exception wording.

A classification is evidence about a failed attempt, never permission to retry,
spend or publish. The schedule still requires its complete unpublished lineage,
funding policy and daily hold allowance before consuming a content failure.
"""
from dataclasses import dataclass
import hashlib


_STORY_STAGES = frozenset({'research', 'director_qc'})
_AUDIO_STAGES = frozenset({'voice_and_visuals', 'audio_qc', 'audio_qc_retry', 'audio_pause_recheck'})
_VISUAL_STAGES = frozenset({'visual_qc', 'ai_scene', 'ai_scene_generation',
    'pre_runway_budget_rescue', 'pre_runway_stock_tournament_2',
    'final_visual_qc', 'final_visual_qc_ai_repair', 'final_visual_qc_rescue'})
_REVIEW_STAGES = _STORY_STAGES | _AUDIO_STAGES | _VISUAL_STAGES
_CONTENT_CODES = {
    'story_quality_exhausted': ('story_rejected', _STORY_STAGES),
    'audio_quality_exhausted': ('audio_rejected', _AUDIO_STAGES),
    'audio_review_unverified': ('review_unverified', _AUDIO_STAGES),
    'visual_quality_exhausted': ('stock_rejected', _VISUAL_STAGES),
    'render_quality_exhausted': ('render_rejected', frozenset({'render'})),
}
_REVIEW_CODES = frozenset({'included_router_response_unverified',
    'commissioning_reasoning_response_unverified', 'commissioning_reasoning_previous_outcome_unknown',
    'commissioning_reasoning_outcome_unknown', 'commissioning_reasoning_legacy_outcome_unknown',
    'commissioning_reasoning_reservation_uncertain', 'commissioning_reasoning_capture_uncertain',
    'included_router_previous_outcome_unknown', 'prepaid_audio_response_unverified',
    'prepaid_audio_previous_outcome_unknown', 'included_stock_pool_unverified',
    'included_visual_completion_unverified', 'included_router_settlement_uncertain',
    'included_router_reservation_uncertain'})
_SOURCE_CODES = frozenset({'included_research_unconsulted_source',
    'included_research_primary_source_required', 'included_research_primary_source_unavailable'})
_VISUAL_INPUT_CODES = frozenset({'abacus_router_request_invalid'})
_COMMISSIONED_VIDEO_CODES = frozenset({
    'commissioning_video_provider_rejected', 'commissioning_video_generation_failed',
    'commissioning_video_outcome_unverified', 'commissioning_video_poll_unavailable',
    'commissioning_video_poll_timeout', 'commissioning_video_previous_outcome_unknown',
    'commissioning_video_episode_capacity',
})


@dataclass(frozen=True)
class _ContentRejection:
    code: str


def content_rejection(error, code):
    """Tag only an actual exhausted content gate; retain its exception type."""
    if not isinstance(error, Exception) or code not in _CONTENT_CODES:
        raise ValueError('Invalid content failure contract')
    error._production_content_rejection = _ContentRejection(code)
    return error


class ProductionContentError(RuntimeError):
    """An exhausted local/editorial correction must not rebuild the whole job."""
    def __init__(self, message, *, code='story_quality_exhausted'):
        super().__init__(message)
        content_rejection(self, code)


def _digest(message):
    return hashlib.sha256(message.encode('utf-8', errors='replace')).hexdigest()


def classify_failure(error, stage):
    """Called at the worker's terminal handler, before Celery serializes it."""
    from app.services.production_spend import SpendBlocked
    from billiard.exceptions import WorkerLostError

    code, category = 'unclassified_failure', 'unclassified'
    tag = getattr(error, '_production_content_rejection', None)
    if type(tag) is _ContentRejection and tag.code in _CONTENT_CODES:
        if stage in _CONTENT_CODES[tag.code][1]:
            code = tag.code
            category = ('review_unverified' if _CONTENT_CODES[code][0] == 'review_unverified'
                        else 'content_rejected')
    elif type(error) is ValueError and str(error) == 'included_factual_audit_invalid' and stage == 'director_qc':
        code, category = str(error), 'review_unverified'
    elif isinstance(error, SpendBlocked):
        # These are fixed provider protocol codes, not prose or substrings.
        if str(error) == 'credit_pool_has_uncertain_intent' and stage in _AUDIO_STAGES:
            code, category = str(error), 'review_unverified'
        elif str(error) in _VISUAL_INPUT_CODES and stage in _VISUAL_STAGES:
            code, category = str(error), 'review_unverified'
        elif str(error) in _COMMISSIONED_VIDEO_CODES and stage in _VISUAL_STAGES:
            code, category = str(error), 'review_unverified'
        elif str(error) == 'production_media_outcome_unverified' and stage in _AUDIO_STAGES | _VISUAL_STAGES | {'render'}:
            code, category = str(error), 'review_unverified'
        elif str(error) in _SOURCE_CODES and stage == 'research':
            code, category = str(error), 'source_unavailable'
        elif str(error) in _REVIEW_CODES and stage in _REVIEW_STAGES:
            code, category = str(error), 'review_unverified'
        else:
            code, category = 'spending_blocked', 'spending_blocked'
    elif type(error) is WorkerLostError and stage in _REVIEW_STAGES | {'queued', 'render', 'plan_retry'}:
        code, category = 'worker_process_lost', 'execution_interrupted'
    return {'version': 1, 'code': code, 'category': category, 'stage': stage,
            'error_sha256': _digest(str(error))}


def classified_hold_reason(job):
    """Reject stale, malformed or wrong-stage evidence; never read prose."""
    evidence = job.get('failure_classification')
    if type(evidence) is not dict or set(evidence) != {
        'version', 'code', 'category', 'stage', 'error_sha256',
    } or type(evidence['version']) is not int or evidence['version'] != 1:
        return None
    error, stage = job.get('error'), job.get('failure_stage')
    if (type(error) is not str or type(stage) is not str
            or evidence['stage'] != stage or evidence['error_sha256'] != _digest(error)):
        return None
    code, category = evidence['code'], evidence['category']
    if type(code) is not str or type(category) is not str:
        return None
    # A competing voice prevented this attempt from reserving or sending.
    # Preserve its original failure and every occupied credit receipt. Normal
    # funding preflight blocks fresh production until the pool really settles.
    if (error == 'credit_pool_has_uncertain_intent' and stage in _AUDIO_STAGES
            and ((code == error and category == 'review_unverified')
                 or code == category == 'spending_blocked')):
        return 'review_unverified'
    # The router rejected this locally before reservation/transport (for
    # example an oversized sampled JPEG). Preserve legacy terminal evidence;
    # the complete unpublished lineage and fresh funding still gate a hold.
    if (error in _VISUAL_INPUT_CODES and stage in _VISUAL_STAGES
            and ((code == error and category == 'review_unverified')
                 or code == category == 'spending_blocked')):
        return 'review_unverified'
    if code == error and code in _COMMISSIONED_VIDEO_CODES and category == 'review_unverified' and stage in _VISUAL_STAGES:
        return 'review_unverified'
    if (code == error == 'production_media_outcome_unverified' and category == 'review_unverified'
            and stage in _AUDIO_STAGES | _VISUAL_STAGES | {'render'}):
        return 'review_unverified'
    # This storage-only wrapper used SpendBlocked as its error type before it
    # had its own classification. It never meant a cash/credit budget decision.
    # Preserve that terminal record; ordinary hold checks still verify the
    # complete unpublished lineage and never free its provider reservations.
    if (code == category == 'spending_blocked' and error == 'included_stock_pool_unverified'
            and stage in _AUDIO_STAGES | _VISUAL_STAGES):
        return 'review_unverified'
    # A previous local-recording failure is not a budget allocation decision.
    # Keep its unknown request occupied and the original job unchanged. Only
    # the ordinary complete-unpublished-lineage hold may advance the schedule.
    if (code == category == 'spending_blocked' and error in {
            'included_router_settlement_uncertain', 'included_router_reservation_uncertain'}
            and stage in _REVIEW_STAGES):
        return 'review_unverified'
    # Before the typed terminal tag, this exact exhausted two-observation
    # failure was left unclassified. Its complete unpublished lineage still
    # has to pass the normal hold checks; no old job or provider receipt changes.
    if (code == 'unclassified_failure' and category == 'unclassified'
            and stage in _AUDIO_STAGES and error ==
            'Audio narration QA could not be verified after one bounded same-audio retry before paid media: '
            '{"provider_attempts":[{"attempt":1,"providers":[]},{"attempt":2,"providers":[]}]}'):
        return 'review_unverified'
    if code in _CONTENT_CODES:
        reason, stages = _CONTENT_CODES[code]
        expected_category = 'review_unverified' if reason == 'review_unverified' else 'content_rejected'
        return reason if stage in stages and category == expected_category else None
    if category == 'review_unverified' and code in _REVIEW_CODES and stage in _REVIEW_STAGES:
        return 'review_unverified' if error == code else None
    if category == 'source_unavailable' and code in _SOURCE_CODES and stage == 'research':
        return 'research_sources_unavailable' if error == code else None
    if category == 'review_unverified' and code == error == 'included_factual_audit_invalid' and stage == 'director_qc':
        return 'story_rejected'
    if category == 'execution_interrupted' and code == 'worker_process_lost' and stage in _REVIEW_STAGES | {'queued', 'render', 'plan_retry'}:
        return 'worker_interrupted'
    return None
