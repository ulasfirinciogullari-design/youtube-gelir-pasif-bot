from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

from app.services import saved_voice_review as service, voice_candidate_recovery as recovery
from app.services import production_included_router as included
from test_voice_candidate_recovery import stored_candidate, SOURCE_ID, CHILD_ID


@pytest.mark.parametrize('acknowledged', [False, True])
def test_real_checkpoint_survives_review_failure_and_never_grants_approval(stored_candidate, monkeypatch, acknowledged):
    candidate = stored_candidate
    monkeypatch.setattr(included, 'enabled', lambda: True)
    loaded = recovery.load_voice_retry_candidate(SOURCE_ID, CHILD_ID, candidate.pointer, candidate.work)
    before = deepcopy(loaded['package'])
    job = {'task_id': CHILD_ID, 'state': 'PROGRESS', 'stage': 'director_qc'}
    def update(task, **fields):
        assert task == CHILD_ID
        if acknowledged:job.update(fields)
    monkeypatch.setitem(sys.modules, 'app.services.studio_state', SimpleNamespace(update_job=update, get_job=lambda task: deepcopy(job)))
    if not acknowledged:
        with pytest.raises(RuntimeError, match='acknowledgement'):
            service.review_options(CHILD_ID, loaded['package'], loaded['voice_result'])
        assert 'audio_candidate_checkpoint' not in job
        return
    assert service.review_options(CHILD_ID, loaded['package'], loaded['voice_result']) == {'immutable_stock_routes': True}
    assert loaded['package'] == before
    pointer = job['audio_candidate_checkpoint']
    assert pointer['audio_sha256'] == candidate.pointer['audio_sha256']
    assert pointer['requires_full_qa'] is True and pointer['qa_approved'] is False
    metadata = json.loads(candidate.objects[pointer['metadata_key']])
    assert metadata['source_task_id'] == CHILD_ID
    from app.services.audio_checkpoint import _candidate_package
    assert metadata['package'] == _candidate_package(before) and metadata['status'] == 'unapproved_candidate'
    assert candidate.objects[pointer['audio_key']] == candidate.audio
    assert job['stage'] == 'director_qc' and 'voice_candidate_reuse' not in job


def test_disabled_existing_route_does_not_change_checkpoint_or_review_mode(monkeypatch):
    monkeypatch.setattr(included, 'enabled', lambda: False)
    assert service.review_options('no-task', None, None) == {}
