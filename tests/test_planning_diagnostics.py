import ast
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.planning_diagnostics import planning_failure_diagnostics, story_planning_error


def test_rejected_candidate_survives_without_approval_or_private_metadata():
    error = story_planning_error('Evidence missing', scenes=[{
        'position': 0, 'narration': 'Why pay before shopping?',
        'visual_queries': ['warehouse membership card'], 'ai_prompt': None,
        'api_key': 'do-not-copy', 'approved': True,
    }], sources=[{'url': 'https://example.test/?token=do-not-copy', 'evidence': 'Annual fee.'}],
        review={'story_review': {'causal_claim_supported': False, 'reason': 'Evidence missing'},
                'dispatch_token': 'do-not-copy'})
    result = planning_failure_diagnostics(error, {'scenes': []})
    assert isinstance(error, RuntimeError)
    assert result['status'] == 'rejected_not_approved'
    assert result['publish_eligible'] is False
    assert result['candidate_kind'] == 'rejected_critic_candidate'
    assert result['scenes'][0]['narration'] == 'Why pay before shopping?'
    assert result['review']['story_review']['causal_claim_supported'] is False
    assert 'do-not-copy' not in json.dumps(result)
    result['scenes'][0]['narration'] = 'mutated'
    assert error.planning_diagnostics['scenes'][0]['narration'] != 'mutated'


def test_fallback_is_explicitly_initial_draft_not_the_rejected_final_edit():
    result = planning_failure_diagnostics(RuntimeError('Transport failed'), {
        'scenes': [{'index': 2, 'narration': 'Initial wording.'}], 'sources': [],
    })
    assert result['candidate_kind'] == 'initial_research_draft_not_final_edit'
    assert result['scenes'][0]['position'] == 2
    assert result['review'] is None


def test_deterministic_rejection_does_not_claim_an_independent_critic_review():
    error = story_planning_error('Invalid shape', scenes=[{'narration': 'Invalid candidate.'}],
                                 review={'failures': [{'position': 0, 'reason': 'Word limit'}]})
    result = planning_failure_diagnostics(error, {'scenes': [{'narration': 'Earlier draft.'}]})
    assert result['candidate_kind'] == 'rejected_planning_candidate_not_critic_reviewed'
    assert result['scenes'][0]['narration'] == 'Invalid candidate.'


@pytest.mark.parametrize('sensitive', [
    'https://example.test/video?X-Amz-Signature=SENSITIVE',
    'Bearer SENSITIVE', 'sk-SENSITIVE1234', 'AIza' + 'SENSITIVE' * 4,
    'api_key=SENSITIVE', 'client_secret: SENSITIVE', 'refresh_token=SENSITIVE',
    '"api_key": "SENSITIVE"', "'password': 'SENSITIVE'",
    '"password": "first SENSITIVE last"', 'Authorization: Basic SENSITIVE',
    r'https:\/\/example.test/?token=SENSITIVE',
    r'{\"api_key\":\"SENSITIVE\"}',
])
def test_every_text_surface_redacts_credentials_and_urls(sensitive):
    error = story_planning_error(sensitive, scenes=[{'narration': sensitive,
        'ai_prompt': sensitive, 'visual_queries': [sensitive]}],
        sources=[{'evidence': sensitive}], review={'reason': sensitive})
    assert 'SENSITIVE' not in json.dumps(planning_failure_diagnostics(error, {}))
    assert 'SENSITIVE' not in json.dumps(planning_failure_diagnostics(RuntimeError(sensitive), {}))


def test_unicode_and_nested_review_are_bounded_json():
    huge = '\U0001f680' * 100000
    error = story_planning_error(huge, scenes=[{
        'narration': huge, 'visual_queries': [huge] * 100, 'ai_prompt': huge,
    }] * 100, sources=[{'evidence': huge}] * 100,
        review={'scenes': [{'reason': huge, 'failed_checks': [huge] * 100}] * 100})
    encoded = json.dumps(error.planning_diagnostics, ensure_ascii=False).encode('utf-8')
    assert len(encoded) <= 64 * 1024
    assert len(error.planning_diagnostics['scenes']) == 6
    assert json.loads(encoded)['publish_eligible'] is False


@pytest.mark.parametrize('fresh', [True, False])
def test_actual_prepare_package_preserves_original_failure_and_never_continues(fresh):
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = deepcopy(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                         and n.name == '_prepare_package'))
    error = story_planning_error('Rejected', scenes=[{'narration': 'Rejected wording.'}])
    update = Mock()
    ns = {'set_stage': Mock(), 'research_and_script': Mock(return_value={'scenes': []}),
          'direct_and_qc': Mock(side_effect=error), 'update_job': update}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    with pytest.raises(RuntimeError) as caught:
        ns['_prepare_package'](None, 'task', 'topic', .5, 'en', {}, None, fresh_scheduled=fresh)
    assert caught.value is error
    assert update.call_count == int(fresh)
    if fresh:
        diagnostic = update.call_args.kwargs['prepaid_story_diagnostics']
        assert diagnostic['publish_eligible'] is False
        assert diagnostic['scenes'][0]['narration'] == 'Rejected wording.'


def test_diagnostic_persistence_error_does_not_replace_quality_error():
    path = Path(__file__).resolve().parents[1] / 'app/tasks.py'
    node = deepcopy(next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                         if isinstance(n, ast.FunctionDef) and n.name == '_prepare_package'))
    error = RuntimeError('Original rejection')
    ns = {'set_stage': Mock(), 'research_and_script': Mock(return_value={}),
          'direct_and_qc': Mock(side_effect=error), 'update_job': Mock(side_effect=OSError('Redis'))}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    with pytest.raises(RuntimeError) as caught:
        ns['_prepare_package'](None, 'task', 'topic', .5, 'en', {}, None, fresh_scheduled=True)
    assert caught.value is error
