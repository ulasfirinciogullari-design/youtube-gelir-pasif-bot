import copy
import json
import sys
import types
import unittest
from types import SimpleNamespace


openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = object
sys.modules.setdefault('openai', openai_stub)

config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(openai_model='test-model')
sys.modules['app.config'] = config_stub

from app.services.director import (
    _repair_short_stock_scenes,
    _short_spoken_quality_issues,
    _short_story_fingerprint,
    _short_story_quality_issues,
    _word_count,
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
            'checkout fallback',
        ),
        _scene(
            5,
            'Sonra mağazadan çıkar kahvemi alıp telefonumu cebime koyarım.',
            ['person exiting shop with coffee', 'person putting phone in pocket'],
            'ending fallback',
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
            'narration': 'Evde genç adam telefonunu masadan dikkatlice eline alır.',
            'visual_queries': [
                'young man picks up smartphone at home',
                'man picks up phone from home table',
            ],
            'ai_prompt': None,
        },
        4: {
            'position': 4,
            'narration': 'Kasiyer kafede müşteriye sıcak kahvesini sakinlikle uzatır bugün.',
            'visual_queries': [
                'barista hands customer coffee inside cafe',
                'cafe cashier hands hot coffee to customer',
            ],
            'ai_prompt': None,
        },
        5: {
            'position': 5,
            'narration': (
                'Kadın aynı kafe tezgâhında sıcak kahvesine memnuniyetle gülümser.'
                if final_variant
                else 'Müşteri aynı kafe tezgâhında sıcak kahvesine gülümser bugün.'
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


def critic_payload(failures=None, story_failures=None, ending_failures=None):
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
        'reason': 'One human situation follows a single causal question to a visible payoff.',
    }
    for key in story_failures:
        story_review[key] = False
    if story_failures:
        story_review['reason'] = 'The draft samples unrelated mechanisms instead of one story.'
    ending_pair = {
        'penultimate_position': 4,
        'final_position': 5,
        **{key: True for key in ending_boolean_keys},
        'location_anchor': 'same cafe counter',
        'reason': 'The same customer receives and enjoys coffee at the same counter.',
    }
    for key in ending_failures:
        ending_pair[key] = False
    if ending_failures:
        ending_pair['reason'] = 'The final beat jumps away from the preceding location.'
    rows = []
    for position in (0, 4, 5):
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
            '"previous_narration": "Kasiyer kafede müşteriye sıcak kahvesini sakinlikle uzatır bugün."',
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

    def test_deterministic_failure_retries_only_bad_position(self):
        first = valid_generator_payload()
        first['scenes'][0]['narration'] = (
            'Evde genç adam telefonunu masadan dikkatlice; eline alır.'
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
