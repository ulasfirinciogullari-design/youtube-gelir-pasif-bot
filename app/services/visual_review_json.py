"""Read unambiguous key spelling and redundant visual candidate metadata.

No values, scores, evidence, array order or missing fields are inferred. The
original provider bytes remain in the observation; ordinary schema and all
visual gates still validate the parsed result. Other JSON errors stay errors.
An optional candidate_index may be discarded only when it exactly repeats the
required integer best_candidate_index. It can never supply a missing choice.
"""
import json
import math
import re

from app.services.abacus_generation import _json_loads


def _review_fields(schema):
    properties = schema.get('properties', {}) if type(schema) is dict else {}
    reviews = properties.get('reviews', {})
    row = reviews.get('items', {})
    if not (set(properties) == {'reviews'} and schema.get('required') == ['reviews']
            and schema.get('additionalProperties') is False and reviews.get('type') == 'array'
            and row.get('type') == 'object' and row.get('additionalProperties') is False
            and {'score', 'reason', 'scene_index', 'best_candidate_index', 'best_moment_index',
                 'retry_queries', 'evidence_moment_indices'} <= set(row.get('required', []))):
        return None
    return set(row.get('properties', {}))


def _quoted_keys(content, names):
    result, quoted, escaped, previous, repairs, index = [], False, False, '', 0, 0
    while index < len(content):
        char = content[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        else:
            if previous in ('{', ',') and char.isascii() and char.isalpha():
                match = re.match(r'([a-z_]+)("?)\s*:', content[index:])
                if match and match.group(1) in names:
                    repairs += 1
                    if repairs > 8:
                        raise ValueError('visual JSON quote repair exceeds bound')
                    # Only key quotes are inserted. The colon, whitespace and
                    # every value stay byte-for-byte unchanged.
                    result.append('"' + match.group(1) + '"')
                    index += len(match.group(1)) + len(match.group(2))
                    previous = '"'
                    continue
            if char == '"':
                quoted = True
        result.append(char)
        if not quoted and not char.isspace():
            previous = char
        index += 1
    return ''.join(result)


def _review_json(content, schema):
    """Collapse an identical repeated boolean only at its actual scene node."""
    class ObjectPairs(list):
        pass

    def reject_constant(value):
        raise ValueError('non-finite number')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite number')
        return number

    row_schema = schema['properties']['reviews']['items']
    booleans = {key for key, spec in row_schema['properties'].items() if spec == {'type': 'boolean'}}
    remaining = 50_000

    def visit(value, path):
        nonlocal remaining
        remaining -= 1
        if len(path) > 32 or remaining < 0:
            raise ValueError('visual JSON structure exceeds bound')
        if isinstance(value, ObjectPairs):
            result, counts = {}, {}
            for key, item in value:
                item = visit(item, (*path, key))
                counts[key] = counts.get(key, 0) + 1
                if key in result and not (len(path) == 2 and path[0] == 'reviews'
                        and type(path[1]) is int and key in booleans and counts[key] == 2
                        and type(result[key]) is bool and type(item) is bool and result[key] is item):
                    raise ValueError('duplicate JSON key')
                result[key] = item
            return result
        if type(value) is list:
            return [visit(item, (*path, index)) for index, item in enumerate(value)]
        return value

    return visit(json.loads(content, object_pairs_hook=ObjectPairs,
        parse_constant=reject_constant, parse_float=finite_float), ())


def decode(content, schema):
    names = _review_fields(schema)
    try:
        parsed = _json_loads(content)
    except (ValueError, TypeError):
        if names is None or type(content) is not str:
            raise
        parsed = _review_json(_quoted_keys(content, names | {'candidate_index'}), schema)
    if names is None or 'candidate_index' in names or type(parsed) is not dict:
        return parsed
    rows = parsed.get('reviews')
    if type(rows) is not list:
        return parsed
    for row in rows:
        if type(row) is not dict or 'candidate_index' not in row:
            continue
        candidate, selected = row['candidate_index'], row.get('best_candidate_index')
        if not (type(candidate) is int and type(selected) is int
                and candidate >= 0 and candidate == selected):
            raise ValueError('visual candidate annotation contradicts selection')
        row.pop('candidate_index')
    return parsed
