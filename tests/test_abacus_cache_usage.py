from copy import deepcopy

import pytest

from app.services import abacus_router_adapter as text, abacus_router_audio_adapter as audio
from test_abacus_router_optional_response_identity import sample, actual, without_identity


@pytest.mark.parametrize('kind', ['text', 'asr', 'prosody'])
@pytest.mark.parametrize('reasoning', [False, True])
def test_native_cache_counter_is_observed_without_inventing_input_arithmetic_or_credit_price(kind, reasoning):
    prepared, payload, observe = sample(kind)
    usage = {'input_tokens': 3883, 'output_tokens': 35,
        'cache_read_input_tokens': 4039, 'raw_input_tokens': 3480}
    if reasoning: usage['reasoning_tokens'] = 50
    payload = {**without_identity(payload), 'usage': usage}
    before = deepcopy(payload)
    observed = observe(prepared, actual(prepared, payload))
    assert observed.usage == usage and payload == before
    assert 'total_tokens' not in observed.usage and 'credits' not in observed.usage
    assert observed.underlying_model_verified is False


@pytest.mark.parametrize('kind', ['text', 'asr', 'prosody'])
@pytest.mark.parametrize('invalid', [-1, True, 1.0, 1_000_000_001, None, '5'])
def test_native_cache_counter_remains_bounded_and_typed(kind, invalid):
    prepared, payload, observe = sample(kind)
    payload['usage'] = {'input_tokens': 3883, 'output_tokens': 35,
        'raw_input_tokens': 3480, 'cache_read_input_tokens': invalid}
    with pytest.raises((text.AbacusRouterError, audio.AbacusRouterAudioError)):
        observe(prepared, actual(prepared, payload))
