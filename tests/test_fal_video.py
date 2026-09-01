import ast
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import urljoin, urlparse


REQUEST_ID = '123e4567-e89b-12d3-a456-426614174000'
MODEL = 'bytedance/seedance-2.0/fast/text-to-video'
QUEUE_ROOT = f'https://queue.fal.run/{MODEL}/requests/{REQUEST_ID}'


class _TimeoutException(Exception):
    pass


class _TransportError(Exception):
    pass


class _Settings:
    fal_key = 'private-test-key'


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = dict(headers or {})

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Client:
    def __init__(self, post_outcome, get_outcomes=()):
        self.post_outcome = post_outcome
        self.get_outcomes = list(get_outcomes)
        self.post_calls = []
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if isinstance(self.post_outcome, BaseException):
            raise self.post_outcome
        return self.post_outcome

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.get_outcomes:
            raise AssertionError('Unexpected Fal queue read')
        outcome = (
            self.get_outcomes.pop(0)
            if len(self.get_outcomes) > 1
            else self.get_outcomes[0]
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += float(seconds)


def _created_payload():
    return {
        'request_id': REQUEST_ID,
        'status_url': f'{QUEUE_ROOT}/status',
        'response_url': f'{QUEUE_ROOT}/response',
    }


def _load_namespace(client, *, fal_key='private-test-key'):
    fake_httpx = types.ModuleType('httpx')
    fake_httpx.TimeoutException = _TimeoutException
    fake_httpx.TransportError = _TransportError
    fake_httpx.Timeout = lambda *args, **kwargs: (args, kwargs)
    fake_httpx.Client = lambda **_kwargs: client

    fake_config = types.ModuleType('app.config')
    test_settings = _Settings()
    test_settings.fal_key = fal_key
    fake_config.settings = test_settings

    source_path = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'services'
        / 'fal_video.py'
    )
    namespace = {'__name__': 'test_loaded_fal_video'}
    with patch.dict(
        sys.modules,
        {'httpx': fake_httpx, 'app.config': fake_config},
    ):
        exec(
            compile(
                source_path.read_text(encoding='utf-8'),
                str(source_path),
                'exec',
            ),
            namespace,
        )
    namespace['time'] = _Clock()
    return namespace


class FalVideoQueueTests(unittest.TestCase):
    def test_success_uses_one_audio_free_720p_queue_submission(self):
        video_url = 'https://v3.fal.media/files/example/video.mp4'
        client = _Client(
            _Response(payload=_created_payload()),
            [
                _Response(payload={
                    'status': 'IN_QUEUE',
                    'request_id': REQUEST_ID,
                }),
                _Response(payload={
                    'status': 'COMPLETED',
                    'request_id': REQUEST_ID,
                }),
                _Response(payload={
                    'video': {
                        'url': video_url,
                        'content_type': 'video/mp4',
                        'file_size': 2_000_000,
                    },
                    'seed': 42,
                }),
            ],
        )
        namespace = _load_namespace(client)

        result = namespace['generate_fal_video']('safe prompt', 7)

        self.assertEqual(result, {
            'url': video_url,
            'provider': 'fal_seedance_2_fast',
            'provider_attempts': 1,
            'provider_request_id': REQUEST_ID,
        })
        self.assertEqual(len(client.post_calls), 1)
        submit_url, submit_kwargs = client.post_calls[0]
        self.assertEqual(
            submit_url,
            'https://queue.fal.run/bytedance/seedance-2.0/fast/text-to-video',
        )
        self.assertEqual(submit_kwargs['json'], {
            'prompt': 'safe prompt',
            'resolution': '720p',
            'duration': '7',
            'aspect_ratio': '16:9',
            'generate_audio': False,
            'bitrate_mode': 'standard',
        })
        self.assertEqual(
            submit_kwargs['headers']['Authorization'],
            'Key private-test-key',
        )
        self.assertEqual(
            [url for url, _kwargs in client.get_calls],
            [f'{QUEUE_ROOT}/status', f'{QUEUE_ROOT}/status', f'{QUEUE_ROOT}/response'],
        )

    def test_absent_key_does_not_construct_or_submit_a_client(self):
        client = _Client(AssertionError('must not submit'))
        namespace = _load_namespace(client, fal_key='')

        with self.assertRaises(namespace['FalVideoNotConfiguredError']):
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(client.post_calls, [])

    def test_explicit_submit_errors_have_content_free_taxonomy(self):
        cases = [
            (401, {}, 'FalVideoAuthError', True),
            (429, {}, 'FalVideoQuotaError', True),
            (
                422,
                {'detail': [{'type': 'content_policy_violation'}]},
                'FalVideoPolicyError',
                False,
            ),
            (503, {'error_type': 'runner_server_error'}, 'FalVideoTransientError', False),
        ]
        for status, payload, class_name, safe_to_fallback in cases:
            with self.subTest(status=status, class_name=class_name):
                client = _Client(_Response(status, payload))
                namespace = _load_namespace(client)

                with self.assertRaises(namespace[class_name]) as raised:
                    namespace['generate_fal_video']('safe prompt', 5)

                self.assertEqual(len(client.post_calls), 1)
                self.assertEqual(client.get_calls, [])
                self.assertEqual(
                    raised.exception.safe_to_fallback,
                    safe_to_fallback,
                )
                self.assertNotIn('private-test-key', str(raised.exception))

    def test_ambiguous_post_is_never_retried_or_fallen_through(self):
        client = _Client(_TimeoutException('secret transport detail'))
        namespace = _load_namespace(client)

        with self.assertRaises(namespace['FalVideoTransientError']) as raised:
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(client.get_calls, [])
        self.assertFalse(raised.exception.safe_to_fallback)
        self.assertIsNone(raised.exception.request_id)
        self.assertNotIn('secret transport detail', str(raised.exception))

    def test_success_without_request_id_is_ambiguous_and_never_polled(self):
        client = _Client(_Response(payload={'status_url': f'{QUEUE_ROOT}/status'}))
        namespace = _load_namespace(client)

        with self.assertRaises(namespace['FalVideoProtocolError']) as raised:
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertFalse(raised.exception.safe_to_fallback)
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(client.get_calls, [])

    def test_completed_result_read_retries_without_a_second_submission(self):
        video_url = 'https://v3.fal.media/files/example/video.mp4'
        client = _Client(
            _Response(payload=_created_payload()),
            [
                _Response(payload={
                    'status': 'COMPLETED',
                    'request_id': REQUEST_ID,
                }),
                _TransportError('temporary read failure'),
                _Response(payload={
                    'video': {
                        'url': video_url,
                        'content_type': 'video/mp4',
                    },
                }),
            ],
        )
        namespace = _load_namespace(client)

        result = namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(result['url'], video_url)
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(len(client.get_calls), 3)

    def test_polling_is_bounded_without_submitting_a_second_job(self):
        client = _Client(
            _Response(payload=_created_payload()),
            [_Response(payload={
                'status': 'IN_PROGRESS',
                'request_id': REQUEST_ID,
            })],
        )
        namespace = _load_namespace(client)
        namespace['_FAL_MAX_WAIT_SECONDS'] = 31.0
        namespace['_FAL_POLL_INTERVAL_SECONDS'] = 16.0

        with self.assertRaises(namespace['FalVideoTransientError']) as raised:
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(len(client.get_calls), 2)
        self.assertEqual(raised.exception.request_id, REQUEST_ID)
        self.assertFalse(raised.exception.safe_to_fallback)

    def test_definitive_completed_transient_failure_can_fall_through(self):
        client = _Client(
            _Response(payload=_created_payload()),
            [_Response(payload={
                'status': 'COMPLETED',
                'request_id': REQUEST_ID,
                'error': 'not inspected',
                'error_type': 'runner_server_error',
            })],
        )
        namespace = _load_namespace(client)

        with self.assertRaises(namespace['FalVideoTransientError']) as raised:
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(raised.exception.request_id, REQUEST_ID)
        self.assertTrue(raised.exception.safe_to_fallback)
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(len(client.get_calls), 1)

    def test_untrusted_media_url_is_rejected_after_same_request_completes(self):
        client = _Client(
            _Response(payload=_created_payload()),
            [
                _Response(payload={
                    'status': 'COMPLETED',
                    'request_id': REQUEST_ID,
                }),
                _Response(payload={
                    'video': {
                        'url': 'https://example.invalid/video.mp4',
                        'content_type': 'video/mp4',
                    },
                }),
            ],
        )
        namespace = _load_namespace(client)

        with self.assertRaises(namespace['FalVideoProtocolError']) as raised:
            namespace['generate_fal_video']('safe prompt', 5)

        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(raised.exception.request_id, REQUEST_ID)

    def test_malformed_media_port_is_rejected_without_parser_leakage(self):
        client = _Client(_Response(payload={}))
        namespace = _load_namespace(client)

        with self.assertRaises(namespace['FalVideoProtocolError']):
            namespace['validate_fal_media_url'](
                'https://v3.fal.media:not-a-port/video.mp4'
            )


class FalVideoDownloadTests(unittest.TestCase):
    @staticmethod
    def _load_download(fake_httpx):
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

        def validate_fal_media_url(candidate):
            parsed = urlparse(str(candidate or ''))
            host = (parsed.hostname or '').casefold()
            if parsed.scheme != 'https' or not (
                host == 'fal.media' or host.endswith('.fal.media')
            ):
                raise RuntimeError('untrusted Fal media URL')
            return str(candidate)

        namespace = {
            'Path': Path,
            'httpx': fake_httpx,
            'urljoin': urljoin,
            'urlparse': urlparse,
            'settings': types.SimpleNamespace(gemini_api_key='unused'),
            '_GEMINI_VIDEO_HOSTS': {
                'generativelanguage.googleapis.com',
                'storage.googleapis.com',
            },
            '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
            'validate_fal_media_url': validate_fal_media_url,
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(source_path),
                'exec',
            ),
            namespace,
        )
        return namespace['download_generated_scene']

    def test_fal_download_reuses_atomic_mp4_validation(self):
        video_bytes = b'\x00\x00\x00\x18ftypmp42' + (b'\x00' * 2048)

        class _StreamResponse:
            status_code = 200
            headers = {
                'content-type': 'video/mp4',
                'content-length': str(len(video_bytes)),
            }

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_bytes():
                return [video_bytes]

        stream_calls = []
        fake_httpx = types.SimpleNamespace()

        def stream(method, url, **kwargs):
            stream_calls.append((method, url, kwargs))
            return _StreamResponse()

        fake_httpx.stream = stream
        download = self._load_download(fake_httpx)
        descriptor = {
            'provider': 'fal_seedance_2_fast',
            'url': 'https://v3.fal.media/files/example/video.mp4',
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'fal.mp4'
            self.assertEqual(download(descriptor, output), str(output))
            self.assertEqual(output.read_bytes(), video_bytes)
            self.assertFalse((Path(tmp) / 'fal.mp4.part').exists())

        self.assertEqual(len(stream_calls), 1)
        self.assertEqual(stream_calls[0][2]['headers'], {})
        self.assertFalse(stream_calls[0][2]['follow_redirects'])

    def test_fal_download_rejects_untrusted_url_before_network_or_replace(self):
        fake_httpx = types.SimpleNamespace()
        fake_httpx.stream = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('must not fetch untrusted URL')
        )
        download = self._load_download(fake_httpx)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'fal.mp4'
            output.write_bytes(b'existing')
            with self.assertRaisesRegex(RuntimeError, 'untrusted'):
                download(
                    {
                        'provider': 'fal_seedance_2_fast',
                        'url': 'https://example.invalid/video.mp4',
                    },
                    output,
                )
            self.assertEqual(output.read_bytes(), b'existing')
            self.assertFalse((Path(tmp) / 'fal.mp4.part').exists())


if __name__ == '__main__':
    unittest.main()
