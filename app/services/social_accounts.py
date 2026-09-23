"""Buffer connection and read-only shared publication monitor for Puppy.

Secrets stay encrypted, network errors are fixed codes, GET pages use cached
observations only. This module cannot create, retry, delete or edit a post.
Existing YouTube accounts and publication journals are deliberately untouched.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4

import httpx

from app.services import animation_studio as animation
from app.services.youtube_auth import _decrypt_json, _encrypt_json

PREFIX = animation.PREFIX + 'social:'
CONNECTION = PREFIX + 'buffer'
OBSERVATION = PREFIX + 'observation'
ENDPOINT = 'https://api.buffer.com'
PLATFORMS = {'instagram': 'Instagram', 'tiktok': 'TikTok'}
STATUS = {'draft': 'Taslak', 'needs_approval': 'Buffer onayı bekliyor', 'scheduled': 'Yayın sırasında',
          'sending': 'Gönderiliyor', 'sent': 'Yayımlandı', 'error': 'Bağlantı / yayın kontrolü gerekli'}
ID = re.compile(r'[A-Za-z0-9_-]{1,128}')


class SocialError(ValueError):
    pass


def require(condition, code='social_invalid'):
    if not condition:
        raise SocialError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def request(api_key, query, variables=None):
    """Only our literal queries; no mutations or automatic HTTP retries."""
    require(query.lstrip().startswith('query ') and 'mutation' not in query.lower())
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False) as session:
            with session.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer ' + api_key},
                                json={'query': query, 'variables': variables or {}}) as response:
                if response.status_code in (401, 403):
                    raise SocialError('buffer_access_required')
                if response.status_code == 429:
                    raise SocialError('buffer_rate_limited')
                require(response.status_code == 200, 'buffer_unavailable')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    require(len(raw) + len(chunk) <= 256_000, 'buffer_response_invalid')
                    raw.extend(chunk)
        result = json.loads(raw)
        require(type(result) is dict and not result.get('errors') and type(result.get('data')) is dict,
                'buffer_response_invalid')
        return result['data']
    except SocialError:
        raise
    except Exception:
        raise SocialError('buffer_unavailable') from None


def _key(value):
    require(type(value) is str and 16 <= len(value) <= 2048 and value.isascii()
            and all(32 < ord(c) < 127 for c in value), 'buffer_key_invalid')
    return value


def catalog(api_key):
    result = request(api_key, 'query StudioOrganizations { account { organizations { id } } }')
    try:
        organizations = result['account']['organizations']
        require(type(organizations) is list and 1 <= len(organizations) <= 10, 'buffer_response_invalid')
        rows = []
        for organization in organizations:
            identity = organization['id']; require(type(identity) is str and ID.fullmatch(identity))
            values = request(api_key, '''query StudioChannels($input: ChannelsInput!) {
                channels(input: $input) { id name displayName service isQueuePaused }
            }''', {'input': {'organizationId': identity}})['channels']
            require(type(values) is list and len(values) <= 100, 'buffer_response_invalid')
            for channel in values:
                if channel.get('service') not in PLATFORMS:
                    continue
                require(type(channel.get('id')) is str and ID.fullmatch(channel['id'])
                        and type(channel.get('isQueuePaused')) is bool, 'buffer_response_invalid')
                rows.append({'id': channel['id'], 'organization_id': identity, 'platform': channel['service'],
                    'name': str(channel.get('displayName') or channel.get('name') or channel['id'])[:160],
                    'paused': channel['isQueuePaused']})
        require(len({r['id'] for r in rows}) == len(rows), 'buffer_response_invalid')
        return rows
    except SocialError:
        raise
    except Exception:
        raise SocialError('buffer_response_invalid') from None


def _read(store):
    raw = store.get(CONNECTION)
    if not raw:
        return None
    value = json.loads(raw)
    require(type(value) is dict and value.get('version') == 1 and value.get('brand_id') == animation.BRAND_ID
            and type(value.get('channels')) is list and type(value.get('selected')) is dict,
            'social_storage_invalid')
    return value


def connect(api_key, *, store=None):
    api_key = _key(api_key)
    store = store or animation.client()
    channels = catalog(api_key)  # Verify before storing; no publication.
    encrypted = _encrypt_json({'buffer_api_key': api_key})
    with store.pipeline() as pipe:
        pipe.watch(CONNECTION)
        previous = _read(pipe)
        selected = previous['selected'] if previous else {}
        # A key for a different organization cannot inherit an old binding.
        selected = {p: i for p, i in selected.items() if any(c['id'] == i and c['platform'] == p for c in channels)}
        value = {'version': 1, 'brand_id': animation.BRAND_ID, 'revision': str(uuid4()),
            'credential': encrypted, 'key_sha256': hashlib.sha256(api_key.encode()).hexdigest(),
            'channels': channels, 'selected': selected, 'checked_at': now()}
        pipe.multi(); pipe.set(CONNECTION, animation.encoded(value))
        require(pipe.execute() == [True], 'social_save_uncertain')
    return public_state(store=store)


def bind(revision, selected, *, store=None):
    require(type(selected) is dict and set(selected) <= set(PLATFORMS))
    store = store or animation.client()
    original = _read(store); require(original is not None, 'buffer_access_required')
    channels = catalog(_decrypt_json(original['credential'])['buffer_api_key'])
    for platform, identity in selected.items():
        require(any(c['platform'] == platform and c['id'] == identity for c in channels), 'social_channel_changed')
    with store.pipeline() as pipe:
        pipe.watch(CONNECTION)
        current = _read(pipe)
        require(current == original and current['revision'] == revision, 'social_changed')
        current.update(selected=selected, channels=channels, revision=str(uuid4()), checked_at=now())
        pipe.multi(); pipe.set(CONNECTION, animation.encoded(current))
        require(pipe.execute() == [True], 'social_save_uncertain')
    return public_state(store=store)


def observe(*, store=None):
    store = store or animation.client()
    original = _read(store)
    require(original is not None, 'buffer_access_required')
    api_key = _decrypt_json(original['credential'])['buffer_api_key']
    channels = catalog(api_key)
    posts, missing, truncated = [], [], False
    for platform, identity in original['selected'].items():
        channel = next((c for c in channels if c['id'] == identity and c['platform'] == platform), None)
        if channel is None:
            missing.append(platform); continue
        data = request(api_key, '''query StudioPosts($input: PostsInput!) {
            posts(first: 50, input: $input) {
                edges { node { id text channelId status dueAt } }
                pageInfo { hasNextPage }
            }
        }''', {'input': {'organizationId': channel['organization_id'], 'filter': {'channelIds': [identity]}}})
        try:
            result = data['posts']; edges = result['edges']
            require(type(edges) is list and len(edges) <= 50, 'buffer_response_invalid')
            require(type(result['pageInfo']['hasNextPage']) is bool, 'buffer_response_invalid')
            truncated |= result['pageInfo']['hasNextPage']
            for edge in edges:
                post = edge['node']
                require(post['channelId'] == identity and post['status'] in STATUS
                        and type(post['id']) is str and ID.fullmatch(post['id']), 'buffer_response_invalid')
                posts.append({'id': post['id'], 'platform': platform, 'channel_id': identity,
                    'text': str(post.get('text', ''))[:500], 'status': post['status'],
                    'due_at': str(post['dueAt'])[:40] if post.get('dueAt') else None})
        except SocialError:
            raise
        except Exception:
            raise SocialError('buffer_response_invalid') from None
    observation = {'version': 1, 'checked_at': now(), 'connection_revision': original['revision'],
        'channels': channels, 'missing_platforms': missing, 'posts': posts, 'truncated': truncated}
    with store.pipeline() as pipe:
        pipe.watch(CONNECTION)
        require(_read(pipe) == original, 'social_changed')
        pipe.multi(); pipe.set(OBSERVATION, animation.encoded(observation))
        require(pipe.execute() == [True], 'social_save_uncertain')
    return public_state(store=store)


def public_state(*, store=None):
    store = store or animation.client()
    connection = _read(store)
    if connection is None:
        return {'connected': False, 'revision': 'new', 'channels': [], 'selected': {}, 'observation': None}
    raw = store.get(OBSERVATION)
    observation = json.loads(raw) if raw else None
    if observation and observation.get('connection_revision') != connection['revision']:
        observation = None
    return {'connected': True, 'revision': connection['revision'],
            'channels': observation['channels'] if observation else connection['channels'],
            'selected': connection['selected'], 'checked_at': connection['checked_at'], 'observation': observation}
