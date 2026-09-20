"""Exercise the real independent story critic, observed reply, and sealed approval."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import director, production_included_router as included, production_spend_runtime as runtime
from app.services import included_research_sources as sources, production_included_transport as transport
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
@pytest.mark.parametrize('review_only', [False, True])
def test_actual_critic_requires_sources_and_failed_verdict_never_gets_approval(commissioned, monkeypatch, rejected, review_only):
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
        output = generated if len(calls) == 1 and not review_only else critic(stock_positions=tuple(range(6)),
            story_failures=['causal_claim_supported'] if rejected else [])
        if len(calls) > 1 or review_only:
            assert 'ACTUAL SOURCE TEXT FOR THE INDEPENDENT CRITIC' in json.dumps(prepared.payload)
            assert director._SOURCE_IDENTITY_RULE in prepared.payload['messages'][1]['content'][0]['text']
            assert 'raw-material cost, face value, sale price and profit' in json.dumps(prepared.payload)
        body = envelope(); body['choices'][0]['message']['content'] = json.dumps(output)
        return response(prepared, payload=body)
    monkeypatch.setattr(transport, 'send_once', send)
    monkeypatch.setattr(director, 'run_optional_gemini_critic', lambda *a, **kw: pytest.fail('No paid fallback'))
    topic = 'Soğukta telefonun pili neden hızla düşer?'
    original = deepcopy(package)
    def run_review():
        if not review_only:
            return director._repair_short_stock_scenes(None, package, 'Turkish', .5, topic)
        from app.services.audio_checkpoint import _candidate_package
        saved = _candidate_package(package)
        # The real content-addressed loader rebuilds this exact joined field.
        saved['narration'] = ' '.join(s['narration'] for s in saved['scenes'])
        return director.revalidate_immutable_short_story(saved, topic, .5, 'tr',
            {'mode': 'production', 'format': 'shorts', 'content_style': 'documentary'},
            immutable_candidate_narrations=[s['narration'] for s in saved['scenes']], immutable_stock_routes=True)
    if rejected:
        with pytest.raises(RuntimeError):
            run_review()
        assert 'subscription_router_critic' not in (package.get('stock_scene_qc') or {})
    else:
        result = run_review()
        assert included.story_review_matches(result, topic)
        changed = deepcopy(result); changed['scenes'][0]['narration'] += ' Invented fact.'
        assert not included.story_review_matches(changed, topic)
        assert not included.story_review_matches(result, 'Unrelated different topic')
        assert len(calls) == (1 if review_only else 2)
        if review_only:
            assert result['narration'] == ' '.join(s['narration'] for s in original['scenes'])
            assert [s['visual_queries'] for s in result['scenes']] == [s['visual_queries'] for s in original['scenes']]
            assert director.short_story_package_is_approved(result, topic)
    if review_only:
        assert len(calls) == 1
        journal = json.loads(ledger.client.get(included.JOURNAL_KEY))
        assert {row['purpose'] for row in journal['requests'].values()} == {'story_review'}
    assert set(consulted) == {s['url'] for s in package['sources']}
    assert ledger.foundation.snapshot()['historical_cash_micro'] is None
