"""New system: hook, ending and title guidance plus audience and word-budget alignment for Shorts."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import audience_strategy, director, research

CHANNEL = 'UC' + 'b' * 22
SCHEDULED = {'mode': 'production', 'format': 'shorts', 'production_scheduled': True,
             'production_channel_id': CHANNEL, 'content_style': 'documentary', 'pace': 'balanced'}
UNSCHEDULED = {key: value for key, value in SCHEDULED.items() if key != 'production_scheduled'}


@pytest.fixture
def growth(monkeypatch):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(studio_shorts_growth_rule=True))
    return director


@pytest.mark.parametrize('options,duration,fresh,enabled', [
    (SCHEDULED, 0.5, True, True), (SCHEDULED, 0.5, False, False), (UNSCHEDULED, 0.5, True, False),
    (SCHEDULED, 3, True, False),
])
def test_growth_rule_only_for_fresh_scheduled_shorts(growth, options, duration, fresh, enabled):
    assert (growth.shorts_growth_rule(options, duration, fresh) == director._SHORTS_GROWTH_RULE) is enabled
    if not enabled:
        assert growth.shorts_growth_rule(options, duration, fresh) == ''


@pytest.mark.parametrize('value', [False, 'true', 1, None])
def test_growth_rule_needs_the_literal_setting(monkeypatch, value):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(studio_shorts_growth_rule=value))
    assert director.shorts_growth_rule(SCHEDULED, 0.5, True) == ''


def test_growth_rule_keeps_truth_and_drops_the_spoken_subscribe_line():
    rule = director._SHORTS_GROWTH_RULE
    assert 'source-supported' in rule and 'never exaggerated beyond the evidence' in rule
    assert 'no spoken closing question, subscribe invitation or sign-off' in rule
    assert 'at most 55 characters' in rule and 'not new acceptance gates' in rule


@pytest.fixture
def research_run(monkeypatch):
    payload = {
        'title': 'Kaynaklı kısa belgesel', 'thumbnail_text': 'Bir ayrıntı', 'description': 'Açıklama.',
        'scenes': [{'narration': f'Sahne {index} kaynaklı ayrıntıyı anlatır.',
                    'visual_queries': ['hands examining banknote', 'banknote close view'], 'ai_prompt': None}
                   for index in range(6)],
        'sources': [
            {'url': 'https://www.ecb.europa.eu/euro/banknotes/current/design/html/index.en.html',
             'evidence': 'The source evidence directly supports the factual answer in this mocked draft.'},
            {'url': 'https://www.bep.gov/currency',
             'evidence': 'A second source evidence record supports the factual answer in this mocked draft.'},
        ],
    }
    generate = Mock(side_effect=lambda *_args, **_kwargs: deepcopy(payload))
    values = SimpleNamespace(studio_plan_provider='gemini', gemini_api_key='mock-only', gemini_model='mock-only',
                             openai_api_key='', studio_research_audience_rule=False,
                             studio_research_turkish_short_budget=False)
    monkeypatch.setattr(research, 'settings', values)
    monkeypatch.setattr(director, 'settings', SimpleNamespace(studio_shorts_growth_rule=False))
    monkeypatch.setattr(research, 'generate_gemini_json', generate)
    monkeypatch.setattr(research, 'OpenAI', Mock(side_effect=AssertionError('No OpenAI/network call')))
    monkeypatch.setattr(audience_strategy, 'writer_rule', lambda channel, minutes: f'AUDIENCE RULE {channel} {minutes}')

    def run(options=SCHEDULED, *, fresh=True, language='tr'):
        result = research.research_and_script('Bir sent neden kendinden pahalı?', 0.5, language, dict(options),
                                              fresh_scheduled=fresh)
        return generate.call_args.args[0], result
    run.settings = values
    return run


def test_research_prompt_is_unchanged_with_every_switch_off(research_run):
    prompt, result = research_run()
    assert director._SHORTS_GROWTH_RULE not in prompt and 'AUDIENCE RULE' not in prompt
    assert 'HARD NARRATION BUDGET: 52-60 total spoken words; aim for 56.' in prompt
    assert result['target_word_range'] == [52, 60]


def test_research_prompt_appends_growth_and_audience_rules(research_run, monkeypatch):
    base, _ = research_run()
    monkeypatch.setattr(director, 'settings', SimpleNamespace(studio_shorts_growth_rule=True))
    research_run.settings.studio_research_audience_rule = True
    prompt, _ = research_run()
    assert prompt == base + director._SHORTS_GROWTH_RULE + '\n' + f'AUDIENCE RULE {CHANNEL} 0.5\n'


def test_audience_rule_reaches_unscheduled_production_shorts_but_not_growth(research_run, monkeypatch):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(studio_shorts_growth_rule=True))
    research_run.settings.studio_research_audience_rule = True
    prompt, _ = research_run(UNSCHEDULED)
    assert 'AUDIENCE RULE' in prompt and director._SHORTS_GROWTH_RULE not in prompt
    prompt, _ = research_run({**UNSCHEDULED, 'mode': 'preview'})
    assert 'AUDIENCE RULE' not in prompt


def test_turkish_short_research_writes_to_the_director_word_range(research_run):
    research_run.settings.studio_research_turkish_short_budget = True
    prompt, result = research_run()
    assert 'HARD NARRATION BUDGET: 48-54 total spoken words; aim for 51.' in prompt
    assert result['target_word_range'] == [48, 54]
    assert director._target_word_budget(0.5, calibrated_short_words=51) == (51, 48, 54)


@pytest.mark.parametrize('options,language', [
    (SCHEDULED, 'en'), ({**SCHEDULED, 'mode': 'preview'}, 'tr'), ({**SCHEDULED, 'format': 'long'}, 'tr'),
])
def test_other_research_keeps_its_word_range(research_run, options, language):
    research_run.settings.studio_research_turkish_short_budget = True
    _prompt, result = research_run(options, language=language)
    assert result['target_word_range'] != [48, 54]


@pytest.mark.parametrize('enabled', [True, False])
def test_director_prompt_carries_the_growth_rule(monkeypatch, enabled):
    monkeypatch.setattr(director, 'settings', SimpleNamespace(
        studio_plan_provider='gemini', studio_abacus_editorial_enabled=False, studio_shorts_growth_rule=enabled,
        gemini_api_key='FAKE-GEMINI-KEY', gemini_model='mock-only'))
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'gemini')
    gemini = Mock(return_value={'scenes': []})
    monkeypatch.setattr(director, 'generate_gemini_json', gemini)
    monkeypatch.setattr(director, 'paid_response', Mock(side_effect=AssertionError('No OpenAI call')))
    director._run_director(Mock(), {}, 'Explain one source-backed story.', 'English', 0.5, 65, 62, 66, 6,
                           dict(SCHEDULED), fresh_scheduled=True)
    prompt = gemini.call_args.args[0]
    assert prompt.endswith(director._SHORTS_GROWTH_RULE + '\n') is enabled
    assert ('- qc_summary is a short list of the main editorial repairs.\n'
            + (director._SHORTS_GROWTH_RULE + '\n' if enabled else '')) in prompt


def test_new_system_turns_the_growth_prompts_on():
    from app.config import Settings
    fields = Settings.model_fields
    for name in ('studio_shorts_growth_rule', 'studio_research_audience_rule',
                 'studio_research_turkish_short_budget'):
        assert fields[name].default is True
