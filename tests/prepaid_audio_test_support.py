from copy import deepcopy

from app.services import production_prepaid_audio as prepaid
from app.services.production_included_router import _stamp


def audio_policy(base, now, **overrides):
    return {**deepcopy(base), 'kind': prepaid.KIND, 'model': prepaid.PREPAID_MODEL,
        'max_requests_total': 1000, 'reported_credit_balance': 15768,
        'credit_balance_observed_at': _stamp(now), 'balance_source': 'owner_report_not_provider_api',
        'current_available_credits': None, 'billing_controls_evidence_sha256': 'a' * 64,
        'model_catalogue_evidence_sha256': 'b' * 64,
        'funding_basis': 'prepaid_credits_with_automatic_purchases_disabled', **overrides}
