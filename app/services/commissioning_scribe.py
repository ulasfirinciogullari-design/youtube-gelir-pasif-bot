"""Independent, blind Scribe review during owner-authorized commissioning.

2026-09-21 official API list price: Scribe v2 $0.22/hour. Reserve $0.004
(one rounded minute) for <=40.08 decoded seconds. This records a conservative
list cost, not an invoice or the owner's deferred operating budget.
https://elevenlabs.io/pricing/api
https://elevenlabs.io/docs/api-reference/speech-to-text/convert

No script/keyterm hint, retry, top-up, ledger reset or automatic QA approval.
The old recognizer's result remains intact. Every actual request has its own
permanent receipt, including unknown outcomes; observed results are reused.
"""
from datetime import datetime, timezone
import hashlib
import json

import httpx

from app.services import production_continuation as continuation
from app.services import production_spend_runtime as runtime, whisper_transcription as audio
from app.services.production_spend import SpendBlocked

PREFIX = 'youtube_studio:commissioning:v1:scribe:'
ROUTE = 'https://api.elevenlabs.io/v1/speech-to-text'
MAX_LIST_COST_MICRO = 4000


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _require(value):
    if not value:
        raise SpendBlocked('commissioning_scribe_unverified')


def _response(observed):
    from app.services.youtube_auth import _decrypt_json
    raw = _decrypt_json(observed['encrypted_response'])['response'].encode()
    _require(_sha(raw) == observed['response_sha256'])
    return httpx.Response(200, content=raw, headers={'Content-Type': 'application/json'})


def _send(snapshot, fields, api_key):
    # No HTTP redirect or transport retry can repeat or disclose this request.
    with httpx.Client(timeout=httpx.Timeout(90, connect=10), trust_env=False, follow_redirects=False) as http:
        with http.stream('POST', ROUTE, headers={'xi-api-key': api_key, 'Accept': 'application/json'},
                data=fields, files={'file': (snapshot.filename, snapshot.raw, snapshot.mime_type)}) as result:
            _require(result.status_code == 200)
            raw = bytearray()
            for chunk in result.iter_bytes():
                raw.extend(chunk)
                _require(len(raw) <= 2 * 1024 * 1024)
            _require(raw and api_key.encode() not in raw)
            data = json.loads(raw)
            _require(type(data) is dict and type(data.get('text')) is str and type(data.get('words')) is list)
            return bytes(raw)


def transcribe_if_commissioned(path, *, api_key, language):
    """None means no active owner authorization; unknown sends never repeat."""
    _require(runtime.enforcement_enabled() and language in {'tr', 'en'})
    foundation = runtime.configured_ledger(read_timeout=2)
    client = foundation.client
    context = runtime.resolve_context(client, runtime._TASK_ID.get())
    _require(context['kind'] in {'shorts', 'long'})
    longform = context['kind'] == 'long'
    with client.pipeline() as pipe:
        if longform:
            from app.services.commissioning_longform import authorize
            authorize(pipe, context)
        authority = continuation.authority(pipe, context['channel_id'])
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    if authority is None:
        return None
    _require(type(api_key) is str and 1 <= len(api_key) <= 4096 and api_key.isascii()
        and not any(c.isspace() for c in api_key))
    raw, suffix = audio._read_audio(path)
    snapshot = audio._snapshot_audio(raw, suffix, allow_natural_short=True,
        **({'allow_commissioned_long': True} if longform else {}))
    fields = {'model_id': 'scribe_v2', 'language_code': {'tr': 'tur', 'en': 'eng'}[language],
        'num_speakers': '1', 'diarize': 'false', 'tag_audio_events': 'false',
        'timestamps_granularity': 'word'}
    descriptor = {**snapshot.descriptor(fields), 'route': ROUTE,
        'credential_sha256': _sha(('elevenlabs\0' + api_key).encode())}
    identity = _sha(_raw({'context': context, 'request': descriptor}).encode())
    key = PREFIX + identity
    with client.pipeline() as pipe:
        _require(continuation.authority(pipe, context['channel_id']) == authority)
        if longform:
            authorize(pipe, context)
        channel_key = runtime._CHANNEL_PREFIX + context['channel_id']
        pipe.watch(key, channel_key)
        channel = json.loads(pipe.get(channel_key))
        _require(channel.get('connection_id') == context['connection_id'] and channel.get('requires_reconnect') is not True)
        prior = pipe.get(key)
        if prior is not None:
            row = json.loads(prior)
            _require(pipe.pttl(key) == -1 and row.get('context') == context
                and row.get('request') == descriptor and row.get('outcome') is not None)
            pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
            return _response(row['outcome'])
        reservation = _raw({'version': 1, 'context': context, 'request': descriptor,
            'continuation_authority_sha256': authority, 'reserved_at': datetime.now(timezone.utc).isoformat(),
            'max_list_cost_micro_usd': MAX_LIST_COST_MICRO * (4 if longform else 1), 'outcome': None})
        pipe.multi(); pipe.set(key, reservation, nx=True)
        _require(pipe.execute() == [True])
    try:
        raw = _send(snapshot, fields, api_key)
        from app.services.youtube_auth import _encrypt_json
        observed = {'response_sha256': _sha(raw),
            'encrypted_response': _encrypt_json({'response': raw.decode()}),
            'observed_at': datetime.now(timezone.utc).isoformat()}
        with client.pipeline() as pipe:
            pipe.watch(key)
            _require(pipe.get(key) == reservation and pipe.pttl(key) == -1)
            row = json.loads(reservation); row['outcome'] = observed
            pipe.multi(); pipe.set(key, _raw(row)); _require(pipe.execute() == [True])
        return _response(observed)
    except Exception:
        raise SpendBlocked('commissioning_scribe_outcome_unverified') from None
