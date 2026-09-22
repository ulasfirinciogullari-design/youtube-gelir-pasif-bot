"""Durable native Gemini reasoning during explicitly authorized commissioning.

Separate from included Abacus credits and the final operating budget. Each
native request is reserved before transport; unknown attempts are never resent.
An explicit worker switch plus current channel/owner authority is mandatory.
Standard Gemini 3.7 Flash rates reviewed 2026-09-22 at
https://ai.google.dev/gemini-api/docs/pricing#gemini-3.7-flash :
$0.75/M input, $3.75/M output (including thinking). Full context is a list-cost
reservation, never a claimed invoice. Tools/search/cache purchases are absent.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import httpx

from app.services import production_spend_runtime as runtime
from app.services.production_spend import SpendBlocked
from app.services.included_stock_pool import _local_transaction

PREFIX = 'youtube_studio:commissioning:v1:reasoning:'
MODEL = 'gemini-3.7-flash'
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent'
PRICE_REVISION = 'gemini37-commissioning-2026-09-22-v1'
PRICE_UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)
MAX_INPUT = 1_048_576
MAX_RESPONSE = 2 * 1024 * 1024
MAX_LINEAGE = 80
MAX_DAY = 1000
UNHANDLED = object()
VISUAL_SCHEMA_PREFIX = (
    'GEMINI_VISUAL_COMPLETE_SCHEMA_V1\nReturn only a JSON object conforming to '
    'this complete schema. Include every required field and explicit boolean; '
    'all bounds, enum values and uniqueItems constraints are validated locally.\n'
)
LONG_SCHEMA_PREFIX = ('GEMINI_LONG_EDITORIAL_COMPLETE_SCHEMA_V1\n'
    'Return the complete documentary using every field and constraint below. '
    'Exact scene counts, indices, enum values, types and all other bounds are '
    'validated locally. Do not shorten the story or omit a scene.\n')


def _require(condition, code='commissioning_reasoning_unverified'):
    if not condition:
        raise SpendBlocked(code)


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def selected():
    return getattr(runtime.settings, 'studio_commissioning_reasoning', False) is True


def _authorize(pipe, foundation, channel_id, context=None):
    from app.services import production_cash_disabled as cash, production_continuation as continuation
    _require(selected() and runtime.enforcement_enabled())
    pipe.watch(runtime.LEDGER_KEY, cash.ANCHOR_KEY)
    _require(cash.present(pipe), 'commissioning_reasoning_normal_budget_required')
    cash.read(pipe, foundation, now=foundation.clock())
    proof = continuation.authority(pipe, channel_id)
    _require(proof is not None, 'commissioning_reasoning_not_authorized')
    channel_key = runtime._CHANNEL_PREFIX + channel_id
    pipe.watch(channel_key, runtime._CHANNEL_INDEX)
    channel = json.loads(pipe.get(channel_key))
    _require(channel.get('id') == channel_id and channel.get('requires_reconnect') is not True
        and pipe.sismember(runtime._CHANNEL_INDEX, channel_id))
    if context is not None:
        if context['kind'] == 'long':
            from app.services.commissioning_longform import authorize
            authorize(pipe, context)
        _require(context['kind'] in {'shorts', 'long'} and context['channel_id'] == channel_id
            and channel.get('connection_id') == context['connection_id']
            and pipe.hget(runtime.LEDGER_KEY, 'binding:' + context['lineage_id']) == _raw(context))
    key = getattr(runtime.settings, 'gemini_api_key', '')
    _require(type(key) is str and 1 <= len(key) <= 8192 and all(32 < ord(c) < 127 for c in key))
    _require(datetime(2026, 9, 22, tzinfo=timezone.utc) <= foundation.clock() < PRICE_UNTIL,
             'commissioning_reasoning_price_expired')
    return proof


def check_capacity(pipe, foundation, channel_id, *, minimum_requests=1):
    proof = _authorize(pipe, foundation, channel_id)
    day = PREFIX + 'day:' + foundation.clock().strftime('%Y-%m-%d')
    pipe.watch(day)
    _require(pipe.pttl(day) in (-1, -2) and pipe.scard(day) + minimum_requests <= MAX_DAY,
             'commissioning_reasoning_capacity')
    return {'policy_sha256': proof, 'day_requests_remaining': MAX_DAY - pipe.scard(day),
            'reasoning_provider': 'gemini', 'reasoning_model': MODEL,
            'funding_basis': 'owner_authorized_commissioning_not_subscription_credits'}


def _visual_response_shape(schema):
    """Constrain the wire shape without compiling every local value bound.

    Gemini rejects some full review grammars. Plain JSON mode, however, can
    omit required evidence or repeat fields. Keep every typed, required field
    in the provider grammar; the original schema still validates all values.
    https://ai.google.dev/gemini-api/docs/structured-output
    """
    kind = schema['type']
    _require(kind in {'object', 'array', 'string', 'integer', 'number', 'boolean'},
             'commissioning_reasoning_input_invalid')
    shape = {'type': kind}
    if kind == 'object':
        shape.update(properties={key: _visual_response_shape(value)
                                 for key, value in schema['properties'].items()},
                     required=list(schema['required']), additionalProperties=False)
    elif kind == 'array':
        shape['items'] = _visual_response_shape(schema['items'])
    return shape


def _long_response_shape(schema):
    """Bounded grammar for the observed long-director HTTP 400.

    A 30-element nested bounded array was rejected. Keep all typed properties,
    mandatory fields and string choices on the wire; enforce cardinalities and
    numeric bounds against the unchanged full schema in the existing observer.
    https://ai.google.dev/gemini-api/docs/structured-output#limitations
    """
    kind = schema['type']
    _require(kind == ['string', 'null'] or type(kind) is str
        and kind in {'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'})
    shape = {'type': deepcopy(kind)}
    if kind == 'object':
        shape.update(properties={k: _long_response_shape(v) for k, v in schema['properties'].items()},
            required=list(schema['required']), additionalProperties=False)
    elif kind == 'array':
        shape['items'] = _long_response_shape(schema['items'])
    elif kind == 'string' and 'enum' in schema:
        shape['enum'] = deepcopy(schema['enum'])
    return shape


def _request(prepared, purpose, *, long_form=False):
    from app.services.abacus_router_adapter import PreparedRouterRequest
    from app.services.abacus_router_audio_adapter import (PreparedPrepaidAudioRequest, PreparedLongformAudioRequest,
        AudioReviewPurpose, schema_for_request)
    from app.services.abacus_router_schema_compat import schema_for_body
    from app.services.production_included_router import PURPOSES
    _require(purpose in PURPOSES and type(prepared) in (PreparedRouterRequest, PreparedPrepaidAudioRequest, PreparedLongformAudioRequest))
    is_audio = type(prepared) in (PreparedPrepaidAudioRequest, PreparedLongformAudioRequest)
    _require((is_audio and {'prosody': AudioReviewPurpose.PROSODY,
                           'blind_asr': AudioReviewPurpose.BLIND_ASR}.get(purpose) is prepared.purpose)
        or (not is_audio and purpose not in {'prosody', 'blind_asr'}))
    body = prepared.payload
    schema = schema_for_request(body, prepared.purpose) if is_audio else schema_for_body(body)
    parts = []
    for part in body['messages'][1]['content']:
        if part['type'] == 'text':
            parts.append({'text': part['text']})
        elif part['type'] == 'image_url':
            value = part['image_url']['url']
            _require(value.startswith('data:image/jpeg;base64,'))
            parts.append({'inlineData': {'mimeType': 'image/jpeg', 'data': value.split(',', 1)[1]}})
        elif part['type'] == 'input_audio':
            _require(is_audio and part['input_audio']['format'] == 'mp3')
            parts.append({'inlineData': {'mimeType': 'audio/mpeg', 'data': part['input_audio']['data']}})
        else:
            raise SpendBlocked('commissioning_reasoning_input_invalid')
    output = body['max_tokens']
    _require(type(output) is int and 1 <= output <= 8192)
    native = {'contents': [{'role': 'user', 'parts': parts}],
        'systemInstruction': {'parts': [{'text': body['messages'][0]['content']}]},
        'generationConfig': {'candidateCount': 1, 'maxOutputTokens': output,
            'thinkingConfig': {'thinkingLevel': 'low'}, 'responseMimeType': 'application/json',
            'responseJsonSchema': deepcopy(schema)}}
    if purpose == 'visual_review':
        # The simple grammar supplies complete typed evidence. Exact counts,
        # scores, indices and uniqueness stay in the unchanged local schema.
        # New request identity; never replay an older provider reservation.
        native['generationConfig']['responseJsonSchema'] = _visual_response_shape(schema)
        parts.append({'text': VISUAL_SCHEMA_PREFIX + _raw(schema)})
    elif long_form and purpose in {'editorial', 'story_review'}:
        native['generationConfig']['responseJsonSchema'] = _long_response_shape(schema)
        parts.append({'text': LONG_SCHEMA_PREFIX + _raw(schema)})
    # Prepared adapters already bounded and decoded every original image/audio.
    encoded = _raw(native)
    _require(len(encoded.encode()) <= 20_000_000)
    return native, schema, 786432 + (output * 15 + 3) // 4


def _legacy_outcome(pipe, ledger, context, prepared, purpose):
    """Preserve every old result/unknown; no source-ledger mutation or refund."""
    from app.services import production_included_router as included
    pipe.watch(ledger.state_key, ledger.journal_key, ledger.anchor_key)
    raws = [pipe.get(k) for k in (ledger.state_key, ledger.journal_key, ledger.anchor_key)]
    if raws == [None, None, None]:
        _require(pipe.hget(runtime.LEDGER_KEY, ledger.mode_field) is None)
        return None
    _require(all(type(v) is str for v in raws)
        and all(pipe.pttl(k) == -1 for k in (ledger.state_key, ledger.journal_key, ledger.anchor_key)))
    state, journal = json.loads(raws[0]), json.loads(raws[1])
    _require(raws[2] == included._sha({'state': state, 'journal': journal})
        and state['policy']['credential_sha256'] == prepared.credential_sha256
        and pipe.hget(runtime.LEDGER_KEY, ledger.mode_field) == included._sha(state))
    identity = ledger.identity(context, purpose, prepared.request_sha256)
    prior = journal['requests'].get(identity)
    if prior is None:
        return None
    _require(prior['context'] == context and prior['request_sha256'] == prepared.request_sha256)
    # Even an explicitly rejected old request is left for its original review
    # path. Fresh commissioning calls get their own identity and provider record.
    _require(prior['outcome'] is not None, 'commissioning_reasoning_legacy_outcome_unknown')
    return prior['outcome']


@_local_transaction
def _reserve(foundation, ledger, context, prepared, purpose, native, ceiling):
    key_hash = _sha(_raw({'endpoint': ENDPOINT, 'body': native, 'purpose': purpose,
        'credential_sha256': _sha('gemini\0' + runtime.settings.gemini_api_key), 'context': context}))
    request_key = PREFIX + 'request:' + key_hash
    response_key = PREFIX + 'response:' + key_hash
    lineage = PREFIX + 'lineage:' + context['lineage_id']
    day = PREFIX + 'day:' + foundation.clock().strftime('%Y-%m-%d')
    with foundation.client.pipeline() as pipe:
        proof = _authorize(pipe, foundation, context['channel_id'], context)
        legacy = _legacy_outcome(pipe, ledger, context, prepared, purpose)
        if legacy is not None:
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return {'legacy': legacy}
        pipe.watch(request_key, response_key, lineage, day)
        prior = pipe.get(request_key)
        if prior is not None:
            row = json.loads(prior)
            _require(pipe.pttl(request_key) == -1 and row['context'] == context
                and row['authority_sha256'] == proof and row['request_sha256'] == key_hash
                and row['legacy_request_sha256'] == prepared.request_sha256
                and row['max_list_cost_micro_usd'] == ceiling
                and pipe.sismember(lineage, key_hash))
            response = pipe.get(response_key)
            _require(response is not None and pipe.pttl(response_key) == -1,
                     'commissioning_reasoning_previous_outcome_unknown')
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return {'record': row, 'response_key': response_key, 'response': json.loads(response), 'send': False}
        _require(not pipe.exists(response_key) and not pipe.sismember(lineage, key_hash)
            and not pipe.sismember(day, key_hash) and pipe.scard(lineage) < MAX_LINEAGE
            and pipe.scard(day) < MAX_DAY and pipe.pttl(lineage) in (-1, -2)
            and pipe.pttl(day) in (-1, -2), 'commissioning_reasoning_capacity')
        row = {'version': 1, 'context': context, 'purpose': purpose, 'provider': 'gemini', 'model': MODEL,
            'credential_sha256': _sha('gemini\0' + runtime.settings.gemini_api_key),
            'authority_sha256': proof, 'request_sha256': key_hash,
            'legacy_request_sha256': prepared.request_sha256,
            'reserved_at': foundation.clock().isoformat(), 'price_revision': PRICE_REVISION,
            'max_list_cost_micro_usd': ceiling, 'historical_cash_micro': None}
        pipe.multi(); pipe.set(request_key, _raw(row), nx=True)
        pipe.sadd(lineage, key_hash); pipe.sadd(day, key_hash)
        _require(pipe.execute() == [True, 1, 1], 'commissioning_reasoning_reservation_uncertain')
    return {'record': row, 'response_key': response_key, 'send': True}


def _send(native):
    with httpx.Client(transport=httpx.HTTPTransport(retries=0, trust_env=False),
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(90, connect=5),
            headers={'Accept-Encoding': 'identity'}) as client:
        with client.stream('POST', ENDPOINT, json=native,
                headers={'x-goog-api-key': runtime.settings.gemini_api_key, 'Content-Type': 'application/json'}) as response:
            _require(not response.history and str(response.request.url) == ENDPOINT
                and response.request.method == 'POST')
            encoding = response.headers.get_list('content-encoding')
            _require(not encoding or encoding == ['identity'])
            chunks, size = [], 0
            for chunk in response.iter_raw(chunk_size=4096):
                size += len(chunk)
                _require(size <= MAX_RESPONSE, 'commissioning_reasoning_response_too_large')
                chunks.append(chunk)
            return response.status_code, b''.join(chunks)


def _capture(foundation, reservation, status, raw):
    from app.services.production_included_router import _cipher
    value = {'version': 1, 'request_sha256': reservation['record']['request_sha256'],
        'observed_at': foundation.clock().isoformat(), 'http_status': status,
        'response_sha256': _sha(raw), 'encrypted_response': _cipher().encrypt(raw).decode('ascii')}
    _require(foundation.client.set(reservation['response_key'], _raw(value), nx=True) is True,
             'commissioning_reasoning_capture_uncertain')
    return value


def _observe(reservation, response, prepared, schema):
    from app.services.production_included_router import _cipher
    from app.services.gemini_generation import _decode_gemini_json_response, _reject_duplicate_keys, _reject_non_finite
    from app.services.abacus_visual_generation import _unique_items_match
    from app.services.abacus_router_adapter import _enum_match
    raw = _cipher().decrypt(response['encrypted_response'].encode())
    _require(_sha(raw) == response['response_sha256']
        and response['request_sha256'] == reservation['record']['request_sha256']
        and response['http_status'] == 200, 'commissioning_reasoning_response_unverified')
    envelope = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite)
    _require(type(envelope.get('modelVersion')) is str and envelope['modelVersion'].startswith(MODEL),
             'commissioning_reasoning_model_unverified')
    from app.services.gemini_generation import GeminiProtocolError
    from app.services.commissioning_visual_completion import partial, decode as decode_visual, IncompleteNativeVisualReview
    incomplete = None
    try:
        output = (decode_visual(raw, schema) if reservation['record']['purpose'] == 'visual_review'
                  else _decode_gemini_json_response(httpx.Response(200, content=raw), schema))
    except GeminiProtocolError:
        if reservation['record']['purpose'] != 'visual_review':
            raise
        incomplete = partial(prepared, raw, schema)
        if incomplete is None:
            raise
        output = incomplete
    _require(_unique_items_match(output, schema) and _enum_match(output, schema),
             'commissioning_reasoning_schema_unverified')
    from app.services.abacus_router_audio_adapter import PreparedPrepaidAudioRequest, AudioReviewPurpose, _asr_timing
    if type(prepared) is PreparedPrepaidAudioRequest and prepared.purpose is AudioReviewPurpose.BLIND_ASR:
        _asr_timing(output, prepared.audio)
    usage = envelope.get('usageMetadata')
    _require(type(usage) is dict)
    prompt, candidates, thoughts = (usage.get('promptTokenCount'), usage.get('candidatesTokenCount'),
                                     usage.get('thoughtsTokenCount', 0))
    _require(all(type(v) is int and v >= 0 for v in (prompt, candidates, thoughts))
        and prompt <= MAX_INPUT and candidates + thoughts <= prepared.payload['max_tokens'])
    cost = (prompt * 3 + (candidates + thoughts) * 15 + 3) // 4
    evidence = {**reservation['record'], 'response_sha256': response['response_sha256'],
        'parsed_result_sha256': _sha(_raw(output)), 'usage': deepcopy(usage),
        'observed_list_cost_micro_usd': cost, 'cost_basis': 'standard_list_estimate_not_invoice'}
    if incomplete is not None:
        raise IncompleteNativeVisualReview(incomplete, evidence)
    return output, evidence


def generate(prepared, purpose, ledger, foundation, context):
    from app.services import production_included_router as included
    from app.services.abacus_router_review_runtime import retained_router_review_active
    from app.services.abacus_router_audio_review_runtime import retained_audio_router_review_active
    if not selected():
        return UNHANDLED
    _require(not retained_router_review_active() and not retained_audio_router_review_active())
    from app.services.abacus_router_audio_adapter import PreparedLongformAudioRequest
    _require(type(prepared) is not PreparedLongformAudioRequest or context.get('kind') == 'long')
    included._LAST_OBSERVED.set(None)
    native, schema, ceiling = _request(prepared, purpose, long_form=context.get('kind') == 'long')
    try:
        reservation = _reserve(foundation, ledger, context, prepared, purpose, native, ceiling)
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked('commissioning_reasoning_reservation_uncertain') from None
    if 'legacy' in reservation:
        result = included._result(prepared, reservation['legacy'])
        evidence = reservation['legacy']['evidence']
    else:
        response = reservation.get('response')
        if reservation['send']:
            try:
                status, raw = _send(native)
                response = _capture(foundation, reservation, status, raw)
            except SpendBlocked:
                raise
            except Exception:
                raise SpendBlocked('commissioning_reasoning_outcome_unknown') from None
        from app.services.commissioning_visual_completion import IncompleteNativeVisualReview, complete
        incomplete = None
        try:
            result, evidence = _observe(reservation, response, prepared, schema)
        except IncompleteNativeVisualReview as missing:
            incomplete = missing
        except Exception:
            raise SpendBlocked('commissioning_reasoning_response_unverified') from None
        # Finish the exception handler before a distinct observed completion.
        if incomplete is not None:
            result, evidence = complete(incomplete, prepared, purpose, ledger, foundation, context)
    included._LAST_OBSERVED.set({'purpose': purpose, 'context': deepcopy(context), 'evidence': deepcopy(evidence)})
    return result
