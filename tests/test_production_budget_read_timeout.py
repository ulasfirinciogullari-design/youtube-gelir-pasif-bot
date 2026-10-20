"""A stalled store cannot hold the authenticated budget display indefinitely."""
import json
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import production_spend_runtime as runtime


def configured_settings(url):
    return SimpleNamespace(studio_spend_enforcement=True, redis_url=url,
        studio_spend_policy_json=json.dumps({name: 0 for name in (
            'monthly_micro', 'daily_micro', 'channel_monthly_micro',
            'shorts_micro', 'long_micro', 'derived_micro')}))


def test_budget_read_stops_on_a_real_stalled_local_connection(monkeypatch):
    stop = threading.Event()
    accepted = threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        def serve():
            with listener.accept()[0] as connection:
                accepted.set()
                stop.wait(2)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        monkeypatch.setattr(runtime, 'settings', configured_settings(
            'redis://127.0.0.1:' + str(listener.getsockname()[1])))
        start = time.monotonic()
        try:
            result = runtime.budget_status(read_timeout=0.15)
            assert accepted.is_set()
            assert result == {'enforced': True, 'status': 'blocked',
                              'reason_code': 'spend_store_unavailable'}
            assert time.monotonic() - start < 1.5
        finally:
            stop.set()
            thread.join(3)
        assert not thread.is_alive()


@pytest.mark.parametrize('timeout', [True, False, 0, -1, 6, float('nan'), float('inf'), '2'])
def test_invalid_read_deadlines_fail_before_any_store_access(monkeypatch, timeout):
    monkeypatch.setattr(runtime, 'settings', configured_settings('redis://unused.invalid'))
    factory = Mock(side_effect=AssertionError('No store access'))
    monkeypatch.setattr(runtime.redis.Redis, 'from_url', factory)
    result = runtime.budget_status(read_timeout=timeout)
    assert result == {'enforced': True, 'status': 'blocked', 'reason_code': 'spend_status_timeout_invalid'}
    factory.assert_not_called()
