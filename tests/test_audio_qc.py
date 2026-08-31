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

    def test_both_provider_errors_raise_one_secret_safe_error(self):
        openai_secret = 'never-leak-openai-key'
        elevenlabs_secret = 'never-leak-elevenlabs-key'

        def fake_post(url, **kwargs):
            if url == audio_qc.OPENAI_AUDIO_TRANSCRIPTIONS_URL:
                return _Response(
                    {'detail': f'unauthorized: {openai_secret}'},
                    status_code=401,
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
        self.assertNotIn(elevenlabs_secret, str(caught.exception))


if __name__ == '__main__':
    unittest.main()

