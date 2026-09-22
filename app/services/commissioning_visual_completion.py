"""One same-frame completion of missing booleans in an observed Gemini result."""
from copy import deepcopy

import httpx


class IncompleteNativeVisualReview(Exception):
    def __init__(self, data, evidence):
        super().__init__('commissioning_visual_fields_incomplete')
        self.data = deepcopy(data)
        self.evidence = deepcopy(evidence)


def partial(prepared, raw, schema):
    from app.services.visual_review_json import _review_fields
    from app.services.included_visual_completion import boolean_fields
    from app.services.gemini_generation import _decode_gemini_json_response
    from app.services.abacus_router_adapter import _enum_match, _unique_items_match
    if _review_fields(schema) is None:
        return None  # Compact completions themselves cannot recurse.
    allowed = boolean_fields(prepared)
    relaxed = deepcopy(schema)
    item = relaxed['properties']['reviews']['items']
    item['required'] = [field for field in item['required'] if field not in allowed]
    data = _decode_gemini_json_response(httpx.Response(200, content=raw), relaxed)
    indices = [row['scene_index'] for row in data['reviews']]
    if (len(indices) != len(set(indices))
            or set(indices) != set(item['properties']['scene_index']['enum'])
            or not _enum_match(data, schema) or not _unique_items_match(data, schema)
            or not any(allowed - set(row) for row in data['reviews'])):
        return None
    return data


def complete(original, prepared, purpose, ledger, foundation, context):
    from app.services import commissioning_reasoning as native, production_included_router as included
    from app.services.included_visual_fields import request, combine
    repair = request(prepared, original.data)
    output = native.generate(repair, purpose, ledger, foundation, context)
    observed = included._LAST_OBSERVED.get()
    native._require(type(observed) is dict and observed['purpose'] == purpose
        and observed['context'] == context and observed['evidence']['provider'] == 'gemini')
    result = combine(prepared, original.data, repair, output)
    evidence = {**original.evidence,
        'parsed_result_sha256': native._sha(native._raw(result)),
        'visual_completion': deepcopy(observed['evidence'])}
    return result, evidence
