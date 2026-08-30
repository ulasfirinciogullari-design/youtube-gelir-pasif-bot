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

from app.services.director import _repair_short_stock_scenes, _word_count


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
                'Kadın kafeden kahve bardağıyla günbatımında yavaşça dışarı çıkar.'
                if final_variant
                else 'Müşteri kafeden kahvesiyle akşam ışığında sakince dışarı çıkar.'
            ),
            'visual_queries': (
                [
                    'woman exits cafe holding coffee at sunset',
                    'customer leaves coffee shop carrying cup',
                ]
                if final_variant
                else [
                    'customer exits cafe carrying coffee at dusk',
                    'person leaves coffee shop holding cup',
                ]
            ),
            'ai_prompt': None,
        },
    }
    for row in rows.values():
        assert _word_count(row['narration']) == 8
    return {'scenes': [copy.deepcopy(rows[position]) for position in positions]}


def critic_payload(failures=None):
    failures = failures or {}
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
    return {'scenes': rows}


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
        self.assertEqual(result['scenes'][0]['narration'], first['scenes'][0]['narration'])
        self.assertEqual(result['scenes'][4]['narration'], first['scenes'][1]['narration'])
        self.assertEqual(result['scenes'][5]['narration'], second['scenes'][0]['narration'])
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 2)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 2)

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


if __name__ == '__main__':
    unittest.main()
