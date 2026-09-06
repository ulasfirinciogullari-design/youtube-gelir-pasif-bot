"""Natural proper-name phrasing is authoring guidance, never an audio waiver."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

# Load the real exception types before the legacy director fixture's optional
# minimal stub; provider calls below still use only explicit local mocks.
import httpx
import pytest

from test_director import FakeClient, _scene, critic_payload
from test_research import valid_research_payload
from app.services import director, research


MARKER = 'TURKISH PROPER-NAME DELIVERY'


@pytest.fixture(autouse=True)
def local_models_only(monkeypatch):
    settings = SimpleNamespace(
        studio_plan_provider='openai', openai_api_key='mock-only', openai_model='mock-only',
        gemini_api_key='', gemini_model='mock-only', gemini_critic_enabled=False,
    )
    monkeypatch.setattr(director, 'settings', settings)
    monkeypatch.setattr(research, 'settings', settings)
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director, '_studio_plan_openai_model', lambda: 'mock-only')


def _package():
    narrations = [
        'Troy kentindeki Marsh mağazasında küçük bir paket kasaya geldi.',
        'Wrigley’s Juicy Fruit adlı sakızın üzerindeki çizgiler okuyucudan geçti.',
        'Kasadaki görevli bu paketin fiyatını artık ekranda hemen görebildi.',
        'Böylece alışverişte aynı ürünün bilgileri yeniden yazılmadan kasaya aktarıldı.',
        'Küçük çizgiler mağazadaki işlemin hızını değiştiren büyük fikri anlattı.',
    ]
    scenes = [_scene(index, text, [
        'cashier scanning gum package at checkout',
        'gum package beside checkout barcode scanner',
    ]) for index, text in enumerate(narrations)]
    return {
        'title': 'Fixture barcode story', 'description': 'Fixture facts, no provider requests.',
        'thumbnail_text': '', 'scenes': scenes, 'narration': ' '.join(narrations),
        'tts_narration': ' '.join(narrations), 'ai_scenes': [],
        'sources': [{'url': 'https://example.org/source', 'evidence': 'Fixture identity evidence.'}],
    }


def _generated(value):
    return {'scenes': [{
        'position': index, 'narration': scene['narration'],
        'visual_queries': scene['visual_queries'], 'ai_prompt': None,
    } for index, scene in enumerate(value['scenes'])]}


def test_shared_rule_preserves_names_locks_and_real_audio_evidence():
    rule = director._proper_name_spoken_guidance('Turkish')
    for text in (
        'newly authored narration', 'suitable Turkish category noun',
        'Preserve the exact identity and spelling', 'explicitly required by the brief',
        'no required name, attribution or meaningful factual distinction is lost',
        'Never invent phonetic spellings', 'alter an exact spoken-text lock',
        'not a ban on foreign names or correctly written Turkish apostrophe suffixes',
        'neither alone makes natural_spoken_language false',
        'Text review cannot prove audible pronunciation',
        'actual transcript and prosody QA remain required',
    ):
        assert text in rule
    assert 'Troy' not in rule and 'Wrigley' not in rule
    assert director._proper_name_spoken_guidance('English') == ''


@pytest.mark.parametrize('correction', [False, True])
@pytest.mark.parametrize('language', ['Turkish', 'English'])
def test_director_initial_and_correction_receive_only_language_scoped_guidance(language, correction):
    client = FakeClient([{'scenes': []}])
    director._run_director(
        client, {}, 'Preserve the required proper names.', language, .5,
        45, 42, 48, 5, {'mode': 'production', 'format': 'shorts', 'visual_mix': 'real_first'},
        correction=correction,
    )
    assert (MARKER in client.responses.calls[0]['input']) is (language == 'Turkish')


@pytest.mark.parametrize('language,duration,expected', [('tr', .5, True), ('en', .5, False), ('tr', 2.0, False)])
def test_initial_research_uses_same_short_turkish_guidance(monkeypatch, language, duration, expected):
    research.settings.studio_plan_provider = 'gemini'
    research.settings.gemini_api_key = 'mock-only'
    generation = Mock(return_value=valid_research_payload())
    monkeypatch.setattr(research, 'generate_gemini_json', generation)
    research.research_and_script('Return exactly 3 scenes about one process.', duration, language,
                                 {'mode': 'production', 'format': 'shorts'})
    prompt = generation.call_args.args[0]
    assert (MARKER in prompt) is expected
    if expected:
        assert director._proper_name_spoken_guidance('Turkish') in prompt


@pytest.mark.parametrize('lock', [None, 'exact', 'saved', 'compression'])
def test_writer_and_independent_critic_share_guidance_without_changing_locked_contract(lock):
    value = _package()
    before = deepcopy(value)
    topic = 'The spoken text must retain Troy, Marsh and Wrigley’s Juicy Fruit.'
    kwargs = {}
    if lock == 'exact':
        topic += ' Anlatım metni aynen şu olsun: “' + value['narration'] + '”'
    elif lock == 'saved':
        kwargs['immutable_candidate_narrations'] = [s['narration'] for s in value['scenes']]
    elif lock == 'compression':
        kwargs['immutable_original_shot_prompts'] = {}
    verdict = critic_payload(stock_positions=range(5), scene_count=5)
    client = FakeClient(([_generated(value)] if lock != 'compression' else []) + [verdict])
    result = director._repair_short_stock_scenes(client, value, 'Turkish', .5, topic, **kwargs)
    assert len(client.responses.calls) == (1 if lock == 'compression' else 2)
    for call in client.responses.calls:
        assert (MARKER in call['input']) is (lock is None)
        assert 'Troy' in call['input'] and 'Wrigley’s Juicy Fruit' in call['input']
    assert value == before
    assert result['narration'] == before['narration']
    assert [s['tts_text'] for s in result['scenes']] == [s['narration'] for s in before['scenes']]
    assert result['stock_scene_qc']['story_review']['accepted'] is True


def test_new_guidance_does_not_turn_an_independent_language_failure_into_a_pass():
    value = _package()
    verdict = critic_payload(stock_positions=range(5), scene_count=5)
    verdict['story_review']['natural_spoken_language'] = False
    verdict['story_review']['natural_spoken_language_evidence'] = (
        'scene 1: "Wrigley’s Juicy Fruit adlı sakızın" forms an awkward phrase in this fixture.'
    )
    verdict['story_review']['reason'] = 'natural_spoken_language failed: scene 1 uses the quoted awkward phrase.'
    client = FakeClient([_generated(value), verdict])
    with pytest.raises(RuntimeError):
        director._repair_short_stock_scenes(
            client, value, 'Turkish', .5, 'Preserve every required proper name.',
            allow_natural_language_repair=False,
        )
    assert len(client.responses.calls) == 2
