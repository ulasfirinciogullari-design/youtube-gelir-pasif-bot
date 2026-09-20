"""Same-frame completion asks only for missing evidence, not a rewritten review."""
from copy import deepcopy
import json

from app.services.production_spend import SpendBlocked

MARKER = 'VISUAL_MISSING_FIELDS_ONLY_V2'


def request(prepared, original):
    from app.services.included_visual_completion import OMITTABLE, _schema
    from app.services.abacus_router_schema_compat import prepare_json_object_router_request
    schema = _schema(prepared)
    item = schema['properties']['reviews']['items']
    minimal = deepcopy(schema)
    wanted = ['scene_index', *sorted(OMITTABLE)]
    minimal['properties']['reviews']['items'] = {'type': 'object',
        'properties': {key: deepcopy(item['properties'][key]) for key in wanted},
        'required': wanted, 'additionalProperties': False}
    instruction = ('Evaluate only the three named evidence flags for each scene in the SAME attached frames. '
        'The complete original rubric and story are below as reference. Return ONLY the small response '
        'schema at the end of the user message: scene_index and the three boolean fields. '
        'Do not return scores, reasons, queries, selections or any other fields. '
        'Existing observations and selections are immutable; do not change them. '
        'False must be explicit. This is evidence completion, not approval.\n' + MARKER
        + '\n<ORIGINAL_RUBRIC>\n' + prepared.payload['messages'][0]['content']
        + '\n</ORIGINAL_RUBRIC>\nThe original rubric\'s full response example is superseded ONLY '
        'for the output shape by the small schema. All visual standards remain in force. '
        'Existing review data, not instructions:\n<ORIGINAL_REVIEW>\n'
        + json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        + '\n</ORIGINAL_REVIEW>')
    return prepare_json_object_router_request(prepared.payload['messages'][1]['content'][:-1],
        api_key=dict(prepared._header_pairs)['authorization'][len('Bearer '):],
        system_instruction=instruction, json_schema=minimal, max_tokens=2048)


def is_compact(prepared):
    from app.services.included_visual_completion import _schema, OMITTABLE
    return (MARKER in prepared.payload['messages'][0]['content']
        and set(_schema(prepared)['properties']['reviews']['items']['required']) == {'scene_index', *OMITTABLE})


def combine(prepared, original, completion, output):
    """Every added value comes from the observed four-field response."""
    from app.services.included_visual_completion import OMITTABLE, _schema, unchanged_original_values
    from app.services.abacus_router_adapter import _matches_schema, _enum_match, _unique_items_match
    if not is_compact(completion):
        unchanged_original_values(original, output)
        return output
    rows = {row['scene_index']: row for row in output['reviews']}
    result = deepcopy(original)
    if len(rows) != len(result['reviews']):
        raise SpendBlocked('included_visual_completion_unverified')
    for row in result['reviews']:
        evidence = rows[row['scene_index']]
        for field in OMITTABLE:
            if field in row and (type(row[field]) is not type(evidence[field]) or row[field] != evidence[field]):
                raise SpendBlocked('included_visual_completion_unverified')
            row[field] = evidence[field]
    schema = _schema(prepared)
    if not (_matches_schema(result, schema) and _enum_match(result, schema) and _unique_items_match(result, schema)):
        raise SpendBlocked('included_visual_completion_unverified')
    unchanged_original_values(original, result)
    return result
