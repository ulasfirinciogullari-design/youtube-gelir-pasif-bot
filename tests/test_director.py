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
    studio_plan_provider='openai',
    openai_api_key='test-openai-key',
    openai_model='test-model',
    gemini_critic_enabled=False,
    gemini_api_key='',
    gemini_model='gemini-3.1-pro-preview',
)
sys.modules['app.config'] = config_stub

import app.services.director as director_module
director_module.settings = config_stub.settings
from app.services.director import (
    ImmutableNarrationSceneBudgetError,
    _NaturalSpokenLanguageRepairRequired,
    _WholeStoryRepairRequired,
    _apply_short_preview_concrete_proxy_routes,
    _repair_short_stock_scenes,
    _short_preview_scene_budget_issues,
    _short_preview_scene_word_ranges,
    _short_spoken_quality_issues,
    _short_story_fingerprint,
    _short_story_quality_issues,
    _word_count,
    direct_and_qc,
    short_story_package_is_approved,
)


class PreviewNarrationBudgetTests(unittest.TestCase):
    def test_thirty_second_generated_budget_requires_natural_speed(self):
        self.assertEqual(
            director_module._target_word_budget(0.5),
            (56, 52, 60),
        )

    def test_thirty_second_legacy_lock_keeps_compatibility_range(self):
        self.assertEqual(
            director_module._target_word_budget(
                0.5,
                allow_legacy_short_lock=True,
            ),
            (56, 40, 60),
        )

    def test_four_scene_preview_has_hard_balanced_single_pass_ceilings(self):
        self.assertEqual(
            _short_preview_scene_word_ranges(48, 4),
            [[10, 14], [10, 14], [10, 14], [10, 14]],
        )
        balanced = {
            'scenes': [
                {
                    'narration': text,
                    'ai_prompt': (
                        None if position == 0 else f'AI scene {position}'
                    ),
                }
                for position, text in enumerate((
                    'Sürücü yola çıkmadan önce emniyet kemerini omzuna doğru sakince hemen çekmeye başlıyor.',
                    'Kemer yavaşça uzarken sürücü onu sertçe çekince mekanizma aniden kilitlenip tamamen duruyor.',
                    'Ani hız makaranın içindeki kilidi dişli çarka geçiriyor ve kemerin dönüşünü hemen durduruyor.',
                    'Sürücü kemeri yavaşça çekip metal dili kırmızı düğmeli tokaya takıyor ve güvenle hazırlanıyor.',
                ))
            ],
        }
        self.assertEqual(
            [
                _word_count(scene['narration'])
                for scene in balanced['scenes']
            ],
            [12, 12, 13, 13],
        )
        self.assertEqual(
            _short_preview_scene_budget_issues(balanced, 48, 4),
            [],
        )

    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_oversized_exact_scene_fails_before_voice_or_visual_media(
        self,
        openai_class,
        run_director,
    ):
        narrations = [
            'Sürücü yola çıkmadan önce emniyet kemerini omzuna doğru sakince çekiyor.',
            'Kemer yavaşça uzarken, tek sert çekişte aniden kilitlenip olduğu yerde kalıyor.',
            'Ani hız, makaranın içindeki kilidi dişli çarka geçirip dönüşü hemen durduruyor.',
            'Sürücü kemeri yeniden yavaşça çekip metal dili kırmızı düğmeli tokaya tek hamlede takıyor ve sonunda güvenle yola hazırlanıyor.',
        ]
        self.assertEqual(
            [_word_count(text) for text in narrations],
            [10, 11, 11, 18],
        )
        package = {
            'title': 'Emniyet Kemeri',
            'thumbnail_text': 'NASIL KİLİTLENİYOR?',
            'description': 'Tek bir emniyet kemeri mekanizması.',
            'sources': [],
            'scenes': [
                _scene(
                    position,
                    narration,
                    [f'seat belt visible action {position}'],
                    None if position == 0 else f'AI scene {position}',
                )
                for position, narration in enumerate(narrations)
            ],
        }
        exact = ' '.join(narrations)
        brief = (
            'Tam dört sahne kullan. Konuşma metni tam olarak şöyle '
            f'olsun: “{exact}”'
        )
        run_director.return_value = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': [
                {
                    'narration': f'Yönetmen sahne {position} metnini değiştirir.',
                    'visual_queries': scene['visual_queries'],
                    'ai_prompt': scene['ai_prompt'],
                    'pace': 'normal',
                    'transition': 'cut',
                }
                for position, scene in enumerate(package['scenes'])
            ],
            'qc_summary': [],
        }

        with self.assertRaisesRegex(
            ImmutableNarrationSceneBudgetError,
            r'scene 3 narration has 18 words.*maximum is 13',
        ):
            direct_and_qc(
                package,
                brief,
                0.5,
                'tr',
                {
                    'mode': 'preview',
                    'pace': 'balanced',
                    'visual_mix': 'ai_first',
                },
            )

        openai_class.assert_called_once()
        run_director.assert_called_once()

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_unlocked_oversized_ai_scene_is_rebalanced_by_director(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        initial_narrations = [
            'Sürücü yola çıkmadan önce emniyet kemerini omzuna doğru sakince çekiyor.',
            'Kemer yavaşça uzarken, tek sert çekişte aniden kilitlenip olduğu yerde kalıyor.',
            'Ani hız, makaranın içindeki kilidi dişli çarka geçirip dönüşü hemen durduruyor.',
            'Sürücü kemeri yeniden yavaşça çekip metal dili kırmızı düğmeli tokaya tek hamlede takıyor ve sonunda güvenle yola hazırlanıyor.',
        ]
        balanced_narrations = [
            'Sürücü yola çıkmadan hemen önce emniyet kemerini omzuna doğru sakince ve kontrollü biçimde çekmeye başlıyor.',
            'Kemer yavaşça uzarken sürücü onu sertçe çekince mekanizma aniden kilitlenip tamamen duruyor.',
            'Ani hız makaranın içindeki kilidi dişli çarka geçiriyor ve kemerin dönüşünü hemen durduruyor.',
            'Sürücü kemerin göğsünde düzgün durduğunu kontrol edip ellerini direksiyona koyarak güvenle yola hazırlanıyor.',
        ]

        def payload(narrations):
            return {
                'title': 'Emniyet Kemeri',
                'thumbnail_text': 'NASIL KİLİTLENİYOR?',
                'description': 'Tek bir emniyet kemeri mekanizması.',
                'scenes': [
                    {
                        'narration': narration,
                        'visual_queries': [
                            f'seat belt visible action {position}',
                        ],
                        'ai_prompt': (
                            None
                            if position == 0
                            else (
                                'Same driver visibly wears an already-fastened '
                                'three-point seat belt across the chest and '
                                'places both hands on the steering wheel.'
                                if position == 3 and 'göğsünde' in narration
                                else f'AI seat belt action scene {position}'
                            )
                        ),
                        'pace': 'normal',
                        'transition': 'cut',
                    }
                    for position, narration in enumerate(narrations)
                ],
                'qc_summary': [],
            }

        package = payload(initial_narrations)
        package['sources'] = []
        run_director.side_effect = [
            payload(initial_narrations),
            payload(balanced_narrations),
        ]

        def approve(_client, candidate, *_args, **_kwargs):
            approved = copy.deepcopy(candidate)
            approved['stock_scene_qc'] = {
                'version': director_module._STOCK_SCENE_QC_VERSION,
                'story_review': {'accepted': True},
                'ending_pair_review': {'accepted': True},
            }
            return approved

        repair_stock_scenes.side_effect = approve
        openai_class.return_value = object()

        result = direct_and_qc(
            package,
            'Tam dört sahne kullan ve tek bir emniyet kemeri hikâyesi anlat.',
            0.5,
            'tr',
            {
                'mode': 'preview',
                'pace': 'balanced',
                'visual_mix': 'ai_first',
            },
        )

        self.assertEqual(run_director.call_count, 2)
        correction_input = run_director.call_args_list[1].args[1]
        self.assertTrue(any(
            'hard AI single-pass maximum is 13' in issue
            for issue in correction_input['narration_quality_issues']
        ))
        self.assertTrue(any(
            'precision seat-belt latch insertion' in issue
            for issue in correction_input['narration_quality_issues']
        ))
        self.assertEqual(
            [
                _word_count(scene['narration'])
                for scene in result['scenes']
            ],
            [15, 12, 13, 13],
        )
        repair_stock_scenes.assert_called_once()


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
            'Otobüs bekleyen genç adam telefonunun hızla düşen pil yüzdesine şaşkınlıkla bakar.',
            ['young man checks phone at bus stop', 'commuter checks low phone battery'],
        ),
        _scene(
            1,
            'Soğukta pil geçici olarak elektrik vermekte kısa süre daha zorlanır.',
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
    assert 52 <= _word_count(narration) <= 60
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


def make_phone_under_pillow_package():
    narrations = [
        'Yastık altında gece şarj olan telefon, sabah normalden daha sıcak olabilir.',
        'Batarya bütün gece şarj olurken az da olsa ısı üretir.',
        'Yastık, bu ısının havaya rahatça yayılmasını büyük ölçüde engeller.',
        'Bu sıcaklık bataryanın zamanla gereğinden daha hızlı eskimesine yol açabilir.',
        'Bu yüzden telefonu sert, düz ve açık bir komodine bırak.',
        'Açıkta kalan telefon ısıyı havaya çok daha kolay verir.',
    ]
    queries = [
        [
            'charging smartphone tucked under bedroom pillow',
            'warm phone beneath pillow in morning',
        ],
        [
            'thermal camera charging smartphone battery',
            'smartphone battery heat while charging',
        ],
        [
            'thermal camera phone heat under pillow',
            'pillow trapping smartphone heat',
        ],
        [
            'aged smartphone battery heat damage',
            'battery cell degradation high temperature',
        ],
        [
            'hand places phone on open nightstand',
            'smartphone resting on hard bedside table',
        ],
        [
            'thermal camera phone cooling on nightstand',
            'smartphone dissipating heat in open air',
        ],
    ]
    scenes = [
        _scene(position, narration, queries[position])
        for position, narration in enumerate(narrations)
    ]
    narration = ' '.join(narrations)
    assert _word_count(narration) == 59
    return {
        'title': 'Telefonu Yastık Altında Şarj Etme',
        'description': 'Telefon ısısını tek bir gündelik sebep ve çözümle anlatır.',
        'thumbnail_text': 'YASTIK ALTINDA NEDEN ISINIR?',
        'sources': [{
            'url': 'https://example.com/battery-thermal-safety',
            'evidence': (
                'Charging creates heat, insulation restricts heat transfer, '
                'and sustained high temperature accelerates battery aging.'
            ),
        }],
        'scenes': scenes,
        'narration': narration,
        'tts_narration': narration,
        'visual_queries': [query for row in queries for query in row],
        'ai_scenes': [],
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


def make_explicit_ai_macro_return_package():
    package = make_ai_first_five_scene_package()
    package['scenes'][3]['narration'] = (
        'Makro kesitte aynı telefonun pili gerilim düşerken açıkça görünür.'
    )
    package['scenes'][3]['tts_text'] = package['scenes'][3]['narration']
    package['scenes'][3]['visual_queries'] = [
        'macro cutaway of same phone battery under load',
        'photorealistic smartphone battery voltage drop macro',
    ]
    package['scenes'][3]['ai_prompt'] = (
        'photorealistic macro cutaway inside the same phone battery as its '
        'voltage drops under load, no text'
    )
    package['narration'] = ' '.join(
        scene['narration'] for scene in package['scenes']
    )
    package['tts_narration'] = package['narration']
    package['visual_queries'] = [
        query
        for scene in package['scenes']
        for query in scene['visual_queries']
    ]
    package['ai_scenes'] = [
        scene['ai_prompt']
        for scene in package['scenes']
        if scene.get('ai_prompt')
    ]
    assert _word_count(package['narration']) == 45
    return package


def make_documentary_airplane_coda_package():
    package = make_ai_first_five_scene_package()
    package['scenes'][3]['narration'] = (
        'Loş kabin ışıkları yolcu uçağının koridorunda çıkış yolunu '
        'belirginleştirir.'
    )
    package['scenes'][3]['tts_text'] = package['scenes'][3]['narration']
    package['scenes'][3]['visual_queries'] = [
        'dim passenger airplane cabin aisle lights',
        'aircraft cabin floor path lighting',
    ]
    package['scenes'][3]['ai_prompt'] = (
        'photorealistic interior of the same passenger aircraft cabin at '
        'night, dim aisle path lights guide toward the forward door, no text'
    )
    package['scenes'][4]['narration'] = (
        'Aynı yolcu uçağı gece pistinden havalanırken kabin ışıkları kısık kalır.'
    )
    package['scenes'][4]['tts_text'] = package['scenes'][4]['narration']
    package['scenes'][4]['visual_queries'] = [
        'passenger airplane night runway takeoff exterior',
        'airliner taking off blue hour exterior',
    ]
    package['scenes'][4]['ai_prompt'] = None
    package['narration'] = ' '.join(
        scene['narration'] for scene in package['scenes']
    )
    package['tts_narration'] = package['narration']
    package['visual_queries'] = [
        query
        for scene in package['scenes']
        for query in scene['visual_queries']
    ]
    package['ai_scenes'] = [
        scene['ai_prompt']
        for scene in package['scenes']
        if scene.get('ai_prompt')
    ]
    assert 42 <= _word_count(package['narration']) <= 60
    return package


class ShortPreviewConcreteProxyRoutingTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.openai_api_key = 'test-openai-key'
        config_stub.settings.openai_model = 'test-model'

    def test_phone_under_pillow_routes_only_grounded_invisible_mechanisms(self):
        package = make_phone_under_pillow_package()

        routed = _apply_short_preview_concrete_proxy_routes(
            package,
            {
                'mode': 'preview',
                'visual_mix': 'balanced',
            },
            0.5,
            'Telefon neden yastık altında ısınır?',
        )

        self.assertEqual(routed['narration'], package['narration'])
        self.assertEqual(
            [
                row['position']
                for row in routed['short_proxy_routes']['routes']
            ],
            [1, 2, 3, 5],
        )
        self.assertEqual(
            [
                row['proxy']
                for row in routed['short_proxy_routes']['routes']
            ],
            [
                'charging_heat',
                'insulated_heat',
                'thermal_aging',
                'open_air_cooling',
            ],
        )
        self.assertEqual(routed['short_proxy_routes']['paid_cap'], 4)
        self.assertEqual(len(routed['ai_scenes']), 4)
        self.assertIsNone(routed['scenes'][0]['ai_prompt'])
        self.assertIsNone(routed['scenes'][4]['ai_prompt'])
        self.assertTrue(all(
            routed['scenes'][position]['ai_prompt']
            for position in (1, 2, 3, 5)
        ))
        self.assertIn(
            'thermal-camera',
            routed['scenes'][2]['ai_prompt'],
        )
        self.assertIn(
            'same hard, flat, open surface in the established scene setting',
            routed['scenes'][5]['ai_prompt'],
        )
        self.assertIn(
            'purely photographic, text-free, and unbranded',
            routed['scenes'][1]['ai_prompt'],
        )
        self.assertTrue(all(
            scene['ai_prompt'] is None
            for scene in package['scenes']
        ))

    def test_proxy_router_rejects_non_phone_batteries_and_retains_setting(self):
        def one_scene(narration, queries):
            return {
                'scenes': [{
                    'narration': narration,
                    'visual_queries': queries,
                    'ai_prompt': None,
                }],
                'narration': narration,
                'tts_narration': narration,
                'ai_scenes': [],
                'director_qc': [],
            }

        for narration, queries in (
            (
                'Laptop bataryası şarj olurken az miktarda ısı üretir.',
                ['thermal camera laptop battery charging'],
            ),
            (
                'An electric vehicle battery produces heat while charging.',
                ['electric vehicle battery thermal charging'],
            ),
        ):
            package = one_scene(narration, queries)
            routed = _apply_short_preview_concrete_proxy_routes(
                package,
                {'mode': 'preview', 'visual_mix': 'balanced'},
                0.5,
                'Batarya ısısı',
            )
            self.assertIs(routed, package)
            self.assertIsNone(routed['scenes'][0]['ai_prompt'])

        car_scene = one_scene(
            'The smartphone battery produces heat while charging.',
            ['smartphone charging inside parked car'],
        )
        routed = _apply_short_preview_concrete_proxy_routes(
            car_scene,
            {'mode': 'preview', 'visual_mix': 'balanced'},
            0.5,
            'Phone charging inside a parked car',
        )
        prompt = routed['scenes'][0]['ai_prompt']
        self.assertIn('smartphone charging inside parked car', prompt)
        self.assertNotIn('bedroom', prompt.casefold())

    def test_proxy_router_rejects_negation_and_reversed_thermal_causality(self):
        examples = (
            (
                'Telefon bataryası şarj olurken ısı üretmez.',
                ['thermal camera smartphone battery charging'],
            ),
            (
                'Telefon bataryası şarj olurken ısı üretmiyor.',
                ['thermal camera smartphone battery charging'],
            ),
            (
                'A phone beneath a pillow does not trap heat.',
                ['thermal camera smartphone under pillow'],
            ),
            (
                'Açıkta kalan telefon ısıyı havaya vermez.',
                ['thermal camera phone cooling on nightstand'],
            ),
            (
                'Battery aging causes the smartphone to run hotter.',
                ['aged smartphone battery thermal closeup'],
            ),
            (
                'Batarya eskidikçe telefon daha sıcak çalışır.',
                ['aged smartphone battery heat closeup'],
            ),
        )
        for narration, queries in examples:
            package = {
                'scenes': [{
                    'narration': narration,
                    'visual_queries': queries,
                    'ai_prompt': None,
                }],
                'narration': narration,
                'tts_narration': narration,
                'ai_scenes': [],
                'director_qc': [],
            }
            routed = _apply_short_preview_concrete_proxy_routes(
                package,
                {'mode': 'preview', 'visual_mix': 'balanced'},
                0.5,
                'Telefon bataryası ve ısı',
            )
            self.assertIs(routed, package, narration)
            self.assertIsNone(routed['scenes'][0]['ai_prompt'])

    def test_vague_abstraction_and_explicit_stock_contract_stay_fail_closed(self):
        package = make_phone_under_pillow_package()
        vague = copy.deepcopy(package)
        vague['scenes'][1]['narration'] = (
            'Telefonun görünmeyen sırrı her şeyi sessizce değiştirir.'
        )
        vague['scenes'][1]['tts_text'] = vague['scenes'][1]['narration']
        vague['narration'] = ' '.join(
            scene['narration'] for scene in vague['scenes']
        )

        routed = _apply_short_preview_concrete_proxy_routes(
            vague,
            {'mode': 'preview', 'visual_mix': 'balanced'},
            0.5,
            'Telefonun görünmeyen sırrı nedir?',
        )
        self.assertIsNone(routed['scenes'][1]['ai_prompt'])

        stock_locked = _apply_short_preview_concrete_proxy_routes(
            package,
            {'mode': 'preview', 'visual_mix': 'balanced'},
            0.5,
            (
                'Bütün sahneler stock only kalsın ve her ai_prompt null olsun. '
                'Konuşma metnini değiştirme.'
            ),
        )
        self.assertIs(stock_locked, package)
        self.assertNotIn('short_proxy_routes', stock_locked)

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_director_applies_proxy_routes_before_stock_preflight(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_phone_under_pillow_package()
        run_director.return_value = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': [
                {
                    'narration': scene['narration'],
                    'visual_queries': copy.deepcopy(scene['visual_queries']),
                    'ai_prompt': None,
                    'pace': 'normal',
                    'transition': 'cut',
                }
                for scene in package['scenes']
            ],
            'qc_summary': [],
        }

        def attest(_client, candidate, *_args, **_kwargs):
            approved = copy.deepcopy(candidate)
            approved['stock_scene_qc'] = {
                'version': director_module._STOCK_SCENE_QC_VERSION,
                'story_review': {'accepted': True},
                'ending_pair_review': {'accepted': True},
            }
            return approved

        repair_stock_scenes.side_effect = attest
        exact = package['narration']
        result = direct_and_qc(
            package,
            (
                'Tam altı sahne kullan. Konuşma metni tam olarak şu altı '
                f'cümle olsun: “{exact}”'
            ),
            0.5,
            'tr',
            {
                'mode': 'preview',
                'pace': 'balanced',
                'visual_mix': 'balanced',
                'content_style': 'documentary',
            },
        )

        preflight_candidate = repair_stock_scenes.call_args.args[1]
        self.assertEqual(
            [
                position
                for position, scene in enumerate(
                    preflight_candidate['scenes']
                )
                if scene.get('ai_prompt')
            ],
            [1, 2, 3, 5],
        )
        self.assertEqual(result['narration'], exact)
        self.assertEqual(result['ai_scene_count'], 4)
        self.assertEqual(run_director.call_count, 1)
        openai_class.assert_called_once()

    def test_routed_phone_story_reaches_stock_critic_with_only_literal_scenes(self):
        package = make_phone_under_pillow_package()
        routed = _apply_short_preview_concrete_proxy_routes(
            package,
            {'mode': 'preview', 'visual_mix': 'balanced'},
            0.5,
            'Telefon neden yastık altında ısınır?',
        )
        generated = {
            'scenes': [
                {
                    'position': position,
                    'narration': routed['scenes'][position]['narration'],
                    'visual_queries': routed['scenes'][position]['visual_queries'],
                    'ai_prompt': None,
                }
                for position in (0, 4)
            ],
        }
        client = FakeClient([
            generated,
            critic_payload(
                stock_positions=(0, 4),
                scene_count=6,
            ),
        ])
        exact = routed['narration']

        result = _repair_short_stock_scenes(
            client,
            routed,
            'Turkish',
            0.5,
            topic=(
                'Konuşma metni tam olarak şu altı cümle olsun: '
                f'“{exact}”'
            ),
            content_style='documentary',
        )

        self.assertEqual(result['narration'], exact)
        self.assertEqual(
            result['stock_scene_qc']['target_positions'],
            [0, 4],
        )
        self.assertEqual(len(result['ai_scenes']), 4)
        self.assertIn(
            'thermal-camera',
            result['scenes'][5]['ai_prompt'],
        )
        self.assertEqual(len(client.responses.calls), 2)


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
    technical_insert_return_valid=True,
    documentary_exterior_coda_valid=True,
):
    failures = failures or {}
    story_failures = story_failures or []
    ending_failures = ending_failures or []
    story_boolean_keys = {
        'all_explicit_brief_constraints_preserved',
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
        'explicit_technical_insert_return_contract_satisfied',
        'documentary_exterior_establishing_coda_satisfied',
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
    ending_pair['explicit_technical_insert_return_contract_satisfied'] = (
        technical_insert_return_valid
    )
    ending_pair['documentary_exterior_establishing_coda_satisfied'] = (
        documentary_exterior_coda_valid
    )
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


class ExplicitSceneCountTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.openai_api_key = 'test-openai-key'
        config_stub.settings.openai_model = 'test-model'

    def test_reads_live_tam_bes_sahne_brief_without_confusing_other_counts(self):
        brief = (
            '30 saniyelik tek mekân hikâyesi. Tam beş sahne ve doğal '
            '50–60 Türkçe kelime kullan; bu beş anlatı vuruşunu koru. '
            'Yalnız sahne 1, 2 ve 3 ai_prompt taşısın; sahne 4 ve 5 null olsun.'
        )

        self.assertEqual(
            director_module._explicit_scene_count_from_brief(brief),
            5,
        )
        self.assertEqual(
            director_module._explicit_scene_count_from_brief(
                'Return exactly 6 scenes with one continuous story.'
            ),
            6,
        )
        self.assertIsNone(
            director_module._explicit_scene_count_from_brief(
                'Beş anlatı vuruşunu koru; sahne 1, 2 ve 3 yapay görsel kullansın.'
            )
        )

    def test_conflicting_or_unsupported_explicit_counts_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'conflicting'):
            director_module._explicit_scene_count_from_brief(
                'Tam beş sahne yaz, fakat exactly 6 scenes return.'
            )
        with self.assertRaisesRegex(RuntimeError, 'between 3 and 70'):
            director_module._explicit_scene_count_from_brief(
                'Tam iki sahne kullan.'
            )
        self.assertEqual(
            director_module._explicit_scene_count_from_brief(
                'Return exactly 35 scenes.'
            ),
            35,
        )
        for unsupported in (71, 100):
            with self.subTest(unsupported=unsupported), self.assertRaisesRegex(
                RuntimeError,
                'between 3 and 70',
            ):
                director_module._explicit_scene_count_from_brief(
                    f'Return exactly {unsupported} scenes.'
                )

    def test_production_sized_exact_narration_lock_is_supported(self):
        locked = ' '.join(
            'A person performs one visible action nearby.'
            for _index in range(35)
        )
        self.assertEqual(
            director_module._infer_exact_narration_scene_count(locked),
            35,
        )

    def test_negated_counts_are_ignored_and_turkish_uppercase_is_normalized(self):
        self.assertIsNone(
            director_module._explicit_scene_count_from_brief(
                'Tam beş sahne olmasın.'
            )
        )
        self.assertIsNone(
            director_module._explicit_scene_count_from_brief(
                'Exactly five scenes is not required.'
            )
        )
        self.assertEqual(
            director_module._explicit_scene_count_from_brief(
                'Do not return exactly five scenes; return exactly 6 scenes.'
            ),
            6,
        )
        self.assertEqual(
            director_module._explicit_scene_count_from_brief(
                'TAM SEKİZ SAHNE KULLAN.'
            ),
            8,
        )
        self.assertEqual(
            director_module._explicit_scene_count_from_brief(
                'Do not add text. Exactly five scenes.'
            ),
            5,
        )
        for positive_brief in (
            'Tam beş sahne kullanmak istiyorum.',
            'Tam beş sahne yapmak istiyorum.',
            'Tam beş sahne olmasını istiyorum.',
        ):
            with self.subTest(positive_brief=positive_brief):
                self.assertEqual(
                    director_module._explicit_scene_count_from_brief(
                        positive_brief
                    ),
                    5,
                )
        for negative_brief in (
            'Must not use exactly five scenes.',
            "I don't want exactly five scenes.",
            'Tam beş sahne zorunlu değil.',
            'Tam beş sahne şart değil.',
            'Tam beş sahne olmasına gerek yok.',
            'Tam beş sahne olmasını istemiyorum.',
            'Tam beş sahne olmamalı.',
            'Tam beş sahne kullanılmasın.',
        ):
            with self.subTest(negative_brief=negative_brief):
                self.assertIsNone(
                    director_module._explicit_scene_count_from_brief(
                        negative_brief
                    )
                )
        with self.assertRaisesRegex(RuntimeError, 'conflicting'):
            director_module._explicit_scene_count_from_brief(
                'Tam beş sahne olmasın; ardından tam beş sahne kullan.'
            )

    def test_oversized_short_brief_fails_before_provider_or_model_construction(self):
        with (
            patch.object(director_module, '_studio_plan_provider') as provider,
            patch.object(director_module, 'OpenAI') as openai_class,
            patch.object(director_module, '_run_director') as run_director,
            self.assertRaisesRegex(
                RuntimeError,
                'too long for complete pre-media constraint review',
            ),
        ):
            direct_and_qc(
                make_coherent_battery_package(),
                'x' * (director_module._MAX_STORY_BRIEF_CHARS + 1),
                0.5,
                'tr',
                {'mode': 'preview'},
            )

        provider.assert_not_called()
        openai_class.assert_not_called()
        run_director.assert_not_called()

    def test_exact_count_tightens_schema_and_trusted_director_instruction(self):
        relaxed = director_module._director_json_schema(5)
        exact = director_module._director_json_schema(
            5,
            exact_scene_count=True,
        )
        self.assertEqual(
            (
                relaxed['properties']['scenes']['minItems'],
                relaxed['properties']['scenes']['maxItems'],
            ),
            (4, 6),
        )
        self.assertEqual(
            (
                exact['properties']['scenes']['minItems'],
                exact['properties']['scenes']['maxItems'],
            ),
            (5, 5),
        )

        package = make_ai_first_five_scene_package()
        payload = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': copy.deepcopy(package['scenes']),
            'qc_summary': [],
        }
        client = FakeClient([payload])
        brief = (
            'Tam beş sahne kullan. Her AI sahnesinde aynı unbranded matte '
            'silver 14-inch laptop görünmeli.'
        )
        director_module._run_director(
            client,
            package,
            brief,
            'Turkish',
            0.5,
            48,
            45,
            51,
            5,
            {'mode': 'preview', 'pace': 'balanced'},
            exact_scene_count=True,
        )
        prompt = client.responses.calls[0]['input']
        self.assertIn(
            'USER-BRIEF HARD CONSTRAINT: return exactly 5 scenes',
            prompt,
        )
        self.assertNotIn(
            'Target scene budget: approximately 5 scenes',
            prompt,
        )
        self.assertIn(brief, prompt)
        self.assertIn('standalone paid-generation instruction', prompt)
        self.assertIn('dimensions', prompt)
        self.assertIn('brand state', prompt)
        self.assertIn('forbidden elements', prompt)
        self.assertIn(
            'Never assume a later generation can see an earlier prompt',
            prompt,
        )
        self.assertIn('“sıkışan ısı fanı hızlandırıyor”', prompt)
        self.assertIn('“açılan boşluk fanı yavaşlatıyor”', prompt)
        self.assertIn('purely visual production metadata out of speech', prompt)
        self.assertIn('“koyu lacivert tişörtlü Mert”', prompt)
        self.assertIn('“arkadan izliyor”', prompt)

    def test_short_preview_prompt_distinguishes_authored_and_paid_ai_limits(self):
        package = make_ai_first_five_scene_package()
        payload = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': copy.deepcopy(package['scenes']),
            'qc_summary': [],
        }
        expected = {
            'real_first': (5, 1),
            'balanced': (5, 4),
            'ai_first': (4, 4),
        }
        for visual_mix, (authored_limit, paid_limit) in expected.items():
            with self.subTest(visual_mix=visual_mix):
                client = FakeClient([copy.deepcopy(payload)])
                director_module._run_director(
                    client,
                    package,
                    'Tam beş sahne kullan.',
                    'Turkish',
                    0.5,
                    48,
                    45,
                    51,
                    5,
                    {
                        'mode': 'preview',
                        'pace': 'balanced',
                        'visual_mix': visual_mix,
                    },
                    exact_scene_count=True,
                )
                prompt = client.responses.calls[0]['input']
                self.assertIn(
                    f'Up to {authored_limit} scenes may carry a non-null fallback',
                    prompt,
                )
                self.assertIn(
                    f'worker will submit at most {paid_limit} paid primary generations',
                    prompt,
                )
                self.assertIn(
                    f'no more than {paid_limit} scenes truly depend on AI',
                    prompt,
                )

    def test_production_scene_target_is_seven_per_minute_for_every_pace_and_capped(self):
        for pace in ('calm', 'balanced', 'dynamic'):
            with self.subTest(pace=pace):
                self.assertEqual(
                    director_module._target_scene_count(2.0, pace),
                    14,
                )
                self.assertEqual(
                    director_module._target_scene_count(5.0, pace),
                    35,
                )
        self.assertEqual(
            director_module._target_scene_count(8.0, 'balanced'),
            56,
        )
        self.assertEqual(
            director_module._target_scene_count(10.0, 'calm'),
            70,
        )
        self.assertEqual(
            director_module._target_scene_count(20.0, 'dynamic'),
            70,
        )

    def test_production_director_prompt_is_single_pass_and_keeps_exact_count(self):
        package = make_ai_first_five_scene_package()
        payload = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': copy.deepcopy(package['scenes']),
            'qc_summary': [],
        }
        client = FakeClient([payload])

        director_module._run_director(
            client,
            package,
            'Return exactly 5 scenes about one visible process.',
            'Turkish',
            5.0,
            500,
            430,
            530,
            5,
            {'mode': 'production', 'pace': 'calm'},
            exact_scene_count=True,
        )

        prompt = client.responses.calls[0]['input']
        self.assertIn(
            'USER-BRIEF HARD CONSTRAINT: return exactly 5 scenes',
            prompt,
        )
        self.assertIn('preferably keep each scene narration at 5-14 words', prompt)
        self.assertIn('one continuous 5-10-second shot', prompt)
        self.assertIn(
            "never overrides a user brief's explicit exact scene count",
            prompt,
        )

    def test_six_scene_output_for_explicit_five_is_repaired_then_rejected_before_critic(self):
        package = make_coherent_battery_package()
        payload = {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': copy.deepcopy(package['scenes']),
            'qc_summary': [],
        }
        brief = (
            '30 saniyelik tek mekân hikâyesi anlat. Tam beş sahne ve '
            'doğal Türkçe kullan.'
        )

        with (
            patch.object(director_module, 'OpenAI', return_value=object()),
            patch.object(
                director_module,
                '_run_director',
                return_value=payload,
            ) as run_director,
            patch.object(
                director_module,
                '_short_story_quality_issues',
                return_value=[],
            ),
            patch.object(
                director_module,
                '_repair_short_stock_scenes',
            ) as repair_stock,
            self.assertRaisesRegex(
                RuntimeError,
                'required exactly 5',
            ),
        ):
            direct_and_qc(
                package,
                brief,
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

        self.assertEqual(run_director.call_count, 4)
        self.assertTrue(all(
            call.kwargs['exact_scene_count'] is True
            and call.args[8] == 5
            for call in run_director.call_args_list
        ))
        repair_stock.assert_not_called()

    def test_explicit_count_is_still_enforced_when_openai_director_is_disabled(self):
        config_stub.settings.openai_api_key = ''
        with self.assertRaisesRegex(RuntimeError, 'required exactly 5'):
            direct_and_qc(
                make_coherent_battery_package(),
                'Tam beş sahne kullan.',
                0.5,
                'tr',
            )


class ExactNarrationDirectorLockTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.openai_api_key = 'test-openai-key'
        config_stub.settings.openai_model = 'test-model'

    @staticmethod
    def _locked_brief(package, narration=None):
        block = narration or ' '.join(
            scene['narration']
            for scene in package['scenes']
        )
        return (
            'Tam altı sahne kullan. Konuşma metni tam olarak şu altı '
            'cümle olsun; prodüksiyon notlarını seslendirme: '
            f'“{block}”'
        )

    @staticmethod
    def _locked_brief_without_scene_count(package):
        block = ' '.join(
            scene['narration']
            for scene in package['scenes']
        )
        return (
            'Konuşma metni tam olarak şöyle olsun: '
            f'“{block}”'
        )

    @staticmethod
    def _director_payload(package, *, all_ai=False, marker='director-marker'):
        scenes = []
        for position, scene in enumerate(package['scenes']):
            scenes.append({
                'narration': (
                    f'Yönetmen sahne {position} metnini tamamen değiştiriyor.'
                ),
                'visual_queries': (
                    [f'{marker} visible action {position}', f'backup action view {position}']
                    if position == 0
                    else copy.deepcopy(scene['visual_queries'])
                ),
                'ai_prompt': (
                    f'paid visual contract for scene {position}'
                    if all_ai
                    else scene.get('ai_prompt')
                ),
                'pace': scene['pace'],
                'transition': scene['transition'],
            })
        return {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': scenes,
            'qc_summary': [],
        }

    @staticmethod
    def _approve(candidate):
        approved = copy.deepcopy(candidate)
        approved['stock_scene_qc'] = {
            'version': director_module._STOCK_SCENE_QC_VERSION,
            'story_review': {'accepted': True},
            'ending_pair_review': {'accepted': True},
        }
        return approved

    def test_exact_lock_split_rejects_false_sentence_boundaries(self):
        ambiguous_blocks = (
            'Dr. Ayşe telefonu açtı\nEkran söndü.',
            (
                'Bugün klinikte görev yapan deneyimli Uzm. '
                'Ayşe telefonu masada iki eliyle açıyor. '
                'Ekran masada bir anda yeniden aydınlanıyor.'
            ),
            (
                '1) Ayşe telefonu masada iki eliyle açıyor. '
                '2) Ekran masada bir anda yeniden aydınlanıyor.'
            ),
        )

        for block in ambiguous_blocks:
            with self.subTest(block=block), self.assertRaisesRegex(
                RuntimeError,
                'cannot be segmented unambiguously',
            ):
                inferred_count = len(list(
                    director_module._EXACT_NARRATION_SENTENCE_PATTERN.finditer(
                        director_module._normalize_exact_narration(block)
                    )
                ))
                director_module._split_exact_narration_lock(
                    block,
                    inferred_count,
                )

    def test_empty_exact_narration_quote_is_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            'exact spoken-narration lock is empty',
        ):
            director_module._exact_narration_lock_from_brief(
                'Konuşma metni tam olarak şöyle olsun: “”'
            )

    def test_declarative_turkish_exact_narration_phrase_is_locked(self):
        block = (
            'Yağmurdan önce toprak kokusu belirginleşmeye başlar. '
            'İlk damlalar gözenekli zeminde küçük kabarcıklar oluşturur. '
            'Kabarcıklar kokulu parçacıkları havaya doğru taşır.'
        )

        locked = director_module._exact_narration_lock_from_brief(
            f'Konuşma metni tam olarak şöyledir: “{block}”'
        )

        self.assertEqual(locked, block)
        self.assertEqual(
            director_module._infer_exact_narration_scene_count(locked),
            3,
        )

    def test_production_exact_lock_is_recognized_as_four_scenes_and_45_words(self):
        block = (
            'Dizinin en heyecanlı yerinde, yataktaki laptop birden uçak gibi '
            'uğuldamaya başlıyor. Yorgan alttaki hava girişini kapatıyor; '
            'sıcak hava içeride kalınca fan daha da hızlanıyor. Mert laptopu '
            'ahşap masaya alıyor; alttaki hava girişi açılınca fanın sesi '
            'hemen düşüyor. Mert yatağın yanındaki ahşap masada dizisini '
            'rahatça izliyor.'
        )
        brief = (
            'Tam dört sahne kullan. Konuşma metni tam olarak şu dört '
            'cümle ve 45 kelime olsun; hiçbir kıyafet, kamera veya kadraj '
            f'talimatını seslendirme: “{block}”'
        )

        locked = director_module._exact_narration_lock_from_brief(brief)

        self.assertEqual(locked, block)
        self.assertEqual(
            director_module._infer_exact_narration_scene_count(locked),
            4,
        )
        self.assertEqual(_word_count(locked), 45)

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_initial_director_paraphrase_is_restored_before_stock_repair(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_coherent_battery_package()
        expected = [scene['narration'] for scene in package['scenes']]
        revised = self._director_payload(package, marker='initial-lock-marker')
        run_director.return_value = revised
        repair_stock_scenes.side_effect = (
            lambda _client, candidate, *_args, **_kwargs: self._approve(candidate)
        )
        openai_class.return_value = object()

        result = direct_and_qc(
            package,
            self._locked_brief(package),
            0.5,
            'tr',
            {'mode': 'preview', 'pace': 'balanced'},
        )

        repaired_input = repair_stock_scenes.call_args.args[1]
        self.assertEqual(
            [scene['narration'] for scene in repaired_input['scenes']],
            expected,
        )
        self.assertEqual(
            [scene['tts_text'] for scene in repaired_input['scenes']],
            expected,
        )
        self.assertTrue(
            repair_stock_scenes.call_args.kwargs[
                'allow_legacy_short_budget'
            ]
        )
        self.assertEqual(result['narration'], ' '.join(expected))
        self.assertEqual(
            result['scenes'][0]['visual_queries'],
            revised['scenes'][0]['visual_queries'],
        )

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_every_director_correction_path_reapplies_exact_narration(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_coherent_battery_package()
        expected = [scene['narration'] for scene in package['scenes']]
        initial = self._director_payload(
            package,
            all_ai=True,
            marker='initial-correction-marker',
        )
        corrected = self._director_payload(
            package,
            marker='ordinary-correction-marker',
        )
        whole_story = self._director_payload(
            package,
            marker='whole-story-marker',
        )
        run_director.side_effect = [initial, corrected, whole_story]
        repair_calls = 0

        def repair(_client, candidate, *_args, **_kwargs):
            nonlocal repair_calls
            repair_calls += 1
            if repair_calls == 1:
                raise _WholeStoryRepairRequired(
                    ['all_explicit_brief_constraints_preserved'],
                    'visible constraint omitted',
                )
            return self._approve(candidate)

        repair_stock_scenes.side_effect = repair
        openai_class.return_value = object()

        result = direct_and_qc(
            package,
            self._locked_brief(package),
            0.5,
            'tr',
            {
                'mode': 'preview',
                'pace': 'balanced',
                'visual_mix': 'ai_first',
            },
        )

        self.assertEqual(run_director.call_count, 3)
        correction_input = run_director.call_args_list[1].args[1]
        self.assertEqual(
            [scene['narration'] for scene in correction_input['scenes']],
            expected,
        )
        first_review = repair_stock_scenes.call_args_list[0].args[1]
        second_review = repair_stock_scenes.call_args_list[1].args[1]
        self.assertEqual(
            [scene['narration'] for scene in first_review['scenes']],
            expected,
        )
        self.assertEqual(
            [scene['narration'] for scene in second_review['scenes']],
            expected,
        )
        self.assertEqual(result['narration'], ' '.join(expected))
        self.assertEqual(
            result['scenes'][0]['visual_queries'],
            whole_story['scenes'][0]['visual_queries'],
        )

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_exact_narration_scene_mismatch_fails_before_stock_or_media(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_coherent_battery_package()
        revised = self._director_payload(package)
        revised['scenes'] = revised['scenes'][:-1]
        run_director.return_value = revised
        openai_class.return_value = object()

        with self.assertRaisesRegex(
            RuntimeError,
            'cannot be segmented unambiguously',
        ):
            direct_and_qc(
                package,
                self._locked_brief(package),
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

        repair_stock_scenes.assert_not_called()

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_exact_lock_without_count_makes_sentence_count_immutable(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_coherent_battery_package()
        revised = self._director_payload(package)
        revised['scenes'] = revised['scenes'][:-1]
        run_director.return_value = revised
        openai_class.return_value = object()

        with self.assertRaisesRegex(
            RuntimeError,
            'cannot be segmented unambiguously',
        ):
            direct_and_qc(
                package,
                self._locked_brief_without_scene_count(package),
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

        self.assertEqual(run_director.call_count, 1)
        self.assertEqual(run_director.call_args.args[8], 6)
        self.assertIs(
            run_director.call_args.kwargs['exact_scene_count'],
            True,
        )
        repair_stock_scenes.assert_not_called()

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_unsafe_exact_locked_speech_is_restored_then_rejected(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        package = make_coherent_battery_package()
        unsafe_scenes = [scene['narration'] for scene in package['scenes']]
        unsafe_scenes[1] = (
            'Sıkışan ısı fanı hızlandırıyor, cihaz yatağın '
            'üstünde uğulduyor.'
        )
        unsafe_block = ' '.join(unsafe_scenes)
        run_director.return_value = self._director_payload(package)
        openai_class.return_value = object()

        with self.assertRaisesRegex(
            RuntimeError,
            'Short-preview editorial gate rejected narration',
        ):
            direct_and_qc(
                package,
                self._locked_brief(package, unsafe_block),
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

        self.assertEqual(run_director.call_count, 4)
        repair_stock_scenes.assert_not_called()


class ShortStockRepairTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.gemini_critic_enabled = False
        config_stub.settings.gemini_api_key = ''
        config_stub.settings.gemini_model = 'gemini-3.1-pro-preview'

    @staticmethod
    def _locked_ai_first_payload(package):
        return {
            'scenes': [
                {
                    'position': 0,
                    'narration': package['scenes'][0]['narration'],
                    'visual_queries': [
                        'woman checks silent phone outdoors',
                        'commuter examines phone at bus stop',
                    ],
                    'ai_prompt': None,
                },
                {
                    'position': 2,
                    'narration': package['scenes'][2]['narration'],
                    'visual_queries': [
                        'woman places phone inside winter coat',
                        'commuter pockets phone at bus stop',
                    ],
                    'ai_prompt': None,
                },
            ],
        }

    @staticmethod
    def _complete_narration(package):
        return ' '.join(
            scene['narration']
            for scene in package['scenes']
        )

    def test_exact_turkish_narration_lock_preserves_text_and_repairs_queries(self):
        package = make_ai_first_five_scene_package()
        original = copy.deepcopy(package)
        block = self._complete_narration(package)
        brief = (
            'Konuşma metni tam olarak şu beş cümle ve 45 kelime olsun; '
            'hiçbir kıyafet, kamera veya kadraj talimatını seslendirme: '
            f'“{block}”'
        )
        client = FakeClient([
            self._locked_ai_first_payload(package),
            critic_payload(stock_positions=(0, 2), scene_count=5),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            topic=brief,
        )

        self.assertEqual(
            [scene['narration'] for scene in result['scenes']],
            [scene['narration'] for scene in original['scenes']],
        )
        self.assertEqual(result['narration'], block)
        self.assertNotEqual(
            result['scenes'][0]['visual_queries'],
            original['scenes'][0]['visual_queries'],
        )
        self.assertNotEqual(
            result['scenes'][2]['visual_queries'],
            original['scenes'][2]['visual_queries'],
        )
        writer_input = client.responses.calls[0]['input']
        self.assertIn('locked_narration', writer_input)
        self.assertIn('Repair only visual_queries', writer_input)

    def test_documentary_broll_keeps_sourced_dates_and_scale_in_voiceover(self):
        narrations = [
            '1997 yılında bu konteyner gemisi ilk seferine çıktı.',
            'İlk rotasında iki liman arasında 62 konteyner taşıdı.',
            'Kariyeri boyunca toplam 4,8 milyon ton yük taşıdı.',
            'Gemi farklı ülkelerdeki yoğun limanlara binlerce kez uğradı.',
            'Gemi 29 yıl sonra aynı limana yeniden döndü.',
            'Gemi son yolculuğunu aynı limanda böylece tamamladı.',
        ]
        queries = [
            [
                'container ship on maiden voyage',
                'cargo vessel sailing from port',
            ],
            [
                'container ship loaded in harbor',
                'cargo ship carrying stacked containers',
            ],
            [
                'container ship carrying heavy cargo',
                'loaded cargo vessel at sea',
            ],
            [
                'container ship entering busy port',
                'cargo vessel arriving crowded harbor',
            ],
            [
                'old container ship returning to port',
                'cargo vessel arriving same harbor',
            ],
            [
                'old cargo ship at home harbor',
                'container vessel final port arrival',
            ],
        ]
        scenes = [
            _scene(position, narration, queries[position])
            for position, narration in enumerate(narrations)
        ]
        complete_narration = ' '.join(narrations)
        self.assertEqual(_word_count(complete_narration), 48)
        package = {
            'title': 'Bir Konteyner Gemisinin 29 Yılı',
            'description': 'Kaynaklı bir denizcilik mikro belgeseli.',
            'thumbnail_text': '29 YIL DENİZDE',
            'sources': [
                {
                    'url': 'https://example.com/ship-history',
                    'evidence': (
                        'The vessel entered service in 1997, called at busy '
                        'ports in multiple countries thousands of times, and '
                        'returned to the same home port for its final voyage '
                        '29 years later.'
                    ),
                },
                {
                    'url': 'https://example.org/ship-capacity',
                    'evidence': (
                        'The first route carried 62 containers; lifetime '
                        'cargo total was 4.8 million tonnes.'
                    ),
                },
            ],
            'scenes': scenes,
            'narration': complete_narration,
            'tts_narration': complete_narration,
            'visual_queries': [query for row in queries for query in row],
            'ai_scenes': [],
            'director_qc': [],
        }
        generated = {
            'scenes': [
                {
                    'position': position,
                    'narration': narration,
                    'visual_queries': queries[position],
                    'ai_prompt': None,
                }
                for position, narration in enumerate(narrations)
            ],
        }
        client = FakeClient([
            generated,
            critic_payload(
                stock_positions=tuple(range(6)),
                scene_count=6,
            ),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            topic=(
                'Konuşma metni tam olarak şu altı cümle olsun: '
                f'“{complete_narration}”'
            ),
            content_style='documentary',
        )

        self.assertEqual(result['narration'], complete_narration)
        self.assertIn('1997', result['narration'])
        self.assertIn('62', result['narration'])
        self.assertIn('4,8 milyon', result['narration'])
        self.assertIn('29 yıl', result['narration'])
        self.assertTrue(
            result['stock_scene_qc']['documentary_broll_semantics']
        )
        self.assertEqual(
            result['stock_scene_qc']['content_style'],
            'documentary',
        )
        writer_input = client.responses.calls[0]['input']
        critic_input = client.responses.calls[1]['input']
        self.assertIn('DOCUMENTARY B-ROLL EXCEPTION', writer_input)
        self.assertIn(
            'without making the numeral readable in the clip',
            writer_input,
        )
        self.assertIn('DOCUMENTARY B-ROLL SEMANTICS ARE ACTIVE', critic_input)
        self.assertIn(
            'exact value is explicitly supported by supplied source evidence',
            critic_input,
        )
        self.assertIn('wrong or contradictory subject', critic_input)
        self.assertIn('generic wallpaper', critic_input)

    def test_documentary_broll_exception_never_overrides_critic_failures(self):
        failure_matrix = {
            'unsupported_fact': ['adds_no_new_fact'],
            'wrong_or_contradictory_era': ['queries_match_same_action'],
            'irrelevant_wallpaper': ['preserves_story_role'],
            'invisible_causal_mechanism': [
                'all_spoken_meaning_visible',
                'no_invisible_or_abstract_claim',
            ],
        }

        for case, failed_checks in failure_matrix.items():
            with self.subTest(case=case):
                rejected = critic_payload({0: failed_checks})
                client = FakeClient([
                    valid_generator_payload(),
                    copy.deepcopy(rejected),
                    valid_generator_payload((0,)),
                    copy.deepcopy(rejected),
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
                        content_style='documentary',
                    )

                self.assertEqual(len(client.responses.calls), 4)
                retry_input = client.responses.calls[2]['input']
                for failed_check in failed_checks:
                    self.assertIn(failed_check, retry_input)

    def test_non_documentary_style_keeps_literal_stock_semantics(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
            content_style='technology',
        )

        self.assertFalse(
            result['stock_scene_qc']['documentary_broll_semantics']
        )
        self.assertIn(
            'NO DOCUMENTARY B-ROLL EXCEPTION: every spoken claim must be '
            'directly visible in the same stock clip',
            client.responses.calls[0]['input'],
        )
        self.assertIn(
            'DOCUMENTARY B-ROLL SEMANTICS ARE NOT ACTIVE',
            client.responses.calls[1]['input'],
        )

    def test_ordinary_quoted_forbidden_examples_are_not_narration_locks(self):
        brief = (
            'Kıyafet ve kamera bilgisini seslendirme; örneğin '
            '“arkadan izliyor” veya “koyu lacivert tişörtlü Mert” deme.'
        )

        self.assertIsNone(
            director_module._exact_narration_lock_from_brief(brief)
        )

    def test_equivalent_english_exact_narration_lock_is_honored(self):
        package = make_ai_first_five_scene_package()
        block = self._complete_narration(package)
        client = FakeClient([
            self._locked_ai_first_payload(package),
            critic_payload(stock_positions=(0, 2), scene_count=5),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            topic=f'The spoken narration must be exactly: "{block}"',
        )

        self.assertEqual(result['narration'], block)

    def test_exact_narration_lock_mismatch_fails_before_model_calls(self):
        package = make_ai_first_five_scene_package()
        block = self._complete_narration(package)
        client = FakeClient([])

        with self.assertRaisesRegex(
            RuntimeError,
            'Exact spoken-narration lock does not match',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                topic=(
                    'Seslendirme metni tam olarak şöyle okunsun: '
                    f'«{block} Fazladan cümle.»'
                ),
            )

        self.assertEqual(client.responses.calls, [])

    def test_stock_writer_output_cannot_change_exact_locked_narration(self):
        package = make_ai_first_five_scene_package()
        package['scenes'][0]['ai_prompt'] = (
            'same woman looking at the silent phone at the winter bus stop'
        )
        package['scenes'][3]['ai_prompt'] = None
        package['scenes'][4]['ai_prompt'] = None
        package['ai_scenes'] = [
            scene['ai_prompt']
            for scene in package['scenes']
            if scene.get('ai_prompt')
        ]
        block = self._complete_narration(package)
        generated = {
            'scenes': [
                {
                    'position': position,
                    'narration': (
                        package['scenes'][position]['narration'] + ' Bugün.'
                        if position == 3
                        else package['scenes'][position]['narration']
                    ),
                    'visual_queries': copy.deepcopy(
                        package['scenes'][position]['visual_queries']
                    ),
                    'ai_prompt': None,
                }
                for position in (2, 3, 4)
            ],
        }
        client = FakeClient([
            generated,
            critic_payload(stock_positions=(2, 3, 4), scene_count=5),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            topic=(
                'Anlatım metni aynen şu olsun: '
                f'“{block}”'
            ),
        )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(result['narration'], block)
        self.assertEqual(
            result['scenes'][3]['narration'],
            package['scenes'][3]['narration'],
        )
        self.assertNotIn(
            package['scenes'][3]['narration'] + ' Bugün.',
            client.responses.calls[1]['input'],
        )
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 1)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 1)

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
        self.assertNotEqual(
            result['scenes'][0]['narration'],
            original['scenes'][0]['narration'],
        )
        self.assertEqual(result['ai_scene_count'] if 'ai_scene_count' in result else 3, 3)
        self.assertEqual(len(result['ai_scenes']), 3)

    def test_complete_long_brief_reaches_writer_and_critic_without_tail_truncation(self):
        marker = (
            'No smiling face, bright or readable screen, logo, extra laptop, '
            'office, advice or on-screen text.'
        )
        brief = ('single-room continuity context ' * 48) + marker
        self.assertGreater(len(brief), 1200)
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(),
        ])

        _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
            topic=brief,
        )

        writer_input = client.responses.calls[0]['input']
        critic_input = client.responses.calls[1]['input']
        self.assertIn(marker, writer_input)
        self.assertIn(marker, critic_input)
        self.assertIn('requested_brief', writer_input)
        self.assertIn(
            'all_explicit_brief_constraints_preserved',
            critic_input,
        )
        self.assertIn('“sıkışan ısı fanı hızlandırıyor”', writer_input)
        self.assertIn('“arkadan izliyor”', writer_input)
        self.assertIn('production-only metadata to be spoken', critic_input)
        self.assertIn('“açılan boşluk fanı yavaşlatıyor”', critic_input)
        self.assertIn('“koyu lacivert tişörtlü Mert ... arkadan izliyor”', critic_input)

    def test_explicit_brief_constraint_rejection_is_fatal_before_media(self):
        verdict = critic_payload(
            story_failures=['all_explicit_brief_constraints_preserved']
        )
        client = FakeClient([
            valid_generator_payload(),
            verdict,
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'all_explicit_brief_constraints_preserved',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                topic='No smiling face or readable screen.',
                allow_explicit_brief_repair=False,
            )

    def test_oversized_brief_fails_instead_of_silently_truncating_constraints(self):
        client = FakeClient([])
        with self.assertRaisesRegex(
            RuntimeError,
            'too long for complete pre-media constraint review',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                topic='x' * (director_module._MAX_STORY_BRIEF_CHARS + 1),
            )
        self.assertEqual(client.responses.calls, [])

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

    def test_all_ai_story_skips_empty_writer_but_keeps_whole_story_critic(self):
        package = make_ai_first_five_scene_package()
        original = copy.deepcopy(package)
        for position, scene in enumerate(package['scenes']):
            scene['ai_prompt'] = (
                scene.get('ai_prompt')
                or f'Photorealistic continuous scene {position}, no text.'
            )
        package['ai_scenes'] = [
            scene['ai_prompt'] for scene in package['scenes']
        ]
        client = FakeClient([
            critic_payload(stock_positions=(), scene_count=5),
        ])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 1)
        self.assertIn('tools', client.responses.calls[0])
        self.assertEqual(result['stock_scene_qc']['target_positions'], [])
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 0)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 1)
        self.assertEqual(result['scenes'], package['scenes'])
        self.assertNotEqual(original['scenes'], package['scenes'])

    @patch('app.services.gemini_critic.httpx.post')
    def test_all_ai_story_supports_enabled_optional_gemini_attestation(
        self, gemini_post
    ):
        package = make_ai_first_five_scene_package()
        for position, scene in enumerate(package['scenes']):
            scene['ai_prompt'] = (
                scene.get('ai_prompt')
                or f'Photorealistic continuous scene {position}, no text.'
            )
        package['ai_scenes'] = [
            scene['ai_prompt'] for scene in package['scenes']
        ]
        verdict = critic_payload(stock_positions=(), scene_count=5)
        client = FakeClient([verdict])
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(verdict)

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
        )

        self.assertEqual(len(client.responses.calls), 1)
        self.assertEqual(gemini_post.call_count, 1)
        self.assertEqual(
            result['stock_scene_qc']['gemini_critic'][
                'reviewed_scene_count'
            ],
            0,
        )

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

    def test_documentary_may_end_on_same_airplane_exterior_establishing_coda(
        self,
    ):
        package = make_documentary_airplane_coda_package()
        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 4,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': None,
        })
        verdict = critic_payload(
            ending_failures=[
                'same_immediate_location',
                'continuous_visible_action_chain',
            ],
            stock_positions=(0, 2, 4),
            scene_count=5,
            documentary_exterior_coda_valid=True,
        )
        verdict['ending_pair']['location_anchor'] = (
            'same passenger airplane event, cabin interior to exterior takeoff'
        )
        verdict['ending_pair']['reason'] = (
            'The exterior takeoff is an establishing coda for the same '
            'passenger airplane and flight event, with no new subject.'
        )
        client = FakeClient([generated, verdict])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            content_style='documentary',
        )

        ending_review = result['stock_scene_qc']['ending_pair_review']
        self.assertTrue(ending_review['accepted'])
        self.assertTrue(
            ending_review[
                'documentary_exterior_establishing_coda_exception'
            ]
        )
        critic_input = client.responses.calls[1]['input']
        self.assertIn(
            'documentary_exterior_establishing_coda_satisfied',
            critic_input,
        )
        self.assertIn(
            'product demonstrations, tutorials, procedures',
            critic_input,
        )

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_may_attest_only_the_scoped_exterior_coda_false_checks(
        self,
        gemini_post,
    ):
        package = make_documentary_airplane_coda_package()
        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 4,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': None,
        })
        verdict = critic_payload(
            ending_failures=[
                'same_immediate_location',
                'continuous_visible_action_chain',
            ],
            stock_positions=(0, 2, 4),
            scene_count=5,
            documentary_exterior_coda_valid=True,
        )
        verdict['ending_pair']['location_anchor'] = (
            'same passenger airplane event, cabin interior to exterior takeoff'
        )
        verdict['ending_pair']['reason'] = (
            'The exterior takeoff preserves the same aircraft and flight event.'
        )
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(copy.deepcopy(verdict))
        client = FakeClient([generated, copy.deepcopy(verdict)])

        result = _repair_short_stock_scenes(
            client,
            package,
            'Turkish',
            0.5,
            content_style='documentary',
        )

        self.assertTrue(result['stock_scene_qc']['gemini_critic']['accepted'])
        trusted_instruction = gemini_post.call_args.kwargs['json'][
            'systemInstruction'
        ]['parts'][0]['text']
        self.assertIn(
            'documentary_exterior_establishing_coda_satisfied',
            trusted_instruction,
        )
        self.assertIn(
            'At most same_immediate_location and '
            'continuous_visible_action_chain may then be false',
            trusted_instruction,
        )

    def test_exterior_coda_exception_is_not_available_to_story_style(self):
        package = make_documentary_airplane_coda_package()
        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 4,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': None,
        })
        client = FakeClient([
            generated,
            critic_payload(
                ending_failures=[
                    'same_immediate_location',
                    'continuous_visible_action_chain',
                ],
                stock_positions=(0, 2, 4),
                scene_count=5,
                documentary_exterior_coda_valid=True,
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'AI-routed short-preview ending before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                content_style='story',
            )

    def test_exterior_coda_cannot_hide_identity_break_or_demo_completion(self):
        package = make_documentary_airplane_coda_package()
        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 4,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': None,
        })
        identity_break = critic_payload(
            ending_failures=[
                'same_immediate_location',
                'continuous_visible_action_chain',
                'same_actor_or_object_thread',
            ],
            stock_positions=(0, 2, 4),
            scene_count=5,
            documentary_exterior_coda_valid=True,
        )
        client = FakeClient([generated, identity_break])

        with self.assertRaisesRegex(
            RuntimeError,
            'same_actor_or_object_thread',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                content_style='documentary',
            )

        demo_completion = critic_payload(
            ending_failures=[
                'same_immediate_location',
                'continuous_visible_action_chain',
            ],
            stock_positions=(0, 2, 4),
            scene_count=5,
            documentary_exterior_coda_valid=False,
        )
        client = FakeClient([generated, demo_completion])
        with self.assertRaisesRegex(
            RuntimeError,
            'documentary_exterior_establishing_coda_satisfied',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                content_style='explainer',
            )

    def test_explicit_ai_macro_insert_may_return_to_same_enclosing_setting(self):
        topic = '''Tam olarak beş sahne kullan.
1) STOCK: Elif aynı telefonla otobüs durağında bekler.
2) AI: Aynı telefonun soğukta kapanmasını göster.
3) STOCK: Elif telefonu aynı durağın bankında montuna koyar.
4) AI: Aynı telefon pilinin mekanizmasını makro kesitte göster.
5) AI: Aynı otobüs durağı bankına ve aynı telefona hemen dön.'''
        verdict = critic_payload(
            ending_failures=['same_immediate_location'],
            stock_positions=(0, 2),
            scene_count=5,
            technical_insert_return_valid=True,
        )
        verdict['ending_pair']['location_anchor'] = (
            'same enclosing winter bus stop bench and same phone'
        )
        verdict['ending_pair']['reason'] = (
            'The camera enters the same phone battery, then immediately '
            'returns to that phone at the same bus stop bench.'
        )
        client = FakeClient([
            valid_ai_first_generator_payload(),
            verdict,
        ])

        result = _repair_short_stock_scenes(
            client,
            make_explicit_ai_macro_return_package(),
            'Turkish',
            0.5,
            topic=topic,
        )

        ending_review = result['stock_scene_qc']['ending_pair_review']
        self.assertTrue(ending_review['accepted'])
        self.assertTrue(ending_review['technical_insert_return_exception'])

    @patch('app.services.gemini_critic.httpx.post')
    def test_gemini_may_attest_only_the_scoped_macro_insert_location_false(
        self,
        gemini_post,
    ):
        topic = '''Tam olarak beş sahne kullan.
1) STOCK: Elif aynı telefonla otobüs durağında bekler.
2) AI: Aynı telefonun soğukta kapanmasını göster.
3) STOCK: Elif telefonu aynı durağın bankında montuna koyar.
4) AI: Aynı telefon pilinin mekanizmasını makro kesitte göster.
5) AI: Aynı otobüs durağı bankına ve aynı telefona hemen dön.'''
        verdict = critic_payload(
            ending_failures=['same_immediate_location'],
            stock_positions=(0, 2),
            scene_count=5,
            technical_insert_return_valid=True,
        )
        verdict['ending_pair']['location_anchor'] = (
            'same enclosing winter bus stop bench and same phone'
        )
        verdict['ending_pair']['reason'] = (
            'The camera enters the same phone battery, then immediately '
            'returns to that phone at the same bus stop bench.'
        )
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(copy.deepcopy(verdict))
        client = FakeClient([
            valid_ai_first_generator_payload(),
            copy.deepcopy(verdict),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_explicit_ai_macro_return_package(),
            'Turkish',
            0.5,
            topic=topic,
        )

        self.assertTrue(result['stock_scene_qc']['gemini_critic']['accepted'])
        trusted_instruction = gemini_post.call_args.kwargs['json'][
            'systemInstruction'
        ]['parts'][0]['text']
        self.assertIn(
            'brief AI-routes both final beats',
            trusted_instruction,
        )
        self.assertIn(
            'Only same_immediate_location may then be false',
            trusted_instruction,
        )
        self.assertIn(
            'Set the contract field false for a stock-routed ending',
            trusted_instruction,
        )

    @patch('app.services.gemini_critic.httpx.post')
    def test_macro_insert_scope_does_not_allow_other_gemini_false_checks(
        self,
        gemini_post,
    ):
        topic = '''Tam olarak beş sahne kullan.
1) STOCK: Elif aynı telefonla otobüs durağında bekler.
2) AI: Aynı telefonun soğukta kapanmasını göster.
3) STOCK: Elif telefonu aynı durağın bankında montuna koyar.
4) AI: Aynı telefon pilinin mekanizmasını makro kesitte göster.
5) AI: Aynı otobüs durağı bankına ve aynı telefona hemen dön.'''
        openai_verdict = critic_payload(
            ending_failures=['same_immediate_location'],
            stock_positions=(0, 2),
            scene_count=5,
            technical_insert_return_valid=True,
        )
        openai_verdict['ending_pair']['location_anchor'] = (
            'same enclosing winter bus stop bench and same phone'
        )
        openai_verdict['ending_pair']['reason'] = (
            'The camera enters the same phone battery, then immediately '
            'returns to that phone at the same bus stop bench.'
        )
        gemini_verdict = copy.deepcopy(openai_verdict)
        gemini_verdict['ending_pair']['same_actor_or_object_thread'] = False
        config_stub.settings.gemini_critic_enabled = True
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        gemini_post.return_value = FakeGeminiResponse(gemini_verdict)
        client = FakeClient([
            valid_ai_first_generator_payload(),
            openai_verdict,
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            r'Gemini critic rejected.*same_actor_or_object_thread',
        ):
            _repair_short_stock_scenes(
                client,
                make_explicit_ai_macro_return_package(),
                'Turkish',
                0.5,
                topic=topic,
            )

    def test_explicit_macro_return_still_requires_critic_attestation(self):
        topic = '''Tam olarak beş sahne kullan.
1) STOCK: Elif aynı telefonla otobüs durağında bekler.
2) AI: Aynı telefonun soğukta kapanmasını göster.
3) STOCK: Elif telefonu aynı durağın bankında montuna koyar.
4) AI: Aynı telefon pilinin mekanizmasını makro kesitte göster.
5) AI: Aynı otobüs durağı bankına ve aynı telefona hemen dön.'''
        client = FakeClient([
            valid_ai_first_generator_payload(),
            critic_payload(
                stock_positions=(0, 2),
                scene_count=5,
                technical_insert_return_valid=False,
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'AI-routed short-preview ending before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                make_explicit_ai_macro_return_package(),
                'Turkish',
                0.5,
                topic=topic,
            )

    def test_macro_return_claim_without_explicit_numbered_route_fails_closed(self):
        client = FakeClient([
            valid_ai_first_generator_payload(),
            critic_payload(
                ending_failures=['same_immediate_location'],
                stock_positions=(0, 2),
                scene_count=5,
                technical_insert_return_valid=True,
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'AI-routed short-preview ending before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                make_ai_first_five_scene_package(),
                'Turkish',
                0.5,
                topic='A macro insert eventually returns to the same object.',
            )

    def test_explicit_ai_return_cannot_bypass_a_stock_routed_final_candidate(self):
        topic = '''Tam olarak beş sahne kullan.
1) STOCK: Elif aynı telefonla otobüs durağında bekler.
2) AI: Aynı telefonun soğukta kapanmasını göster.
3) STOCK: Elif telefonu aynı durağın bankında montuna koyar.
4) AI: Aynı telefon pilinin mekanizmasını makro kesitte göster.
5) AI: Aynı otobüs durağı bankına ve aynı telefona hemen dön.'''
        package = make_explicit_ai_macro_return_package()
        package['scenes'][4]['ai_prompt'] = None
        package['ai_scenes'] = [
            scene['ai_prompt']
            for scene in package['scenes']
            if scene.get('ai_prompt')
        ]
        generated = valid_ai_first_generator_payload()
        generated['scenes'].append({
            'position': 4,
            'narration': package['scenes'][4]['narration'],
            'visual_queries': package['scenes'][4]['visual_queries'],
            'ai_prompt': None,
        })
        client = FakeClient([
            generated,
            critic_payload(
                ending_failures=['same_immediate_location'],
                stock_positions=(0, 2, 4),
                scene_count=5,
                technical_insert_return_valid=True,
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'AI-routed short-preview ending before paid media',
        ):
            _repair_short_stock_scenes(
                client,
                package,
                'Turkish',
                0.5,
                topic=topic,
            )

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
        self.assertNotIn('tools', client.responses.calls[2])
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
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                'A specific requested phone topic',
            )
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn('"critic_calls":1', str(error.exception))
        self.assertIn(
            '"requested_topic": "A specific requested phone topic"',
            client.responses.calls[1]['input'],
        )

    def test_explicit_brief_rejection_requests_one_bounded_full_repair(self):
        verdict = critic_payload(
            story_failures=['all_explicit_brief_constraints_preserved']
        )
        verdict['story_review']['reason'] = (
            'Scene 1 omits unbranded and Scene 2 omits 14-inch from ai_prompt.'
        )
        client = FakeClient([
            valid_generator_payload(),
            verdict,
        ])

        with self.assertRaises(_WholeStoryRepairRequired) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                'Every AI prompt must repeat unbranded matte silver 14-inch laptop.',
            )

        self.assertEqual(
            error.exception.failed_checks,
            ['all_explicit_brief_constraints_preserved'],
        )
        self.assertIn('unbranded', error.exception.evidence)
        self.assertEqual(len(client.responses.calls), 2)

    def test_second_explicit_brief_rejection_fails_closed(self):
        client = FakeClient([
            valid_generator_payload(),
            critic_payload(
                story_failures=['all_explicit_brief_constraints_preserved']
            ),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            'incoherent short-preview story before paid media',
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
                'Every AI prompt must repeat unbranded matte silver 14-inch laptop.',
                allow_explicit_brief_repair=False,
            )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn('"critic_calls":1', str(error.exception))

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

    def test_generator_retry_does_not_consume_critic_protocol_retry(self):
        secret = 'critic-retry-secret-must-not-leak'
        config_stub.settings.gemini_api_key = secret
        first = valid_generator_payload()
        first['scenes'][0]['narration'] = (
            'Genç adam evde masadaki telefonunu; tek eliyle alır.'
        )
        invalid_critic = critic_payload()
        invalid_critic['story_review']['unexpected_field'] = 'schema drift'
        client = FakeClient([
            first,
            valid_generator_payload((0,)),
            invalid_critic,
            critic_payload(),
        ])

        result = _repair_short_stock_scenes(
            client,
            make_short_package(),
            'Turkish',
            0.5,
        )

        self.assertEqual(result['stock_scene_qc']['generator_calls'], 2)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 2)
        first_critic_call = client.responses.calls[2]
        second_critic_call = client.responses.calls[3]
        self.assertEqual(first_critic_call, second_critic_call)
        self.assertNotIn(secret, json.dumps(first_critic_call))
        self.assertNotIn(secret, json.dumps(second_critic_call))

    def test_two_invalid_critic_contracts_fail_without_generator_rewrite(self):
        invalid_critic = critic_payload()
        invalid_critic['story_review']['unexpected_field'] = 'schema drift'
        client = FakeClient([
            valid_generator_payload(),
            copy.deepcopy(invalid_critic),
            copy.deepcopy(invalid_critic),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            r'"generator_calls":1,"critic_calls":2',
        ) as error:
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertIn(
            'whole-story critic returned the wrong fields',
            str(error.exception),
        )
        self.assertEqual(len(client.responses.calls), 3)
        self.assertEqual(
            client.responses.calls[1],
            client.responses.calls[2],
        )

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
            copy.deepcopy(invalid),
            copy.deepcopy(invalid),
        ])

        with self.assertRaisesRegex(
            RuntimeError,
            r'"generator_calls":1,"critic_calls":2',
        ):
            _repair_short_stock_scenes(
                client,
                make_short_package(),
                'Turkish',
                0.5,
            )
        self.assertEqual(len(client.responses.calls), 3)

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
            'version': director_module._SHORT_STORY_QC_VERSION,
            'requested_topic': 'one useful phone story',
            'story_review_accepted': True,
            'ending_pair_accepted': True,
        }
        result['short_story_qc']['fingerprint'] = _short_story_fingerprint(result)
        self.assertTrue(short_story_package_is_approved(result))
        self.assertEqual(
            result['stock_scene_qc']['version'],
            director_module._STOCK_SCENE_QC_VERSION,
        )
        self.assertEqual(
            result['stock_scene_qc']['gemini_critic']['contract'],
            director_module._STORY_STOCK_CONTRACT,
        )

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
            'version': director_module._STOCK_SCENE_QC_VERSION,
            'story_review': {'accepted': True},
            'ending_pair_review': {'accepted': True},
        }
        evidence = (
            'scene 4: "Koyu lacivert tişörtlü Mert dizisini arkadan '
            'izliyor" verbalizes wardrobe and camera metadata that belongs '
            'only in the visual fields.'
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
        self.assertEqual(
            [
                call.kwargs['content_style']
                for call in repair_stock_scenes.call_args_list
            ],
            ['documentary', 'documentary'],
        )
        self.assertTrue(all(
            call.kwargs['allow_legacy_short_budget'] is False
            for call in repair_stock_scenes.call_args_list
        ))
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
        self.assertGreaterEqual(result['narration_word_count'], 52)
        self.assertLessEqual(result['narration_word_count'], 60)

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_explicit_brief_failure_gets_one_full_story_repair_and_rereview(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        original = make_coherent_battery_package()
        corrected = copy.deepcopy(original)
        corrected['scenes'][2]['ai_prompt'] = (
            'standalone shot of the same unbranded matte silver 14-inch phone '
            'battery mechanism, no logo or readable text'
        )

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
            'version': director_module._STOCK_SCENE_QC_VERSION,
            'story_review': {'accepted': True},
            'ending_pair_review': {'accepted': True},
        }
        evidence = (
            'all_explicit_brief_constraints_preserved; Scene 2 ai_prompt '
            'omits required "unbranded" and "14-inch" identity attributes.'
        )
        run_director.side_effect = [
            director_payload(original),
            director_payload(corrected),
        ]
        repair_stock_scenes.side_effect = [
            _WholeStoryRepairRequired(
                ['all_explicit_brief_constraints_preserved'],
                evidence,
            ),
            approved,
        ]
        openai_class.return_value = object()

        result = direct_and_qc(
            original,
            (
                'Tam altı sahne kullan. Her AI sahnesinde aynı markasız mat '
                'gümüş 14 inç cihazı koru.'
            ),
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
        self.assertFalse(
            second_review.kwargs['allow_explicit_brief_repair']
        )
        correction_context = run_director.call_args_list[1].args[1]
        self.assertEqual(
            correction_context['correction_attempt'],
            'whole_story_critic',
        )
        feedback = correction_context['narration_quality_issues'][0]
        self.assertIn('all_explicit_brief_constraints_preserved', feedback)
        self.assertIn('unbranded', feedback)
        self.assertIn('14-inch', feedback)
        self.assertIn('scene-by-scene checklist', feedback)
        self.assertIn('brand state', feedback)
        self.assertGreaterEqual(result['narration_word_count'], 52)
        self.assertLessEqual(result['narration_word_count'], 60)


class WholeStoryRepairDiagnosticTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.openai_api_key = 'test-openai-key'
        config_stub.settings.openai_model = 'test-model'
        config_stub.settings.gemini_critic_enabled = False

    def test_diagnostics_redact_untrusted_check_and_issue_text(self):
        secret = 'diagnostic-secret-must-not-leak'
        source_url = 'https://private.example.invalid/sensitive-source'

        diagnostics = director_module._whole_story_repair_diagnostics(
            failed_checks=[
                'natural_spoken_language',
                f'{secret} {source_url}',
            ],
            words=12,
            min_words=45,
            max_words=51,
            scene_count=4,
            target_scenes=5,
            exact_scene_count=True,
            ai_scene_count=4,
            preview_ai_limit=3,
            short_editorial_issues=[
                f'scene 0 uses TTS-unsafe raw term(s): {secret}',
            ],
        )

        encoded = json.dumps(diagnostics, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertNotIn(source_url, encoded)
        self.assertEqual(
            diagnostics['critic_failed_checks'],
            ['natural_spoken_language', 'unknown_story_check'],
        )
        self.assertEqual(
            diagnostics['failed_deterministic_gates'],
            [
                'narration_word_count',
                'scene_count',
                'ai_scene_count',
                'short_editorial_issues',
            ],
        )
        self.assertEqual(
            diagnostics['post_repair_shape'][
                'short_editorial_issue_categories'
            ],
            ['tts_unsafe_raw_terms'],
        )

    @patch('app.services.director._repair_short_stock_scenes')
    @patch('app.services.director._run_director')
    @patch('app.services.director.OpenAI')
    def test_failed_repair_reports_only_bounded_shape_diagnostics(
        self,
        openai_class,
        run_director,
        repair_stock_scenes,
    ):
        secret = 'whole-story-secret-must-not-leak'
        source_url = 'https://private.example.invalid/source-with-token'
        raw_prompt = f'RAW MODEL PROMPT {secret} {source_url}'
        original = make_coherent_battery_package()
        original['sources'][0] = {
            'url': source_url,
            'evidence': f'private source evidence {secret}',
        }
        corrected = copy.deepcopy(original)
        corrected['scenes'] = corrected['scenes'][:5]
        for scene in corrected['scenes']:
            scene['narration'] = "QR'ın GPS'i OLED'in içinde."
            scene['tts_text'] = scene['narration']
            scene['ai_prompt'] = raw_prompt

        def director_payload(package):
            return {
                'title': package['title'],
                'thumbnail_text': package['thumbnail_text'],
                'description': package['description'],
                'scenes': copy.deepcopy(package['scenes']),
                'qc_summary': [],
            }

        evidence = (
            'all_explicit_brief_constraints_preserved; '
            f'{secret}; {source_url}; {raw_prompt}'
        )
        run_director.side_effect = [
            director_payload(original),
            director_payload(corrected),
        ]
        repair_stock_scenes.side_effect = _WholeStoryRepairRequired(
            ['all_explicit_brief_constraints_preserved'],
            evidence,
        )
        openai_class.return_value = object()

        with self.assertRaisesRegex(
            RuntimeError,
            'Whole-story critic repair violated a deterministic',
        ) as error:
            direct_and_qc(
                original,
                (
                    'Tam altı sahne kullan. Keep diagnostics private. '
                    f'{secret} {source_url}'
                ),
                0.5,
                'tr',
                {
                    'mode': 'preview',
                    'pace': 'balanced',
                    'visual_mix': 'ai_first',
                },
            )

        message = str(error.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn(source_url, message)
        self.assertNotIn('RAW MODEL PROMPT', message)
        diagnostics = json.loads(
            message.split('before paid media: ', 1)[1]
        )
        self.assertEqual(
            diagnostics['critic_failed_checks'],
            ['all_explicit_brief_constraints_preserved'],
        )
        self.assertEqual(
            diagnostics['failed_deterministic_gates'],
            [
                'narration_word_count',
                'scene_count',
                'ai_scene_count',
                'short_editorial_issues',
            ],
        )
        shape = diagnostics['post_repair_shape']
        self.assertEqual(shape['narration_word_count'], 20)
        self.assertEqual(shape['required_narration_word_range'], [52, 60])
        self.assertEqual(shape['scene_count'], 5)
        self.assertEqual(shape['target_scene_count'], 6)
        self.assertIs(shape['exact_scene_count'], True)
        self.assertEqual(shape['ai_scene_count'], 5)
        self.assertEqual(shape['max_ai_scene_count'], 4)
        self.assertGreater(shape['short_editorial_issue_count'], 0)
        self.assertIn(
            'tts_unsafe_raw_terms',
            shape['short_editorial_issue_categories'],
        )
        self.assertEqual(run_director.call_count, 2)
        self.assertEqual(repair_stock_scenes.call_count, 1)


class GeminiPlanProviderTests(unittest.TestCase):
    def setUp(self):
        config_stub.settings.studio_plan_provider = 'gemini'
        config_stub.settings.openai_api_key = ''
        config_stub.settings.openai_model = 'test-model'
        config_stub.settings.gemini_api_key = 'test-gemini-key'
        config_stub.settings.gemini_model = 'gemini-3.1-pro-preview'
        config_stub.settings.gemini_critic_enabled = False

    def tearDown(self):
        config_stub.settings.studio_plan_provider = 'openai'
        config_stub.settings.openai_api_key = 'test-openai-key'
        config_stub.settings.gemini_api_key = ''

    def _director_payload(self):
        package = make_ai_first_five_scene_package()
        return {
            'title': package['title'],
            'thumbnail_text': package['thumbnail_text'],
            'description': package['description'],
            'scenes': [
                {
                    'narration': scene['narration'],
                    'visual_queries': scene['visual_queries'],
                    'ai_prompt': scene['ai_prompt'],
                    'pace': scene['pace'],
                    'transition': scene['transition'],
                }
                for scene in package['scenes']
            ],
            'qc_summary': ['Kept one causal story.'],
        }

    def test_director_uses_explicit_schema_without_search(self):
        compact = make_ai_first_five_scene_package()
        payload = self._director_payload()

        with patch.object(
            director_module,
            'generate_gemini_json',
            return_value=payload,
        ) as generate:
            result = director_module._run_director(
                None,
                compact,
                'Soğukta telefon neden kapanır?',
                'Turkish',
                0.5,
                48,
                45,
                51,
                5,
                {
                    'mode': 'preview',
                    'pace': 'balanced',
                    'visual_mix': 'ai_first',
                },
            )

        self.assertEqual(result, payload)
        request = generate.call_args
        self.assertFalse(request.kwargs['google_search'])
        self.assertEqual(request.kwargs['thinking_level'], 'low')
        schema = request.kwargs['json_schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(
            set(schema['required']),
            {
                'title',
                'thumbnail_text',
                'description',
                'scenes',
                'qc_summary',
            },
        )
        self.assertEqual(schema['properties']['scenes']['minItems'], 4)
        self.assertEqual(schema['properties']['scenes']['maxItems'], 6)
        scene = schema['properties']['scenes']['items']
        self.assertEqual(
            scene['properties']['pace']['enum'],
            ['fast', 'normal', 'slow'],
        )
        self.assertEqual(
            scene['properties']['transition']['enum'],
            ['cut', 'match', 'dip'],
        )
        self.assertEqual(
            scene['properties']['ai_prompt']['type'],
            ['string', 'null'],
        )
        self.assertNotIn(
            config_stub.settings.gemini_api_key,
            request.args[0] + json.dumps(schema),
        )

    def test_direct_and_qc_never_constructs_openai_for_gemini(self):
        with patch.object(director_module, 'OpenAI') as openai_class, patch.object(
            director_module,
            '_run_director',
            side_effect=RuntimeError('stop-after-provider-selection'),
        ) as run_director, self.assertRaisesRegex(
            RuntimeError,
            'stop-after-provider-selection',
        ):
            direct_and_qc(
                make_coherent_battery_package(),
                'Soğukta telefon pili neden düşer?',
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

        openai_class.assert_not_called()
        self.assertIsNone(run_director.call_args.args[0])

    def test_stock_writer_and_primary_critic_use_schemas_without_double_veto(self):
        with patch.object(
            director_module,
            'generate_gemini_json',
            side_effect=[valid_generator_payload(), critic_payload()],
        ) as generate, patch.object(
            director_module,
            'run_optional_gemini_critic',
        ) as optional_critic:
            result = _repair_short_stock_scenes(
                None,
                make_short_package(),
                'Turkish',
                0.5,
                'one useful phone story',
            )

        self.assertEqual(generate.call_count, 2)
        optional_critic.assert_not_called()
        writer_request, critic_request = generate.call_args_list
        self.assertFalse(writer_request.kwargs['google_search'])
        writer_schema = writer_request.kwargs['json_schema']
        writer_scenes = writer_schema['properties']['scenes']
        self.assertEqual(writer_scenes['minItems'], 3)
        self.assertEqual(writer_scenes['maxItems'], 3)
        writer_row = writer_scenes['items']
        self.assertEqual(
            writer_row['properties']['position']['enum'],
            [0, 4, 5],
        )
        self.assertEqual(
            writer_row['properties']['ai_prompt'],
            {'type': 'null'},
        )
        self.assertFalse(critic_request.kwargs['google_search'])
        critic_schema = critic_request.kwargs['json_schema']
        self.assertEqual(
            set(critic_schema['required']),
            {'story_review', 'ending_pair', 'scenes'},
        )
        self.assertEqual(
            critic_schema['properties']['story_review']['properties'][
                'not_fact_montage'
            ]['type'],
            'boolean',
        )
        attestation = result['stock_scene_qc']['gemini_critic']
        self.assertTrue(attestation['accepted'])
        self.assertEqual(attestation['reviewed_scene_count'], 3)
        self.assertEqual(attestation['model'], 'gemini-3.1-pro-preview')

    def test_all_ai_story_uses_zero_item_critic_schema_without_writer_call(self):
        package = make_ai_first_five_scene_package()
        for position, scene in enumerate(package['scenes']):
            scene['ai_prompt'] = (
                scene.get('ai_prompt')
                or f'Photorealistic continuous scene {position}, no text.'
            )
        package['ai_scenes'] = [
            scene['ai_prompt'] for scene in package['scenes']
        ]
        verdict = critic_payload(stock_positions=(), scene_count=5)

        with patch.object(
            director_module,
            'generate_gemini_json',
            return_value=verdict,
        ) as generate, patch.object(
            director_module,
            'run_optional_gemini_critic',
        ) as optional_critic:
            result = _repair_short_stock_scenes(
                None,
                package,
                'Turkish',
                0.5,
            )

        self.assertEqual(generate.call_count, 1)
        optional_critic.assert_not_called()
        request = generate.call_args
        schema = request.kwargs['json_schema']
        scene_schema = schema['properties']['scenes']
        self.assertEqual(scene_schema['minItems'], 0)
        self.assertEqual(scene_schema['maxItems'], 0)
        self.assertEqual(result['stock_scene_qc']['target_positions'], [])
        self.assertEqual(result['stock_scene_qc']['generator_calls'], 0)
        self.assertEqual(result['stock_scene_qc']['critic_calls'], 1)
        self.assertEqual(
            result['stock_scene_qc']['gemini_critic'][
                'reviewed_scene_count'
            ],
            0,
        )

    def test_false_critic_boolean_is_a_semantic_rejection_not_schema_failure(self):
        verdict = critic_payload(story_failures=['not_fact_montage'])
        with patch.object(
            director_module,
            'generate_gemini_json',
            side_effect=[valid_generator_payload(), verdict],
        ) as generate, self.assertRaisesRegex(
            RuntimeError,
            'incoherent short-preview story before paid media',
        ) as error:
            _repair_short_stock_scenes(
                None,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(generate.call_count, 2)
        self.assertIn('not_fact_montage', str(error.exception))
        self.assertNotIn('invalid JSON', str(error.exception))

    def test_invalid_primary_critic_retry_reuses_immutable_request(self):
        generation_error = director_module.GeminiGenerationError(
            'Gemini response failed schema validation'
        )
        with patch.object(
            director_module,
            'generate_gemini_json',
            side_effect=[
                valid_generator_payload(),
                generation_error,
                generation_error,
            ],
        ) as generate, self.assertRaisesRegex(
            RuntimeError,
            r'"generator_calls":1,"critic_calls":2',
        ):
            _repair_short_stock_scenes(
                None,
                make_short_package(),
                'Turkish',
                0.5,
            )

        self.assertEqual(generate.call_count, 3)
        first_critic = generate.call_args_list[1]
        second_critic = generate.call_args_list[2]
        self.assertEqual(first_critic.args, second_critic.args)
        self.assertEqual(first_critic.kwargs, second_critic.kwargs)
        self.assertFalse(first_critic.kwargs['retry_once'])

    def test_invalid_provider_fails_before_model_construction(self):
        config_stub.settings.studio_plan_provider = 'automatic'
        with patch.object(director_module, 'OpenAI') as openai_class, patch.object(
            director_module,
            'generate_gemini_json',
        ) as generate, self.assertRaisesRegex(
            RuntimeError,
            'STUDIO_PLAN_PROVIDER must be openai or gemini',
        ):
            direct_and_qc(
                make_coherent_battery_package(),
                'test',
                0.5,
                'tr',
            )

        openai_class.assert_not_called()
        generate.assert_not_called()


class ShortSpokenQualityTests(unittest.TestCase):
    def test_rejects_translated_causality_and_spoken_production_metadata(self):
        unsafe = {
            'scenes': [
                {
                    'narration': (
                        'Yorgan hava girişini kapattığı için sıkışan ısı '
                        'fanı hızlandırıyor.'
                    ),
                },
                {
                    'narration': (
                        'Mert laptopu kaldırınca açılan boşluk fanı '
                        'yavaşlatıyor.'
                    ),
                },
                {
                    'narration': (
                        'Koyu lacivert tişörtlü Mert dizisini aynı masada '
                        'arkadan izliyor.'
                    ),
                    'visual_queries': [
                        'navy shirt man watches laptop rear view',
                    ],
                },
            ],
        }
        safe = {
            'scenes': [
                {
                    'narration': (
                        'Yorgan hava girişini kapatıyor; sıcak hava içeride '
                        'kalınca fan hızlanıyor.'
                    ),
                },
                {
                    'narration': (
                        'Mert laptopu kaldırıyor; altta hava boşluğu açılınca '
                        'fan yavaşlıyor.'
                    ),
                },
                {
                    'narration': (
                        'Mert dizisine aynı masada, bu kez sessizce devam ediyor.'
                    ),
                    'visual_queries': [
                        'navy shirt man watches laptop rear view',
                    ],
                },
            ],
        }

        issues = _short_spoken_quality_issues(unsafe, 'Turkish')

        self.assertTrue(any('scene 0' in issue and 'energy' in issue for issue in issues))
        self.assertTrue(any('scene 1' in issue and 'inanimate-cause' in issue for issue in issues))
        self.assertTrue(any('scene 2' in issue and 'wardrobe' in issue for issue in issues))
        self.assertTrue(any('scene 2' in issue and 'camera direction' in issue for issue in issues))
        self.assertEqual(_short_spoken_quality_issues(safe, 'Turkish'), [])

    def test_allows_story_meaningful_colored_clothing_without_camera_language(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Kırmızı tişörtlü koşucu finişte takım arkadaşını '
                        'hemen buluyor.'
                    ),
                },
                {
                    'narration': (
                        'Lacivert montlu şüpheli kalabalığın arasına '
                        'karışıyor.'
                    ),
                },
            ],
        }

        self.assertEqual(
            _short_spoken_quality_issues(package, 'Turkish'),
            [],
        )

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

    def test_rejects_precision_seatbelt_insertion_in_ai_scene(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü sonunda güvenle yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Driver inserts a metal seat-belt latch plate into '
                        'the buckle.'
                    ),
                },
            ],
        }

        issues = _short_story_quality_issues(package, 'Turkish')

        self.assertEqual(len(issues), 1)
        self.assertIn('precision seat-belt latch insertion', issues[0])

    def test_rejects_seatbelt_payoff_without_safe_prompt_contract(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Same driver sits in the car and places both hands '
                        'on the steering wheel.'
                    ),
                },
            ],
        }

        issues = _short_story_quality_issues(package, 'Turkish')

        self.assertEqual(len(issues), 1)
        self.assertIn('without a stable visible result', issues[0])

    def test_prior_seatbelt_context_still_requires_safe_final_payoff(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Emniyet kemeri sert çekilince makaranın içinde '
                        'kilitleniyor.'
                    ),
                    'ai_prompt': (
                        'Macro view of a seat-belt retractor pawl stopping '
                        'the spool.'
                    ),
                },
                {
                    'narration': 'Sürücü artık güvenle yola hazırlanıyor.',
                    'ai_prompt': (
                        'Same driver places both hands on the steering wheel.'
                    ),
                },
            ],
        }

        issues = _short_story_quality_issues(package, 'Turkish')

        self.assertEqual(len(issues), 1)
        self.assertIn('scene 1', issues[0])
        self.assertIn('without a stable visible result', issues[0])

    def test_unrelated_already_fastened_phrase_cannot_approve_payoff(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Same driver sits in the car with his jacket already '
                        'fastened and both hands on the steering wheel.'
                    ),
                },
            ],
        }

        issues = _short_story_quality_issues(package, 'Turkish')

        self.assertEqual(len(issues), 1)
        self.assertIn('without a stable visible result', issues[0])

    def test_safe_result_cannot_hide_prompt_side_latch_insertion(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Driver inserts the metal seat-belt latch plate into '
                        'the buckle, then visibly wears the already-fastened '
                        'three-point belt across the chest.'
                    ),
                },
            ],
        }

        issues = _short_story_quality_issues(package, 'Turkish')

        self.assertEqual(len(issues), 1)
        self.assertIn('precision seat-belt latch insertion', issues[0])

    def test_allows_ai_seatbelt_payoff_with_already_fastened_belt(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Same driver visibly wears an already-fastened '
                        'three-point seat belt and places both hands on the '
                        'steering wheel, with the red buckle visible by the '
                        'hip.'
                    ),
                },
            ],
        }

        self.assertEqual(
            _short_story_quality_issues(package, 'Turkish'),
            [],
        )

    def test_retractor_lock_narration_does_not_poison_static_payoff(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kilitlenen emniyet kemerinin göğsünde '
                        'düzgün durduğunu kontrol ediyor.'
                    ),
                    'ai_prompt': (
                        'Same driver visibly wears an already-fastened '
                        'three-point seat belt across the chest, with the red '
                        'buckle visible by the hip.'
                    ),
                },
            ],
        }

        self.assertEqual(
            _short_story_quality_issues(package, 'Turkish'),
            [],
        )

    def test_english_takes_the_wheel_is_not_turkish_latch_action(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'Same driver visibly wears an already-fastened '
                        'three-point seat belt across the chest, red buckle '
                        'visible, and takes the steering wheel.'
                    ),
                },
            ],
        }

        self.assertEqual(
            _short_story_quality_issues(package, 'Turkish'),
            [],
        )

    def test_static_buckle_before_seatbelt_is_not_buckling_action(self):
        package = {
            'scenes': [
                {
                    'narration': (
                        'Sürücü kemerin göğsünde düzgün durduğunu '
                        'kontrol edip yola hazırlanıyor.'
                    ),
                    'ai_prompt': (
                        'The red buckle is visible at his hip while the same '
                        'driver visibly wears an already-fastened seat belt '
                        'across the chest.'
                    ),
                },
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
            'version': director_module._SHORT_STORY_QC_VERSION,
            'requested_topic': 'one useful phone story',
            'story_review_accepted': True,
            'ending_pair_accepted': True,
        }
        package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            package,
        )
        return package

    def _approved_exact_package(self):
        package = self._approved_package()
        locked_narration = ' '.join(
            scene['narration'] for scene in package['scenes']
        )
        brief = (
            'Konuşma metni tam olarak şöyle olsun: '
            f'“{locked_narration}”'
        )
        package['short_story_qc']['requested_topic'] = brief
        package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            package,
        )
        return package, brief

    def test_old_contract_and_exact_count_mismatch_cannot_use_approval_fast_path(self):
        old_package = self._approved_package()
        old_package['short_story_qc']['version'] = (
            director_module._SHORT_STORY_QC_VERSION - 1
        )
        old_package['stock_scene_qc']['version'] = (
            director_module._STOCK_SCENE_QC_VERSION - 1
        )
        old_package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            old_package,
        )
        self.assertFalse(
            short_story_package_is_approved(
                old_package,
                'one useful phone story',
            )
        )

        wrong_scene_count = self._approved_package()
        exact_brief = 'Tam beş sahne kullan.'
        wrong_scene_count['short_story_qc']['requested_topic'] = exact_brief
        wrong_scene_count['short_story_qc']['fingerprint'] = (
            _short_story_fingerprint(wrong_scene_count)
        )
        self.assertEqual(len(wrong_scene_count['scenes']), 6)
        self.assertFalse(
            short_story_package_is_approved(
                wrong_scene_count,
                exact_brief,
            )
        )

        oversized = self._approved_package()
        oversized_brief = 'x' * (director_module._MAX_STORY_BRIEF_CHARS + 1)
        oversized['short_story_qc']['requested_topic'] = oversized_brief
        oversized['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            oversized,
        )
        self.assertFalse(
            short_story_package_is_approved(
                oversized,
                oversized_brief,
            )
        )

    def test_exact_lock_rejects_old_fingerprint_and_recomputed_paraphrase(self):
        package, brief = self._approved_exact_package()
        self.assertTrue(short_story_package_is_approved(package, brief))
        old_valid_fingerprint = package['short_story_qc']['fingerprint']

        package['scenes'][0]['narration'] = (
            'Telefon sahibi ekrana bakıp cihazını hemen açıyor.'
        )
        package['scenes'][0]['tts_text'] = package['scenes'][0]['narration']
        package['narration'] = ' '.join(
            scene['narration'] for scene in package['scenes']
        )
        package['tts_narration'] = package['narration']

        self.assertEqual(
            package['short_story_qc']['fingerprint'],
            old_valid_fingerprint,
        )
        self.assertFalse(short_story_package_is_approved(package, brief))

        package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            package,
        )
        self.assertFalse(short_story_package_is_approved(package, brief))

    def test_exact_lock_approval_fails_closed_on_ambiguous_segmentation(self):
        package = self._approved_package()
        brief = (
            'Konuşma metni tam olarak şöyle olsun: '
            '“Dr. Ayşe telefonu açtı ve ekrana baktı. '
            'Ekran söndü, sonra yeniden aydınlandı.”'
        )
        package['short_story_qc']['requested_topic'] = brief
        package['short_story_qc']['fingerprint'] = _short_story_fingerprint(
            package,
        )

        self.assertFalse(short_story_package_is_approved(package, brief))

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

