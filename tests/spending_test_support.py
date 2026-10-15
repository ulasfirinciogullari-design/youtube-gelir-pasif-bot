"""Explicit synthetic funding/scene commissioning for offline dispatch tests."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


TEST_KEY = 'private-test-key'
_INSTALLED_SDK_MODULES = {}


def _sdk_modules(name):
    return {key: value for key, value in sys.modules.copy().items()
            if key == name or key.startswith(name + '.')}


@contextmanager
def real_sdk_imports():
    """Temporarily replace legacy collection stubs with installed SDK packages.

    Keep each real package's submodules together so Omit retains its actual
    class identity. Restore the original module table after the test, including
    removing SDK submodules first imported by a real SDK's lazy resources.
    """
    names = ('openai', 'runwayml')
    previous = {name: _sdk_modules(name) for name in names}
    loaded = {}
    try:
        for name in names:
            cached = _INSTALLED_SDK_MODULES.get(name)
            current = sys.modules.get(name)
            if cached or not isinstance(getattr(current, '__file__', None), str):
                for key in _sdk_modules(name):
                    del sys.modules[key]
                if cached:
                    sys.modules.update(cached)
            module = importlib.import_module(name)
            assert isinstance(module.__file__, str)
            assert module.Omit is importlib.import_module(name + '._types').Omit
            loaded[name] = module
        yield loaded
    finally:
        for name in names:
            if name in loaded:
                _INSTALLED_SDK_MODULES[name] = _sdk_modules(name)
            for key in _sdk_modules(name):
                del sys.modules[key]
            sys.modules.update(previous[name])


@pytest.fixture
def installed_sdk_modules():
    with real_sdk_imports() as modules:
        yield modules


class _FakeSDKClient:
    """Offline SDK shape with dynamic auth and a detached header clone."""

    def __init__(self, base_url, api_key, *, default_headers=None, resources=None):
        self.base_url = base_url
        self.api_key = api_key
        self.organization = None
        self.project = None
        self._client = SimpleNamespace(
            auth=None, headers={}, params={}, event_hooks={'request': [], 'response': []},
        )
        self.custom_auth = None
        self._custom_headers = dict(default_headers or {})
        if resources is None:
            resources = (SimpleNamespace(create=Mock()), SimpleNamespace(create=Mock()))
        self.responses, self.text_to_video = resources
        self.with_options = Mock(side_effect=self._with_options)

    @property
    def auth_headers(self):
        return {'Authorization': 'Bearer ' + self.api_key}

    @property
    def default_headers(self):
        return {**self.auth_headers, **self._custom_headers}

    @property
    def default_query(self):
        return {}

    def _with_options(self, *, max_retries, api_key, set_default_headers):
        assert max_retries == 0
        return _FakeSDKClient(
            self.base_url, api_key, default_headers=set_default_headers,
            resources=(self.responses, self.text_to_video),
        )


def fake_sdk_client(base_url, api_key=TEST_KEY):
    """Make a synthetic SDK client; never constructs a network transport."""
    return _FakeSDKClient(base_url, api_key)


def test_funding_policy(ledger):
    from app.services import production_spend_quotes as quotes
    now = ledger.clock().astimezone(timezone.utc)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    expiry = end.strftime('%Y-%m-%dT%H:%M:%SZ')
    routes = {
        'openai': [('https://api.openai.com/v1/responses', 'gpt-6-astra')],
        'runway': [('https://api.dev.runwayml.com/v1/text_to_video', model)
                   for model in ('gen4.5', 'seedance2_fast')],
        'abacus': [('https://routellm.abacus.ai/v1/messages', model)
                   for model in quotes.ABACUS_TEXT_RATES_PER_TOKEN],
        'gemini': [('https://generativelanguage.googleapis.com/v1beta/models/' + model + suffix, model)
                   for model, suffix in [('gemini-3.1-pro-preview', ':generateContent')]
                   + [(model, ':predictLongRunning') for model in (
                       'veo-3.1-lite-generate-preview', 'veo-3.1-fast-generate-preview',
                       'veo-3.1-generate-preview')]],
    }
    return {
        'version': 1, 'currency': 'USD', 'month': now.strftime('%Y-%m'),
        'valid_from': now.replace(day=1, hour=0, minute=0, second=0).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'valid_until': expiry, 'cash_cap_micro': 10_000_000, 'opening_cash_micro': 0,
        'reconciliation_sha256': 'c' * 64,
        'accounts': [{
            'provider': provider, 'account_sha256': hashlib.sha256(provider.encode()).hexdigest(),
            'credential_sha256': hashlib.sha256((provider + '\0' + TEST_KEY).encode()).hexdigest(),
            'evidence_sha256': 'e' * 64, 'valid_until': expiry, 'mode': 'cash_only',
            'routes': [{'route': route, 'model': model, 'price_revision': (
                quotes.OPENAI_TEXT_PRICE_REVISION if provider == 'openai' else quotes._REVISION)}
                       for route, model in items],
            'funding': {'cash_factor_numerator': 1, 'cash_factor_denominator': 1,
                        'cash_bound_verified': True},
        } for provider, items in routes.items()],
    }


def initialize_test_funding(ledger):
    ledger.initialize_funding(test_funding_policy(ledger))


def initialize_test_scene(ledger, *, channel, root):
    """Wide scene envelope keeps legacy family-cap tests focused on that cap."""
    from app.services import production_spend_runtime as runtime
    prepared = runtime.prepare_video_scene_budget('a' * 64, [4.0], '9:16', scene_count=1)
    scenes = runtime._quoted_video_scenes(prepared)
    scenes[0].update(max_request_micro=10_000_000, total_micro=10_000_000)
    ledger.initialize_scene_plan(channel_id=channel, lineage_id=root, kind='shorts',
                                 connection_id='connection_AAAAA', package_sha256='a' * 64, scenes=scenes)
    return runtime._SCENE.set({'channel_id': channel, 'lineage_id': root, 'kind': 'shorts',
                              'connection_id': 'connection_AAAAA', 'package_sha256': 'a' * 64,
                              'scene_index': 0})
