import json
from copy import deepcopy

import httpx
import pytest

from app.services import video_dubbing as dubs, kie_voice_ledger as ledger, kie_gemini_voice as gemini
from app.services import kie_voice_adapter as api, video_localization as loc
from app.services.production_spend import SpendBlocked, LEDGER_KEY
from test_kie_voice import kie, commission
from test_commissioning_audio import box
from test_whisper_transcription import CHANNEL

VIDEO = 'abcdefghijk'
SCRIPTS = {lang: {'sentences': ['Toyota examines each part before it moves on.'], 'voice_id': 'Fenrir'}
           for lang in dubs.LANGUAGES}


@pytest.fixture
def planned(kie, monkeypatch):
    kie.balance = 1000
    commission(kie)
    gemini.commission(kie.foundation, owner_evidence_sha256='b' * 64)
    source = {'spec': {'language': 'tr'}, 'result': {'caption_key': 'captions.tr.srt', 'duration': 31.4, 'title': 'Toyota'}}
    receipt = {'youtube_video_id': VIDEO, 'target_channel_id': CHANNEL}
    monkeypatch.setattr(loc, '_source', lambda *a: (source, receipt))
    monkeypatch.setattr(loc, '_current_connection', lambda *a: 'connection_AAAAA')
    from app.services.youtube_automation import PROFILE_PREFIX
    kie.client.set(PROFILE_PREFIX + CHANNEL, json.dumps({'production_enabled': True, 'auto_publish': True}))
    kie.source = source
    kie.plan = dubs.prepare(kie.foundation, 'source', SCRIPTS, owner_evidence_sha256='c' * 64)
    original = kie.handler
    def handler(request):
        response = original(request)
        if request.url.path.endswith('/recordInfo'):
            value = response.json(); value['data']['model'] = gemini.MODEL
            return httpx.Response(200, json=value)
        return response
    kie.handler = handler
    return kie


def synthesize(case, language='en', *, text=None, attempt=0):
    row = case.plan['languages'][language]
    body, ceiling = gemini.request_body(text or ' '.join(row['sentences']), row['voice_id'], language=language)
    journal = ledger.Journal(case.foundation, dubs.scope(case.plan, language), body, ceiling, attempt=attempt)
    return api.generate(body, case.config.openai_api_key, journal, sleep=lambda _: None)


def test_plan_does_not_allocate_more_credit_or_make_paid_requests(planned):
    case = planned
    assert [r.method for r in case.requests] == ['GET']
    assert ledger.status(case.client)['allocation_microcredits'] == 1_000_000_000
    assert ledger.status(case.client)['requests'] == 0
    assert not case.client.exists(ledger.ACTIVE_KEY)
    before = case.client.hgetall(LEDGER_KEY)
    with pytest.raises(SpendBlocked, match='already_exists'):
        dubs.prepare(case.foundation, 'source', SCRIPTS, owner_evidence_sha256='c' * 64)
    assert before == case.client.hgetall(LEDGER_KEY)


def test_five_distinct_languages_share_original_ledger_and_resume_without_new_creates(planned):
    for language in dubs.LANGUAGES:
        assert synthesize(planned, language) == synthesize(planned, language)
    assert [r.method for r in planned.requests].count('POST') == 5
    status = ledger.status(planned.client)
    assert status['requests'] == 5 and status['committed_microcredits'] == 2_500_000
    assert status['allocation_microcredits'] == 1_000_000_000
    with pytest.raises(SpendBlocked, match='request_changed'):
        synthesize(planned, text='A different script cannot silently buy another voice.')
    with pytest.raises(SpendBlocked, match='request_changed'):
        synthesize(planned, attempt=1)
    assert [r.method for r in planned.requests].count('POST') == 5


def test_unknown_dub_request_cannot_be_resubmitted(planned):
    def fail(request):
        planned.requests.append(request)
        raise httpx.ReadTimeout('unknown')
    planned.handler = fail
    with pytest.raises(api.KieVoiceError, match='outcome_unknown'):
        synthesize(planned)
    with pytest.raises(SpendBlocked, match='previous_outcome_unknown'):
        synthesize(planned)
    assert [r.method for r in planned.requests].count('POST') == 1
    assert ledger.status(planned.client)['committed_microcredits'] == 48_000_000


@pytest.mark.parametrize('damage', ['source', 'plan', 'connection', 'funding', 'paused'])
def test_stale_source_plan_grant_or_funding_blocks_paid_dubbing(planned, monkeypatch, damage):
    if damage == 'source':
        planned.source['result']['duration'] = 30
    elif damage == 'plan':
        value = deepcopy(planned.plan); value['languages']['en']['sentences'] = ['Changed text.']
        planned.client.set(dubs._key(VIDEO), ledger.raw(value))
    elif damage == 'connection':
        monkeypatch.setattr(loc, '_current_connection', lambda *a: 'different_connection')
    elif damage == 'paused':
        from app.services.youtube_automation import PROFILE_PREFIX
        planned.client.set(PROFILE_PREFIX + CHANNEL, json.dumps({'production_enabled': False, 'auto_publish': False}))
    else:
        planned.client.delete(ledger.ANCHOR_KEY)
    with pytest.raises(SpendBlocked):
        synthesize(planned)
    assert [r.method for r in planned.requests].count('POST') == 0


def test_extra_languages_require_exact_documented_body_without_weakening_original_voices():
    for language in ('tr', *dubs.LANGUAGES):
        body, ceiling = gemini.request_body('A real sentence.', 'Kore', language=language)
        assert gemini.describe(body) == ('A real sentence.', 'Kore', ceiling, language)
        body['input']['speakers'][0]['audio_profile'] += ' Modified.'
        with pytest.raises(SpendBlocked):
            gemini.describe(body)
    with pytest.raises(SpendBlocked):
        gemini.request_body('Unknown language.', language='xx')


def test_one_explicit_reviewed_correction_preserves_first_request_and_allocation(planned):
    case = planned
    synthesize(case, 'hi')
    before = json.loads(case.client.get(ledger.JOURNAL_KEY))['requests']
    identity = next(iter(before))
    case.client.set(dubs._key(VIDEO) + ':track:hi', json.dumps({'status': 'generated', 'request_identity': identity}))
    revised = {'sentences': ['A clearer, faithfully corrected Hindi translation.'], 'voice_id': 'Kore'}
    grant = dubs.commission_repair(case.foundation, VIDEO, 'hi', revised,
        review_evidence_sha256='d'*64, owner_evidence_sha256='c'*64)
    assert grant['additional_allocation_microcredits'] == 0
    body, ceiling = gemini.request_body(revised['sentences'][0], 'Kore', language='hi')
    for _ in range(2):
        journal = ledger.Journal(case.foundation, dubs.scope(case.plan, 'hi'), body, ceiling, attempt=1)
        api.generate(body, case.config.openai_api_key, journal, sleep=lambda _: None)
    assert [r.method for r in case.requests].count('POST') == 2
    current = json.loads(case.client.get(ledger.JOURNAL_KEY))['requests']
    assert current[identity] == before[identity] and len(current) == 2
    assert ledger.status(case.client)['allocation_microcredits'] == 1_000_000_000
    with pytest.raises(SpendBlocked, match='repair_not_available'):
        dubs.commission_repair(case.foundation, VIDEO, 'hi', revised,
            review_evidence_sha256='e'*64, owner_evidence_sha256='c'*64)


def test_unknown_original_audio_is_not_a_quality_correction(planned):
    def fail(request):
        planned.requests.append(request)
        raise httpx.ReadTimeout('unknown')
    planned.handler = fail
    with pytest.raises(api.KieVoiceError): synthesize(planned, 'hi')
    identity = next(iter(json.loads(planned.client.get(ledger.JOURNAL_KEY))['requests']))
    planned.client.set(dubs._key(VIDEO) + ':track:hi', json.dumps({'status': 'generated', 'request_identity': identity}))
    with pytest.raises(SpendBlocked, match='original_outcome_unverified'):
        dubs.commission_repair(planned.foundation, VIDEO, 'hi', SCRIPTS['hi'],
            review_evidence_sha256='d'*64, owner_evidence_sha256='c'*64)
    assert [r.method for r in planned.requests].count('POST') == 1
