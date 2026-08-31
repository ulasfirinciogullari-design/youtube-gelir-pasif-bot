import base64
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(
    openai_api_key='',
    gemini_api_key='',
    elevenlabs_api_key='',
)
_previous_config_module = sys.modules.get('app.config')
sys.modules['app.config'] = config_stub

# The lightweight CI unit-test job intentionally does not install runtime
# dependencies. Keep this module collectable there while retaining the real
# httpx module whenever it is available.
try:
    import httpx as _httpx_for_tests
except ModuleNotFoundError:
    _httpx_for_tests = types.ModuleType('httpx')
    _httpx_for_tests.Timeout = lambda *args, **kwargs: SimpleNamespace(
        args=args,
        kwargs=kwargs,
    )
    _httpx_for_tests.post = lambda *args, **kwargs: None

_previous_httpx_module = sys.modules.get('httpx')
sys.modules['httpx'] = _httpx_for_tests

import app.services.audio_qc as audio_qc

if _previous_config_module is None:
    sys.modules.pop('app.config', None)
else:
    sys.modules['app.config'] = _previous_config_module
if _previous_httpx_module is None:
    sys.modules.pop('httpx', None)
else:
    sys.modules['httpx'] = _previous_httpx_module
# Keep this test module self-contained when files are collected in any order.
sys.modules.pop('app.services.audio_qc', None)
services_package = sys.modules.get('app.services')
if services_package is not None and getattr(
    services_package,
    'audio_qc',
    None,
) is audio_qc:
    delattr(services_package, 'audio_qc')


def _words(*values: str) -> list[dict]:
    return [
        {
            'text': value,
            'start': index * 0.4,
            'end': (index + 1) * 0.4,
            'type': 'word',
        }
        for index, value in enumerate(values)
    ]


def _openai_words(*values: str) -> list[dict]:
    return [
        {
            'word': value,
            'start': index * 0.4,
            'end': (index + 1) * 0.4,
        }
        for index, value in enumerate(values)
    ]


def _gemini_interaction(transcript: str, *values: str) -> dict:
    annotations = [
        {
            'type': 'word_info',
            'text': value,
            'start_offset': f'{index * 0.4:.3f}s',
            'end_offset': f'{(index + 1) * 0.4:.3f}s',
        }
        for index, value in enumerate(values)
    ]
    return {
        'status': 'completed',
        'steps': [{
            'type': 'model_output',
            'content': [{
                'type': 'text',
                'text': transcript,
                'annotations': annotations,
            }],
        }],
    }


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class AudioQCTests(unittest.TestCase):
    def test_exact_transcript_returns_complete_evidence(self):
        result = audio_qc.compare_transcript(
            'Bug\u00fcn hava g\u00fczel',
            'Bug\u00fcn hava g\u00fczel',
            language_code='tur',
            language_probability=0.997,
            words=_words('Bug\u00fcn', 'hava', 'g\u00fczel'),
        )

        self.assertTrue(result['available'])
        self.assertTrue(result['pass'])
        self.assertEqual(result['score'], 100.0)
        self.assertEqual(result['transcript'], 'Bug\u00fcn hava g\u00fczel')
        self.assertEqual(result['language_probability'], 0.997)
        self.assertEqual(len(result['word_timestamps']), 3)
        self.assertAlmostEqual(result['ending_word_time'], 1.2)
        self.assertEqual(result['mismatch_details']['operations'], [])
        self.assertTrue(
            result['mismatch_details']['timestamp_sequence_match']
        )

    def test_turkish_case_unicode_and_punctuation_are_tolerated(self):
        result = audio_qc.compare_transcript(
            "\u0130STANBUL'da, I\u011eDIR'\u0131n havas\u0131 g\u00fczel!",
            'istanbulda \u0131\u011fd\u0131r\u0131n havas\u0131 g\u00fczel',
        )

        self.assertTrue(result['pass'])
        self.assertEqual(result['score'], 100.0)
        self.assertEqual(
            result['normalized_expected'],
            'istanbulda \u0131\u011fd\u0131r\u0131n havas\u0131 g\u00fczel',
        )

    def test_spoken_turkish_numbers_match_digit_transcriptions(self):
        cases = (
            ('Yirmi dokuz y\u0131l', '29 y\u0131l'),
            (
                'Bin dokuz y\u00fcz doksan yedide sefere \u00e7\u0131kt\u0131',
                "1997'de sefere \u00e7\u0131kt\u0131",
            ),
            ('Altm\u0131\u015f iki konteyner', '62 konteyner'),
            (
                'D\u00f6rt virg\u00fcl sekiz milyon ton',
                '4,8 milyon ton',
            ),
        )

        for expected, heard in cases:
            with self.subTest(expected=expected, heard=heard):
                result = audio_qc.compare_transcript(expected, heard)

                self.assertTrue(result['pass'])
                self.assertEqual(result['score'], 100.0)
                self.assertTrue(result['mismatch_details']['exact_match'])
                self.assertEqual(
                    result['mismatch_details']['operations'],
                    [],
                )

    def test_circumflex_and_explicit_tokio_alias_are_tolerated(self):
        result = audio_qc.compare_transcript(
            'Tokio h\u00e2l\u00e2 sakin',
            'Tokyo hala sakin',
        )

        self.assertTrue(result['pass'])
        self.assertEqual(result['score'], 100.0)

    def test_lexical_turkish_diacritics_are_not_folded(self):
        result = audio_qc.compare_transcript('O oldu', 'O \u00f6ld\u00fc')

        self.assertFalse(result['pass'])
        self.assertEqual(
            result['mismatch_details']['missing_words'],
            ['oldu'],
        )
        self.assertEqual(
            result['mismatch_details']['unexpected_words'],
            ['\u00f6ld\u00fc'],
        )

    def test_numeric_normalization_fails_closed(self):
        cases = (
            ('Yirmi dokuz y\u0131l', '30 y\u0131l'),
            (
                'Bin dokuz y\u00fcz doksan yedide ba\u015flad\u0131',
                '1998de basladi',
            ),
            ('D\u00f6rt virg\u00fcl sekiz milyon', '4,9 milyon'),
            ('D\u00f6rt virg\u00fcl sekiz milyon ton', '4,8 ton'),
            ('\u0130ki \u00fc\u00e7 numara', '23 numara'),
            ('Yirmi dokuz y\u0131l', '029 y\u0131l'),
            ('Yirmi dokuz derece', '-29 derece'),
            ('Yirmi dokuz ki\u015fi', '%29 kisi'),
            ('Onda sorun var', "10'da sorun var"),
            ('Tokio liman\u0131', 'Kyoto limani'),
        )

        for expected, heard in cases:
            with self.subTest(expected=expected, heard=heard):
                result = audio_qc.compare_transcript(expected, heard)

                self.assertFalse(result['pass'])
                self.assertLess(result['score'], 100.0)
                self.assertFalse(result['mismatch_details']['exact_match'])

    def test_attached_and_spaced_numeric_signs_fail_closed(self):
        for heard in (
            '-29 derece',
            '- 29 derece',
            '+29 derece',
            '+ 29 derece',
            '\u221229 derece',
            '\u2212 29 derece',
            '\u00b129 derece',
            '\u00b1 29 derece',
        ):
            with self.subTest(heard=heard):
                result = audio_qc.compare_transcript(
                    'Yirmi dokuz derece',
                    heard,
                )

                self.assertFalse(result['pass'])
                self.assertLess(result['score'], 100.0)

    def test_repeated_numeric_signs_fail_closed(self):
        for heard in (
            '--29 derece',
            '+-29 derece',
            '\u2212-29 derece',
            '-+29 derece',
        ):
            with self.subTest(heard=heard):
                result = audio_qc.compare_transcript('-29 derece', heard)

                self.assertFalse(result['pass'])
                self.assertLess(result['score'], 100.0)

    def test_non_decimal_numeric_operators_are_not_discarded(self):
        for heard in (
            '4/8',
            '4 / 8',
            '4:8',
            '4\u00d78',
            '4\u00f78',
            '4\u20448',
            '4\u22158',
        ):
            with self.subTest(heard=heard):
                result = audio_qc.compare_transcript('D\u00f6rt sekiz', heard)

                self.assertFalse(result['pass'])
                self.assertLess(result['score'], 100.0)

    def test_wrong_number_diagnostics_preserve_spoken_source_words(self):
        result = audio_qc.compare_transcript(
            'Gemi yirmi dokuz y\u0131l sonra d\u00f6nd\u00fc',
            'Gemi 30 y\u0131l sonra d\u00f6nd\u00fc',
        )

        self.assertFalse(result['pass'])
        self.assertEqual(
            result['mismatch_details']['missing_words'],
            ['yirmi', 'dokuz'],
        )
        self.assertEqual(
            result['mismatch_details']['unexpected_words'],
            ['30'],
        )

    def test_replacement_reports_both_sides(self):
        result = audio_qc.compare_transcript(
            'Merhaba g\u00fczel d\u00fcnya',
            'Merhaba b\u00fcy\u00fck d\u00fcnya',
        )

        self.assertFalse(result['pass'])
        self.assertLess(result['score'], 100)
        self.assertEqual(result['mismatch_details']['missing_words'], ['g\u00fczel'])
        self.assertEqual(result['mismatch_details']['unexpected_words'], ['b\u00fcy\u00fck'])
        self.assertEqual(
            result['mismatch_details']['operations'][0]['operation'],
            'replace',
        )

    def test_missing_word_lowers_token_score(self):
        result = audio_qc.compare_transcript(
            'Bug\u00fcn hava \u00e7ok g\u00fczel',
            'Bug\u00fcn hava g\u00fczel',
        )

        self.assertFalse(result['pass'])
        self.assertEqual(result['score'], 75.0)
        self.assertEqual(result['mismatch_details']['missing_words'], ['\u00e7ok'])
        self.assertEqual(result['mismatch_details']['unexpected_words'], [])

    def test_missing_keys_are_explicitly_unavailable(self):
        with (
            patch.object(audio_qc.settings, 'openai_api_key', ''),
            patch.object(audio_qc.settings, 'gemini_api_key', ''),
            patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
        ):
            result = audio_qc.verify_audio_narration(
                'not-read-without-a-key.mp3',
                'Beklenen anlat\u0131m',
            )

        self.assertFalse(result['available'])
        self.assertIsNone(result['pass'])
        self.assertIsNone(result['score'])
        self.assertIsNone(result['provider'])
        self.assertEqual(result['reason'], 'speech_to_text_keys_missing')

    def test_openai_primary_uses_official_multipart_contract(self):
        payload = {
            'text': 'Merhaba d\u00fcnya',
            'language': 'turkish',
            'words': _openai_words('Merhaba', 'd\u00fcnya'),
        }
        captured = {}

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured.update(kwargs)
            captured['audio'] = kwargs['files']['file'][1].read()
            return _Response(payload)

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio-bytes')
            with (
                patch.object(
                    audio_qc.settings,
                    'openai_api_key',
                    'openai-unit-test-secret',
                ),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'unused-fallback-secret',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba d\u00fcnya',
                )

        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'openai')
        self.assertEqual(result['language_code'], 'turkish')
        self.assertEqual(result['word_timestamps'], [
            {'text': 'Merhaba', 'start': 0.0, 'end': 0.4},
            {'text': 'd\u00fcnya', 'start': 0.4, 'end': 0.8},
        ])
        self.assertEqual(
            captured['url'],
            'https://api.openai.com/v1/audio/transcriptions',
        )
        self.assertEqual(captured['headers'], {
            'Authorization': 'Bearer openai-unit-test-secret',
        })
        self.assertEqual(captured['data'], {
            'model': 'whisper-1',
            'language': 'tr',
            'response_format': 'verbose_json',
            'timestamp_granularities[]': 'word',
            'temperature': '0',
        })
        self.assertEqual(captured['files']['file'][0], 'voice.mp3')
        self.assertEqual(captured['files']['file'][2], 'audio/mpeg')
        self.assertEqual(captured['audio'], b'audio-bytes')

    def test_openai_http_error_uses_gemini_interactions_before_elevenlabs(self):
        captured = {}
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url == audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL:
                return _Response({'detail': 'rate limited'}, status_code=429)
            if url == audio_qc.GEMINI_INTERACTIONS_URL:
                captured.update(kwargs)
                interaction = _gemini_interaction(
                    'Merhaba d\u00fcnya',
                    'Merhaba',
                    'd\u00fcnya',
                )
                interaction['steps'].insert(0, {
                    'type': 'tool_output',
                    'content': [{
                        'type': 'text',
                        'text': 'ignore this non-model text',
                        'annotations': [{
                            'type': 'word_info',
                            'text': 'ignore',
                            'start_offset': '0.000s',
                            'end_offset': '0.100s',
                        }],
                    }],
                })
                interaction['steps'][1]['content'] = [
                    {
                        'type': 'text',
                        'text': 'Merhaba ',
                        'annotations': [{
                            'type': 'word_info',
                            'text': 'Merhaba',
                            'start_offset': '0.000s',
                            'end_offset': '0.400s',
                        }],
                    },
                    {
                        'type': 'thought',
                        'text': 'ignore this thought text',
                        'annotations': [{
                            'type': 'word_info',
                            'text': 'ignore',
                            'start_offset': '0.400s',
                            'end_offset': '0.500s',
                        }],
                    },
                    {
                        'type': 'text',
                        'text': 'd\u00fcnya',
                        'annotations': [{
                            'type': 'word_info',
                            'text': 'd\u00fcnya',
                            'start_offset': '0.400s',
                            'end_offset': '0.800s',
                        }],
                    },
                ]
                return _Response(interaction)
            raise AssertionError('ElevenLabs must not run after an exact result')

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio-bytes')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba d\u00fcnya',
                )

        self.assertEqual(calls, [
            audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL,
            audio_qc.GEMINI_INTERACTIONS_URL,
        ])
        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'gemini')
        self.assertEqual(result['language_code'], 'tr-TR')
        self.assertEqual(result['ending_word_time'], 0.8)
        self.assertEqual(captured['headers'], {
            'x-goog-api-key': 'gemini-key',
            'Content-Type': 'application/json',
        })
        body = captured['json']
        self.assertEqual(body['model'], 'gemini-3.5-transcribe')
        self.assertIs(body['store'], False)
        self.assertEqual(body['input'][0]['type'], 'audio')
        self.assertEqual(body['input'][0]['mime_type'], 'audio/mpeg')
        self.assertEqual(
            base64.b64decode(body['input'][0]['data']),
            b'audio-bytes',
        )
        self.assertEqual(body['generation_config'], {
            'transcription_config': {
                'language_codes': ['tr-TR'],
                'mode': {
                    'type': 'verbatim',
                    'timestamp_granularities': ['word'],
                },
            },
        })
        self.assertNotIn('Merhaba', str(body))

    def test_gemini_missing_word_annotations_falls_back_to_elevenlabs(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url == audio_qc.GEMINI_INTERACTIONS_URL:
                return _Response(_gemini_interaction('Beklenen anlat\u0131m'))
            return _Response({
                'text': 'Beklenen anlat\u0131m',
                'language_code': 'tur',
                'words': _words('Beklenen', 'anlat\u0131m'),
            })

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Beklenen anlat\u0131m',
                )

        self.assertEqual(calls, [
            audio_qc.GEMINI_INTERACTIONS_URL,
            audio_qc.ELEVENLABS_SPEECH_TO_TEXT_URL,
        ])
        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'elevenlabs')

    def test_gemini_uses_only_last_model_output_then_falls_back(self):
        interaction = _gemini_interaction('d\u00fcnya', 'd\u00fcnya')
        interaction['steps'].insert(
            0,
            _gemini_interaction('Merhaba ', 'Merhaba')['steps'][0],
        )
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url == audio_qc.GEMINI_INTERACTIONS_URL:
                return _Response(interaction)
            return _Response({
                'text': 'Merhaba d\u00fcnya',
                'language_code': 'tur',
                'words': _words('Merhaba', 'd\u00fcnya'),
            })

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba d\u00fcnya',
                )

        self.assertEqual(calls, [
            audio_qc.GEMINI_INTERACTIONS_URL,
            audio_qc.ELEVENLABS_SPEECH_TO_TEXT_URL,
        ])
        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'elevenlabs')

    def test_gemini_model_output_error_rejects_exact_content(self):
        interaction = _gemini_interaction(
            'Merhaba d\u00fcnya',
            'Merhaba',
            'd\u00fcnya',
        )
        interaction['steps'][-1]['error'] = {
            'message': 'do not expose this detail',
        }
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Merhaba d\u00fcnya',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text reported an interaction error',
        )
        self.assertNotIn('do not expose', str(caught.exception))

    def test_gemini_punctuated_number_annotation_preserves_sequence_gate(self):
        for number in ('4,8', '4.8', '-29', '+29', '\u221229', '\u00b129'):
            with self.subTest(number=number):
                interaction = _gemini_interaction(number, number)
                with tempfile.TemporaryDirectory() as temporary:
                    audio_path = Path(temporary) / 'voice.mp3'
                    audio_path.write_bytes(b'audio')
                    with (
                        patch.object(audio_qc.settings, 'openai_api_key', ''),
                        patch.object(
                            audio_qc.settings,
                            'gemini_api_key',
                            'gemini-key',
                        ),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(interaction),
                        ),
                    ):
                        result = audio_qc.verify_audio_narration(
                            audio_path,
                            number,
                        )

                self.assertTrue(result['pass'])
                self.assertEqual(result['provider'], 'gemini')
                self.assertEqual(len(result['word_timestamps']), 1)
                self.assertTrue(
                    result['mismatch_details']['timestamp_sequence_match']
                )

    def test_gemini_trailing_sentence_punctuation_is_benign(self):
        transcript = (
            'Sa\u00e7\u0131ld\u0131. Ba\u015flad\u0131. Kaybetti. Vard\u0131. '
            'Ay\u0131r\u0131yor. Topluyor. Ta\u015f\u0131yor.'
        )
        interaction = _gemini_interaction(
            transcript,
            'Sa\u00e7\u0131ld\u0131.',
            'Ba\u015flad\u0131.',
            'Kaybetti.',
            'Vard\u0131.',
            'Ay\u0131r\u0131yor.',
            'Topluyor.',
            'Ta\u015f\u0131yor.',
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    transcript,
                )

        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'gemini')
        self.assertTrue(
            result['mismatch_details']['timestamp_sequence_match']
        )

    def test_gemini_digit_normalization_keeps_timestamp_evidence(self):
        interaction = _gemini_interaction(
            '1997de 62 konteyner, 4,8 milyon ton ve 29 y\u0131l Tokyo.',
            '1997de',
            '62',
            'konteyner',
            '4,8',
            'milyon',
            'ton',
            've',
            '29',
            'y\u0131l',
            'Tokyo',
        )
        expected = (
            'Bin dokuz y\u00fcz doksan yedide altm\u0131\u015f iki konteyner, '
            'd\u00f6rt virg\u00fcl sekiz milyon ton ve yirmi dokuz y\u0131l '
            'Tokio.'
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    expected,
                )

        self.assertEqual(result['provider'], 'gemini')
        self.assertTrue(result['pass'])
        self.assertEqual(result['score'], 100.0)
        self.assertTrue(
            result['mismatch_details']['timestamp_sequence_match']
        )

    def test_gemini_semantic_but_lexically_wrong_annotations_fail_closed(self):
        interaction = _gemini_interaction(
            '29 y\u0131l',
            'yirmi',
            'dokuz',
            'y\u0131l',
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Yirmi dokuz y\u0131l',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned inconsistent word timestamps',
        )

    def test_gemini_decimal_split_across_annotations_fails_closed(self):
        interaction = _gemini_interaction(
            '4,8 milyon',
            '4',
            '8',
            'milyon',
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'D\u00f6rt virg\u00fcl sekiz milyon',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned inconsistent word timestamps',
        )

    def test_gemini_numeric_operators_cannot_hide_split_annotations(self):
        for transcript in ('4/8', '4:8', '4\u00d78'):
            with self.subTest(transcript=transcript):
                interaction = _gemini_interaction(transcript, '4', '8')
                with tempfile.TemporaryDirectory() as temporary:
                    audio_path = Path(temporary) / 'voice.mp3'
                    audio_path.write_bytes(b'audio')
                    with (
                        patch.object(audio_qc.settings, 'openai_api_key', ''),
                        patch.object(
                            audio_qc.settings,
                            'gemini_api_key',
                            'gemini-key',
                        ),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(interaction),
                        ),
                    ):
                        with self.assertRaises(
                            audio_qc.AudioQCError
                        ) as caught:
                            audio_qc.verify_audio_narration(
                                audio_path,
                                transcript,
                            )

                self.assertEqual(
                    str(caught.exception),
                    'Gemini speech-to-text returned inconsistent word '
                    'timestamps',
                )

    def test_gemini_whitespace_separated_multiword_annotation_is_invalid(self):
        interaction = _gemini_interaction('Merhaba d\u00fcnya')
        interaction['steps'][0]['content'][0]['annotations'] = [{
            'type': 'word_info',
            'text': 'Merhaba d\u00fcnya',
            'start_offset': '0.000s',
            'end_offset': '0.800s',
        }]
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Merhaba d\u00fcnya',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned invalid word annotations',
        )

    def test_gemini_empty_or_non_numeric_compound_annotation_is_invalid(self):
        for invalid_word in (
            '',
            ' , ',
            'Merhaba,d\u00fcnya',
            '4+8',
            '4/8',
            '4:8',
            '4%8',
            '4\u20ba8',
            '%29',
            '29%',
            '4+8.',
            '4/8!',
            '4%8?',
            '4\u20ba8;',
            '--29.',
            '++29!',
            '+-29?',
            '-+29,',
            '\u2212-29\u2026',
            'Sa\u00e7\u0131ld\u0131..',
            '4,8,.',
            'kelime,.:;!?\u2026',
        ):
            with self.subTest(invalid_word=invalid_word):
                interaction = _gemini_interaction('Beklenen', invalid_word)
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc._gemini_interaction_payload(
                        _Response(interaction)
                    )
                self.assertEqual(
                    str(caught.exception),
                    'Gemini speech-to-text returned invalid word annotations',
                )

    def test_gemini_inconsistent_word_annotations_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(_gemini_interaction(
                        'Merhaba d\u00fcnya',
                        'Merhaba',
                        'yanl\u0131\u015f',
                    )),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Merhaba d\u00fcnya',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned inconsistent word timestamps',
        )

    def test_gemini_http_error_does_not_expose_secret_response(self):
        secret = 'never-leak-gemini-key-or-response'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', secret),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(
                        {'detail': f'unauthorized: {secret}'},
                        status_code=401,
                    ),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen anlat\u0131m',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text failed with HTTP 401',
        )
        self.assertNotIn(secret, str(caught.exception))

    def test_gemini_transport_error_does_not_expose_exception_details(self):
        secret = 'never-leak-gemini-transport-detail'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    side_effect=RuntimeError(secret),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen anlat\u0131m',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text transport failed',
        )
        self.assertNotIn(secret, str(caught.exception))

    def test_gemini_unsupported_file_fails_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.txt'
            audio_path.write_bytes(b'not-audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(audio_qc.httpx, 'post') as post,
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen anlat\u0131m',
                    )

        post.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text audio format is unsupported',
        )

    def test_gemini_empty_file_fails_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(audio_qc.httpx, 'post') as post,
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen anlat\u0131m',
                    )

        post.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text audio input is empty',
        )

    def test_gemini_limit_plus_one_file_fails_before_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'12345')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(audio_qc, '_GEMINI_MAX_RAW_AUDIO_BYTES', 4),
                patch.object(audio_qc.httpx, 'post') as post,
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen anlat\u0131m',
                    )

        post.assert_not_called()
        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text inline audio exceeds the safe size limit',
        )

    def test_gemini_non_completed_status_fails_closed(self):
        interaction = _gemini_interaction('Beklenen', 'Beklenen')
        interaction['status'] = 'in_progress'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(audio_path, 'Beklenen')

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text did not finish safely',
        )

    def test_gemini_malformed_duration_fails_closed(self):
        interaction = _gemini_interaction('Beklenen', 'Beklenen')
        annotation = interaction['steps'][0]['content'][0]['annotations'][0]
        annotation['end_offset'] = 'not-a-duration'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(audio_path, 'Beklenen')

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned invalid word annotations',
        )

    def test_gemini_nonmonotonic_word_annotations_fail_closed(self):
        interaction = _gemini_interaction(
            'Merhaba d\u00fcnya',
            'Merhaba',
            'd\u00fcnya',
        )
        second = interaction['steps'][0]['content'][0]['annotations'][1]
        second['start_offset'] = '0.100s'
        second['end_offset'] = '0.300s'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Merhaba d\u00fcnya',
                    )

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned incomplete word timestamps',
        )

    def test_gemini_final_word_end_must_be_positive(self):
        interaction = _gemini_interaction('Beklenen', 'Beklenen')
        annotation = interaction['steps'][0]['content'][0]['annotations'][0]
        annotation['start_offset'] = '0s'
        annotation['end_offset'] = '0s'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(audio_qc.settings, 'gemini_api_key', 'gemini-key'),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(interaction),
                ),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(audio_path, 'Beklenen')

        self.assertEqual(
            str(caught.exception),
            'Gemini speech-to-text returned invalid word annotations',
        )

    def test_openai_transport_error_falls_back_to_elevenlabs(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if url == audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL:
                raise RuntimeError('provider transport details')
            return _Response({
                'text': 'Merhaba d\u00fcnya',
                'language_code': 'tur',
                'words': _words('Merhaba', 'd\u00fcnya'),
            })

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba d\u00fcnya',
                )

        self.assertEqual(calls, [
            audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL,
            audio_qc.ELEVENLABS_SPEECH_TO_TEXT_URL,
        ])
        self.assertEqual(result['provider'], 'elevenlabs')
        self.assertTrue(result['pass'])

    def test_openai_protocol_error_falls_back_to_elevenlabs(self):
        responses = iter([
            _Response(['not', 'an', 'object']),
            _Response({
                'text': 'Beklenen anlat\u0131m',
                'language_code': 'tur',
                'words': _words('Beklenen', 'anlat\u0131m'),
            }),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.wav'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    side_effect=lambda *args, **kwargs: next(responses),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Beklenen anlat\u0131m',
                )

        self.assertEqual(result['provider'], 'elevenlabs')
        self.assertTrue(result['pass'])

    def test_openai_nonempty_transcript_requires_word_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            for words in (None, []):
                with self.subTest(words=words):
                    payload = {
                        'text': 'Beklenen anlat\u0131m',
                        'language': 'turkish',
                    }
                    if words is not None:
                        payload['words'] = words
                    with (
                        patch.object(
                            audio_qc.settings,
                            'openai_api_key',
                            'openai-key',
                        ),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(payload),
                        ),
                    ):
                        with self.assertRaises(
                            audio_qc.AudioQCError
                        ) as caught:
                            audio_qc.verify_audio_narration(
                                audio_path,
                                'Beklenen anlat\u0131m',
                            )

                    self.assertEqual(
                        str(caught.exception),
                        'OpenAI speech-to-text returned incomplete word '
                        'timestamps',
                    )

    def test_openai_overlapping_or_duplicate_word_times_fail_closed(self):
        cases = (
            (
                {'word': 'Merhaba', 'start': 0.0, 'end': 1.0},
                {'word': 'd\u00fcnya', 'start': 0.0, 'end': 1.0},
            ),
            (
                {'word': 'Merhaba', 'start': 0.0, 'end': 1.0},
                {'word': 'd\u00fcnya', 'start': 0.5, 'end': 1.5},
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            for words in cases:
                with self.subTest(words=words):
                    payload = {
                        'text': 'Merhaba d\u00fcnya',
                        'language': 'turkish',
                        'words': list(words),
                    }
                    with (
                        patch.object(
                            audio_qc.settings,
                            'openai_api_key',
                            'openai-key',
                        ),
                        patch.object(audio_qc.settings, 'gemini_api_key', ''),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(payload),
                        ),
                    ):
                        with self.assertRaises(
                            audio_qc.AudioQCError
                        ) as caught:
                            audio_qc.verify_audio_narration(
                                audio_path,
                                'Merhaba d\u00fcnya',
                            )

                    self.assertEqual(
                        str(caught.exception),
                        'OpenAI speech-to-text returned incomplete word '
                        'timestamps',
                    )

    def test_openai_malformed_word_entries_fail_closed(self):
        malformed_entries = (
            None,
            {'word': '', 'start': 0.4, 'end': 0.8},
            {'word': ' ', 'start': 0.4, 'end': 0.8},
            {'type': 'unknown', 'text': 'd\u00fcnya', 'start': 0.4, 'end': 0.8},
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            for malformed in malformed_entries:
                with self.subTest(malformed=malformed):
                    payload = {
                        'text': 'Merhaba d\u00fcnya',
                        'language': 'turkish',
                        'words': [
                            {'word': 'Merhaba', 'start': 0.0, 'end': 0.4},
                            malformed,
                            {'word': 'd\u00fcnya', 'start': 0.8, 'end': 1.2},
                        ],
                    }
                    with (
                        patch.object(
                            audio_qc.settings,
                            'openai_api_key',
                            'openai-key',
                        ),
                        patch.object(audio_qc.settings, 'gemini_api_key', ''),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(payload),
                        ),
                    ):
                        with self.assertRaises(
                            audio_qc.AudioQCError
                        ) as caught:
                            audio_qc.verify_audio_narration(
                                audio_path,
                                'Merhaba d\u00fcnya',
                            )

                    self.assertEqual(
                        str(caught.exception),
                        'OpenAI speech-to-text returned incomplete word '
                        'timestamps',
                    )

    def test_openai_word_boundary_split_keeps_exact_timestamp_evidence(self):
        payload = {
            'text': 'Okyanusa sa\u00e7\u0131ld\u0131.',
            'language': 'turkish',
            'words': _openai_words('Okyanus', 'a', 'sa\u00e7\u0131ld\u0131'),
        }
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(audio_qc.settings, 'gemini_api_key', ''),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(payload),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    "Okyanus'a sa\u00e7\u0131ld\u0131.",
                )

        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'openai')
        self.assertTrue(
            result['mismatch_details']['timestamp_sequence_match']
        )

    def test_openai_word_boundary_merge_keeps_exact_timestamp_evidence(self):
        payload = {
            'text': 'Okyanus a sa\u00e7\u0131ld\u0131.',
            'language': 'turkish',
            'words': _openai_words('Okyanusa', 'sa\u00e7\u0131ld\u0131'),
        }
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(audio_qc.settings, 'gemini_api_key', ''),
                patch.object(audio_qc.settings, 'elevenlabs_api_key', ''),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(payload),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Okyanus a sa\u00e7\u0131ld\u0131.',
                )

        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'openai')
        self.assertTrue(
            result['mismatch_details']['timestamp_sequence_match']
        )

    def test_openai_boundary_tolerance_preserves_numeric_and_lexical_data(self):
        cases = (
            ('4,8', ('4', '8')),
            ('4.8', ('4', '8')),
            ('4 8', ('4/8',)),
            ('4 8', ('48',)),
            ('4, 8', ('48',)),
            ('2 9 y\u0131l', ('29', 'y\u0131l')),
            ('4/8', ('4', '8')),
            ('4+8', ('4', '8')),
            ('-29', ('29',)),
            ('29', ('-29',)),
            ('\u00f6ld\u00fc', ('oldu',)),
        )
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            for transcript, words in cases:
                with self.subTest(transcript=transcript, words=words):
                    payload = {
                        'text': transcript,
                        'language': 'turkish',
                        'words': _openai_words(*words),
                    }
                    with (
                        patch.object(
                            audio_qc.settings,
                            'openai_api_key',
                            'openai-key',
                        ),
                        patch.object(audio_qc.settings, 'gemini_api_key', ''),
                        patch.object(
                            audio_qc.settings,
                            'elevenlabs_api_key',
                            '',
                        ),
                        patch.object(
                            audio_qc.httpx,
                            'post',
                            return_value=_Response(payload),
                        ),
                    ):
                        with self.assertRaises(
                            audio_qc.AudioQCError
                        ) as caught:
                            audio_qc.verify_audio_narration(
                                audio_path,
                                transcript,
                            )

                    self.assertEqual(
                        str(caught.exception),
                        'OpenAI speech-to-text returned inconsistent word '
                        'timestamps',
                    )

    def test_openai_mismatch_uses_exact_elevenlabs_fallback(self):
        responses = iter([
            _Response({
                'text': 'Merhaba b\u00fcy\u00fck d\u00fcnya',
                'language': 'turkish',
                'words': _openai_words('Merhaba', 'b\u00fcy\u00fck', 'd\u00fcnya'),
            }),
            _Response({
                'text': 'Merhaba g\u00fczel d\u00fcnya',
                'language_code': 'tur',
                'words': _words('Merhaba', 'g\u00fczel', 'd\u00fcnya'),
            }),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    side_effect=lambda *args, **kwargs: next(responses),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba g\u00fczel d\u00fcnya',
                )

        self.assertEqual(result['provider'], 'elevenlabs')
        self.assertTrue(result['pass'])
        self.assertEqual(result['score'], 100.0)

    def test_openai_mismatch_survives_elevenlabs_provider_error(self):
        fallback_secret = 'never-leak-fallback-error'

        def fake_post(url, **kwargs):
            if url == audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL:
                return _Response({
                    'text': 'Merhaba b\u00fcy\u00fck d\u00fcnya',
                    'language': 'turkish',
                    'words': _openai_words(
                        'Merhaba',
                        'b\u00fcy\u00fck',
                        'd\u00fcnya',
                    ),
                })
            raise RuntimeError(f'fallback failed: {fallback_secret}')

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba g\u00fczel d\u00fcnya',
                )

        self.assertEqual(result['provider'], 'openai')
        self.assertFalse(result['pass'])
        self.assertEqual(
            result['mismatch_details']['missing_words'],
            ['g\u00fczel'],
        )
        self.assertEqual(
            result['mismatch_details']['unexpected_words'],
            ['b\u00fcy\u00fck'],
        )
        self.assertNotIn(fallback_secret, str(result))

    def test_two_mismatches_return_the_higher_score(self):
        responses = iter([
            _Response({
                'text': 'Bug\u00fcn hava g\u00fczel',
                'language': 'turkish',
                'words': _openai_words('Bug\u00fcn', 'hava', 'g\u00fczel'),
            }),
            _Response({
                'text': 'Bug\u00fcn r\u00fczgar sert',
                'language_code': 'tur',
                'words': _words('Bug\u00fcn', 'r\u00fczgar', 'sert'),
            }),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', 'openai-key'),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'eleven-key',
                ),
                patch.object(
                    audio_qc.httpx,
                    'post',
                    side_effect=lambda *args, **kwargs: next(responses),
                ),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Bug\u00fcn hava \u00e7ok g\u00fczel',
                )

        self.assertEqual(result['provider'], 'openai')
        self.assertFalse(result['pass'])
        self.assertEqual(result['score'], 75.0)

    def test_elevenlabs_fallback_uses_official_multipart_contract(self):
        payload = {
            'text': 'Merhaba d\u00fcnya',
            'language_code': 'tur',
            'language_probability': 0.99,
            'words': _words('Merhaba', 'd\u00fcnya'),
        }
        captured = {}

        def fake_post(url, **kwargs):
            captured['url'] = url
            captured.update(kwargs)
            captured['audio'] = kwargs['files']['file'][1].read()
            return _Response(payload)

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio-bytes')
            with (
                patch.object(audio_qc.settings, 'openai_api_key', ''),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    'unit-test-secret',
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                result = audio_qc.verify_audio_narration(
                    audio_path,
                    'Merhaba d\u00fcnya',
                )

        self.assertTrue(result['pass'])
        self.assertEqual(result['provider'], 'elevenlabs')
        self.assertEqual(
            captured['url'],
            'https://api.elevenlabs.io/v1/speech-to-text',
        )
        self.assertEqual(captured['headers'], {'xi-api-key': 'unit-test-secret'})
        self.assertEqual(captured['data'], {
            'model_id': 'scribe_v2',
            'language_code': 'tur',
            'num_speakers': '1',
            'tag_audio_events': 'false',
            'timestamps_granularity': 'word',
        })
        self.assertEqual(captured['files']['file'][0], 'voice.mp3')
        self.assertEqual(captured['files']['file'][2], 'audio/mpeg')
        self.assertEqual(captured['audio'], b'audio-bytes')

    def test_all_provider_errors_raise_one_secret_safe_error(self):
        openai_secret = 'never-leak-openai-key'
        gemini_secret = 'never-leak-gemini-key'
        elevenlabs_secret = 'never-leak-elevenlabs-key'

        def fake_post(url, **kwargs):
            if url == audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL:
                return _Response(
                    {'detail': f'unauthorized: {openai_secret}'},
                    status_code=401,
                )
            if url == audio_qc.GEMINI_INTERACTIONS_URL:
                return _Response(
                    {'detail': f'forbidden: {gemini_secret}'},
                    status_code=403,
                )
            raise RuntimeError(f'network error: {elevenlabs_secret}')

        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with (
                patch.object(
                    audio_qc.settings,
                    'openai_api_key',
                    openai_secret,
                ),
                patch.object(
                    audio_qc.settings,
                    'gemini_api_key',
                    gemini_secret,
                ),
                patch.object(
                    audio_qc.settings,
                    'elevenlabs_api_key',
                    elevenlabs_secret,
                ),
                patch.object(audio_qc.httpx, 'post', side_effect=fake_post),
            ):
                with self.assertRaises(audio_qc.AudioQCError) as caught:
                    audio_qc.verify_audio_narration(
                        audio_path,
                        'Beklenen metin',
                    )

        self.assertEqual(
            str(caught.exception),
            'Audio QC transcription failed for all configured providers',
        )
        self.assertNotIn(openai_secret, str(caught.exception))
        self.assertNotIn(gemini_secret, str(caught.exception))
        self.assertNotIn(elevenlabs_secret, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
