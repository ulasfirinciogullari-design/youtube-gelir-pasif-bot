"""Numbers the narrator reads aloud.

The plane Short (WADWjFRfYYw, 2026-10-15) said "10.000 metrede" as digits because the ordinal rule turned the text
into "onuncu000 metrede" before it reached ElevenLabs; Ulaş caught it.
"""

import pytest

from app.services.turkish_phonetics import normalize_turkish_tts_text, number_to_turkish


@pytest.mark.parametrize("text, spoken", [
    ("Bu devasa uçaklar, 10.000 metrede güvenle süzülür.", "Bu devasa uçaklar, on bin metrede güvenle süzülür."),
    ("Saniyede 1.500.000 kez", "Saniyede bir milyon beş yüz bin kez"),
    ("Kalbi 100.000'den fazla atar", "Kalbi yüz binden fazla atar"),
    ("1.001 gece", "bin bir gece"),
    ("Tam 14.000'i", "Tam on dört bini"),
    ("Sayı 3.014'ü geçti", "Sayı üç bin on dördü geçti"),
    ("Fiyatı %1.000 arttı", "Fiyatı yüzde bin arttı"),
    ("%14'ü", "yüzde on dördü"),
])
def test_numbers_with_thousand_dots_are_spelled_out(text, spoken):
    assert normalize_turkish_tts_text(text) == spoken


@pytest.mark.parametrize("text, spoken", [
    ("1. Dünya Savaşı", "birinci Dünya Savaşı"),
    ("Baykuşta tam 14.", "Baykuşta tam 14."),  # a count at the end of a sentence stays a count
    ("Yarışı 5. bitirdi", "Yarışı beşinci bitirdi"),
    ("Bu 2.5 saniye sürer", "Bu 2.5 saniye sürer"),
    ("Pi sayısı 3.14 ile başlar", "Pi sayısı 3.14 ile başlar"),
    ("Sadece 0.001 saniye sürer", "Sadece 0.001 saniye sürer"),  # a decimal, not a thousand
    ("1950'lerde kare camlar", "1950'lerde kare camlar"),
    ("%90'ı suyla dolu", "yüzde doksanı suyla dolu"),
    ("Faiz %2.5 oldu", "Faiz %2.5 oldu"),
])
def test_ordinals_and_other_numbers_are_left_whole(text, spoken):
    assert normalize_turkish_tts_text(text) == spoken


def test_number_words():
    assert [number_to_turkish(n) for n in (0, 15, 100, 101, 270, 1000, 1950, 10000, 14000, 2000000000)] == [
        "sıfır", "on beş", "yüz", "yüz bir", "iki yüz yetmiş", "bin", "bin dokuz yüz elli", "on bin", "on dört bin",
        "iki milyar"]
