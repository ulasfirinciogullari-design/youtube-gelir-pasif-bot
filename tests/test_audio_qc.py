import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(elevenlabs_api_key='')
_previous_config_module = sys.modules.get('app.config')
sys.modules['app.config'] = config_stub

import app.services.audio_qc as audio_qc

if _previous_config_module is None:
    sys.modules.pop('app.config', None)
else:
    sys.modules['app.config'] = _previous_config_module
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


class _Response:
    def __init__(self, payload: dict, status_code: int = 200):
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

    def test_missing_key_is_explicitly_unavailable(self):
        with patch.object(audio_qc.settings, 'elevenlabs_api_key', ''):
            result = audio_qc.verify_audio_narration(
                'not-read-without-a-key.mp3',
                'Beklenen anlat\u0131m',
            )

        self.assertFalse(result['available'])
        self.assertIsNone(result['pass'])
        self.assertIsNone(result['score'])
        self.assertEqual(result['reason'], 'elevenlabs_api_key_missing')

    def test_http_request_uses_official_multipart_contract(self):
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

    def test_transport_and_http_errors_do_not_expose_secrets(self):
        secret = 'never-leak-this-api-key'
        with tempfile.TemporaryDirectory() as temporary:
            audio_path = Path(temporary) / 'voice.mp3'
            audio_path.write_bytes(b'audio')
            with patch.object(audio_qc.settings, 'elevenlabs_api_key', secret):
                with patch.object(
                    audio_qc.httpx,
                    'post',
                    side_effect=RuntimeError(f'network error: {secret}'),
                ):
                    with self.assertRaises(audio_qc.AudioQCError) as caught:
                        audio_qc.verify_audio_narration(
                            audio_path,
                            'Beklenen metin',
                        )
                self.assertNotIn(secret, str(caught.exception))

                with patch.object(
                    audio_qc.httpx,
                    'post',
                    return_value=_Response(
                        {'detail': f'unauthorized: {secret}'},
                        status_code=401,
                    ),
                ):
                    with self.assertRaises(audio_qc.AudioQCError) as caught:
                        audio_qc.verify_audio_narration(
                            audio_path,
                            'Beklenen metin',
                        )
                self.assertEqual(
                    str(caught.exception),
                    'ElevenLabs speech-to-text failed with HTTP 401',
                )
                self.assertNotIn(secret, str(caught.exception))


if __name__ == '__main__':
    unittest.main()

