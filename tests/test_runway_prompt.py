import ast
import math
from pathlib import Path
import re
import unittest
from unittest.mock import Mock


class FinalVisualQualityError(RuntimeError):
    pass


class PreRunwayRetryableError(RuntimeError):
    pass


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
        '_runway_generation_seconds',
        '_runway_single_pass_supported',
        '_validate_runway_single_pass_candidates',
        '_preflight_runway_candidates_before_paid',
        '_generated_visual_spec',
        '_render_target_duration',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        're': re,
        'math': math,
        'Path': Path,
        'FinalVisualQualityError': FinalVisualQualityError,
        'PreRunwayRetryableError': PreRunwayRetryableError,
    }
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
        namespace['_runway_generation_seconds'],
        namespace['_runway_single_pass_supported'],
        namespace['_validate_runway_single_pass_candidates'],
        namespace['_preflight_runway_candidates_before_paid'],
        namespace['_generated_visual_spec'],
        namespace['_render_target_duration'],
    )


(
    runway_prompt,
    apply_visual_review,
    runway_generation_seconds,
    runway_single_pass_supported,
    validate_runway_single_pass_candidates,
    preflight_runway_candidates_before_paid,
    generated_visual_spec,
    render_target_duration,
) = (
    _load_prompt_functions()
)


class RunwayPromptTests(unittest.TestCase):
    def test_scene_duration_buys_enough_single_pass_footage(self):
        self.assertEqual(runway_generation_seconds(9.108), 10)
        self.assertEqual(runway_generation_seconds(8.986), 10)
        self.assertEqual(runway_generation_seconds(6.565), 7)
        self.assertEqual(runway_generation_seconds(2.0), 5)
        self.assertTrue(runway_single_pass_supported(9.108))
        self.assertFalse(runway_single_pass_supported(9.61))

    def test_preview_and_production_mode_route_render_safely(self):
        self.assertEqual(render_target_duration({'mode': 'preview'}, 30), 30.0)
        self.assertIsNone(
            render_target_duration({'mode': 'production'}, 600)
        )
        self.assertTrue(generated_visual_spec('preview.mp4')['forbid_loop'])
        self.assertTrue(generated_visual_spec('production.mp4')['forbid_loop'])

    def test_all_single_pass_candidates_are_preflighted_before_paid_calls(self):
        paid_provider = Mock()
        with self.assertRaises(FinalVisualQualityError):
            validate_runway_single_pass_candidates(
                [0, 1, 2],
                [6.0, 7.0, 11.0],
            )
            paid_provider()
        paid_provider.assert_not_called()

        with self.assertRaises(PreRunwayRetryableError):
            preflight_runway_candidates_before_paid(
                [0],
                [11.0],
                None,
            )
        with self.assertRaises(FinalVisualQualityError):
            preflight_runway_candidates_before_paid(
                [0],
                [11.0],
                {'scenes': [{}]},
            )

        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        initial_preflight = source.index(
            '_preflight_runway_candidates_before_paid(\n'
            '            [int(item'
        )
        initial_loop = source.index('for candidate in selected_runway:')
        repair_preflight = source.index(
            '_preflight_runway_candidates_before_paid(\n'
            '            [int(index)'
        )
        repair_loop = source.index(
            'for scene_idx in final_runway_repair_candidates:'
        )
        self.assertLess(initial_preflight, initial_loop)
        self.assertLess(repair_preflight, repair_loop)

    def test_initial_and_repair_generation_never_use_fixed_five_seconds(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn('generate_scene(prompt_candidates[scene_idx], duration=5)', source)
        self.assertNotIn('generate_scene(repair_prompt, duration=5)', source)
        self.assertEqual(source.count("'forbid_loop': True"), 1)
        self.assertNotIn('five-second photorealistic', source)

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
            'forbid_loop': True,
        }]]
        apply_visual_review(
            visuals,
            0,
            {'best_candidate_index': 0, 'best_start_fraction': 0.82},
        )
        self.assertEqual(visuals[0][0]['start_fraction'], 0.0)
        self.assertTrue(visuals[0][0]['forbid_loop'])

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

