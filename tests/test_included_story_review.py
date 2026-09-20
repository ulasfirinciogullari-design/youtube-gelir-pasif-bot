"""Exercise the real independent story critic, observed reply, and sealed approval."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import director, production_included_router as included, production_spend_runtime as runtime
from app.services import included_research_sources as sources, abacus_router_review_runtime as transport
from test_production_included_router import commissioned, client, CONTEXT
from test_abacus_router_adapter import KEY, response, envelope


def fixture_story():
    # Read only pure fixtures; importing the legacy test module changes global
    # application settings during collection and can hide routing defects.
    tree = ast.parse(Path(__file__).with_name('test_director.py').read_text())
    names = {'_scene', 'make_coherent_battery_package', 'critic_payload'}
    tree.body = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name in names
        or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CRITIC_BOOLEAN_KEYS' for t in node.targets))]
    env = {'_word_count': director._word_count}
    exec(compile(tree, '<pure-story-fixtures>', 'exec'), env)
    package = env['make_coherent_battery_package']()
    for scene in package['scenes']:
        scene['ai_prompt'] = None
    package['ai_scenes'] = []
    return package, env['critic_payload']


@pytest.mark.parametrize('rejected', [False, True])
def test_actual_critic_requires_sources_and_failed_verdict_never_gets_approval(commissioned, monkeypatch, rejected):
    ledger, _, _ = commissioned
    from app import config
    for key, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                      'abacus_api_key': KEY, 'gemini_critic_enabled': True}.items():
        monkeypatch.setattr(config.settings, key, value, raising=False)
    monkeypatch.setattr(director, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: deepcopy(CONTEXT))
    package, critic = fixture_story()
    generated = {'scenes': [{'position': pos, 'narration': scene['narration'],
        'visual_queries': scene['visual_queries'], 'ai_prompt': None} for pos, scene in enumerate(package['scenes'])]}
    calls, consulted = [], []
    def page(url):
        consulted.append(url)
        return {'url': url, 'text': 'ACTUAL SOURCE TEXT FOR THE INDEPENDENT CRITIC', 'text_sha256': 'd' * 64}
    monkeypatch.setattr(sources, 'fetch_page', page)
    def send(prepared):
        calls.append(prepared)
        output = generated if len(calls) == 1 else critic(stock_positions=tuple(range(6)),
            story_failures=['causal_claim_supported'] if rejected else [])
        if len(calls) > 1:
            assert 'ACTUAL SOURCE TEXT FOR THE INDEPENDENT CRITIC' in json.dumps(prepared.payload)
        body = envelope(); body['choices'][0]['message']['content'] = json.dumps(output)
        return response(prepared, payload=body)
    monkeypatch.setattr(transport, '_send_once', send)
    monkeypatch.setattr(director, 'run_optional_gemini_critic', lambda *a, **kw: pytest.fail('No paid fallback'))
    topic = 'Soğukta telefonun pili neden hızla düşer?'
    if rejected:
        with pytest.raises(RuntimeError):
            director._repair_short_stock_scenes(None, package, 'Turkish', .5, topic)
        assert 'subscription_router_critic' not in (package.get('stock_scene_qc') or {})
    else:
        result = director._repair_short_stock_scenes(None, package, 'Turkish', .5, topic)
        assert included.story_review_matches(result, topic)
        changed = deepcopy(result); changed['scenes'][0]['narration'] += ' Invented fact.'
        assert not included.story_review_matches(changed, topic)
        assert not included.story_review_matches(result, 'Unrelated different topic')
        assert len(calls) == 2
    assert set(consulted) == {s['url'] for s in package['sources']}
    assert ledger.foundation.snapshot()['historical_cash_micro'] is None
