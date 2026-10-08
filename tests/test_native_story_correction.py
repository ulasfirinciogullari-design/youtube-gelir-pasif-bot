"""Private fresh-story grants preserve native budgets and every old receipt."""
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from app.services import native_story_correction as correction
from app.services import production_spend_runtime as runtime, production_included_router as included
from app.services import production_prepaid_audio as audio
from app.services.production_credit_ledger import CreditLedger
from app.services.production_spend import LEDGER_KEY
from test_full_video_rebuild import case, SOURCE, CHILD, TOKEN, _all, _write, _edit
from test_production_cash_disabled import money
from test_production_credit_ledger import policy, NOW
from test_production_included_router import request
from prepaid_audio_test_support import audio_policy


def pointer(task_id):
    prefix = 'audio_candidates/' + task_id + '/' + 'a' * 64
    return {'version': 1, 'status': 'unapproved_candidate', 'qa_approved': False,
        'requires_full_qa': True, 'audio_key': prefix + '/candidate.mp3',
        'metadata_key': prefix + '/metadata-' + 'b' * 64 + '.json',
        'audio_sha256': 'a' * 64, 'metadata_sha256': 'b' * 64,
        'package_sha256': 'c' * 64, 'size': 2048}


@pytest.fixture
def native(case, policy, monkeypatch):
    c, m = case, case.module
    foundation = money(c.client)
    CreditLedger(c.client, foundation=foundation, clock=foundation.clock).initialize(policy)
    prepared = request()
    base = {'version': 1, 'kind': 'existing_subscription_included_router',
        'endpoint': prepared.endpoint, 'model': prepared.model,
        'credential_sha256': prepared.credential_sha256, 'owner_evidence_sha256': 'b' * 64,
        'terms_evidence_sha256': 'c' * 64,
        'valid_from': included._stamp(NOW), 'valid_until': included._stamp(NOW + timedelta(days=1)),
        'automatic_purchase_enabled': False, 'new_cash_allowance_micro': 0, 'historical_cash_micro': None,
        'allowed_channels': [c.spec['production_channel_id']],
        'max_requests_per_lineage': 40, 'max_requests_per_day': 240}
    ledger = included.IncludedRouterLedger(foundation); ledger.initialize(base)
    audio.PrepaidAudioLedger(foundation).initialize(audio_policy(base, NOW,
        max_requests_per_lineage=12, max_requests_per_day=48))
    context = {'channel_id': c.spec['production_channel_id'],
        'connection_id': c.spec['production_connection_id'], 'lineage_id': SOURCE, 'kind': 'shorts'}
    c.client.hset(LEDGER_KEY, 'binding:' + SOURCE, included._raw(context))
    ledger.reserve(context, 'editorial', prepared)  # genuinely occupied unknown result
    for key, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                       'studio_abacus_prepaid_audio': True, 'studio_elevenlabs_native_credits': True}.items():
        monkeypatch.setattr(runtime.settings, key, value, raising=False)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: foundation)
    monkeypatch.setattr(m, 'validate_correction', correction.validate_correction, raising=False)
    source = deepcopy(c.source)
    source.update(failure_stage='visual_qc', error='included_router_previous_outcome_unknown',
        audio_candidate_checkpoint=pointer(SOURCE))
    source.pop('generated_asset_candidates')
    _write(c.client, m.JOB_PREFIX + SOURCE, source)
    c.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '0')
    note = {'version': 1, 'kind': 'native_story_correction',
        'source_audio_sha256': 'a' * 64, 'source_package_sha256': 'c' * 64,
        'correction_note': 'Distinguish total production cost from material cost and preserve historical tense.',
        'evidence_url': 'https://home.treasury.gov/news/featured-stories/penny-production-cessation-faqs',
        'evidence_sha256': 'd' * 64}
    return SimpleNamespace(case=c, module=m, client=c.client, note=note,
        foundation=foundation, ledger=ledger, context=context, source=source)


def reserve(n):
    return n.module.reserve_full_video_rebuild(SOURCE, CHILD, TOKEN, native_story_correction=n.note)


def test_explicit_correction_gets_new_unapproved_child_with_same_root_and_unchanged_funding(native):
    n = native; before = _all(n.client)
    n.module._source(n.client, SOURCE, {}, native_story_correction=n.note)
    assert _all(n.client) == before
    result = reserve(n)
    assert result['claimed'] is True and result['checkpoint'] is None
    assert n.case.state.acquire_retry_child_execution(CHILD, SOURCE) is True
    proof = n.module.get_full_rebuild_policy(CHILD, SOURCE, n.case.spec)
    assert proof['fresh_story'] is proof['fresh_voice'] is proof['requires_full_qa'] is True
    grant = json.loads(n.client.get(n.module.POLICY_PREFIX + CHILD))
    assert grant['lineage_root_task_id'] == SOURCE and grant['native_story_correction'] == n.note
    assert json.loads(n.client.get(n.module.NATIVE_CORRECTION_ROOT_PREFIX + SOURCE)) == grant
    original = json.loads(n.client.get(n.module.JOB_PREFIX + SOURCE))
    assert all(original[k] == v for k, v in n.source.items())
    after = _all(n.client)
    assert all(after[k] == v for k, v in before.items() if k.startswith('youtube_studio:{production_spend}:'))
    assert n.foundation.snapshot()['historical_cash_micro'] is None
    assert n.foundation.snapshot()['cash_spending_enabled'] is False
    assert len(json.loads(n.client.get(included.JOURNAL_KEY))['requests']) == 1
    assert 'qa_approved' not in proof and not n.case.calls
    assert n.client.get(n.module.REPAIR_CHECKPOINT_PREFIX + SOURCE) == before[n.module.REPAIR_CHECKPOINT_PREFIX + SOURCE][1]


def test_ordinary_retry_cannot_implicitly_request_a_fresh_voice(native):
    n = native; before = _all(n.client)
    with pytest.raises(n.module.FullVideoRebuildError):
        n.module.reserve_full_video_rebuild(SOURCE, CHILD, TOKEN)
    assert _all(n.client) == before


@pytest.mark.parametrize('change', ['audio', 'package', 'note', 'url', 'extra_field', 'stage',
    'paid_media', 'candidate_approval', 'source_connection', 'missing_ledger', 'native_off', 'missing_binding'])
def test_changed_or_unfunded_correction_never_grants_a_child(native, change, monkeypatch):
    n = native; m = n.module
    if change in {'audio', 'package'}: n.note['source_' + change + '_sha256'] = 'f' * 64
    elif change == 'note': n.note['correction_note'] = ''
    elif change == 'url': n.note['evidence_url'] = 'http://127.0.0.1/private'
    elif change == 'extra_field': n.note['qa_approved'] = True
    elif change == 'stage': _edit(n.case, m.JOB_PREFIX + SOURCE, 'failure_stage', 'upload')
    elif change == 'paid_media': n.client.hset(m.PAID_CREATE_BUDGET_PREFIX + SOURCE, 'used', '1')
    elif change == 'candidate_approval':
        _edit(n.case, m.JOB_PREFIX + SOURCE, 'audio_candidate_checkpoint', {**pointer(SOURCE), 'qa_approved': True})
    elif change == 'source_connection':
        _edit(n.case, m.OAUTH_CHANNEL_PREFIX + n.context['channel_id'], 'connection_id', 'changed')
    elif change == 'missing_ledger': n.client.delete(audio.ANCHOR_KEY)
    elif change == 'native_off': monkeypatch.setattr(runtime.settings, 'studio_abacus_included_production', False)
    else: n.client.hdel(LEDGER_KEY, 'binding:' + SOURCE)
    before = _all(n.client)
    with pytest.raises(n.module.FullVideoRebuildError): reserve(n)
    assert _all(n.client) == before


def test_unknown_old_requests_reduce_remaining_rebuild_capacity(native):
    n = native
    for index in range(32): n.ledger.reserve(n.context, 'editorial', request('unknown ' + str(index)))
    before = _all(n.client)
    with pytest.raises(n.module.FullVideoRebuildError): reserve(n)
    assert _all(n.client) == before
    assert len(json.loads(n.client.get(included.JOURNAL_KEY))['requests']) == 33


@pytest.mark.parametrize('lost_child_grants', [False, True])
def test_native_grant_cannot_be_repeated_on_its_own_failed_child(native, lost_child_grants):
    n = native; m = n.module
    reserve(n)
    assert n.case.state.acquire_retry_child_execution(CHILD, SOURCE) is True
    _edit(n.case, m.JOB_PREFIX + CHILD, 'state', 'FAILURE')
    _edit(n.case, m.JOB_PREFIX + CHILD, 'failure_stage', 'visual_qc')
    _edit(n.case, m.JOB_PREFIX + CHILD, 'audio_candidate_checkpoint', pointer(CHILD))
    n.client.hset(m.PAID_CREATE_BUDGET_PREFIX + CHILD, mapping={'cap': '6', 'used': '0'})
    n.client.hset(LEDGER_KEY, 'binding:' + CHILD, included._raw(n.context))
    if lost_child_grants:
        n.client.delete(m.POLICY_PREFIX + CHILD, m.SOURCE_PREFIX + SOURCE)
    before = _all(n.client)
    with pytest.raises(m.FullVideoRebuildError):
        m.reserve_full_video_rebuild(CHILD, '18500000-0000-4000-8000-000000000005', TOKEN,
            native_story_correction=n.note)
    assert _all(n.client) == before


def test_missing_root_witness_blocks_an_existing_grant_before_fresh_generation(native):
    n = native; m = n.module
    reserve(n)
    assert n.case.state.acquire_retry_child_execution(CHILD, SOURCE) is True
    n.client.delete(m.NATIVE_CORRECTION_ROOT_PREFIX + SOURCE)
    before = _all(n.client)
    with pytest.raises(m.FullVideoRebuildError):
        m.get_full_rebuild_policy(CHILD, SOURCE, n.case.spec)
    assert _all(n.client) == before


def test_budget_change_between_preflight_and_atomic_reservation_is_detected(native, monkeypatch):
    n = native; m = n.module; original = m._evaluate
    def changed(*args, **kwargs):
        n.client.hset(LEDGER_KEY, 'concurrent-change', 'occupied')
        return original(*args, **kwargs)
    monkeypatch.setattr(m, '_evaluate', changed)
    with pytest.raises(m.FullVideoRebuildError): reserve(n)
    assert not n.client.exists(m.POLICY_PREFIX + CHILD, m.SOURCE_PREFIX + SOURCE, m.JOB_PREFIX + CHILD)


def test_lost_reservation_ack_never_dispatches_or_grants_a_second_child(native, monkeypatch):
    n = native; m = n.module; original = m._evaluate
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError('Lost acknowledgement')
    monkeypatch.setattr(m, '_evaluate', lost)
    with pytest.raises(m.FullVideoRebuildError): reserve(n)
    monkeypatch.setattr(m, '_evaluate', original)
    assert reserve(n) == {'claimed': False, 'child_task_id': CHILD}
    assert not n.case.calls
