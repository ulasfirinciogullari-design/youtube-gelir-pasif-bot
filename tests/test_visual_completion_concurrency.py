import json

import pytest
from redis.exceptions import WatchError

from app.services import production_included_router as included
from app.services.production_spend import SpendBlocked
from test_included_visual_completion import case, commissioned, client, run, journal


@pytest.mark.parametrize('phase', ['link', 'cached'])
def test_real_conflict_repeats_only_local_link_and_keeps_both_provider_observations(case, monkeypatch, phase):
    if phase == 'cached': run(case)
    original = included.IncludedRouterLedger._ack
    conflicts = []
    def conflict(pipe, expected):
        commands = [command for command, _ in pipe.command_stack]
        linking = any(command[0] == 'SET' and command[1] == included.JOURNAL_KEY
                      and '"completion"' in command[2] for command in commands)
        cached_read = phase == 'cached' and commands == [('PING',)]
        if not conflicts and (linking if phase == 'link' else cached_read):
            key = included.ANCHOR_KEY
            case.ledger.client.set(key, case.ledger.client.get(key))
            conflicts.append(phase)
        original(pipe, expected)
    monkeypatch.setattr(included.IncludedRouterLedger, '_ack', staticmethod(conflict))
    assert run(case) == case.complete
    assert conflicts == [phase] and len(case.calls) == 2
    rows = list(journal(case)['requests'].values())
    assert len(rows) == 2 and sum('completion' in row for row in rows) == 1
    before = json.dumps(journal(case), sort_keys=True)
    assert run(case) == case.complete and len(case.calls) == 2
    assert json.dumps(journal(case), sort_keys=True) == before


@pytest.mark.parametrize('failure', ['lost_connection', 'ack', 'exhausted'])
def test_ambiguous_or_exhausted_local_completion_is_terminal_without_pipeline_rebuild(case, monkeypatch, failure):
    from app.services import included_visual_completion as completion
    original = included.IncludedRouterLedger._ack
    calls = []
    def stop(pipe, expected):
        linking = any(command[0] == 'SET' and command[1] == included.JOURNAL_KEY
                      and '"completion"' in command[2] for command, _ in pipe.command_stack)
        if linking:
            calls.append(1)
            if failure == 'ack': raise ConnectionError('Ambiguous acknowledgment')
            if failure == 'lost_connection': raise WatchError('ConnectionError while watching keys')
            raise WatchError('Watched variable changed.')
        return original(pipe, expected)
    monkeypatch.setattr(included.IncludedRouterLedger, '_ack', staticmethod(stop))
    with pytest.raises(SpendBlocked, match='included_visual_completion_unverified'): run(case)
    assert len(calls) == (8 if failure == 'exhausted' else 1) and len(case.calls) == 2
    from app.services.production_failures import classify_failure, classified_hold_reason
    error = SpendBlocked('included_visual_completion_unverified')
    assert classified_hold_reason({'error': str(error), 'failure_stage': 'visual_qc',
        'failure_classification': classify_failure(error, 'visual_qc')}) == 'review_unverified'
    # Restore only local access. Adopt the already observed target, with no HTTP.
    monkeypatch.setattr(included.IncludedRouterLedger, '_ack', staticmethod(original))
    assert run(case) == case.complete and len(case.calls) == 2
