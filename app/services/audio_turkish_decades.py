"""Exact Turkish decade inflections, retaining every original timestamp token."""
import re


_SUFFIXES = ('lerdeki', 'lardaki', 'lerden', 'lardan', 'lerin', 'ların', 'lerde', 'larda', 'ler', 'lar')


def decade_unit(tokens, index, value, matches):
    from app.services.audio_qc import _NUMBER_TENS, _number_word_unit
    if index and tokens[index - 1] in {'-', '+', '%', '‰', '$', '€', '£', '₺'}:
        return None
    digit = re.fullmatch(r'([12][0-9]{2}0)(' + '|'.join(_SUFFIXES) + ')', tokens[index])
    if digit:
        return '\x00turkish_decade:' + digit[1] + ':' + digit[2], 1
    # Require a complete four-digit decade. "Ellilerde" alone cannot prove
    # 1950, and changing the suffix or decade must remain a lexical mismatch.
    for end in range(index, min(len(tokens), index + 7)):
        for base in _NUMBER_TENS:
            if not tokens[end].startswith(base):
                continue
            suffix = tokens[end][len(base):]
            if suffix not in _SUFFIXES:
                continue
            candidate = [*tokens[index:end], base]
            parsed = _number_word_unit(candidate, 0)
            if parsed is None or parsed[1] != len(candidate):
                continue
            number = re.fullmatch(r'\x00number:([12][0-9]{2}0):', parsed[0])
            if number is None or any(not value[matches[i].end():matches[i + 1].start()].isspace()
                    for i in range(index, end)):
                continue
            return '\x00turkish_decade:' + number[1] + ':' + suffix, end - index + 1
    return None
