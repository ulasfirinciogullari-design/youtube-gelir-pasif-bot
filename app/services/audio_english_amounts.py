"""Exact whole currency amounts; no fuzzy name, year or general count matching."""
import re

_SCALES = {'thousand': 1000, 'million': 1_000_000,
           'billion': 1_000_000_000, 'trillion': 1_000_000_000_000}
_CURRENCIES = {'dollar', 'dollars', 'cent', 'cents', 'euro', 'euros',
               'pound', 'pounds', 'krona', 'kronor', 'krone', 'kroner', 'lira', 'yen'}
_QUALIFIERS = {'danish', 'swedish', 'norwegian', 'british', 'american', 'us', 'turkish'}
_MEASUREMENTS = {'gram', 'grams', 'kilogram', 'kilograms', 'milligram', 'milligrams',
    'meter', 'meters', 'metre', 'metres', 'centimeter', 'centimeters', 'centimetre', 'centimetres',
    'millimeter', 'millimeters', 'millimetre', 'millimetres', 'liter', 'liters', 'litre', 'litres',
    'watt', 'watts', 'volt', 'volts'}
_SPELLING_STEMS = {'tokenis', 'organis', 'recognis', 'realis', 'specialis', 'standardis',
                   'optimis', 'capitalis', 'industrialis', 'globalis',
                   'prioritis', 'modernis', 'centralis', 'decentralis', 'privatis',
                   'nationalis', 'mobilis', 'customis', 'digitalis', 'digitis', 'utilis'}
_SPELLING_ENDINGS = {'e', 'ed', 'es', 'ing', 'ation', 'ations', 'er', 'ers'}
_OTHER_SPELLINGS = {'analyse': 'analyze', 'analysed': 'analyzed',
                    'analysing': 'analyzing', 'colour': 'color', 'colours': 'colors',
                    'coloured': 'colored', 'colouring': 'coloring', 'labour': 'labor',
                    'labours': 'labors', 'laboured': 'labored', 'centre': 'center',
                    'centres': 'centers', 'defence': 'defense'}


def spelling_key(token):
    """Named UK/US orthographic variants only; never replace arbitrary -ise."""
    for stem in _SPELLING_STEMS:
        if token.startswith(stem) and token[len(stem):] in _SPELLING_ENDINGS:
            return stem[:-1] + 'z' + token[len(stem):]
    return _OTHER_SPELLINGS.get(token, token)


def _digits(token):
    if (re.fullmatch(r'(?:0|[1-9][0-9]{0,14})', token)
            or re.fullmatch(r'[1-9][0-9]{0,2}(?:,[0-9]{3}){1,4}', token)):
        return int(token.replace(',', ''))
    return None


def currency_amount_unit(tokens, matches, value, index, parse_small, number_words):
    def numeric(token):
        return token in number_words or _digits(token) is not None

    if not numeric(tokens[index]) or index and numeric(tokens[index - 1]):
        return None
    end = index
    while end < len(tokens) and end - index < 10 and (numeric(tokens[end]) or tokens[end] == 'and'):
        end += 1
    noun = end + 1 if end < len(tokens) and tokens[end] in _QUALIFIERS else end
    if noun >= len(tokens) or tokens[noun] not in _CURRENCIES:
        return None
    # No punctuation, operator, decimal or sign can join number fragments.
    if any(not re.fullmatch(r'(?:\s+|[-\u2010\u2011])',
            value[matches[pos].end():matches[pos + 1].start()]) for pos in range(index, end - 1)):
        return None
    if any(not value[matches[pos].end():matches[pos + 1].start()].isspace()
            for pos in range(end - 1, noun)):
        return None
    words = tokens[index:end]
    scale = _SCALES.get(words[-1], 1)
    coefficient = words[:-1] if words[-1] in _SCALES else words
    number = _digits(coefficient[0]) if len(coefficient) == 1 else None
    if number is None:
        number = parse_small(coefficient)
    if number is None or not 0 <= number * scale <= 10**15:
        return None
    return '\x00english_currency_amount:' + str(number * scale), end - index


def measurement_amount_unit(tokens, matches, value, index, parse_small, number_words):
    """Exact whole 0..999 measurement, including 'forty-five-gram headphones'.

    The unit remains a separate compared token: no unit conversion, inferred
    plural, bare count, sign, decimal, leading zero or malformed number list.
    """
    def numeric(token):
        return token in number_words or _digits(token) is not None
    if not numeric(tokens[index]):
        return None
    if index and (numeric(tokens[index - 1]) or tokens[index - 1] in {'minus', 'negative', 'plus'}):
        return None
    if matches[index].start() and value[matches[index].start() - 1] in '+-−±':
        return None
    end = index
    while end < len(tokens) and end - index < 6 and (numeric(tokens[end]) or tokens[end] == 'and'):
        end += 1
    if end >= len(tokens) or tokens[end] not in _MEASUREMENTS:
        return None
    if any(not re.fullmatch(r'(?:\s+|[-\u2010\u2011])',
            value[matches[pos].end():matches[pos + 1].start()]) for pos in range(index, end)):
        return None
    words = tokens[index:end]
    number = _digits(words[0]) if len(words) == 1 else None
    if number is None:
        number = parse_small(words)
    if number is None or not 0 <= number <= 999:
        return None
    return '\x00english_measurement_amount:' + str(number), end - index
