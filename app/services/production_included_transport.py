"""One bounded native router exchange, including already-received errors.

No retry, fallback, request construction, authorization or result interpretation.
The caller must reserve once before entry and keep failed requests occupied.
"""
import httpx

from app.services.abacus_router_adapter import ENDPOINT, MAX_RESPONSE_BYTES, PreparedRouterRequest
from app.services.abacus_router_audio_adapter import PreparedAudioRouterRequest
from app.services.production_spend import SpendBlocked

MAX_ERROR_BYTES = 16384


def _require(value):
    if not value:
        raise SpendBlocked('included_router_transport_unverified')


def send_once(prepared):
    _require(type(prepared) in (PreparedRouterRequest, PreparedAudioRouterRequest)
             and prepared.endpoint == ENDPOINT)
    transport = httpx.HTTPTransport(retries=0, trust_env=False)
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
            auth=None, cookies=None, event_hooks={'request': [], 'response': []},
            headers={'Accept-Encoding': 'identity'}) as client:
        with client.stream('POST', ENDPOINT, **prepared.wire_kwargs()) as response:
            _require(not response.history and response.request.method == 'POST'
                     and str(response.request.url) == ENDPOINT)
            encoding = response.headers.get_list('content-encoding')
            _require(not encoding or encoding == ['identity'])
            bound = MAX_RESPONSE_BYTES if 200 <= response.status_code < 300 else MAX_ERROR_BYTES
            lengths = response.headers.get_list('content-length')
            _require(len(lengths) <= 1)
            if lengths:
                length = lengths[0]
                _require(length.isascii() and length.isdecimal() and len(length) <= 9 and int(length) <= bound)
            chunks, total = [], 0
            for chunk in response.iter_raw(chunk_size=4096):
                _require(type(chunk) is bytes and len(chunk) <= bound - total)
                total += len(chunk); chunks.append(chunk)
            response._content = b''.join(chunks)
            return response
