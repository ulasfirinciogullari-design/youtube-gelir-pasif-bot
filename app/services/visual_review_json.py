"""Repair only a missing opening quote on a known visual-review field name.

No values, scores, evidence, array order or missing fields are inferred. The
original provider bytes remain in the observation; ordinary schema and all
visual gates still validate the parsed result. Other JSON errors stay errors.
"""
import re

from app.services.abacus_generation import _json_loads


def decode(content, schema):
    try:
        return _json_loads(content)
    except (ValueError, TypeError):
        pass
    properties = schema.get('properties', {}) if type(schema) is dict else {}
    reviews = properties.get('reviews', {})
    row = reviews.get('items', {})
    if not (set(properties) == {'reviews'} and schema.get('required') == ['reviews']
            and schema.get('additionalProperties') is False and reviews.get('type') == 'array'
            and row.get('type') == 'object' and row.get('additionalProperties') is False
            and {'score', 'reason', 'scene_index', 'best_candidate_index', 'best_moment_index',
                 'retry_queries', 'evidence_moment_indices'} <= set(row.get('required', []))):
        return _json_loads(content)
    names = set(row.get('properties', {}))
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
                match = re.match(r'([a-z_]+)"\s*:', content[index:])
                if match and match.group(1) in names:
                    repairs += 1
                    if repairs > 8:
                        raise ValueError('visual JSON quote repair exceeds bound')
                    result.append('"')
                    quoted = True
            if char == '"':
                quoted = True
        result.append(char)
        if not quoted and not char.isspace():
            previous = char
        index += 1
    return _json_loads(''.join(result))
