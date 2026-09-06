import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from app.services.render import aspect_ratio_for_mode, resolution_for_mode
from app.services.visual_routing import preview_total_paid_create_cap


SOURCE = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))


def task_boundaries():
    names = {
        '_normalized_options', '_render_target_duration',
        '_preflight_production_shorts_paid_plan', '_validate_paid_create_allocation',
        '_manual_qa_preview_passes', '_persisted_paid_create_budget', '_persisted_paid_create_slots',
        '_reserve_paid_create_slot',
    }
    definitions = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'FinalVisualQualityError': RuntimeError}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


class ProductionShortsTests(unittest.TestCase):
    def test_canvas_is_independent_of_production_quality_mode(self):
        normalize = task_boundaries()['_normalized_options']
        production = normalize({'mode': 'production', 'format': 'shorts', 'publish_after_render': True}, 0.5)
        self.assertEqual(production['mode'], 'production')
        self.assertEqual(production['format'], 'shorts')
        self.assertTrue(production['publish_after_render'])
        self.assertEqual(resolution_for_mode(production['mode'], production['format']), '1080x1920')
        self.assertEqual(aspect_ratio_for_mode(production['mode'], production['format']), '9:16')
        self.assertEqual(resolution_for_mode('production'), '1920x1080')
        self.assertEqual(resolution_for_mode('preview'), '1080x1920')
        self.assertEqual(normalize({'mode': 'production'}, 0.5)['format'], 'landscape')
        self.assertEqual(normalize({'mode': 'preview'}, 0.5)['format'], 'shorts')

    def test_invalid_format_fails_before_any_pipeline_work(self):
        normalize = task_boundaries()['_normalized_options']
        for value in ('portrait', 'square', '', False, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize({'mode': 'production', 'format': value}, 0.5)
                with self.assertRaises(ValueError):
                    resolution_for_mode('production', value)
        self.assertEqual(normalize({'mode': 'production', 'format': ' SHORTS '}, 0.5)['format'], 'shorts')

    def test_server_production_binding_survives_normalization(self):
        fields = {
            'production_channel_id': 'channel-id',
            'production_connection_id': 'connection-generation-id',
            'production_scheduled': True,
            'production_profile_revision': 'profile-revision',
        }
        options = task_boundaries()['_normalized_options']({'mode': 'production', 'format': 'shorts', **fields}, 0.5)
        for name, value in fields.items():
            self.assertEqual(options[name], value)

    def test_production_short_cannot_use_manual_preview_quality_exception(self):
        boundary = task_boundaries()
        options = boundary['_normalized_options']({'mode': 'production', 'format': 'shorts', 'quality_threshold': 86}, 0.5)
        boundary['_manual_qa_visual_source_type'] = Mock(side_effect=AssertionError('Preview-only quality path reached'))
        self.assertFalse(boundary['_manual_qa_preview_passes'](options, 0.5, {}, {'score': 70}, {'path': 'candidate.mp4'}))
        self.assertEqual(boundary['_render_target_duration'](options, 30), 30)
        self.assertIsNone(boundary['_render_target_duration']({'mode': 'production'}, 30))

    def test_exact_production_short_cap_is_two_and_not_caller_configurable(self):
        for mix in ('real_first', 'balanced', 'ai_first'):
            options = {'mode': 'production', 'format': 'shorts', 'visual_mix': mix,
                       'preview_total_paid_create_cap': 99, 'paid_create_cap': 99}
            self.assertEqual(preview_total_paid_create_cap(options, 0.5), 2)
        for options, duration in (({'mode': 'production'}, 0.5), ({'mode': 'production', 'format': 'landscape'}, 0.5),
                                  ({'mode': 'production', 'format': 'shorts'}, 0.55)):
            self.assertIsNone(preview_total_paid_create_cap(options, duration))
        self.assertEqual(preview_total_paid_create_cap({'mode': 'preview'}, 0.5), 2)

    def test_prevoice_plan_rejects_three_new_ai_scenes_but_reuses_cached_media(self):
        boundary = task_boundaries()
        preflight = boundary['_preflight_production_shorts_paid_plan']
        options = {'mode': 'production', 'format': 'shorts'}
        scenes = [{'ai_prompt': f'Concrete shot {index}'} for index in range(3)]
        with self.assertRaisesRegex(RuntimeError, 'paid-create cap'):
            preflight(options, scenes, None, 2)
        recovered = {'scenes': {0: [{'key': 'paid-existing.mp4'}]}}
        preflight(options, scenes, recovered, 2)
        with self.assertRaisesRegex(RuntimeError, 'paid-create cap'):
            preflight(options, scenes, recovered, 2, paid_slots_used=1)
        preflight(options, [{'ai_prompt': None}] * 5, None, 2, paid_slots_used=2)
        preflight({'mode': 'preview'}, scenes, None, 2)

    def test_production_short_reuses_persistent_budget_after_worker_restart(self):
        boundary = task_boundaries()
        options = {'mode': 'production', 'format': 'shorts'}
        cap = preview_total_paid_create_cap(options, 0.5)
        ledger = {'used': 0}

        def state(_task_id, requested_cap, *, reserve=False):
            self.assertEqual(requested_cap, 2)
            if reserve:
                if ledger['used'] >= requested_cap:
                    raise RuntimeError('paid-create budget exhausted')
                ledger['used'] += 1
            return dict(ledger)

        boundary['paid_create_budget_state'] = state
        reserve = boundary['_reserve_paid_create_slot']
        self.assertEqual(reserve(0, cap, task_id='production-short'), 1)
        self.assertEqual(reserve(1, cap, task_id='production-short'), 2)
        restarted = task_boundaries()
        restarted['paid_create_budget_state'] = state
        self.assertEqual(restarted['_persisted_paid_create_slots']('production-short', cap), 2)
        with self.assertRaisesRegex(RuntimeError, 'no new generation was submitted'):
            restarted['_reserve_paid_create_slot'](0, cap, task_id='production-short')
        self.assertEqual(ledger['used'], 2)

    def test_both_provider_paths_disable_paid_terminal_resubmit_for_short(self):
        calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'generate_scene']
        self.assertEqual(len(calls), 2)
        context = {'total_paid_create_cap': preview_total_paid_create_cap({'mode': 'production', 'format': 'shorts'}, 0.5),
                   'is_private_image_motion_preview': False}
        for call in calls:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            for name in ('allow_paid_terminal_resubmit', 'allow_image_motion'):
                allowed = eval(compile(ast.Expression(keywords[name]), str(SOURCE), 'eval'), context)
                self.assertFalse(allowed)


if __name__ == '__main__':
    unittest.main()
