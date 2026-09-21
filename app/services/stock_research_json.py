"""Read narrowly defined redundant metadata in stock-only research JSON.

Some RouteLLM results repeat ai_prompt:null within a scene. Only that exact
null-only field, in the authored stock research contract, may occur twice.
An optional scene query counter may be omitted from the parsed content only
when its integer exactly equals the actual two or three queries. Contradictory
values and every other repeated key remain invalid. This does
not authorize transport, settle old requests, edit narration or approve facts.
An optional word_count is discarded as untrusted draft bookkeeping: actual
word counts are computed from the unchanged narration by the production gates.
"""
import json
import math

from app.services.abacus_generation import _json_loads


def _stock_research_schema(schema):
    if type(schema) is not dict:
        return False
    properties = schema.get('properties') or {}
    fields = {'title', 'thumbnail_text', 'description', 'scenes', 'sources'}
    if (schema.get('type') != 'object' or set(properties) != fields
            or set(schema.get('required') or []) != fields
            or schema.get('additionalProperties') is not False):
        return False
    scenes = properties['scenes']
    scene = scenes.get('items') or {}
    return (scenes.get('type') == 'array' and scene.get('type') == 'object'
            and set(scene.get('properties') or {}) == {'narration', 'visual_queries', 'ai_prompt'}
            and set(scene.get('required') or []) == {'narration', 'visual_queries', 'ai_prompt'}
            and scene.get('additionalProperties') is False
            and scene['properties']['ai_prompt'] == {'type': 'null'})


def decode(content, schema):
    """Return existing field values; full response/schema validation is separate."""
    if not _stock_research_schema(schema):
        from app.services.visual_review_json import decode as decode_review
        return decode_review(content, schema)

    class ObjectPairs(list):
        pass

    def reject_constant(value):
        raise ValueError('non-finite number')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite number')
        return number

    parsed = json.loads(content, object_pairs_hook=ObjectPairs,
                        parse_constant=reject_constant, parse_float=finite_float)
    remaining = 50_000

    def visit(value, path):
        nonlocal remaining
        remaining -= 1
        if len(path) > 32 or remaining < 0:
            raise ValueError('research JSON structure exceeds bound')
        if isinstance(value, ObjectPairs):
            result, counts = {}, {}
            for key, item in value:
                item = visit(item, (*path, key))
                counts[key] = counts.get(key, 0) + 1
                if key in result:
                    if not (len(path) == 2 and path[0] == 'scenes' and type(path[1]) is int
                            and key == 'ai_prompt' and counts[key] == 2
                            and result[key] is None and item is None):
                        raise ValueError('duplicate JSON key')
                result[key] = item
            if (len(path) == 2 and path[0] == 'scenes' and type(path[1]) is int
                    and 'visual_queries_count' in result):
                count, queries = result['visual_queries_count'], result.get('visual_queries')
                if not (type(count) is int and 2 <= count <= 3
                        and type(queries) is list and len(queries) == count):
                    raise ValueError('research query counter disagrees with actual queries')
                # Retain every actual query, narration and source. The exact
                # response bytes remain in the provider observation/proof.
                result.pop('visual_queries_count')
            if len(path) == 2 and path[0] == 'scenes' and type(path[1]) is int:
                for field in ('word_count', 'narration_word_count'):
                    if field not in result:
                        continue
                    count = result[field]
                    if not (type(count) is int and 0 <= count <= 1000):
                        raise ValueError('invalid research draft word counter')
                    # Model-authored counts are not evidence. Do not use them for
                    # length/timing acceptance or change the actual spoken words.
                    result.pop(field)
            return result
        if type(value) is list:
            return [visit(item, (*path, index)) for index, item in enumerate(value)]
        return value

    return visit(parsed, ())
