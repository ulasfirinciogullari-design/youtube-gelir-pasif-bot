"""Audited owner display correction, real receipt/Redis logic and no live API."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import editorial_routes as routes
from app.services import editorial_series_display as display, youtube_automation as automation
from test_external_editorial_review import case, approve, saved, raw, CHANNEL, CONNECTION, REVISION, review


@pytest.fixture
def ready(case, monkeypatch):
    c = case
    profile = json.loads(c.client.get(automation.PROFILE_PREFIX + CHANNEL))
    profile.update(series_id='business-decisions-01', series_name='Decisions That Changed Business', series_total=4)
    c.client.set(automation.PROFILE_PREFIX + CHANNEL, raw(profile))
    approve(c)
    c.intent = {'number': 3, 'total': 4, 'reason': 'owner_renumbered_predecessor',
                'predecessor_video_id': 'j8wwIEc24j8', 'expected_channel_id': CHANNEL,
                'expected_connection_id': CONNECTION, 'expected_profile_revision': REVISION}
    c.profile = profile
    c.api = SimpleNamespace(channel=CHANNEL, video={'id': 'j8wwIEc24j8',
        'snippet': {'channelId': CHANNEL, 'title': 'Why LEGO Changed (2/4)'},
        'status': {'privacyStatus': 'public'}}, calls=[])
    from app.services import youtube_auth, studio_state
    from googleapiclient import discovery

    def load(channel, **kwargs):
        assert channel == CHANNEL and kwargs == {'expected_connection_id': CONNECTION, 'refresh': True}
        c.api.calls.append('credentials')
        return object()

    class Resource:
        def __init__(self, kind): self.kind = kind
        def list(self, **kwargs):
            c.api.calls.append((self.kind, kwargs))
            return self
        def execute(self, **kwargs):
            assert kwargs == {'num_retries': 0}
            return {'items': [{'id': c.api.channel}] if self.kind == 'channels' else [deepcopy(c.api.video)]}

    youtube = SimpleNamespace(channels=lambda: Resource('channels'), videos=lambda: Resource('videos'))
    monkeypatch.setattr(youtube_auth, 'load_credentials', load)
    monkeypatch.setattr(discovery, 'build', lambda *a, **kw: youtube)
    monkeypatch.setattr(studio_state, 'get_job', lambda task: saved(c) if task == c.task else None)
    c.client.set(automation.SERIES_COUNTER_PREFIX + CHANNEL + ':business-decisions-01', '3')
    c.client.set(automation.SERIES_ASSIGNMENT_PREFIX + CHANNEL + ':business-decisions-01:old-lego', '3')
    return c


def create(c):
    return display.create_series_display_receipt(c.task, c.intent)


def test_receipt_preserves_source_old_allocations_and_replays_without_api(ready):
    c = ready
    before = {key: c.client.get(key) for key in c.client.keys('*') if c.client.type(key) == 'string'}
    receipt = create(c)
    assert receipt['intent']['number'] == 3
    assert receipt['predecessor_proof']['title'].endswith('(2/4)')
    for key, value in before.items(): assert c.client.get(key) == value
    calls = deepcopy(c.api.calls)
    assert create(c) == receipt and c.api.calls == calls
    assert c.client.get(display._claim_key(CHANNEL, 'business-decisions-01', 3)) == c.task


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(number=True), lambda v: v.update(number=1), lambda v: v.update(number=5),
    lambda v: v.update(total='4'), lambda v: v.update(total=10001),
    lambda v: v.update(reason='skip_series'), lambda v: v.update(predecessor_video_id='bad'),
    lambda v: v.update(expected_channel_id='other-channel'),
    lambda v: v.update(expected_connection_id='other-connection'),
    lambda v: v.update(expected_profile_revision='other-revision'),
    lambda v: v.update(proof={'verified': True}), lambda v: v.pop('reason'),
])
def test_malformed_or_wrong_target_intent_cannot_write(ready, mutation):
    mutation(ready.intent)
    with pytest.raises(display.SeriesDisplayError): create(ready)
    assert not ready.client.exists(display.RECEIPT_PREFIX + ready.task)
    assert not ready.api.calls


@pytest.mark.parametrize('damage', ['wrong_channel', 'wrong_title', 'private', 'wrong_id', 'wrong_credential_channel'])
def test_actual_google_predecessor_must_be_public_correct_channel_previous_part(ready, damage):
    c = ready
    if damage == 'wrong_channel': c.api.video['snippet']['channelId'] = 'other-channel'
    elif damage == 'wrong_title': c.api.video['snippet']['title'] = 'Why LEGO Changed (3/4)'
    elif damage == 'private': c.api.video['status']['privacyStatus'] = 'private'
    elif damage == 'wrong_id': c.api.video['id'] = 'abcdefghijk'
    else: c.api.channel = 'other-channel'
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert not c.client.exists(display.RECEIPT_PREFIX + c.task)


def test_existing_upload_cannot_acquire_receipt(ready):
    c = ready
    c.client.set(review.UPLOAD_PREFIX + c.task, raw({'status': 'complete', 'youtube_video_id': 'real-video'}))
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert not c.client.exists(display.RECEIPT_PREFIX + c.task)


@pytest.mark.parametrize('existing', ['lock', 'assignment', 'publisher_id', 'youtube_id'])
def test_existing_publication_claim_or_inflight_work_cannot_acquire_receipt(ready, existing):
    c = ready
    from app.services.youtube_publish_state import EXECUTION_LOCK_PREFIX
    if existing == 'lock': c.client.set(EXECUTION_LOCK_PREFIX + c.task, 'worker-lock')
    elif existing == 'assignment':
        c.client.set(automation.SERIES_ASSIGNMENT_PREFIX + CHANNEL + ':business-decisions-01:' + c.task, '4')
    else:
        source = saved(c)
        field, value = ('youtube_automation', {'publish_task_id': 'existing-publisher'}) if existing == 'publisher_id' else ('youtube', {'video_id': 'existing-video'})
        source['result'][field] = value
        c.client.set(review.JOB_PREFIX + c.task, raw(source))
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert not c.client.exists(display.RECEIPT_PREFIX + c.task)


def test_source_without_real_editorial_receipt_is_rejected(ready):
    c = ready
    c.client.delete(review.EDITORIAL_RECEIPT_PREFIX + c.task)
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert not c.api.calls and not c.client.exists(display.RECEIPT_PREFIX + c.task)


def test_other_source_display_claim_collision_fails_without_overwrite(ready):
    c = ready
    key = display._claim_key(CHANNEL, 'business-decisions-01', 3)
    c.client.set(key, 'another-source')
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert c.client.get(key) == 'another-source' and not c.client.exists(display.RECEIPT_PREFIX + c.task)


def test_existing_receipt_conflicting_intent_cannot_rewrite_it(ready):
    c = ready
    receipt = create(c)
    c.intent['number'] = 4
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert json.loads(c.client.get(display.RECEIPT_PREFIX + c.task)) == receipt


def test_authority_change_during_google_read_prevents_commit(ready, monkeypatch):
    c = ready
    original = display._verified_predecessor
    def change(intent):
        proof = original(intent)
        c.client.set(review.ingest.AUTH_EPOCH_KEY, '16')
        return proof
    monkeypatch.setattr(display, '_verified_predecessor', change)
    with pytest.raises(display.SeriesDisplayError): create(c)
    assert not c.client.exists(display.RECEIPT_PREFIX + c.task)


def test_real_plan_displays_three_keeps_internal_four_and_validates_receipt(ready, monkeypatch):
    c = ready
    create(c)
    monkeypatch.setattr(automation, 'reserve_series_number', lambda *args, **kwargs: 4)
    plan = automation.build_publish_plan(c.task, saved(c), c.profile)
    assert plan['series']['number'] == 4 and plan['series_display']['number'] == 3
    assert plan['title'].endswith('(3/4)')
    assert plan['description'].startswith('Decisions That Changed Business · 3/4\n\n')
    assert plan['contains_synthetic_media'] is True
    assert automation.validate_publish_plan(plan) == plan
    for mutation in [lambda v: v['series_display'].update(number=2),
                     lambda v: v.update(series_display=None),
                     lambda v: v.update(title='Wrong (4/4)'),
                     lambda v: v.update(description='Wrong description'),
                     lambda v: v['series'].update(number=2),
                     lambda v: v['series_display'].update(receipt_sha256='f' * 64)]:
        wrong = deepcopy(plan); mutation(wrong)
        with pytest.raises(automation.MetadataValidationError): automation.validate_publish_plan(wrong)
    missing = deepcopy(plan); missing.pop('series_display')
    with pytest.raises(display.SeriesDisplayError): display.validate_series_display_plan(saved(c), missing)
    c.client.delete(display.RECEIPT_PREFIX + c.task)
    with pytest.raises(automation.MetadataValidationError): automation.validate_publish_plan(plan)


def test_final_editorial_boundary_validates_display_receipt_and_scope_claim(ready, monkeypatch):
    c = ready
    create(c)
    monkeypatch.setattr(automation, 'reserve_series_number', lambda *args, **kwargs: 4)
    plan = automation.build_publish_plan(c.task, saved(c), c.profile)
    scope = CHANNEL + ':business-decisions-01'
    c.client.set(automation.SERIES_COUNTER_PREFIX + scope, '4')
    c.client.set(automation.SERIES_ASSIGNMENT_PREFIX + scope + ':' + c.task, '4')
    c.client.set(review.UPLOAD_PREFIX + c.task, raw({'publish_plan': plan}))
    assert review.validate_editorial_publication(saved(c), plan)['receipt_id'] == c.task
    c.client.set(display._claim_key(CHANNEL, 'business-decisions-01', 3), 'another-source')
    with pytest.raises(review.EditorialReviewError): review.validate_editorial_publication(saved(c), plan)


def test_ordinary_review_without_override_keeps_original_number(ready, monkeypatch):
    c = ready
    monkeypatch.setattr(automation, 'reserve_series_number', lambda *args, **kwargs: 4)
    plan = automation.build_publish_plan(c.task, saved(c), c.profile)
    assert plan['series']['number'] == 4 and 'series_display' not in plan
    assert plan['title'].endswith('(4/4)') and not c.api.calls
    assert display.series_display_watch_keys(saved(c), None) == []
    assert display.validate_series_display_plan(saved(c), None) is None


def test_route_auth_strict_schema_and_owner_intent_forwarding(ready, monkeypatch):
    c = ready
    from app.external_routes import settings
    monkeypatch.setattr(settings, 'factory_api_token', 'test-owner-token', raising=False)
    calls = []
    monkeypatch.setattr(routes, '_review_and_queue', lambda *args: calls.append(args) or {'task_id': args[0]})
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app)
    url = '/studio/api/external-masters/' + c.task + '/review'
    body = {'evidence_pack': {}, 'series_display': c.intent}
    assert client.post(url, json=body).status_code == 401 and not calls
    headers = {'X-Factory-Token': 'test-owner-token'}
    assert client.post(url, json={**body, 'series_display': None}, headers=headers).status_code == 422
    assert client.post(url, json=body, headers=headers).status_code == 200
    assert calls == [(c.task, {}, None, c.intent)]
