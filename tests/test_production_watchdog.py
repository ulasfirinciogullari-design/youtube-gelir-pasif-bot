from copy import deepcopy
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location('watchdog', Path(__file__).resolve().parents[1] / 'ops/production_watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

OWNER = {'project_id': watchdog.PROJECT, 'environment_id': watchdog.ENV, 'service_id': watchdog.SERVICE,
    'deployment_id': '11111111-1111-4111-8111-111111111111',
    'instance_id': '22222222-1111-4111-8111-111111111111', 'git_sha': 'a' * 40}
CURRENT = {**OWNER, 'instance_id': '33333333-1111-4111-8111-111111111111'}
CANDIDATE = {'execution': {'identity': OWNER}}


def payload(status='REMOVED'):
    return {'data': {'deployment': {'id': OWNER['deployment_id'], 'projectId': watchdog.PROJECT,
        'environmentId': watchdog.ENV, 'serviceId': watchdog.SERVICE,
        'instances': [{'id': OWNER['instance_id'], 'status': status}]}}}


def test_only_exact_authenticated_removed_instance_supplies_recovery_proof():
    query = Mock(return_value=payload())
    proof = watchdog.removal_observation(CANDIDATE, CURRENT, query)
    assert proof['instance_status'] == 'REMOVED' and proof['instance_id'] == OWNER['instance_id']
    assert len(proof['response_sha256']) == 64
    query.assert_called_once_with(OWNER['deployment_id'])


@pytest.mark.parametrize('change', ['running', 'crashed', 'missing_instance', 'duplicate_instance',
    'wrong_deployment', 'wrong_project', 'wrong_service', 'wrong_environment', 'graphql_error',
    'same_replica', 'unsafe_id'])
def test_absence_age_and_other_scope_do_not_prove_removal(change):
    response = payload(); candidate = deepcopy(CANDIDATE); current = deepcopy(CURRENT)
    deployment = response['data']['deployment']
    if change in {'running', 'crashed'}: deployment['instances'][0]['status'] = change.upper()
    elif change == 'missing_instance': deployment['instances'] = []
    elif change == 'duplicate_instance': deployment['instances'] *= 2
    elif change == 'wrong_deployment': deployment['id'] = 'other'
    elif change.startswith('wrong_'): deployment[change.removeprefix('wrong_') + 'Id'] = 'other'
    elif change == 'graphql_error': response['errors'] = [{'message': 'unavailable'}]
    elif change == 'same_replica': current = OWNER
    elif change == 'unsafe_id': candidate['execution']['identity']['deployment_id'] = '$(echo injected)'
    assert watchdog.removal_observation(candidate, current, Mock(return_value=response)) is None


def test_regular_tick_never_rewrites_active_or_unknown_jobs(monkeypatch):
    remote = Mock(return_value={'current_identity': CURRENT, 'candidates': [CANDIDATE]})
    monkeypatch.setattr(watchdog, 'remote', remote)
    monkeypatch.setattr(watchdog, 'query_deployment', Mock(return_value=payload('RUNNING')))
    assert watchdog.run()['actions'] == []
    remote.assert_called_once_with('candidates')


def test_proven_removal_invokes_only_the_fenced_operation(monkeypatch):
    remote = Mock(side_effect=[{'current_identity': CURRENT, 'candidates': [CANDIDATE]}, {'status': 'failure_recorded'}])
    monkeypatch.setattr(watchdog, 'remote', remote)
    monkeypatch.setattr(watchdog, 'query_deployment', Mock(return_value=payload()))
    assert watchdog.run()['actions'] == [{'status': 'failure_recorded'}]
    assert [call.args[0] for call in remote.call_args_list] == ['candidates', 'record_removed_replica']
