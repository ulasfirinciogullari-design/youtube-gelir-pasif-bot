"""Exact calendar dates, retail counts and measured ranges in blind transcripts.

Only representation changes are compared. Names, units, modifiers, word order
and raw timing evidence remain untouched; no missing words are inferred.
"""
import calendar
import re

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_ORDINALS = dict(zip((
    'first second third fourth fifth sixth seventh eighth ninth tenth eleventh '
    'twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth '
    'nineteenth twentieth').split(), range(1, 21)))
_ORDINALS.update({'twenty ' + word: 20 + n for word, n in list(_ORDINALS.items()) if n <= 9})
_ORDINALS.update({'thirtieth': 30, 'thirty first': 31})
_CENTURIES = dict(zip(('ten eleven twelve thirteen fourteen fifteen sixteen '
                     'seventeen eighteen nineteen twenty').split(), range(10, 21)))
_RETAIL_NOUNS = {'store', 'stores', 'outlet', 'outlets', 'branch', 'branches'}
_RETAIL_VERBS = {'opened', 'operated', 'owned', 'established', 'closed'}
_MEASURES = {'grams', 'kilograms', 'milligrams', 'meters', 'metres', 'centimeters',
             'centimetres', 'millimeters', 'millimetres', 'liters', 'litres', 'watts', 'volts'}
_JOIN = r'(?:\s+|[-\u2010\u2011])'


def _joined(matches, value, start, end):
    return all(re.fullmatch(_JOIN, value[matches[p].end():matches[p + 1].start()])
               for p in range(start, end - 1))


def _small(tokens, start, end, parse_small):
    words = tokens[start:end]
    if len(words) == 1 and re.fullmatch(r'(?:0|[1-9][0-9]{0,2})', words[0]):
        return int(words[0])
    return parse_small(words)


def _year(words, parse_small):
    if len(words) == 1 and re.fullmatch(r'[12][0-9]{3}', words[0]):
        return int(words[0])
    if len(words) >= 2 and words[0] in _CENTURIES:
        rest = words[1:]
        number = 0 if rest == ['hundred'] else parse_small(rest)
        if number is not None and 0 <= number <= 99:
            return 100 * _CENTURIES[words[0]] + number
    if len(words) >= 2 and words[:2] in (['one', 'thousand'], ['two', 'thousand']):
        rest = words[2:]
        if rest[:1] == ['and']:
            rest = rest[1:]
            if not rest:
                return None
        number = parse_small(rest) if rest else 0
        if number is not None and 0 <= number <= 999:
            return (1000 if words[0] == 'one' else 2000) + number
    return None


def _date(tokens, matches, value, index, parse_small, number_words):
    month = _MONTHS.get(tokens[index])
    if month is None or index + 2 >= len(tokens):
        return None
    if not value[matches[index].end():matches[index + 1].start()].isspace():
        return None
    for day_end in range(index + 2, min(index + 4, len(tokens))):
        day_words = tokens[index + 1:day_end]
        day = _ORDINALS.get(' '.join(day_words))
        if len(day_words) == 1:
            digit = re.fullmatch(r'([1-9]|[12][0-9]|3[01])(st|nd|rd|th)?', day_words[0])
            if digit:
                day = int(digit[1])
                suffix = 'th' if 10 <= day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
                if digit[2] and digit[2] != suffix:
                    day = None
        if day is None or not _joined(matches, value, index + 1, day_end):
            continue
        if not re.fullmatch(r'(?:\s+|\s*,\s*)', value[matches[day_end - 1].end():matches[day_end].start()]):
            continue
        for end in range(day_end + 1, min(day_end + 7, len(tokens)) + 1):
            year = _year(tokens[day_end:end], parse_small)
            if year is None or not _joined(matches, value, day_end, end):
                continue
            if end < len(tokens) and (tokens[end] in number_words | {'and'} or any(c.isdigit() for c in tokens[end])):
                continue
            if day > calendar.monthrange(year, month)[1]:
                continue
            return '\x00english_calendar_date:%04d-%02d-%02d' % (year, month, day), end - index
    return None


def contextual_unit(tokens, matches, value, index, parse_small, number_words):
    date = _date(tokens, matches, value, index, parse_small, number_words)
    if date is not None:
        return date
    # Retail count with an explicit action and a bounded, unchanged noun phrase.
    if index and tokens[index - 1] in _RETAIL_VERBS and value[matches[index - 1].end():matches[index].start()].isspace():
        end = index
        while end < len(tokens) and end - index < 6 and (tokens[end] in number_words | {'and'} or tokens[end].isdecimal()):
            end += 1
        number = _small(tokens, index, end, parse_small)
        if number is not None and 0 <= number <= 999 and _joined(matches, value, index, end):
            for noun in range(end, min(end + 3, len(tokens))):
                if not tokens[noun].isalpha() or tokens[noun] in number_words | {'and', 'point', 'minus', 'plus'}:
                    break
                if not value[matches[noun - 1].end():matches[noun].start()].isspace():
                    break
                if tokens[noun] in _RETAIL_NOUNS:
                    return '\x00english_retail_count:' + str(number), end - index
    # Complete ordered range: "between three hundred and four hundred total grams".
    if not index or tokens[index - 1] != 'between' or not value[matches[index - 1].end():matches[index].start()].isspace():
        return None
    end = index
    while end < len(tokens) and end - index < 12 and (tokens[end] in number_words | {'and'} or tokens[end].isdecimal()):
        end += 1
    noun = end + 1 if end < len(tokens) and tokens[end] == 'total' else end
    if noun >= len(tokens) or tokens[noun] not in _MEASURES or not _joined(matches, value, index, end):
        return None
    if any(not value[matches[p].end():matches[p + 1].start()].isspace() for p in range(end - 1, noun)):
        return None
    candidates = []
    for split in range(index + 1, end - 1):
        if tokens[split] != 'and':
            continue
        left, right = _small(tokens, index, split, parse_small), _small(tokens, split + 1, end, parse_small)
        if left is not None and right is not None and 0 <= left <= 999 and 0 <= right <= 999:
            candidates.append((left, right))
    if len(candidates) == 1:
        left, right = candidates[0]
        return '\x00english_measurement_range:%d:%d' % (left, right), end - index
    return None
