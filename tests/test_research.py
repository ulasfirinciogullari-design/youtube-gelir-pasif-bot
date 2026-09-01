import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = object
sys.modules.setdefault('openai', openai_stub)

config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(
    openai_api_key='test-key',
    openai_model='test-model',
    studio_plan_provider='openai',
    gemini_api_key='',
    gemini_model='gemini-3.1-pro-preview',
)
sys.modules['app.config'] = config_stub

import app.services.research as research_module
from app.services.research import _max_ai_scenes, _parse_json_payload


def valid_research_payload():
    return {
        'title': 'One useful story',
        'thumbnail_text': 'One reveal',
        'description': 'Evidence-backed test story.',
        'scenes': [
            {
                'narration': f'Visible action number {index}.',
                'visual_queries': [f'visible action {index}'],
                'ai_prompt': None,
            }
            for index in range(3)
        ],
        'sources': [
            {
                'url': 'https://example.com/evidence',
                'evidence': 'This concrete evidence sentence supports the central causal reveal.',
            },
            {
                'url': 'https://example.org/second-evidence',
                'evidence': 'This independent evidence sentence confirms the same causal reveal.',
            },
        ],
    }


class ResearchEvidenceContractTests(unittest.TestCase):
    def setUp(self):
        research_module.settings.openai_api_key = 'test-key'
        research_module.settings.openai_model = 'test-model'
        research_module.settings.studio_plan_provider = 'openai'
        research_module.settings.gemini_api_key = ''
        research_module.settings.gemini_model = 'gemini-3.1-pro-preview'

    def test_thirty_second_budget_targets_natural_speed_voice(self):
        self.assertEqual(
            research_module._target_word_budget(0.5),
            (56, 52, 60),
        )

    def test_ai_first_short_preview_authorship_matches_runway_cap(self):
        self.assertEqual(
            _max_ai_scenes(
                5,
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                0.5,
            ),
            4,
        )
        for visual_mix in ('balanced', 'real_first'):
            with self.subTest(visual_mix=visual_mix):
                self.assertEqual(
                    _max_ai_scenes(
                        5,
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        0.5,
                    ),
                    5,
                )

    def test_production_scene_target_is_seven_per_minute_for_every_pace_and_capped(self):
        for pace in ('calm', 'balanced', 'dynamic'):
            with self.subTest(pace=pace):
                self.assertEqual(
                    research_module._target_scene_count(2.0, pace),
                    14,
                )
                self.assertEqual(
                    research_module._target_scene_count(5.0, pace),
                    35,
                )
        self.assertEqual(
            research_module._target_scene_count(8.0, 'balanced'),
            56,
        )
        self.assertEqual(
            research_module._target_scene_count(10.0, 'calm'),
            70,
        )
        self.assertEqual(
            research_module._target_scene_count(20.0, 'dynamic'),
            70,
        )

    def test_production_prompt_is_single_pass_but_explicit_count_stays_authoritative(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible action number {index}.',
                'visual_queries': [
                    f'visible action close view {index}',
                    f'visible action wide view {index}',
                ],
                'ai_prompt': None,
            }
            for index in range(5)
        ]
        research_module.settings.studio_plan_provider = 'gemini'
        research_module.settings.openai_api_key = ''
        research_module.settings.gemini_api_key = 'test-gemini-key'

        with patch.object(
            research_module,
            'generate_gemini_json',
            return_value=payload,
        ) as gemini_generate:
            result = research_module.research_and_script(
                'Return exactly 5 scenes about one visible process.',
                5.0,
                'tr',
                {'mode': 'production', 'pace': 'calm'},
            )

        prompt = gemini_generate.call_args.args[0]
        schema = gemini_generate.call_args.kwargs['json_schema']
        self.assertEqual(result['target_scene_count'], 5)
        self.assertIn('Create EXACTLY 5 scenes', prompt)
        self.assertIn('preferably keep each scene narration at 5-14 words', prompt)
        self.assertIn('one continuous 5-10-second shot', prompt)
        self.assertIn('scene-length shot, normally 5-10 seconds', prompt)
        self.assertNotIn('sustains the complete requested duration', prompt)
        self.assertIn(
            "never overrides a user brief's explicit exact scene count",
            prompt,
        )
        self.assertEqual(schema['properties']['scenes']['minItems'], 5)
        self.assertEqual(schema['properties']['scenes']['maxItems'], 5)

    def test_non_explicit_production_prompt_and_schema_use_approximate_scene_budget(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible production action number {index}.',
                'visual_queries': [
                    f'production action close view {index}',
                    f'production action wide view {index}',
                ],
                'ai_prompt': None,
            }
            for index in range(14)
        ]
        research_module.settings.studio_plan_provider = 'gemini'
        research_module.settings.openai_api_key = ''
        research_module.settings.gemini_api_key = 'test-gemini-key'

        with patch.object(
            research_module,
            'generate_gemini_json',
            return_value=payload,
        ) as gemini_generate:
            result = research_module.research_and_script(
                'Explain one visible production process.',
                2.0,
                'tr',
                {'mode': 'production', 'pace': 'calm'},
            )

        prompt = gemini_generate.call_args.args[0]
        schema = gemini_generate.call_args.kwargs['json_schema']
        self.assertEqual(result['target_scene_count'], 14)
        self.assertIn(
            'Target scene budget: approximately 14 scenes; return 13-15 scenes',
            prompt,
        )
        self.assertNotIn('Create EXACTLY 14 scenes', prompt)
        self.assertIn('preferably keep each scene narration at 5-14 words', prompt)
        self.assertIn('scene-length shot, normally 5-10 seconds', prompt)
        self.assertNotIn('sustains the complete requested duration', prompt)
        self.assertEqual(schema['properties']['scenes']['minItems'], 13)
        self.assertEqual(schema['properties']['scenes']['maxItems'], 15)

    def test_accepts_structured_url_and_evidence_records(self):
        result = _parse_json_payload(
            json.dumps(valid_research_payload()),
        )

        self.assertEqual(
            result['sources'][0]['url'],
            'https://example.com/evidence',
        )
        self.assertIn('central causal reveal', result['sources'][0]['evidence'])

    def test_rejects_missing_or_bare_url_sources(self):
        missing = valid_research_payload()
        missing['sources'] = []
        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(missing))

        bare = valid_research_payload()
        bare['sources'] = ['https://example.com/evidence']
        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(bare))

    def test_rejects_non_string_evidence_and_hostless_urls(self):
        invalid_host = valid_research_payload()
        invalid_host['sources'][0]['url'] = 'https://'
        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(invalid_host))

        invalid_type = valid_research_payload()
        invalid_type['sources'][0]['evidence'] = ['not', 'a', 'sentence']
        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(invalid_type))

    def test_research_web_search_has_strict_tool_budget(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible short action number {index}.',
                'visual_queries': [f'visible short action {index}'],
                'ai_prompt': None,
            }
            for index in range(6)
        ]

        class FakeResponses:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    output_text=json.dumps(payload),
                )

        client = SimpleNamespace(responses=FakeResponses())
        original_openai = research_module.OpenAI
        research_module.OpenAI = lambda **_kwargs: client
        try:
            research_module.research_and_script(
                'one useful phone story',
                0.5,
                'tr',
                {
                    'mode': 'preview',
                    'pace': 'balanced',
                },
            )
        finally:
            research_module.OpenAI = original_openai

        self.assertEqual(len(client.responses.calls), 1)
        self.assertEqual(
            client.responses.calls[0]['max_tool_calls'],
            2,
        )
        self.assertEqual(
            client.responses.calls[0]['tools'][0]['type'],
            'web_search',
        )

    def test_gemini_provider_uses_search_schema_without_constructing_openai(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible action number {index}.',
                'visual_queries': [
                    f'visible action close view {index}',
                    f'visible action wide view {index}',
                ],
                'ai_prompt': None,
            }
            for index in range(5)
        ]
        secret = 'gemini-plan-secret'
        research_module.settings.studio_plan_provider = 'gemini'
        research_module.settings.openai_api_key = ''
        research_module.settings.gemini_api_key = secret

        with patch.object(
            research_module,
            'OpenAI',
            side_effect=AssertionError('OpenAI must not be constructed'),
        ) as openai_class, patch.object(
            research_module,
            'generate_gemini_json',
            return_value=payload,
        ) as gemini_generate:
            result = research_module.research_and_script(
                'one useful phone story',
                0.5,
                'tr',
                {
                    'mode': 'preview',
                    'pace': 'calm',
                    'visual_mix': 'ai_first',
                },
            )

        openai_class.assert_not_called()
        self.assertEqual(len(result['scenes']), 5)
        request = gemini_generate.call_args
        self.assertEqual(request.kwargs['api_key'], secret)
        self.assertTrue(request.kwargs['google_search'])
        self.assertEqual(request.kwargs['thinking_level'], 'medium')
        schema = request.kwargs['json_schema']
        self.assertEqual(
            set(schema['properties']),
            {'title', 'thumbnail_text', 'description', 'scenes', 'sources'},
        )
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['scenes']['minItems'], 4)
        self.assertEqual(schema['properties']['scenes']['maxItems'], 6)
        scene_schema = schema['properties']['scenes']['items']
        self.assertEqual(
            scene_schema['properties']['visual_queries']['minItems'],
            2,
        )
        self.assertEqual(
            scene_schema['properties']['visual_queries']['maxItems'],
            3,
        )
        self.assertEqual(
            scene_schema['properties']['ai_prompt']['type'],
            ['string', 'null'],
        )
        self.assertEqual(schema['properties']['sources']['minItems'], 2)
        self.assertEqual(schema['properties']['sources']['maxItems'], 5)
        self.assertNotIn(secret, request.args[0])
        self.assertNotIn(secret, json.dumps(schema))

    def test_explicit_scene_count_controls_first_research_prompt_schema_and_gate(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible action number {index}.',
                'visual_queries': [
                    f'visible action close view {index}',
                    f'visible action wide view {index}',
                ],
                'ai_prompt': (
                    f'standalone unbranded silver laptop shot {index}'
                    if index < 3 else None
                ),
            }
            for index in range(5)
        ]
        research_module.settings.studio_plan_provider = 'gemini'
        research_module.settings.openai_api_key = ''
        research_module.settings.gemini_api_key = 'test-gemini-key'
        brief = (
            'Tam beş sahne kullan. İlk üç sahne AI olsun. '
            'Her AI sahnesinde aynı markasız mat gümüş 14 inç laptopu koru.'
        )

        with patch.object(
            research_module,
            'generate_gemini_json',
            return_value=payload,
        ) as gemini_generate:
            result = research_module.research_and_script(
                brief,
                0.5,
                'tr',
                {
                    'mode': 'preview',
                    'pace': 'balanced',
                    'visual_mix': 'ai_first',
                },
            )

        request = gemini_generate.call_args
        prompt = request.args[0]
        schema = request.kwargs['json_schema']
        self.assertIn('Create EXACTLY 5 scenes', prompt)
        self.assertIn('Topic is the authoritative production contract', prompt)
        self.assertIn('Every non-null ai_prompt is standalone', prompt)
        self.assertEqual(schema['properties']['scenes']['minItems'], 5)
        self.assertEqual(schema['properties']['scenes']['maxItems'], 5)
        self.assertEqual(result['target_scene_count'], 5)

    def test_explicit_scene_count_rejects_mismatched_research_payload(self):
        payload = valid_research_payload()
        payload['scenes'] = [
            {
                'narration': f'Visible action number {index}.',
                'visual_queries': [
                    f'visible action close view {index}',
                    f'visible action wide view {index}',
                ],
                'ai_prompt': None,
            }
            for index in range(6)
        ]
        research_module.settings.studio_plan_provider = 'gemini'
        research_module.settings.openai_api_key = ''
        research_module.settings.gemini_api_key = 'test-gemini-key'

        with patch.object(
            research_module,
            'generate_gemini_json',
            return_value=payload,
        ), self.assertRaisesRegex(
            RuntimeError,
            'required exactly 5',
        ):
            research_module.research_and_script(
                'Tam beş sahne kullan.',
                0.5,
                'tr',
                {'mode': 'preview', 'pace': 'balanced'},
            )

    def test_invalid_plan_provider_fails_before_any_model_client(self):
        research_module.settings.studio_plan_provider = 'unexpected-provider'
        with patch.object(research_module, 'OpenAI') as openai_class, patch.object(
            research_module,
            'generate_gemini_json',
        ) as gemini_generate, self.assertRaisesRegex(
            RuntimeError,
            'STUDIO_PLAN_PROVIDER',
        ):
            research_module.research_and_script(
                'topic',
                0.5,
                'tr',
            )

        openai_class.assert_not_called()
        gemini_generate.assert_not_called()

    def test_rejects_empty_evidence(self):
        invalid = valid_research_payload()
        invalid['sources'][0]['evidence'] = ''

        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(invalid))


if __name__ == '__main__':
    unittest.main()

