from copy import deepcopy

import pytest

from app.services import production_credit_funding as credit
from app.services.production_credit_ledger import CreditLedger
from app.services.production_spend import SpendBlocked
from test_production_cash_disabled import money
from test_production_credit_ledger import client, policy, NOW, binding, context, intent
from test_elevenlabs_credit_adapter import kwargs, voice_body, prepared, observe, response


def test_actual_turkish_short_voice_body_preserves_flash_model_and_exact_meter(kwargs, voice_body):
    kwargs['json'] = voice_body('Bir sent neden pahalı?', turkish_short_preview=True, seed=123)
    item = prepared(kwargs)
    assert item.model == credit.TURKISH_SHORT_MODEL
    assert item.payload['json'] == kwargs['json'] and item.payload['json']['language_code'] == 'tr'
    assert observe(item, response(item, cost='11'))['actual_credit_cost'] == 11


def test_additional_native_model_requires_explicit_v2_commissioning(client, policy):
    foundation = money(client)
    flash = {**intent(), 'model': credit.TURKISH_SHORT_MODEL}
    with pytest.raises(SpendBlocked):
        credit.reserve_credit_intent(policy, credit.initial_credit_state(policy, now=NOW),
            intent=flash, now=NOW, **binding(policy))
    both = {**deepcopy(policy), 'version': 2, 'additional_models': [credit.TURKISH_SHORT_MODEL]}
    ledger = CreditLedger(client, foundation=foundation, clock=lambda: NOW)
    ledger.initialize(both)
    assert ledger.binding_snapshot()['additional_models'] == [credit.TURKISH_SHORT_MODEL]
    receipt = ledger.reserve(intent=flash, production_context=context(client), **binding(both))
    assert receipt['reserved_credits'] == both['allocation_credits']
    assert foundation.snapshot()['historical_cash_micro'] is None
    with pytest.raises(SpendBlocked):
        credit.validate_credit_policy({**both, 'additional_models': ['other-model']}, now=NOW)
