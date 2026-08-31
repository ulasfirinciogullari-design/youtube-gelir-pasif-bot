import copy
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = object
sys.modules.setdefault('openai', openai_stub)

httpx_stub = types.ModuleType('httpx')
httpx_stub.Timeout = lambda *args, **kwargs: object()
httpx_stub.post = lambda *args, **kwargs: None
sys.modules.setdefault('httpx', httpx_stub)

config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(
    openai_api_key='test-openai-key',
    openai_model='test-model',
    gemini_critic_enabled=False,
    gemini_api_key='',
    gemini_model='gemini-3.1-pro-preview',
)
sys.modules['app.config'] = config_stub

from app.services.director import (
    _NaturalSpokenLanguageRepairRequired,
    _repair_short_stock_scenes,
    _short_spoken_quality_issues,
    _short_story_fingerprint,
    _short_story_quality_issues,
    _word_count,
    direct_and_qc,
    short_story_package_is_approved,
)


CRITIC_BOOLEAN_KEYS = {
    'single_sentence',
    'single_visible_action',
    'single_ordinary_location',
    'all_spoken_meaning_visible',
    'no_invisible_or_abstract_claim',
    'all_named_subjects_coexist',
    'queries_are_english',
    'queries_match_same_action',
    'common_stock_clip_feasible',
    'continues_from_previous',
    'leads_to_next',
    'preserves_story_role',
    'adds_no_new_fact',
}


class SequencedResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outputs:
            raise AssertionError('unexpected OpenAI call')
        value = self.outputs.pop(0)
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        return SimpleNamespace(output_text=value)


class FakeClient:
    def __init__(self, outputs):
        self.responses = SequencedResponses(outputs)


class FakeGeminiResponse:
    def __init__(self, verdict=None, *, status_code=200, raw_text=None):
        self.status_code = status_code
        self.verdict = verdict
        self.raw_text = raw_text

    def json(self):
        output_text = (
            self.raw_text
            if self.raw_text is not None
            else json.dumps(self.verdict, ensure_ascii=False)
        )
        return {
            'candidates': [{
                'finishReason': 'STOP',
                'content': {'parts': [{'text': output_text}]},
            }],
        }


def _scene(index, narration, queries, ai_prompt=None):
    return {
        'index': index,
        'narration': narration,
        'tts_text': narration,
        'visual_queries': queries,
        'ai_prompt': ai_prompt,
        'overlay_text': None,
        'pace': 'normal',
        'transition': 'cut',
    }


def make_short_package():
    scenes = [
        _scene(
            0,
            'Elim telefona gider ve sihir gibi çalışır bugün.',
            ['hand reaching smartphone at home', 'person unlocking phone indoors'],
        ),
        _scene(
            1,
            'Yakından OLED ekranındaki siyah pikseller tamamen karanlık kalır.',
            ['macro OLED pixels black area', 'OLED subpixel matrix close up'],
            'macro OLED evidence',
        ),
        _scene(
            2,
            'Metroda telefon çevredeki ağlarla konumunu sessizce yeniden bulur.',
            ['person using navigation in metro', 'phone location inside subway'],
            'indoor location assistance',
        ),
        _scene(
            3,
            'Yıpranmış QR kodu eksik karelere rağmen hızla çözülür.',
            ['damaged QR code being scanned', 'worn QR close up'],
            'damaged QR recovery',
        ),
        _scene(
            4,
            'Kasada telefonumu okuyucuya tutup ödemeyi sessizce tamamlarım bugün.',
            ['customer paying by phone checkout', 'phone held over payment reader'],
        ),
        _scene(
            5,
            'Sonra mağazadan çıkar kahvemi alıp telefonumu cebime koyarım.',
            ['person exiting shop with coffee', 'person putting phone in pocket'],
        ),
    ]
    narration = ' '.join(scene['narration'] for scene in scenes)
    assert _word_count(narration) == 48
    return {
        'title': 'Telefonun Sessiz Günü',
        'description': 'test',
        'thumbnail_text': 'test',
        'sources': [
            {
                'url': 'https://example.com/phone-evidence',
                'evidence': 'A concrete source sentence supports the selected phone mechanism.',
            },
            {
                'url': 'https://example.org/second-phone-source',
                'evidence': 'A second independent sentence confirms the same causal mechanism.',
            },
        ],
        'scenes': scenes,
        'narration': narration,
        'tts_narration': narration,
        'visual_queries': [
            query
            for scene in scenes
            for query in scene['visual_queries']
        ],
        'ai_scenes': [
            scene['ai_prompt']
            for scene in scenes
            if scene.get('ai_prompt')
        ],
        'director_qc': [],
    }


def make_coherent_battery_package():
    scenes = [
        _scene(
            0,
            'Otobüs bekleyen genç adam telefonunun düşen piline bakar.',
            ['young man checks phone at bus stop', 'commuter checks low phone battery'],
        ),
        _scene(
            1,
            'Soğukta pil elektrik vermekte kısa süre daha zorlanır.',
            ['cold smartphone in commuters hand', 'person holds phone in winter'],
        ),
        _scene(
            2,
            'Gerilim düşünce telefon kalan gücü olduğundan az hesaplar.',
            ['battery voltage drop scientific visualization', 'cold battery voltage animation'],
            'cinematic macro battery voltage visualization without text',
        ),
        _scene(
            3,
            'Genç adam telefonu otobüs durağında iç cebine koyar.',
            ['man puts phone in inner coat pocket', 'commuter pockets phone at bus stop'],
        ),
        _scene(
            4,
            'Telefon aynı otobüs durağında iç cebinde yavaşça ısınır.',
            ['phone warming inside coat pocket', 'commuter waits with phone in coat'],
        ),
        _scene(
            5,
            'Genç aynı otobüs durağında açılan telefon ekranına bakar.',
            ['young man uses phone at bus stop', 'commuter checks working phone outside'],
        ),
    ]
    narration = ' '.join(scene['narration'] for scene in scenes)
    assert 45 <= _word_count(narration) <= 51
    return {
        'title': 'Soğukta Düşen Pil',
        'description': 'Tek bir gündelik pil sorusunu anlatır.',
        'thumbnail_text': 'PİL NEDEN DÜŞÜYOR?',
        'sources': [
            {
                'url': 'https://example.com/cold-battery-evidence',
                'evidence': 'Cold conditions can temporarily reduce battery performance.',
            },
            {
                'url': 'https://example.org/voltage-evidence',
                'evidence': 'Lower available voltage can affect the displayed charge estimate.',
            },
        ],
        'scenes': scenes,
        'narration': narration,
        'tts_narration': narration,
        'visual_queries': [
            query
            for scene in scenes
            for query in scene['visual_queries']
        ],
        'ai_scenes': [
            scene['ai_prompt']
            for scene in scenes
            if scene.get('ai_prompt')
        ],
        'director_qc': [],
    }


def make_ai_first_five_scene_package():
    scenes = [
        _scene(
            0,
            'Elif soğuk otobüs durağında kapanan telefon ekranına bakar.',
            ['woman looks at phone at bus stop', 'woman checks phone at winter bus stop'],
        ),
        _scene(
            1,
            'Soğuk pilin iç direncini artırır, yük altında gerilim düşer ve telefon kapanır.',
            ['cold battery internal resistance macro', 'smartphone battery voltage sag visualization'],
            'cinematic macro of a cold smartphone battery voltage sag without text',
        ),
        _scene(
            2,
            'Elif telefonu soğuk otobüs durağında montunun içine koyar.',
            ['woman puts phone inside coat at bus stop', 'commuter pockets phone at winter bus stop'],
        ),
        _scene(
            3,
            'Elif soğuk otobüs durağı bankında telefonunu cebinden yavaşça çıkarır.',
            ['woman removes phone at bus stop bench', 'commuter takes phone from coat by bench'],
            'same woman slowly removes her phone beside the same winter bus stop bench',
        ),
        _scene(
            4,
            'Aynı otobüs durağı bankında telefonun ekranı yeniden açılır.',
            ['phone screen lights at bus stop bench', 'woman sees phone turn on by bench'],
            'close shot at the same bus stop bench as the phone screen lights without readable text',
        ),
    ]
    narration = ' '.join(scene['narration'] for scene in scenes)
    assert _word_count(narration) == 45
    return {
        'title': 'Soğukta Kapanan Telefon',
        'description': 'Tek bir gündelik pil sorusunu görünür bir sonuca bağlar.',
        'thumbnail_text': 'SOĞUKTA NEDEN KAPANIR?',
        'sources': [
            {
                'url': 'https://example.com/cold-battery-evidence',
                'evidence': 'Cold can raise internal resistance and reduce loaded voltage.',
            },
            {
                'url': 'https://example.org/voltage-evidence',
                'evidence': 'A loaded voltage drop can make a device shut down.',
            },
        ],
        'scenes': scenes,
        'narration': narration,
        'tts_narration': narration,
        'visual_queries': [
            query
            for scene in scenes
            for query in scene['visual_queries']
        ],
        'ai_scenes': [
            scene['ai_prompt']
            for scene in scenes
            if scene.get('ai_prompt')
        ],
        'director_qc': [],
    }


def valid_generator_payload(positions=(0, 4, 5), final_variant=False):
    rows = {
        0: {
            'position': 0,
            'narration': 'Genç adam evde masadaki telefonunu tek eliyle alır.',
            'visual_queries': [
                'young man picks up smartphone at home',
                'man picks up phone from home table',
            ],
            'ai_prompt': None,
        },
        4: {
            'position': 4,
            'narration': 'Kasiyer kafe tezgâhında müşteriye sıcak kahve fincanını uzatır.',
            'visual_queries': [
                'barista hands customer coffee inside cafe',
                'cafe cashier hands hot coffee to customer',
            ],
            'ai_prompt': None,
        },
        5: {
            'position': 5,
            'narration': (
                'Kadın aynı kafe tezgâhında önündeki sıcak kahveye gülümser.'
                if final_variant
                else 'Müşteri aynı kafe tezgâhında uzatılan sıcak kahveye gülümser.'
            ),
            'visual_queries': (
                [
                    'woman smiles at coffee by cafe counter',
                    'happy customer with coffee at cafe counter',
                ]
                if final_variant
                else [
                    'customer smiles at coffee by cafe counter',
                    'happy person with cup at cafe counter',
                ]
            ),
            'ai_prompt': None,
        },
    }
    for row in rows.values():
        assert _word_count(row['narration']) == 8
    return {'scenes': [copy.deepcopy(rows[position]) for position in positions]}


def valid_ai_first_generator_payload():
    return {
        'scenes': [
            {
                'position': 0,
                'narration': (
                    'Elif soğuk otobüs durağında kapanan telefon ekranına bakar.'
                ),
                'visual_queries': [
                    'woman looks at phone at bus stop',
                    'woman checks phone at winter bus stop',
                ],
                'ai_prompt': None,
            },
            {
                'position': 2,
                'narration': (
                    'Elif telefonu soğuk otobüs durağında montunun içine koyar.'
                ),
                'visual_queries': [
                    'woman puts phone inside coat at bus stop',
                    'commuter pockets phone at winter bus stop',
                ],
                'ai_prompt': None,
            },
        ],
    }


def critic_payload(
    failures=None,
    story_failures=None,
    ending_failures=None,
    *,
    stock_positions=(0, 4, 5),
    scene_count=6,
):
    failures = failures or {}
    story_failures = story_failures or []
    ending_failures = ending_failures or []
    story_boolean_keys = {
        'single_human_situation',
        'single_central_question',
        'not_fact_montage',
        'causal_scene_chain',
        'same_actor_or_object_thread',
        'human_payoff_visible',
        'natural_spoken_language',
        'directly_answers_requested_topic',
        'one_specific_useful_reveal',
        'causal_claim_supported',
        'hook_payoff_same_promise',
    }
    ending_boolean_keys = {
        'same_immediate_location',
        'continuous_visible_action_chain',
        'same_actor_or_object_thread',
        'everyday_benefit_visible',
    }
    story_review = {
        **{key: True for key in story_boolean_keys},
        'central_question': 'Why does the familiar action work?',
        'causal_answer': 'One supported mechanism makes the action reliable.',
        'visible_payoff': 'The same person visibly completes the useful action.',
        'natural_spoken_language_evidence': (
            'PASS: every sentence is idiomatic and breath-friendly.'
        ),
        'reason': 'One human situation follows a single causal question to a visible payoff.',
    }
    for key in story_failures:
        story_review[key] = False
    if story_failures:
        story_review['reason'] = 'The draft samples unrelated mechanisms instead of one story.'
    if 'natural_spoken_language' in story_failures:
        story_review['natural_spoken_language_evidence'] = (
            'scene 2: "voltaj sarkması gerçekleşir" is textbook-like '
            'rather than conversational Turkish.'
        )
        if story_failures == ['natural_spoken_language']:
            story_review['reason'] = (
                'natural_spoken_language failed at the quoted scene wording.'
            )
    ending_pair = {
        'penultimate_position': scene_count - 2,
        'final_position': scene_count - 1,
        **{key: True for key in ending_boolean_keys},
        'location_anchor': 'same cafe counter',
        'reason': 'The same customer receives and enjoys coffee at the same counter.',
    }
    for key in ending_failures:
        ending_pair[key] = False
    if ending_failures:
        ending_pair['reason'] = 'The final beat jumps away from the preceding location.'
    rows = []
    for position in stock_positions:
        row = {
            'position': position,
            **{key: True for key in CRITIC_BOOLEAN_KEYS},
            'reason': 'One common clip shows the exact actor, action, setting and story role.',
        }
        for key in failures.get(position, []):
            row[key] = False
        if failures.get(position):
            row['reason'] = 'The exact narration or queries do not satisfy this stock contract.'
        rows.append(row)
    return {
        'story_review': story_review,
        'ending_pair': ending_pair,
        'scenes': rows,
    }


class ShortStockRepairTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.gemini_critic_enabled = False
        config_stub.settings.gemini_api_key = ''
        config_stub.settings.gemini_model = 'gemini-3.1-pro-preview'

    def test_repairs_all_null_and_final_positions_only(self):
        package = make_short_package()
        original = copy.deepcopy(package)
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(client.responses.calls[1]['max_tool_calls'], 1)
        self.assertEqual(result['stock_scene_qc']['target_positions'], [0, 4, 5])
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 1)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 1)
        self.assertEqual(_word_count(result['narration']), 48)
        for position in (1, 2, 3):
            self.assertEqual(result['scenes'][position], original['scenes'][position])
        for position in (0, 4, 5):
            self.assertIsNone(result['scenes'][position]['ai_prompt'])
        self.assertEqual(result['ai_scene_count'] if 'ai_scene_count' in result else 3, 3)
        self.assertEqual(len(result['ai_scenes']), 3)

    def test_ai_first_preserves_authored_ai_ending_and_repairs_only_stock(self):
        package = make_ai_first_five_scene_package()
        original = copy.deepcopy(package)
        client = FakeClient([
            valid_ai_first_generator_payload(),
            critic_payload(stock_positions=(0, 2), scene_count=5),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
        )

        self.assertEqual(
            result['stock_scene_qc']['target_positions'],
            [0, 2],
        )
        self.assertEqual(
            result['stock_scene_qc']['ending_pair_review']['positions'],
            [3, 4],
        )
        for position in (1, 3, 4):
            self.assertEqual(result['scenes'][position], original['scenes'][position])
            self.assertTrue(result['scenes'][position]['ai_prompt'])
        self.assertEqual(len(result['ai_scenes']), 3)
        self.assertEqual(_word_count(result['narration']), 45)

    def test_ai_first_ending_rejection_fails_closed_without_stock_rewrite(self):
        client = FakeClient([
            valid_ai_first_generator_payload(),
            critic_payload(
                ending_failures=['same_immediate_location'],
                stock_positions=(0, 2),
                scene_count=5,
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'AI-routed short-preview ending before paid media',
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_ai_first_five_scene_package(),
                'Turkish',
                0.5,
            )

        self.assertNotIsInstance(error.exception, KeyError)
        self.assertIn('"positions":[3,4]', str(error.exception))
        self.assertEqual(len(client.responses.calls), 2)

    def test_relaxed_scene_word_counts_keep_hard_total_duration_range(self):
        generated = valid_generator_payload()
        generated['scenes'][0]['narration'] = (
            'Genç adam evde telefonunu masadan alır.'
        )
        generated['scenes'][1]['narration'] = (
            'Kasiyer kafe tezgâhında bekleyen müşteriye sıcak kahve '
            'fincanını uzatır.'
        )
        generated['scenes'][2]['narration'] = (
            'Müşteri aynı kafe tezgâhında kahvesine gülümser.'
        )
        client = FakeClient([generated, critic_payload()])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertEqual(
            [_word_count(result['scenes'][position]['narration']) for position in (0, 4, 5)],
            [6, 9, 6],
        )
        self.assertEqual(_word_count(result['narration']), 45)
        self.assertEqual(len(client.responses.calls), 2)

    def test_relaxed_scene_counts_still_reject_out_of_range_total(self):
        generated = valid_generator_payload()
        generated['scenes'][0]['narration'] = (
            'Genç adam evde telefonunu alır.'
        )
        generated['scenes'][1]['narration'] = (
            'Kasiyer müşteriye sıcak kahve uzatır.'
        )
        generated['scenes'][2]['narration'] = (
            'Müşteri kafe tezgâhında kahvesine gülümser.'
        )
        client = FakeClient([copy.deepcopy(generated), copy.deepcopy(generated)])

        with self.assertRaisesRegex(
            RuntimeError,
            'fully stock-safe short-preview scenes',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertTrue(all('tools' not in call for call in client.responses.calls))

    def test_second_attempt_rewrites_only_critic_rejection(self):
        first = valid_generator_payload()
        second = valid_generator_payload((5,), final_variant=True)
        client = FakeClient([
            first,
            critic_payload({5: ['queries_match_same_action', 'preserves_story_role']}),
            second,
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 4)
        second_generator_input = client.responses.calls[2]['input']
        self.assertIn(
            '"previous_narration": "Kasiyer kafe tezgâhında müşteriye sıcak kahve fincanını uzatır."',
            second_generator_input,
        )
        self.assertNotIn(
            '"previous_narration": "Kasada telefonumu okuyucuya tutup ödemeyi sessizce tamamlarım bugün."',
            second_generator_input,
        )
        self.assertEqual(result['scenes'][0]['narration'], first['scenes'][0]['narration'])
        self.assertEqual(result['scenes'][4]['narration'], first['scenes'][1]['narration'])
        self.assertEqual(result['scenes'][5]['narration'], second['scenes'][0]['narration'])
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 2)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 2)

    def test_grounded_same_micro_location_uses_one_shared_narrow_rule(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertIn(
            'kafe tezgâhında',
            result['scenes'][4]['narration'],
        )
        self.assertIn(
            'aynı kafe tezgâhında',
            result['scenes'][5]['narration'],
        )
        for prompt in (
            client.responses.calls[0]['input'],
            client.responses.calls[1]['input'],
        ):
            self.assertIn(
                'only adjacent-continuity deictic exception is literal '
                '"same/aynı"',
                prompt,
            )
            self.assertIn(
                'immediately preceding scene explicitly establishes a '
                'compatible concrete anchor',
                prompt,
            )
            self.assertIn(
                '"same/aynı" is not evidence by itself',
                prompt,
            )
            self.assertIn(
                'When an ending scene has a non-null ai_prompt, that prompt '
                'must also explicitly preserve the same concrete '
                'micro-location anchor, actor or object and visible action',
                prompt,
            )
            self.assertIn(
                'a missing or conflicting AI-prompt anchor is false',
                prompt,
            )
            self.assertIn('"there/orada"', prompt)
            self.assertIn('"this time/bu kez"', prompt)
            self.assertIn('mental states', prompt)

    def test_ungrounded_same_location_and_unrelated_claim_fail_closed(self):
        first = valid_generator_payload()
        first['scenes'][1]['narration'] = (
            'Kasiyer mutfakta müşteriye sıcak kahve fincanını uzatır.'
        )
        first['scenes'][1]['visual_queries'] = [
            'barista hands customer coffee in kitchen',
            'server gives coffee inside kitchen',
        ]
        first['scenes'][2]['narration'] = (
            'Müşteri aynı kafe tezgâhında kahvenin faydasını düşünür.'
        )
        retry = {
            'scenes': copy.deepcopy(first['scenes'][1:]),
        }
        rejected = critic_payload(
            {
                5: [
                    'all_spoken_meaning_visible',
                    'no_invisible_or_abstract_claim',
                ],
            },
            ending_failures=['same_immediate_location'],
        )
        client = FakeClient([
            first,
            rejected,
            retry,
            rejected,
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'fully stock-safe short-preview scenes',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(len(client.responses.calls), 4)
        self.assertIn(
            'ending pair: same_immediate_location',
            client.responses.calls[2]['input'],
        )

    def test_ending_pair_failure_rewrites_both_final_positions(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(ending_failures=['same_immediate_location']),
            valid_generator_payload((4, 5), final_variant=True),
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 4)
        second_generator_input = client.responses.calls[2]['input']
        self.assertIn(
            '"stock_positions_to_rewrite": [{"position": 4',
            second_generator_input,
        )
        self.assertIn('"position": 5', second_generator_input)
        self.assertEqual(
            result['stock_scene_qc']['ending_pair_review']['location_anchor'],
            'same cafe counter',
        )

    def test_whole_story_rejection_stops_before_local_retry(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(
                story_failures=[
                    'not_fact_montage',
                    'directly_answers_requested_topic',
                ],
                ending_failures=['same_immediate_location'],
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'incoherent short-preview story before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                'A specific requested phone topic',
            )
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn(
            '"requested_topic": "A specific requested phone topic"',
            client.responses.calls[1]['input'],
        )

    def test_natural_only_story_rejection_carries_tied_critic_evidence(self):
        verdict = critic_payload(
            story_failures=['natural_spoken_language']
        )
        expected_evidence = verdict['story_review'][
            'natural_spoken_language_evidence'
        ]
        verdict['story_review']['reason'] = (
            'The same person follows one clear causal chain to a visible payoff.'
        )
        client = FakeClient([
            valid_generator_payload(),
            verdict,
        ])

        with self.assertRaises(
            _NaturalSpokenLanguageRepairRequired
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(error.exception.evidence, expected_evidence)
        self.assertEqual(len(client.responses.calls), 2)

    def test_natural_language_false_with_pass_evidence_fails_closed(self):
        verdict = critic_payload(
            story_failures=['natural_spoken_language']
        )
        verdict['story_review']['natural_spoken_language_evidence'] = (
            'PASS: the narration sounds natural.'
        )
        client = FakeClient([
            valid_generator_payload(),
            verdict,
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'inconsistent_natural_spoken_language_evidence',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(len(client.responses.calls), 2)

    def test_deterministic_failure_retries_only_bad_position(self):
        first = valid_generator_payload()
        first['scenes'][0]['narration'] = (
            'Genç adam evde masadaki telefonunu; tek eliyle alır.'
        )
        client = FakeClient([
            first,
            valid_generator_payload((0,)),
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 3)
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 2)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 1)
        self.assertNotIn(';', result['scenes'][0]['narration'])

    def test_two_critic_rejections_fail_closed_at_four_calls(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload({5: ['continues_from_previous']}),
            valid_generator_payload((5,), final_variant=True),
            critic_payload({5: ['queries_match_same_action']}),
        ])

        with self.assertRaisesRegex(RuntimeError, r'"generator_calls":2'):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )
        self.assertEqual(len(client.responses.calls), 4)

    def test_ending_pair_boolean_position_is_rejected(self):
        invalid = critic_payload()
        invalid['ending_pair']['penultimate_position'] = True
        client = FakeClient([
            valid_generator_payload(),
            invalid,
            valid_generator_payload(),
            invalid,
        ])

        with self.assertRaises(RuntimeError):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )
        self.assertEqual(len(client.responses.calls), 4)

    def test_longer_duration_is_noop_without_calls(self):
        package = make_short_package()
        client = FakeClient([])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.7,
        )

        self.assertIs(result, package)
        self.assertEqual(client.responses.calls, [])

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_disabled_preserves_existing_behavior(self, gemini_post):
        client = FakeClient([valid_generator_payload(), critic_payload()])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        gemini_post.assert_not_called()
        self.assertNotIn('gemini_critic', result['stock_scene_qc'])

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_enabled_accepts_and_binds_attestation(self, gemini_post):
        secret = 'gemini-secret-must-not-leak'
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = secret
        gemini_post.return_value = FakeGeminiResponse(critic_payload())
        client = FakeClient([valid_generator_payload(), critic_payload()])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
            'one useful phone story',
        )

        attestation = result['stock_scene_qc']['gemini_critic']
        self.assertTrue(attestation['accepted'])
        self.assertEqual(attestation['model'], 'gemini-3.1-pro-preview')
        request = gemini_post.call_args
        self.assertNotIn(secret, request.args[0])
        self.assertNotIn(secret, json.dumps(request.kwargs['json']))
        self.assertIs(request.kwargs['json']['store'], False)
        trusted_instruction = request.kwargs['json'][
            'systemInstruction'
        ]['parts'][0]['text']
        self.assertIn(
            'only adjacent-continuity deictic exception is literal '
            '"same/aynı"',
            trusted_instruction,
        )
        self.assertIn(
            'immediately preceding scene explicitly establishes a '
            'compatible concrete anchor',
            trusted_instruction,
        )
        self.assertIn(
            'ending_pair.same_immediate_location may be true only when both '
            'adjacent beats and their queries contain compatible concrete '
            'micro-location anchors',
            trusted_instruction,
        )
        self.assertIn(
            'When an ending scene has a non-null ai_prompt, that prompt must '
            'also explicitly preserve the same concrete micro-location '
            'anchor, actor or object and visible action',
            trusted_instruction,
        )
        self.assertIn(
            'a missing or conflicting AI-prompt anchor is false',
            trusted_instruction,
        )
        self.assertIn('"there/orada"', trusted_instruction)
        self.assertIn('"this time/bu kez"', trusted_instruction)
        self.assertIn('"again/yeniden"', trusted_instruction)
        self.assertEqual(
            request.kwargs['json']['generationConfig']['maxOutputTokens'],
            4096,
        )
        self.assertNotIn(
            'temperature',
            request.kwargs['json']['generationConfig'],
        )
        self.assertEqual(
            request.kwargs['json']['generationConfig']['thinkingConfig'],
            {'thinkingLevel': 'medium'},
        )

        result['short_story_qc'] = {
            'version': 1,
            'requested_topic': 'one useful phone story',
            'story_review_accepted': True,
            'ending_pair_accepted': True,
        }
        result['short_story_qc']['fingerprint'] = _short_story_fingerprint(result)
        self.assertTrue(short_story_package_is_approved(result))

        missing_attestation = copy.deepcopy(result)
        missing_attestation['stock_scene_qc'].pop('gemini_critic')
        missing_attestation['short_story_qc']['fingerprint'] = (
            _short_story_fingerprint(missing_attestation)
        )
        self.assertFalse(short_story_package_is_approved(missing_attestation))

        result['stock_scene_qc']['gemini_critic']['model'] = 'changed-model'
        self.assertFalse(short_story_package_is_approved(result))

    @patch('app.services.gemini_critic.httpx.post')
    def test_stock_ai_ending_exposes_both_concrete_anchors_to_critics(
        self,
        gemini_post,
    ):
        secret = 'gemini-anchor-secret-must-not-leak'
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = secret
        package = make_ai_first_five_scene_package()
        package['scenes'][3]['ai_prompt'] = None
        package['scenes'][4]['narration'] = (
            'Aynı otobüs durağı bankında telefonun parlak ekranı açılır.'
        )
        package['scenes'][4]['tts_text'] = package['scenes'][4]['narration']
        package['scenes'][4]['visual_queries'] = [
            'phone screen lights at bus stop bench',
            'woman sees phone turn on by bench',
        ]
        package['scenes'][4]['ai_prompt'] = (
            'same woman and phone at the same winter bus stop bench as the '
            'screen lights without readable text'
        )
        package['narration'] = ' '.join(
            scene['narration'] for scene in package['scenes']
        )
        package['tts_narration'] = package['narration']
        package['ai_scenes'] = [
            scene['ai_prompt']
            for scene in package['scenes']
            if scene.get('ai_prompt')
        ]

        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 3,
            'narration': (
                'Elif soğuk otobüs durağı bankında telefonunu '
                'cebinden yavaşça çıkarır.'
            ),
            'visual_queries': [
                'woman removes phone at bus stop bench',
                'commuter takes phone from coat by bench',
            ],
            'ai_prompt': None,
        })
        verdict = critic_payload(
            stock_positions=(0, 2, 3),
            scene_count=5,
        )
        verdict['ending_pair']['location_anchor'] = (
            'same winter bus stop bench'
        )
        verdict['ending_pair']['reason'] = (
            'Both adjacent beats and queries name the same bus stop bench.'
        )
        gemini_post.return_value = FakeGeminiResponse(copy.deepcopy(verdict))
        client = FakeClient([
            generated,
            copy.deepcopy(verdict),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            'cold phone at a bus stop',
        )

        expected_stock_ending = {
            'position': 3,
            'route': 'stock',
            'role': 'penultimate',
            'narration': generated['scenes'][2]['narration'],
            'visual_queries': generated['scenes'][2]['visual_queries'],
            'ai_prompt': None,
        }
        expected_ai_ending = {
            'position': 4,
            'route': 'ai',
            'role': None,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': package['scenes'][4]['ai_prompt'],
        }
        openai_critic_input = client.responses.calls[1]['input']
        self.assertIn(
            json.dumps(expected_stock_ending, ensure_ascii=False),
            openai_critic_input,
        )
        self.assertIn(
            json.dumps(expected_ai_ending, ensure_ascii=False),
            openai_critic_input,
        )

        request_body = gemini_post.call_args.kwargs['json']
        gemini_user_payload = json.loads(
            request_body['contents'][0]['parts'][0]['text']
        )
        gemini_story = gemini_user_payload[
            'critic_context'
        ]['candidate_story_in_order']
        self.assertEqual(gemini_story[3], expected_stock_ending)
        self.assertEqual(gemini_story[4], expected_ai_ending)
        self.assertNotIn(secret, json.dumps(request_body))
        self.assertTrue(result['stock_scene_qc']['gemini_critic']['accepted'])

    @patch('app.services.gemini_critic.httpx.post')
    def test_conflicting_ai_prompt_ending_anchor_fails_closed(
        self,
        gemini_post,
    ):
        secret = 'gemini-conflicting-anchor-secret'
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = secret
        package = make_ai_first_five_scene_package()
        package['scenes'][3]['ai_prompt'] = None
        package['scenes'][4]['narration'] = (
            'Aynı sert çalışma masasında telefonun parlak ekranı açılır.'
        )
        package['scenes'][4]['tts_text'] = package['scenes'][4]['narration']
        package['scenes'][4]['visual_queries'] = [
            'phone screen lights on home work desk',
            'woman sees phone turn on at desk',
        ]
        package['scenes'][4]['ai_prompt'] = (
            'same woman and phone outside on a city street as the screen '
            'lights without readable text'
        )
        package['narration'] = ' '.join(
            scene['narration'] for scene in package['scenes']
        )
        package['tts_narration'] = package['narration']
        package['ai_scenes'] = [
            scene['ai_prompt']
            for scene in package['scenes']
            if scene.get('ai_prompt')
        ]

        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 3,
            'narration': (
                'Elif evdeki sert çalışma masasında telefonunu '
                'cebinden yavaşça çıkarır.'
            ),
            'visual_queries': [
                'woman removes phone beside home work desk',
                'woman takes phone from pocket at desk',
            ],
            'ai_prompt': None,
        })
        openai_verdict = critic_payload(
            stock_positions=(0, 2, 3),
            scene_count=5,
        )
        gemini_verdict = critic_payload(
            ending_failures=['same_immediate_location'],
            stock_positions=(0, 2, 3),
            scene_count=5,
        )
        gemini_verdict['ending_pair']['location_anchor'] = (
            'conflicting work desk and city street'
        )
        gemini_verdict['ending_pair']['reason'] = (
            'Narration and queries require the same work desk, but the '
            'AI prompt moves the final beat to a city street.'
        )
        gemini_post.return_value = FakeGeminiResponse(gemini_verdict)
        client = FakeClient([
            generated,
            openai_verdict,
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            r'Gemini critic rejected.*ending_pair\.same_immediate_location',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                'cold phone at a work desk',
            )

        openai_critic_input = client.responses.calls[1]['input']
        self.assertIn(package['scenes'][4]['ai_prompt'], openai_critic_input)
        self.assertIn(
            'a missing or conflicting AI-prompt anchor is false',
            openai_critic_input,
        )
        request_body = gemini_post.call_args.kwargs['json']
        self.assertNotIn(secret, json.dumps(request_body))
        trusted_instruction = request_body[
            'systemInstruction'
        ]['parts'][0]['text']
        self.assertIn(
            'a missing or conflicting AI-prompt anchor is false',
            trusted_instruction,
        )

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_enabled_rejection_vetoes_before_paid_media(self, gemini_post):
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(
            critic_payload(story_failures=['one_specific_useful_reveal'])
        )
        client = FakeClient([valid_generator_payload(), critic_payload()])

        with self.assertRaisesRegex(
            RuntimeError,
            'Gemini critic rejected the story before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(gemini_post.call_count, 1)

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_malformed_json_fails_closed(self, gemini_post):
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(raw_text='not-json')
        client = FakeClient([valid_generator_payload(), critic_payload()])

        with self.assertRaisesRegex(RuntimeError, 'invalid JSON'):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_network_failure_is_bounded_and_never_logs_key(self, gemini_post):
        secret = 'do-not-log-this-gemini-key'
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = secret
        gemini_post.side_effect = OSError(
            f'network failed while handling {secret}'
        )
        client = FakeClient([valid_generator_payload(), critic_payload()])

        with patch('builtins.print') as print_mock, patch(
            'logging.Logger._log'
        ) as log_mock, self.assertRaisesRegex(
            RuntimeError,
            'Gemini critic request failed',
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(gemini_post.call_count, 2)
        self.assertNotIn(secret, str(error.exception))
        print_mock.assert_not_called()
        log_mock.assert_not_called()

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_natural_language_only_failure_gets_one_full_story_repair_and_rereview(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        original = make_coherent_battery_package()
        corrected = make_coherent_battery_package()
        corrected['scenes'][2]['narration'] = (
            'Soğuk pil yüzünden telefon yüzdeyi olduğundan düşük gösterir.'
        )
        corrected['scenes'][2]['tts_text'] = corrected['scenes'][2]['narration']
        corrected['narration'] = ' '.join(
            scene['narration'] for scene in corrected['scenes']
        )
        corrected['tts_narration'] = corrected['narration']

        def director_payload(package):
            return {
                'title': package['title'],
                'thumbnail_text': package['thumbnail_text'],
                'description': package['description'],
                'scenes': copy.deepcopy(package['scenes']),
                'qc_summary': [],
            }

        approved = copy.deepcopy(corrected)
        approved['stock_scene_qc'] = {
            'version': 3,
            'story_review': {'accepted': True},
            'ending_pair_review': {'accepted': True},
        }
        evidence = (
            'scene 2: "gerilim düşünce" sounds textbook-like in this '
            'spoken sentence.'
        )
        run_director.side_effect = [
            director_payload(original),
            director_payload(corrected),
        ]
        repair_stock_scenes.side_effect = [
            _NaturalSpokenLanguageRepairRequired(evidence),
            approved,
        ]
        openai_class.return_value = object()

        result = direct_and_qc(
            original,
            'Soğukta telefon pili neden birden düşer?',
            0.5,
            'tr',
            {'mode': 'preview', 'pace': 'balanced'},
        )

        self.assertEqual(run_director.call_count, 2)
        self.assertEqual(repair_stock_scenes.call_count, 2)
        second_review = repair_stock_scenes.call_args_list[1]
        self.assertFalse(
            second_review.kwargs['allow_natural_language_repair']
        )
        correction_context = run_director.call_args_list[1].args[1]
        self.assertIn(
            'natural_spoken_language=false',
            correction_context['narration_quality_issues'][0],
        )
        self.assertIn(evidence, correction_context['narration_quality_issues'][0])
        self.assertGreaterEqual(result['narration_word_count'], 45)
        self.assertLessEqual(result['narration_word_count'], 51)


class ShortSpokenQualityTests(unittest.TestCase):
    def test_rejects_live_unsafe_turkish_wording_and_fact_montage(self):
        package = {
            'scenes': [
                {'narration': 'OLEDde siyah yerde alt pikseller susar.'},
                {'narration': 'GPS zayıflayınca WiFi ve hücresel zamanlama tamamlar konumu.'},
                {'narration': 'Yıpranmış QR ReedSolomonla okunur yine kolayca.'},
            ],
        }

        spoken = _short_spoken_quality_issues(package, 'Turkish')
        story = _short_story_quality_issues(package, 'Turkish')

        self.assertGreaterEqual(len(spoken), 6)
        self.assertTrue(any('scene 0' in issue for issue in spoken))
        self.assertTrue(any('scene 1' in issue for issue in spoken))
        self.assertTrue(any('scene 2' in issue for issue in spoken))
        self.assertTrue(any('mixes unrelated mechanism families' in issue for issue in story))

    def test_accepts_native_single_mechanism_turkish(self):
        package = {
            'scenes': [
                {'narration': 'Metroda uydu sinyali zayıflayınca harita kısa süre bekler.'},
                {'narration': 'Kablosuz ağ ve baz istasyonları konum hesabına destek olur.'},
                {'narration': 'Harita aynı metro girişinde doğru yönü yeniden gösterir.'},
            ],
        }

        self.assertEqual(
            _short_story_quality_issues(package, 'Turkish'),
            [],
        )

    def test_allows_voice_normalized_standalone_terms_but_rejects_suffixes(self):
        safe = {
            'scenes': [
                {'narration': 'OLED ekran siyah pikselleri tek tek kapatır.'},
                {'narration': 'GPS sinyali açık havada konumu doğrular.'},
                {'narration': 'QR kodu kasada hızla okunur.'},
            ],
        }
        unsafe = {
            'scenes': [
                {'narration': "OLED'de görüntü kararır."},
                {'narration': 'GPSle konum bulunur.'},
                {'narration': "QR'ın kareleri eksiktir."},
                {'narration': "WiFi'den sinyal gelir."},
                {'narration': 'ReedSolomonla veri onarılır.'},
            ],
        }

        self.assertEqual(
            _short_spoken_quality_issues(safe, 'Turkish'),
            [],
        )
        self.assertEqual(
            len(_short_spoken_quality_issues(unsafe, 'Turkish')),
            5,
        )

    def test_non_turkish_is_noop(self):
        package = {
            'scenes': [
                {'narration': 'OLED, GPS, Wi-Fi and QR appear together.'},
            ],
        }

        self.assertEqual(
            _short_story_quality_issues(package, 'English'),
            [],
        )


class ShortStoryApprovalTests(unittest.TestCase):
    def _approved_package(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(),
        ])
        package = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
            'one useful phone story',
        )
        package['short_story_qc'] = {
            'version': 1,
            'requested_topic': 'one useful phone story',
            'story_review_accepted': True,
            'ending_pair_accepted': True,
        }
        package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            package,
        )
        return package

    def test_approved_short_package_fingerprint_accepts_exact_material(self):
        package = self._approved_package()

        self.assertTrue(
            short_story_package_is_approved(
                package,
                'one useful phone story',
            )
        )
        self.assertFalse(
            short_story_package_is_approved(
                package,
                'a different requested topic',
            )
        )

    def test_approved_short_package_rejects_malformed_evidence(self):
        invalid_sources = [
            [
                {
                    'url': 'https://',
                    'evidence': 'A sentence that is long enough but has no URL host.',
                },
                {
                    'url': 'https://example.org/valid',
                    'evidence': 'A second valid-looking record cannot rescue the malformed one.',
                },
            ],
            [
                {
                    'url': 'https://example.com/invalid-type',
                    'evidence': ['not', 'a', 'sentence'],
                },
                {
                    'url': 'https://example.org/valid',
                    'evidence': 'A second valid-looking record cannot rescue the malformed one.',
                },
            ],
        ]

        for sources in invalid_sources:
            with self.subTest(sources=sources):
                package = self._approved_package()
                package['sources'] = sources
                self.assertFalse(short_story_package_is_approved(package))

    def test_approved_short_package_rejects_malformed_versions_and_scenes(self):
        for bad_version in ('v3', {}, True, None):
            with self.subTest(version=bad_version):
                package = self._approved_package()
                package['stock_scene_qc']['version'] = bad_version
                self.assertFalse(short_story_package_is_approved(package))

        package = self._approved_package()
        package['short_story_qc']['version'] = True
        self.assertFalse(short_story_package_is_approved(package))

        package = self._approved_package()
        package['scenes'].append('not a scene object')
        self.assertFalse(short_story_package_is_approved(package))

    def test_approved_short_package_binds_research_evidence(self):
        package = self._approved_package()
        package['sources'] = [
            {
                'url': 'https://example.com/unrelated',
                'evidence': 'An unrelated source sentence replaces the audited evidence.',
            },
            {
                'url': 'https://example.org/another-unrelated',
                'evidence': 'Another unrelated sentence replaces the second audited source.',
            },
        ]

        self.assertFalse(
            short_story_package_is_approved(
                package,
                'one useful phone story',
            )
        )

    def test_approved_short_package_fingerprint_rejects_mutation_or_missing_qc(self):
        package = self._approved_package()
        package['scenes'][0]['narration'] = 'Değiştirilmiş anlatım.'

        self.assertFalse(short_story_package_is_approved(package))
        self.assertFalse(short_story_package_is_approved({'scenes': []}))


if __name__ == '__main__':
    unittest.main()
