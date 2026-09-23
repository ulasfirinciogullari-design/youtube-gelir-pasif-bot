from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from zipfile import ZipFile

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from redis.exceptions import WatchError

from app import animation_routes as routes
from app.config import settings
from app.services import animation_studio as animation, social_accounts as social

KEY = 'offline-buffer-key-for-test-only'
CHANNELS = [
    {'id': 'igPuppy', 'organization_id': 'org1', 'platform': 'instagram', 'name': 'Puppy IG', 'paused': False},
    {'id': 'ttPuppy', 'organization_id': 'org1', 'platform': 'tiktok', 'name': 'Puppy TT', 'paused': False},
]
NOW = datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def state(monkeypatch):
    store = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(animation, 'client', lambda: store)
    monkeypatch.setattr(settings, 'app_encryption_key', 'offline-animation-test-encryption')
    monkeypatch.setattr(settings, 'factory_api_token', 'test-owner-cookie')
    monkeypatch.setattr(settings, 'google_redirect_uri', 'https://studio.example/studio/youtube/callback')
    return store


@pytest.fixture
def ui(state):
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, base_url='https://studio.example')
    client.cookies.set(routes.COOKIE_NAME, 'test-owner-cookie')
    return client


def test_concurrent_day_planning_never_duplicates_or_spends(state):
    def prepare(_):
        try:
            return animation.prepare_day(store=state, now=NOW)
        except WatchError:
            return {'status': 'changed'}
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(prepare, range(5)))
    assert sum(r['status'] == 'prepared' for r in results) == 1
    assert len(animation.queue(store=state)) == 5
    assert all(row['phase'] == 'script_ready' and row['output'] is None for row in animation.queue(store=state))
    assert all(key.startswith(animation.PREFIX) for key in state.scan_iter())
    receipts = [json.loads(state.get(k)) for k in state.scan_iter(animation.DAY+'*')]
    assert receipts[0]['day'] == '2026-09-24'
    assert receipts[0]['provider_requests'] == receipts[0]['publication_requests'] == 0


def test_stock_counts_unfinished_drafts_and_new_day_cannot_grow_forever(state):
    assert animation.prepare_day(now=NOW)['created'] == 5
    assert animation.prepare_day(now=NOW + timedelta(days=1))['created'] == 5
    assert animation.prepare_day(now=NOW + timedelta(days=2))['status'] == 'stock_full'
    assert len(animation.queue()) == 10
    assert sum(r['phase'] == 'outline' for r in animation.queue()) == 5


def test_four_episode_cadence_preserves_five_episode_pilot_finale(state):
    animation.save_config('new', {'episodes_per_day': 4, 'stock_target': 4})
    assert animation.prepare_day(now=NOW)['status'] == 'pilot_requires_five_stock_slots'
    config = animation.read_config()
    animation.save_config(config['revision'], {'stock_target': 10})
    assert animation.prepare_day(now=NOW)['created'] == 5
    assert animation.pilot()['episodes'][-1]['next_question'] == ''


def test_stale_settings_or_audience_bypass_cannot_change_config(state):
    animation.save_config('new', {'planning_enabled': False})
    before = state.get(animation.CONFIG)
    with pytest.raises(animation.AnimationError, match='animation_changed'):
        animation.save_config('new', {'stock_target': 20})
    with pytest.raises(animation.AnimationError):
        animation.save_config(animation.read_config()['revision'], {'made_for_kids': False})
    assert state.get(animation.CONFIG) == before
    assert animation.prepare_day(now=NOW)['status'] == 'paused'


def test_puppy_lead_and_multilingual_drafts_bind_to_exact_script(state):
    assert animation.brand()['lead_character'] == 'puppy'
    for ep in animation.pilot()['episodes']:
        assert all('Puppy' in s['action'] for s in ep['shots'])
        assert sum(s['seconds'] for s in ep['shots']) == 32
        candidates = animation.translation_candidates(ep)
        assert len(candidates) == 10
        cues = animation.dialogue_cues(ep)
        assert all(c['end'] <= 32 for c in cues)
        assert len({c['id'] for c in cues}) == len(cues)
        for code, rows in candidates.items():
            assert set(rows) == {c['id'] for c in cues}
            animation.subtitles(ep, code, rows)
    ep = deepcopy(animation.pilot()['episodes'][0])
    ep['shots'][0]['dialogue'][0]['text'] = 'Changed speech'
    with pytest.raises(animation.AnimationError, match='outdated'):
        animation.translation_candidates(ep)


def test_zip_has_utf8_rtl_timing_and_honest_manifest(state):
    with ZipFile(BytesIO(animation.subtitle_package('the-little-light-1'))) as archive:
        assert len(archive.namelist()) == 22
        assert 'ضوء صغير' in archive.read('the-little-light-1.ar.srt').decode()
        assert archive.read('the-little-light-1.ko.vtt').decode().startswith('WEBVTT\n')
        assert '00:00:04,200 --> 00:00:06,000' in archive.read('the-little-light-1.en.srt').decode()
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['status'] == 'draft' and manifest['audio_tracks'] == []
        assert manifest['youtube_uploaded'] is False


@pytest.mark.parametrize('bad', ['<script>', 'hello\n\n999', 'hello --> world', '\x00', ' '])
def test_subtitle_control_or_markup_injection_is_rejected(state, bad):
    ep = animation.pilot()['episodes'][0]
    rows = animation.translation_candidates(ep)['en']
    rows['s01-1'] = bad
    with pytest.raises(animation.AnimationError):
        animation.subtitles(ep, 'en', rows)


def test_studio_get_pages_are_readonly_and_protect_assets_and_packages(state, ui, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('No provider calls on GET')
    monkeypatch.setattr(social, 'request', forbid)
    paths = ['/studio/animation', '/studio/social', '/studio/api/animation',
             '/studio/animation/episode/the-little-light-1',
             '/studio/animation/episode/the-little-light-1/subtitles.zip',
             '/studio/animation/assets/puppy-model-sheet-v1.png']
    for path in paths:
        response = ui.get(path)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        assert 'test-owner-cookie' not in response.text
    assert list(state.scan_iter()) == []
    assert ui.get('/studio/animation/assets/brand.json').status_code == 404
    ui.cookies.clear()
    for path in paths:
        assert ui.get(path).status_code == 401


def test_posts_reject_foreign_origin_and_keys_are_never_reflected(state, ui, monkeypatch):
    calls = []
    monkeypatch.setattr(social, 'connect', lambda key: calls.append(key))
    data = {'api_key': KEY}
    for path in ['/studio/social/connect', '/studio/animation/prepare', '/studio/social/refresh']:
        assert ui.post(path, data=data, headers={'Origin': 'https://foreign.example'}).status_code == 403
    response = ui.post('/studio/social/connect', data=data, headers={'Origin': 'https://studio.example'}, follow_redirects=False)
    assert response.status_code == 303 and calls == [KEY]
    assert KEY not in response.text and KEY not in str(response.headers)
    response = ui.post('/studio/social/connect', json=data, headers={'Origin': 'https://studio.example'})
    assert response.status_code == 415 and KEY not in response.text
    response = ui.post('/studio/social/connect', content='api_key='+KEY+'&api_key='+KEY,
                       headers={'Origin': 'https://studio.example', 'Content-Type': 'application/x-www-form-urlencoded'})
    assert response.status_code == 422 and KEY not in response.text


def test_encrypted_connection_explicit_binding_and_rotation_invalidate_old_observation(state, monkeypatch):
    monkeypatch.setattr(social, 'catalog', lambda _: deepcopy(CHANNELS))
    public = social.connect(KEY)
    assert KEY not in state.get(social.CONNECTION)
    assert 'credential' not in public and 'key_sha256' not in public and public['selected'] == {}
    public = social.bind(public['revision'], {'instagram': 'igPuppy'})
    assert public['selected'] == {'instagram': 'igPuppy'}
    with pytest.raises(social.SocialError):
        social.bind(public['revision'], {'youtube': 'YTchannel'})
    with pytest.raises(social.SocialError, match='channel_changed'):
        social.bind(public['revision'], {'instagram': 'ttPuppy'})
    with pytest.raises(social.SocialError, match='social_changed'):
        social.bind('stale', {'instagram': 'igPuppy'})
    state.set(social.OBSERVATION, animation.encoded({'connection_revision': public['revision'], 'posts': [], 'channels': CHANNELS}))
    monkeypatch.setattr(social, 'catalog', lambda _: [{**CHANNELS[0], 'id': 'someoneElse'}])
    rotated = social.connect('different-offline-buffer-key')
    assert rotated['selected'] == {} and rotated['observation'] is None


def test_monitor_only_selected_channels_and_preserves_published_vs_queued(state, monkeypatch):
    calls = []
    def remote(key, query, variables=None):
        calls.append((query, variables))
        assert key == KEY and query.startswith('query ') and 'mutation' not in query
        if 'StudioOrganizations' in query:
            return {'account': {'organizations': [{'id': 'org1'}]}}
        if 'StudioChannels' in query:
            return {'channels': [{'id': r['id'], 'displayName': r['name'], 'service': r['platform'], 'isQueuePaused': False} for r in CHANNELS]
                    + [{'id': 'YT', 'service': 'youtube', 'displayName': 'Capital', 'isQueuePaused': False}]}
        assert variables['input']['filter']['channelIds'] == ['igPuppy']
        return {'posts': {'edges': [{'node': {'id': 'post'+s, 'text': '<script>unsafe</script>', 'status': s,
                 'channelId': 'igPuppy', 'dueAt': '2026-09-25T10:00:00Z'}} for s in ('scheduled', 'sent', 'error')],
                 'pageInfo': {'hasNextPage': True}}}
    monkeypatch.setattr(social, 'request', remote)
    connected = social.connect(KEY)
    assert len(connected['channels']) == 2
    social.bind(connected['revision'], {'instagram': 'igPuppy'})
    state_view = social.observe()
    assert [r['status'] for r in state_view['observation']['posts']] == ['scheduled', 'sent', 'error']
    assert state_view['observation']['truncated'] is True
    page = routes.render_social(state_view).body.decode()
    assert '<script>unsafe</script>' not in page
    assert '&lt;script&gt;unsafe&lt;/script&gt;' in page
    before = state.get(social.OBSERVATION)
    monkeypatch.setattr(social, 'catalog', lambda _: (_ for _ in ()).throw(social.SocialError('buffer_unavailable')))
    with pytest.raises(social.SocialError):
        social.observe()
    assert state.get(social.OBSERVATION) == before


def test_http_contract_is_bounded_no_redirects_and_no_secret_errors(state, monkeypatch):
    real_client = httpx.Client
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == social.ENDPOINT and request.headers['authorization'] == 'Bearer '+KEY
        return httpx.Response(429, json={'message': KEY})
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(social.httpx, 'Client', client)
    with pytest.raises(social.SocialError, match='buffer_rate_limited') as error:
        social.request(KEY, 'query Test { account { organizations { id } } }')
    assert KEY not in str(error.value) and len(calls) == 1
    with pytest.raises(social.SocialError):
        social.request(KEY, 'mutation Create { createPost }')
    assert len(calls) == 1
