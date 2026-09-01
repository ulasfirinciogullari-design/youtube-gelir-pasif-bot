import base64
import unittest
from unittest.mock import patch

from app.services.gemini_generation import (
    GeminiGenerationError,
    GeminiProtocolError,
    generate_gemini_audio_json,
    generate_gemini_json,
    generate_gemini_multimodal_json,
)


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200):
        self.status_code = status_code
        self.payload = payload
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        return self.payload


def gemini_response(text='{"answer":"ok"}', *, parts=None):
    return {
        'candidates': [{
            'finishReason': 'STOP',
            'content': {
                'role': 'model',
                'parts': parts if parts is not None else [{'text': text}],
            },
        }],
    }


class GeminiJsonGenerationTests(unittest.TestCase):
    @patch('app.services.gemini_generation.httpx.post')
    def test_audio_uses_bounded_inline_data_and_header_only_key(self, post):
        post.return_value = FakeResponse(gemini_response())

        result = generate_gemini_audio_json(
            b'voice-bytes',
            'audio/mpeg',
            'Review this narration.',
            api_key='header-only-secret',
            retry_once=False,
            system_instruction='Trusted prosody rubric.',
        )

        self.assertEqual(result, {'answer': 'ok'})
        request = post.call_args
        self.assertNotIn('header-only-secret', request.args[0])
        self.assertEqual(
            request.kwargs['headers']['x-goog-api-key'],
            'header-only-secret',
        )
        parts = request.kwargs['json']['contents'][0]['parts']
        self.assertEqual(parts[0], {'text': 'Review this narration.'})
        self.assertEqual(parts[1]['inlineData']['mimeType'], 'audio/mpeg')
        self.assertEqual(
            base64.b64decode(parts[1]['inlineData']['data']),
            b'voice-bytes',
        )

    @patch('app.services.gemini_generation.httpx.post')
    def test_audio_rejects_invalid_format_and_size_before_network(self, post):
        for audio_bytes, mime_type in (
            (b'', 'audio/mpeg'),
            (bytearray(b'x'), 'audio/mpeg'),
            (b'x', 'video/mp4'),
        ):
            with self.subTest(mime_type=mime_type):
                with self.assertRaises(GeminiGenerationError):
                    generate_gemini_audio_json(
                        audio_bytes,
                        mime_type,
                        'Review.',
                        api_key='test-key',
                    )
        with patch('app.services.gemini_generation._MAX_AUDIO_BYTES', 1):
            with self.assertRaises(GeminiGenerationError):
                generate_gemini_audio_json(
                    b'xx',
                    'audio/mpeg',
                    'Review.',
                    api_key='test-key',
                )
        post.assert_not_called()

    @patch('app.services.gemini_generation.httpx.post')
    def test_multimodal_uses_native_ordered_inline_data(self, post):
        jpeg = b'\xff\xd8\xffjpeg-data\xff\xd9'
        png = b'\x89PNG\r\n\x1a\npng-data'
        webp = b'RIFF\x08\x00\x00\x00WEBPwebp-data'
        post.return_value = FakeResponse(gemini_response())

        result = generate_gemini_multimodal_json(
            [
                {'text': 'first label'},
                {'image_bytes': jpeg},
                {'text': 'second label'},
                {'image_bytes': png},
                {'image_bytes': webp},
            ],
            api_key='header-only-secret',
            retry_once=False,
            system_instruction='Trusted editorial rubric.',
        )

        self.assertEqual(result, {'answer': 'ok'})
        request = post.call_args
        self.assertNotIn('header-only-secret', request.args[0])
        self.assertEqual(
            request.kwargs['headers']['x-goog-api-key'],
            'header-only-secret',
        )
        parts = request.kwargs['json']['contents'][0]['parts']
        self.assertEqual(parts[0], {'text': 'first label'})
        self.assertEqual(
            parts[1],
            {
                'inlineData': {
                    'mimeType': 'image/jpeg',
                    'data': '/9j/anBlZy1kYXRh/9k=',
                },
            },
        )
        self.assertEqual(parts[2], {'text': 'second label'})
        self.assertEqual(parts[3]['inlineData']['mimeType'], 'image/png')
        self.assertEqual(parts[4]['inlineData']['mimeType'], 'image/webp')
        self.assertEqual(
            request.kwargs['json']['systemInstruction'],
            {'parts': [{'text': 'Trusted editorial rubric.'}]},
        )
        self.assertIs(request.kwargs['json']['store'], False)
        self.assertNotIn('tools', request.kwargs['json'])

    @patch('app.services.gemini_generation.httpx.post')
    def test_multimodal_rejects_non_bytes_magic_and_safety_limits(self, post):
        jpeg = b'\xff\xd8\xffx'
        invalid_parts = [
            [{'text': 'prompt'}],
            [{'text': 'prompt'}, {'path': 'local.jpg'}],
            [{'text': 'prompt'}, {'image_bytes': bytearray(jpeg)}],
            [{'text': 'prompt'}, {'image_bytes': b'not-an-image'}],
        ]
        for parts in invalid_parts:
            with self.subTest(parts=parts):
                with self.assertRaises(GeminiGenerationError):
                    generate_gemini_multimodal_json(
                        parts,
                        api_key='test-key',
                    )
        for system_instruction in ('', 42):
            with self.subTest(system_instruction=system_instruction):
                with self.assertRaises(GeminiGenerationError):
                    generate_gemini_multimodal_json(
                        [
                            {'text': 'prompt'},
                            {'image_bytes': jpeg},
                        ],
                        api_key='test-key',
                        system_instruction=system_instruction,
                    )

        with patch(
            'app.services.gemini_generation._MAX_IMAGE_BYTES', 3
        ):
            with self.assertRaises(GeminiGenerationError):
                generate_gemini_multimodal_json(
                    [{'text': 'prompt'}, {'image_bytes': jpeg}],
                    api_key='test-key',
                )
        with patch(
            'app.services.gemini_generation._MAX_MULTIMODAL_IMAGES', 1
        ):
            with self.assertRaises(GeminiGenerationError):
                generate_gemini_multimodal_json(
                    [
                        {'text': 'prompt'},
                        {'image_bytes': jpeg},
                        {'image_bytes': jpeg},
                    ],
                    api_key='test-key',
                )
        with patch(
            'app.services.gemini_generation._MAX_TOTAL_IMAGE_BYTES', 7
        ):
            with self.assertRaises(GeminiGenerationError):
                generate_gemini_multimodal_json(
                    [
                        {'text': 'prompt'},
                        {'image_bytes': jpeg},
                        {'image_bytes': jpeg},
                    ],
                    api_key='test-key',
                )
        self.assertEqual(post.call_count, 0)

    @patch('app.services.gemini_generation.httpx.post')
    def test_nested_string_and_array_limits_are_enforced(self, post):
        schema = {
            'type': 'object',
            'properties': {
                'tags': {
                    'type': 'array',
                    'maxItems': 1,
                    'items': {'type': 'string', 'minLength': 2},
                },
            },
            'required': ['tags'],
            'additionalProperties': False,
        }
        for output in ('{"tags":["ok","no"]}', '{"tags":[""]}'):
            with self.subTest(output=output):
                post.reset_mock()
                post.return_value = FakeResponse(gemini_response(output))
                with self.assertRaises(GeminiGenerationError):
                    generate_gemini_json(
                        'prompt',
                        api_key='test-key',
                        json_schema=schema,
                    )

    @patch('app.services.gemini_generation.httpx.post')
    def test_missing_selected_key_fails_before_network(self, post):
        with self.assertRaisesRegex(
            GeminiGenerationError,
            'GEMINI_API_KEY is required',
        ):
            generate_gemini_json('prompt', api_key='')

        post.assert_not_called()

    @patch('app.services.gemini_generation.httpx.post')
    def test_success_returns_one_parsed_object_and_ignores_thoughts(self, post):
        post.return_value = FakeResponse(gemini_response(parts=[
            {'thought': True, 'text': 'private reasoning summary'},
            {'text': '{"answer":"tamam"}'},
        ]))

        result = generate_gemini_json(
            'Bir JSON nesnesi üret.',
            api_key='test-key',
            model='gemini-3.1-pro-preview',
        )

        self.assertEqual(result, {'answer': 'tamam'})
        self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_search_and_schema_are_sent_with_native_safe_request(self, post):
        schema = {
            'type': 'object',
            'properties': {
                'title': {'type': 'string'},
                'score': {'type': 'integer', 'minimum': 0, 'maximum': 100},
            },
            'required': ['title', 'score'],
            'additionalProperties': False,
        }
        post.return_value = FakeResponse(
            gemini_response('{"title":"Sonuç","score":93}')
        )

        result = generate_gemini_json(
            'Güncel bilgiyi ara ve değerlendir.',
            api_key='header-only-secret',
            model='gemini-3.1-pro-preview',
            json_schema=schema,
            google_search=True,
            thinking_level='high',
            timeout=37,
            retry_once=False,
        )

        self.assertEqual(result['score'], 93)
        request = post.call_args
        self.assertEqual(
            request.args[0],
            'https://generativelanguage.googleapis.com/v1beta/models/'
            'gemini-3.1-pro-preview:generateContent',
        )
        self.assertNotIn('header-only-secret', request.args[0])
        self.assertEqual(
            request.kwargs['headers']['x-goog-api-key'],
            'header-only-secret',
        )
        self.assertEqual(request.kwargs['timeout'], 37)
        body = request.kwargs['json']
        self.assertIs(body['store'], False)
        self.assertEqual(body['tools'], [{'google_search': {}}])
        self.assertEqual(body['generationConfig']['candidateCount'], 1)
        self.assertEqual(
            body['generationConfig']['thinkingConfig'],
            {'thinkingLevel': 'high'},
        )
        self.assertEqual(
            body['generationConfig']['responseMimeType'],
            'application/json',
        )
        self.assertEqual(
            body['generationConfig']['responseJsonSchema'],
            schema,
        )
        self.assertIsNot(body['generationConfig']['responseJsonSchema'], schema)

    @patch('app.services.gemini_generation.httpx.post')
    def test_malformed_or_unsafe_candidate_fails_closed(self, post):
        malformed_payloads = [
            {'candidates': []},
            {
                'candidates': [
                    gemini_response()['candidates'][0],
                    gemini_response()['candidates'][0],
                ],
            },
            {
                'candidates': [{
                    'finishReason': 'SAFETY',
                    'content': {'parts': [{'text': '{"answer":"ok"}'}]},
                }],
            },
            gemini_response(parts=[
                {'text': '{"answer":"first"}'},
                {'text': '{"answer":"second"}'},
            ]),
            gemini_response('{"answer":"first","answer":"second"}'),
            gemini_response('[]'),
            {
                'candidates': [{
                    'finishReason': 'STOP',
                    'safetyRatings': 'malformed',
                    'content': {'parts': [{'text': '{"answer":"ok"}'}]},
                }],
            },
        ]

        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                post.reset_mock()
                post.return_value = FakeResponse(payload)
                expected_error = (
                    GeminiGenerationError
                    if (
                        isinstance(payload, dict)
                        and payload.get('candidates')
                        and (
                            payload['candidates'][0].get('finishReason')
                            in {'SAFETY'}
                            or not isinstance(
                                payload['candidates'][0].get(
                                    'safetyRatings'
                                ),
                                (list, type(None)),
                            )
                        )
                    )
                    else GeminiProtocolError
                )
                with self.assertRaises(expected_error):
                    generate_gemini_json(
                        'prompt',
                        api_key='test-key',
                        retry_once=False,
                    )
                self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_prompt_block_without_candidates_is_not_protocol_retryable(
        self, post
    ):
        post.return_value = FakeResponse({
            'promptFeedback': {
                'blockReason': 'SAFETY',
                'safetyRatings': [{'blocked': True}],
            },
            'candidates': [],
        })

        with self.assertRaises(GeminiGenerationError) as raised:
            generate_gemini_json(
                'prompt',
                api_key='test-key',
                retry_once=False,
            )

        self.assertNotIsInstance(raised.exception, GeminiProtocolError)
        self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_malformed_safety_metadata_fails_closed_without_protocol_retry(
        self, post
    ):
        payloads = [
            {
                'promptFeedback': 'malformed',
                **gemini_response(),
            },
            {
                'promptFeedback': {'safetyRatings': ['malformed']},
                **gemini_response(),
            },
            {
                'candidates': [{
                    **gemini_response()['candidates'][0],
                    'safetyRatings': ['malformed'],
                }],
            },
            {
                'candidates': [{
                    **gemini_response()['candidates'][0],
                    'safetyRatings': [{'blocked': 'yes'}],
                }],
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                post.reset_mock()
                post.return_value = FakeResponse(payload)
                with self.assertRaises(GeminiGenerationError) as raised:
                    generate_gemini_json(
                        'prompt',
                        api_key='test-key',
                        retry_once=False,
                    )
                self.assertNotIsInstance(
                    raised.exception,
                    GeminiProtocolError,
                )
                self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_missing_or_non_string_finish_reason_is_protocol_error(self, post):
        for finish_reason in (None, 7, ''):
            with self.subTest(finish_reason=finish_reason):
                candidate = dict(gemini_response()['candidates'][0])
                if finish_reason is None:
                    candidate.pop('finishReason')
                else:
                    candidate['finishReason'] = finish_reason
                post.reset_mock()
                post.return_value = FakeResponse({
                    'candidates': [candidate],
                })
                with self.assertRaises(GeminiProtocolError):
                    generate_gemini_json(
                        'prompt',
                        api_key='test-key',
                        retry_once=False,
                    )
                self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_locally_rejects_schema_violation(self, post):
        post.return_value = FakeResponse(
            gemini_response('{"score":101,"unexpected":true}')
        )
        schema = {
            'type': 'object',
            'properties': {
                'score': {'type': 'integer', 'maximum': 100},
            },
            'required': ['score'],
            'additionalProperties': False,
        }

        with self.assertRaises(GeminiProtocolError):
            generate_gemini_json(
                'prompt',
                api_key='test-key',
                json_schema=schema,
            )

    @patch('app.services.gemini_generation.httpx.post')
    def test_retries_network_429_and_5xx_only_once(self, post):
        success = FakeResponse(gemini_response())
        retryable_cases = [
            OSError('temporary connection failure'),
            FakeResponse(status_code=429),
            FakeResponse(status_code=503),
        ]
        for first_result in retryable_cases:
            with self.subTest(first_result=first_result):
                post.reset_mock()
                post.side_effect = [first_result, success] if isinstance(
                    first_result, BaseException
                ) else None
                if not isinstance(first_result, BaseException):
                    post.side_effect = None
                    post.side_effect = [first_result, success]
                result = generate_gemini_json('prompt', api_key='test-key')
                self.assertEqual(result, {'answer': 'ok'})
                self.assertEqual(post.call_count, 2)

        post.reset_mock()
        rejected = FakeResponse(
            {'error': {'message': 'must never be exposed'}},
            status_code=400,
        )
        post.side_effect = None
        post.return_value = rejected
        with self.assertRaises(GeminiGenerationError):
            generate_gemini_json('prompt', api_key='test-key')
        self.assertEqual(post.call_count, 1)
        self.assertEqual(rejected.json_calls, 0)

    @patch('app.services.gemini_generation.httpx.post')
    def test_retries_malformed_json_then_returns_valid_response(self, post):
        post.side_effect = [
            FakeResponse(gemini_response('not-json')),
            FakeResponse(gemini_response('{"answer":"recovered"}')),
        ]

        result = generate_gemini_json('prompt', api_key='test-key')

        self.assertEqual(result, {'answer': 'recovered'})
        self.assertEqual(post.call_count, 2)

    @patch('app.services.gemini_generation.httpx.post')
    def test_protocol_retry_is_bounded_to_two_malformed_responses(self, post):
        post.side_effect = [
            FakeResponse(gemini_response('not-json-one')),
            FakeResponse(gemini_response('not-json-two')),
        ]

        with self.assertRaisesRegex(
            GeminiProtocolError,
            '^Gemini returned invalid JSON$',
        ):
            generate_gemini_json('prompt', api_key='test-key')

        self.assertEqual(post.call_count, 2)

    @patch('app.services.gemini_generation.httpx.post')
    def test_retry_once_false_does_not_retry_malformed_json(self, post):
        post.return_value = FakeResponse(gemini_response('not-json'))

        with self.assertRaises(GeminiProtocolError):
            generate_gemini_json(
                'prompt',
                api_key='test-key',
                retry_once=False,
            )

        self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_safety_and_non_stop_responses_remain_fail_fast(self, post):
        payloads = [
            {
                'candidates': [{
                    'finishReason': 'SAFETY',
                    'safetyRatings': [{'blocked': True}],
                    'content': {'parts': [{'text': '{"answer":"unsafe"}'}]},
                }],
            },
            {
                'candidates': [{
                    'finishReason': 'MAX_TOKENS',
                    'content': {'parts': [{'text': '{"answer":"partial"}'}]},
                }],
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                post.reset_mock()
                post.return_value = FakeResponse(payload)
                with self.assertRaises(GeminiGenerationError) as raised:
                    generate_gemini_json('prompt', api_key='test-key')
                self.assertNotIsInstance(
                    raised.exception,
                    GeminiProtocolError,
                )
                self.assertEqual(post.call_count, 1)

    @patch('app.services.gemini_generation.httpx.post')
    def test_protocol_retry_never_exposes_key_or_malformed_output(self, post):
        secret = 'gemini-protocol-secret-must-not-leak'
        malformed_output = f'not-json-containing-{secret}'
        post.side_effect = [
            FakeResponse(gemini_response(malformed_output)),
            FakeResponse(gemini_response(malformed_output)),
        ]

        with self.assertRaises(GeminiProtocolError) as raised:
            generate_gemini_json('prompt', api_key=secret)

        rendered_error = str(raised.exception)
        self.assertNotIn(secret, rendered_error)
        self.assertNotIn(malformed_output, rendered_error)
        self.assertEqual(post.call_count, 2)

    @patch('app.services.gemini_generation.httpx.post')
    def test_errors_never_expose_key_or_upstream_body(self, post):
        secret = 'gemini-secret-must-not-leak'
        upstream_body = f'upstream echoed {secret}'
        response = FakeResponse(
            {'error': {'message': upstream_body}},
            status_code=403,
        )
        post.return_value = response

        with self.assertRaises(GeminiGenerationError) as raised:
            generate_gemini_json('prompt', api_key=secret)

        rendered_error = str(raised.exception)
        self.assertNotIn(secret, rendered_error)
        self.assertNotIn(upstream_body, rendered_error)
        self.assertEqual(response.json_calls, 0)


if __name__ == '__main__':
    unittest.main()
