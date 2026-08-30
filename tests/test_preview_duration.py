import ast
from pathlib import Path
import unittest


def _load_duration_gate():
    source_path = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_preview_duration_within_gate'
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[definition], type_ignores=[]), str(source_path), 'exec'),
        namespace,
    )
    return namespace['_preview_duration_within_gate']


duration_ok = _load_duration_gate()


class PreviewDurationGateTests(unittest.TestCase):
    def test_accepts_live_29_4_second_preview_for_30_second_request(self):
        self.assertTrue(duration_ok(29.4, 30.0))

    def test_rejects_materially_short_30_second_preview(self):
        self.assertFalse(duration_ok(28.9, 30.0))

    def test_sixty_second_preview_allowance_never_exceeds_one_second(self):
        self.assertTrue(duration_ok(59.0, 60.0))
        self.assertFalse(duration_ok(58.9, 60.0))


if __name__ == '__main__':
    unittest.main()
