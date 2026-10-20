"""Offline request and actual-response validation for native ElevenLabs credits.

No transport, ledger, account lookup, policy activation or USD quote lives here.
The caller must reserve against authenticated account/source evidence before
one send, then bind the observed meter to that acknowledged reservation. These
helpers do not grant a send permit or approve audio. Unknown usage raises the
existing terminal SpendBlocked exception; it must never trigger a new synthesis.

The request is an immutable private snapshot. Its payload accessor returns a
detached body for the EXISTING runtime fingerprint (operation is the URL path).
Response proof hashes bind actual wire bytes and meter headers, not a guessed
character tariff. The trusted transport must separately bound bytes while
reading; this pure observer rejects an already-loaded oversized response.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re

import httpx

from app.services.production_credit_funding import MODEL, TURKISH_SHORT_MODEL, PROVIDER, ROUTE, VOICE_ID
from app.services.production_spend import SpendBlocked


MAX_REQUEST_BYTES = 300_000
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_FORMAT = 'mp3_44100_128'
_OPERATION = '/v1/text-to-speech/' + VOICE_ID + '/with-timestamps'
_REQUEST_ERROR = 'credit_tts_request_invalid'
_RESPONSE_ERROR = 'credit_tts_response_unverified'
_METER_ERROR = 'credit_tts_meter_unverified'


def _require(condition, code):
    if not condition:
        raise SpendBlocked(code)


def _canonical(value, code):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked(code) from None


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _body(body):
    code = _REQUEST_ERROR
    required = {'text', 'model_id', 'apply_text_normalization', 'voice_settings'}
    short_turkish = type(body) is dict and body.get('model_id') == TURKISH_SHORT_MODEL
    if short_turkish:
        required.add('language_code')
    _require(type(body) is dict and set(body) in (required, required | {'seed'}), code)
    _require(type(body['model_id']) is str and body['model_id'] in (MODEL, TURKISH_SHORT_MODEL)
             and type(body['apply_text_normalization']) is str
             and body['apply_text_normalization'] == 'on', code)
    text = body['text']
    _require(type(text) is str and bool(text.strip()) and 1 <= len(text) <= 10_000, code)
    if 'seed' in body:
        _require(type(body['seed']) is int and 0 <= body['seed'] <= 4_294_967_295, code)
    settings = body['voice_settings']
    expected = {'stability': .50, 'similarity_boost': .75} if short_turkish else {
        'stability': .40, 'similarity_boost': .80, 'style': 0.0}
    extra = {'speed'} if short_turkish else {'use_speaker_boost', 'speed'}
    _require(type(settings) is dict and set(settings) == {*expected, *extra}, code)
    if short_turkish:
        _require(body['language_code'] == 'tr', code)
    else:
        _require(settings['use_speaker_boost'] is True, code)
    for field, value in expected.items():
        _require(type(settings[field]) in (int, float) and settings[field] == value, code)
    speed = settings['speed']
    _require(type(speed) in (int, float) and math.isfinite(speed) and .7 <= speed <= 1.2, code)
    result = _canonical(body, code)
    _require(len(result) <= MAX_REQUEST_BYTES, code)
    return result


@dataclass(frozen=True, repr=False)
class PreparedCreditRequest:
    """Private immutable transport input; never serialize it into a job/ledger."""

    _body_bytes: bytes
    _header_pairs: tuple
    _voice_id: str = VOICE_ID

    def __repr__(self):
        return '<PreparedCreditRequest elevenlabs native credits redacted>'

    @property
    def provider(self):
        return PROVIDER

    @property
    def operation(self):
        return '/v1/text-to-speech/' + self._voice_id + '/with-timestamps'

    @property
    def route(self):
        return 'https://api.elevenlabs.io' + self.operation

    @property
    def model(self):
        return json.loads(self._body_bytes)['model_id']

    @property
    def voice_id(self):
        return self._voice_id

    @property
    def credential_sha256(self):
        return _sha(('elevenlabs\0' + dict(self._header_pairs)['xi-api-key']).encode('utf-8'))

    @property
    def payload(self):
        return {'json': json.loads(self._body_bytes), 'params': {'output_format': _FORMAT}}

    def wire_kwargs(self):
        """Return detached actual secrets/body for trusted dispatch only."""
        return {**self.payload, 'headers': dict(self._header_pairs), 'timeout': 180}


def inspect_credit_request(url, kwargs):
    """Freeze exactly the existing selected Multilingual continuous request.

    Input size limits describe accepted content, never a billed-credit bound.
    No account/root authority or financially initialized state is implied.
    """
    code = _REQUEST_ERROR
    from app.services.narrator_rotation import VOICE_IDS, route
    voice = next((voice for voice in VOICE_IDS if type(url) is str and url == route(voice)), None)
    _require(voice is not None, code)
    _require(type(kwargs) is dict and set(kwargs) == {'json', 'headers', 'params', 'timeout'}, code)
    params = kwargs['params']
    _require(type(params) is dict and set(params) == {'output_format'}
             and type(params['output_format']) is str and params['output_format'] == _FORMAT, code)
    _require(type(kwargs['timeout']) in (int, float) and kwargs['timeout'] == 180, code)
    headers = kwargs['headers']
    _require(type(headers) is dict and len(headers) == 3
             and all(type(k) is str and type(v) is str for k, v in headers.items()), code)
    normalized = {k.lower(): v for k, v in headers.items()}
    _require(set(normalized) == {'xi-api-key', 'accept', 'content-type'}
             and normalized['accept'] == 'application/json'
             and normalized['content-type'] == 'application/json', code)
    key = normalized['xi-api-key']
    _require(1 <= len(key) <= 8192 and all(32 < ord(c) < 127 for c in key), code)
    return PreparedCreditRequest(_body(kwargs['json']), tuple(sorted(normalized.items())), voice)


def _unique_headers(headers, code, *, names=None):
    result = {}
    try:
        for raw_name, raw_value in headers.raw:
            name = raw_name.decode('ascii').lower()
            if names is not None and name not in names:
                continue
            value = raw_value.decode('ascii')
            _require(name not in result, code)
            result[name] = value
        return result
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, AttributeError):
        raise SpendBlocked(code) from None


def _json_body(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, _RESPONSE_ERROR)
            result[key] = value
        return result

    def constant(_):
        raise SpendBlocked(_RESPONSE_ERROR)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except SpendBlocked:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SpendBlocked(_RESPONSE_ERROR) from None


def _verify_wire_request(prepared, request):
    code = _RESPONSE_ERROR
    _require(type(request) is httpx.Request and request.method == 'POST'
             and str(request.url.copy_with(query=None)) == prepared.route
             and list(request.url.params.multi_items()) == [('output_format', _FORMAT)], code)
    headers = _unique_headers(request.headers, code)
    allowed = {'xi-api-key', 'accept', 'content-type', 'host', 'content-length',
               'accept-encoding', 'connection', 'user-agent'}
    _require(set(headers) <= allowed and headers.get('host') == 'api.elevenlabs.io', code)
    _require(all(headers.get(k) == v for k, v in prepared._header_pairs), code)
    try:
        raw = request.content
    except httpx.RequestNotRead:
        raise SpendBlocked(code) from None
    _require(type(raw) is bytes and len(raw) <= MAX_REQUEST_BYTES
             and headers.get('content-length') == str(len(raw)), code)
    actual = _json_body(raw)
    _require(_canonical(actual, code) == prepared._body_bytes, code)
    return _sha(raw)


def observe_credit_response(prepared, response, *, reserved_credits):
    """Return three metering fields for a caller-bound immutable settlement.

    Complete charged bytes may fail later audio/JSON QA. Missing meter, redirects,
    wrong requests, unconsumed/oversized bodies and ambiguous headers fail closed.
    The caller retains the original timestamp/receipt when replaying settlement.
    A meter exceeding an internal hold remains known usage. The policy validator
    applies the evidenced account quota; the observer only bounds finite counts.
    """
    code = _RESPONSE_ERROR
    try:
        _require(type(prepared) is PreparedCreditRequest, code)
        _require(inspect_credit_request(prepared.route, prepared.wire_kwargs()) == prepared, code)
        _require(type(reserved_credits) is int and 0 < reserved_credits <= 1_000_000_000, _METER_ERROR)
        _require(type(response) is httpx.Response and 200 <= response.status_code < 300
                 and not response.history and response.is_stream_consumed, code)
        wire_body_sha = _verify_wire_request(prepared, response.request)
        raw = response.content
        _require(type(raw) is bytes and len(raw) <= MAX_RESPONSE_BYTES, code)
        # CDN headers such as Set-Cookie/Vary may legitimately repeat. Only
        # metering and response framing/content headers must be unambiguous;
        # request/auth header validation above remains strict for every header.
        headers = _unique_headers(response.headers, code, names={
            'character-cost', 'request-id', 'content-length', 'content-type',
            'content-encoding', 'transfer-encoding',
        })
        cost, request_id = headers.get('character-cost'), headers.get('request-id')
        _require(type(cost) is str and re.fullmatch(r'[1-9][0-9]{0,9}', cost) is not None, _METER_ERROR)
        actual = int(cost)
        _require(actual <= 1_000_000_000, _METER_ERROR)
        _require(type(request_id) is str and 1 <= len(request_id) <= 256
                 and ',' not in request_id and all(32 < ord(c) < 127 for c in request_id), _METER_ERROR)
        request_id_sha = _sha(('elevenlabs\0request\0' + request_id).encode('ascii'))
        proof = {
            'version': 1, 'provider': PROVIDER, 'operation': prepared.operation,
            'request': {'method': 'POST', 'route': prepared.route,
                        'payload_sha256': _sha(_canonical(prepared.payload, code)),
                        'wire_body_sha256': wire_body_sha,
                        'credential_sha256': prepared.credential_sha256},
            'response': {'status': response.status_code, 'body_sha256': _sha(raw),
                         'headers': {'character_cost': actual, 'request_id_sha256': request_id_sha}},
        }
        return {'actual_credit_cost': actual, 'provider_request_id_sha256': request_id_sha,
                'response_proof_sha256': _sha(_canonical(proof, code))}
    except SpendBlocked:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError, RuntimeError, RecursionError):
        # Never expose response content, HTTP exception text, URL credentials or
        # account data in a task error. Nothing in this function sends or settles.
        raise SpendBlocked(code) from None
