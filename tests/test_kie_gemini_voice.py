"""The alternative model shares funding without reopening old failed probes."""
import json

import httpx
import pytest

from app.services import kie_gemini_voice as gemini, kie_voice_ledger as ledger
from app.services import kie_voice_adapter as api
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_kie_voice import box, kie, commission, generate, KEY


def setup(kie):
    kie.balance = 10080
    commission(kie)
    original = kie.handler
    pending = {}
    def handler(request):
        if request.url.path.endswith('/credit'):
            return original(request)
        kie.requests.append(request)
        if request.method == 'POST':
            task = 'model_probe_' + str(len(pending)).zfill(8)
            pending[task] = json.loads(request.content)['model']
            return httpx.Response(200, json={'code': 200, 'data': {'taskId': task}})
        task = request.url.params['taskId']
        return httpx.Response(200, json={'code': 200, 'data': {
            'taskId': task, 'model': pending[task], 'state': 'success', 'creditsConsumed': 0.75,
            'resultJson': json.dumps({'resultUrls': ['https://tempfile.aiquickdraw.com/check.mp3']})}})
    kie.handler = handler


def extension(kie):
    return gemini.commission(kie.foundation, owner_evidence_sha256='b' * 64)


def request(kie, language='tr', voice='Fenrir', text='Her başarılı iş, gerçek bir ihtiyacı karşılar.'):
    body, ceiling = gemini.request_body(text, voice, language=language)
    journal = ledger.Journal(kie.foundation,
        {'kind': 'connection_probe', 'language': language, 'voice_id': voice}, body, ceiling)
    return api.generate(body, KEY, journal, sleep=lambda _: None)


def test_new_model_requires_separate_authority_before_any_paid_request(kie):
    setup(kie)
    with pytest.raises(SpendBlocked, match='model_not_authorized'):
        request(kie)
    assert [r.method for r in kie.requests] == ['GET']
    assert not kie.client.exists(gemini.KEY, ledger.ACTIVE_KEY)


def test_extension_never_changes_policy_balance_old_receipts_or_cash(kie):
    setup(kie); generate(kie)
    prior = {key: kie.client.get(key) for key in (ledger.POLICY_KEY, ledger.JOURNAL_KEY, ledger.ANCHOR_KEY)}
    cash = kie.client.hgetall(LEDGER_KEY)
    value = extension(kie)
    assert value['allocation_added_microcredits'] == 0
    assert all(kie.client.get(key) == raw for key, raw in prior.items())
    assert kie.client.hgetall(LEDGER_KEY) == cash
    assert not kie.client.exists(ledger.ACTIVE_KEY)
    with pytest.raises(SpendBlocked):
        extension(kie)


def test_gemini_charge_and_replay_use_original_funding_journal(kie):
    setup(kie); extension(kie)
    first = request(kie)
    assert request(kie) == first
    state = ledger.status(kie.client)
    assert state['allocation_microcredits'] == 10_080_000_000
    assert state['committed_microcredits'] == 750000
    assert state['remaining_microcredits'] == 10_079_250_000
    assert state['requests'] == 1 and state['unknown_or_pending'] == 0
    assert [r.method for r in kie.requests] == ['GET', 'POST', 'GET']


def test_eight_legacy_probes_stay_closed_and_new_model_has_only_two_checks(kie):
    setup(kie)
    for index in range(8):
        kie.scope = {**kie.scope, 'voice_id': 'legacy_voice_' + str(index)}
        generate(kie)
    old = json.loads(kie.client.get(ledger.JOURNAL_KEY))['requests']
    extension(kie)
    request(kie, language='tr'); request(kie, language='en')
    with pytest.raises(SpendBlocked, match='probe_limit'):
        request(kie, voice='Kore')
    kie.scope = {**kie.scope, 'voice_id': 'another_legacy_voice'}
    with pytest.raises(SpendBlocked, match='probe_limit'):
        generate(kie)
    current = json.loads(kie.client.get(ledger.JOURNAL_KEY))['requests']
    assert len(current) == 10 and all(current[key] == value for key, value in old.items())
    assert [r.method for r in kie.requests].count('POST') == 10


def test_unknown_new_model_request_stays_reserved_and_is_not_rebought(kie):
    setup(kie); extension(kie)
    def timeout(request):
        kie.requests.append(request)
        raise httpx.ReadTimeout('outcome unknown')
    kie.handler = timeout
    with pytest.raises(api.KieVoiceError, match='outcome_unknown'):
        request(kie)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        request(kie)
    state = ledger.status(kie.client)
    assert state['committed_microcredits'] == 48_000_000 and state['unknown_or_pending'] == 1
    assert [r.method for r in kie.requests].count('POST') == 1


@pytest.mark.parametrize('damage', ['ceiling', 'dialogue', 'speaker', 'temperature', 'language'])
def test_unpriced_request_shapes_cannot_reserve_or_send(kie, damage):
    setup(kie); extension(kie)
    body, ceiling = gemini.request_body('Bir fikrin değeri önemlidir.', language='tr')
    if damage == 'ceiling':
        ceiling -= 1
    elif damage == 'dialogue':
        body['input']['dialogue_turns'].append(dict(body['input']['dialogue_turns'][0]))
    elif damage == 'speaker':
        body['input']['speakers'][0]['voice_name'] = 'unknown'
    elif damage == 'temperature':
        body['input']['temperature'] = True
    else:
        body['input']['speakers'][0]['accent'] = 'Unknown language'
    journal = ledger.Journal(kie.foundation,
        {'kind': 'connection_probe', 'language': 'tr', 'voice_id': 'Fenrir'}, body, ceiling)
    with pytest.raises(SpendBlocked):
        api.generate(body, KEY, journal, sleep=lambda _: None)
    assert [r.method for r in kie.requests] == ['GET']


def test_missing_extension_cannot_hide_or_reset_spent_new_model_credits(kie):
    setup(kie); extension(kie); request(kie)
    before = kie.client.get(ledger.JOURNAL_KEY)
    kie.client.delete(gemini.KEY)
    with pytest.raises(SpendBlocked):
        ledger.status(kie.client)
    with pytest.raises(SpendBlocked):
        extension(kie)
    assert kie.client.get(ledger.JOURNAL_KEY) == before


def test_bound_covers_full_documented_input_and_audio_token_limits():
    spec = gemini.SPEC
    actual_limit_microcredits = (spec['input_token_limit'] * spec['input_credits_per_million']
        + spec['output_token_limit'] * spec['output_credits_per_million'])
    assert actual_limit_microcredits == 47_022_080
    assert actual_limit_microcredits <= spec['maximum_microcredits']
