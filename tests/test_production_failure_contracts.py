from copy import deepcopy
import json

import pytest

from app.services.production_failures import (
    ProductionContentError, classify_failure, classified_hold_reason, content_rejection,
)
from app.services.production_spend import SpendBlocked


@pytest.mark.parametrize('code,stage,reason', [
    ('story_quality_exhausted', 'director_qc', 'story_rejected'),
    ('story_contract_rejected', 'director_qc', 'story_rejected'),
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
                                  'visual_quality_exhausted', 'render_quality_exhausted',
                                  'story_contract_rejected'])
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


def test_legacy_exhausted_audio_observation_can_be_held_but_not_with_changed_stage_or_evidence():
    message = ('Audio narration QA could not be verified after one bounded same-audio retry before paid media: '
        '{"provider_attempts":[{"attempt":1,"providers":[]},{"attempt":2,"providers":[]}]}')
    error = RuntimeError(message)
    job = {'failure_stage': 'audio_qc', 'error': message, 'failure_classification': classify_failure(error, 'audio_qc')}
    before = deepcopy(job)
    assert classified_hold_reason(job) == 'review_unverified' and job == before
    for stage in ('research', 'upload', 'publishing'):
        altered = {**job, 'failure_stage': stage, 'failure_classification': classify_failure(error, stage)}
        assert classified_hold_reason(altered) is None
    job['error'] += ' extra'
    assert classified_hold_reason(job) is None


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


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('stage', ['visual_qc', 'ai_scene', 'final_visual_qc_rescue'])
def test_local_visual_request_rejection_can_be_held_without_rewriting_job(legacy, stage):
    from app.services.abacus_router_adapter import AbacusRouterError
    error = AbacusRouterError('abacus_router_request_invalid')
    evidence = classify_failure(error, stage)
    assert evidence['category'] == 'review_unverified'
    if legacy:
        evidence.update(code='spending_blocked', category='spending_blocked')
    job = {'error': str(error), 'failure_stage': stage, 'failure_classification': evidence}
    original = deepcopy(job)
    assert classified_hold_reason(job) == 'review_unverified' and job == original
    job['error'] += ' changed'
    assert classified_hold_reason(job) is None


@pytest.mark.parametrize('stage', ['research', 'audio_qc', 'render', 'upload', 'youtube_publish'])
def test_visual_input_rejection_cannot_clear_another_stage(stage):
    error = SpendBlocked('abacus_router_request_invalid')
    job = {'error': str(error), 'failure_stage': stage,
           'failure_classification': classify_failure(error, stage)}
    assert classified_hold_reason(job) is None


@pytest.mark.parametrize('damage', ['phrase', 'missing_slot', 'extra_word', 'missing_scene', 'dual_narration'])
def test_actual_countable_decoder_rejects_without_leaving_an_unclassified_failure(damage):
    from app.services import countable_stock_narration as countable
    from test_countable_stock_narration import encoded, package
    value=encoded(package())
    if damage=='phrase':value['scenes'][0]['narration_words']['w01']='two words'
    elif damage=='missing_slot':value['scenes'][0]['narration_words'].pop('w11')
    elif damage=='extra_word':value['scenes'][0]['narration_words']['w12']='extra'
    elif damage=='missing_scene':value['scenes'].pop()
    else:value['scenes'][0]['narration']='second representation'
    original=deepcopy(value)
    with pytest.raises(countable.NarrationContractError)as caught:countable.decode_director(value)
    assert value==original
    error=caught.value
    assert isinstance(error,ValueError)
    for stage in ('director_qc','research','audio_qc','render','youtube_publish'):
        job={'error':str(error),'failure_stage':stage,'failure_classification':classify_failure(error,stage)}
        assert classified_hold_reason(job)==('story_rejected' if stage=='director_qc' else None)


@pytest.mark.parametrize('damage', [None,'stage','hash','message','category','code'])
def test_exact_legacy_word_rejection_requires_unchanged_worker_evidence(damage):
    from app.services.countable_stock_narration import WORD_FIELD_ERROR
    job={'error':WORD_FIELD_ERROR,'failure_stage':'director_qc',
         'failure_classification':classify_failure(ValueError(WORD_FIELD_ERROR),'director_qc')}
    if damage=='stage':job['failure_stage']='youtube_publish'
    elif damage=='message':job['error']+=' changed'
    elif damage=='hash':job['failure_classification']['error_sha256']='0'*64
    elif damage=='category':job['failure_classification']['category']='spending_blocked'
    elif damage=='code':job['failure_classification']['code']='spending_blocked'
    original=deepcopy(job)
    assert classified_hold_reason(job)==('story_rejected' if damage is None else None)
    assert job==original
