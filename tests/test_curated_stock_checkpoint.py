"""Outer-only curated pointers retain the actual one-shot recovery contract."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import paid_render_recovery as recovery, studio_state
from test_paid_render_recovery import (  # Existing fixture uses real Redis Lua claims.
    SOURCE, CHILD, GRANDCHILD, case, continuation, _redis_snapshot,
)
from test_studio_repair_retry import _load_retry, HTTPException


@pytest.fixture
def curated(continuation, monkeypatch):
    f = continuation
    f.curated_pointer = {'version':1, 'source_task_id':CHILD,
        'key':f'curated_stock/{CHILD}/'+'c'*64+'.json', 'sha256':'c'*64, 'size':1024}
    f.workprint = {'version':1, 'status':'qa_workprint', 'metadata_sha256':'d'*64}
    f.leaf['qa_workprint'] = deepcopy(f.workprint)
    f.case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(f.leaf))
    def validate(pointer, *, source_job, approved_package):
        if (pointer != f.curated_pointer or source_job.get('task_id') != CHILD
                or source_job.get('qa_workprint') != f.workprint):
            raise ValueError('Binding mismatch')
        assert approved_package == f.original['approved_package']
        assert approved_package['_recovered_generated_media']['version'] == 3
        assert approved_package['_recovered_generated_media']['source_task_id'] == SOURCE
        assert approved_package['_recovered_voice']['source_task_id'] == SOURCE
        return {'source_task_id':CHILD, 'requires_full_qa':True}
    f.validator = Mock(side_effect=validate)
    monkeypatch.setitem(sys.modules, 'app.services.curated_stock',
                        SimpleNamespace(validate_curated_stock_manifest=f.validator))
    return f


def _prepare_curated(f):
    return recovery.prepare_paid_recovery_continuation(
        CHILD, f.pointer, f.work, curated_stock_manifest=f.curated_pointer,
    )


def test_curated_preparation_and_publication_keep_package_hash_assets_and_ancestors(curated):
    f, case = curated, curated.case
    before, puts, reviews = _redis_snapshot(case), list(case.puts), case.review.call_count
    prepared = _prepare_curated(f)
    assert _redis_snapshot(case) == before and case.puts == puts
    assert case.review.call_count == reviews
    assert prepared['curated_stock_manifest'] == f.curated_pointer
    assert prepared['approved_package'] == f.original['approved_package']
    assert prepared['package_sha256'] == f.original['package_sha256']
    assert 'curated_stock_manifest' not in prepared['approved_package']
    assert 'curated_stock_manifest' not in prepared['approved_package']['studio_options']
    assert prepared['new_paid_create_requests'] == prepared['new_tts_requests'] == 0
    assert f.validator.call_count == 1
    assert recovery.publish_paid_recovery_continuation(prepared)['source_task_id'] == CHILD
    assert f.validator.call_count == 2  # Revalidated under WATCH, not trusted from preparation.
    allowed = {studio_state.JOB_PREFIX+CHILD, studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD}
    after = _redis_snapshot(case)
    assert {k:v for k,v in before.items() if k not in allowed} == {k:v for k,v in after.items() if k not in allowed}
    stored = studio_state.get_job(CHILD)
    assert stored['spec'] == f.leaf['spec'] and stored['result'] == f.leaf['result']
    assert stored['qa_workprint'] == f.workprint
    assert 'curated_stock_manifest' not in stored
    dispatch = studio_state.claim_retry_dispatch(CHILD, GRANDCHILD, 'curated-retry-token-long-enough', allow_repair=True)
    assert dispatch['claimed'] and dispatch['checkpoint']['curated_stock_manifest'] == f.curated_pointer
    assert not studio_state.claim_retry_dispatch(CHILD, SOURCE, 'duplicate-curated-token-long-enough', allow_repair=True)['claimed']


def test_legacy_continuation_does_not_import_or_validate_curation(curated):
    f = curated
    prepared = recovery.prepare_paid_recovery_continuation(CHILD, f.pointer, f.work)
    recovery.publish_paid_recovery_continuation(prepared)
    assert 'curated_stock_manifest' not in prepared
    f.validator.assert_not_called()


@pytest.mark.parametrize('result', [None, {}, False, RuntimeError('SECRET Storage response')])
def test_invalid_or_unavailable_curated_manifest_cannot_prepare_or_write(curated, result):
    f, case = curated, curated.case
    f.validator.side_effect = result if isinstance(result, Exception) else None
    f.validator.return_value = result
    before, puts = _redis_snapshot(case), list(case.puts)
    with pytest.raises(recovery.PaidRenderRecoveryError) as error: _prepare_curated(f)
    assert 'SECRET' not in str(error.value)
    assert _redis_snapshot(case) == before and case.puts == puts


@pytest.mark.parametrize('damage', ['pointer_hash','null_pointer','new_workprint','invalid_manifest','unexpected_field'])
def test_curated_publisher_rechecks_live_manifest_and_workprint_without_writes(curated, damage):
    f, case = curated, curated.case
    prepared = _prepare_curated(f)
    if damage == 'pointer_hash': prepared['curated_stock_manifest']['sha256'] = 'e'*64
    if damage == 'null_pointer': prepared['curated_stock_manifest'] = None
    if damage == 'new_workprint':
        leaf = studio_state.get_job(CHILD)
        leaf['qa_workprint']['metadata_sha256'] = 'f'*64
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
    if damage == 'invalid_manifest': f.validator.side_effect = ValueError('Changed manifest')
    if damage == 'unexpected_field': prepared['curated_stock'] = prepared['curated_stock_manifest']
    before = _redis_snapshot(case)
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(prepared)
    assert _redis_snapshot(case) == before


def test_workprint_change_during_preparation_is_rejected(curated, monkeypatch):
    f, case = curated, curated.case
    original_probe = case.runtime._validate_recovered_generated_clip
    def change(*args, **kwargs):
        original_probe(*args, **kwargs)
        leaf = studio_state.get_job(CHILD)
        leaf['qa_workprint']['metadata_sha256'] = 'e'*64
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
    monkeypatch.setattr(case.runtime, '_validate_recovered_generated_clip', change)
    with pytest.raises(recovery.PaidRenderRecoveryError): _prepare_curated(f)
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD)
    assert not studio_state.get_job(CHILD).get('repair_available')


def test_manifest_read_is_inside_watch_and_cannot_race_leaf_workprint(curated):
    f, case = curated, curated.case
    prepared = _prepare_curated(f)
    validate = f.validator.side_effect
    def race(*args, **kwargs):
        result = validate(*args, **kwargs)
        leaf = studio_state.get_job(CHILD)
        leaf['qa_workprint']['metadata_sha256'] = 'e'*64
        case.redis.set(studio_state.JOB_PREFIX+CHILD, json.dumps(leaf))
        return result
    f.validator.side_effect = race
    with pytest.raises(recovery.PaidRenderRecoveryError): recovery.publish_paid_recovery_continuation(prepared)
    assert not case.redis.exists(studio_state.REPAIR_CHECKPOINT_PREFIX+CHILD)
    assert not studio_state.get_job(CHILD).get('repair_available')
    assert case.redis.get(studio_state.REPAIR_CHECKPOINT_CLAIM_PREFIX+SOURCE)


def test_validator_receives_detached_package_source_and_pointer(curated):
    f = curated
    original, leaf, pointer = deepcopy(f.original), deepcopy(f.leaf), deepcopy(f.curated_pointer)
    validate = f.validator.side_effect
    def mutate(*args, **kwargs):
        result = validate(*args, **kwargs)
        args[0].clear()
        kwargs['approved_package'].clear()
        kwargs['source_job'].clear()
        return result
    f.validator.side_effect = mutate
    prepared = _prepare_curated(f)
    assert prepared['curated_stock_manifest'] == pointer
    assert prepared['approved_package'] == original['approved_package']
    assert f.original == original and f.leaf == leaf and f.curated_pointer == pointer


def test_studio_forwards_curated_pointer_only_from_atomically_claimed_outer_checkpoint(curated):
    f = curated
    prepared = _prepare_curated(f)
    record = deepcopy(f.leaf)
    record['spec']['curated_stock_manifest'] = {'malicious':'ignored options input'}
    boundary = _load_retry(record, prepared)
    boundary.retry(CHILD, studio_token='owner')
    assert len(boundary.claimed) == 1 and len(boundary.render.calls) == 1
    args, _task_id = boundary.render.calls[0]
    assert len(args) == 8 and args[-1] == f.curated_pointer
    assert args[5] == f.original['approved_package'] and args[6] == CHILD
    assert args[-1] != record['spec']['curated_stock_manifest']
    assert boundary.created[0][1]['parent_id'] == CHILD
    assert boundary.created[0][0][1]['production_profile_revision'] == f.leaf['spec']['production_profile_revision']


def test_options_or_public_job_field_cannot_enable_curated_worker_argument(curated):
    f = curated
    record = deepcopy(f.leaf)
    record['curated_stock_manifest'] = f.curated_pointer
    record['spec']['curated_stock_manifest'] = f.curated_pointer
    boundary = _load_retry(record)
    boundary.retry(CHILD, studio_token='owner')
    assert len(boundary.render.calls[0][0]) == 7
    assert boundary.render.calls[0][0][5] is None


@pytest.mark.parametrize('invalid', [None, 'pointer', [], True])
def test_malformed_claimed_curated_pointer_fails_closed_without_enqueue(curated, invalid):
    f = curated
    checkpoint = {**deepcopy(f.original), 'source_task_id':CHILD, 'curated_stock_manifest':invalid}
    boundary = _load_retry(deepcopy(f.leaf), checkpoint)
    with pytest.raises(HTTPException) as error: boundary.retry(CHILD, studio_token='owner')
    assert error.value.status_code == 409
    assert len(boundary.claimed) == 1
    assert not boundary.created and not boundary.render.calls and not boundary.restored


def test_curated_dispatch_uncertainty_never_reissues_or_reopens_checkpoint(curated):
    f = curated
    prepared = _prepare_curated(f)
    boundary = _load_retry(deepcopy(f.leaf), prepared, publish_error=TimeoutError('broker response lost'))
    response = boundary.retry(CHILD, studio_token='owner')
    assert response['status_code'] == 303 and len(boundary.render.calls) == 1
    assert len(boundary.render.calls[0][0]) == 8
    assert boundary.restored[0][2] == 'uncertain'
    assert boundary.updated[0][1]['stage'] == 'dispatch_uncertain'
