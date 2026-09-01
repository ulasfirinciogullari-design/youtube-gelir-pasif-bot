import ast
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import Mock
from urllib.parse import urljoin, urlparse


class RateLimitError(Exception):
    pass


class BadRequestError(Exception):
    pass


class _Settings:
    runwayml_api_secret = 'configured-test-key'
    gemini_api_key = 'configured-gemini-key'


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


def _load_runway_functions(
    runway_client_factory=None,
    gemini_video_uri=None,
):
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
    names = {
        '_gemini_video_duration',
        '_generate_gemini_video_uri',
        '_create_text_to_video_task',
        'generate_scene',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        'RateLimitError': RateLimitError,
        'BadRequestError': BadRequestError,
        'RunwayML': runway_client_factory or _RunwayClientFactory([object()]),
        'settings': _Settings(),
        'httpx': Mock(),
        're': re,
        'time': time,
        'urlparse': urlparse,
        '_GEMINI_VIDEO_BASE': 'https://generativelanguage.googleapis.com/v1beta',
        '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
        '_GEMINI_OPERATION_PATTERN': re.compile(
            r'^(?:models/[A-Za-z0-9._-]+/)?operations/[A-Za-z0-9._~/-]+$'
        ),
        '_GEMINI_VIDEO_HOSTS': {
            'generativelanguage.googleapis.com',
            'storage.googleapis.com',
        },
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(source_path),
            'exec',
        ),
        namespace,
    )
    if gemini_video_uri is not None:
        namespace['_generate_gemini_video_uri'] = gemini_video_uri
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

        self.assertEqual(
            result,
            {'url': 'video-url', 'provider': 'runway'},
        )
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

    def test_create_bad_request_uses_one_gemini_fallback(self):
        factory = _RunwayClientFactory([
            BadRequestError('secret insufficient-credit response'),
        ])
        gemini_video_uri = Mock(
            return_value=(
                'https://generativelanguage.googleapis.com/v1beta/files/video'
            )
        )
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(
            result,
            {
                'url': (
                    'https://generativelanguage.googleapis.com/'
                    'v1beta/files/video'
                ),
                'provider': 'gemini_veo',
            },
        )
        gemini_video_uri.assert_called_once_with('safe prompt', 5)
        self.assertEqual(len(factory.init_calls), 1)
        self.assertEqual(len(factory.create_resources[0].calls), 1)
        self.assertEqual(factory.retrieve_calls, [])

    def test_poll_bad_request_never_starts_gemini_fallback(self):
        created_task = _CreatedTask()
        poll_error = BadRequestError('secret polling response')
        factory = _RunwayClientFactory([created_task])
        gemini_video_uri = Mock(return_value='must-not-be-used')

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

        _, generate_scene = _load_runway_functions(
            build_client,
            gemini_video_uri=gemini_video_uri,
        )

        with self.assertRaises(BadRequestError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertIs(raised.exception, poll_error)
        gemini_video_uri.assert_not_called()
        self.assertEqual(len(factory.create_resources[0].calls), 1)

    def test_gemini_fallback_duration_is_bounded_to_supported_values(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'services'
            / 'runway.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == '_gemini_video_duration'
        )
        namespace = {}
        exec(
            compile(ast.Module(body=[function], type_ignores=[]), str(source_path), 'exec'),
            namespace,
        )
        duration = namespace['_gemini_video_duration']

        self.assertEqual([duration(value) for value in (4, 5, 6, 7, 8)], [4, 6, 6, 8, 8])
        with self.assertRaises(RuntimeError):
            duration(9)

    def test_gemini_fallback_create_is_single_and_polling_is_read_only(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'services'
            / 'runway.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        definitions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                '_gemini_video_duration',
                '_generate_gemini_video_uri',
            }
        ]

        class _Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class _Client:
            def __init__(self):
                self.post_calls = []
                self.get_calls = []
                self.statuses = [
                    {'done': False},
                    {
                        'done': True,
                        'response': {
                            'generateVideoResponse': {
                                'generatedSamples': [{
                                    'video': {
                                        'uri': (
                                            'https://generativelanguage.googleapis.com/'
                                            'v1beta/files/generated-video'
                                        ),
                                    },
                                }],
                            },
                        },
                    },
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *args, **kwargs):
                self.post_calls.append((args, kwargs))
                return _Response({
                    'name': (
                        'models/veo-3.1-lite-generate-preview/'
                        'operations/operation-123'
                    ),
                })

            def get(self, *args, **kwargs):
                self.get_calls.append((args, kwargs))
                return _Response(self.statuses.pop(0))

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_time = Mock()
        fake_time.monotonic.side_effect = [0.0, 1.0, 2.0]
        namespace = {
            'httpx': fake_httpx,
            'time': fake_time,
            'urlparse': urlparse,
            'settings': _Settings(),
            '_GEMINI_VIDEO_BASE': (
                'https://generativelanguage.googleapis.com/v1beta'
            ),
            '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
            '_GEMINI_OPERATION_PATTERN': re.compile(
                r'^(?:models/[A-Za-z0-9._-]+/)?operations/'
                r'[A-Za-z0-9._~/-]+$'
            ),
            '_GEMINI_VIDEO_HOSTS': {
                'generativelanguage.googleapis.com',
                'storage.googleapis.com',
            },
        }
        exec(
            compile(
                ast.Module(body=definitions, type_ignores=[]),
                str(source_path),
                'exec',
            ),
            namespace,
        )

        uri = namespace['_generate_gemini_video_uri']('safe prompt', 5)

        self.assertTrue(uri.endswith('/generated-video'))
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(len(client.get_calls), 2)
        post_args, post_kwargs = client.post_calls[0]
        self.assertTrue(
            post_args[0].endswith(
                '/models/veo-3.1-lite-generate-preview:predictLongRunning'
            )
        )
        self.assertEqual(
            post_kwargs['json']['parameters'],
            {
                'aspectRatio': '16:9',
                'durationSeconds': 6,
                'resolution': '720p',
            },
        )
        self.assertEqual(post_kwargs['json']['instances'], [{'prompt': 'safe prompt'}])
        fake_httpx.Client.assert_called_once_with(
            timeout=fake_httpx.Timeout.return_value,
            follow_redirects=False,
        )
        fake_time.sleep.assert_called_once_with(10.0)

    def test_gemini_download_key_is_never_sent_to_other_hosts(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'services'
            / 'runway.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'download_generated_scene'
        )

        video_bytes = b'\x00\x00\x00\x18ftypmp42' + (b'\x00' * 2048)

        class _StreamResponse:
            def __init__(self, status_code, headers, chunks=()):
                self.status_code = status_code
                self.headers = headers
                self.chunks = list(chunks)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_bytes(self):
                return list(self.chunks)

        fake_httpx = Mock()
        fake_httpx.stream.side_effect = [
            _StreamResponse(
                302,
                {
                    'location': (
                        'https://storage.googleapis.com/bucket/video.mp4'
                    ),
                },
            ),
            _StreamResponse(
                200,
                {
                    'content-type': 'video/mp4',
                    'content-length': str(len(video_bytes)),
                },
                [video_bytes],
            ),
        ]
        namespace = {
            'Path': Path,
            'httpx': fake_httpx,
            'urljoin': urljoin,
            'urlparse': urlparse,
            'settings': _Settings(),
            '_GEMINI_VIDEO_HOSTS': {
                'generativelanguage.googleapis.com',
                'storage.googleapis.com',
            },
            '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(source_path),
                'exec',
            ),
            namespace,
        )

        with tempfile.TemporaryDirectory() as tmp:
            download = namespace['download_generated_scene']
            output = Path(tmp) / 'gemini.mp4'
            download(
                'https://generativelanguage.googleapis.com/v1beta/files/video',
                output,
            )
            self.assertEqual(output.read_bytes(), video_bytes)
            self.assertFalse((Path(tmp) / 'gemini.mp4.part').exists())

        first = fake_httpx.stream.call_args_list[0].kwargs
        second = fake_httpx.stream.call_args_list[1].kwargs
        self.assertEqual(
            first['headers'],
            {'x-goog-api-key': 'configured-gemini-key'},
        )
        self.assertEqual(second['headers'], {})
        self.assertFalse(first['follow_redirects'])
        self.assertFalse(second['follow_redirects'])

    def test_invalid_or_oversized_download_never_replaces_target(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'services'
            / 'runway.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'download_generated_scene'
        )

        class _StreamResponse:
            status_code = 200
            headers = {
                'content-type': 'video/mp4',
                'content-length': str(101 * 1024 * 1024),
            }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def raise_for_status(self):
                return None

            def iter_bytes(self):
                raise AssertionError('oversized response must fail before streaming')

        fake_httpx = Mock()
        fake_httpx.stream.return_value = _StreamResponse()
        namespace = {
            'Path': Path,
            'httpx': fake_httpx,
            'urljoin': urljoin,
            'urlparse': urlparse,
            'settings': _Settings(),
            '_GEMINI_VIDEO_HOSTS': {
                'generativelanguage.googleapis.com',
                'storage.googleapis.com',
            },
            '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(source_path),
                'exec',
            ),
            namespace,
        )

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'scene.mp4'
            with self.assertRaisesRegex(RuntimeError, 'size limit'):
                namespace['download_generated_scene'](
                    'https://storage.googleapis.com/bucket/video.mp4',
                    output,
                )
            self.assertFalse(output.exists())
            self.assertFalse((Path(tmp) / 'scene.mp4.part').exists())

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

    def test_paid_pipeline_disables_worker_loss_redelivery(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'run_video_pipeline'
        )
        decorator = next(
            item for item in function.decorator_list
            if isinstance(item, ast.Call)
        )
        keywords = {item.arg: item.value for item in decorator.keywords}
        self.assertIn('acks_late', keywords)
        self.assertIsInstance(keywords['acks_late'], ast.Constant)
        self.assertIs(keywords['acks_late'].value, False)


if __name__ == '__main__':
    unittest.main()

