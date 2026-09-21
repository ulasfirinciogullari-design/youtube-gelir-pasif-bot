"""Unambiguous null metadata does not relax facts, request binding or reviews."""
from copy import deepcopy
import hashlib
import json

import pytest

from app.services import abacus_router_adapter as adapter, stock_research_json
from app.services import included_research_sources as sources, research
from app.services.abacus_router_schema_compat import prepare_json_object_router_request
from test_abacus_router_adapter import KEY, envelope, response


def contract():
    return sources.consulted_source_schema(research._research_json_schema(6, exact_scene_count=True),
        [{'url': url} for url in sources.GOLDMAN_BACKUP_SOURCES])


def result():
    return {'title': 'A sourced museum story', 'thumbnail_text': 'Wheels',
        'description': 'Unapproved synthetic research.',
        'scenes': [{'narration': 'A visible basket moves on wheels.',
                    'visual_queries': ['basket wheels', 'shopping cart'], 'ai_prompt': None}
                   for _ in range(6)],
        'sources': [{'url': url, 'evidence': 'Synthetic test evidence.'}
                    for url in sources.GOLDMAN_BACKUP_SOURCES]}


def redundant_content():
    return json.dumps(result()).replace('"ai_prompt": null', '"ai_prompt": null, "ai_prompt": null')


def prepared(schema=None):
    return prepare_json_object_router_request([{'type': 'text', 'text': 'Synthetic sourced research.'}],
        api_key=KEY, system_instruction='Keep factual and editorial review separate.',
        json_schema=contract() if schema is None else schema)


def observe(content, schema=None):
    request = prepared(schema)
    payload = envelope()
    payload['choices'][0]['message']['content'] = content
    wire = response(request, payload=payload)
    return adapter.observe_router_response(request, wire), wire, request


def test_all_six_redundant_nulls_preserve_every_value_and_exact_wire_proof():
    content = redundant_content()
    observed, wire, request = observe(content)
    assert observed.result == result()
    assert json.loads(wire.content)['choices'][0]['message']['content'] == content
    assert observed.evidence['response_body_sha256'] == hashlib.sha256(wire.content).hexdigest()
    assert observed.evidence['request_sha256'] == request.request_sha256
    assert observed.evidence['parsed_result_sha256'] == hashlib.sha256(adapter._canonical(result())).hexdigest()
    assert 'approved' not in observed.result and 'accepted' not in observed.result


def test_normal_research_is_unchanged_and_legacy_contract_keeps_duplicate_rejection():
    assert observe(json.dumps(result()))[0].result == result()
    schema = contract()
    schema['properties']['scenes']['items']['properties']['ai_prompt'] = {'type': ['string', 'null']}
    with pytest.raises(adapter.AbacusRouterError):
        observe(redundant_content(), schema)
    assert stock_research_json.decode(json.dumps(result()), schema) == result()


@pytest.mark.parametrize('claimed_count', [0, 7, 13, 1000])
@pytest.mark.parametrize('field', ['word_count', 'narration_word_count'])
def test_untrusted_draft_word_count_never_replaces_actual_narration_or_approves_it(claimed_count, field):
    data = result()
    for scene in data['scenes']:
        scene[field] = claimed_count
    content = json.dumps(data)
    observed, wire, request = observe(content)
    assert observed.result == result()
    assert all(len(scene['narration'].split()) == 6 for scene in observed.result['scenes'])
    assert json.loads(wire.content)['choices'][0]['message']['content'] == content
    assert observed.evidence['response_body_sha256'] == hashlib.sha256(wire.content).hexdigest()
    assert observed.evidence['request_sha256'] == request.request_sha256
    assert 'approved' not in observed.result and 'accepted' not in observed.result


@pytest.mark.parametrize('count', [-1, 1001, True, 7.0, '7', None, {}, []])
def test_invalid_draft_word_counter_still_rejected(count):
    data = result()
    data['scenes'][0]['word_count'] = count
    with pytest.raises(adapter.AbacusRouterError):
        observe(json.dumps(data))


@pytest.mark.parametrize('where', ['root', 'source', 'nested', 'duplicate', 'approval', 'legacy'])
def test_word_counter_exception_is_scoped_to_unreviewed_stock_scene_metadata(where):
    data = result()
    schema = contract()
    if where == 'root': data['word_count'] = 7
    elif where == 'source': data['sources'][0]['word_count'] = 7
    elif where == 'nested': data['scenes'][0]['nested'] = {'word_count': 7}
    else: data['scenes'][0]['word_count'] = 7
    if where == 'approval': data['scenes'][0]['approved'] = True
    if where == 'legacy':
        schema['properties']['scenes']['items']['properties']['ai_prompt'] = {'type': ['string', 'null']}
    content = json.dumps(data)
    if where == 'duplicate': content = content.replace('"word_count": 7', '"word_count": 7, "word_count": 7', 1)
    with pytest.raises(adapter.AbacusRouterError):
        observe(content, schema)


def test_redundant_query_counts_preserve_content_and_actual_wire_without_approval():
    original = result()
    for scene in original['scenes']:
        scene['visual_queries_count'] = len(scene['visual_queries'])
    content = json.dumps(original)
    observed, wire, request = observe(content)
    assert observed.result == result()
    assert json.loads(wire.content)['choices'][0]['message']['content'] == content
    assert observed.evidence['response_body_sha256'] == hashlib.sha256(wire.content).hexdigest()
    assert observed.evidence['request_sha256'] == request.request_sha256
    assert 'approved' not in observed.result and 'accepted' not in observed.result
    schema = contract()
    schema['properties']['scenes']['items']['properties']['ai_prompt'] = {'type': ['string', 'null']}
    with pytest.raises(adapter.AbacusRouterError):observe(content, schema)


@pytest.mark.parametrize('count', [0, 1, 3, 4, True, 2.0, '2', None])
def test_wrong_or_unbounded_redundant_query_counter_is_rejected(count):
    data = result()
    assert len(data['scenes'][0]['visual_queries']) == 2
    data['scenes'][0]['visual_queries_count'] = count
    with pytest.raises(adapter.AbacusRouterError):observe(json.dumps(data))


@pytest.mark.parametrize('where', ['root', 'source', 'nested', 'duplicate', 'unrelated'])
def test_query_counter_exception_cannot_admit_other_metadata_or_duplicate_keys(where):
    data = result()
    if where == 'root':data['visual_queries_count'] = 2
    elif where == 'source':data['sources'][0]['visual_queries_count'] = 2
    elif where == 'nested':data['scenes'][0]['nested'] = {'visual_queries_count': 2}
    elif where == 'unrelated':data['scenes'][0]['approved'] = True
    else:data['scenes'][0]['visual_queries_count'] = 2
    content = json.dumps(data)
    if where == 'duplicate':content = content.replace('"visual_queries_count": 2', '"visual_queries_count": 2, "visual_queries_count": 2', 1)
    with pytest.raises(adapter.AbacusRouterError):observe(content)


@pytest.mark.parametrize('replacement', [
    '"ai_prompt": null, "ai_prompt": "paid scene"',
    '"ai_prompt": "paid scene", "ai_prompt": null',
    '"ai_prompt": null, "ai_prompt": false',
    '"ai_prompt": 0, "ai_prompt": null',
    '"ai_prompt": null, "ai_prompt": null, "ai_prompt": null',
    '"ai_prompt": null, "narration": "A visible basket moves on wheels."',
    '"ai_prompt": null, "visual_queries": ["basket wheels", "shopping cart"]',
    '"ai_prompt": null, "accepted": false, "accepted": false',
])
def test_contradictory_null_extra_duplicates_or_other_scene_keys_are_rejected(replacement):
    content = json.dumps(result()).replace('"ai_prompt": null', replacement, 1)
    with pytest.raises(adapter.AbacusRouterError):
        observe(content)


@pytest.mark.parametrize('content', [
    '{"ai_prompt": null, "ai_prompt": null}',
    '{"scenes": {"0": {"ai_prompt": null, "ai_prompt": null}}}',
    '{"sources": [{"ai_prompt": null, "ai_prompt": null}]}',
    '{"scenes": [{"nested": {"ai_prompt": null, "ai_prompt": null}}]}',
    '{"scenes": [{"ai_prompt": NaN}]}', '{"scenes": [{"ai_prompt": 1e999}]}',
    '{"scenes": [], "scenes": []}', '{"title": "same", "title": "same"}',
])
def test_path_boundaries_nonfinite_numbers_and_other_duplicates_stay_invalid(content):
    with pytest.raises(ValueError):
        stock_research_json.decode(content, contract())


@pytest.mark.parametrize('damage', ['unread_source', 'missing_source', 'scene_count',
                                   'paid_scene', 'missing_narration', 'extra_field'])
def test_entire_original_schema_still_validates_after_redundant_null(damage):
    data = result()
    if damage == 'unread_source': data['sources'][0]['url'] = sources.GOLDMAN_REFERENCE
    elif damage == 'missing_source': data['sources'].pop()
    elif damage == 'scene_count': data['scenes'].pop()
    elif damage == 'paid_scene': data['scenes'][-1]['ai_prompt'] = 'Generate a historical photograph'
    elif damage == 'missing_narration': del data['scenes'][-1]['narration']
    else: data['sources'][0]['approved'] = True
    content = json.dumps(data).replace('"ai_prompt": null', '"ai_prompt": null, "ai_prompt": null', 1)
    with pytest.raises(adapter.AbacusRouterError):
        observe(content)


def test_envelope_and_review_duplicates_remain_strict():
    request = prepared()
    wire = response(request, payload=envelope())
    raw = b'{"model":"route-llm",' + wire.content[1:]
    with pytest.raises(adapter.AbacusRouterError):
        adapter.observe_router_response(request, response(request, raw=raw))
    for content in ('{"accepted": false, "accepted": true}',
                    '{"scenes":[{"ai_prompt":null,"ai_prompt":null}]}'):
        with pytest.raises(ValueError):
            stock_research_json.decode(content, {'type': 'object'})


def test_null_contract_does_not_change_the_callers_legacy_research_schema():
    legacy = research._research_json_schema(6, exact_scene_count=True)
    before = deepcopy(legacy)
    native = sources.consulted_source_schema(legacy, [{'url': url} for url in sources.GOLDMAN_BACKUP_SOURCES])
    assert legacy == before
    assert native['properties']['scenes']['items']['properties']['ai_prompt'] == {'type': 'null'}
    assert legacy['properties']['scenes']['items']['properties']['ai_prompt'] == {'type': ['string', 'null']}
