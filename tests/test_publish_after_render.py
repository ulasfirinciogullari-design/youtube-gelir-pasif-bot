import ast
from pathlib import Path
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
        '_queue_automatic_publish_if_enabled',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in function_names
    ]
    namespace = {}
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
    def _call_with_queue_stub(self, options, queue):
        module_name = 'app.publish_tasks'
        previous = sys.modules.get(module_name)
        stub = types.ModuleType(module_name)
        stub.queue_automatic_publish = queue
        sys.modules[module_name] = stub
        try:
            return queue_if_enabled('render-task-123', options)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

    def test_preview_never_queues_even_with_explicit_true(self):
        queue = Mock()
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
        queue = Mock()

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


if __name__ == '__main__':
    unittest.main()
