"""Unambiguous Turkish ordinal spellings, with original token spans retained.

Plain counts remain counts. An unsigned dotted number denotes a century only
when the following noun explicitly names a century. Nothing edits the script,
the heard transcript, or the provider's word timing evidence.
"""
import re

_ORDINALS = {
    'birinci': 1, 'ikinci': 2, 'üçüncü': 3, 'dördüncü': 4, 'beşinci': 5,
    'altıncı': 6, 'yedinci': 7, 'sekizinci': 8, 'dokuzuncu': 9,
    'onuncu': 10, 'yirminci': 20, 'otuzuncu': 30, 'kırkıncı': 40, 'ellinci': 50,
    'altmışıncı': 60, 'yetmişinci': 70, 'sekseninci': 80, 'doksanıncı': 90,
    'yüzüncü': 100, 'bininci': 1000,
}
_TENS = {'on': 10, 'yirmi': 20, 'otuz': 30, 'kırk': 40, 'elli': 50,
         'altmış': 60, 'yetmiş': 70, 'seksen': 80, 'doksan': 90}
_SUFFIXES = {
    1: {'inci'}, 2: {'nci', 'inci'}, 3: {'üncü'}, 4: {'üncü'}, 5: {'inci'},
    6: {'ncı', 'ıncı'}, 7: {'nci', 'inci'}, 8: {'inci'}, 9: {'uncu'},
    10: {'uncu'}, 20: {'nci', 'inci'}, 30: {'uncu'}, 40: {'ıncı'},
    50: {'nci', 'inci'}, 60: {'ıncı'}, 70: {'inci'}, 80: {'inci'}, 90: {'ıncı'},
    100: {'üncü'}, 1000: {'inci'},
}
_CENTURY_NOUNS = {'yüzyıl', 'yüzyılın', 'yüzyılda', 'yüzyıla', 'yüzyılı',
                  'yüzyıldan', 'yüzyıldaki', 'yüzyılında', 'yüzyılının'}


def ordinal_unit(tokens, index, value, matches):
    token = tokens[index]
    number, count = _ORDINALS.get(token), 1
    if token in _TENS and index + 1 < len(tokens):
        unit = _ORDINALS.get(tokens[index + 1])
        if (unit is not None and 1 <= unit <= 9
                and value[matches[index].end():matches[index + 1].start()].isspace()):
            number, count = _TENS[token] + unit, 2
    if number is None:
        explicit = re.fullmatch(r'([1-9][0-9]{0,3})(inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü)', token)
        if explicit:
            candidate = int(explicit[1])
            final = candidate % 10 or (candidate % 100) or (100 if candidate % 1000 else 1000)
            if explicit[2] in _SUFFIXES.get(final, set()):
                number = candidate
        elif (re.fullmatch(r'[1-9][0-9]?', token) and index + 1 < len(tokens)
                and tokens[index + 1] in _CENTURY_NOUNS
                and re.fullmatch(r'\.[ \t]+', value[matches[index].end():matches[index + 1].start()])):
            number = int(token)
    return ('\x00ordinal:' + str(number), count) if number is not None else None
