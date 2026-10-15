import httpx
import pytest

from app.services import fal_voice_adapter as api

ID = '7c03f3b3-6663-4785-8cdc-794626c99c42'
URL = api.ORIGIN + '/fal-ai/elevenlabs/requests/' + ID


class Journal:
    def __init__(self): self.claimed = False; self.create = self.final = None
    def submit(self, sender, url, **kwargs):
        if self.claimed:
            if self.create is None: raise api.FalVoiceError('unknown_submit')
            return self.create
        self.claimed = True
        self.create = sender(url, **kwargs)
        return self.create
    def result_response(self): return self.final
    def observe_result(self, response): self.final = response


@pytest.fixture
def transport(monkeypatch):
    requests = []; state = {'outage': False, 'submit_timeout': False, 'queued': False}
    def send(request):
        requests.append(request)
        if request.method == 'POST':
            if state['submit_timeout']: raise httpx.ReadTimeout('unknown')
            return httpx.Response(200, json={'request_id': ID, 'status_url': URL + '/status', 'response_url': URL})
        if request.url.path.endswith('/status'):
            if state['outage']: return httpx.Response(503)
            if state['queued']:
                state['queued'] = False
                return httpx.Response(202, json={'request_id':ID,'status':'IN_PROGRESS'})
            return httpx.Response(200, json={'request_id': ID, 'status': 'COMPLETED'})
        return httpx.Response(200, json={'audio': {'url': 'https://v3.fal.media/files/test/audio.mp3'},
            'timestamps': [{'word': 'Merhaba', 'start': 0, 'end': .5}]})
    original = httpx.Client
    monkeypatch.setattr(api.httpx, 'Client', lambda **kw: original(**{**kw, 'transport': httpx.MockTransport(send)}))
    return requests, state


def test_receipt_reuse_and_poll_outage_never_resubmit(transport):
    requests, state = transport; body, ceiling = api.request_body('Merhaba.', language='tr'); journal = Journal()
    assert ceiling == 50000 and body['timestamps'] is True
    state['outage'] = True
    with pytest.raises(api.FalVoiceError, match='poll_unavailable'):
        api.generate(body, 'offline', journal, sleep=lambda _:None)
    state['outage'] = False
    state['queued'] = True
    first = api.generate(body, 'offline', journal, sleep=lambda _:None)
    assert api.generate(body, 'offline', journal) == first
    assert [r.method for r in requests].count('POST') == 1


def test_lost_submit_stays_unknown(transport):
    requests, state = transport; body, _ = api.request_body('Hello.', language='en'); journal = Journal()
    state['submit_timeout'] = True
    with pytest.raises(httpx.ReadTimeout): api.generate(body, 'offline', journal)
    with pytest.raises(api.FalVoiceError, match='unknown_submit'): api.generate(body, 'offline', journal)
    assert len(requests) == 1


@pytest.mark.parametrize('url', [URL.replace('queue.fal.run','attacker.example'),
    URL + '?key=secret', URL + '/status', URL.replace(ID,'another-id'),
    URL.replace('https:', 'http:'), URL.replace('queue.fal.run','user@queue.fal.run')])
def test_queue_links_bind_only_the_accepted_job(url):
    with pytest.raises(api.FalVoiceError): api.queue_url(url, ID, '')


def test_alignment_is_required_and_never_a_quality_approval():
    with pytest.raises(api.FalVoiceError, match='alignment_missing'):
        api.result(httpx.Response(200, json={'audio': {'url': 'https://v3.fal.media/audio.mp3'}}))
    assert api.request_body('x' * 1001, language='en')[1] == 100000
    with pytest.raises(api.FalVoiceError): api.request_body('x' * 5001, language='en')
