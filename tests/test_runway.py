import ast
from pathlib import Path
import unittest


class RateLimitError(Exception):
    pass


def _load_runway_create_functions():
    source_path = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'services'
        / 'runway.py'
    )
    tree = ast.parse(
        source_path.read_text(encoding='utf-8'),
        filename=str(source_path),
    )
    names = {'_create_text_to_video_task'}
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {'RateLimitError': RateLimitError}
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(source_path),
            'exec',
        ),
        namespace,
    )
    return namespace['_create_text_to_video_task']


create_text_to_video_task = _load_runway_create_functions()


class _FakeTextToVideo:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, outcomes):
        self.text_to_video = _FakeTextToVideo(outcomes)


class RunwayQuotaFallbackTests(unittest.TestCase):
    def test_primary_success_never_calls_fallback(self):
        accepted_task = object()
        client = _FakeClient([accepted_task])

        result = create_text_to_video_task(client, 'safe prompt', 7)

        self.assertIs(result, accepted_task)
        self.assertEqual(
            client.text_to_video.calls,
            [{
                'model': 'gen4.5',
                'prompt_text': 'safe prompt',
                'ratio': '1280:720',
                'duration': 7,
            }],
        )

    def test_primary_rate_limit_uses_one_silent_seedance_fallback(self):
        accepted_task = object()
        client = _FakeClient([
            RateLimitError('secret provider response'),
            accepted_task,
        ])

        result = create_text_to_video_task(client, 'safe prompt', 7)

        self.assertIs(result, accepted_task)
        self.assertEqual(len(client.text_to_video.calls), 2)
        self.assertEqual(
            client.text_to_video.calls[1],
            {
                'model': 'seedance2_fast',
                'prompt_text': 'safe prompt',
                'ratio': '1280:720',
                'duration': 7,
                'audio': False,
            },
        )

    def test_non_rate_limit_error_never_calls_fallback(self):
        error = TimeoutError('possibly ambiguous provider failure')
        client = _FakeClient([error])

        with self.assertRaises(TimeoutError) as raised:
            create_text_to_video_task(client, 'safe prompt', 7)

        self.assertIs(raised.exception, error)
        self.assertEqual(len(client.text_to_video.calls), 1)

    def test_fallback_is_attempted_only_once(self):
        fallback_error = RateLimitError('secret fallback response')
        client = _FakeClient([
            RateLimitError('secret primary response'),
            fallback_error,
        ])

        with self.assertRaises(RateLimitError) as raised:
            create_text_to_video_task(client, 'safe prompt', 7)

        self.assertIs(raised.exception, fallback_error)
        self.assertEqual(len(client.text_to_video.calls), 2)

    def test_fallback_preserves_full_pipeline_duration_contract(self):
        for duration in range(5, 11):
            with self.subTest(duration=duration):
                accepted_task = object()
                client = _FakeClient([
                    RateLimitError('secret primary response'),
                    accepted_task,
                ])

                result = create_text_to_video_task(
                    client,
                    'safe prompt',
                    duration,
                )

                self.assertIs(result, accepted_task)
                self.assertEqual(
                    client.text_to_video.calls[1]['duration'],
                    duration,
                )

    def test_polling_is_outside_fallback_submission_boundary(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'services'
            / 'runway.py'
        ).read_text(encoding='utf-8')
        helper_start = source.index('def _create_text_to_video_task(')
        generate_start = source.index('def generate_scene(')
        create_start = source.index(
            'created = _create_text_to_video_task(',
            generate_start,
        )
        wait_start = source.index(
            'completed = created.wait_for_task_output(timeout=600)',
            create_start,
        )

        self.assertNotIn(
            '.wait_for_task_output(',
            source[helper_start:generate_start],
        )
        self.assertLess(create_start, wait_start)

    def test_pipeline_submits_selected_runway_scenes_serially(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        initial_start = source.index('for candidate in selected_runway:')
        initial_end = source.index(
            "set_stage(self, task_id, 'final_visual_qc'",
            initial_start,
        )
        repair_start = source.index(
            'for scene_idx in final_runway_repair_candidates:'
        )
        repair_end = source.index(
            '# Give every still-rejected clip one bounded free stock rescue.',
            repair_start,
        )

        self.assertNotIn(
            'ThreadPoolExecutor',
            source[initial_start:initial_end],
        )
        self.assertNotIn(
            'ThreadPoolExecutor',
            source[repair_start:repair_end],
        )


if __name__ == '__main__':
    unittest.main()

