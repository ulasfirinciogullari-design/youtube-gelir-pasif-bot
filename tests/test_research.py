import json
import sys
import types
import unittest
from types import SimpleNamespace


openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = object
sys.modules.setdefault('openai', openai_stub)

config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(
    openai_api_key='test-key',
    openai_model='test-model',
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
    def test_ai_first_short_preview_authorship_matches_runway_cap(self):
        self.assertEqual(
            _max_ai_scenes(
                5,
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                0.5,
            ),
            3,
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

    def test_rejects_empty_evidence(self):
        invalid = valid_research_payload()
        invalid['sources'][0]['evidence'] = ''

        with self.assertRaisesRegex(RuntimeError, 'invalid research sources'):
            _parse_json_payload(json.dumps(invalid))


if __name__ == '__main__':
    unittest.main()
