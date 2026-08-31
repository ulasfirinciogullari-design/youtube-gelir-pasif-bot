import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace()
sys.modules['app.config'] = config_stub
sys.modules.setdefault('httpx', types.ModuleType('httpx'))
sys.modules.setdefault('redis', types.ModuleType('redis'))

from app.services.voice import normalize_turkish_tts
from app.services.voice import _voice_speed
from app.services.voice import synthesize_voice_with_id
import app.services.voice as voice_module


class _FakeVoiceResponse:
    content = b'voice-bytes'

    def raise_for_status(self):
        return None


class TurkishVoiceNormalizationTests(unittest.TestCase):
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

