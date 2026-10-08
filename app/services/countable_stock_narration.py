"""Countable fresh English writer output; never narration or QA authority."""
from copy import deepcopy
import re


WORD_FIELD_ERROR = 'each narration word field must contain exactly one spoken word with attached punctuation'


class NarrationContractError(ValueError):
    """Invalid provider-authored words remain rejected and cannot stop a channel."""
    def __init__(self, message):
        super().__init__(message)
        from app.services.production_failures import content_rejection
        content_rejection(self, 'story_contract_rejected')


SLOTS = tuple(f'w{index:02d}' for index in range(1, 12))
EMPTY_TAIL = tuple(f'w{index:02d}' for index in range(12, 16))
RULE = (
    'COUNTABLE ENGLISH NARRATION: return narration_words instead of narration. '
    'Fill w01 through w11 in spoken order with exactly one useful word per '
    'field. Attach punctuation to its word; ASCII and typographic apostrophes '
    'inside a word are equivalent for counting. Do not put '
    'spaces, numbers used as word labels, empty padding or multiple words in '
    'a field. Compose each scene as its own natural complete eleven-word sentence, '
    'then put that sentence into its word fields. Never divide a longer paragraph '
    'into eleven-word chunks or carry a sentence into the next scene. Finish w11 '
    'with sentence-ending punctuation (. ? !); punctuation alone cannot repair '
    'an unfinished thought. Rewrite the whole sentence if it does not fit. '
    'Six scenes therefore contain 66 words, within the existing 62-66-word '
    'budget. Preserve sourced meaning and every visual constraint. These '
    'fields only control writing length; the full independent source and '
    'editorial reviews and actual audio timing checks are still required. '
    'Preserve the exact metric and direction of any percentage: faster checkout '
    'does not mean an identical percentage reduction in transaction time. '
    'If that distinction cannot fit naturally, omit the number without inventing a new claim.'
)


def sentence_boundary_error(narration):
    """Reject an obvious unfinished scene; punctuation never proves grammar or facts."""
    if not re.search(r'[.!?][\"\u201d\u2019\x27)]*$', narration):
        return ('narration has no sentence ending: rewrite this scene as one complete '
            'eleven-word sentence ending in . ? or !; do not continue into another '
            'scene or merely punctuate an unfinished phrase')
    return ''


def eligible(provider, fresh, calibrated, scenes, stock_positions, targets):
    return (provider == 'abacus_included' and fresh is True and calibrated is True
        and type(scenes) is list and len(scenes) == 6
        and type(stock_positions) is list and stock_positions == list(range(6))
        and all(type(pos) is int for pos in stock_positions)
        and type(targets) is list and bool(targets)
        and all(type(target) is dict and 'locked_narration' not in target for target in targets))


def request_format(schema, shape):
    schema, shape = deepcopy((schema, shape))
    # Some routed writers echo the contextual title. Treat that bounded value
    # as unused response metadata; it cannot replace the package's title.
    schema['properties']['title'] = {'type': 'string', 'minLength': 1, 'maxLength': 180}
    row = schema['properties']['scenes']['items']
    del row['properties']['narration']
    row['properties']['narration_words'] = {
        'type': 'object', 'properties': {**{slot: {'type': 'string'} for slot in SLOTS},
            **{slot: {'type': 'null'} for slot in EMPTY_TAIL}},
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
    if type(value) is not dict or set(value) not in ({'scenes'}, {'scenes', 'title'}) or type(value['scenes']) is not list:
        raise NarrationContractError('countable narration requires the complete scene array')
    if 'title' in value:
        title = value.pop('title')
        if type(title) is not str or not 1 <= len(title) <= 180 or not title.strip() or not title.isprintable():
            raise NarrationContractError('countable narration title metadata is invalid')
    for row in value['scenes']:
        if type(row) is not dict or set(row) != {'position', 'narration_words', 'visual_queries', 'ai_prompt'}:
            raise NarrationContractError('countable narration requires only the requested scene fields')
        words = row.pop('narration_words')
        if (type(words) is not dict or not set(SLOTS) <= set(words)
                or not set(words) <= set(SLOTS + EMPTY_TAIL)
                or any(words[slot] is not None for slot in EMPTY_TAIL if slot in words)):
            raise NarrationContractError('countable narration requires exactly w01 through w11')
        # Some routed models echo unused slots from a wider word object. Null
        # slots carry no narration: never discard an actual extra spoken word.
        words = {slot: words[slot] for slot in SLOTS}
        if any(type(word) is not str or not 1 <= len(word) <= 80
               or not word.isprintable() or any(char.isspace() for char in word)
               or _word_count(word) != 1 for word in words.values()):
            raise NarrationContractError(WORD_FIELD_ERROR)
        row['narration'] = ' '.join(words[slot] for slot in SLOTS)
        if _word_count(row['narration']) != 11:
            raise NarrationContractError('joined narration must contain exactly eleven words')
    return value


def director_eligible(fresh, language, duration, target_scenes, min_words, max_words, compact, topic):
    from app.services.director import _exact_narration_lock_from_brief

    scenes = compact.get('scenes')
    return (fresh is True and str(language).lower().startswith('en')
        and duration == .5 and target_scenes == 6 and (min_words, max_words) == (62, 66)
        and type(scenes) is list and len(scenes) == 6
        and all(type(row) is dict and not row.get('ai_prompt') for row in scenes)
        and _exact_narration_lock_from_brief(topic) is None)


def director_schema(schema):
    """Apply the same countable words to the whole-story writer and its repair."""
    result = deepcopy(schema)
    shape = {'scenes': []}
    # Reuse precisely the existing eleven-word contract, retaining all other
    # director metadata and its required fields instead of the scene-only shape.
    converted, _ = request_format(result, shape)
    converted['properties']['title'] = deepcopy(schema['properties']['title'])
    converted['properties']['scenes']['items']['properties']['ai_prompt'] = {'type': 'null'}
    return converted


def decode_director(value):
    result = deepcopy(value)
    rows = result['scenes']
    if type(rows) is not list or len(rows) != 6:
        raise NarrationContractError('countable director requires six scenes')
    decoded = decode({'scenes': [
        {'position': pos, 'narration_words': row['narration_words'],
         'visual_queries': row['visual_queries'], 'ai_prompt': row['ai_prompt']}
        for pos, row in enumerate(rows)]})
    for row, observed in zip(rows, decoded['scenes'], strict=True):
        if row.get('ai_prompt') is not None or 'narration' in row:
            raise NarrationContractError('countable director requires stock and one spoken representation')
        row.pop('narration_words')
        row['narration'] = observed['narration']
    return result
