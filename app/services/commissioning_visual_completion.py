"""One same-frame completion of missing booleans in an observed Gemini result."""
from copy import deepcopy
import json

def decode(raw, schema):
    """Read a real native envelope with the existing lossless visual parser.

    Only known key spelling/redundancy is normalized; conflicting duplicates,
    scores, missing values and compact completion objects remain strict.
    The caller retains and hashes the original provider bytes unchanged.
    """
    from app.services.gemini_generation import (
        _extract_output_text, _reject_duplicate_keys, _reject_non_finite,
        _matches_schema, GeminiProtocolError,
    )
    from app.services.visual_review_json import decode as visual_json
    try:
        envelope = json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                              parse_constant=_reject_non_finite)
        output = visual_json(_extract_output_text(envelope), schema)
    except Exception:
        raise GeminiProtocolError('Gemini returned invalid visual JSON') from None
    if type(output) is not dict or not _matches_schema(output, schema):
        raise GeminiProtocolError('Gemini visual output violated its schema')
    return output


class IncompleteNativeVisualReview(Exception):
    def __init__(self, data, evidence):
        super().__init__('commissioning_visual_fields_incomplete')
        self.data = deepcopy(data)
        self.evidence = deepcopy(evidence)


def partial(prepared, raw, schema):
    from app.services.visual_review_json import _review_fields
    from app.services.included_visual_completion import boolean_fields
    from app.services.abacus_router_adapter import _enum_match, _unique_items_match
    if _review_fields(schema) is None:
        return None  # Compact completions themselves cannot recurse.
    allowed = boolean_fields(prepared)
    relaxed = deepcopy(schema)
    item = relaxed['properties']['reviews']['items']
    item['required'] = [field for field in item['required'] if field not in allowed]
    data = decode(raw, relaxed)
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
