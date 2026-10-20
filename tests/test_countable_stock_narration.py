"""Countable writing remains unapproved until the actual independent path runs."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services import countable_stock_narration as words, director
from app.services import production_included_router as included, production_spend_runtime as runtime
from app.services import included_research_sources as sources, production_included_transport as transport
from test_production_included_router import commissioned, client, CONTEXT
from test_abacus_router_adapter import KEY, response, envelope
from test_included_story_review import fixture_story


@pytest.fixture
def writer_commissioned(client, monkeypatch):
    # Four observed synthetic calls are needed for a rejected visual plan and
    # its review. Choose this allowance before genesis; never alter a ledger.
    initialize = included.IncludedRouterLedger.initialize
    def initial_policy(ledger, policy):
        return initialize(ledger, {**policy, 'max_requests_per_lineage': 4})
    monkeypatch.setattr(included.IncludedRouterLedger, 'initialize', initial_policy)
    return commissioned.__wrapped__(client, monkeypatch)


def package():
    tree = ast.parse(Path(__file__).with_name('test_documentary_evidence_planning.py').read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'package']
    env = {}; exec(compile(tree, '<pure-countable-fixture>', 'exec'), env)
    value = env['package'](calibrated=True)
    text = 'Membership fees and merchandise sales play distinct roles in this business.'
    value['scenes'][-1].update(narration=text, tts_text=text)
    value['narration'] = value['tts_narration'] = ' '.join(s['narration'] for s in value['scenes'])
    value['spoken_word_budget'] = dict(director._ENGLISH_SHORT_SPOKEN_BUDGET)
    assert director._word_count(value['narration']) == 66
    return value


def encoded(value):
    rows = []
    for pos, scene in enumerate(value['scenes']):
        parts = scene['narration'].split()
        assert len(parts) == 11
        rows.append({'position': pos, 'narration_words': dict(zip(words.SLOTS, parts, strict=True)),
            'visual_queries': deepcopy(scene['visual_queries']), 'ai_prompt': None})
    return {'scenes': rows}


def test_word_order_is_explicit_and_joining_changes_no_observed_input_or_visuals():
    value = encoded(package()); before = deepcopy(value)
    for row in value['scenes']:
        row['narration_words'] = dict(reversed(list(row['narration_words'].items())))
    output = words.decode(value)
    assert value == before
    assert [r['narration'] for r in output['scenes']] == [s['narration'] for s in package()['scenes']]
    assert sum(director._word_count(r['narration']) for r in output['scenes']) == 66
    assert all(set(r) == {'position', 'narration', 'visual_queries', 'ai_prompt'} for r in output['scenes'])
    assert [r['visual_queries'] for r in output['scenes']] == [r['visual_queries'] for r in value['scenes']]
    assert not any('approved' in str(k) for k in output)


def test_optional_title_metadata_never_changes_package_content_or_raw_response():
    value = encoded(package()); value['title'] = 'An unused suggested title'
    before = deepcopy(value)
    assert words.decode(value) == words.decode({k: v for k, v in value.items() if k != 'title'})
    assert value == before and 'title' not in words.decode(value)


def test_observed_null_tail_contains_no_extra_speech_and_preserves_every_authored_word():
    value = encoded(package()); original = deepcopy(value)
    value['scenes'][4]['narration_words'].update(w12=None, w13=None, w14=None, w15=None)
    before = deepcopy(value)
    assert words.decode(value) == words.decode(original)
    assert value == before
    schema, _ = words.request_format(director._stock_writer_json_schema(list(range(6))),
        {'scenes': [{'position': 0, 'narration': 'example', 'visual_queries': [], 'ai_prompt': None}]})
    from app.services.abacus_router_adapter import _matches_schema
    assert _matches_schema(value, schema)
    for field, content in [('w12', 'extra'), ('w12', False), ('w16', None)]:
        changed = deepcopy(value); changed['scenes'][4]['narration_words'][field] = content
        with pytest.raises(ValueError): words.decode(changed)


@pytest.mark.parametrize('extra', [{'title': ''}, {'title': 1}, {'title': 'x' * 181},
    {'title': 'bad\x00title'}, {'qa_approved': True}, {'publish_eligible': True}])
def test_only_bounded_unused_title_metadata_is_accepted(extra):
    value = {**encoded(package()), **extra}; before = deepcopy(value)
    with pytest.raises(ValueError): words.decode(value)
    assert value == before


@pytest.mark.parametrize('damage', ['missing', 'extra', 'phrase', 'empty', 'punctuation', 'control', 'long', 'number'])
def test_invalid_word_fields_cannot_be_silently_padded_truncated_or_normalized(damage):
    value = encoded(package()); row = value['scenes'][0]['narration_words']
    if damage == 'missing': row.pop('w11')
    elif damage == 'extra': row['w12'] = 'extra'
    else:
        row['w01'] = {'phrase': 'two words', 'empty': '', 'punctuation': '.',
            'control': 'word\x00', 'long': 'x' * 81, 'number': 1}[damage]
    before = deepcopy(value)
    with pytest.raises(ValueError): words.decode(value)
    assert value == before


@pytest.mark.parametrize('word', ['Laurer’s', 'don’t', 'Nintendo‘s'])
def test_typographic_apostrophe_is_one_observed_word_and_never_changes_the_text(word):
    value = encoded(package()); value['scenes'][0]['narration_words']['w01'] = word
    before = deepcopy(value)
    result = words.decode(value)
    assert result['scenes'][0]['narration'].startswith(word + ' ')
    assert director._word_count(word) == 1
    assert director._word_count(result['scenes'][0]['narration']) == 11
    assert sum(director._word_count(row['narration']) for row in result['scenes']) == 66
    assert value == before and not any('approved' in str(k) for k in result)


@pytest.mark.parametrize('text,expected', [("Laurer's barcode", 2), ('Laurer’s barcode', 2),
    ('“scan the barcode”', 3), ('first second', 2), ('can’t scan', 2), ('Ankara’nın sokakları', 2)])
def test_spoken_budget_uses_same_count_for_ascii_and_typographic_punctuation(text, expected):
    assert director._word_count(text) == expected


@pytest.mark.parametrize('damage', ['provider', 'fresh_false', 'fresh_truthy', 'uncalibrated', 'scene_count', 'generated', 'locked', 'no_targets', 'null_targets', 'bad_target', 'null_scenes'])
def test_legacy_saved_and_noncalibrated_routes_do_not_use_word_fields(damage):
    args = ['abacus_included', True, True, package()['scenes'], list(range(6)), [{'position': 0}]]
    if damage == 'provider': args[0] = 'openai'
    elif damage == 'fresh_false': args[1] = False
    elif damage == 'fresh_truthy': args[1] = 1
    elif damage == 'uncalibrated': args[2] = False
    elif damage == 'scene_count': args[3] = args[3][:-1]
    elif damage == 'generated': args[4] = [0, 2, 4]
    elif damage == 'locked': args[5] = [{'position': 0, 'locked_narration': 'Preserve this exact text.'}]
    elif damage == 'no_targets': args[5] = []
    elif damage == 'null_targets': args[5] = None
    elif damage == 'bad_target': args[5] = [None]
    elif damage == 'null_scenes': args[3] = None
    assert words.eligible(*args) is False


@pytest.mark.parametrize('reject', [False, 'factual', 'editorial', 'visual_then_accept'])
@pytest.mark.parametrize('with_title', [False, True])
def test_native_observed_word_fields_require_full_independent_critique(writer_commissioned, monkeypatch, reject, with_title):
    ledger, _, _ = writer_commissioned
    from app import config
    for key, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                      'abacus_api_key': KEY, 'gemini_critic_enabled': True}.items():
        monkeypatch.setattr(config.settings, key, value, raising=False)
    monkeypatch.setattr(director, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: deepcopy(CONTEXT))
    value = package(); before = deepcopy(value)
    _, critic = fixture_story()
    calls = []
    def page(url):
        return {'url': url, 'text': 'ACTUAL RETRIEVED TEST EVIDENCE', 'text_sha256': 'd' * 64}
    monkeypatch.setattr(sources, 'fetch_page', page)
    from app.services.included_source_passages import catalogue
    passage = catalogue([page(value['sources'][0]['url'])])[0]
    def send(prepared):
        calls.append(prepared)
        prompt = json.dumps(prepared.payload)
        if len(calls) == 1:
            assert words.RULE in prompt
            output = encoded(value)
            if with_title: output['title'] = 'This suggested title must never replace the existing title'
        elif len(calls) == 3 and reject == 'visual_then_accept':
            assert words.RULE not in prompt and 'narration_words' not in prompt
            output = {'scenes': [{'position': 2,
                'narration': 'This invented assertion must never replace the already reviewed narration.',
                'visual_queries': ['worker inspecting original shipping cartons warehouse',
                    'warehouse original shipping cartons inspection close up'], 'ai_prompt': None}]}
        else:
            assert len(calls) in (2, 4) and 'ACTUAL RETRIEVED TEST EVIDENCE' in prompt
            assert all(s['narration'] in prompt for s in value['scenes'])
            assert 'narration_words' not in prompt
            assert 'This invented assertion' not in prompt
            output = {'editorial_review': critic(stock_positions=tuple(range(6)),
                failures={2: ['common_stock_clip_feasible']} if reject == 'visual_then_accept' and len(calls) == 2 else {},
                story_failures=['causal_claim_supported'] if reject == 'editorial' else []),
                'factual_audit': {'sentences': [{'position': pos, 'narration': s['narration'],
                    'assessment': 'unsupported' if reject == 'factual' and pos == 2 else 'supported',
                    'reason': 'Synthetic test assessment for this complete sentence.',
                    'quotations': [{'passage_id': passage['passage_id']}]} for pos, s in enumerate(value['scenes'])]}}
        body = envelope(); body['choices'][0]['message']['content'] = json.dumps(output)
        return response(prepared, payload=body)
    monkeypatch.setattr(transport, 'send_once', send)
    monkeypatch.setattr(director, 'run_optional_gemini_critic', lambda *a, **kw: pytest.fail('No paid fallback'))
    def run():
        return director._repair_short_stock_scenes(None, value, 'English', .5, 'Explain the sourced membership model.',
            fresh_scheduled=True, spoken_word_budget=value['spoken_word_budget'])
    expected_calls = 4 if reject == 'visual_then_accept' else 2
    if reject in ('factual', 'editorial'):
        with pytest.raises(RuntimeError) as error: run()
        assert error.value.planning_diagnostics['publish_eligible'] is False
    else:
        result = run()
        assert result['narration'] == value['narration']
        assert result['title'] == value['title']
        assert result['stock_scene_qc']['source_claim_review']['accepted'] is True
        assert included.story_review_matches(result, 'Explain the sourced membership model.')
        assert len(calls) == expected_calls
        assert run()['narration'] == value['narration'] and len(calls) == expected_calls
    assert len(calls) == expected_calls and value == before
    journal = json.loads(ledger.client.get(included.JOURNAL_KEY))
    assert len(journal['requests']) == expected_calls and all(r['outcome'] is not None for r in journal['requests'].values())
    assert ledger.foundation.snapshot()['cash_spending_enabled'] is False


@pytest.mark.parametrize('outcome', ['repaired', 'factual_rejection', 'still_fragmented'])
def test_observed_incomplete_hook_uses_existing_writer_repair_before_critic(writer_commissioned, monkeypatch, outcome):
    ledger, _, _ = writer_commissioned
    from app import config
    for key, value in {'studio_spend_enforcement': True, 'studio_abacus_included_production': True,
                      'abacus_api_key': KEY, 'gemini_critic_enabled': True}.items():
        monkeypatch.setattr(config.settings, key, value, raising=False)
    monkeypatch.setattr(director, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'settings', config.settings)
    monkeypatch.setattr(runtime, 'configured_ledger', lambda **kw: ledger.foundation)
    monkeypatch.setattr(runtime, 'resolve_context', lambda *a: deepcopy(CONTEXT))
    value = package(); before = deepcopy(value)
    _, critic = fixture_story()
    def page(url):
        return {'url': url, 'text': 'ACTUAL RETRIEVED TEST EVIDENCE', 'text_sha256': 'd' * 64}
    monkeypatch.setattr(sources, 'fetch_page', page)
    from app.services.included_source_passages import catalogue
    passage = catalogue([page(value['sources'][0]['url'])])[0]
    # Exact provider-authored failure observed in the restaurant Short. The
    # decoder must retain it verbatim, never append a word or punctuation.
    fragment = "Why does a furniture retailer operate some of the world's busiest"
    broken = encoded(value)
    broken['scenes'][0]['narration_words'] = dict(zip(words.SLOTS, fragment.split(), strict=True))
    observed = deepcopy(broken)
    assert words.decode(broken)['scenes'][0]['narration'] == fragment and broken == observed
    calls = []
    def send(prepared):
        calls.append(prepared)
        prompt = json.dumps(prepared.payload)
        if len(calls) == 1:
            output = deepcopy(broken)
        elif len(calls) == 2 or outcome == 'still_fragmented':
            assert len(calls) <= 3 and words.RULE in prompt
            assert 'narration has no sentence ending' in prompt
            context = json.loads(prepared.payload['messages'][1]['content'][0]['text'].split(
                'Story context:\n', 1)[1].split('\n\nReturn ONLY', 1)[0])
            assert [row['position'] for row in context['stock_positions_to_rewrite']] == [0]
            assert [row['position'] for row in context['accepted_stock_scenes_locked']] == [1, 2, 3, 4, 5]
            output = {'scenes': [deepcopy((broken if outcome == 'still_fragmented' else encoded(value))['scenes'][0])]}
        else:
            assert len(calls) == 3 and words.RULE not in prompt
            assert fragment not in prompt and all(s['narration'] in prompt for s in value['scenes'])
            output = {'editorial_review': critic(stock_positions=tuple(range(6))),
                'factual_audit': {'sentences': [{'position': pos, 'narration': s['narration'],
                    'assessment': 'unsupported' if outcome == 'factual_rejection' and pos == 0 else 'supported',
                    'reason': 'Synthetic independent source assessment after the sentence rewrite.',
                    'quotations': [{'passage_id': passage['passage_id']}]} for pos, s in enumerate(value['scenes'])]}}
        body = envelope(); body['choices'][0]['message']['content'] = json.dumps(output)
        return response(prepared, payload=body)
    monkeypatch.setattr(transport, 'send_once', send)
    monkeypatch.setattr(director, 'run_optional_gemini_critic', lambda *a, **kw: pytest.fail('No paid fallback'))
    def run():
        return director._repair_short_stock_scenes(None, value, 'English', .5, 'Explain the sourced membership model.',
            fresh_scheduled=True, spoken_word_budget=value['spoken_word_budget'])
    if outcome == 'repaired':
        result = run()
        assert result['narration'] == value['narration']
        assert included.story_review_matches(result, 'Explain the sourced membership model.')
        assert run()['narration'] == result['narration']
    else:
        with pytest.raises(RuntimeError) as error:
            run()
        assert error.value.planning_diagnostics['publish_eligible'] is False
    # The last deterministic attempt has identical input when the model keeps
    # the same fragment. Its prior response must be reused, not paid for again.
    expected_calls = 2 if outcome == 'still_fragmented' else 3
    assert len(calls) == expected_calls and value == before and broken == observed
    journal = json.loads(ledger.client.get(included.JOURNAL_KEY))
    assert len(journal['requests']) == expected_calls and all(r['outcome'] is not None for r in journal['requests'].values())
    assert ledger.foundation.snapshot()['cash_spending_enabled'] is False
