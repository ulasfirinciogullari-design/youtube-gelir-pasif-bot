from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import director, research


NEXT_TOPICS = [
    'Kasadaki ilk barkod bir sakız paketiydi: 26 Haziran 1974, Troy. Modern kasa görüntüsünü arşiv diye sunma.',
    'Eurodaki köprü hangi şehirde? Tasarımlar belirli gerçek köprülerin resmi değil.',
    'Türkiye bir gecede altı sıfırı nasıl sildi? Bir milyon eski TL, bir YTL oldu; bu tek başına zenginleşme değildi.',
    'Bir sent neden kendinden pahalı? Üretim maliyeti ve paranın değerini karşılaştır; üretim ve dolaşımı karıştırma.',
    'Banka kapalıyken para çekmek: ilk ATM, Enfield, 27 Haziran 1967. Günümüz ATM görüntüsünü arşiv diye sunma.',
    'Market arabası fikri: Sylvan Goldman ve tekerlekli sepet. Güncel market görüntüsüyle anlat.',
    'Doların üzerindeki yıl basıldığı yıl mı? Seri yılı tasarım onayı veya imza değişimini gösterir.',
]


@pytest.fixture
def capture(monkeypatch):
    payload = {
        'title': 'Kaynaklı kısa belgesel', 'thumbnail_text': 'Bir ayrıntı',
        'description': 'Bu açıklama ilk taslaktan korunur.',
        'scenes': [
            {
                'narration': f'Bu örnekte anlatılan kaynaklı ayrıntı, sahne {index} ile bir sonraki açıklamaya bağlanır.',
                'visual_queries': ['hands examining banknote', 'banknote close view'],
                'ai_prompt': None,
            }
            for index in range(6)
        ],
        'sources': [
            {'url': 'https://www.ecb.europa.eu/euro/banknotes/current/design/html/index.en.html',
             'evidence': 'The source evidence directly supports the factual answer in this mocked draft.'},
            {'url': 'https://www.bep.gov/currency',
             'evidence': 'A second source evidence record supports the factual answer in this mocked draft.'},
        ],
    }
    generate = Mock(side_effect=lambda *_args, **_kwargs: deepcopy(payload))
    monkeypatch.setattr(research, 'settings', SimpleNamespace(
        studio_plan_provider='gemini', gemini_api_key='mock-only',
        gemini_model='mock-only', openai_api_key='',
    ))
    monkeypatch.setattr(research, 'generate_gemini_json', generate)
    monkeypatch.setattr(research, 'OpenAI', Mock(side_effect=AssertionError('No OpenAI/network call')))

    def run(topic=NEXT_TOPICS[1], *, style='documentary', mode='production', duration=0.5, fresh=False):
        options = {
            'content_style': style, 'mode': mode, 'format': 'shorts',
            'pace': 'balanced', 'visual_mix': 'real_first', 'quality_threshold': 86,
        }
        before = deepcopy(options)
        result = research.research_and_script(topic, duration, 'tr', options, fresh_scheduled=fresh)
        assert options == before
        generate.assert_called_once()
        return generate.call_args.args[0], generate.call_args.kwargs, result

    return run


@pytest.mark.parametrize('fresh', [True, False, None, 1, 'true'])
@pytest.mark.parametrize('style', ['documentary', 'technology'])
def test_fresh_documentary_research_plans_moving_video_not_unavailable_archival_photos(capture, fresh, style):
    prompt, kwargs, _result = capture(NEXT_TOPICS[0], fresh=fresh, style=style)
    marker = 'FRESH DOCUMENTARY STOCK-VIDEO CONTRACT'
    enabled = fresh is True and style == 'documentary'
    assert (marker in prompt) is enabled
    assert kwargs['google_search'] is True
    if enabled:
        assert director._fresh_documentary_stock_video_rule(style, True) in prompt
        assert 'all explicit user actions, identities and historical constraints' in prompt
        assert 'not archival photographs' in prompt
        assert 'modern footage is real archive' in prompt


@pytest.mark.parametrize('topic', NEXT_TOPICS)
def test_next_factual_briefs_receive_the_existing_director_contract(capture, topic):
    prompt, kwargs, result = capture(topic)
    assert f'Topic: {topic}\n' in prompt
    assert director._documentary_broll_writer_rule('documentary') in prompt
    assert director._documentary_explanatory_coda_rule('documentary') in prompt
    assert director._HUMAN_CURIOSITY_RULE in prompt
    assert 'the beat after the hook must begin answering the established curiosity, not ask the same question again' in prompt
    assert 'Unless Topic explicitly requires otherwise' in prompt
    assert 'Do not invent claims or statistics.' in prompt
    assert 'pages actually used, never a bare URL list' in prompt
    assert 'directly supports the story\'s central sourced answer' in prompt
    assert 'directly support the central sourced answer; omit interesting but unused sources' in prompt
    assert kwargs['google_search'] is True
    assert kwargs['thinking_level'] == 'low'
    assert result['description'] == 'Bu açıklama ilk taslaktan korunur.'


@pytest.mark.parametrize('mode', ['production', 'preview'])
def test_documentary_qualifies_old_forced_action_requirements(capture, mode):
    prompt, kwargs, result = capture(mode=mode)
    assert '- The final scene must resolve the central curiosity through a visible human action' not in prompt
    assert 'Outside the active sourced documentary explanatory coda: The final scene must resolve' in prompt
    assert '- For a short preview, silently define one sentence that states:' not in prompt
    assert '- A stock-only scene may not summarize' not in prompt
    assert 'honestly illustrate the exact supported subject under the active documentary B-roll contract' in prompt
    assert kwargs['json_schema'] == research._research_json_schema(6)
    assert 'HARD NARRATION BUDGET: 52-60 total spoken words; aim for 56.' in prompt
    assert result['target_scene_count'] == 6
    assert result['target_word_range'] == [52, 60]
    if mode == 'preview':
        assert '- Every scene with ai_prompt set to null must narrate only one literal' not in prompt
        assert '- Make the penultimate action and closing payoff' not in prompt
        assert '- Before returning, audit each ai_prompt-null scene' not in prompt
        assert 'Outside the active sourced documentary explanatory coda: Make the penultimate action' in prompt


@pytest.mark.parametrize('style', ['story', 'technology', 'cinematic', 'explainer'])
@pytest.mark.parametrize('mode', ['preview', 'production'])
def test_non_documentary_physical_contract_remains_unchanged(capture, style, mode):
    prompt, kwargs, _result = capture('Show how a physical latch locks.', style=style, mode=mode)
    assert 'DOCUMENTARY B-ROLL EXCEPTION:' not in prompt
    assert 'SOURCED DOCUMENTARY EXPLANATORY CODA:' not in prompt
    assert 'Outside the active sourced documentary explanatory coda:' not in prompt
    assert '- The final scene must resolve the central curiosity through a visible human action' in prompt
    assert '- A stock-only scene may not summarize several earlier mechanisms or invisible abstractions; it must describe one subject performing one visible action in one ordinary location.' in prompt
    assert '- Give every scene 2-3 DISTINCT English search phrases that literally visualize the exact narration.' in prompt
    assert 'directly supports the story\'s central causal reveal' in prompt
    assert 'directly support the central causal claim' in prompt
    assert kwargs['json_schema'] == research._research_json_schema(6)
    if mode == 'preview':
        assert '- Make the penultimate action and closing payoff two consecutive visible beats' in prompt
        assert '- Every scene with ai_prompt set to null must narrate only one literal' in prompt


def test_documentary_demonstrations_do_not_gain_a_blanket_broll_exception(capture):
    prompt, _kwargs, _result = capture('Demonstrate a banknote durability test with a visible before and after.')
    assert 'This contract does not apply to a tutorial, procedure, before/after result, physical demonstration' in prompt
    assert 'those retain the strict physical-evidence and continuity rules' in prompt
    assert 'any such causal demonstration still needs its own source support and visible evidence' in prompt
    assert 'never covers an unsupported claim' in prompt
    assert 'Modern establishing footage may illustrate a still-existing subject, but it must not masquerade as archive footage' in prompt
    assert 'Explicit user shot/identity constraints always remain binding' in prompt


def test_explicit_scene_count_and_user_contract_still_override_generic_story_shaping(capture):
    topic = 'Return exactly 6 scenes. Preserve this physical before/after demonstration and its exact subject.'
    prompt, kwargs, result = capture(topic)
    assert 'USER-BRIEF HARD CONSTRAINT: Create EXACTLY 6 scenes' in prompt
    assert 'Topic is the authoritative production contract.' in prompt
    assert kwargs['json_schema']['properties']['scenes']['minItems'] == 6
    assert kwargs['json_schema']['properties']['scenes']['maxItems'] == 6
    assert result['target_scene_count'] == 6
