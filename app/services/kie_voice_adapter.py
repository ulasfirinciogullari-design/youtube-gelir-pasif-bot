"""Bounded Kie TTS queue transport. The mandatory journal owns paid admission."""
from decimal import Decimal, InvalidOperation
import json
import re
import time

import httpx

ORIGIN = 'https://api.kie.ai'
CREATE = ORIGIN + '/api/v1/jobs/createTask'
RESULT = ORIGIN + '/api/v1/jobs/recordInfo'
BALANCE = ORIGIN + '/api/v1/chat/credit'
TURBO = 'elevenlabs/text-to-speech-turbo-2-5'
MULTILINGUAL = 'elevenlabs/text-to-speech-multilingual-v2'
RATES = {TURBO: 6, MULTILINGUAL: 12}
MAX_RESPONSE = 4 * 1024 * 1024
TASK_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')


class KieVoiceError(RuntimeError):
    """Content-free error; unknown or accepted requests remain in their journal."""


def require(value, code='kie_voice_response_invalid'):
    if not value:
        raise KieVoiceError(code)


def object_response(response):
    require(response.status_code == 200 and 0 < len(response.content) <= MAX_RESPONSE,
            'kie_voice_http_rejected')
    try:
        value = json.loads(response.content)
    except (ValueError, UnicodeError):
        raise KieVoiceError('kie_voice_response_invalid') from None
    require(type(value) is dict and type(value.get('code')) is int and value['code'] == 200,
            'kie_voice_api_rejected')
    return value


def microcredits(value):
    require(type(value) in (int, float, str), 'kie_voice_credit_invalid')
    try:
        number = Decimal(str(value)) * 1_000_000
        require(number.is_finite() and 0 <= number <= 10**15 and number == number.to_integral_value(),
                'kie_voice_credit_invalid')
        return int(number)
    except (InvalidOperation, ValueError, OverflowError):
        raise KieVoiceError('kie_voice_credit_invalid') from None


def headers(secret):
    require(type(secret) is str and 16 <= len(secret) <= 4096
        and all(32 < ord(c) < 127 for c in secret), 'kie_voice_key_invalid')
    return {'Authorization': 'Bearer ' + secret, 'Accept': 'application/json',
            'Content-Type': 'application/json'}


def read_balance(secret):
    """One authenticated GET. Does not initialize funding, top up or generate."""
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                          transport=httpx.HTTPTransport(retries=0)) as client:
            response = client.get(BALANCE, headers=headers(secret))
        value = object_response(response)
        return {'available_microcredits': microcredits(value.get('data')),
                'response': response.content.decode(), 'status': 'verified'}
    except httpx.HTTPError:
        raise KieVoiceError('kie_voice_balance_unavailable') from None


def request_body(text, voice_id, *, turkish=False, speed=1.0):
    units = len(text.encode('utf-16-le')) // 2 if type(text) is str else 0
    require(1 <= units <= 5000 and text.strip(), 'kie_voice_text_limit')
    require(type(voice_id) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,80}', voice_id),
            'kie_voice_voice_invalid')
    require(type(turkish) is bool and type(speed) in (float, int) and 0.7 <= speed <= 1.2,
            'kie_voice_request_invalid')
    model = TURBO if turkish else MULTILINGUAL
    body = {'model': model, 'input': {'text': text, 'voice': voice_id,
        'stability': 0.5 if turkish else 0.4, 'similarity_boost': 0.75 if turkish else 0.8,
        'style': 0, 'speed': speed, 'timestamps': True}}
    if turkish:
        body['input']['language_code'] = 'tr'
    # Reserve full 1,000-character blocks conservatively. Observed provider
    # credit usage is recorded separately and never guessed from list price.
    return body, ((units + 999) // 1000) * RATES[model] * 1_000_000


def generate(body, secret, journal, *, clock=time.monotonic, sleep=time.sleep):
    """Restore prior receipts, submit once, then GET the same accepted task."""
    require(journal is not None and body.get('model') in RATES, 'kie_voice_journal_required')
    auth = headers(secret)
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                      transport=httpx.HTTPTransport(retries=0)) as client:
        try:
            response = journal.submit(client.post, CREATE, json=body, headers=auth)
        except httpx.HTTPError:
            raise KieVoiceError('kie_voice_submission_outcome_unknown') from None
        created = object_response(response).get('data')
        require(type(created) is dict and type(created.get('taskId')) is str
            and TASK_ID.fullmatch(created['taskId']), 'kie_voice_submission_outcome_unknown')
        task_id = created['taskId']
        cached = journal.result_response()
        deadline, delay, failures = clock() + 600, 2, 0
        while True:
            if cached is not None:
                response, cached = cached, None
            else:
                require(clock() < deadline, 'kie_voice_accepted_task_pending')
                try:
                    response = client.get(RESULT, params={'taskId': task_id}, headers=auth)
                except httpx.HTTPError:
                    failures += 1
                    require(failures <= 3, 'kie_voice_accepted_task_pending')
                    sleep(delay)
                    continue
            data = object_response(response).get('data')
            require(type(data) is dict and data.get('taskId') == task_id
                and data.get('model') == body['model'], 'kie_voice_result_binding_invalid')
            state = data.get('state')
            require(state in {'waiting', 'queuing', 'generating', 'success', 'fail'})
            if state in {'success', 'fail'}:
                journal.observe_result(response)
                require(state == 'success', 'kie_voice_generation_failed')
                try:
                    result = json.loads(data['resultJson'])
                except (TypeError, ValueError, KeyError):
                    raise KieVoiceError('kie_voice_result_invalid') from None
                require(type(result) is dict, 'kie_voice_result_invalid')
                return {'task_id': task_id, 'result': result,
                    'actual_microcredits': (microcredits(data['creditsConsumed'])
                        if data.get('creditsConsumed') is not None else None)}
            sleep(delay)
            delay = min(delay + 1, 8)
