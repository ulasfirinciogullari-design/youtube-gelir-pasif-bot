"""Countable Turkish documentary corrections; all editorial gates still run."""
from copy import deepcopy
import re

from app.services.countable_stock_narration import NarrationContractError, WORD_FIELD_ERROR

SLOTS = tuple(f'w{i:02d}' for i in range(1, 12))
RULE = (
    'DOCUMENTARY_WORD_CONTRACT_V1: write each scene in narration_words, '
    'with exactly the fields w01 through w11 in spoken order. Each field '
    'contains one spoken word with its attached punctuation, without spaces. '
    'The eleven words must form one idiomatic Turkish sentence expressing '
    'the same source-supported thought. Thirty scenes total 330 words. '
    'Use useful explanations, never filler or invented facts to fill slots. '
    'Keep all visual directions out of narration. Preserve every source and '
    'visual constraint; this is writing structure, not factual or quality approval.'
)


def eligible(options, duration, language, target_scenes, bounds, topic, correction):
    from app.services.director import _exact_narration_lock_from_brief
    from app.services.commissioning_longform import active
    return bool(correction is True and duration == 3 and target_scenes == 30
        and str(language).casefold().startswith('turk') and bounds == (300, 330)
        and options.get('content_plan_item_id') and options.get('mode') == 'production'
        and _exact_narration_lock_from_brief(topic) is None and active())


def schema(original):
    result = deepcopy(original)
    row = result['properties']['scenes']['items']
    del row['properties']['narration']
    row['properties']['narration_words'] = {'type': 'object',
        'properties': {key: {'type': 'string'} for key in SLOTS},
        'required': list(SLOTS), 'additionalProperties': False}
    row['required'] = ['narration_words' if key == 'narration' else key for key in row['required']]
    return result


def decode(value):
    from app.services.director import _word_count
    result = deepcopy(value)
    if type(result) is not dict or type(result.get('scenes')) is not list or len(result['scenes']) != 30:
        raise NarrationContractError('Documentary word contract requires thirty complete scenes')
    for row in result['scenes']:
        words = row.get('narration_words') if type(row) is dict else None
        if type(words) is not dict or set(words) != set(SLOTS) or 'narration' in row:
            raise NarrationContractError('Documentary word contract has missing or competing narration')
        if any(type(word) is not str or not 1 <= len(word) <= 80
               or not word.isprintable() or any(c.isspace() for c in word)
               or _word_count(word) != 1 for word in words.values()):
            raise NarrationContractError(WORD_FIELD_ERROR)
        narration = ' '.join(words[key] for key in SLOTS)
        if _word_count(narration) != 11:
            raise NarrationContractError('Documentary sentence is not eleven observed words')
        del row['narration_words']
        row['narration'] = narration
    return result


def duration_failure(source):
    """Match only the local deterministic long-word-count failure contract."""
    spec = source.get('spec') or {}
    if spec.get('duration_minutes') != 3 or spec.get('language') not in {'tr', 'en'}:
        return False
    match = re.fullmatch(r'Duration gate rejected script: (\d+) words for requested 3(?:\.0)? min \(target (\d+)-(\d+)\)',
                         str(source.get('error') or ''))
    if not match:
        return False
    count, lower, upper = map(int, match.groups())
    expected = (300, 330) if spec['language'] == 'tr' else (345, 375)
    return (lower, upper) == expected and 1 <= count <= 1000 and not lower <= count <= upper
