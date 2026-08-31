import ast
from pathlib import Path
import unittest


class RateLimitError(Exception):
    pass


class _Settings:
    runwayml_api_secret = 'configured-test-key'


class _CreatedTask:
    def __init__(self, task_id='task-123'):
        self.id = task_id
        self.direct_wait_calls = []

    def wait_for_task_output(self, **kwargs):
        self.direct_wait_calls.append(kwargs)
        raise AssertionError('create response must not poll with create client')


class _RetrievedTask:
    def __init__(self, output):
        self.output = output
        self.wait_calls = []

    def wait_for_task_output(self, **kwargs):
        self.wait_calls.append(kwargs)
        return self


class _RunwayClientFactory:
    def __init__(self, create_outcomes, retrieved_task=None):
        self.create_outcomes = list(create_outcomes)
        self.retrieved_task = retrieved_task or _RetrievedTask(['video-url'])
        self.init_calls = []
        self.create_resources = []
        self.retrieve_calls = []

    def __call__(self, **kwargs):
        self.init_calls.append(kwargs)
        if kwargs.get('max_retries') == 0:
            resource = _FakeTextToVideo(self.create_outcomes)
            self.create_resources.append(resource)
            return type('CreateClient', (), {'text_to_video': resource})()

        factory = self

        class _Tasks:
            @staticmethod
            def retrieve(task_id):
                factory.retrieve_calls.append(task_id)
                return factory.retrieved_task

        return type('PollClient', (), {'tasks': _Tasks()})()


def _load_runway_functions(runway_client_factory=None):
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
    names = {'_create_text_to_video_task', 'generate_scene'}
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        'RateLimitError': RateLimitError,
        'RunwayML': runway_client_factory or _RunwayClientFactory([object()]),
        'settings': _Settings(),
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
        namespace['_create_text_to_video_task'],
        namespace['generate_scene'],
    )


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


create_text_to_video_task, _unused_generate_scene = _load_runway_functions()


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
        retrieve_start = source.index(
            'completed = poll_client.tasks.retrieve(',
            create_start,
        )
        wait_start = source.index(
            ').wait_for_task_output(timeout=600)',
            retrieve_start,
        )

        self.assertNotIn(
            '.wait_for_task_output(',
            source[helper_start:generate_start],
        )
        self.assertLess(create_start, retrieve_start)
        self.assertLess(retrieve_start, wait_start)

    def test_paid_create_disables_sdk_retries_and_polling_uses_read_client(self):
        created_task = _CreatedTask()
        retrieved_task = _RetrievedTask(['video-url'])
        factory = _RunwayClientFactory(
            [created_task],
            retrieved_task=retrieved_task,
        )
        _, generate_scene = _load_runway_functions(factory)

        result = generate_scene('safe prompt', duration=7)

        self.assertEqual(result, 'video-url')
        self.assertEqual(
            factory.init_calls,
            [
                {
                    'api_key': 'configured-test-key',
                    'max_retries': 0,
                },
                {'api_key': 'configured-test-key'},
            ],
        )
        self.assertEqual(
            factory.create_resources[0].calls[0]['model'],
            'gen4.5',
        )
        self.assertEqual(factory.retrieve_calls, ['task-123'])
        self.assertEqual(retrieved_task.wait_calls, [{'timeout': 600}])
        self.assertEqual(created_task.direct_wait_calls, [])

    def test_poll_rate_limit_never_submits_fallback_or_second_create(self):
        created_task = _CreatedTask()
        poll_error = RateLimitError('secret read response')
        factory = _RunwayClientFactory([created_task])

        class _FailingTasks:
            @staticmethod
            def retrieve(task_id):
                factory.retrieve_calls.append(task_id)
                raise poll_error

        original_call = factory.__call__

        def build_client(**kwargs):
            if kwargs.get('max_retries') == 0:
                return original_call(**kwargs)
            factory.init_calls.append(kwargs)
            return type('PollClient', (), {'tasks': _FailingTasks()})()

        _, generate_scene = _load_runway_functions(build_client)

        with self.assertRaises(RateLimitError) as raised:
            generate_scene('safe prompt', duration=7)

        self.assertIs(raised.exception, poll_error)
        self.assertEqual(len(factory.create_resources), 1)
        self.assertEqual(len(factory.create_resources[0].calls), 1)
        self.assertEqual(
            factory.create_resources[0].calls[0]['model'],
            'gen4.5',
        )

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

