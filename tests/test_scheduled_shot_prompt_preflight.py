"""New scheduled shots use bounded, lossless direction before any narration spend."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import director, research


TOPIC = 'Explain how warehouse membership fees support low merchandise margins.'
NARRATIONS = [
    'Members pay a yearly fee before entering the warehouse.',
    'The club keeps merchandise margins low across everyday goods.',
    'Plain buildings help the retailer control its operating costs.',
    'Membership fees provide steady revenue throughout the entire year.',
    'That income supports the business alongside its merchandise sales.',
    'Customers choose whether those savings justify the membership fee.',
]
SHORT = '9:16 continuous shot: the same worker in a blue shirt sorts sealed parcels at the same warehouse table; no visible brand or overlay.'
LONG = SHORT + ' Restrained observational camera and calm deliberate movement.' * 20


def _attest(package):
    package['stock_scene_qc'] = {
        'version': director._STOCK_SCENE_QC_VERSION,
        'story_review': {'accepted': True}, 'ending_pair_review': {'accepted': True},
    }
    package['short_story_qc'] = {
        'version': director._SHORT_STORY_QC_VERSION, 'requested_topic': TOPIC,
        'story_review_accepted': True, 'ending_pair_accepted': True,
    }
    package['short_story_qc']['fingerprint'] = director._short_story_fingerprint(package)
    return package


@pytest.fixture
def case(monkeypatch):
    options = {'mode': 'production', 'format': 'shorts', 'production_scheduled': True,
               'content_style': 'documentary', 'visual_mix': 'ai_first', 'quality_threshold': 86}
    package = {
        'title': 'The warehouse membership', 'description': 'Existing approved description.',
        'thumbnail_text': 'Member value', 'narration': ' '.join(NARRATIONS),
        'tts_narration': ' '.join(NARRATIONS), 'studio_options': options,
        'sources': [
            {'url': 'https://www.costco.com/about.html',
             'evidence': 'This test evidence connects warehouse membership fees to low merchandise margins.'},
            {'url': 'https://investor.costco.com/financials/annual-reports-and-proxy-statements/default.aspx',
             'evidence': 'This independent test evidence describes membership fee income in the business.'},
        ],
        'scenes': [{'index': index, 'narration': text, 'tts_text': text,
                    'visual_queries': ['warehouse employee sorts parcels', 'hands sorting warehouse parcels'],
                    'ai_prompt': SHORT if index in (0, 2) else None,
                    'pace': 'normal', 'transition': 'cut', 'custom_identity': {'shirt': 'blue'}}
                   for index, text in enumerate(NARRATIONS)],
        'director_qc': ['Original editorial record.'], 'custom_metadata': {'unchanged': [1, 'x']},
    }
    package['ai_scenes'] = [scene['ai_prompt'] for scene in package['scenes'] if scene['ai_prompt']]
    package['visual_queries'] = [q for scene in package['scenes'] for q in scene['visual_queries']]
    _attest(package)
    settings = SimpleNamespace(studio_plan_provider='gemini', gemini_api_key='mock-only',
                               gemini_model='existing-model', openai_model='existing-openai-model',
                               openai_api_key='mock-only', gemini_critic_enabled=False)
    monkeypatch.setattr(director, 'settings', settings)
    monkeypatch.setattr(research, 'settings', settings)
    monkeypatch.setattr(director, 'OpenAI', Mock(side_effect=AssertionError('No real OpenAI transport')))
    events, critic_inputs = [], []

    def generate(prompt, **kwargs):
        if prompt.startswith('Compress only'):
            events.append('compress')
            context = json.loads(prompt.split('\n', 1)[1])
            return {'scenes': [{'index': index, 'ai_prompt': SHORT}
                               for index in context['only_compress_scene_indices']]}
        events.append('critic')
        critic_inputs.append(prompt)
        assert 'Act as an independent' in prompt  # Never invoke a stock writer.
        shape = prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1]
        result, _ = json.JSONDecoder().raw_decode(shape)
        return result

    model = Mock(side_effect=generate)
    monkeypatch.setattr(director, 'generate_gemini_json', model)
    reserve = Mock(side_effect=lambda: events.append('reserve'))
    return SimpleNamespace(package=package, options=options, settings=settings, model=model,
                           events=events, critic_inputs=critic_inputs, reserve=reserve)


def _long(case, indices=(0, 2)):
    for index in indices:
        case.package['scenes'][index]['ai_prompt'] = LONG
    case.package['ai_scenes'] = [scene['ai_prompt'] for scene in case.package['scenes'] if scene['ai_prompt']]
    _attest(case.package)


def _run(case, **kwargs):
    return director.ensure_scheduled_short_shot_prompts(
        case.package, TOPIC, kwargs.get('duration', .5), 'en', case.options,
        fresh_scheduled=kwargs.get('fresh', True), before_compression=kwargs.get('reserve', case.reserve))


def test_valid_full_package_is_unchanged_with_no_model_or_reservation(case):
    before = deepcopy(case.package)
    assert director.short_story_package_is_approved(case.package, TOPIC)
    assert _run(case) is case.package and case.package == before
    assert case.events == []


@pytest.mark.parametrize('field,value', [('mode', 'preview'), ('format', 'landscape'),
    ('production_scheduled', False), ('production_scheduled', 1), ('production_scheduled', 'true')])
def test_manual_and_other_routes_remain_unchanged_even_with_long_prompts(case, field, value):
    _long(case); case.options[field] = value
    before = deepcopy(case.package)
    assert _run(case) is case.package and case.package == before and case.events == []


@pytest.mark.parametrize('kwargs', [{'fresh': False}, {'fresh': 1}, {'fresh': 'true'},
                                   {'duration': 1.0}, {'duration': True}])
def test_only_explicit_new_thirty_second_scope_changes(case, kwargs):
    _long(case)
    assert _run(case, **kwargs) is case.package and case.events == []


@pytest.mark.parametrize('prompt', ['', ' x', 'x ', 'x\ny', 'x\ty', 'x\x00y', 'x\x7fy',
                                 '\ud800', 123, False, {}, 'x' * 12001])
def test_malformed_not_merely_overlong_prompts_stop_without_reservation(case, prompt):
    _long(case, (0,))
    case.package['scenes'][2]['ai_prompt'] = prompt
    if prompt != '\ud800':
        _attest(case.package)  # An unpaired surrogate cannot have a valid UTF-8 attestation.
    with pytest.raises(director.ScheduledShotPromptError): _run(case)
    assert case.events == []


@pytest.mark.parametrize('damage', ['voice', 'media', 'approval', 'options', 'order', 'missing_prompt', 'narration'])
def test_frozen_or_inconsistent_package_is_never_compressed(case, damage):
    _long(case)
    if damage in ('voice', 'media'): case.package['_recovered_' + damage] = {}
    if damage == 'approval': case.package['short_story_qc'] = {}
    if damage == 'options': case.package['studio_options'] = {**case.options, 'format': 'landscape'}
    if damage == 'order': case.package['scenes'][0]['index'] = True
    if damage == 'missing_prompt': case.package['scenes'][1].pop('ai_prompt')
    if damage == 'narration': case.package['narration'] += ' changed'
    if damage != 'approval': _attest(case.package)
    with pytest.raises(director.ScheduledShotPromptError): _run(case)
    assert case.events == []


def test_one_compression_then_real_immutable_critic_preserves_every_nonprompt_field(case):
    _long(case)
    before = deepcopy(case.package)
    result = _run(case)
    assert case.events == ['reserve', 'compress', 'critic']
    assert case.package == before
    assert director.short_story_package_is_approved(result, TOPIC)
    for index, (old, new) in enumerate(zip(before['scenes'], result['scenes'])):
        assert {key: value for key, value in new.items() if key != 'ai_prompt'} == {
            key: value for key, value in old.items() if key != 'ai_prompt'}
        assert new['ai_prompt'] == (SHORT if index in (0, 2) else None)
    for key, value in before.items():
        if key not in {'scenes', 'ai_scenes', 'stock_scene_qc', 'short_story_qc'}:
            assert result[key] == value
    assert result['ai_scenes'] == [SHORT, SHORT]
    assert result['stock_scene_qc']['generator_calls'] == 0
    assert result['stock_scene_qc']['critic_calls'] == 1
    assert result['stock_scene_qc'] != before['stock_scene_qc']
    assert LONG in case.critic_inputs[0]
    assert 'PROMPT COMPRESSION INDEPENDENT CHECK' in case.critic_inputs[0]
    assert 'all_explicit_brief_constraints_preserved' in case.critic_inputs[0]
    assert all(call.kwargs['retry_once'] is False for call in case.model.call_args_list)
    assert all(call.kwargs['model'] == 'existing-model' for call in case.model.call_args_list)
    assert all(call.kwargs['google_search'] is False for call in case.model.call_args_list)


@pytest.mark.parametrize('prompt', ['x' * 1000, '\U0001f600' * 500])
def test_exact_utf16_boundary_is_lossless_without_models(case, prompt):
    case.package['scenes'][0]['ai_prompt'] = prompt; _attest(case.package)
    assert _run(case)['scenes'][0]['ai_prompt'] == prompt and case.events == []


def test_nonbmp_overcapacity_is_repaired_not_silently_truncated(case):
    case.package['scenes'][0]['ai_prompt'] = '\U0001f600' * 501; _attest(case.package)
    assert _run(case)['scenes'][0]['ai_prompt'] == SHORT
    assert case.events == ['reserve', 'compress', 'critic']


@pytest.mark.parametrize('damage', ['missing', 'extra', 'duplicate', 'order', 'bool_index', 'null',
    'extra_field', 'extra_top_field', 'too_long', 'emoji_too_long', 'control', 'malformed'])
def test_invalid_patch_is_terminal_after_one_attempt_and_before_critic(case, damage):
    _long(case)
    before = deepcopy(case.package)
    rows = [{'index': index, 'ai_prompt': SHORT} for index in (0, 2)]
    payload = {'scenes': rows}
    if damage == 'missing': rows.pop()
    if damage == 'extra': rows.append({'index': 1, 'ai_prompt': SHORT})
    if damage == 'duplicate': rows[1]['index'] = 0
    if damage == 'order': rows.reverse()
    if damage == 'bool_index': rows[0]['index'] = False
    if damage == 'null': rows[0]['ai_prompt'] = None
    if damage == 'extra_field': rows[0]['narration'] = 'rewrite'
    if damage == 'extra_top_field': payload['title'] = 'rewrite'
    if damage == 'too_long': rows[0]['ai_prompt'] = 'x' * 1001
    if damage == 'emoji_too_long': rows[0]['ai_prompt'] = '\U0001f600' * 501
    if damage == 'control': rows[0]['ai_prompt'] = 'x\ny'
    if damage == 'malformed': payload = None
    case.model.side_effect = None; case.model.return_value = payload
    with pytest.raises(director.ScheduledShotPromptError): _run(case)
    case.model.assert_called_once(); case.reserve.assert_called_once()
    assert case.package == before


@pytest.mark.parametrize('damage', ['missing', 'used', 'uncertain'])
def test_durable_reservation_failure_never_calls_model(case, damage):
    _long(case)
    if damage == 'missing': reserve = None
    else: reserve = Mock(side_effect=RuntimeError('private reservation detail'))
    with pytest.raises(director.ScheduledShotPromptError) as caught: _run(case, reserve=reserve)
    assert 'private' not in str(caught.value)
    case.model.assert_not_called()


@pytest.mark.parametrize('damage', ['dropped_identity', 'malformed', 'unavailable'])
def test_independent_negative_or_unknown_cannot_approve_and_cannot_retry(case, damage):
    _long(case)
    before = deepcopy(case.package)
    original = case.model.side_effect
    def generate(prompt, **kwargs):
        response = original(prompt, **kwargs)
        if prompt.startswith('Act as an independent'):
            if damage == 'unavailable': raise RuntimeError('secret provider failure')
            if damage == 'malformed': return {'unexpected': True}
            response['story_review']['all_explicit_brief_constraints_preserved'] = False
            response['story_review']['reason'] = 'all_explicit_brief_constraints_preserved: the blue shirt identity was omitted.'
        return response
    case.model.side_effect = generate
    with pytest.raises(director.ScheduledShotPromptError) as caught: _run(case)
    assert 'secret' not in str(caught.value)
    assert case.events == ['reserve', 'compress', 'critic'] and case.package == before


@pytest.mark.parametrize('damage', ['title', 'sources', 'narration', 'queries', 'identity', 'route', 'order', 'old_qa'])
def test_even_a_critic_wrapper_cannot_mutate_approved_facts_or_routes(case, monkeypatch, damage):
    _long(case)
    before = deepcopy(case.package)
    actual = director.revalidate_immutable_short_story
    def review(*args, **kwargs):
        result = actual(*args, **kwargs)
        if damage == 'title': result['title'] += ' changed'
        if damage == 'sources': result['sources'] = []
        if damage == 'narration': result['narration'] += ' changed'
        if damage == 'queries': result['scenes'][0]['visual_queries'] = ['new query']
        if damage == 'identity': result['scenes'][0]['custom_identity']['shirt'] = 'red'
        if damage == 'route': result['scenes'][1]['ai_prompt'] = SHORT
        if damage == 'order': result['scenes'].reverse()
        if damage == 'old_qa': result['short_story_qc'] = before['short_story_qc']
        elif damage != 'sources': _attest(result)
        return result
    monkeypatch.setattr(director, 'revalidate_immutable_short_story', review)
    with pytest.raises(director.ScheduledShotPromptError): _run(case)
    assert case.package == before


def test_compressor_cannot_mutate_original_input_by_reference(case, monkeypatch):
    _long(case)
    before = deepcopy(case.package)
    def compress(package, _topic, indices):
        package['sources'].clear()
        return {'scenes': [{'index': index, 'ai_prompt': SHORT} for index in indices]}
    monkeypatch.setattr(director, '_compress_scheduled_shot_prompts', compress)
    assert _run(case)['sources'] == before['sources'] and case.package == before


@pytest.mark.parametrize('status,output', [
    ('incomplete', '{}'), ('failed', '{}'),
    ('completed', '{"scenes":[],"scenes":[]}'),
    ('completed', '{"scenes":[],"x":NaN}'), ('completed', 'not JSON'),
])
def test_openai_incomplete_duplicate_or_nonfinite_response_is_terminal(case, monkeypatch, status, output):
    _long(case)
    case.settings.studio_plan_provider = 'openai'
    client = Mock(); client.responses.create.return_value = SimpleNamespace(status=status, output_text=output)
    factory = Mock(return_value=client); monkeypatch.setattr(director, 'OpenAI', factory)
    with pytest.raises(director.ScheduledShotPromptError): _run(case)
    factory.assert_called_once_with(api_key='mock-only', timeout=90.0, max_retries=0)
    client.responses.create.assert_called_once()
    request = client.responses.create.call_args.kwargs
    assert request['model'] == 'existing-openai-model'
    assert request['text']['format']['strict'] is True
    assert request['text']['format']['schema']['additionalProperties'] is False
    assert 'tools' not in request


def test_openai_complete_patch_uses_existing_model_then_separate_actual_critic(case, monkeypatch):
    _long(case)
    case.settings.studio_plan_provider = 'openai'
    def respond(**request):
        prompt = request['input']
        if prompt.startswith('Compress only'):
            payload = {'scenes': [{'index': index, 'ai_prompt': SHORT} for index in (0, 2)]}
        else:
            assert 'immutable_original_shot_prompts' in prompt and LONG in prompt
            payload, _ = json.JSONDecoder().raw_decode(
                prompt.split('Return ONLY JSON in exactly this shape:\n', 1)[1])
        return SimpleNamespace(status='completed', output_text=json.dumps(payload))
    client = Mock(); client.responses.create.side_effect = respond
    factory = Mock(return_value=client); monkeypatch.setattr(director, 'OpenAI', factory)
    result = _run(case)
    assert director.short_story_package_is_approved(result, TOPIC)
    assert client.responses.create.call_count == factory.call_count == 2
    assert all(call.kwargs['max_retries'] == 0 for call in factory.call_args_list)
    assert all(call.kwargs['model'] == 'existing-openai-model' for call in client.responses.create.call_args_list)
    case.reserve.assert_called_once(); case.model.assert_not_called()


@pytest.mark.parametrize('fresh,scheduled,mode,duration,aspect', [
    (True, True, 'production', .5, '9:16'), (False, True, 'production', .5, '16:9'),
    (True, False, 'production', .5, '16:9'), (True, 1, 'production', .5, '16:9'),
    (True, True, 'preview', .5, '16:9'), (True, True, 'production', .4, '16:9'),
])
def test_both_writers_share_new_contract_only_under_explicit_fresh_scope(case, monkeypatch, fresh, scheduled, mode, duration, aspect):
    options = {**case.options, 'production_scheduled': scheduled, 'mode': mode}
    compact = deepcopy(case.package)
    output = {key: deepcopy(value) for key, value in compact.items()
              if key in {'title', 'description', 'thumbnail_text', 'scenes', 'sources'}}
    model = Mock(return_value=output)
    monkeypatch.setattr(research, 'generate_gemini_json', model)
    research.research_and_script(TOPIC, duration, 'en', options, fresh_scheduled=fresh)
    writer_prompt = model.call_args.args[0]
    assert f'one continuous {aspect} photorealistic' in writer_prompt
    monkeypatch.setattr(director, 'generate_gemini_json', model)
    director._run_director(None, compact, TOPIC, 'English', duration, 56, 52, 60, 6,
                           options, fresh_scheduled=fresh)
    director_prompt = model.call_args.args[0]
    assert f'continuous cinematic {aspect}' in director_prompt
    for prompt in (writer_prompt, director_prompt):
        assert ('FRESH SCHEDULED SHOT CAPACITY' in prompt) == (aspect == '9:16')
        if aspect == '9:16':
            assert '1000 UTF-16 code units' in prompt
            assert 'inside that limit' in prompt and 'Keep null stock routes null' in prompt
    assert options['production_scheduled'] == scheduled and compact == case.package
