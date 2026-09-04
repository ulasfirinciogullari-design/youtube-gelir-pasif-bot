import ast
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock


SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


def _load_publish_boundary():
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding='utf-8'),
        filename=str(SOURCE_PATH),
    )
    function_names = {
        '_normalized_options',
        '_record_publish_queue_outcome',
        '_queue_automatic_publish_if_enabled',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in function_names
    ]
    namespace = {'re': re}
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return (
        namespace['_normalized_options'],
        namespace['_queue_automatic_publish_if_enabled'],
    )


normalize_options, queue_if_enabled = _load_publish_boundary()


class PublishAfterRenderTests(unittest.TestCase):
    def _call_with_queue_stub(self, options, queue, source=None):
        module_name = 'app.publish_tasks'
        state_module_name = 'app.services.studio_state'
        previous = sys.modules.get(module_name)
        previous_state = sys.modules.get(state_module_name)
        stub = types.ModuleType(module_name)
        stub.queue_automatic_publish = queue
        state_stub = types.ModuleType(state_module_name)
        state_stub.merge_youtube_result_field = Mock(return_value=True)
        self.merge_writer = state_stub.merge_youtube_result_field
        sys.modules[module_name] = stub
        sys.modules[state_module_name] = state_stub
        try:
            return queue_if_enabled('render-task-123', options)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
            if previous_state is None:
                sys.modules.pop(state_module_name, None)
            else:
                sys.modules[state_module_name] = previous_state

    def test_preview_never_queues_even_with_explicit_true(self):
        queue = Mock(return_value={'status': 'queued'})
        options = normalize_options(
            {
                'mode': 'preview',
                'publish_after_render': True,
            },
            0.5,
        )

        self.assertIs(options['publish_after_render'], False)
        self.assertIs(self._call_with_queue_stub(options, queue), False)
        queue.assert_not_called()

        self.assertIs(
            self._call_with_queue_stub(
                {
                    'mode': 'preview',
                    'publish_after_render': True,
                },
                queue,
            ),
            False,
        )
        queue.assert_not_called()

    def test_missing_and_non_boolean_values_fail_closed(self):
        for supplied in (None, False, 'true', 'false', 1, 0):
            with self.subTest(supplied=supplied):
                raw = {'mode': 'production'}
                if supplied is not None:
                    raw['publish_after_render'] = supplied
                options = normalize_options(raw, 5.0)
                queue = Mock()

                self.assertIs(options['publish_after_render'], False)
                self.assertIs(
                    self._call_with_queue_stub(options, queue),
                    False,
                )
                queue.assert_not_called()

    def test_explicit_production_opt_in_queues_once(self):
        options = normalize_options(
            {
                'mode': 'production',
                'publish_after_render': True,
            },
            5.0,
        )
        queue = Mock(return_value={'status': 'queued'})

        self.assertIs(options['publish_after_render'], True)
        self.assertIs(self._call_with_queue_stub(options, queue), True)
        queue.assert_called_once_with('render-task-123')

    def test_opted_in_publish_failure_does_not_invalidate_render(self):
        options = normalize_options(
            {
                'mode': 'production',
                'publish_after_render': True,
            },
            5.0,
        )
        queue = Mock(side_effect=RuntimeError('queue unavailable'))

        self.assertIs(self._call_with_queue_stub(options, queue), False)
        queue.assert_called_once_with('render-task-123')

    def test_unexpected_queue_failure_records_sanitized_outcome_and_keeps_video(self):
        options = {'mode': 'production', 'publish_after_render': True}
        source = {'result': {'video_key': 'videos/master.mp4', 'audio_qc': {'score': 100}}}
        queue = Mock(side_effect=RuntimeError('private-provider-detail-must-not-be-stored'))
        self.assertFalse(self._call_with_queue_stub(options, queue, source))
        self.merge_writer.assert_called_once_with(
            'render-task-123', 'youtube_automation', {'status': 'queue_error'},
            only_if_missing=True,
        )

    def test_early_block_records_missing_outcome_without_overwriting_publish(self):
        options = {'mode': 'production', 'publish_after_render': True}
        for status in ('quality_blocked', 'not_enabled', 'no_unique_route'):
            with self.subTest(status=status):
                self.assertFalse(self._call_with_queue_stub(options, Mock(return_value={'status': status}),
                                                           {'result': {'video_key': 'master.mp4'}}))
                self.merge_writer.assert_called_once_with(
                    'render-task-123', 'youtube_automation', {'status': status},
                    only_if_missing=True,
                )
        for existing in ({'status': 'queued', 'publish_task_id': 'existing-child'}, {'status': 'completed'}):
            with self.subTest(existing=existing):
                self.assertFalse(self._call_with_queue_stub(options, Mock(side_effect=RuntimeError('queue failure')),
                                                           {'result': {'video_key': 'master.mp4', 'youtube_automation': existing}}))
                self.merge_writer.assert_called_once_with(
                    'render-task-123', 'youtube_automation', {'status': 'queue_error'},
                    only_if_missing=True,
                )


if __name__ == '__main__':
    unittest.main()
