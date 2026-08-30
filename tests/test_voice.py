import sys
import types
import unittest
from types import SimpleNamespace


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace()
sys.modules['app.config'] = config_stub

from app.services.voice import normalize_turkish_tts


class TurkishVoiceNormalizationTests(unittest.TestCase):
    def test_qr_code_phrases_do_not_duplicate_code_word(self):
        cases = {
            'QR kod kasada okunur': 'kare kod kasada okunur.',
            'QR kodu kasada okunur': 'kare kodu kasada okunur.',
            'QR kodunu kameraya gösterir': 'kare kodunu kameraya gösterir.',
            'QR kodunda çizik vardır': 'kare kodunda çizik vardır.',
            'QR koduyla ödeme yapar': 'kare koduyla ödeme yapar.',
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
