"""One Fal ElevenLabs submit, followed only by the accepted queue's reads.

Reviewed 2026-09-25: fal-ai/elevenlabs/tts/turbo-v2.5, $0.05/1000
characters. Native returned word timestamps are alignment, not an ASR pass.
https://fal.ai/models/fal-ai/elevenlabs/tts/turbo-v2.5/api
"""
import math
import re
import time
from urllib.parse import urlparse

import httpx

MODEL = 'fal-ai/elevenlabs/tts/turbo-v2.5'
ORIGIN = 'https://queue.fal.run'
ROUTE = ORIGIN + '/' + MODEL
VOICES = {'tr': 'Adam', 'en': 'George'}
MAX_RESPONSE = 2 * 1024 * 1024


class FalVoiceError(RuntimeError):
    pass


def require(value, code='fal_voice_unverified'):
    if not value:
        raise FalVoiceError(code)


def request_body(text, *, language):
    require(type(text) is str and text.strip() and language in VOICES)
    units = len(text.encode('utf-16-le')) // 2
    require(1 <= units <= 5000)
    body = {'text': text, 'voice': VOICES[language], 'language_code': language,
        'stability': .65, 'similarity_boost': .75, 'style': 0, 'speed': 1,
        'timestamps': True, 'apply_text_normalization': 'off'}
    # Round upward, including supplementary Unicode, rather than infer a bill.
    return body, math.ceil(units / 1000) * 50_000


def describe(body):
    require(type(body) is dict)
    try:
        expected, ceiling = request_body(body['text'], language=body['language_code'])
        require(body == expected)
    except (KeyError, TypeError):
        raise FalVoiceError('fal_voice_input_unverified') from None
    return ceiling


def queue_url(value, request_id, suffix):
    require(type(value) is str and type(request_id) is str
        and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', request_id))
    try: parsed = urlparse(value); port = parsed.port
    except ValueError: raise FalVoiceError('fal_voice_queue_untrusted') from None
    paths = {f'/{MODEL}/requests/{request_id}{suffix}',
             f'/fal-ai/elevenlabs/requests/{request_id}{suffix}'}
    require(parsed.scheme == 'https' and parsed.hostname == 'queue.fal.run'
        and port in (None, 443) and parsed.path in paths and not parsed.query
        and not parsed.fragment and not parsed.params and not parsed.username
        and not parsed.password, 'fal_voice_queue_untrusted')
    return value


def json_body(response):
    require(len(response.content) <= MAX_RESPONSE, 'fal_voice_response_large')
    try: value = response.json()
    except ValueError: raise FalVoiceError('fal_voice_response_invalid') from None
    require(type(value) is dict)
    return value


def result(response):
    from app.services.fal_video import validate_fal_media_url
    require(response.status_code == 200, 'fal_voice_result_rejected')
    value = json_body(response)
    require(not value.get('error') and type(value.get('audio')) is dict
        and type(value.get('timestamps')) is list and 1 <= len(value['timestamps']) <= 10000,
        'fal_voice_alignment_missing')
    validate_fal_media_url(value['audio'].get('url'))
    return value


def generate(body, secret, journal, *, sleep=time.sleep, maximum_polls=60):
    """The journal must reserve before sender and refuse an unknown submit."""
    describe(body)
    require(type(secret) is str and 1 <= len(secret) <= 8192
        and all(32 < ord(c) < 127 for c in secret))
    require(type(maximum_polls) is int and 1 <= maximum_polls <= 120)
    headers = {'Authorization': 'Key ' + secret, 'Accept': 'application/json'}
    with httpx.Client(transport=httpx.HTTPTransport(retries=0, trust_env=False),
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(75, connect=5)) as client:
        # No transport or application retry is allowed around this POST.
        response = journal.submit(client.post, ROUTE, json=body, headers=headers)
        require(response.status_code in (200, 201, 202), 'fal_voice_submit_rejected')
        accepted = json_body(response); identity = accepted.get('request_id')
        status_url = queue_url(accepted.get('status_url'), identity, '/status')
        response_url = queue_url(accepted.get('response_url'), identity, '')
        saved = journal.result_response()
        if saved is not None:
            return result(saved)
        read_failures = 0
        for _ in range(maximum_polls):
            try:
                observed = client.get(status_url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError):
                read_failures += 1
                require(read_failures < 3, 'fal_voice_accepted_poll_unavailable')
                sleep(3)
                continue
            if observed.status_code >= 500:
                read_failures += 1
                require(read_failures < 3, 'fal_voice_accepted_poll_unavailable')
                sleep(3)
                continue
            require(200 <= observed.status_code < 300, 'fal_voice_accepted_poll_unavailable')
            read_failures = 0
            status = json_body(observed)
            require(status.get('request_id', identity) == identity)
            if status.get('status') == 'COMPLETED':
                completed = client.get(response_url, headers=headers)
                journal.observe_result(completed)
                return result(completed)
            require(status.get('status') in {'IN_QUEUE', 'IN_PROGRESS'}, 'fal_voice_accepted_state_unverified')
            sleep(3)
    raise FalVoiceError('fal_voice_accepted_task_pending')
