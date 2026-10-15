"""Exact Turkish decade inflections, retaining every original timestamp token."""
import re


_SUFFIXES = ('lerdeki', 'lardaki', 'lerden', 'lardan', 'lerin', 'ların', 'lerde', 'larda', 'ler', 'lar')
_ADJECTIVE_SUFFIXES = {
    'on': 'lu', 'yirmi': 'li', 'otuz': 'lu', 'kırk': 'lı', 'elli': 'li',
    'altmış': 'lı', 'yetmiş': 'li', 'seksen': 'li', 'doksan': 'lı',
}
_YEAR_FORMS = frozenset({
    'yıllar', 'yılları', 'yıllara', 'yıllarda', 'yıllardan', 'yılların',
    'yıllardaki', 'yıllarında',
})


def _has_year_context(tokens, end, value, matches):
    return (end + 1 < len(tokens) and tokens[end + 1] in _YEAR_FORMS
            and value[matches[end].end():matches[end + 1].start()].isspace())


def decade_unit(tokens, index, value, matches):
    from app.services.audio_qc import _NUMBER_TENS, _number_word_unit
    if index and tokens[index - 1] in {'-', '+', '%', '‰', '$', '€', '£', '₺'}:
        return None
    digit = re.fullmatch(r'([12][0-9]{2}0)(' + '|'.join(_SUFFIXES) + ')', tokens[index])
    if digit:
        return '\x00turkish_decade:' + digit[1] + ':' + digit[2], 1
    # "Ellili yıllarda" and "50'li yıllarda" name the same abbreviated
    # decade. Keep 50 distinct from 1950: never infer a missing century.
    adjective = re.fullmatch(r'([1-9]0|[12][0-9]{2}0)(lı|li|lu)', tokens[index])
    if adjective and _has_year_context(tokens, index, value, matches):
        base = next((word for word, number in _NUMBER_TENS.items()
                     if number == int(adjective[1]) % 100), None)
        if base and adjective[2] == _ADJECTIVE_SUFFIXES[base]:
            return '\x00turkish_decade_adjective:' + adjective[1] + ':' + adjective[2], 1
    # Require a complete four-digit decade. "Ellilerde" alone cannot prove
    # 1950, and changing the suffix or decade must remain a lexical mismatch.
    for end in range(index, min(len(tokens), index + 7)):
        for base in _NUMBER_TENS:
            if not tokens[end].startswith(base):
                continue
            suffix = tokens[end][len(base):]
            is_adjective = (suffix == _ADJECTIVE_SUFFIXES[base]
                            and _has_year_context(tokens, end, value, matches))
            if suffix not in _SUFFIXES and not is_adjective:
                continue
            candidate = [*tokens[index:end], base]
            parsed = _number_word_unit(candidate, 0)
            if parsed is None or parsed[1] != len(candidate):
                continue
            pattern = (r'\x00number:([1-9]0|[12][0-9]{2}0):' if is_adjective
                       else r'\x00number:([12][0-9]{2}0):')
            number = re.fullmatch(pattern, parsed[0])
            if number is None or any(not value[matches[i].end():matches[i + 1].start()].isspace()
                    for i in range(index, end)):
                continue
            prefix = '\x00turkish_decade_adjective:' if is_adjective else '\x00turkish_decade:'
            return prefix + number[1] + ':' + suffix, end - index + 1
    return None
