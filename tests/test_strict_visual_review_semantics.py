"""Frozen request/result parity with the pre-extraction 634ae0a implementation.

All images are synthetic and all senders are tripwires or local captures. These
fixtures establish interpretation/request parity, not actual sampling authority.
"""
import base64
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services import visual_qc as visual
from app.services import strict_visual_review_semantics as pure
from app.services import abacus_router_review_runtime as runtime
from test_visual_qc import _review, _trusted_image_motion_spec, JPEG_BYTES


def raw(value):
    def default(item):
        if type(item) is bytes:
            return {'synthetic_bytes_base64': base64.b64encode(item).decode('ascii')}
        if type(item) is set:
            return sorted(item)
        raise TypeError
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=default).encode()


def case(name):
    scenes = [{'index': 7, 'narration': 'Mert laptopu masaya koyuyor.',
               'visual_queries': ['man puts laptop on desk']},
              {'index': 11, 'narration': 'A machine sorts paper.',
               'visual_queries': ['paper sorting machine'], 'ai_prompt': 'A paper sorting machine.'}]
    specs = [[{'path': 'first.mp4', 'generated': True, 'source_type': 'generated',
               'generation_provider': 'synthetic-test'}], ['second.mp4']]
    options = {'topic': 'A literal source topic', 'story_scenes': deepcopy(scenes),
               'content_style': '', 'evidence_sources': []}
    if name == 'documentary':
        options.update(content_style='documentary', evidence_sources=[{
            'url': 'https://www.bep.gov/currency/how-money-is-made',
            'evidence': 'U.S. currency paper is 75% cotton and 25% linen.'}])
    elif name == 'trusted':
        specs[0] = [_trusted_image_motion_spec('first.mp4')]
    elif name == 'replica':
        scenes[0]['ai_prompt'] = 'A molded plastic toy man puts a toy laptop on the desk.'
        scenes[0]['narration'] = 'A toy man puts a toy laptop on the desk.'
        options['story_scenes'] = deepcopy(scenes)
    return scenes, specs, options


def live(module, monkeypatch, tmp_path, provider, name, response=None):
    scenes, specs, options = case(name)
    frame = tmp_path / 'synthetic.jpg'
    frame.write_bytes(JPEG_BYTES)
    settings = SimpleNamespace(studio_visual_qc_provider='', studio_plan_provider=provider,
        openai_api_key='synthetic', openai_model='synthetic', gemini_api_key='synthetic',
        gemini_model='synthetic')
    monkeypatch.setattr(module, 'settings', settings)
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: provider == 'abacus_router')
    monkeypatch.setattr(runtime, 'retained_router_review_evidence', lambda: {})
    samples, calls = [], []
    def sample(path, target, fraction):
        index = 0 if path == 'first.mp4' else 1
        if name == 'partial' and (index == 0 or fraction == .06):
            return None
        samples.append({'scene_index': index, 'candidate_index': 0,
                        'moment_index': module.MOMENT_FRACTIONS.index(fraction), 'jpeg': JPEG_BYTES})
        return frame
    def request(*args, **kwargs):
        calls.append(deepcopy(args[:7]))
        return deepcopy(response if response is not None else
                        {'reviews': [_review(index) for index in args[5]]})
    monkeypatch.setattr(module, '_frame', sample)
    monkeypatch.setattr(module, '_request_visual_review', request)
    result = module.review_scene_visuals(scenes, specs, tmp_path / 'work', 6,
        _missing_review_attempts=0, _score_reason_consistency_attempts=0,
        _temporal_response_repair_attempts=0, **options)
    assert len(calls) == 1
    return {'call': calls[0], 'result': result}, samples, (scenes, specs, options)


# SHA256 of complete captured positional request payload + complete normalized
# result, generated once from committed 634ae0a before this extraction. No Git or
# old implementation is loaded by these tests.
GOLDEN = {'openai:literal': '73b9819b6fed6fbaa106abffe1aebf9bf643f14f4650a8e3929aa4709433d26d', 'openai:documentary': '2775b1ead39ea2f291c5715c01c3087e248b83d59cee6ad98594bc124a1bb15a', 'openai:trusted': '2e1e265ea0fc51622f67f15da4f12b9a7724ddae89538866f20e9f803e47b98c', 'openai:replica': 'ce271ae3708e3ae26e1029dc484d6a3df8fe953b33aa969461fd324f83ca471c', 'gemini:literal': '61aac377385bbe03e3853d2f6d356003636c183521d26e77f56d9619bbdf63f5', 'gemini:documentary': '439497bc8bc8d298a88251240e7be849160dbd5c27576a7ae3c77c0f15f4ee06', 'gemini:trusted': '10fa7b06671bf9f54616ae3b0ea7ad551315e4e3ab505d74e3d114c3ff9a8bd0', 'gemini:replica': 'f4215ca4d95252f0052d4805e7a4b3ac5dca82afc9063546a0128c8df9f975bc', 'abacus_router:literal': 'c0b0161e6cc12a9d4a79c66ca81347afbcf923e9410d21649fba439a6dfab64e', 'abacus_router:documentary': 'f16088ae5ad2d63318ce7e180b430306664945a625f536f6f53d46bf58e76e5d', 'abacus_router:trusted': 'af7c514d6fd0f2f66b190ef290f177cbaa8c39b6d9ed93d3c864fb1cf16abe2f', 'abacus_router:replica': 'b32135f7fb3c03f1a9767568d9099a31fe1b8a26d9a0db1dd792b53f41476067', 'openai:partial': 'edf5858b0fa79f0ea707439f4e2e6646ea4b446523a1b920884ef93cf9351e40', 'gemini:partial': '81897de5816860a39ac5851b59d3f211e0f4d579366a33dda775c9a377c1e101'}


@pytest.mark.parametrize('provider,name', [(p, n) for p in
    ('openai', 'gemini', 'abacus_router') for n in ('literal', 'documentary', 'trusted', 'replica')]
    + [('openai', 'partial'), ('gemini', 'partial')])
def test_frozen_full_request_and_semantic_parity(monkeypatch, tmp_path, provider, name):
    snapshot, samples, (scenes, specs, options) = live(visual, monkeypatch, tmp_path, provider, name)
    assert hashlib.sha256(raw(snapshot)).hexdigest() == GOLDEN[provider + ':' + name]
    derived = pure.derive_strict_visual_review_contract(scenes, specs, samples=samples, **options)
    call = snapshot['call']
    assert derived['request']['system_instruction'] == call[2]
    assert derived['content'] == call[3]
    assert derived['gemini_parts'] == call[4]
    assert derived['semantic_arguments']['included_indices'] == call[5]
    assert derived['semantic_arguments']['available_moments'] == call[6]
    assert derived['request']['json_schema'] == visual._review_json_schema(call[5], call[6])
    assert derived['request']['parts'] == [
        {'type': 'text', 'text': row['text']} if row['type'] == 'input_text' else
        {'type': 'image_url', 'image_url': {'url': row['image_url']}}
        for row in call[3][1:]]


def arguments():
    scenes, specs, options = case('literal')
    samples = [{'scene_index': index, 'candidate_index': 0, 'moment_index': moment,
                'jpeg': JPEG_BYTES} for index in range(2) for moment in (3, 0, 1, 2, 4)]
    return pure.derive_strict_visual_review_contract(scenes, specs, samples=samples, **options)


@pytest.mark.parametrize('damage', ['duplicate', 'bool_id', 'bool_score', 'unknown_field', 'missing_flag',
                                    'unknown_moment', 'missing_reason', 'query_type'])
def test_structural_failures_do_not_acquire_semantic_acceptance(damage):
    data = {'reviews': [_review()]}
    row = data['reviews'][0]
    if damage == 'duplicate': data['reviews'].append(deepcopy(row))
    elif damage == 'bool_id': row['scene_index'] = False
    elif damage == 'bool_score': row['score'] = True
    elif damage == 'unknown_field': row['approved'] = True
    elif damage == 'missing_flag': row.pop('major_visual_artifact_visible')
    elif damage == 'unknown_moment': row['best_moment_index'] = 10
    elif damage == 'missing_reason': row['reason'] = ' '
    elif damage == 'query_type': row['retry_queries'] = 'replacement'
    before = deepcopy(data)
    assert pure.normalize_strict_visual_reviews(data, **arguments()['semantic_arguments']) == {}
    assert data == before


@pytest.mark.parametrize('field', ['major_visual_artifact_visible', 'effectively_static_or_frozen',
    'prominent_readable_text_or_logo_visible', 'substantially_repeats_adjacent_scene',
    'authored_identity_or_material_conflict_visible'])
def test_existing_editorial_and_identity_clamps_remain_hard(field):
    derived = arguments()
    rows = pure.normalize_strict_visual_reviews({'reviews': [_review(**{field: True})]},
                                                **derived['semantic_arguments'])
    rows = pure.annotate_visual_hard_gates(rows)
    assert rows[0]['score'] <= 40
    assert rows[0]['editorial_gate_passed'] is False


def test_router_contradiction_rejects_without_new_request_and_outputs_are_detached(monkeypatch):
    derived = arguments()
    data = {'reviews': [_review(score=30, reason='The clip matches the narration and scene requirements.')]}
    rows = pure.normalize_strict_visual_reviews(data, **derived['semantic_arguments'])
    rows = pure.annotate_visual_hard_gates(rows)
    original = deepcopy(rows)
    def forbidden(*args, **kwargs):
        raise AssertionError('no provider or retry')
    monkeypatch.setattr(visual, '_request_visual_review', forbidden)
    closed = pure.close_router_score_reason_conflicts(rows, scenes=case('literal')[0], documentary_sources=[])
    assert closed[0]['score'] <= 40
    assert closed[0]['score_reason_revalidation_attempted'] is False
    assert closed[0]['score_reason_revalidated'] is False
    assert rows == original
    closed[0]['retry_queries'].append('detached')
    assert rows == original


@pytest.mark.parametrize('damage', ['order', 'duplicate', 'unknown_scene', 'bool_moment', 'empty', 'extra', 'wrong_jpeg'])
def test_pure_builder_rejects_ambiguous_sample_identity(damage):
    scenes, specs, options = case('literal')
    sample = {'scene_index': 0, 'candidate_index': 0, 'moment_index': 0, 'jpeg': JPEG_BYTES}
    samples = [sample]
    if damage == 'order': samples += [dict(sample, moment_index=3)]
    elif damage == 'duplicate': samples += [dict(sample)]
    elif damage == 'unknown_scene': sample['scene_index'] = 4
    elif damage == 'bool_moment': sample['moment_index'] = False
    elif damage == 'empty': samples = []
    elif damage == 'extra': sample['trusted'] = True
    elif damage == 'wrong_jpeg': sample['jpeg'] = 'not bytes'
    with pytest.raises(ValueError, match='^strict_visual_contract_invalid$'):
        pure.derive_strict_visual_review_contract(scenes, specs, samples=samples, **options)
