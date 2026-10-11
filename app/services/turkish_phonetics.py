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

ORDINALS = {
    "1.": "birinci",
    "2.": "ikinci",
    "3.": "üçüncü",
    "4.": "dördüncü",
    "5.": "beşinci",
    "10.": "onuncu",
    "48.": "kırk sekizinci",
}


def number_to_turkish(n: int) -> str:
    """Converts integers (0-100) to spoken Turkish words."""
    if n in NUMBER_WORDS:
        return NUMBER_WORDS[n]
    if n < 100:
        tens = (n // 10) * 10
        ones = n % 10
        return f"{NUMBER_WORDS.get(tens, '')} {NUMBER_WORDS.get(ones, '')}".strip()
    return str(n)


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
        num_val = int(match.group(1))
        num_word = number_to_turkish(num_val)
        suffix = match.group(2) or ""
        suffix = suffix.lstrip("'")
        if suffix:
            return f"yüzde {num_word}{suffix}"
        return f"yüzde {num_word}"

    result = re.sub(r"%(\d+)(?:'([a-zA-ZçğıöşüÇĞİÖŞÜ]+))?", replace_percent, result)

    # 2. Ordinal numbers
    for ord_num, ord_word in ORDINALS.items():
        result = re.sub(rf"\b{re.escape(ord_num)}", ord_word, result)

    # 3. Clean up repetitive punctuation that causes robotic pauses
    result = re.sub(r"\.{4,}", "...", result)
    result = re.sub(r"\s+", " ", result).strip()

    return result
