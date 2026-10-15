"""Synthetic operator contracts only: no tariff assertion or provider traffic."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import pytest

from app.services.production_spend import SpendBlocked
from app.services.elevenlabs_spend_quotes import (
    ELEVENLABS_SELECTED_VOICE_ID, ELEVENLABS_TTS_ROUTE,
    elevenlabs_price_revision, quote_elevenlabs_tts, validate_elevenlabs_pricing_evidence,
)


NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
KEY = 'offline-elevenlabs-fixture-key'
CREDENTIAL = hashlib.sha256(('elevenlabs\0' + KEY).encode()).hexdigest()
MODEL = {'multilingual_v2_continuous_v1': 'eleven_multilingual_v2',
         'turkish_flash_v2_5_continuous_v1': 'eleven_flash_v2_5'}


def _profile(name):
    # Deliberately fictional rational values: these are NOT ElevenLabs rates.
    return {'profile_id': name, 'model_id': MODEL[name], 'max_text_codepoints': 1000,
            'billing_unit_bound': {'basis': 'submitted_text_unicode_codepoints',
                'scope': 'normalization_model_voice_all_request_charges', 'verified': True,
                'text_unit_numerator': 3, 'text_unit_denominator': 2, 'per_request_units': 7},
            'list_rate': {'micro_usd_numerator': 11, 'billing_unit_denominator': 3}}


@pytest.fixture
def evidence():
    return {'version': 1, 'provider': 'elevenlabs', 'currency': 'USD',
            'account_sha256': 'a' * 64, 'credential_sha256': CREDENTIAL, 'proof_sha256': 'b' * 64,
            'valid_from': '2026-09-09T00:00:00Z', 'valid_until': '2026-10-01T00:00:00Z',
            'voice_id': ELEVENLABS_SELECTED_VOICE_ID, 'route': ELEVENLABS_TTS_ROUTE,
            'profiles': [_profile(name) for name in MODEL]}


@pytest.fixture(scope='module')
def voice_body():
    """Use the actual normalizer/profile builder without importing worker APIs."""
    path = Path(__file__).resolve().parents[1] / 'app/services/voice.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = {'_normalize_qr_code_phrase', 'normalize_turkish_tts', '_voice_request_body'}
    constants = {'_TURKISH_PRONUNCIATION_RULES', 'ELEVENLABS_MULTILINGUAL_V2_MODEL_ID',
                 'ELEVENLABS_TURKISH_SHORT_MODEL_ID'}
    selected = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name in functions
        or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants
                                               for target in node.targets))]
    namespace = {'re': re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_voice_request_body']


def _request(voice_body, text='A clear sentence.', *, flash=False, **options):
    return {'json': voice_body(text, turkish_short_preview=flash, **options),
            'headers': {'xi-api-key': KEY, 'Accept': 'application/json', 'Content-Type': 'application/json'},
            'params': {'output_format': 'mp3_44100_128'}, 'timeout': 180}


def _quote(evidence, request, url=ELEVENLABS_TTS_ROUTE, *, credential=CREDENTIAL, now=NOW):
    return quote_elevenlabs_tts(url, request, evidence=evidence, credential_sha256=credential, now=now)


@pytest.mark.parametrize('flash,text', [(False, 'A small coin reveals the design.'), (True, 'QR KODU ve GPS açık')])
@pytest.mark.parametrize('speed', [0.7, 0.84, 1.0, 1.01, 1.2])
def test_actual_continuous_profiles_use_normalized_transmitted_text(evidence, voice_body, flash, text, speed):
    request = _request(voice_body, text, flash=flash, speed=speed, seed=123)
    original = deepcopy(request)
    quote = _quote(evidence, request)
    size = len(request['json']['text'])
    units = (size * 3 + 1) // 2 + 7
    assert quote.maximum_micro == (units * 11 + 2) // 3
    assert quote.model == ('eleven_flash_v2_5' if flash else 'eleven_multilingual_v2')
    assert quote.provider == 'elevenlabs'
    assert quote.price_revision == elevenlabs_price_revision(evidence, now=NOW)
    assert request == original and request['json']['apply_text_normalization'] == 'on'
    if flash:
        assert request['json']['text'] != text
        assert request['json']['language_code'] == 'tr'
    else:
        assert 'language_code' not in request['json']


def test_utf8_bytes_do_not_replace_the_explicit_codepoint_basis(evidence, voice_body):
    request = _request(voice_body, 'Ş🙂.', flash=True)
    assert len(request['json']['text']) == 3
    assert len(request['json']['text'].encode()) == 7
    # ceil(3 * 3/2) + 7 = 12 units; ceil(12 * 11/3) = 44 USD micro.
    assert _quote(evidence, request).maximum_micro == 44


def test_rounding_and_submicro_rates_are_exact_positive_integer_bounds(evidence, voice_body):
    row = evidence['profiles'][0]
    row['billing_unit_bound'].update(text_unit_numerator=1, text_unit_denominator=1000, per_request_units=0)
    row['list_rate'].update(micro_usd_numerator=1, billing_unit_denominator=1000)
    assert _quote(evidence, _request(voice_body, 'One.')).maximum_micro == 1


def test_no_cash_or_covered_credit_claim_is_part_of_the_price(evidence, voice_body):
    quote = _quote(evidence, _request(voice_body))
    assert set(quote.__dict__) == {'provider', 'model', 'maximum_micro', 'price_revision'}
    assert KEY not in repr(quote) and CREDENTIAL not in repr(quote)


def test_validation_returns_detached_data_and_canonical_utf8_revision(evidence):
    validated = validate_elevenlabs_pricing_evidence(evidence, now=NOW)
    expected = 'elevenlabs-' + hashlib.sha256(json.dumps(
        evidence, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')).hexdigest()
    assert elevenlabs_price_revision(evidence, now=NOW) == expected
    assert len(expected) == 75
    assert elevenlabs_price_revision(dict(reversed(list(evidence.items()))), now=NOW) == expected
    validated['profiles'][0]['list_rate']['micro_usd_numerator'] = 99
    assert evidence['profiles'][0]['list_rate']['micro_usd_numerator'] == 11


@pytest.mark.parametrize('field,value', [('account_sha256', 'c' * 64), ('proof_sha256', 'c' * 64),
                                       ('valid_until', '2026-09-30T00:00:00Z')])
def test_changes_to_operator_contract_change_funding_revision(evidence, field, value):
    old = elevenlabs_price_revision(evidence, now=NOW)
    evidence[field] = value
    assert elevenlabs_price_revision(evidence, now=NOW) != old


@pytest.mark.parametrize('section,field,value', [
    ('list_rate', 'micro_usd_numerator', 12), ('list_rate', 'billing_unit_denominator', 4),
    ('billing_unit_bound', 'text_unit_numerator', 4), ('billing_unit_bound', 'text_unit_denominator', 3),
    ('billing_unit_bound', 'per_request_units', 8),
])
def test_rate_and_unit_bound_changes_cannot_keep_old_revision(evidence, section, field, value):
    old = elevenlabs_price_revision(evidence, now=NOW)
    evidence['profiles'][0][section][field] = value
    assert elevenlabs_price_revision(evidence, now=NOW) != old


@pytest.mark.parametrize('raw', [None, {}, '', [], {'sharing': {'rate': 1.0}}, {'free': True}])
def test_missing_operator_evidence_never_quotes_even_tiny_requests(voice_body, raw):
    with pytest.raises(SpendBlocked):
        _quote(raw, _request(voice_body, '.'))


@pytest.mark.parametrize('field,value', [
    ('version', True), ('version', 2), ('provider', 'abacus'), ('currency', 'credits'),
    ('account_sha256', ''), ('credential_sha256', None), ('proof_sha256', 'not-verified'),
    ('voice_id', 'another-voice'), ('route', 'https://example.invalid/tts'),
    ('profiles', []), ('profiles', {}), ('valid_from', 'yesterday'),
    ('valid_until', '2026-09-31T00:00:00Z'), ('valid_from', '2026-09-09T00:00:00+00:00'),
])
def test_invalid_evidence_is_rejected(evidence, field, value):
    evidence[field] = value
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


@pytest.mark.parametrize('field', ['version', 'provider', 'currency', 'account_sha256', 'credential_sha256',
                                  'proof_sha256', 'valid_from', 'valid_until', 'voice_id', 'route', 'profiles'])
def test_every_evidence_field_is_required(evidence, field):
    evidence.pop(field)
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


@pytest.mark.parametrize('target', ['evidence', 'profile', 'bound', 'rate'])
def test_unknown_evidence_fields_do_not_extend_the_contract(evidence, target):
    row = evidence['profiles'][0]
    chosen = {'evidence': evidence, 'profile': row, 'bound': row['billing_unit_bound'], 'rate': row['list_rate']}[target]
    chosen['auto_top_up'] = True
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


@pytest.mark.parametrize('field,value', [('profile_id', 'unknown'), ('model_id', 'eleven_v3'),
                                       ('max_text_codepoints', True), ('max_text_codepoints', 0),
                                       ('max_text_codepoints', 10001)])
def test_profile_model_and_native_limits_are_fixed(evidence, field, value):
    evidence['profiles'][0][field] = value
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


def test_duplicate_profiles_and_absent_requested_model_are_not_an_allowance(evidence, voice_body):
    evidence['profiles'] = [evidence['profiles'][0], deepcopy(evidence['profiles'][0])]
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)
    evidence['profiles'] = evidence['profiles'][:1]
    with pytest.raises(SpendBlocked):
        _quote(evidence, _request(voice_body, flash=True))


@pytest.mark.parametrize('section,field', [
    ('billing_unit_bound', 'text_unit_numerator'), ('billing_unit_bound', 'text_unit_denominator'),
    ('list_rate', 'micro_usd_numerator'), ('list_rate', 'billing_unit_denominator'),
])
@pytest.mark.parametrize('value', [None, True, 0, -1, 1.0, '1', float('nan'), float('inf'), 1_000_000_001])
def test_price_factors_have_no_zero_float_or_guessed_default(evidence, section, field, value):
    evidence['profiles'][0][section][field] = value
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


@pytest.mark.parametrize('field,value', [
    ('basis', 'utf8_bytes'), ('scope', 'model_only'), ('verified', False), ('verified', 1),
    ('per_request_units', -1), ('per_request_units', True), ('per_request_units', 0.0),
])
def test_unverified_or_incomplete_billing_bound_is_not_accepted(evidence, field, value):
    evidence['profiles'][0]['billing_unit_bound'][field] = value
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


@pytest.mark.parametrize('now', [datetime(2026, 9, 8, tzinfo=timezone.utc),
                               datetime(2026, 10, 1, tzinfo=timezone.utc),
                               datetime(2026, 9, 9), None])
def test_out_of_window_or_naive_clock_never_authorizes(evidence, now):
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=now)


def test_fresh_evidence_must_end_by_current_utc_month(evidence):
    evidence['valid_until'] = '2026-10-01T00:00:01Z'
    with pytest.raises(SpendBlocked):
        validate_elevenlabs_pricing_evidence(evidence, now=NOW)


def test_aware_non_utc_clock_uses_the_same_instant(evidence):
    assert validate_elevenlabs_pricing_evidence(evidence, now=NOW.astimezone(
        timezone(timedelta(hours=3)))) == evidence


@pytest.mark.parametrize('suffix', ['?output_format=mp3_44100_128', '/', '#fragment'])
def test_query_redirect_style_or_fragment_url_is_not_the_fixed_route(evidence, voice_body, suffix):
    with pytest.raises(SpendBlocked):
        _quote(evidence, _request(voice_body), ELEVENLABS_TTS_ROUTE + suffix)


@pytest.mark.parametrize('url', [ELEVENLABS_TTS_ROUTE.replace('https:', 'http:'),
                               ELEVENLABS_TTS_ROUTE.replace('api.elevenlabs.io', 'api.elevenlabs.io:443'),
                               ELEVENLABS_TTS_ROUTE.replace(ELEVENLABS_SELECTED_VOICE_ID, 'other'),
                               ELEVENLABS_TTS_ROUTE.removesuffix('/with-timestamps')])
def test_other_voice_origin_or_plain_audio_route_has_no_quote(evidence, voice_body, url):
    with pytest.raises(SpendBlocked):
        _quote(evidence, _request(voice_body), url)


@pytest.mark.parametrize('field,value', [('previous_text', 'before'), ('next_text', 'after'),
    ('previous_request_ids', ['old']), ('next_request_ids', ['old']), ('pronunciation_dictionary_locators', []),
    ('apply_language_text_normalization', True), ('use_pvc_as_ivc', True), ('unknown', False)])
def test_context_history_or_extra_body_fields_remain_unpriced(evidence, voice_body, field, value):
    request = _request(voice_body)
    request['json'][field] = value
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


@pytest.mark.parametrize('text', ['', '  ', '\ud800', 'a' * 1001])
def test_empty_unencodable_or_over_bound_text_is_rejected(evidence, voice_body, text):
    request = _request(voice_body)
    request['json']['text'] = text
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


@pytest.mark.parametrize('field,value', [('apply_text_normalization', 'off'), ('model_id', 'eleven_v3'),
                                       ('language_code', 'tr'), ('seed', True), ('seed', -1),
                                       ('seed', 4_294_967_296)])
def test_existing_multilingual_profile_and_seed_bounds_are_not_weakened(evidence, voice_body, field, value):
    request = _request(voice_body)
    request['json'][field] = value
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


@pytest.mark.parametrize('seed', [0, 4_294_967_295])
def test_seed_endpoints_preserve_existing_native_contract(evidence, voice_body, seed):
    assert _quote(evidence, _request(voice_body, seed=seed)).maximum_micro > 0


@pytest.mark.parametrize('damage', ['language_missing', 'language_changed', 'speaker_boost', 'style'])
def test_turkish_flash_profile_is_exact(evidence, voice_body, damage):
    request = _request(voice_body, flash=True)
    if damage == 'language_missing':
        request['json'].pop('language_code')
    elif damage == 'language_changed':
        request['json']['language_code'] = 'en'
    else:
        request['json']['voice_settings'][{'speaker_boost': 'use_speaker_boost', 'style': 'style'}[damage]] = 0
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


@pytest.mark.parametrize('field,value', [('stability', 0.41), ('similarity_boost', 0.7),
    ('style', False), ('use_speaker_boost', 1), ('speed', True), ('speed', '1'),
    ('speed', 0.69), ('speed', 1.21), ('speed', float('nan')), ('speed', float('inf'))])
def test_voice_settings_cannot_change_profile_or_admit_nonfinite_values(evidence, voice_body, field, value):
    request = _request(voice_body)
    request['json']['voice_settings'][field] = value
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


@pytest.mark.parametrize('damage', ['extra', 'missing', 'duplicate_key', 'auth_instead', 'accept', 'content_type', 'nonstring'])
def test_only_actual_native_headers_are_accepted(evidence, voice_body, damage):
    request = _request(voice_body)
    headers = request['headers']
    if damage == 'extra': headers['Authorization'] = 'Bearer unknown'
    elif damage == 'missing': headers.pop('xi-api-key')
    elif damage == 'duplicate_key': headers['XI-API-KEY'] = headers.pop('Accept')
    elif damage == 'auth_instead': headers['Authorization'] = headers.pop('xi-api-key')
    elif damage == 'accept': headers['Accept'] = 'audio/mpeg'
    elif damage == 'content_type': headers['Content-Type'] = 'application/x-www-form-urlencoded'
    else: headers['Accept'] = ['application/json']
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)


def test_header_names_are_case_insensitive_without_losing_exact_credential_binding(evidence, voice_body):
    request = _request(voice_body)
    request['headers'] = {name.upper(): value for name, value in request['headers'].items()}
    assert _quote(evidence, request).maximum_micro > 0


@pytest.mark.parametrize('damage', ['actual_key', 'runtime_digest', 'evidence_digest', 'key_newline', 'key_empty'])
def test_actual_header_runtime_digest_and_evidence_must_all_match(evidence, voice_body, damage):
    request = _request(voice_body)
    digest = CREDENTIAL
    if damage == 'actual_key': request['headers']['xi-api-key'] = 'other-key'
    elif damage == 'runtime_digest': digest = 'c' * 64
    elif damage == 'evidence_digest': evidence['credential_sha256'] = 'c' * 64
    elif damage == 'key_newline': request['headers']['xi-api-key'] = KEY + '\n'
    else: request['headers']['xi-api-key'] = ''
    with pytest.raises(SpendBlocked) as caught:
        _quote(evidence, request, credential=digest)
    assert KEY not in str(caught.value) and 'other-key' not in str(caught.value)


@pytest.mark.parametrize('damage', ['params_extra', 'other_output', 'follow_redirects', 'data', 'timeout', 'missing_params'])
def test_transport_shape_is_fixed_and_cannot_add_an_unpriced_feature(evidence, voice_body, damage):
    request = _request(voice_body)
    if damage == 'params_extra': request['params']['enable_logging'] = False
    elif damage == 'other_output': request['params']['output_format'] = 'mp3_44100_192'
    elif damage == 'follow_redirects': request['follow_redirects'] = True
    elif damage == 'data': request['data'] = 'other payload'
    elif damage == 'timeout': request['timeout'] = True
    else: request.pop('params')
    with pytest.raises(SpendBlocked):
        _quote(evidence, request)
