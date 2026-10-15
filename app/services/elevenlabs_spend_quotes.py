"""Pure operator-evidenced bounds for the existing continuous ElevenLabs voice.

No public price, sharing.rate conversion, balance or free-call default exists.
The operator evidence must explicitly bound ALL normalization, model, custom
voice and fixed request charges for the declared request profile. Validating
this immutable contract is not independent proof of a provider tariff.

For the actual transmitted text, the declared upper bound is:
  units = ceil(Unicode codepoints * numerator / denominator) + per_request_units
  maximum USD micro = ceil(units * micro_usd_numerator / billing_unit_denominator)
Both ceilings are integer arithmetic. Units are contract-defined upper bounds,
not an assumption that input characters equal the provider's invoice units.

Funding/covered credits, account reconciliation and the USD10 cash ceiling stay
in the existing funding ledger. The runtime must match this evidence's account
hash AND the actual credential to that ledger, then atomically reserve before
sending the identical frozen body/headers/params. This module never sends,
records, activates, refreshes, refunds, logs credentials or changes a voice.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from app.services.production_spend import SpendBlocked, SpendQuote


ELEVENLABS_SELECTED_VOICE_ID = 'WtOce4YK0dDSxlVlSdBh'
ELEVENLABS_TTS_ROUTE = (
    'https://api.elevenlabs.io/v1/text-to-speech/'
    + ELEVENLABS_SELECTED_VOICE_ID + '/with-timestamps'
)
_PROFILES = {
    'multilingual_v2_continuous_v1': ('eleven_multilingual_v2', 10_000),
    'turkish_flash_v2_5_continuous_v1': ('eleven_flash_v2_5', 40_000),
}
_HASH = re.compile(r'[0-9a-f]{64}')
_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
_MAX_EVIDENCE_BYTES = 16_384
_MAX_REQUEST_BYTES = 300_000
_MAX_FACTOR = 1_000_000_000
_MAX_QUOTE_MICRO = 10_000_000_000


def _require(condition, code='spend_elevenlabs_evidence_invalid'):
    if not condition:
        raise SpendBlocked(code)


def _exact(value, fields, code='spend_elevenlabs_evidence_invalid'):
    _require(type(value) is dict and set(value) == set(fields), code)


def _integer(value, maximum=_MAX_FACTOR, *, zero=False):
    _require(type(value) is int and (0 if zero else 1) <= value <= maximum)
    return value


def _canonical(value, code='spend_elevenlabs_evidence_invalid'):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked(code) from None


def _utc(now):
    _require(isinstance(now, datetime) and now.tzinfo is not None
             and now.utcoffset() is not None, 'spend_elevenlabs_clock_invalid')
    return now.astimezone(timezone.utc)


def _timestamp(value):
    _require(type(value) is str and _STAMP.fullmatch(value) is not None)
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked('spend_elevenlabs_evidence_invalid') from None


def _ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def _maximum_micro(profile, codepoints):
    bound, rate = profile['billing_unit_bound'], profile['list_rate']
    units = _ceil_div(codepoints * bound['text_unit_numerator'], bound['text_unit_denominator'])
    units += bound['per_request_units']
    return _ceil_div(units * rate['micro_usd_numerator'], rate['billing_unit_denominator'])


def validate_elevenlabs_pricing_evidence(raw: dict, *, now: datetime) -> dict:
    """Return a detached strict account-tariff contract; never infer evidence."""
    now = _utc(now)
    _exact(raw, {'version', 'provider', 'currency', 'account_sha256', 'credential_sha256',
                 'proof_sha256', 'valid_from', 'valid_until', 'voice_id', 'route', 'profiles'})
    _require(type(raw['version']) is int and raw['version'] == 1
             and raw['provider'] == 'elevenlabs' and raw['currency'] == 'USD'
             and raw['voice_id'] == ELEVENLABS_SELECTED_VOICE_ID
             and raw['route'] == ELEVENLABS_TTS_ROUTE)
    for field in ('account_sha256', 'credential_sha256', 'proof_sha256'):
        _require(type(raw[field]) is str and _HASH.fullmatch(raw[field]) is not None)
    start, end = _timestamp(raw['valid_from']), _timestamp(raw['valid_until'])
    try:
        boundary = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    except ValueError:
        raise SpendBlocked('spend_elevenlabs_clock_invalid') from None
    _require((start.year, start.month) == (now.year, now.month)
             and start <= now < end <= boundary, 'spend_elevenlabs_evidence_expired')
    profiles = raw['profiles']
    _require(type(profiles) is list and 1 <= len(profiles) <= 2)
    seen = set()
    for profile in profiles:
        _exact(profile, {'profile_id', 'model_id', 'max_text_codepoints', 'billing_unit_bound', 'list_rate'})
        name = profile['profile_id']
        _require(type(name) is str and name in _PROFILES and name not in seen)
        seen.add(name)
        model, maximum = _PROFILES[name]
        _require(profile['model_id'] == model)
        _integer(profile['max_text_codepoints'], maximum)
        bound = profile['billing_unit_bound']
        _exact(bound, {'basis', 'scope', 'verified', 'text_unit_numerator',
                       'text_unit_denominator', 'per_request_units'})
        _require(bound['basis'] == 'submitted_text_unicode_codepoints'
                 and bound['scope'] == 'normalization_model_voice_all_request_charges'
                 and bound['verified'] is True)
        _integer(bound['text_unit_numerator'])
        _integer(bound['text_unit_denominator'])
        _integer(bound['per_request_units'], zero=True)
        rate = profile['list_rate']
        _exact(rate, {'micro_usd_numerator', 'billing_unit_denominator'})
        _integer(rate['micro_usd_numerator'])
        _integer(rate['billing_unit_denominator'])
        _require(1 <= _maximum_micro(profile, profile['max_text_codepoints']) <= _MAX_QUOTE_MICRO)
    _require(len(_canonical(raw)) <= _MAX_EVIDENCE_BYTES)
    return deepcopy(raw)


def elevenlabs_price_revision(evidence: dict, *, now: datetime) -> str:
    """Bind account, proof, date, profiles and rates to the funding route revision."""
    evidence = validate_elevenlabs_pricing_evidence(evidence, now=now)
    return 'elevenlabs-' + hashlib.sha256(_canonical(evidence)).hexdigest()


def _request_profile(body, evidence):
    code = 'spend_elevenlabs_request_not_priced'
    _require(type(body) is dict, code)
    model = body.get('model_id')
    _require(type(model) is str, code)
    profiles = [profile for profile in evidence['profiles'] if profile['model_id'] == model]
    _require(len(profiles) == 1, code)
    profile = profiles[0]
    flash = model == 'eleven_flash_v2_5'
    fields = {'text', 'model_id', 'apply_text_normalization', 'voice_settings'}
    if flash:
        fields.add('language_code')
    _require(set(body) in (fields, fields | {'seed'}), code)
    _require(body['apply_text_normalization'] == 'on', code)
    if flash:
        _require(body['language_code'] == 'tr', code)
    text = body['text']
    _require(type(text) is str and bool(text.strip())
             and 1 <= len(text) <= profile['max_text_codepoints'], code)
    try:
        text.encode('utf-8', errors='strict')
    except UnicodeError:
        raise SpendBlocked(code) from None
    if 'seed' in body:
        _require(type(body['seed']) is int and 0 <= body['seed'] <= 4_294_967_295, code)
    settings = body['voice_settings']
    expected = {'stability': 0.50, 'similarity_boost': 0.75} if flash else {
        'stability': 0.40, 'similarity_boost': 0.80, 'style': 0.0, 'use_speaker_boost': True}
    _exact(settings, {*expected, 'speed'}, code)
    for field, value in expected.items():
        if type(value) is bool:
            _require(settings[field] is value, code)
        else:
            _require(type(settings[field]) in (int, float) and settings[field] == value, code)
    speed = settings['speed']
    _require(type(speed) in (int, float) and math.isfinite(speed) and 0.7 <= speed <= 1.2, code)
    _require(len(_canonical(body, code)) <= _MAX_REQUEST_BYTES, code)
    return profile, len(text)


def quote_elevenlabs_tts(url, kwargs, *, evidence, credential_sha256, now: datetime) -> SpendQuote:
    """Price exactly the existing continuous request, using its actual key/text.

    The API key is consumed solely for a digest equality check, never returned.
    `credential_sha256` must come from runtime admission of the actual headers.
    An empty/missing evidence object cannot grant a quote, even for tiny text.
    """
    evidence = validate_elevenlabs_pricing_evidence(evidence, now=now)
    code = 'spend_elevenlabs_request_not_priced'
    _require(type(url) is str and url == evidence['route'] == ELEVENLABS_TTS_ROUTE, code)
    _exact(kwargs, {'json', 'headers', 'params', 'timeout'}, code)
    _exact(kwargs['params'], {'output_format'}, code)
    _require(kwargs['params']['output_format'] == 'mp3_44100_128', code)
    _require(type(kwargs['timeout']) in (int, float) and kwargs['timeout'] == 180, code)
    headers = kwargs['headers']
    _require(type(headers) is dict and len(headers) == 3
             and all(type(key) is str and type(value) is str for key, value in headers.items()), code)
    normalized = {key.lower(): value for key, value in headers.items()}
    _exact(normalized, {'xi-api-key', 'accept', 'content-type'}, code)
    _require(normalized['accept'] == 'application/json'
             and normalized['content-type'] == 'application/json', code)
    key = normalized['xi-api-key']
    _require(type(key) is str and 1 <= len(key) <= 8192
             and all(32 < ord(char) < 127 for char in key), 'spend_funding_credential_invalid')
    actual_digest = hashlib.sha256(('elevenlabs\0' + key).encode('utf-8')).hexdigest()
    _require(type(credential_sha256) is str and credential_sha256 == actual_digest
             and evidence['credential_sha256'] == actual_digest, 'spend_funding_credential_mismatch')
    profile, codepoints = _request_profile(kwargs['json'], evidence)
    revision = 'elevenlabs-' + hashlib.sha256(_canonical(evidence)).hexdigest()
    return SpendQuote('elevenlabs', profile['model_id'], _maximum_micro(profile, codepoints), revision).validate()
