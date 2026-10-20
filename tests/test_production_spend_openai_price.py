"""Default Astra cache writes are reserved without enabling extra API shapes."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json

import fakeredis
import pytest

from app.services import production_spend_quotes as quotes
from app.services.production_spend import LEDGER_KEY, SpendBlocked, SpendLedger, SpendPolicy
from spending_test_support import TEST_KEY, test_funding_policy as funding_policy_fixture


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
CHANNEL = 'UC5v9AvNtD3PTLgo6m1jROOA'
ROOT = '11111111-1111-4111-8111-111111111111'
ROUTE = 'https://api.openai.com/v1/responses'


@pytest.fixture(autouse=True)
def reviewed_date(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(quotes, 'datetime', FixedDateTime)


def request(text='A clear sentence.', output=8192):
    return {'model': 'gpt-6-astra', 'input': text, 'store': False,
            'max_output_tokens': output, 'service_tier': 'default'}


def expected_micro(body):
    # Preserve the existing conservative token ceiling; only the tariff changes.
    ceiling = len(json.dumps(body, ensure_ascii=True, allow_nan=False).encode()) + 4096
    amount = Decimal(ceiling) * Decimal('12.5') + body['max_output_tokens'] * 50
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


@pytest.mark.parametrize('text', ['A clear sentence.', 'A clear sentence..', 'Şeffaf kayıt.'])
@pytest.mark.parametrize('output', [1, 8192, 16384])
def test_default_plain_request_prices_all_input_as_cache_write(text, output):
    body = request(text, output)
    original = deepcopy(body)
    quote = quotes.quote_openai_response(body)
    assert quote.maximum_micro == expected_micro(body)
    assert quote.provider == 'openai' and quote.model == 'gpt-6-astra'
    assert quote.price_revision == quotes.OPENAI_TEXT_PRICE_REVISION != quotes._REVISION
    assert body == original
    assert not any(name.startswith('prompt_cache') for name in body)


def test_half_micro_cache_write_amount_rounds_up():
    body = request('A', output=1)
    # Vary one ASCII byte so the maintained input ceiling has odd parity.
    if (len(json.dumps(body).encode()) + 4096) % 2 == 0:
        body['input'] += 'B'
    ceiling = len(json.dumps(body).encode()) + 4096
    assert ceiling % 2 == 1
    assert quotes.quote_openai_response(body).maximum_micro == (ceiling * 25 + 1) // 2 + 50


def test_large_plain_payload_has_no_free_cache_opt_in_assumption():
    body = request(' '.join(f'evidence{i:05}' for i in range(4096)), output=8192)
    quote = quotes.quote_openai_response(body)
    assert quote.maximum_micro == expected_micro(body)
    old_rate_micro = (len(json.dumps(body).encode()) + 4096) * 10 + 8192 * 50
    assert quote.maximum_micro > old_rate_micro
    assert set(body) == {'model', 'input', 'store', 'max_output_tokens', 'service_tier'}


@pytest.mark.parametrize('field,value', [
    ('prompt_cache_retention', '24h'), ('prompt_cache_key', 'cache'),
    ('prompt_cache_options', {'mode': 'explicit'}), ('tools', []),
    ('service_tier', 'auto'), ('service_tier', 'priority'),
    ('input', [{'role': 'user', 'content': 'A sentence.'}]),
    ('max_output_tokens', 0), ('max_output_tokens', 16385),
])
def test_price_correction_does_not_expand_request_contract(field, value):
    body = request()
    body[field] = value
    with pytest.raises(SpendBlocked, match='^spend_request_not_priced$'):
        quotes.quote_openai_response(body)


def test_openai_revision_does_not_invalidate_existing_video_quotes():
    quote = quotes.quote_runway_video({
        'model': 'gen4.5', 'prompt_text': 'A clearly lit coin.',
        'ratio': '720:1280', 'duration': 5,
    })
    assert quote.maximum_micro == 600_000
    assert quote.price_revision == quotes._REVISION == 'official-2026-09-08-v3'


def new_ledger():
    client = fakeredis.FakeRedis(decode_responses=True)
    ledger = SpendLedger(client, SpendPolicy(*([100_000_000] * 6)), clock=lambda: NOW)
    ledger.initialize()
    return client, ledger


def admission():
    return {'route': ROUTE,
            'credential_sha256': hashlib.sha256(('openai\0' + TEST_KEY).encode()).hexdigest()}


def test_old_funding_revision_blocks_with_whole_ledger_unchanged():
    client, ledger = new_ledger()
    policy = funding_policy_fixture(ledger)
    openai = next(row for row in policy['accounts'] if row['provider'] == 'openai')
    openai['routes'][0]['price_revision'] = quotes._REVISION
    ledger.initialize_funding(policy)
    before = client.hgetall(LEDGER_KEY)
    quote = quotes.quote_openai_response(request())
    with pytest.raises(SpendBlocked, match='^spend_funding_route_not_covered$'):
        ledger.reserve(request_key='stale-openai-price', channel_id=CHANNEL,
                       lineage_id=ROOT, kind='shorts', quote=quote, funding=admission())
    assert client.hgetall(LEDGER_KEY) == before


def test_current_revision_reserves_new_rate_and_preserves_replay_fence():
    client, ledger = new_ledger()
    policy = funding_policy_fixture(ledger)
    for account in policy['accounts']:
        expected = quotes.OPENAI_TEXT_PRICE_REVISION if account['provider'] == 'openai' else quotes._REVISION
        assert all(row['price_revision'] == expected for row in account['routes'])
    ledger.initialize_funding(policy)
    quote = quotes.quote_openai_response(request())
    args = dict(request_key='current-openai-price', channel_id=CHANNEL,
                lineage_id=ROOT, kind='shorts', quote=quote, funding=admission())
    ledger.reserve(**args)
    funding = ledger.funding_snapshot()
    assert funding['cash_reserved_micro'] == quote.maximum_micro == expected_micro(request())
    receipts = [json.loads(value) for _, value in client.hscan_iter(LEDGER_KEY, match='request:*')]
    assert len(receipts) == 1
    assert receipts[0]['quote']['price_revision'] == quotes.OPENAI_TEXT_PRICE_REVISION
    assert receipts[0]['funding']['list_maximum_micro'] == quote.maximum_micro
    assert receipts[0]['funding']['cash_micro'] == quote.maximum_micro
    before = client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='^spend_request_already_reserved$'):
        ledger.reserve(**args)
    assert client.hgetall(LEDGER_KEY) == before


def test_provider_specific_revision_still_expires(monkeypatch):
    class ExpiredDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, 'datetime', ExpiredDateTime)
    with pytest.raises(SpendBlocked, match='^spend_price_review_expired$'):
        quotes.quote_openai_response(request())
