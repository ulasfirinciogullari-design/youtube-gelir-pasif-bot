"""Competing voices wait before admission; uncertain sends are never replayed."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest

from app.services import production_credit_runtime as native, production_spend_runtime as runtime
from app.services import production_credit_ledger as durable, production_credit_funding as credit
from app.services.production_spend import SpendBlocked
from test_production_credit_runtime import case, request, state, ROOT, TEXT, all_records


def test_two_concurrent_voices_both_complete_with_serial_provider_requests(case, monkeypatch):
    started, release, waiting = Event(), Event(), Event()
    lock, active, max_active, sends = Lock(), [0], [0], []
    reserve = durable.CreditLedger.reserve
    def watch(self, **kwargs):
        try:
            return reserve(self, **kwargs)
        except SpendBlocked as exc:
            if str(exc) == 'credit_pool_has_uncertain_intent': waiting.set()
            raise
    monkeypatch.setattr(durable.CreditLedger, 'reserve', watch)
    def respond(req):
        with lock:
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
            sends.append(req)
            index = len(sends)
        if index == 1:
            started.set()
            assert release.wait(5)
        with lock: active[0] -= 1
        return httpx.Response(200, content=b'{}', headers={
            'character-cost': '248', 'request-id': f'concurrent-request-{index}'})
    def produce(http, suffix):
        token = runtime._TASK_ID.set(ROOT)
        try:
            return native.paid_credit_post(http.post, credit.ROUTE, request(TEXT + suffix)).status_code
        finally:
            runtime._TASK_ID.reset(token)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http, ThreadPoolExecutor(2) as pool:
        first = pool.submit(produce, http, ' First take.')
        try:
            assert started.wait(2)
            second = pool.submit(produce, http, ' Second take.')
            assert waiting.wait(2)
        finally:
            release.set()
        assert first.result(timeout=5) == second.result(timeout=5) == 200
    snapshot = state(case)
    assert max_active == [1] and len(sends) == 2
    assert len(snapshot['intents']) == 2 and snapshot['revision'] == 4
    assert (snapshot['spent_credits'], snapshot['reserved_credits']) == (496, 0)


def test_unknown_existing_voice_times_out_without_sending_or_releasing_hold(case, monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'{}'))) as http:
        with pytest.raises(SpendBlocked, match='credit_tts_meter_unverified'):
            native.paid_credit_post(http.post, credit.ROUTE, request())
    before = all_records(case.client)
    clock = [0.]
    monkeypatch.setattr(native, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(native, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(native, '_POOL_WAIT_SECONDS', 1)
    with pytest.raises(SpendBlocked, match='credit_pool_has_uncertain_intent'):
        native.paid_credit_post(case.http.post, credit.ROUTE, request(TEXT + ' distinct'))
    assert not case.sends and all_records(case.client) == before and clock == [1.]


@pytest.mark.parametrize('code', ['credit_commit_uncertain', 'credit_store_unavailable',
                                 'credit_cross_mode_request_conflict', 'credit_intent_already_reserved'])
def test_no_other_failure_can_enter_admission_wait(case, monkeypatch, code):
    def fail(self, **kwargs): raise SpendBlocked(code)
    monkeypatch.setattr(durable.CreditLedger, 'reserve', fail)
    monkeypatch.setattr(native, 'sleep', lambda seconds: pytest.fail('must not retry ambiguous reservations'))
    with pytest.raises(SpendBlocked, match=code):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert not case.sends


def test_channel_revocation_while_waiting_prevents_late_send(case, monkeypatch):
    reserve = durable.CreditLedger.reserve
    attempts = []
    def busy(self, **kwargs):
        attempts.append(True)
        if len(attempts) == 1: raise SpendBlocked('credit_pool_has_uncertain_intent')
        return reserve(self, **kwargs)
    monkeypatch.setattr(durable.CreditLedger, 'reserve', busy)
    monkeypatch.setattr(native, 'sleep', lambda seconds: case.client.delete(runtime._CHANNEL_INDEX))
    with pytest.raises(SpendBlocked):
        native.paid_credit_post(case.http.post, credit.ROUTE, request())
    assert attempts == [True] and not case.sends


@pytest.mark.parametrize('legacy', [False, True])
def test_pre_send_voice_contention_can_hold_unpublished_job_without_rewriting_it(legacy):
    from copy import deepcopy
    from app.services.production_failures import classify_failure, classified_hold_reason
    error = SpendBlocked('credit_pool_has_uncertain_intent')
    record = classify_failure(error, 'audio_qc_retry')
    if legacy: record.update(code='spending_blocked', category='spending_blocked')
    job = {'error': str(error), 'failure_stage': 'audio_qc_retry', 'failure_classification': record}
    before = deepcopy(job)
    assert classified_hold_reason(job) == 'review_unverified' and job == before
    for stage in ('render', 'youtube_upload', 'publishing', 'research'):
        changed = {**job, 'failure_stage': stage, 'failure_classification': classify_failure(error, stage)}
        assert classified_hold_reason(changed) is None
    job['error'] += ' altered'
    assert classified_hold_reason(job) is None


@pytest.mark.parametrize('code', ['credit_commit_uncertain', 'credit_allocation_exhausted',
                                 'credit_request_already_reserved', 'credit_runtime_outcome_unverified'])
def test_actual_budget_or_paid_unknown_is_not_classified_as_voice_contention(code):
    from app.services.production_failures import classify_failure, classified_hold_reason
    error = SpendBlocked(code)
    assert classified_hold_reason({'error': code, 'failure_stage': 'audio_qc',
        'failure_classification': classify_failure(error, 'audio_qc')}) is None
