import json
import sys
import types
import unittest
from types import SimpleNamespace


openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = object
sys.modules.setdefault('openai', openai_stub)

config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace()
sys.modules['app.config'] = config_stub

from app.services.research import _parse_json_payload


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
        ],
    }


class ResearchEvidenceContractTests(unittest.TestCase):
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
        with self.assertRaisesRegex(RuntimeError, 'evidence-backed sources'):
            _parse_json_payload(json.dumps(missing))

        bare = valid_research_payload()
        bare['sources'] = ['https://example.com/evidence']
        with self.assertRaisesRegex(RuntimeError, 'url and evidence'):
            _parse_json_payload(json.dumps(bare))

    def test_rejects_empty_evidence(self):
        invalid = valid_research_payload()
        invalid['sources'][0]['evidence'] = ''

        with self.assertRaisesRegex(RuntimeError, 'concrete evidence'):
            _parse_json_payload(json.dumps(invalid))


if __name__ == '__main__':
    unittest.main()
