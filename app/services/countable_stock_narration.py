"""Countable fresh English writer output; never narration or QA authority."""
from copy import deepcopy


SLOTS = tuple(f'w{index:02d}' for index in range(1, 12))
RULE = (
    'COUNTABLE ENGLISH NARRATION: return narration_words instead of narration. '
    'Fill w01 through w11 in spoken order with exactly one useful word per '
    'field. Attach punctuation to its word; use ASCII apostrophes. Do not put '
    'spaces, numbers used as word labels, empty padding or multiple words in '
    'a field. The eleven words must join into one natural complete sentence. '
    'Six scenes therefore contain 66 words, within the existing 62-66-word '
    'budget. Preserve sourced meaning and every visual constraint. These '
    'fields only control writing length; the full independent source and '
    'editorial reviews and actual audio timing checks are still required.'
)


def eligible(provider, fresh, calibrated, scenes, stock_positions, targets):
    return (provider == 'abacus_included' and fresh is True and calibrated is True
        and type(scenes) is list and len(scenes) == 6
        and type(stock_positions) is list and stock_positions == list(range(6))
        and all(type(pos) is int for pos in stock_positions)
        and type(targets) is list and bool(targets)
        and all(type(target) is dict and 'locked_narration' not in target for target in targets))


def request_format(schema, shape):
    schema, shape = deepcopy((schema, shape))
    row = schema['properties']['scenes']['items']
    del row['properties']['narration']
    row['properties']['narration_words'] = {
        'type': 'object', 'properties': {slot: {'type': 'string'} for slot in SLOTS},
        'required': list(SLOTS), 'additionalProperties': False,
    }
    row['required'] = ['narration_words' if key == 'narration' else key for key in row['required']]
    for scene in shape['scenes']:
        del scene['narration']
        scene['narration_words'] = {slot: 'word' for slot in SLOTS}
    return schema, shape


def decode(value):
    """Join exact observed words into an unapproved candidate without rewriting."""
    from app.services.director import _word_count

    value = deepcopy(value)
    if type(value) is not dict or set(value) != {'scenes'} or type(value['scenes']) is not list:
        raise ValueError('countable narration requires the complete scene array')
    for row in value['scenes']:
        if type(row) is not dict or set(row) != {'position', 'narration_words', 'visual_queries', 'ai_prompt'}:
            raise ValueError('countable narration requires only the requested scene fields')
        words = row.pop('narration_words')
        if type(words) is not dict or set(words) != set(SLOTS):
            raise ValueError('countable narration requires exactly w01 through w11')
        if any(type(word) is not str or not 1 <= len(word) <= 80
               or not word.isprintable() or any(char.isspace() for char in word)
               or _word_count(word) != 1 for word in words.values()):
            raise ValueError('each narration word field must contain exactly one spoken word with attached punctuation')
        row['narration'] = ' '.join(words[slot] for slot in SLOTS)
        if _word_count(row['narration']) != 11:
            raise ValueError('joined narration must contain exactly eleven words')
    return value
