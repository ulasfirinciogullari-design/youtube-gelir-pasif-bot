from copy import deepcopy
import json

import pytest

from app.services.production_failures import (
    ProductionContentError, classify_failure, classified_hold_reason, content_rejection,
)
from app.services.production_spend import SpendBlocked


@pytest.mark.parametrize('code,stage,reason', [
    ('story_quality_exhausted', 'director_qc', 'story_rejected'),
    ('audio_quality_exhausted', 'audio_qc_retry', 'audio_rejected'),
    ('audio_quality_exhausted', 'audio_pause_recheck', 'audio_rejected'),
    ('audio_review_unverified', 'audio_qc', 'review_unverified'),
    ('visual_quality_exhausted', 'final_visual_qc_rescue', 'stock_rejected'),
    ('render_quality_exhausted', 'render', 'render_rejected'),
])
@pytest.mark.parametrize('message', ['New provider-specific wording.', 'Denetim düzeltmelerden sonra hâlâ başarısız.'])
def test_worker_contract_survives_text_changes_and_json_roundtrip(code, stage, reason, message):
    error = content_rejection(RuntimeError(message), code)
    job = {'failure_stage': stage, 'error': str(error),
           'failure_classification': classify_failure(error, stage)}
    assert classified_hold_reason(json.loads(json.dumps(job))) == reason
    assert str(error) == message


@pytest.mark.parametrize('change', ['version', 'bool_version', 'stage', 'code', 'category', 'message',
                                  'hash', 'extra', 'code_list', 'evidence_list'])
def test_changed_or_malformed_worker_evidence_cannot_authorize_continuation(change):
    error = ProductionContentError('Short-preview story rejected with an old matching prefix')
    job = {'failure_stage': 'director_qc', 'error': str(error),
           'failure_classification': classify_failure(error, 'director_qc')}
    evidence = job['failure_classification']
    if change == 'message': job['error'] += ' changed'
    elif change == 'hash': evidence['error_sha256'] = '0' * 64
    elif change == 'extra': evidence['publish_eligible'] = True
    elif change == 'code_list': evidence['code'] = []
    elif change == 'evidence_list': job['failure_classification'] = []
    else: evidence[change.removeprefix('bool_')] = {
        'version': 2, 'bool_version': True, 'stage': 'research',
        'code': 'unclassified_failure', 'category': 'spending_blocked',
    }[change]
    from app.services.production_quality_holds import _reason

    assert classified_hold_reason(job) is None and _reason(job) is None


@pytest.mark.parametrize('stage', ['upload', 'youtube_publish', 'publishing', 'unknown', 'failed'])
@pytest.mark.parametrize('code', ['story_quality_exhausted', 'audio_quality_exhausted',
                                  'visual_quality_exhausted', 'render_quality_exhausted'])
def test_no_content_code_applies_to_delivery_or_unknown_stage(stage, code):
    error = content_rejection(RuntimeError('Rejected.'), code)
    assert classified_hold_reason({'failure_stage': stage, 'error': str(error),
        'failure_classification': classify_failure(error, stage)}) is None


@pytest.mark.parametrize('error', [SpendBlocked('spend_day_limit'), SpendBlocked('spend_store_unavailable'),
    SpendBlocked('spend_request_already_reserved'), RuntimeError('Final visual quality gate rejected'),
    ValueError('Unknown payment or upload result'), RuntimeError('included_router_response_unverified')])
def test_budget_integrity_and_untyped_errors_are_not_editorial_skip_instructions(error):
    evidence = classify_failure(error, 'final_visual_qc')
    assert classified_hold_reason({'failure_stage': 'final_visual_qc', 'error': str(error),
        'failure_classification': evidence}) is None
    assert evidence['category'] == ('spending_blocked' if isinstance(error, SpendBlocked) else 'unclassified')


def test_string_copy_and_caller_supplied_attribute_cannot_invent_worker_content_evidence():
    error = RuntimeError('fake rejection')
    error._production_content_rejection = {'code': 'story_quality_exhausted'}
    for candidate in (error, str(error)):
        assert classify_failure(candidate, 'director_qc')['category'] == 'unclassified'


@pytest.mark.parametrize('error,stage,reason', [
    (SpendBlocked('included_research_primary_source_unavailable'), 'research', 'research_sources_unavailable'),
    (ValueError('included_factual_audit_invalid'), 'director_qc', 'story_rejected'),
    (SpendBlocked('included_router_response_unverified'), 'final_visual_qc_rescue', 'review_unverified'),
    (SpendBlocked('prepaid_audio_previous_outcome_unknown'), 'audio_qc_retry', 'review_unverified'),
])
def test_fixed_protocol_codes_keep_original_uncertainty_without_prose_matching(error, stage, reason):
    job = {'failure_stage': stage, 'error': str(error), 'failure_classification': classify_failure(error, stage)}
    before = deepcopy(job)
    assert classified_hold_reason(job) == reason and job == before


@pytest.mark.parametrize('code', ['included_research_unconsulted_source',
    'included_research_primary_source_required', 'included_research_primary_source_unavailable'])
def test_actual_retrieved_source_guard_keeps_its_hold_contract(code):
    from app.services.included_research_sources import _require

    with pytest.raises(SpendBlocked) as caught:
        _require(False, code)
    error = caught.value
    job = {'failure_stage': 'research', 'error': str(error),
           'failure_classification': classify_failure(error, 'research')}
    assert classified_hold_reason(job) == 'research_sources_unavailable'
