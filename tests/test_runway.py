import ast
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, call
from urllib.parse import urljoin, urlparse


class RateLimitError(Exception):
    pass


class BadRequestError(Exception):
    def __init__(self, message, *, body=None):
        super().__init__(message)
        self.body = body


class GeminiVideoTerminalError(RuntimeError):
    pass


class GeminiVideoQuotaError(RuntimeError):
    pass


class GeminiImageAttemptedError(RuntimeError):
    pass


class RunwayCreateRejectedError(RuntimeError):
    pass


class RunwayCreditPreflightInsufficientError(RuntimeError):
    pass


def _safe_runway_fallback_error():
    return BadRequestError(
        'secret provider response that must not be inspected',
        body={
            'error': {'code': 'CAPACITY_UNAVAILABLE'},
            'detail': 'secret provider diagnostic',
        },
    )


class FalVideoError(RuntimeError):
    def __init__(self, message, *, safe_to_fallback=False):
        super().__init__(message)
        self.safe_to_fallback = safe_to_fallback


def fal_error_allows_provider_fallback(exc):
    return isinstance(exc, FalVideoError) and exc.safe_to_fallback


class _Settings:
    runwayml_api_secret = 'configured-test-key'
    gemini_api_key = 'configured-gemini-key'
    fal_key = ''


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


class _FakeOrganization:
    def __init__(self, outcome):
        self.outcome = outcome
        self.retrieve_calls = []

    def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _CreditAwareRunwayClientFactory(_RunwayClientFactory):
    def __init__(self, create_outcomes, organization_outcome, **kwargs):
        super().__init__(create_outcomes, **kwargs)
        self.organization = _FakeOrganization(organization_outcome)

    def __call__(self, **kwargs):
        client = super().__call__(**kwargs)
        if kwargs.get('max_retries') == 0:
            client.organization = self.organization
        return client


def _load_runway_functions(
    runway_client_factory=None,
    gemini_video_uri=None,
    gemini_image_descriptor=None,
    fal_video=None,
    fal_key='',
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
        '_aspect_ratio_profile',
        '_gemini_video_duration',
        '_is_daily_gemini_quota_rejection',
        '_gemini_image_rejection_category',
        '_validated_jpeg_dimensions',
        '_probe_single_jpeg_frame',
        '_decode_gemini_image',
        '_generate_gemini_image_descriptor',
        '_generate_gemini_video_uri',
        '_create_text_to_video_task',
        '_runway_gen45_credits_known_insufficient',
        '_normalize_runway_provider_code',
        '_runway_create_error_allows_provider_fallback',
        '_runway_bad_request_category',
        'generate_scene',
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    test_settings = _Settings()
    test_settings.fal_key = fal_key
    namespace = {
        'RateLimitError': RateLimitError,
        'BadRequestError': BadRequestError,
        'GeminiVideoTerminalError': GeminiVideoTerminalError,
        'GeminiVideoQuotaError': GeminiVideoQuotaError,
        'GeminiImageAttemptedError': GeminiImageAttemptedError,
        'RunwayCreateRejectedError': RunwayCreateRejectedError,
        'RunwayCreditPreflightInsufficientError': (
            RunwayCreditPreflightInsufficientError
        ),
        'FalVideoError': FalVideoError,
        'fal_error_allows_provider_fallback': (
            fal_error_allows_provider_fallback
        ),
        'generate_fal_video': fal_video or Mock(),
        'RunwayML': runway_client_factory or _RunwayClientFactory([object()]),
        'settings': test_settings,
        'httpx': Mock(),
        'base64': base64,
        'binascii': binascii,
        'hashlib': hashlib,
        're': re,
        'time': time,
        'urlparse': urlparse,
        '_GEMINI_VIDEO_BASE': 'https://generativelanguage.googleapis.com/v1beta',
        '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
        '_GEMINI_VIDEO_FAST_MODEL': 'veo-3.1-fast-generate-preview',
        '_GEMINI_VIDEO_STANDARD_MODEL': 'veo-3.1-generate-preview',
        '_GEMINI_IMAGE_MODEL': 'gemini-3.1-flash-image',
        '_GEMINI_IMAGE_ENDPOINT': (
            'https://generativelanguage.googleapis.com/v1beta/interactions'
        ),
        '_GEMINI_IMAGE_MIME_TYPE': 'image/jpeg',
        '_IMAGE_MOTION_RECIPE_VERSION': 'diagonal-push-v2',
        '_GEMINI_OPERATION_PATTERN': re.compile(
            r'^(?:models/[A-Za-z0-9._-]+/)?operations/[A-Za-z0-9._~/-]+$'
        ),
        '_GEMINI_VIDEO_HOSTS': {
            'generativelanguage.googleapis.com',
            'storage.googleapis.com',
        },
        '_MIN_GENERATED_IMAGE_BYTES': 10 * 1024,
        '_MAX_GENERATED_IMAGE_BYTES': 12 * 1024 * 1024,
        '_MAX_GENERATED_IMAGE_PIXELS': 8_388_608,
        '_GEMINI_VIDEO_QUOTA_COOLDOWN_SECONDS': 10 * 60,
        '_GEMINI_VIDEO_QUOTA_BLOCKED_UNTIL': {},
        '_RUNWAY_GEN45_CREDITS_PER_SECOND': 12,
        '_ASPECT_RATIO_PROFILES': {
            '16:9': {
                'runway_ratio': '1280:720',
                'motion_scale': '2560:1440',
                'motion_output': '1280x720',
                'motion_width': 1280,
                'motion_height': 720,
            },
            '9:16': {
                'runway_ratio': '720:1280',
                'motion_scale': '1440:2560',
                'motion_output': '720x1280',
                'motion_width': 720,
                'motion_height': 1280,
            },
        },
        '_RUNWAY_SAFE_PROVIDER_FALLBACK_CODES': frozenset({
            'capacity_exhausted',
            'capacity_unavailable',
            'billing_limit_exceeded',
            'concurrency_limit_exceeded',
            'credit_balance_exhausted',
            'credits_exhausted',
            'insufficient_credits',
            'insufficient_credit_balance',
            'model_disabled',
            'model_not_available',
            'model_not_enabled',
            'model_not_supported',
            'model_temporarily_unavailable',
            'model_unavailable',
            'model_unsupported',
            'no_eligible_model',
            'not_enough_credits',
            'quota_exceeded',
            'unsupported_model',
        }),
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
    if gemini_image_descriptor is not None:
        namespace['_generate_gemini_image_descriptor'] = (
            gemini_image_descriptor
        )
    return (
        namespace['_create_text_to_video_task'],
        namespace['generate_scene'],
    )


def _load_image_namespace(fake_httpx=None, fake_subprocess=None):
    source_path = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'services'
        / 'runway.py'
    )
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    names = {
        '_aspect_ratio_profile',
        '_gemini_image_rejection_category',
        '_validated_jpeg_dimensions',
        '_probe_single_jpeg_frame',
        '_decode_gemini_image',
        '_generate_gemini_image_descriptor',
        '_image_motion_filter',
        '_render_gemini_image_motion',
        'download_generated_scene',
    }
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        'Path': Path,
        'base64': base64,
        'binascii': binascii,
        'hashlib': hashlib,
        'json': json,
        're': re,
        'subprocess': fake_subprocess or subprocess,
        'httpx': fake_httpx or Mock(),
        'urljoin': urljoin,
        'urlparse': urlparse,
        'settings': _Settings(),
        '_GEMINI_VIDEO_HOSTS': {
            'generativelanguage.googleapis.com',
            'storage.googleapis.com',
        },
        '_GEMINI_IMAGE_MODEL': 'gemini-3.1-flash-image',
        '_GEMINI_IMAGE_ENDPOINT': (
            'https://generativelanguage.googleapis.com/v1beta/interactions'
        ),
        '_GEMINI_IMAGE_MIME_TYPE': 'image/jpeg',
        '_IMAGE_MOTION_RECIPE_VERSION': 'diagonal-push-v2',
        '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        '_MIN_GENERATED_IMAGE_BYTES': 10 * 1024,
        '_MAX_GENERATED_IMAGE_BYTES': 12 * 1024 * 1024,
        '_MAX_GENERATED_IMAGE_PIXELS': 8_388_608,
        '_IMAGE_MOTION_FPS': 30,
        '_ASPECT_RATIO_PROFILES': {
            '16:9': {
                'runway_ratio': '1280:720',
                'motion_scale': '2560:1440',
                'motion_output': '1280x720',
                'motion_width': 1280,
                'motion_height': 720,
            },
            '9:16': {
                'runway_ratio': '720:1280',
                'motion_scale': '1440:2560',
                'motion_output': '720x1280',
                'motion_width': 720,
                'motion_height': 1280,
            },
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
    return namespace


def _fake_jpeg(width=1024, height=576, size=11 * 1024):
    header = (
        b'\xff\xd8\xff\xc0\x00\x11\x08'
        + int(height).to_bytes(2, 'big')
        + int(width).to_bytes(2, 'big')
        + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00'
    )
    scan_header = (
        b'\xff\xda\x00\x0c\x03'
        b'\x01\x00\x02\x11\x03\x11\x00\x3f\x00'
    )
    padding = b'\x00' * max(
        0,
        int(size) - len(header) - len(scan_header) - 2,
    )
    return header + scan_header + padding + b'\xff\xd9'


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

    def test_primary_portrait_create_uses_runway_vertical_ratio(self):
        accepted_task = object()
        client = _FakeClient([accepted_task])

        result = create_text_to_video_task(
            client,
            'safe portrait prompt',
            7,
            '9:16',
        )

        self.assertIs(result, accepted_task)
        self.assertEqual(
            client.text_to_video.calls[0]['ratio'],
            '720:1280',
        )

    def test_bad_request_category_exposes_only_allowlisted_structure(self):
        secret = 'sk-secret prompt and account detail'
        factory = _RunwayClientFactory([
            BadRequestError(
                secret,
                body={
                    'error': 'Validation of body failed',
                    'issues': [{
                        'path': ['body', 'model'],
                        'message': secret,
                        'received': secret,
                    }],
                },
            ),
        ])
        _, generate_scene = _load_runway_functions(factory)

        with self.assertRaises(RunwayCreateRejectedError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertEqual(
            getattr(raised.exception, 'reason_code', ''),
            'invalid_model',
        )
        self.assertNotIn(secret, str(raised.exception))

    def test_prompt_issue_categories_never_serialize_provider_message(self):
        cases = [
            ('Prompt must be at most 1000 UTF-16 code units', 'prompt_too_long'),
            ('Prompt blocked by content safety policy', 'prompt_safety'),
            ('Prompt must be non-empty', 'prompt_empty'),
        ]
        for message, expected in cases:
            with self.subTest(expected=expected):
                secret = 'secret prompt and account value'
                factory = _RunwayClientFactory([
                    BadRequestError(
                        secret,
                        body={
                            'error': 'Validation of body failed',
                            'issues': [{
                                'path': ['body', 'promptText'],
                                'message': f'{message}; {secret}',
                            }],
                        },
                    ),
                ])
                _, generate_scene = _load_runway_functions(factory)

                with self.assertRaises(RunwayCreateRejectedError) as raised:
                    generate_scene('safe prompt', duration=5)

                self.assertEqual(
                    getattr(raised.exception, 'reason_code', ''),
                    expected,
                )
                self.assertNotIn(secret, str(raised.exception))

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
            {
                'url': 'video-url',
                'provider': 'runway',
                'provider_attempts': 1,
            },
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

    def test_known_55_credit_balance_skips_impossible_five_second_create(self):
        organization = type('Organization', (), {'credit_balance': 55})()
        factory = _CreditAwareRunwayClientFactory(
            [AssertionError('Runway create must not be submitted')],
            organization,
        )
        fal_video = Mock(return_value={
            'url': 'https://v3.fal.media/files/example/video.mp4',
            'provider': 'fal_seedance_2_fast',
            'provider_attempts': 1,
            'provider_request_id': (
                '123e4567-e89b-12d3-a456-426614174000'
            ),
        })
        gemini_video_uri = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'fal_seedance_2_fast')
        self.assertEqual(factory.create_resources[0].calls, [])
        self.assertEqual(
            factory.organization.retrieve_calls,
            [{'timeout': 5.0}],
        )
        fal_video.assert_called_once_with('safe prompt', 5)
        gemini_video_uri.assert_not_called()
        self.assertNotIn('55', str(result))

    def test_known_insufficient_balance_uses_gemini_when_fal_is_missing(self):
        organization = type('Organization', (), {'credit_balance': 55})()
        factory = _CreditAwareRunwayClientFactory(
            [AssertionError('Runway create must not be submitted')],
            organization,
        )
        gemini_video_uri = Mock(return_value=(
            'https://generativelanguage.googleapis.com/v1beta/files/video'
        ))
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'gemini_veo')
        self.assertEqual(factory.create_resources[0].calls, [])
        gemini_video_uri.assert_called_once_with('safe prompt', 5)

    def test_portrait_fallback_contract_reaches_fal_and_gemini(self):
        organization = type('Organization', (), {'credit_balance': 55})()
        fal_factory = _CreditAwareRunwayClientFactory(
            [AssertionError('Runway create must not be submitted')],
            organization,
        )
        fal_video = Mock(return_value={
            'url': 'https://v3.fal.media/files/example/video.mp4',
            'provider': 'fal_seedance_2_fast',
            'provider_attempts': 1,
        })
        _, generate_with_fal = _load_runway_functions(
            fal_factory,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        generate_with_fal(
            'safe portrait prompt',
            duration=5,
            aspect_ratio='9:16',
        )

        fal_video.assert_called_once_with(
            'safe portrait prompt',
            5,
            aspect_ratio='9:16',
        )

        gemini_factory = _CreditAwareRunwayClientFactory(
            [AssertionError('Runway create must not be submitted')],
            organization,
        )
        gemini_video_uri = Mock(return_value=(
            'https://generativelanguage.googleapis.com/v1beta/files/video'
        ))
        _, generate_with_gemini = _load_runway_functions(
            gemini_factory,
            gemini_video_uri=gemini_video_uri,
        )

        generate_with_gemini(
            'safe portrait prompt',
            duration=5,
            aspect_ratio='9:16',
        )

        gemini_video_uri.assert_called_once_with(
            'safe portrait prompt',
            5,
            aspect_ratio='9:16',
        )

    def test_invalid_aspect_ratio_fails_before_paid_create(self):
        factory = _RunwayClientFactory([
            AssertionError('invalid ratio must not submit'),
        ])
        _, generate_scene = _load_runway_functions(factory)

        with self.assertRaisesRegex(ValueError, 'Aspect ratio'):
            generate_scene(
                'safe prompt',
                duration=5,
                aspect_ratio='1:1',
            )

        self.assertEqual(factory.init_calls, [])

    def test_exact_required_balance_preserves_runway_create(self):
        created_task = _CreatedTask()
        organization = type('Organization', (), {'credit_balance': 60})()
        factory = _CreditAwareRunwayClientFactory(
            [created_task],
            organization,
        )
        _, generate_scene = _load_runway_functions(factory)

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'runway')
        self.assertEqual(len(factory.create_resources[0].calls), 1)
        self.assertEqual(
            factory.create_resources[0].calls[0]['model'],
            'gen4.5',
        )

    def test_ambiguous_credit_preflight_failure_preserves_runway_create(self):
        created_task = _CreatedTask()
        secret = 'secret organization transport detail'
        factory = _CreditAwareRunwayClientFactory(
            [created_task],
            TimeoutError(secret),
        )
        _, generate_scene = _load_runway_functions(factory)

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'runway')
        self.assertEqual(len(factory.create_resources[0].calls), 1)
        self.assertNotIn(secret, str(result))

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

    def test_unclassified_create_bad_request_stops_before_other_providers(self):
        secret = 'secret unclassified provider response'
        factory = _RunwayClientFactory([
            BadRequestError(secret, body={
                'error': 'Validation of body failed',
                'issues': [{
                    'code': 'custom',
                    'path': ['promptText'],
                    'message': 'secret validation detail',
                }],
            }),
        ])
        fal_video = Mock()
        gemini_video_uri = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        with self.assertRaises(RunwayCreateRejectedError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn('secret validation detail', str(raised.exception))
        fal_video.assert_not_called()
        gemini_video_uri.assert_not_called()
        self.assertEqual(len(factory.create_resources[0].calls), 1)

    def test_terminal_runway_codes_never_authorize_provider_hopping(self):
        cases = [
            {'failureCode': 'SAFETY.INPUT.TEXT'},
            {'code': 'INPUT_PREPROCESSING.SAFETY.TEXT'},
            {'error': {'failure_code': 'ASSET.INVALID'}},
            {'error': {'code': 'VALIDATION_ERROR'}},
        ]
        for body in cases:
            with self.subTest(body=body):
                secret = 'secret terminal provider detail'
                body = {**body, 'detail': secret}
                factory = _RunwayClientFactory([
                    BadRequestError(secret, body=body),
                ])
                fal_video = Mock()
                gemini_video_uri = Mock()
                _, generate_scene = _load_runway_functions(
                    factory,
                    gemini_video_uri=gemini_video_uri,
                    fal_video=fal_video,
                    fal_key='configured-fal-key',
                )

                with self.assertRaises(RunwayCreateRejectedError) as raised:
                    generate_scene('safe prompt', duration=5)

                self.assertNotIn(secret, str(raised.exception))
                fal_video.assert_not_called()
                gemini_video_uri.assert_not_called()
                self.assertEqual(len(factory.create_resources[0].calls), 1)

    def test_only_explicit_capacity_or_model_codes_allow_provider_hopping(self):
        cases = [
            {'code': 'CAPACITY_UNAVAILABLE'},
            {'error': 'Not enough credits'},
            {'error': {'errorCode': 'MODEL_UNAVAILABLE'}},
            {'error': {'reason': 'UNSUPPORTED_MODEL'}},
        ]
        for body in cases:
            with self.subTest(body=body):
                factory = _RunwayClientFactory([
                    BadRequestError(
                        'secret safe fallback detail',
                        body=body,
                    ),
                ])
                fal_video = Mock(return_value={
                    'url': 'https://v3.fal.media/files/example/video.mp4',
                    'provider': 'fal_seedance_2_fast',
                    'provider_attempts': 1,
                    'provider_request_id': (
                        '123e4567-e89b-12d3-a456-426614174000'
                    ),
                })
                gemini_video_uri = Mock()
                _, generate_scene = _load_runway_functions(
                    factory,
                    gemini_video_uri=gemini_video_uri,
                    fal_video=fal_video,
                    fal_key='configured-fal-key',
                )

                result = generate_scene('safe prompt', duration=5)

                self.assertEqual(result['provider'], 'fal_seedance_2_fast')
                self.assertNotIn(
                    'secret safe fallback detail',
                    str(result),
                )
                fal_video.assert_called_once_with('safe prompt', 5)
                gemini_video_uri.assert_not_called()

    def test_free_form_capacity_message_does_not_authorize_fallback(self):
        factory = _RunwayClientFactory([
            BadRequestError(
                'secret capacity unavailable',
                body={
                    'error': 'capacity unavailable for secret account',
                    'message': 'secret provider message',
                },
            ),
        ])
        fal_video = Mock()
        gemini_video_uri = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        with self.assertRaises(RunwayCreateRejectedError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertNotIn('secret', str(raised.exception))
        fal_video.assert_not_called()
        gemini_video_uri.assert_not_called()

    def test_configured_fal_runs_after_runway_rejection_before_gemini(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        fal_video = Mock(return_value={
            'url': 'https://v3.fal.media/files/example/video.mp4',
            'provider': 'fal_seedance_2_fast',
            'provider_attempts': 1,
            'provider_request_id': (
                '123e4567-e89b-12d3-a456-426614174000'
            ),
        })
        gemini_video_uri = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        result = generate_scene('safe prompt', duration=7)

        self.assertEqual(result['provider'], 'fal_seedance_2_fast')
        fal_video.assert_called_once_with('safe prompt', 7)
        gemini_video_uri.assert_not_called()
        self.assertEqual(len(factory.create_resources[0].calls), 1)

    def test_definitive_fal_quota_rejection_falls_through_to_gemini(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        fal_video = Mock(side_effect=FalVideoError(
            'Fal quota unavailable',
            safe_to_fallback=True,
        ))
        gemini_video_uri = Mock(return_value=(
            'https://generativelanguage.googleapis.com/v1beta/files/video'
        ))
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'gemini_veo')
        self.assertEqual(
            result['provider_fallback_from'],
            'fal_seedance_2_fast',
        )
        fal_video.assert_called_once_with('safe prompt', 5)
        gemini_video_uri.assert_called_once_with('safe prompt', 5)

    def test_ambiguous_fal_submit_never_starts_gemini(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        ambiguous_error = FalVideoError(
            'Fal submission state is ambiguous',
            safe_to_fallback=False,
        )
        fal_video = Mock(side_effect=ambiguous_error)
        gemini_video_uri = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            fal_video=fal_video,
            fal_key='configured-fal-key',
        )

        with self.assertRaises(FalVideoError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertIs(raised.exception, ambiguous_error)
        fal_video.assert_called_once_with('safe prompt', 5)
        gemini_video_uri.assert_not_called()

    def test_create_bad_request_uses_one_gemini_fallback(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
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
                'provider_attempts': 1,
                'quota_fallback_from': None,
            },
        )
        gemini_video_uri.assert_called_once_with('safe prompt', 5)
        self.assertEqual(len(factory.init_calls), 1)
        self.assertEqual(len(factory.create_resources[0].calls), 1)
        self.assertEqual(factory.retrieve_calls, [])

    def test_definitive_gemini_rejection_gets_one_bounded_retry(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoTerminalError('definitive provider rejection'),
            'https://generativelanguage.googleapis.com/v1beta/files/video',
        ])
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['provider'], 'gemini_veo')
        self.assertEqual(result['provider_attempts'], 2)
        self.assertEqual(gemini_video_uri.call_count, 2)

    def test_exhausted_lite_quota_switches_once_to_fast_model(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        fast_uri = (
            'https://generativelanguage.googleapis.com/v1beta/files/fast-video'
        )
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            fast_uri,
        ])
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['url'], fast_uri)
        self.assertEqual(result['provider'], 'gemini_veo_fast')
        self.assertEqual(result['provider_attempts'], 1)
        self.assertEqual(result['quota_fallback_from'], 'gemini_veo')
        self.assertEqual(
            gemini_video_uri.call_args_list,
            [
                call('safe prompt', 5),
                call('safe prompt', 5, 'veo-3.1-fast-generate-preview'),
            ],
        )

    def test_exhausted_fast_quota_switches_once_to_standard_model(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        standard_uri = (
            'https://generativelanguage.googleapis.com/v1beta/'
            'files/standard-video'
        )
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            standard_uri,
        ])
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        result = generate_scene('safe prompt', duration=5)

        self.assertEqual(result['url'], standard_uri)
        self.assertEqual(result['provider'], 'gemini_veo_standard')
        self.assertEqual(result['provider_attempts'], 1)
        self.assertEqual(result['quota_fallback_from'], 'gemini_veo_fast')
        self.assertEqual(
            gemini_video_uri.call_args_list,
            [
                call('safe prompt', 5),
                call('safe prompt', 5, 'veo-3.1-fast-generate-preview'),
                call('safe prompt', 5, 'veo-3.1-generate-preview'),
            ],
        )

    def test_all_veo_quotas_use_one_explicit_private_image_fallback(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            GeminiVideoQuotaError('definitive Standard quota rejection'),
        ])
        descriptor = {
            '_inline_image': {
                'mime_type': 'image/jpeg',
                'data': 'private-inline-data',
            },
            'motion_seconds': 5,
            'source_media_type': 'image',
            'synthetic_motion': True,
            'motion_recipe_version': 'diagonal-push-v2',
            'image_sha256': 'a' * 64,
            'prompt_sha256': 'b' * 64,
            'image_model': 'gemini-3.1-flash-image',
        }
        gemini_image_descriptor = Mock(return_value=descriptor)
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            gemini_image_descriptor=gemini_image_descriptor,
        )

        result = generate_scene(
            'safe video prompt',
            duration=5,
            allow_image_motion=True,
            image_prompt='safe image prompt',
        )

        self.assertEqual(result['provider'], 'gemini_image_motion')
        self.assertEqual(result['provider_attempts'], 1)
        self.assertEqual(
            result['quota_fallback_from'],
            'gemini_veo_standard',
        )
        self.assertEqual(
            result['quota_fallback_chain'],
            ['gemini_veo', 'gemini_veo_fast', 'gemini_veo_standard'],
        )
        self.assertEqual(gemini_video_uri.call_count, 3)
        gemini_image_descriptor.assert_called_once_with(
            'safe image prompt',
            5,
        )

    def test_later_scene_skips_models_in_process_quota_cooldown(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            GeminiVideoQuotaError('definitive Standard quota rejection'),
        ])
        descriptor = {
            '_inline_image': {
                'mime_type': 'image/jpeg',
                'data': 'private-inline-data',
            },
            'motion_seconds': 5,
            'source_media_type': 'image',
            'synthetic_motion': True,
            'motion_recipe_version': 'diagonal-push-v2',
            'image_sha256': 'a' * 64,
            'prompt_sha256': 'b' * 64,
            'image_model': 'gemini-3.1-flash-image',
        }
        gemini_image_descriptor = Mock(return_value=descriptor)
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            gemini_image_descriptor=gemini_image_descriptor,
        )

        for scene_number in range(2):
            result = generate_scene(
                f'safe video prompt {scene_number}',
                duration=5,
                allow_image_motion=True,
                image_prompt=f'safe image prompt {scene_number}',
            )
            self.assertEqual(result['provider'], 'gemini_image_motion')

        self.assertEqual(gemini_video_uri.call_count, 3)
        self.assertEqual(gemini_image_descriptor.call_count, 2)

    def test_image_fallback_is_disabled_without_private_preview_flag(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            GeminiVideoQuotaError('definitive Standard quota rejection'),
        ])
        gemini_image_descriptor = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            gemini_image_descriptor=gemini_image_descriptor,
        )

        with self.assertRaises(GeminiVideoQuotaError):
            generate_scene(
                'safe prompt',
                duration=5,
                image_prompt='must not be submitted',
            )

        gemini_image_descriptor.assert_not_called()

    def test_unsupported_veo_duration_uses_one_image_without_video_post(self):
        for duration in (9, 10):
            with self.subTest(duration=duration):
                factory = _RunwayClientFactory([
                    _safe_runway_fallback_error(),
                ])
                gemini_video_uri = Mock()
                gemini_image_descriptor = Mock(return_value={
                    'motion_seconds': duration,
                    'source_media_type': 'image',
                    'synthetic_motion': True,
                    'motion_recipe_version': 'diagonal-push-v2',
                    'image_sha256': 'a' * 64,
                    'prompt_sha256': 'b' * 64,
                    'image_model': 'gemini-3.1-flash-image',
                })
                _, generate_scene = _load_runway_functions(
                    factory,
                    gemini_video_uri=gemini_video_uri,
                    gemini_image_descriptor=gemini_image_descriptor,
                )

                result = generate_scene(
                    'safe video prompt',
                    duration=duration,
                    allow_image_motion=True,
                    image_prompt='safe image prompt',
                )

                self.assertEqual(result['provider'], 'gemini_image_motion')
                self.assertEqual(
                    result['fallback_reason'],
                    'unsupported_veo_duration',
                )
                self.assertEqual(result['quota_fallback_chain'], [])
                gemini_video_uri.assert_not_called()
                gemini_image_descriptor.assert_called_once_with(
                    'safe image prompt',
                    duration,
                )

    def test_ambiguous_standard_failure_never_starts_image_fallback(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        ambiguous_error = TimeoutError('ambiguous Standard operation')
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            ambiguous_error,
        ])
        gemini_image_descriptor = Mock()
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            gemini_image_descriptor=gemini_image_descriptor,
        )

        with self.assertRaises(TimeoutError) as raised:
            generate_scene(
                'safe prompt',
                duration=5,
                allow_image_motion=True,
                image_prompt='must not be submitted',
            )

        self.assertIs(raised.exception, ambiguous_error)
        gemini_image_descriptor.assert_not_called()

    def test_failed_paid_image_returns_attempt_receipt_for_scene_reservation(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            GeminiVideoQuotaError('definitive Fast quota rejection'),
            GeminiVideoQuotaError('definitive Standard quota rejection'),
        ])
        ambiguous_error = TimeoutError('ambiguous paid image create')
        gemini_image_descriptor = Mock(side_effect=ambiguous_error)
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
            gemini_image_descriptor=gemini_image_descriptor,
        )

        with self.assertRaises(GeminiImageAttemptedError) as raised:
            generate_scene(
                'safe prompt',
                duration=5,
                allow_image_motion=True,
                image_prompt='safe image prompt',
            )

        self.assertIs(raised.exception.__cause__, ambiguous_error)
        gemini_image_descriptor.assert_called_once_with(
            'safe image prompt',
            5,
        )

    def test_ambiguous_fast_failure_never_starts_standard_fallback(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        ambiguous_error = TimeoutError('ambiguous Fast operation')
        gemini_video_uri = Mock(side_effect=[
            GeminiVideoQuotaError('definitive Lite quota rejection'),
            ambiguous_error,
        ])
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        with self.assertRaises(TimeoutError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertIs(raised.exception, ambiguous_error)
        self.assertEqual(
            gemini_video_uri.call_args_list,
            [
                call('safe prompt', 5),
                call('safe prompt', 5, 'veo-3.1-fast-generate-preview'),
            ],
        )

    def test_ambiguous_gemini_failure_is_never_retried(self):
        factory = _RunwayClientFactory([
            _safe_runway_fallback_error(),
        ])
        ambiguous_error = TimeoutError('ambiguous accepted operation')
        gemini_video_uri = Mock(side_effect=ambiguous_error)
        _, generate_scene = _load_runway_functions(
            factory,
            gemini_video_uri=gemini_video_uri,
        )

        with self.assertRaises(TimeoutError) as raised:
            generate_scene('safe prompt', duration=5)

        self.assertIs(raised.exception, ambiguous_error)
        gemini_video_uri.assert_called_once_with('safe prompt', 5)

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
                '_is_daily_gemini_quota_rejection',
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
            're': re,
            'urlparse': urlparse,
            'settings': _Settings(),
            'GeminiVideoTerminalError': GeminiVideoTerminalError,
            'GeminiVideoQuotaError': GeminiVideoQuotaError,
            '_GEMINI_VIDEO_BASE': (
                'https://generativelanguage.googleapis.com/v1beta'
            ),
            '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
            '_GEMINI_VIDEO_FAST_MODEL': 'veo-3.1-fast-generate-preview',
            '_GEMINI_VIDEO_STANDARD_MODEL': 'veo-3.1-generate-preview',
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

    def test_gemini_video_portrait_request_uses_vertical_aspect_ratio(self):
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
                '_is_daily_gemini_quota_rejection',
                '_gemini_video_duration',
                '_generate_gemini_video_uri',
            }
        ]

        class _Response:
            status_code = 200
            headers = {}

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class _Client:
            def __init__(self):
                self.post_calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *args, **kwargs):
                self.post_calls.append((args, kwargs))
                return _Response({'name': 'operations/portrait-operation'})

            def get(self, *_args, **_kwargs):
                return _Response({
                    'done': True,
                    'response': {
                        'generateVideoResponse': {
                            'generatedSamples': [{
                                'video': {
                                    'uri': (
                                        'https://generativelanguage.googleapis.com/'
                                        'v1beta/files/portrait-video'
                                    ),
                                },
                            }],
                        },
                    },
                })

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_time = Mock()
        fake_time.monotonic.side_effect = [0.0, 1.0]
        namespace = {
            'httpx': fake_httpx,
            'time': fake_time,
            're': re,
            'urlparse': urlparse,
            'settings': _Settings(),
            'GeminiVideoTerminalError': GeminiVideoTerminalError,
            'GeminiVideoQuotaError': GeminiVideoQuotaError,
            '_GEMINI_VIDEO_BASE': (
                'https://generativelanguage.googleapis.com/v1beta'
            ),
            '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
            '_GEMINI_VIDEO_FAST_MODEL': 'veo-3.1-fast-generate-preview',
            '_GEMINI_VIDEO_STANDARD_MODEL': 'veo-3.1-generate-preview',
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

        namespace['_generate_gemini_video_uri'](
            'safe portrait prompt',
            5,
            aspect_ratio='9:16',
        )

        self.assertEqual(
            client.post_calls[0][1]['json']['parameters']['aspectRatio'],
            '9:16',
        )

    def test_gemini_create_429_waits_and_retries_exactly_once(self):
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
                '_is_daily_gemini_quota_rejection',
                '_gemini_video_duration',
                '_generate_gemini_video_uri',
            }
        ]

        class _Response:
            def __init__(self, status_code, payload, headers=None):
                self.status_code = status_code
                self.payload = payload
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f'HTTP {self.status_code}')

            def json(self):
                return self.payload

        class _Client:
            def __init__(self):
                self.post_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                if self.post_calls == 1:
                    return _Response(
                        429,
                        {'error': {'status': 'RESOURCE_EXHAUSTED'}},
                        {'retry-after': '2.5'},
                    )
                return _Response(200, {'name': 'operations/operation-429'})

            def get(self, *_args, **_kwargs):
                return _Response(200, {
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
                })

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_time = Mock()
        fake_time.monotonic.side_effect = [0.0, 1.0]
        namespace = {
            'httpx': fake_httpx,
            'time': fake_time,
            'urlparse': urlparse,
            'settings': _Settings(),
            'GeminiVideoTerminalError': GeminiVideoTerminalError,
            'GeminiVideoQuotaError': GeminiVideoQuotaError,
            '_GEMINI_VIDEO_BASE': (
                'https://generativelanguage.googleapis.com/v1beta'
            ),
            '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
            '_GEMINI_VIDEO_FAST_MODEL': 'veo-3.1-fast-generate-preview',
            '_GEMINI_VIDEO_STANDARD_MODEL': 'veo-3.1-generate-preview',
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
        self.assertEqual(client.post_calls, 2)
        fake_time.sleep.assert_called_once_with(2.5)

    def test_explicit_daily_quota_rejection_does_not_wait_or_retry(self):
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
                '_is_daily_gemini_quota_rejection',
                '_gemini_video_duration',
                '_generate_gemini_video_uri',
            }
        ]

        class _Response:
            status_code = 429
            headers = {'retry-after': '60'}

            @staticmethod
            def json():
                return {
                    'error': {
                        'details': [{
                            'violations': [{
                                'quotaId': (
                                    'generate_requests_per_model_per_day'
                                ),
                            }],
                        }],
                    },
                }

        class _Client:
            def __init__(self):
                self.post_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                return _Response()

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_time = Mock()
        namespace = {
            'httpx': fake_httpx,
            'time': fake_time,
            're': re,
            'urlparse': urlparse,
            'settings': _Settings(),
            'GeminiVideoTerminalError': GeminiVideoTerminalError,
            'GeminiVideoQuotaError': GeminiVideoQuotaError,
            '_GEMINI_VIDEO_BASE': (
                'https://generativelanguage.googleapis.com/v1beta'
            ),
            '_GEMINI_VIDEO_MODEL': 'veo-3.1-lite-generate-preview',
            '_GEMINI_VIDEO_FAST_MODEL': 'veo-3.1-fast-generate-preview',
            '_GEMINI_VIDEO_STANDARD_MODEL': 'veo-3.1-generate-preview',
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

        with self.assertRaises(GeminiVideoQuotaError):
            namespace['_generate_gemini_video_uri']('safe prompt', 5)

        self.assertEqual(client.post_calls, 1)
        fake_time.sleep.assert_not_called()

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

    def test_image_interaction_is_one_retry_free_inline_jpeg_request(self):
        image_bytes = _fake_jpeg()
        encoded = base64.b64encode(image_bytes).decode('ascii')

        class _Response:
            def __init__(self, encoded_data):
                self.encoded_data = encoded_data

            @staticmethod
            def raise_for_status():
                return None

            def json(self):
                return {
                    'status': 'completed',
                    'steps': [{
                        'type': 'model_output',
                        'content': [{
                            'type': 'image',
                            'mime_type': 'image/jpeg',
                            'data': self.encoded_data,
                        }],
                    }],
                }

        class _Client:
            def __init__(self):
                self.post_calls = []
                self.encoded_data = encoded

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *args, **kwargs):
                self.post_calls.append((args, kwargs))
                return _Response(self.encoded_data)

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_subprocess = Mock()
        fake_subprocess.SubprocessError = subprocess.SubprocessError
        fake_subprocess.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({
                'streams': [{
                    'codec_name': 'mjpeg',
                    'width': 1024,
                    'height': 576,
                    'nb_read_frames': '1',
                }],
            }).encode('utf-8'),
        )
        namespace = _load_image_namespace(
            fake_httpx=fake_httpx,
            fake_subprocess=fake_subprocess,
        )

        descriptor = namespace['_generate_gemini_image_descriptor'](
            'literal documentary keyframe',
            5,
        )

        self.assertEqual(len(client.post_calls), 1)
        self.assertTrue(descriptor['synthetic_motion'])
        self.assertEqual(descriptor['motion_seconds'], 5)
        self.assertEqual(
            descriptor['image_sha256'],
            hashlib.sha256(image_bytes).hexdigest(),
        )
        post_args, post_kwargs = client.post_calls[0]
        self.assertTrue(post_args[0].endswith('/v1beta/interactions'))
        self.assertFalse(post_kwargs['json']['store'])
        self.assertEqual(
            post_kwargs['json']['input'],
            'literal documentary keyframe',
        )
        self.assertEqual(
            post_kwargs['json']['response_format'],
            {
                'type': 'image',
                'mime_type': 'image/jpeg',
                'aspect_ratio': '16:9',
                'image_size': '1K',
            },
        )
        self.assertNotIn('configured-gemini-key', json.dumps(post_kwargs['json']))
        self.assertEqual(
            post_kwargs['headers']['x-goog-api-key'],
            'configured-gemini-key',
        )
        probe_kwargs = fake_subprocess.run.call_args.kwargs
        probe_args = fake_subprocess.run.call_args.args[0]
        self.assertEqual(probe_args, [
            'ffprobe', '-v', 'error',
            '-f', 'image2pipe',
            '-count_frames', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height,nb_read_frames',
            '-of', 'json', 'pipe:0',
        ])
        self.assertNotIn('-c:v', probe_args)
        self.assertEqual(probe_kwargs['input'], image_bytes)
        self.assertTrue(probe_kwargs['capture_output'])
        self.assertFalse(probe_kwargs['check'])
        self.assertEqual(probe_kwargs['timeout'], 30)

        portrait_bytes = _fake_jpeg(width=576, height=1024)
        client.encoded_data = base64.b64encode(
            portrait_bytes
        ).decode('ascii')
        fake_subprocess.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({
                'streams': [{
                    'codec_name': 'mjpeg',
                    'width': 576,
                    'height': 1024,
                    'nb_read_frames': '1',
                }],
            }).encode('utf-8'),
        )
        portrait_descriptor = namespace[
            '_generate_gemini_image_descriptor'
        ](
            'literal portrait documentary keyframe',
            5,
            '9:16',
        )
        self.assertEqual(len(client.post_calls), 2)
        self.assertEqual(
            client.post_calls[1][1]['json']['response_format'][
                'aspect_ratio'
            ],
            '9:16',
        )
        self.assertEqual(portrait_descriptor['aspect_ratio'], '9:16')

    def test_image_interaction_rejection_exposes_only_safe_category(self):
        class _Response:
            status_code = 400

            @staticmethod
            def json():
                return {
                    'error': {
                        'status': 'INVALID_ARGUMENT',
                        'message': 'sensitive provider detail',
                    },
                }

            @staticmethod
            def raise_for_status():
                raise AssertionError('classified rejection must fail first')

        class _Client:
            post_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                return _Response()

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        namespace = _load_image_namespace(fake_httpx=fake_httpx)

        with self.assertRaisesRegex(
            RuntimeError,
            r'rejected \(invalid_argument\)',
        ) as raised:
            namespace['_generate_gemini_image_descriptor']('prompt', 5)

        self.assertNotIn('sensitive provider detail', str(raised.exception))
        self.assertEqual(client.post_calls, 1)

    def test_image_interaction_rejects_multiple_outputs_without_retry(self):
        encoded = base64.b64encode(_fake_jpeg()).decode('ascii')

        class _Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                image = {
                    'type': 'image',
                    'mime_type': 'image/jpeg',
                    'data': encoded,
                }
                return {
                    'status': 'completed',
                    'steps': [{
                        'type': 'model_output',
                        'content': [image, dict(image)],
                    }],
                }

        class _Client:
            post_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                return _Response()

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        namespace = _load_image_namespace(fake_httpx=fake_httpx)

        with self.assertRaisesRegex(RuntimeError, 'invalid response'):
            namespace['_generate_gemini_image_descriptor']('prompt', 5)

        self.assertEqual(client.post_calls, 1)

    def test_image_interaction_timeout_is_never_retried(self):
        ambiguous_error = TimeoutError('ambiguous paid image create')

        class _Client:
            post_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                self.post_calls += 1
                raise ambiguous_error

        client = _Client()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        namespace = _load_image_namespace(fake_httpx=fake_httpx)

        with self.assertRaises(TimeoutError) as raised:
            namespace['_generate_gemini_image_descriptor']('prompt', 5)

        self.assertIs(raised.exception, ambiguous_error)
        self.assertEqual(client.post_calls, 1)

    def test_structural_jpeg_that_fails_real_decode_is_rejected(self):
        encoded = base64.b64encode(_fake_jpeg()).decode('ascii')

        class _Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    'status': 'completed',
                    'steps': [{
                        'type': 'model_output',
                        'content': [{
                            'type': 'image',
                            'mime_type': 'image/jpeg',
                            'data': encoded,
                        }],
                    }],
                }

        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = _Response()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_subprocess = Mock()
        fake_subprocess.SubprocessError = subprocess.SubprocessError
        fake_subprocess.run.return_value = Mock(returncode=1, stdout=b'')
        namespace = _load_image_namespace(
            fake_httpx=fake_httpx,
            fake_subprocess=fake_subprocess,
        )

        with self.assertRaisesRegex(RuntimeError, 'invalid media'):
            namespace['_generate_gemini_image_descriptor']('prompt', 5)

        client.post.assert_called_once()
        fake_subprocess.run.assert_called_once()

    def test_image_probe_rejects_multiple_decoded_frames(self):
        encoded = base64.b64encode(_fake_jpeg()).decode('ascii')

        class _Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    'status': 'completed',
                    'steps': [{
                        'type': 'model_output',
                        'content': [{
                            'type': 'image',
                            'mime_type': 'image/jpeg',
                            'data': encoded,
                        }],
                    }],
                }

        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = _Response()
        fake_httpx = Mock()
        fake_httpx.Timeout.return_value = object()
        fake_httpx.Client.return_value = client
        fake_subprocess = Mock()
        fake_subprocess.SubprocessError = subprocess.SubprocessError
        fake_subprocess.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({
                'streams': [{
                    'codec_name': 'mjpeg',
                    'width': 1024,
                    'height': 576,
                    'nb_read_frames': '2',
                }],
            }).encode('utf-8'),
        )
        namespace = _load_image_namespace(
            fake_httpx=fake_httpx,
            fake_subprocess=fake_subprocess,
        )

        with self.assertRaisesRegex(RuntimeError, 'invalid media'):
            namespace['_generate_gemini_image_descriptor']('prompt', 5)

        client.post.assert_called_once()
        fake_subprocess.run.assert_called_once()

    def test_image_probe_rejects_non_jpeg_codec_after_autodetection(self):
        fake_subprocess = Mock()
        fake_subprocess.SubprocessError = subprocess.SubprocessError
        fake_subprocess.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({
                'streams': [{
                    'codec_name': 'png',
                    'width': 1024,
                    'height': 576,
                    'nb_read_frames': '1',
                }],
            }).encode('utf-8'),
        )
        namespace = _load_image_namespace(fake_subprocess=fake_subprocess)

        with self.assertRaisesRegex(RuntimeError, 'invalid media'):
            namespace['_probe_single_jpeg_frame'](
                _fake_jpeg(),
                (1024, 576),
            )

        fake_subprocess.run.assert_called_once()

    def test_image_decoder_rejects_mime_base64_size_aspect_and_pixel_bombs(self):
        namespace = _load_image_namespace()
        decode = namespace['_decode_gemini_image']
        cases = [
            ('image/png', base64.b64encode(_fake_jpeg()).decode('ascii')),
            ('image/jpeg', 'not valid base64***'),
            (
                'image/jpeg',
                base64.b64encode(_fake_jpeg()[:-2]).decode('ascii'),
            ),
            (
                'image/jpeg',
                base64.b64encode(_fake_jpeg(size=1024)).decode('ascii'),
            ),
            (
                'image/jpeg',
                base64.b64encode(_fake_jpeg(width=800, height=800)).decode('ascii'),
            ),
            (
                'image/jpeg',
                base64.b64encode(
                    _fake_jpeg(width=8192, height=4608)
                ).decode('ascii'),
            ),
        ]
        for mime_type, encoded in cases:
            with self.subTest(mime_type=mime_type, encoded_length=len(encoded)):
                with self.assertRaisesRegex(RuntimeError, 'invalid media'):
                    decode(mime_type, encoded)

    def test_image_motion_filter_is_dynamic_hash_deterministic_and_bounded(self):
        namespace = _load_image_namespace()
        build_filter = namespace['_image_motion_filter']
        expected_directions = {
            '00': (0.490, 0.060, 0.495, 0.025),
            '01': (0.510, -0.060, 0.495, 0.025),
            '02': (0.490, 0.060, 0.505, -0.025),
            '03': (0.510, -0.060, 0.505, -0.025),
        }

        for prefix, expected in expected_directions.items():
            with self.subTest(prefix=prefix):
                x_start, x_delta, y_start, y_delta = expected
                digest = prefix + ('a' * 62)
                motion_filter = build_filter(digest, 150)
                self.assertEqual(motion_filter, build_filter(digest, 150))
                self.assertIn(
                    'scale=2560:1440:force_original_aspect_ratio=increase:'
                    'flags=lanczos,crop=2560:1440',
                    motion_filter,
                )
                self.assertIn("z='1.06+0.18*on/149'", motion_filter)
                self.assertIn(
                    f"x='iw*({x_start:.3f}+({x_delta:.3f})*on/149)"
                    "-iw/(2*zoom)'",
                    motion_filter,
                )
                self.assertIn(
                    f"y='ih*({y_start:.3f}+({y_delta:.3f})*on/149)"
                    "-ih/(2*zoom)'",
                    motion_filter,
                )
                self.assertIn('d=150:s=1280x720:fps=30', motion_filter)
                self.assertNotIn('0.08*on', motion_filter)
                for start, delta in (
                    (x_start, x_delta),
                    (y_start, y_delta),
                ):
                    focal_centers = [
                        start + delta * frame / 149
                        for frame in range(150)
                    ]
                    comparisons = zip(focal_centers, focal_centers[1:])
                    if delta > 0:
                        self.assertTrue(all(left < right for left, right in comparisons))
                    else:
                        self.assertTrue(all(left > right for left, right in comparisons))

        ten_second_filter = build_filter('ff' + ('0' * 62), 300)
        self.assertIn("z='1.06+0.18*on/299'", ten_second_filter)
        self.assertIn('d=300:s=1280x720:fps=30', ten_second_filter)
        portrait_filter = build_filter('04' + ('0' * 62), 150, '9:16')
        self.assertIn(
            'scale=1440:2560:force_original_aspect_ratio=increase',
            portrait_filter,
        )
        self.assertIn('crop=1440:2560', portrait_filter)
        self.assertIn('d=150:s=720x1280:fps=30', portrait_filter)
        for digest, frames in (('bad', 150), ('0' * 64, 149), ('0' * 64, 301)):
            with self.subTest(digest=digest[:3], frames=frames):
                with self.assertRaisesRegex(RuntimeError, 'descriptor'):
                    build_filter(digest, frames)

    def test_image_motion_render_failure_preserves_target_and_cleans_parts(self):
        fake_subprocess = Mock()
        fake_subprocess.run.return_value = Mock(returncode=1)
        namespace = _load_image_namespace(fake_subprocess=fake_subprocess)
        image_bytes = _fake_jpeg()
        descriptor = {
            'provider': 'gemini_image_motion',
            '_inline_image': {
                'mime_type': 'image/jpeg',
                'data': base64.b64encode(image_bytes).decode('ascii'),
            },
            'motion_seconds': 5,
            'source_media_type': 'image',
            'synthetic_motion': True,
            'motion_recipe_version': 'diagonal-push-v2',
            'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'motion.mp4'
            output.write_bytes(b'existing-target')

            with self.assertRaisesRegex(RuntimeError, 'render failed'):
                namespace['download_generated_scene'](descriptor, output)

            self.assertEqual(output.read_bytes(), b'existing-target')
            self.assertFalse((Path(tmp) / 'motion.mp4.part').exists())
            self.assertFalse(
                (Path(tmp) / 'motion.mp4.part.source.jpg').exists()
            )
            self.assertNotIn('_inline_image', descriptor)
            render_args = fake_subprocess.run.call_args.args[0]
            render_filter = render_args[render_args.index('-vf') + 1]
            self.assertIn(
                'scale=in_range=full:out_range=tv,format=yuv420p',
                render_filter,
            )
            self.assertEqual(
                render_args[render_args.index('-color_range') + 1],
                'tv',
            )

    @unittest.skipUnless(
        shutil.which('ffmpeg') and shutil.which('ffprobe'),
        'ffmpeg and ffprobe are required',
    )
    def test_real_image_motion_render_is_exact_atomic_720p_video(self):
        namespace = _load_image_namespace()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source.jpg'
            output = Path(tmp) / 'motion.mp4'
            try:
                generated = subprocess.run([
                    'ffmpeg', '-y', '-hide_banner', '-nostats',
                    '-f', 'lavfi', '-i', 'testsrc2=size=1024x576:rate=1',
                    '-frames:v', '1', '-q:v', '2', str(source),
                ], capture_output=True, check=False)
            except OSError:
                self.skipTest('ffmpeg execution is blocked by the local sandbox')
            if generated.returncode != 0:
                self.skipTest('ffmpeg image generation is unavailable')
            image_bytes = source.read_bytes()
            if len(image_bytes) < 10 * 1024:
                self.skipTest('local ffmpeg produced an unusually small JPEG')
            namespace['_probe_single_jpeg_frame'](
                image_bytes,
                (1024, 576),
            )
            descriptor = {
                'provider': 'gemini_image_motion',
                '_inline_image': {
                    'mime_type': 'image/jpeg',
                    'data': base64.b64encode(image_bytes).decode('ascii'),
                },
                'motion_seconds': 5,
                'source_media_type': 'image',
                'synthetic_motion': True,
                'motion_recipe_version': 'diagonal-push-v2',
                'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
            }

            try:
                result = namespace['download_generated_scene'](
                    descriptor,
                    output,
                )
            except OSError:
                self.skipTest('ffmpeg execution is blocked by the local sandbox')

            self.assertEqual(result, str(output))
            self.assertTrue(output.is_file())
            self.assertNotIn('_inline_image', descriptor)
            self.assertFalse((Path(tmp) / 'motion.mp4.part').exists())
            self.assertFalse((Path(tmp) / 'motion.mp4.part.source.jpg').exists())
            probe = subprocess.check_output([
                'ffprobe', '-v', 'error', '-count_frames',
                '-select_streams', 'v:0',
                '-show_entries', (
                    'stream=width,height,pix_fmt,color_range,nb_read_frames'
                ),
                '-of', 'json', str(output),
            ], text=True)
            stream = json.loads(probe)['streams'][0]
            self.assertEqual(stream['width'], 1280)
            self.assertEqual(stream['height'], 720)
            self.assertEqual(stream['pix_fmt'], 'yuv420p')
            self.assertEqual(stream['color_range'], 'tv')
            self.assertEqual(int(stream['nb_read_frames']), 150)

    def test_image_prompt_preserves_review_evidence_and_closing_guardrails(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        )
        tree = ast.parse(source_path.read_text(encoding='utf-8'))
        definitions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                '_truncate_utf16',
                '_image_motion_prompt_for_scene',
            }
        ]
        from app.services.visual_identity import manufactured_replica_guardrail
        namespace = {
            'manufactured_replica_guardrail': (
                manufactured_replica_guardrail
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
        prompt = namespace['_image_motion_prompt_for_scene'](
            {
                'narration': 'Denizden çıkan aşınmış oyuncak. ' * 30,
                'ai_prompt': 'çok uzun ana görsel ' * 100,
                'visual_queries': ['unused fallback'],
            },
            {
                'retry_queries': [
                    'weathered plastic octopus on wet Cornwall sand',
                    'salt-faded toy with scratches and sea residue',
                    'must be ignored third hint',
                ],
                'reason': 'UNTRUSTED_REASON_MUST_NOT_APPEAR',
            },
        )

        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 1000)
        self.assertIn('weathered plastic octopus', prompt)
        self.assertIn('salt-faded toy', prompt)
        self.assertNotIn('must be ignored third hint', prompt)
        self.assertNotIn('UNTRUSTED_REASON_MUST_NOT_APPEAR', prompt)
        self.assertIn('MANUFACTURED IDENTITY', prompt)
        self.assertIn('Never a live or dead biological original', prompt)
        self.assertTrue(prompt.endswith('letterbox.'))

        portrait_prompt = namespace['_image_motion_prompt_for_scene'](
            {
                'narration': 'Telefon açık komodinde soğuyor.',
                'ai_prompt': 'Black phone on an open wooden nightstand.',
                'visual_queries': ['phone cooling on nightstand'],
            },
            None,
            '9:16',
        )
        self.assertIn('9:16 vertical documentary keyframe', portrait_prompt)
        self.assertIn('YouTube Shorts', portrait_prompt)
        self.assertIn('central safe area', portrait_prompt)

    def test_pipeline_passes_one_aspect_contract_to_prompts_and_providers(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')

        self.assertIn(
            'generation_aspect_ratio = aspect_ratio_for_mode(',
            source,
        )
        self.assertGreaterEqual(
            source.count('generation_aspect_ratio,'),
            4,
        )
        self.assertEqual(
            source.count('aspect_ratio=generation_aspect_ratio'),
            2,
        )

    def test_pipeline_limits_image_motion_to_private_preview_and_one_per_scene(self):
        source = (
            Path(__file__).resolve().parents[1]
            / 'app'
            / 'tasks.py'
        ).read_text(encoding='utf-8')
        self.assertIn(
            'allow_image_motion=is_private_image_motion_preview',
            source,
        )
        self.assertIn('duration_minutes == 0.5', source)
        self.assertIn(
            'scene_idx not in image_motion_submission_scenes',
            source,
        )
        self.assertIn(
            'isinstance(exc, GeminiImageAttemptedError)',
            source,
        )
        self.assertIn(
            "== 'gemini_image_motion'",
            source,
        )
        self.assertIn(
            'keep that exact clip private-review-only',
            source,
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

