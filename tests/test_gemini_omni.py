import ast
import base64
import binascii
import json
from pathlib import Path
import re
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qsl, urljoin, urlparse


SOURCE_PATH = (
    Path(__file__).resolve().parents[1] / 'app' / 'services' / 'runway.py'
)
TASKS_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


class GeminiOmniPreAcceptanceFallbackError(RuntimeError):
    pass


class GeminiOmniTerminalError(RuntimeError):
    pass


class GeminiOmniContinuityReferenceError(RuntimeError):
    pass


class _Settings:
    gemini_api_key = 'configured-gemini-secret'
    runwayml_api_secret = 'configured-runway-secret'


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class _Client:
    def __init__(self, post_outcome, delete_outcomes=None):
        self.post_outcome = post_outcome
        self.delete_outcomes = list(delete_outcomes or [])
        self.post_calls = []
        self.delete_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        if isinstance(self.post_outcome, BaseException):
            raise self.post_outcome
        return self.post_outcome

    def delete(self, *args, **kwargs):
        self.delete_calls.append((args, kwargs))
        outcome = (
            self.delete_outcomes.pop(0)
            if self.delete_outcomes
            else _Response(200, {})
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _video_payload(*, data=None, uri=None, **overrides):
    video = {
        'type': 'video',
        'mime_type': 'video/mp4',
    }
    if data is not None:
        video['data'] = data
    if uri is not None:
        video['uri'] = uri
    payload = {
        'id': 'v1_private-interaction-id',
        'status': 'completed',
        'model': 'gemini-omni-1.1-flash',
        'object': 'interaction',
        'steps': [{
            'type': 'model_output',
            'content': [video],
        }],
    }
    payload.update(overrides)
    return payload


def _load_omni_namespace(client):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    names = {
        '_normalize_runway_provider_code',
        '_gemini_omni_response_allows_provider_fallback',
        '_gemini_omni_prompt',
        '_read_bounded_gemini_omni_reference_image',
        '_gemini_omni_file_id',
        '_best_effort_delete_gemini_omni_resource',
        '_generate_gemini_omni_video',
    }
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    stored = []
    downloaded = []
    validated = []

    def store_video(data, *, minimum_seconds):
        stored.append((data, minimum_seconds))
        return 'validated-omni-output.mp4'

    def download_video(_client, uri, headers, *, minimum_seconds):
        downloaded.append((uri, dict(headers), minimum_seconds))
        return 'validated-omni-uri-output.mp4'

    def validate_image(path):
        validated.append(Path(path).read_bytes())

    httpx = SimpleNamespace(
        Timeout=lambda *args, **kwargs: (args, kwargs),
        Client=lambda **_kwargs: client,
    )
    namespace = {
        'Path': Path,
        'base64': base64,
        'binascii': binascii,
        'json': json,
        'parse_qsl': parse_qsl,
        're': re,
        'tempfile': tempfile,
        'urlparse': urlparse,
        'httpx': httpx,
        'settings': _Settings(),
        'GeminiOmniPreAcceptanceFallbackError': (
            GeminiOmniPreAcceptanceFallbackError
        ),
        'GeminiOmniTerminalError': GeminiOmniTerminalError,
        '_GEMINI_OMNI_MODEL': 'gemini-omni-1.1-flash',
        '_GEMINI_OMNI_ENDPOINT': (
            'https://generativelanguage.googleapis.com/v1beta/interactions'
        ),
        '_GEMINI_OMNI_ASPECT_RATIO': '9:16',
        '_GEMINI_OMNI_RESOLUTION': '720p',
        '_GEMINI_OMNI_MIN_SECONDS': 3.0,
        '_GEMINI_OMNI_MAX_SECONDS': 10.0,
        '_GEMINI_OMNI_REFERENCE_IMAGE_WIDTH': 720,
        '_GEMINI_OMNI_REFERENCE_IMAGE_HEIGHT': 1280,
        '_MAX_GEMINI_OMNI_REFERENCE_IMAGE_BYTES': 4 * 1024 * 1024,
        '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        '_GEMINI_OMNI_CAPACITY_CODES': frozenset({
            'capacity_exhausted',
            'capacity_unavailable',
            'overloaded',
            'quota_exceeded',
            'rate_limit_exceeded',
            'resource_exhausted',
            'service_unavailable',
            'unavailable',
        }),
        '_GEMINI_OMNI_FILE_ID_PATTERN': re.compile(
            r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$'
        ),
        '_GEMINI_OMNI_INTERACTION_ID_PATTERN': re.compile(
            r'^v1_[A-Za-z0-9_-]{1,253}$'
        ),
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    namespace['_store_validated_gemini_omni_video'] = store_video
    namespace['_download_gemini_omni_uri'] = download_video
    namespace['_validated_gemini_omni_reference_image_file'] = validate_image
    namespace['_test_stored'] = stored
    namespace['_test_downloaded'] = downloaded
    namespace['_test_validated'] = validated
    return namespace


class GeminiOmniRequestTests(unittest.TestCase):
    def test_text_request_is_retry_free_portrait_720p_and_secret_safe(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'x' * 2048
        client = _Client(_Response(
            200,
            _video_payload(data=base64.b64encode(media).decode('ascii')),
        ))
        namespace = _load_omni_namespace(client)

        result = namespace['_generate_gemini_omni_video'](
            'A hand places a small wooden boat into moving water.',
            5,
        )

        self.assertEqual(len(client.post_calls), 1)
        args, kwargs = client.post_calls[0]
        self.assertEqual(
            args[0],
            'https://generativelanguage.googleapis.com/v1beta/interactions',
        )
        payload = kwargs['json']
        self.assertEqual(payload['model'], 'gemini-omni-1.1-flash')
        self.assertEqual(payload['response_format'], {
            'type': 'video',
            'delivery': 'uri',
            'aspect_ratio': '9:16',
            'resolution': '720p',
        })
        self.assertIs(payload['background'], False)
        self.assertIs(payload['store'], True)
        self.assertIs(payload['stream'], False)
        self.assertIsInstance(payload['input'], str)
        self.assertIn('exactly 5 seconds', payload['input'])
        self.assertIn('single continuous unbroken shot', payload['input'])
        self.assertIn('no scene cuts', payload['input'])
        self.assertNotIn('phone', payload['input'].casefold())
        self.assertNotIn('room', payload['input'].casefold())
        self.assertNotIn('generation_config', payload)
        self.assertEqual(namespace['_test_stored'], [(media, 5)])
        self.assertEqual(result['provider'], 'gemini_omni')
        serialized = json.dumps(result)
        self.assertNotIn('v1_private-interaction-id', serialized)
        self.assertNotIn(_Settings.gemini_api_key, serialized)
        self.assertNotIn('wooden boat', serialized)
        self.assertEqual(len(client.delete_calls), 1)
        self.assertEqual(
            client.delete_calls[0][0][0],
            'https://generativelanguage.googleapis.com/v1beta/'
            'interactions/v1_private-interaction-id',
        )
        self.assertNotIn('?', client.delete_calls[0][0][0])

    def test_reference_request_uses_private_image_input_and_subject_contract(self):
        reference = b'\xff\xd8\xff' + b'r' * 2048 + b'\xff\xd9'
        output = b'\x00\x00\x00\x18ftypisom' + b'o' * 2048
        client = _Client(_Response(
            200,
            _video_payload(data=base64.b64encode(output).decode('ascii')),
        ))
        namespace = _load_omni_namespace(client)

        result = namespace['_generate_gemini_omni_video'](
            'The same LEGO ship now reaches a bright beach at sunrise.',
            6,
            continuity_reference_image=reference,
        )

        payload = client.post_calls[0][1]['json']
        self.assertIs(payload['store'], True)
        self.assertNotIn('generation_config', payload)
        self.assertEqual(len(payload['input']), 2)
        image_input = payload['input'][0]
        self.assertEqual(image_input['type'], 'image')
        self.assertEqual(image_input['mime_type'], 'image/jpeg')
        self.assertEqual(base64.b64decode(image_input['data']), reference)
        prompt = payload['input'][1]['text']
        self.assertIn('<IMAGE_REF_0>', prompt)
        self.assertIn('subject identity reference', prompt)
        self.assertIn('do not use it as a literal initial frame', prompt)
        self.assertIn('current scene direction is authoritative', prompt)
        self.assertIn('primary subject, object or person identity', prompt)
        self.assertIn('authored non-text physical markings', prompt)
        self.assertIn('non-diegetic graphic or interface pixels', prompt)
        self.assertIn('raw camera footage only', prompt)
        self.assertIn('narrated setting or time transition', prompt)
        self.assertNotIn('phone', prompt.casefold())
        self.assertNotIn('room', prompt.casefold())
        self.assertIn('LEGO ship', prompt)
        self.assertEqual(namespace['_test_validated'], [reference])
        serialized = json.dumps(result)
        self.assertNotIn(image_input['data'], serialized)
        self.assertNotIn('v1_private-interaction-id', serialized)
        self.assertEqual(len(client.delete_calls), 1)

    def test_reference_is_omitted_without_an_explicit_private_anchor(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'x' * 2048
        client = _Client(_Response(
            200,
            _video_payload(data=base64.b64encode(media).decode('ascii')),
        ))
        namespace = _load_omni_namespace(client)
        namespace['_generate_gemini_omni_video']('literal action', 3)
        payload = client.post_calls[0][1]['json']
        self.assertIsInstance(payload['input'], str)
        self.assertNotIn('<IMAGE_REF_0>', payload['input'])
        self.assertEqual(namespace['_test_validated'], [])

    def test_mp4_cannot_enter_the_subject_reference_image_contract(self):
        client = _Client(Mock())
        namespace = _load_omni_namespace(client)
        mp4 = b'\x00\x00\x00\x18ftypisom' + b'r' * 2048
        with self.assertRaisesRegex(ValueError, 'not a JPEG'):
            namespace['_generate_gemini_omni_video'](
                'safe action',
                5,
                continuity_reference_image=mp4,
            )
        self.assertEqual(client.post_calls, [])

    def test_interaction_cleanup_failure_keeps_validated_media(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'x' * 2048
        client = _Client(
            _Response(
                200,
                _video_payload(data=base64.b64encode(media).decode('ascii')),
            ),
            [TimeoutError('private cleanup detail'), _Response(503, {})],
        )
        namespace = _load_omni_namespace(client)

        result = namespace['_generate_gemini_omni_video']('safe action', 3)

        self.assertEqual(result['_local_video_path'], 'validated-omni-output.mp4')
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(len(client.delete_calls), 2)
        self.assertEqual(len(namespace['_test_stored']), 1)

    def test_uri_output_accepts_only_official_file_contract(self):
        uri = (
            'https://generativelanguage.googleapis.com/v1beta/'
            'files/abc-123-xy:download?alt=media'
        )
        client = _Client(_Response(200, _video_payload(uri=uri)))
        namespace = _load_omni_namespace(client)
        result = namespace['_generate_gemini_omni_video']('action', 4)
        self.assertEqual(result['_local_video_path'], 'validated-omni-uri-output.mp4')
        self.assertEqual(namespace['_test_downloaded'][0][0], uri)
        file_id = namespace['_gemini_omni_file_id']
        for untrusted in (
            'https://evil.invalid/v1beta/files/abc:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media&key=secret',
            'http://generativelanguage.googleapis.com/v1beta/files/abc:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/../abc:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/Abc:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/abc_def:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/-abc:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/abc-:download?alt=media',
            'https://generativelanguage.googleapis.com/v1beta/files/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:download?alt=media',
        ):
            with self.subTest(uri=untrusted):
                with self.assertRaises(GeminiOmniTerminalError):
                    file_id(untrusted)

    def test_only_explicit_preacceptance_capacity_can_fall_through(self):
        namespace = _load_omni_namespace(_Client(_Response(200, {})))
        allows = namespace['_gemini_omni_response_allows_provider_fallback']
        self.assertTrue(allows(_Response(429, {'error': {}})))
        self.assertFalse(allows(_Response(503, {
            'error': {'status': 'RESOURCE_EXHAUSTED'},
        })))
        self.assertFalse(allows(_Response(503, {
            'error': {'status': 'INTERNAL'},
        })))
        self.assertFalse(allows(_Response(500, {
            'error': {'status': 'RESOURCE_EXHAUSTED'},
        })))

    def test_ambiguous_transport_is_never_retried(self):
        client = _Client(TimeoutError('provider body must stay private'))
        namespace = _load_omni_namespace(client)
        with self.assertRaisesRegex(
            GeminiOmniTerminalError,
            'acceptance is unknown',
        ):
            namespace['_generate_gemini_omni_video']('safe prompt', 5)
        self.assertEqual(len(client.post_calls), 1)

    def test_malformed_or_terminal_accepted_responses_fail_closed(self):
        media = base64.b64encode(
            b'\x00\x00\x00\x18ftypisom' + b'x' * 2048
        ).decode('ascii')
        valid = _video_payload(data=media)
        cases = []
        for key, value in (
            ('status', 'in_progress'),
            ('model', 'gemini-omni-preview'),
            ('object', 'response'),
            ('id', None),
            ('id', 'v1_bad/../interaction'),
            ('id', 'v1_' + ('a' * 254)),
        ):
            payload = dict(valid)
            payload[key] = value
            cases.append(payload)
        duplicate = dict(valid)
        duplicate['steps'] = valid['steps'] * 2
        cases.append(duplicate)
        wrong_mime = _video_payload(data=media)
        wrong_mime['steps'][0]['content'][0]['mime_type'] = 'text/html'
        cases.append(wrong_mime)
        both = _video_payload(data=media, uri=(
            'https://generativelanguage.googleapis.com/v1beta/'
            'files/abc:download?alt=media'
        ))
        cases.append(both)
        for payload in cases:
            with self.subTest(payload=payload):
                client = _Client(_Response(200, payload))
                namespace = _load_omni_namespace(client)
                with self.assertRaises(GeminiOmniTerminalError):
                    namespace['_generate_gemini_omni_video']('safe', 5)
                self.assertEqual(len(client.post_calls), 1)
                has_valid_interaction_id = (
                    isinstance(payload.get('id'), str)
                    and re.fullmatch(
                        r'^v1_[A-Za-z0-9_-]{1,253}$',
                        payload['id'],
                    )
                    is not None
                )
                self.assertEqual(
                    len(client.delete_calls),
                    1 if has_valid_interaction_id else 0,
                )
                self.assertEqual(namespace['_test_stored'], [])


def _load_reference_image_validator(fake_subprocess):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_validated_gemini_omni_reference_image_file'
    )
    namespace = {
        'Path': Path,
        'json': json,
        'subprocess': fake_subprocess,
        '_GEMINI_OMNI_REFERENCE_IMAGE_WIDTH': 720,
        '_GEMINI_OMNI_REFERENCE_IMAGE_HEIGHT': 1280,
        '_MAX_GEMINI_OMNI_REFERENCE_IMAGE_BYTES': 4 * 1024 * 1024,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_validated_gemini_omni_reference_image_file']


def _reference_image_probe(*, width=720, height=1280, codec_name='mjpeg'):
    payload = {
        'streams': [{
            'codec_name': codec_name,
            'codec_type': 'video',
            'width': width,
            'height': height,
        }],
    }
    return SimpleNamespace(
        run=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
        )
    )


def _anchor_candidate_review(
    index,
    *,
    text=False,
    logo=False,
    social_ui=False,
    artifact=False,
    subject=True,
    detail=80,
):
    return {
        'candidate_index': index,
        'readable_text_visible': text,
        'logo_or_watermark_visible': logo,
        'social_ui_or_handle_visible': social_ui,
        'major_visual_artifact_visible': artifact,
        'primary_subject_clear': subject,
        'detail_score': detail,
    }


def _load_reference_candidate_selector(outcome):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_select_gemini_omni_continuity_candidate'
    )
    calls = []

    def generate(parts, **kwargs):
        calls.append((parts, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    schema = {'sentinel': 'anchor-qc-schema'}
    namespace = {
        'Path': Path,
        'settings': SimpleNamespace(
            gemini_api_key='configured-gemini-secret',
            gemini_model='configured-critic-model',
        ),
        'GEMINI_DEFAULT_MODEL': 'default-critic-model',
        'GeminiOmniContinuityReferenceError': (
            GeminiOmniContinuityReferenceError
        ),
        '_GEMINI_OMNI_ANCHOR_QC_SCHEMA': schema,
        '_validated_gemini_omni_reference_image_file': lambda _path: None,
        'generate_gemini_multimodal_json': generate,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace[function.name], calls, schema


class GeminiOmniReferenceImageTests(unittest.TestCase):
    def test_anchor_qc_schema_is_strict_for_all_three_candidates(self):
        tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == '_GEMINI_OMNI_ANCHOR_QC_SCHEMA'
                for target in node.targets
            )
        )
        schema = ast.literal_eval(assignment.value)
        self.assertIs(schema['additionalProperties'], False)
        candidates = schema['properties']['candidates']
        self.assertEqual(candidates['minItems'], 3)
        self.assertEqual(candidates['maxItems'], 3)
        item = candidates['items']
        self.assertIs(item['additionalProperties'], False)
        self.assertEqual(
            set(item['required']),
            set(item['properties']),
        )
        for field in (
            'readable_text_visible',
            'logo_or_watermark_visible',
            'social_ui_or_handle_visible',
            'major_visual_artifact_visible',
            'primary_subject_clear',
        ):
            self.assertEqual(item['properties'][field], {'type': 'boolean'})

    def test_validator_requires_one_exact_portrait_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / 'valid.jpg'
            valid.write_bytes(b'\xff\xd8\xff' + b'i' * 2048 + b'\xff\xd9')
            _load_reference_image_validator(_reference_image_probe())(valid)

            for name, data, probe in (
                ('wrong-codec.jpg', valid.read_bytes(), _reference_image_probe(
                    codec_name='png'
                )),
                ('wrong-size.jpg', valid.read_bytes(), _reference_image_probe(
                    width=1280, height=720
                )),
                ('wrong-magic.jpg', b'n' * 2053, _reference_image_probe()),
            ):
                with self.subTest(name=name):
                    candidate = root / name
                    candidate.write_bytes(data)
                    with self.assertRaises(ValueError):
                        _load_reference_image_validator(probe)(candidate)

    def test_clean_candidate_gate_overrides_largest_file_proxy(self):
        outcome = {
            'candidates': [
                _anchor_candidate_review(2, detail=70),
                _anchor_candidate_review(0, logo=True, detail=100),
                _anchor_candidate_review(1, detail=90),
            ],
        }
        select, calls, schema = _load_reference_candidate_selector(outcome)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = [root / f'candidate-{index}.jpg' for index in range(3)]
            candidate_bytes = []
            for index, candidate in enumerate(candidates):
                frame_bytes = (
                    b'\xff\xd8\xff' + b'i' * (6000 - index * 1000) + b'\xff\xd9'
                )
                candidate.write_bytes(frame_bytes)
                candidate_bytes.append(frame_bytes)
            self.assertEqual(select(candidates), candidates[1])

        self.assertEqual(len(calls), 1)
        parts, kwargs = calls[0]
        self.assertEqual(
            [part['image_bytes'] for part in parts if 'image_bytes' in part],
            candidate_bytes,
        )
        self.assertEqual(kwargs['api_key'], 'configured-gemini-secret')
        self.assertEqual(kwargs['model'], 'configured-critic-model')
        self.assertIs(kwargs['json_schema'], schema)
        self.assertEqual(kwargs['thinking_level'], 'low')
        self.assertEqual(kwargs['timeout'], 90.0)
        self.assertIs(kwargs['retry_once'], True)
        self.assertIn('fail-closed', kwargs['system_instruction'])
        self.assertIn('@handles', kwargs['system_instruction'])

    def test_clean_candidate_gate_fails_closed_without_a_safe_frame(self):
        outcome = {
            'candidates': [
                _anchor_candidate_review(0, text=True),
                _anchor_candidate_review(1, social_ui=True),
                _anchor_candidate_review(2, subject=False),
            ],
        }
        select, _calls, _schema = _load_reference_candidate_selector(outcome)
        with tempfile.TemporaryDirectory() as directory:
            candidates = []
            for index in range(3):
                candidate = Path(directory) / f'candidate-{index}.jpg'
                candidate.write_bytes(b'\xff\xd8\xffframe\xff\xd9')
                candidates.append(candidate)
            with self.assertRaises(GeminiOmniContinuityReferenceError):
                select(candidates)

    def test_clean_candidate_gate_rejects_ambiguous_indices_and_provider_errors(self):
        invalid_outcomes = (
            {
                'candidates': [
                    _anchor_candidate_review(0),
                    _anchor_candidate_review(0),
                    _anchor_candidate_review(2),
                ],
            },
            {
                'candidates': [
                    _anchor_candidate_review(False),
                    _anchor_candidate_review(1),
                    _anchor_candidate_review(2),
                ],
            },
            RuntimeError('provider failed'),
        )
        with tempfile.TemporaryDirectory() as directory:
            candidates = []
            for index in range(3):
                candidate = Path(directory) / f'candidate-{index}.jpg'
                candidate.write_bytes(b'\xff\xd8\xffframe\xff\xd9')
                candidates.append(candidate)
            for outcome in invalid_outcomes:
                with self.subTest(outcome=outcome):
                    select, _calls, _schema = (
                        _load_reference_candidate_selector(outcome)
                    )
                    with self.assertRaises(
                        GeminiOmniContinuityReferenceError
                    ):
                        select(candidates)

    def test_creator_extracts_three_jpegs_and_replaces_selected_atomically(self):
        tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == 'create_gemini_omni_continuity_reference'
        )
        commands = []

        def render(command, **_kwargs):
            if command[0] == 'ffprobe':
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({'format': {'duration': '3.0'}}),
                )
            commands.append(command)
            sample_seconds = command[command.index('-ss') + 1]
            detail_bytes = {
                '1.500': 2048,
                '0.750': 2600,
                '2.250': 2300,
            }[sample_seconds]
            Path(command[-1]).write_bytes(
                b'\xff\xd8\xff' + b'i' * detail_bytes + b'\xff\xd9'
            )
            return SimpleNamespace(returncode=0)

        validated = []

        def validate(path):
            validated.append(Path(path).read_bytes())

        selector = Mock(side_effect=lambda values: values[1])
        namespace = {
            'Path': Path,
            'json': json,
            'subprocess': SimpleNamespace(run=render),
            'GeminiOmniContinuityReferenceError': type(
                'GeminiOmniContinuityReferenceError',
                (RuntimeError,),
                {},
            ),
            '_GEMINI_OMNI_MAX_SECONDS': 10.0,
            '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
            '_validated_gemini_omni_reference_image_file': validate,
            '_select_gemini_omni_continuity_candidate': selector,
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(SOURCE_PATH),
                'exec',
            ),
            namespace,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            output = root / 'anchor.jpg'
            source.write_bytes(
                b'\x00\x00\x00\x18ftypisom' + b'v' * 2048
            )
            result = namespace[
                'create_gemini_omni_continuity_reference'
            ](source, output)
            self.assertEqual(result, str(output))
            self.assertTrue(output.exists())
            self.assertEqual(len(commands), 3)
            self.assertEqual(len(validated), 3)
            self.assertEqual(output.read_bytes(), validated[1])
            selector.assert_called_once()
            for index in range(3):
                self.assertFalse(
                    (root / f'anchor.candidate-{index}.jpg').exists()
                )
            self.assertEqual(
                [
                    command[command.index('-ss') + 1]
                    for command in commands
                ],
                ['1.500', '0.750', '2.250'],
            )
            for command in commands:
                self.assertEqual(
                    command[command.index('-frames:v') + 1],
                    '1',
                )
                self.assertEqual(command[command.index('-f') + 1], 'image2')


def _load_validator(fake_subprocess):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_validated_gemini_omni_video_file'
    )
    namespace = {
        'Path': Path,
        'json': json,
        'subprocess': fake_subprocess,
        '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        '_GEMINI_OMNI_MIN_SECONDS': 3.0,
        '_GEMINI_OMNI_MAX_SECONDS': 10.0,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_validated_gemini_omni_video_file']


class GeminiOmniMediaContractTests(unittest.TestCase):
    def _probe(
        self,
        *,
        width=720,
        height=1280,
        duration=5.0,
        rate='24/1',
        sar='1:1',
        dar='9:16',
        rotation=None,
    ):
        stream = {
            'codec_type': 'video',
            'width': width,
            'height': height,
            'r_frame_rate': rate,
            'nb_read_frames': '120',
        }
        if sar is not None:
            stream['sample_aspect_ratio'] = sar
        if dar is not None:
            stream['display_aspect_ratio'] = dar
        if rotation is not None:
            stream['side_data_list'] = [{'rotation': rotation}]
        payload = {
            'streams': [stream],
            'format': {'duration': str(duration)},
        }
        return SimpleNamespace(
            run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload),
            )
        )

    def test_validator_requires_portrait_720p_24fps_and_three_to_ten_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 2048)
            self.assertEqual(_load_validator(self._probe())(path, minimum_seconds=5), 5.0)
            for probe in (
                self._probe(width=1280, height=720),
                self._probe(rate='30/1'),
                self._probe(rotation=90),
                self._probe(duration=2.9),
                self._probe(duration=10.2),
            ):
                with self.subTest(probe=probe):
                    with self.assertRaises(RuntimeError):
                        _load_validator(probe)(path, minimum_seconds=3)

    def test_validator_accepts_missing_sar_for_unrotated_portrait_display(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 2048)
            validator = _load_validator(self._probe(sar=None, dar=None))
            self.assertEqual(validator(path, minimum_seconds=5), 5.0)

    def test_validator_rejects_explicit_non_square_sar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 2048)
            validator = _load_validator(self._probe(sar='4:3', dar='9:16'))
            with self.assertRaises(RuntimeError):
                validator(path, minimum_seconds=5)

    def test_validator_rejects_conflicting_display_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'clip.mp4'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 2048)
            validator = _load_validator(self._probe(sar=None, dar='3:4'))
            with self.assertRaises(RuntimeError):
                validator(path, minimum_seconds=5)


class _StreamResponse:
    def __init__(self, status_code, headers, chunks=()):
        self.status_code = status_code
        self.headers = headers
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        return iter(self._chunks)


class _URIClient:
    def __init__(self, get_outcomes, stream_outcomes, delete_outcomes=None):
        self.get_outcomes = list(get_outcomes)
        self.stream_outcomes = list(stream_outcomes)
        self.delete_outcomes = list(delete_outcomes or [])
        self.get_calls = []
        self.stream_calls = []
        self.delete_calls = []

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        outcome = self.get_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def stream(self, *args, **kwargs):
        self.stream_calls.append((args, kwargs))
        outcome = self.stream_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def delete(self, *args, **kwargs):
        self.delete_calls.append((args, kwargs))
        outcome = (
            self.delete_outcomes.pop(0)
            if self.delete_outcomes
            else _Response(200, {})
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _load_uri_downloader(client, validator):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    names = {
        '_gemini_omni_file_id',
        '_best_effort_delete_gemini_omni_resource',
        '_download_gemini_omni_uri',
    }
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        'Path': Path,
        'parse_qsl': parse_qsl,
        're': re,
        'tempfile': tempfile,
        'time': SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _value: None),
        'urljoin': urljoin,
        'urlparse': urlparse,
        'GeminiOmniTerminalError': GeminiOmniTerminalError,
        '_GEMINI_VIDEO_BASE': (
            'https://generativelanguage.googleapis.com/v1beta'
        ),
        '_GEMINI_VIDEO_HOSTS': {
            'generativelanguage.googleapis.com',
            'storage.googleapis.com',
        },
        '_GEMINI_OMNI_FILE_ID_PATTERN': re.compile(
            r'^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$'
        ),
        '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        '_validated_gemini_omni_video_file': validator,
    }
    exec(
        compile(
            ast.Module(body=definitions, type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_download_gemini_omni_uri']


class GeminiOmniURIRetrievalTests(unittest.TestCase):
    def test_official_file_is_polled_downloaded_bounded_and_validated(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'v' * 2048
        client = _URIClient(
            [_Response(200, {'state': 'ACTIVE'})],
            [_StreamResponse(200, {'content-type': 'video/mp4'}, [media])],
        )
        validation_calls = []

        def validate(path, *, minimum_seconds):
            validation_calls.append((Path(path).read_bytes(), minimum_seconds))
            return 5.0

        download = _load_uri_downloader(client, validate)
        output = Path(download(
            client,
            (
                'https://generativelanguage.googleapis.com/v1beta/'
                'files/abc-123:download?alt=media'
            ),
            {'x-goog-api-key': 'private-key'},
            minimum_seconds=5,
        ))
        try:
            self.assertEqual(output.read_bytes(), media)
            self.assertEqual(validation_calls, [(media, 5)])
            self.assertEqual(len(client.get_calls), 1)
            self.assertEqual(len(client.stream_calls), 1)
            self.assertEqual(
                client.stream_calls[0][0][1],
                'https://generativelanguage.googleapis.com/v1beta/'
                'files/abc-123:download?alt=media',
            )
            self.assertEqual(
                client.stream_calls[0][1]['headers'],
                {'x-goog-api-key': 'private-key'},
            )
            self.assertEqual(len(client.delete_calls), 1)
            self.assertEqual(
                client.delete_calls[0][0][0],
                'https://generativelanguage.googleapis.com/v1beta/'
                'files/abc-123',
            )
            self.assertEqual(
                client.delete_calls[0][1]['headers'],
                {'x-goog-api-key': 'private-key'},
            )
            self.assertEqual(client.delete_calls[0][1]['timeout'], 10.0)
        finally:
            output.unlink(missing_ok=True)

    def test_cleanup_failure_keeps_validated_local_video(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'v' * 2048
        client = _URIClient(
            [_Response(200, {'state': 'ACTIVE'})],
            [_StreamResponse(200, {'content-type': 'video/mp4'}, [media])],
            [TimeoutError('private cleanup detail'), _Response(503, {})],
        )
        download = _load_uri_downloader(client, lambda *_a, **_k: 5.0)
        output = Path(download(
            client,
            (
                'https://generativelanguage.googleapis.com/v1beta/'
                'files/abc:download?alt=media'
            ),
            {'x-goog-api-key': 'private-key'},
            minimum_seconds=5,
        ))
        try:
            self.assertEqual(output.read_bytes(), media)
            self.assertEqual(len(client.delete_calls), 2)
            self.assertEqual(len(client.stream_calls), 1)
        finally:
            output.unlink(missing_ok=True)

    def test_untrusted_redirect_fails_without_forwarding_api_key(self):
        client = _URIClient(
            [_Response(200, {'state': 'ACTIVE'})],
            [_StreamResponse(302, {'location': 'https://evil.invalid/video.mp4'})],
        )
        validator = Mock()
        download = _load_uri_downloader(client, validator)
        with self.assertRaises(GeminiOmniTerminalError):
            download(
                client,
                (
                    'https://generativelanguage.googleapis.com/v1beta/'
                    'files/abc:download?alt=media'
                ),
                {'x-goog-api-key': 'private-key'},
                minimum_seconds=5,
            )
        self.assertEqual(len(client.stream_calls), 1)
        self.assertEqual(client.delete_calls, [])
        validator.assert_not_called()

    def test_post_acceptance_status_transport_error_fails_closed(self):
        client = _URIClient([TimeoutError('private provider detail')], [])
        download = _load_uri_downloader(client, Mock())
        with self.assertRaisesRegex(
            GeminiOmniTerminalError,
            'retrieval was ambiguous',
        ):
            download(
                client,
                (
                    'https://generativelanguage.googleapis.com/v1beta/'
                    'files/abc:download?alt=media'
                ),
                {'x-goog-api-key': 'private-key'},
                minimum_seconds=5,
            )
        self.assertEqual(len(client.get_calls), 1)
        self.assertEqual(client.stream_calls, [])
        self.assertEqual(client.delete_calls, [])

    def test_download_validation_failure_is_terminal_and_cleans_temp_file(self):
        media = b'\x00\x00\x00\x18ftypisom' + b'v' * 2048
        client = _URIClient(
            [_Response(200, {'state': 'ACTIVE'})],
            [_StreamResponse(200, {'content-type': 'video/mp4'}, [media])],
        )
        probed_paths = []

        def reject(path, *, minimum_seconds):
            probed_paths.append(Path(path))
            raise RuntimeError('private ffprobe output')

        download = _load_uri_downloader(client, reject)
        with self.assertRaisesRegex(
            GeminiOmniTerminalError,
            'failed validation',
        ):
            download(
                client,
                (
                    'https://generativelanguage.googleapis.com/v1beta/'
                    'files/abc:download?alt=media'
                ),
                {'x-goog-api-key': 'private-key'},
                minimum_seconds=5,
            )
        self.assertEqual(len(probed_paths), 1)
        self.assertFalse(probed_paths[0].exists())
        self.assertEqual(client.delete_calls, [])


def _load_generated_scene_downloader(validator):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == 'download_generated_scene'
    )
    namespace = {
        'Path': Path,
        'urljoin': urljoin,
        'urlparse': urlparse,
        'httpx': Mock(),
        'settings': _Settings(),
        '_GEMINI_VIDEO_HOSTS': {
            'generativelanguage.googleapis.com',
            'storage.googleapis.com',
        },
        '_MAX_GENERATED_VIDEO_BYTES': 100 * 1024 * 1024,
        '_GEMINI_OMNI_MIN_SECONDS': 3.0,
        '_validated_gemini_omni_video_file': validator,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['download_generated_scene']


class GeminiOmniLocalHandoffTests(unittest.TestCase):
    def test_copy_validation_failure_cleans_source_and_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'provider-temp.mp4'
            output = root / 'scene.mp4'
            source.write_bytes(
                b'\x00\x00\x00\x18ftypisom' + b'v' * 2048
            )
            download = _load_generated_scene_downloader(
                Mock(side_effect=RuntimeError('invalid portrait'))
            )
            with self.assertRaises(RuntimeError):
                download({
                    'provider': 'gemini_omni',
                    '_local_video_path': str(source),
                }, output)
            self.assertFalse(source.exists())
            self.assertFalse(output.exists())
            self.assertFalse((root / 'scene.mp4.part').exists())


class _RunwayFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get('max_retries') == 0:
            return object()

        class _Retrieved:
            output = ['runway-output']

            def wait_for_task_output(self, **_kwargs):
                return self

        class _Tasks:
            @staticmethod
            def retrieve(_task_id):
                return _Retrieved()

        return SimpleNamespace(tasks=_Tasks())


def _load_generate_scene(omni, runway_factory):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'generate_scene'
    )
    created = SimpleNamespace(id='runway-task')
    namespace = {
        'GeminiOmniPreAcceptanceFallbackError': (
            GeminiOmniPreAcceptanceFallbackError
        ),
        'GeminiOmniTerminalError': GeminiOmniTerminalError,
        'BadRequestError': type('BadRequestError', (Exception,), {}),
        'RunwayCreditPreflightInsufficientError': type(
            'RunwayCreditPreflightInsufficientError',
            (Exception,),
            {},
        ),
        'GeminiVideoTerminalError': type(
            'GeminiVideoTerminalError', (Exception,), {}
        ),
        'GeminiVideoQuotaError': type(
            'GeminiVideoQuotaError', (Exception,), {}
        ),
        'GeminiImageAttemptedError': type(
            'GeminiImageAttemptedError', (Exception,), {}
        ),
        'RunwayCreateRejectedError': type(
            'RunwayCreateRejectedError', (Exception,), {}
        ),
        'FalVideoError': type('FalVideoError', (Exception,), {}),
        'settings': _Settings(),
        '_GEMINI_OMNI_ASPECT_RATIO': '9:16',
        '_aspect_ratio_profile': lambda ratio: {
            'runway_ratio': '720:1280' if ratio == '9:16' else '1280:720',
        },
        '_generate_gemini_omni_video': omni,
        '_runway_gen45_credits_known_insufficient': lambda *_args: False,
        '_create_text_to_video_task': lambda *_args: created,
        'RunwayML': runway_factory,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['generate_scene']


class GeminiOmniProviderOrderTests(unittest.TestCase):
    def test_preference_calls_omni_first_with_private_reference(self):
        omni = Mock(return_value={'provider': 'gemini_omni'})
        runway = _RunwayFactory()
        generate = _load_generate_scene(omni, runway)
        reference = b'private-reference'
        result = generate(
            'safe action',
            duration=5,
            prefer_gemini_omni=True,
            continuity_reference_image=reference,
            aspect_ratio='9:16',
        )
        self.assertEqual(result, {'provider': 'gemini_omni'})
        omni.assert_called_once_with(
            'safe action',
            5,
            continuity_reference_image=reference,
        )
        self.assertEqual(runway.calls, [])

    def test_ambiguous_omni_failure_never_starts_runway(self):
        omni = Mock(side_effect=GeminiOmniTerminalError('unknown acceptance'))
        runway = _RunwayFactory()
        generate = _load_generate_scene(omni, runway)
        with self.assertRaises(GeminiOmniTerminalError):
            generate(
                'safe action',
                prefer_gemini_omni=True,
                aspect_ratio='9:16',
            )
        self.assertEqual(runway.calls, [])

    def test_omni_preference_is_rejected_without_explicit_portrait_contract(self):
        omni = Mock()
        runway = _RunwayFactory()
        generate = _load_generate_scene(omni, runway)
        with self.assertRaisesRegex(ValueError, 'requires 9:16'):
            generate('safe action', prefer_gemini_omni=True)
        omni.assert_not_called()
        self.assertEqual(runway.calls, [])

    def test_explicit_preacceptance_rejection_may_use_existing_provider(self):
        omni = Mock(side_effect=GeminiOmniPreAcceptanceFallbackError('quota'))
        runway = _RunwayFactory()
        generate = _load_generate_scene(omni, runway)
        result = generate(
            'safe action',
            prefer_gemini_omni=True,
            aspect_ratio='9:16',
        )
        self.assertEqual(result['provider'], 'runway')
        self.assertEqual(len(runway.calls), 2)


class GeminiOmniTaskOrchestrationTests(unittest.TestCase):
    def _continuity_applies(self):
        tree = ast.parse(TASKS_PATH.read_text(encoding='utf-8'))
        names = {
            '_OMNI_CONTINUITY_MARKER_PATTERN',
            '_OMNI_CONTINUITY_STOP_WORDS',
        }
        functions = {
            '_omni_identity_tokens',
            '_omni_continuity_reference_applies',
        }
        definitions = [
            node for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in names
                    for target in node.targets
                )
            )
            or (
                isinstance(node, ast.FunctionDef)
                and node.name in functions
            )
        ]
        namespace = {'re': re}
        exec(
            compile(
                ast.Module(body=definitions, type_ignores=[]),
                str(TASKS_PATH),
                'exec',
            ),
            namespace,
        )
        return namespace['_omni_continuity_reference_applies']

    def _continuity_needed(self):
        tree = ast.parse(TASKS_PATH.read_text(encoding='utf-8'))
        names = {
            '_OMNI_CONTINUITY_MARKER_PATTERN',
            '_OMNI_CONTINUITY_STOP_WORDS',
        }
        functions = {
            '_omni_identity_tokens',
            '_omni_continuity_reference_applies',
            '_omni_continuity_reference_needed',
        }
        definitions = [
            node for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in names
                    for target in node.targets
                )
            )
            or (
                isinstance(node, ast.FunctionDef)
                and node.name in functions
            )
        ]
        namespace = {'re': re}
        exec(
            compile(
                ast.Module(body=definitions, type_ignores=[]),
                str(TASKS_PATH),
                'exec',
            ),
            namespace,
        )
        return namespace['_omni_continuity_reference_needed']

    def test_reference_requires_explicit_marker_and_shared_identity(self):
        applies = self._continuity_applies()
        anchor = {
            'ai_prompt': 'An unbranded black smartphone rests beside a LEGO ship.'
        }
        self.assertTrue(applies(
            anchor,
            {'ai_prompt': 'The same LEGO ship reaches the beach at sunrise.'},
        ))
        self.assertTrue(applies(
            anchor,
            {'ai_prompt': 'The same phone now shows a cooling animation.'},
        ))
        self.assertFalse(applies(
            anchor,
            {'ai_prompt': 'A LEGO ship reaches an unrelated beach.'},
        ))
        self.assertFalse(applies(
            anchor,
            {'ai_prompt': 'The same turtle crosses a bright beach.'},
        ))
        self.assertFalse(applies(
            {'ai_prompt': 'Documentary room lighting around a metal tool.'},
            {'ai_prompt': 'The same documentary room lighting surrounds a bird.'},
        ))

    def test_billed_anchor_gate_requires_a_later_eligible_continuation(self):
        needed = self._continuity_needed()
        scenes = [
            {'ai_prompt': 'Wide aerial view of Cornwall at sunrise.'},
            {'ai_prompt': 'A black LEGO dragon rests on wet sand.'},
            {'ai_prompt': 'The same black LEGO dragon rises from wet sand.'},
        ]

        self.assertFalse(needed(0, scenes, {0, 1, 2}))
        self.assertTrue(needed(1, scenes, {1, 2}))
        self.assertFalse(needed(1, scenes, {1}))
        self.assertFalse(needed(1, scenes, {0, 1}))
        self.assertFalse(needed(False, scenes, {1, 2}))

    def test_all_anchor_creation_paths_are_guarded_by_later_paid_use(self):
        source = TASKS_PATH.read_text(encoding='utf-8')
        self.assertEqual(
            source.count('_omni_continuity_reference_needed('),
            4,
        )
        recovered_start = source.index('if recovered_scene_entries:')
        recovered_end = source.index('\n                continue', recovered_start)
        self.assertIn(
            '_omni_continuity_reference_needed(',
            source[recovered_start:recovered_end],
        )
        fresh_start = source.index('generated_scene = generate_scene(')
        fresh_end = source.index(
            "runway_spec = _generated_visual_spec(",
            fresh_start,
        )
        self.assertIn(
            '_omni_continuity_reference_needed(',
            source[fresh_start:fresh_end],
        )
        repair_start = source.index('repair_scene = generate_scene(')
        repair_end = source.index(
            "repair_spec = _generated_visual_spec(",
            repair_start,
        )
        self.assertIn(
            '_omni_continuity_reference_needed(',
            source[repair_start:repair_end],
        )

    def test_private_ai_first_selected_scenes_are_sorted_before_generation(self):
        source = TASKS_PATH.read_text(encoding='utf-8')
        sort_position = source.index(
            'if is_private_ai_first_omni_preview:\n'
            '            # Omni continuity is causal:'
        )
        loop_position = source.index('for candidate_number, candidate in enumerate(selected_runway, start=1):')
        self.assertLess(sort_position, loop_position)
        self.assertIn(
            "key=lambda item: int(item['scene_index'])",
            source[sort_position:loop_position],
        )

    def test_continuity_frame_crop_preserves_source_aspect_ratio(self):
        source = SOURCE_PATH.read_text(encoding='utf-8')
        self.assertIn(
            'scale=720:1280:force_original_aspect_ratio=increase:',
            source,
        )
        self.assertIn('flags=lanczos,crop=720:1280,setsar=1', source)
        self.assertIn("'-frames:v', '1'", source)
        self.assertIn("'-f', 'image2'", source)
        self.assertNotIn(
            "'-vf', 'scale=720:1280:flags=lanczos,setsar=1,fps=24'",
            source,
        )

    def test_both_paid_generation_sites_receive_omni_preference_and_anchor(self):
        tree = ast.parse(TASKS_PATH.read_text(encoding='utf-8'))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'generate_scene'
        ]
        self.assertEqual(len(calls), 2)
        for call_node in calls:
            keywords = {keyword.arg: keyword.value for keyword in call_node.keywords}
            keyword_names = set(keywords)
            self.assertIn('prefer_gemini_omni', keyword_names)
            self.assertIn('continuity_reference_image', keyword_names)
            self.assertIn('aspect_ratio', keyword_names)
            self.assertIsInstance(
                keywords['continuity_reference_image'],
                ast.Name,
            )
            self.assertEqual(
                keywords['continuity_reference_image'].id,
                'scene_continuity_reference',
            )

    def test_continuity_reference_never_enters_records_or_checkpoints(self):
        source = TASKS_PATH.read_text(encoding='utf-8')
        sensitive_lines = [
            line for line in source.splitlines()
            if 'omni_continuity_reference_image_path' in line
        ]
        self.assertGreaterEqual(len(sensitive_lines), 5)
        for line in sensitive_lines:
            self.assertNotIn('json', line)
            self.assertNotIn('checkpoint', line)
            self.assertNotIn('provider_records', line)
            self.assertNotIn("'", line.strip().split('=', 1)[0])
        self.assertNotIn('interaction_id', source)

    def test_anchor_extraction_failure_stops_paid_ai_first_generation(self):
        source = TASKS_PATH.read_text(encoding='utf-8')
        self.assertEqual(
            source.count(
                'if isinstance(exc, GeminiOmniContinuityReferenceError):'
            ),
            2,
        )
        self.assertIn(
            'GeminiOmniContinuityReferenceError,\n'
            '    GeminiOmniTerminalError,',
            source,
        )
        pipeline = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'run_video_pipeline')
        exclusions = next(keyword.value for decorator in pipeline.decorator_list
                          if isinstance(decorator, ast.Call) for keyword in decorator.keywords
                          if keyword.arg == 'dont_autoretry_for')
        self.assertIsInstance(exclusions, ast.Tuple)
        self.assertTrue({'FinalVisualQualityError', 'FinalAudioQualityError',
                         'GeminiOmniContinuityReferenceError'} <= {
                             node.id for node in exclusions.elts if isinstance(node, ast.Name)})
        terminal_start = source.index('terminal_pre_media_error = isinstance(')
        terminal_end = source.index('\n        if (', terminal_start)
        self.assertIn(
            'GeminiOmniContinuityReferenceError,',
            source[terminal_start:terminal_end],
        )

    def test_accepted_or_ambiguous_omni_scene_cannot_be_resubmitted_as_repair(self):
        source = TASKS_PATH.read_text(encoding='utf-8')
        guards = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Call) and isinstance(node.test.func, ast.Name)
                  and node.test.func.id == 'isinstance' and len(node.test.args) == 2
                  and isinstance(node.test.args[0], ast.Name) and node.test.args[0].id == 'exc'
                  and any(isinstance(part, ast.Name) and part.id == 'GeminiOmniTerminalError'
                          for part in ast.walk(node.test.args[1]))]
        self.assertEqual(len(guards), 2)
        for guard in guards:
            self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                and isinstance(node.func.value, ast.Name)
                                and node.func.value.id == 'omni_unsafe_submission_scenes'
                                and node.func.attr == 'add' for node in ast.walk(guard)))
        self.assertIn(
            'if scene_idx not in omni_unsafe_submission_scenes',
            source,
        )
        accepted = source.index(
            'if is_private_ai_first_omni_preview:\n'
            '                    # A provider request is accepted before local copy'
        )
        completed = source.index(
            'omni_unsafe_submission_scenes.discard(scene_idx)',
            accepted,
        )
        self.assertLess(accepted, completed)


if __name__ == '__main__':
    unittest.main()
