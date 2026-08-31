import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace()
_previous_config_module = sys.modules.get('app.config')
sys.modules['app.config'] = config_stub

from app.services.voice import normalize_turkish_tts
from app.services.voice import _voice_speed
from app.services.voice import _fit_duration
from app.services.voice import synthesize_voice_with_id
import app.services.voice as voice_module

if _previous_config_module is None:
    sys.modules.pop('app.config', None)
else:
    sys.modules['app.config'] = _previous_config_module


class _FakeVoiceResponse:
    content = b'voice-bytes'

    def raise_for_status(self):
        return None


class TurkishVoiceNormalizationTests(unittest.TestCase):
    def test_short_preview_always_fits_content_before_reserved_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'voice.mp3'
            output.write_bytes(b'raw')

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b'fitted')

            with (
                patch.object(
                    voice_module,
                    '_media_duration',
                    side_effect=[30.048, 29.52],
                ),
                patch.object(
                    voice_module.subprocess,
                    'run',
                    side_effect=fake_ffmpeg,
                ) as run,
            ):
                durations, before, after, rate = _fit_duration(
                    output,
                    [9.0, 6.0, 9.0, 6.0],
                    30.0,
                )

        self.assertEqual(before, 30.048)
        self.assertEqual(after, 29.52)
        self.assertAlmostEqual(rate, 30.048 / 29.5, places=6)
        self.assertAlmostEqual(
            sum(durations),
            30.0 * 29.52 / 30.048,
            places=6,
        )
        self.assertIn(
            f'atempo={rate:.6f}',
            run.call_args.args[0],
        )

    def test_short_preview_uses_clearer_deliberate_voice_speed(self):
        self.assertEqual(_voice_speed(30), 0.92)
        self.assertEqual(_voice_speed(40), 0.92)
        self.assertEqual(_voice_speed(60), 1.01)
        self.assertEqual(_voice_speed(None), 1.01)

    @patch.object(voice_module.httpx, 'post', create=True)
    def test_selected_speed_is_sent_to_elevenlabs(self, post):
        config_stub.settings.elevenlabs_api_key = 'test-key'
        post.return_value = _FakeVoiceResponse()

        audio = synthesize_voice_with_id(
            'Elif telefonu cebine koyar.',
            'test-voice',
            speed=0.84,
        )

        self.assertEqual(audio, b'voice-bytes')
        request = post.call_args
        self.assertEqual(
            request.kwargs['json']['voice_settings']['speed'],
            0.84,
        )

    def test_qr_code_phrases_do_not_duplicate_code_word(self):
        cases = {
            'QR kod kasada okunur': 'kare kod kasada okunur.',
            'QR kodu kasada okunur': 'kare kodu kasada okunur.',
            'QR kodunu kameraya gösterir': 'kare kodunu kameraya gösterir.',
            'QR kodunda çizik vardır': 'kare kodunda çizik vardır.',
            'QR koduyla ödeme yapar': 'kare koduyla ödeme yapar.',
            'QR kodları rafta görünür': 'kare kodları rafta görünür.',
            'QR kodlarla giriş yapılır': 'kare kodlarla giriş yapılır.',
            'QR kodlarında çizik vardır': 'kare kodlarında çizik vardır.',
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                actual = normalize_turkish_tts(source)
                self.assertEqual(actual, expected)
                self.assertEqual(
                    normalize_turkish_tts(actual),
                    expected,
                )
                self.assertNotIn('kare kod kod', actual)

    def test_supported_standalone_initialisms_remain_idempotent(self):
        cases = {
            'OLED ekran kararır': 'oled ekran kararır.',
            'GPS sinyali güçlenir': 'ci pi es sinyali güçlenir.',
            'QR kasada okunur': 'kare kod kasada okunur.',
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                actual = normalize_turkish_tts(source)
                self.assertEqual(actual, expected)
                self.assertEqual(
                    normalize_turkish_tts(actual),
                    expected,
                )


if __name__ == '__main__':
    unittest.main()

