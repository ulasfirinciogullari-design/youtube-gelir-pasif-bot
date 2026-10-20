"""The real immutable director sends its complete critic contract to the router."""
from contextlib import contextmanager
from copy import deepcopy
import json
from unittest.mock import Mock

import httpx
import pytest

from app.services import abacus_router_review_runtime as runtime, director
from app.services import preserved_visual_recovery as recovery
from app.services.abacus_router_adapter import prepare_router_request, observe_router_response, ENDPOINT
from app.services.production_spend import SpendBlocked
from app.services.production_connection_continuity import LEAF_ID
from test_immutable_selected_story import case, planning_case, TOPIC, _review, _route, _shape


def route(monkeypatch, transform=lambda value: value):
    evidence = {}
    def generate(parts, **kwargs):
        assert kwargs['purpose'] == 'immutable_story_review'
        prepared = prepare_router_request(parts, api_key='offline-key',
            **{key: kwargs[key] for key in ('system_instruction', 'json_schema', 'max_tokens')})
        value = transform(_shape(parts[0]['text']))
        wire = prepared.wire_kwargs(); wire.pop('timeout')
        response = httpx.Response(200, request=httpx.Request('POST', ENDPOINT, **wire), json={
            'id': 'offline-story-1', 'object': 'chat.completion', 'model': 'route-llm',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(value)}}]})
        observed = observe_router_response(prepared, response)
        evidence['immutable_story_review'] = observed.evidence
        return observed.result
    sender = Mock(side_effect=generate)
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: True)
    monkeypatch.setattr(runtime, 'retained_router_review_approval_active', lambda: True)
    monkeypatch.setattr(runtime, 'generate_retained_router_review', sender)
    monkeypatch.setattr(runtime, 'retained_router_review_evidence', lambda: deepcopy(evidence))
    monkeypatch.setattr(director, 'paid_response', Mock(side_effect=AssertionError('No paid fallback')))
    return sender, evidence


@pytest.mark.parametrize('count', [6, 12])
@pytest.mark.parametrize('kind', ['stock', 'mixed', 'generated'])
def test_complete_original_rubric_schema_and_story_are_preserved(case, monkeypatch, count, kind):
    _route(case, count, kind)
    _review(case)  # Obtain the full existing contract, with a mocked provider.
    prior = case.model.call_args
    case.model.reset_mock()
    before = deepcopy(case.package)
    sender, evidence = route(monkeypatch)
    out = _review(case)
    sender.assert_called_once()
    assert sender.call_args.args[0] == [{'type': 'text', 'text': prior.args[0]}]
    assert sender.call_args.kwargs['json_schema'] == prior.kwargs['json_schema']
    assert case.package == before
    assert {k: v for k, v in out.items() if k not in {'stock_scene_qc', 'short_story_qc'}} == {
        k: v for k, v in before.items() if k not in {'stock_scene_qc', 'short_story_qc'}}
    qc = out['stock_scene_qc']
    assert qc['generator_calls'] == qc['attempts_used'] == 0 and qc['critic_calls'] == 1
    assert qc['included_router_critic'] == evidence['immutable_story_review']
    assert qc['included_router_critic']['underlying_model_verified'] is False
    assert 'gemini_critic' not in qc
    assert director.short_story_package_is_approved(out, TOPIC)
    case.model.assert_not_called(); director.paid_response.assert_not_called()
    director.OpenAI.assert_not_called(); director._run_director.assert_not_called()


@pytest.mark.parametrize('section,field', [
    ('story_review', 'causal_claim_supported'), ('story_review', 'natural_spoken_language'),
    ('ending_pair', 'same_actor_or_object_thread'), ('scene', 'queries_match_same_action'),
])
def test_schema_valid_negative_verdict_never_rewrites_or_retries(case, monkeypatch, section, field):
    def negative(value):
        row = value['scenes'][0] if section == 'scene' else value[section]
        row[field] = False
        if field == 'natural_spoken_language':
            row['natural_spoken_language_evidence'] = 'Scene 1 "Members pay" sounds unnatural.'
        return value
    sender, _ = route(monkeypatch, negative)
    before = deepcopy(case.package)
    with pytest.raises(RuntimeError):
        _review(case)
    sender.assert_called_once()
    assert case.package == before
    case.model.assert_not_called(); director.paid_response.assert_not_called()
    director._run_director.assert_not_called()


def test_router_protocol_or_funding_failure_is_terminal(case, monkeypatch):
    sender, _ = route(monkeypatch)
    sender.side_effect = SpendBlocked('router_review_outcome_unverified')
    with pytest.raises(SpendBlocked):
        _review(case)
    sender.assert_called_once()
    case.model.assert_not_called(); director.paid_response.assert_not_called()


def test_scope_cannot_fund_writing(case, monkeypatch):
    sender, _ = route(monkeypatch)
    with pytest.raises(SpendBlocked, match='scope_or_critic_conflict'):
        _review(case, immutable_scene_fields=False)
    sender.assert_not_called(); case.model.assert_not_called()
    director.paid_response.assert_not_called(); director.OpenAI.assert_not_called()


@pytest.mark.parametrize('gemini_enabled', [False, True])
def test_router_proof_is_honest_scoped_and_cannot_be_replayed_by_a_package_marker(case, monkeypatch, gemini_enabled):
    case.settings.gemini_critic_enabled = gemini_enabled
    sender, evidence = route(monkeypatch)
    out = _review(case)
    assert director.short_story_package_is_approved(out, TOPIC)
    assert 'gemini_critic' not in out['stock_scene_qc']
    sender.assert_called_once(); case.model.assert_not_called()
    changed = deepcopy(out)
    changed['stock_scene_qc']['included_router_critic']['status_code'] = 200.0
    assert not director.short_story_package_is_approved(changed, TOPIC)
    for field in ('title', 'custom_metadata'):
        changed = deepcopy(out)
        changed[field] = 'changed non-QA field'
        assert not director.short_story_package_is_approved(changed, TOPIC)
    assert not director.short_story_package_is_approved(out, TOPIC + ' changed')
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: False)
    assert not director.short_story_package_is_approved(out, TOPIC)
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: True)
    old = deepcopy(evidence)
    evidence.clear()  # A different, newly opened scope has no acknowledged review.
    assert not director.short_story_package_is_approved(out, TOPIC)
    evidence.update(old)
    evidence['immutable_story_review']['response_proof_sha256'] = 'f' * 64
    assert not director.short_story_package_is_approved(out, TOPIC)


def test_caller_authored_router_metadata_without_typed_completed_review_is_rejected(case, monkeypatch):
    sender, evidence = route(monkeypatch)
    case.package['stock_scene_qc']['included_router_critic'] = {'response_proof_sha256': 'a' * 64}
    director._INCLUDED_STORY_APPROVAL.set(None)
    evidence['immutable_story_review'] = case.package['stock_scene_qc']['included_router_critic']
    assert not director.short_story_package_is_approved(case.package, TOPIC)
    sender.assert_not_called()


def test_explicit_preparation_uses_context_and_exits_after_failure(monkeypatch, tmp_path):
    events = []
    @contextmanager
    def scope(source):
        assert source == LEAF_ID
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')
    monkeypatch.setattr(runtime, 'retained_router_review_scope', scope)
    def fail(*args, **kwargs):
        assert args == (LEAF_ID, tmp_path) and kwargs == {}
        assert events == ['enter']
        raise recovery.PreservedVisualRecoveryError()
    monkeypatch.setattr(recovery, 'prepare_preserved_visual_recovery', fail)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        recovery.prepare_subscription_router_recovery(LEAF_ID, tmp_path)
    assert events == ['enter', 'exit']


@pytest.mark.parametrize('source,repairs,overrides', [
    ('11111111-1111-4111-8111-111111111111', (), None),
    (LEAF_ID, (0,), None), (LEAF_ID, (0,), {0: 'A changed retained shot'}),
])
def test_included_scope_rejects_different_source_or_new_repair_prompts_before_io(
    monkeypatch, tmp_path, source, repairs, overrides,
):
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: True)
    state = Mock(side_effect=AssertionError('No source I/O allowed'))
    monkeypatch.setattr(recovery, '_state', state)
    with pytest.raises(recovery.PreservedVisualRecoveryError):
        recovery.prepare_preserved_visual_recovery(source, tmp_path,
            repair_scene_indices=repairs, shot_prompt_overrides=overrides)
    state.assert_not_called()


def test_private_recovery_audit_preserves_observer_but_never_arbitrary_error_text(monkeypatch):
    monkeypatch.setattr(runtime, 'retained_router_review_active', lambda: True)
    evidence = {'immutable_story_review': {'returned_model': 'route-llm', 'underlying_model_verified': False}}
    monkeypatch.setattr(runtime, 'retained_router_review_evidence', lambda: deepcopy(evidence))
    audit = {'status': 'story_review_rejected_or_unavailable'}
    recovery._capture_included_router_review(audit, RuntimeError('secret arbitrary provider text'))
    assert 'secret' not in json.dumps(audit)
    assert audit['included_router_review']['observations'] == evidence
    recovery._capture_included_router_review(audit, SpendBlocked('router_review_outcome_unverified'))
    assert audit['included_router_review']['failure_code'] == 'router_review_outcome_unverified'
    from app.services.planning_diagnostics import story_planning_error
    rejected = story_planning_error('Story was rejected', scenes=[], sources=[],
        review={'story_review': {'causal_claim_supported': False, 'reason': 'The causal claim lacks support.'}})
    recovery._capture_included_router_review(audit, rejected)
    report = audit['included_router_review']['rejected_story']
    assert report['publish_eligible'] is False
    assert report['review']['story_review']['causal_claim_supported'] is False
