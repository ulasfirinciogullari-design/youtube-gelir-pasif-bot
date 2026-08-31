import ast
from pathlib import Path
import re
import unittest


def _load_prompt_functions():
    source_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(
        source_path.read_text(encoding='utf-8'),
        filename=str(source_path),
    )
    names = {
        '_visual_path',
        '_truncate_utf16',
        '_runway_prompt_for_scene',
        '_apply_visual_review',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {'re': re}
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(source_path),
            'exec',
        ),
        namespace,
    )
    return (
        namespace['_runway_prompt_for_scene'],
        namespace['_apply_visual_review'],
    )


runway_prompt, apply_visual_review = _load_prompt_functions()


class RunwayPromptTests(unittest.TestCase):
    def test_primary_event_precedes_identity_and_requires_visible_change(self):
        prompt = runway_prompt(
            {
                'narration': 'Mina presses the switch and the lamp turns off.',
                'visual_queries': ['woman beside a lamp'],
                'ai_prompt': (
                    'A woman with a red jacket stands in a detailed apartment. '
                    'She presses the wall switch and the lamp turns off.'
                ),
            },
            {
                'retry_queries': [
                    'finger physically presses wall switch and lamp turns off'
                ],
                'reason': 'THIS FREEFORM REASON MUST NEVER ENTER THE PROMPT',
            },
        )
        self.assertLess(
            prompt.index('PRIMARY EVENT:'),
            prompt.index('Core shot direction:'),
        )
        self.assertIn('Show a clear START state', prompt)
        self.assertIn('PHYSICAL ACTION or CAUSE', prompt)
        self.assertIn('hold the visibly CHANGED RESULT', prompt)
        self.assertIn('A static final-only shot fails.', prompt)
        self.assertNotIn('THIS FREEFORM REASON', prompt)

    def test_temporal_contract_survives_long_authored_prompt_and_utf16_cap(self):
        prompt = runway_prompt(
            {
                'narration': 'The subject changes from one visible state to another.',
                'visual_queries': ['visible before and after action'],
                'ai_prompt': 'identity and location continuity ' * 120,
            },
            {'retry_queries': ['hand performs one visible action']},
        )
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 1000)
        self.assertIn('PRIMARY EVENT:', prompt)
        self.assertIn('static final-only shot fails', prompt)
        self.assertTrue(prompt.endswith('watermark or metaphor.'))

    def test_existing_mechanism_guardrail_is_retained(self):
        prompt = runway_prompt(
            {
                'narration': 'True black OLED pixels use less power.',
                'visual_queries': ['real OLED subpixel matrix changing'],
                'ai_prompt': 'Extreme macro of an OLED phone display.',
            },
            None,
        )
        self.assertIn('OLED proof:', prompt)
        self.assertIn('real physical meter visibly falls', prompt)

    def test_long_event_and_mechanism_keep_authored_contract_and_closing(self):
        authored = (
            ('same person same wardrobe same location ' * 7)
            + 'AUTHORED_CONTINUITY_SENTINEL '
            + ('specific physical composition ' * 40)
        )
        prompt = runway_prompt(
            {
                'narration': 'OLED true black uses less power ' * 20,
                'visual_queries': ['real OLED subpixels change visibly ' * 8],
                'ai_prompt': authored,
            },
            {'retry_queries': ['physical meter falls after OLED pixels turn off ' * 8]},
        )
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 1000)
        self.assertIn('AUTHORED_CONTINUITY_SENTINEL', prompt)
        self.assertIn('OLED proof:', prompt)
        self.assertTrue(prompt.endswith('watermark or metaphor.'))

    def test_generated_clip_keeps_zero_start_after_qc_ranking(self):
        visuals = [[{
            'path': 'runway_repair.mp4',
            'start_fraction': 0.0,
            'preserve_start_fraction': True,
        }]]
        apply_visual_review(
            visuals,
            0,
            {'best_candidate_index': 0, 'best_start_fraction': 0.82},
        )
        self.assertEqual(visuals[0][0]['start_fraction'], 0.0)

    def test_stock_clip_still_uses_qc_selected_start(self):
        visuals = [[{'path': 'stock.mp4', 'start_fraction': 0.25}]]
        apply_visual_review(
            visuals,
            0,
            {'best_candidate_index': 0, 'best_start_fraction': 0.82},
        )
        self.assertEqual(visuals[0][0]['start_fraction'], 0.82)


if __name__ == '__main__':
    unittest.main()

