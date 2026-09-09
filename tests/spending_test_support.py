"""Explicit synthetic funding/scene commissioning for offline dispatch tests."""
from datetime import datetime, timezone
import hashlib


TEST_KEY = 'private-test-key'


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
            'routes': [{'route': route, 'model': model, 'price_revision': quotes._REVISION}
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
