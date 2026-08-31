import ast
import json
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
        '_runway_failure_diagnostic',
        '_runway_failure_payload',
        '_runway_single_pass_supported',
        '_validate_runway_single_pass_candidates',
        '_preflight_runway_candidates_before_paid',
        '_generated_visual_spec',
        '_render_target_duration',
        '_max_runway_scenes',
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
        'preview_paid_ai_limit': lambda options, scene_count, duration: (
            min(
                1 if (options.get('visual_mix') or 'balanced') == 'real_first' else 4,
                scene_count,
            )
            if options.get('mode') == 'preview' and duration <= 0.6
            else None
        ),
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
        namespace['_runway_failure_diagnostic'],
        namespace['_runway_failure_payload'],
        namespace['_runway_single_pass_supported'],
        namespace['_validate_runway_single_pass_candidates'],
        namespace['_preflight_runway_candidates_before_paid'],
        namespace['_generated_visual_spec'],
        namespace['_render_target_duration'],
        namespace['_max_runway_scenes'],
    )


(
    runway_prompt,
    apply_visual_review,
    runway_generation_seconds,
    runway_failure_diagnostic,
    runway_failure_payload,
    runway_single_pass_supported,
    validate_runway_single_pass_candidates,
    preflight_runway_candidates_before_paid,
    generated_visual_spec,
    render_target_duration,
    max_runway_scenes,
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

    def test_real_first_short_preview_caps_initial_runway_spend_at_one_scene(self):
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'real_first'},
                5,
                0.5,
            ),
            1,
        )
        for visual_mix in ('balanced', 'ai_first'):
            with self.subTest(visual_mix=visual_mix):
                self.assertEqual(
                    max_runway_scenes(
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        5,
                        0.5,
                    ),
                    4,
                )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'preview', 'visual_mix': 'real_first'},
                5,
                0.61,
            ),
            0,
        )
        self.assertEqual(
            max_runway_scenes(
                {'mode': 'production', 'visual_mix': 'real_first'},
                20,
                10.0,
            ),
            2,
        )

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

    def test_audio_transcript_gate_runs_before_any_paid_runway_submission(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')

        audio_gate = source.index('audio_qc = verify_audio_narration(')
        audio_rejection = source.index(
            "if not audio_qc.get('available') or not audio_qc.get('pass'):"
        )
        initial_runway_loop = source.index(
            'for candidate in selected_runway:'
        )

        self.assertLess(audio_gate, audio_rejection)
        self.assertLess(audio_rejection, initial_runway_loop)
        self.assertIn("'audio_qc': audio_qc", source)
        self.assertIn("audio_mismatch.get('operations')", source)
        self.assertNotIn(
            "(audio_qc.get('mismatch_details') or [])[:6]",
            source,
        )

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

    def test_runway_failure_diagnostic_never_serializes_exception_message(self):
        secret = (
            'sk-live-secret-value '
            'https://media.example/video.mp4?X-Amz-Signature=secret '
            'PROMPT: private authored scene'
        )
        diagnostic = runway_failure_diagnostic(
            'initial_generation',
            2,
            RuntimeError(secret),
        )

        self.assertEqual(
            diagnostic,
            {
                'stage': 'initial_generation',
                'scene_index': 2,
                'exception_class': 'RuntimeError',
            },
        )
        serialized = json.dumps(diagnostic)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('sk-live-secret-value', serialized)
        self.assertNotIn('X-Amz-Signature', serialized)
        self.assertNotIn('private authored scene', serialized)

    def test_public_runway_failure_payload_filters_fields_and_scenes(self):
        secret = 'key=super-secret&url=https://private.example/generated.mp4'
        payload = runway_failure_payload(
            3,
            [1],
            [
                {
                    **runway_failure_diagnostic(
                        'final_repair',
                        1,
                        TimeoutError(secret),
                    ),
                    'message': secret,
                    'url': 'https://private.example/generated.mp4',
                    'prompt': 'private authored scene',
                },
                runway_failure_diagnostic(
                    'initial_generation',
                    2,
                    RuntimeError(secret),
                ),
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': secret,
                },
            ],
        )

        self.assertEqual(payload['attempts'], 3)
        self.assertEqual(payload['failed_scenes'], [1])
        self.assertEqual(
            payload['failures'],
            [
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': 'TimeoutError',
                },
                {
                    'stage': 'final_repair',
                    'scene_index': 1,
                    'exception_class': 'Exception',
                },
            ],
        )
        serialized = json.dumps(payload)
        self.assertNotIn('super-secret', serialized)
        self.assertNotIn('private.example', serialized)
        self.assertNotIn('private authored scene', serialized)

        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn('runway_errors', source)
        self.assertNotIn("str(exc)[:320]", source)
        self.assertIn("'runway_failure_diagnostics'", source)

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

    def test_connection_action_requires_visible_connector_socket_and_release(self):
        prompt = runway_prompt(
            {
                'narration': (
                    'Sürücü kemeri yeniden yavaşça çekip tokaya takıyor.'
                ),
                'visual_queries': [
                    'metal seat belt tongue visibly enters buckle slot'
                ],
                'ai_prompt': (
                    'Macro shot inside a car showing one seat belt fastening.'
                ),
            },
            None,
        )

        self.assertIn('Connection proof:', prompt)
        self.assertIn('distinct moving connector', prompt)
        self.assertIn('receiving socket before contact', prompt)
        self.assertIn('release the hand', prompt)
        self.assertIn('loose strap', prompt)

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

