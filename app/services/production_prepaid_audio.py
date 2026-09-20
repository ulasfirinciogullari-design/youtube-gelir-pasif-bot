"""Audio understanding from existing prepaid Abacus credits, never a top-up.

RouteLLM itself rejects input_audio. A separately commissioned fixed Gemini
route is required. The owner-reported balance is historical evidence, not a
current available balance, cash allowance or credit-to-USD conversion. This
policy requires confirmed automatic purchases off, bounds requests, and stops
on provider rejection. No balance/entitlement initialization or renewal occurs
in a production request; old unknown requests remain occupied in their ledger.
"""
from copy import deepcopy
from datetime import timedelta

from app.services.production_included_router import (
    IncludedRouterLedger, _require, _hash, _date, validate_policy as router_policy,
)
from app.services.abacus_router_audio_adapter import MODEL, PREPAID_MODEL

PREFIX = 'youtube_studio:{production_spend}:prepaid_audio:v1:'
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (PREFIX + name for name in ('state', 'journal', 'anchor'))
MODE_FIELD = 'prepaid_audio_policy'
KIND = 'existing_subscription_prepaid_audio'


def enabled():
    from app.config import settings
    return getattr(settings, 'studio_abacus_prepaid_audio', False) is True


def validate_policy(policy, now):
    _require(type(policy) is dict and policy.get('kind') == KIND
             and policy.get('model') == PREPAID_MODEL, 'prepaid_audio_policy_invalid')
    extras = {'max_requests_total', 'reported_credit_balance', 'credit_balance_observed_at',
              'balance_source', 'current_available_credits', 'billing_controls_evidence_sha256',
              'model_catalogue_evidence_sha256', 'funding_basis'}
    _require(extras <= set(policy), 'prepaid_audio_policy_invalid')
    # Reuse the strict channel, lifetime, credential, request ceilings and
    # zero-cash validation without granting this policy a RouteLLM entitlement.
    common = {k: v for k, v in policy.items() if k not in extras}
    common.update(kind='existing_subscription_included_router', model=MODEL)
    router_policy(common, now)
    _require(type(policy['max_requests_total']) is int and 1 <= policy['max_requests_total'] <= 1000
        and type(policy['reported_credit_balance']) is int and 1 <= policy['reported_credit_balance'] <= 1000000
        and policy['balance_source'] == 'owner_report_not_provider_api'
        and policy['current_available_credits'] is None
        and policy['funding_basis'] == 'prepaid_credits_with_automatic_purchases_disabled'
        and all(_hash(policy[k]) for k in ('billing_controls_evidence_sha256', 'model_catalogue_evidence_sha256')),
        'prepaid_audio_policy_invalid')
    observed, start = _date(policy['credit_balance_observed_at']), _date(policy['valid_from'])
    _require(observed <= start <= observed + timedelta(days=1), 'prepaid_audio_opening_evidence_stale')
    return deepcopy(policy)


class PrepaidAudioLedger(IncludedRouterLedger):
    prefix, state_key, journal_key, anchor_key = PREFIX, STATE_KEY, JOURNAL_KEY, ANCHOR_KEY
    mode_field = MODE_FIELD
    purposes = frozenset({'blind_asr', 'prosody'})
    validate_policy = staticmethod(validate_policy)
