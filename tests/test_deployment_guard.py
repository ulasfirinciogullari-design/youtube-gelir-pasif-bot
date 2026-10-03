"""The new system never runs on the live system's Redis by accident."""
import fakeredis
import pytest
import redis

from app.config import settings
from app.services import deployment_guard as guard


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, 'studio_deployment_id', 'new-system')
    monkeypatch.setattr(settings, 'studio_adopt_existing_redis', False)
    return fakeredis.FakeRedis(decode_responses=True)


def test_first_start_claims_an_empty_redis_and_later_starts_match(client):
    assert guard.claim(client=client) == 'claimed'
    assert client.get(guard.MARKER_KEY) == 'new-system'
    client.sadd('youtube_studio:jobs', 'job')
    assert guard.claim(client=client) == 'same'


def test_redis_claimed_by_another_deployment_stops_the_start(client):
    client.set(guard.MARKER_KEY, 'other')
    with pytest.raises(guard.SharedRedisError, match="'other'"):
        guard.claim(client=client)


@pytest.mark.parametrize('key', ['youtube_studio:jobs', 'youtube_studio:oauth:channels:v3',
                                 'youtube_studio:production:v1:active'])
def test_unclaimed_redis_with_live_data_is_refused_unless_adopted(client, monkeypatch, key):
    client.set(key, 'x') if key.endswith('active') else client.sadd(key, 'x')
    with pytest.raises(guard.SharedRedisError, match='STUDIO_ADOPT_EXISTING_REDIS'):
        guard.claim(client=client)
    assert client.get(guard.MARKER_KEY) is None
    monkeypatch.setattr(settings, 'studio_adopt_existing_redis', True)
    assert guard.claim(client=client) == 'claimed'


def test_empty_id_disables_and_unreachable_redis_is_left_to_normal_errors(client, monkeypatch):
    class Down:
        def get(self, _key):
            raise redis.ConnectionError('down')
    assert guard.claim(client=Down()) == 'unavailable'
    monkeypatch.setattr(settings, 'studio_deployment_id', '')
    client.set(guard.MARKER_KEY, 'other')
    assert guard.claim(client=client) == 'disabled'


def test_web_and_workers_claim_before_serving():
    from app import celery_app
    from celery.signals import beat_init, worker_init
    import weakref

    def registered(signal):
        return any((item[1]() if isinstance(item[1], weakref.ReferenceType) else item[1])
                   is celery_app._claim_redis for item in signal.receivers)
    assert registered(worker_init) and registered(beat_init)
    import inspect
    from app import main
    assert 'claim()' in inspect.getsource(main._lifespan)
