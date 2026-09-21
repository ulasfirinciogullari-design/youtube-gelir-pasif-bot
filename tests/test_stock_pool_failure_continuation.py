from copy import deepcopy

import pytest

from app.services.production_failures import classify_failure, classified_hold_reason
from app.services.production_spend import SpendBlocked


@pytest.mark.parametrize('stage', ['audio_qc', 'visual_qc', 'ai_scene', 'pre_runway_budget_rescue'])
def test_storage_wrapper_is_a_missing_asset_review_not_a_new_spending_allowance(stage):
    error = SpendBlocked('included_stock_pool_unverified')
    record = classify_failure(error, stage)
    job = {'error': str(error), 'failure_stage': stage, 'failure_classification': record}
    before = deepcopy(job)
    assert record['category'] == 'review_unverified'
    assert classified_hold_reason(job) == 'review_unverified' and job == before
    legacy = {**job, 'failure_classification': {**record, 'category': 'spending_blocked', 'code': 'spending_blocked'}}
    assert classified_hold_reason(legacy) == 'review_unverified'


@pytest.mark.parametrize('damage', ['wrong_stage', 'changed_error', 'real_budget_block', 'bad_hash'])
def test_legacy_storage_exception_cannot_consume_other_spending_or_publication_failures(damage):
    error = SpendBlocked('included_stock_pool_unverified')
    record = classify_failure(error, 'audio_qc')
    record.update(category='spending_blocked', code='spending_blocked')
    job = {'error': str(error), 'failure_stage': 'audio_qc', 'failure_classification': record}
    if damage == 'wrong_stage': job['failure_stage'] = record['stage'] = 'youtube_upload'
    elif damage == 'changed_error': job['error'] += ' changed'
    elif damage == 'real_budget_block':
        error = SpendBlocked('cash_allowance_exhausted');job['error'] = str(error)
        job['failure_classification'] = classify_failure(error, 'audio_qc')
    else: record['error_sha256'] = '0' * 64
    assert classified_hold_reason(job) is None
