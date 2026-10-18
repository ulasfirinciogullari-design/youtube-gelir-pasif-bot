"""
Turkish Phonetic & Numerical Normalization Engine for Ultra-Natural TTS.
Converts symbols, percentages (e.g. %90'ı -> yüzde doksanı), ordinal numbers (1. -> birinci),
and symbols into 100% natural, human-pronounced Turkish words.
Prevents neural TTS engines (ElevenLabs & Edge-TTS) from sounding robotic or choking on symbols.
"""

import re

NUMBER_WORDS = {
    0: "sıfır", 1: "bir", 2: "iki", 3: "üç", 4: "dört", 5: "beş",
    6: "altı", 7: "yedi", 8: "sekiz", 9: "dokuz", 10: "on",
    20: "yirmi", 30: "otuz", 40: "kırk", 50: "elli", 60: "altmış",
    70: "yetmiş", 80: "seksen", 90: "doksan", 100: "yüz"
}

# Before a capitalised word the dot may end a sentence ("bizde ise sadece 7. Bu da..."), so only these numbers are
# read as ordinals there ("1. Dünya Savaşı"); before a lowercase word every number is ("15. kattaki").
ORDINALS_BEFORE_CAPITAL = (1, 2, 3, 4, 5, 10, 48)
ORDINAL_SUFFIX = {"a": "ıncı", "ı": "ıncı", "e": "inci", "i": "inci", "o": "uncu", "u": "uncu", "ö": "üncü", "ü": "üncü"}
LOWER = "a-zçğıöşü"


LARGE_UNITS = ((10**9, "milyar"), (10**6, "milyon"), (1000, "bin"))
# A number written with thousand dots ("10.000", "1.500.000"), optionally with a suffix ("100.000'den"). A leading
# zero makes it a decimal ("0.001 saniye"), which stays as written.
GROUPED_DIGITS = r"[1-9]\d{0,2}(?:\.\d{3})+"
GROUPED_NUMBER_RE = re.compile(rf"(?<![\d.,%])({GROUPED_DIGITS})(?![\d]|[.,]\d)(?:'([a-zA-ZçğıöşüÇĞİÖŞÜ]+))?")


def number_to_turkish(n: int) -> str:
    """Converts a non-negative integer to spoken Turkish words ("10000" -> "on bin", "1500" -> "bin beş yüz")."""
    if n in NUMBER_WORDS:
        return NUMBER_WORDS[n]
    if n < 100:
        tens = (n // 10) * 10
        ones = n % 10
        return f"{NUMBER_WORDS.get(tens, '')} {NUMBER_WORDS.get(ones, '')}".strip()
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = "yüz" if hundreds == 1 else f"{NUMBER_WORDS[hundreds]} yüz"
        return f"{head} {number_to_turkish(rest)}" if rest else head
    for value, name in LARGE_UNITS:
        if n >= value:
            count, rest = divmod(n, value)
            head = "bin" if value == 1000 and count == 1 else f"{number_to_turkish(count)} {name}"
            return f"{head} {number_to_turkish(rest)}" if rest else head
    return str(n)


def ordinal_to_turkish(n: int) -> str:
    """15 -> "on beşinci", 4 -> "dördüncü", 2 -> "ikinci": the suffix follows the last vowel of the spoken number."""
    words = number_to_turkish(n)
    suffix = ORDINAL_SUFFIX[next(ch for ch in reversed(words) if ch in ORDINAL_SUFFIX)]
    if words[-1] in ORDINAL_SUFFIX:
        suffix = suffix[1:]
    elif words.endswith("dört"):
        words = words[:-1] + "d"
    return words + suffix


def _with_suffix(words: str, suffix: str) -> str:
    """Attaches a written suffix to the spoken number: "on bin" + "den" -> "on binden"; "dört" softens before a
    vowel ("on dört" + "ü" -> "on dördü")."""
    if not suffix:
        return words
    if words.endswith("dört") and suffix[0].lower() in "aeıioöuü":
        words = words[:-1] + "d"
    return words + suffix


def normalize_turkish_tts_text(text: str) -> str:
    """
    Transforms raw script text into flawlessly pronounceable phonetic Turkish.
    Examples:
    - "%90'ı" -> "yüzde doksanı"
    - "%95'inin" -> "yüzde doksan beşinin"
    - "%50'si" -> "yüzde ellisi"
    - "%100" -> "yüzde yüz"
    - "1." -> "birinci"
    """
    if not text:
        return ""

    result = text

    # 1. Percentage patterns: %90'ı, %95'inin, %50, %100'ü
    def replace_percent(match):
        num_word = number_to_turkish(int(match.group(1).replace(".", "")))
        return f"yüzde {_with_suffix(num_word, match.group(2) or '')}"

    result = re.sub(rf"%({GROUPED_DIGITS}(?![.,]?\d)|\d+(?![\d]|[.,]\d))(?:'([a-zA-ZçğıöşüÇĞİÖŞÜ]+))?", replace_percent, result)

    # 2. Numbers with thousand dots, before the ordinals: "10.000 metrede" had become "onuncu000 metrede" and was
    #    read digit by digit (plane Short WADWjFRfYYw, 2026-10-15).
    result = GROUPED_NUMBER_RE.sub(
        lambda m: _with_suffix(number_to_turkish(int(m.group(1).replace(".", ""))), m.group(2) or ""), result)

    # 3. Ordinal numbers: a whole number followed by a dot and a word ("1. Dünya Savaşı"), never part of a longer
    #    number or a sentence-final count ("Baykuşta tam 14."). Ya Olsaydı? read "15. kattaki" as "on beş katta ki"
    #    (Ulaş, 2026-10-18), so any number counts before a lowercase word; written suffixes too ("3'üncü").
    result = re.sub(rf"(?<![\d.,])(\d+)\.(?=\s+[{LOWER}])", lambda m: ordinal_to_turkish(int(m.group(1))), result)
    result = re.sub(rf"(?<![\d.,])({'|'.join(map(str, ORDINALS_BEFORE_CAPITAL))})\.(?=\s+[^\W\d])",
                    lambda m: ordinal_to_turkish(int(m.group(1))), result)
    result = re.sub(rf"(?<![\d.,])(\d+)'[iıuü]?nc[iıuü]([{LOWER}]*)",
                    lambda m: ordinal_to_turkish(int(m.group(1))) + m.group(2), result)

    # 4. Clean up repetitive punctuation that causes robotic pauses
    result = re.sub(r"\.{4,}", "...", result)
    result = re.sub(r"\s+", " ", result).strip()

    return result
