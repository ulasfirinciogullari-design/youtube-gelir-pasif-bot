import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch


config_stub = types.ModuleType('app.config')
config_stub.settings = SimpleNamespace(redis_url='redis://test')
previous_config_module = sys.modules.get('app.config')
sys.modules['app.config'] = config_stub

redis_stub = types.ModuleType('redis')
redis_stub.Redis = SimpleNamespace(from_url=lambda *_args, **_kwargs: None)
previous_redis_module = sys.modules.get('redis')
sys.modules['redis'] = redis_stub

import app.services.studio_state as studio_state

if previous_config_module is None:
    sys.modules.pop('app.config', None)
else:
    sys.modules['app.config'] = previous_config_module
if previous_redis_module is None:
    sys.modules.pop('redis', None)
else:
    sys.modules['redis'] = previous_redis_module
sys.modules.pop('app.services.studio_state', None)
services_package = sys.modules.get('app.services')
if services_package is not None and getattr(
    services_package,
    'studio_state',
    None,
) is studio_state:
    delattr(services_package, 'studio_state')


class RepairStateRedis:
    """Small behavioral fake for the three atomic repair-state scripts."""

    def __init__(self):
        self.values = {}

    def _patch_job(self, key, **fields):
        raw = self.values.get(key)
        if not raw:
            return
        record = json.loads(raw)
        record.update(fields)
        self.values[key] = json.dumps(record)

    def eval(self, script, numkeys, *values):
        keys = values[:numkeys]
        args = values[numkeys:]
        if script == studio_state._SAVE_REPAIR_CHECKPOINT:
            job_key, checkpoint_key, claim_key = keys
            self.values[checkpoint_key] = args[0]
            self.values.pop(claim_key, None)
            self._patch_job(
                job_key,
                repair_available=True,
                repair_claimed=False,
            )
            return 1
        if script == studio_state._CONSUME_REPAIR_CHECKPOINT:
            job_key, checkpoint_key, claim_key = keys
            raw = self.values.pop(checkpoint_key, None)
            if raw is not None:
                self.values[claim_key] = '1'
            claimed = claim_key in self.values
            self._patch_job(
                job_key,
                repair_available=False,
                repair_claimed=claimed,
            )
            return raw
        if script == studio_state._SYNC_REPAIR_CHECKPOINT_STATE:
            job_key, checkpoint_key, claim_key, dispatch_key = keys
            dispatch = self.values.get(dispatch_key)
            available = checkpoint_key in self.values and not dispatch
            claimed = (
                claim_key in self.values
                or isinstance(dispatch, dict)
                and dispatch.get('mode') == 'repair'
            )
            self._patch_job(
                job_key,
                repair_available=available,
                repair_claimed=claimed,
                retry_claimed=bool(dispatch),
                retry_child_task_id=(
                    dispatch.get('child_task_id')
                    if isinstance(dispatch, dict)
                    else None
                ),
                retry_dispatch_state=(
                    dispatch.get('state')
                    if isinstance(dispatch, dict)
                    else None
                ),
            )
            return 2 if available else 1 if claimed or dispatch else 0
        if script == studio_state._CLAIM_RETRY_DISPATCH:
            (
                job_key,
                checkpoint_key,
                claim_key,
                dispatch_key,
                child_claim_key,
            ) = keys
            record = json.loads(self.values.get(job_key, '{}'))
            if record.get('state') != 'FAILURE':
                return [-2, '']
            existing = self.values.get(dispatch_key)
            if isinstance(existing, dict):
                return [0, existing.get('child_task_id', '')]
            allow_repair = args[4] == '1'
            raw = None
            mode = 'full'
            if allow_repair and checkpoint_key in self.values:
                raw = self.values.pop(checkpoint_key)
                self.values[claim_key] = args[0]
                mode = 'repair'
            elif claim_key in self.values:
                return [0, '']
            self.values[dispatch_key] = {
                'token': args[0],
                'child_task_id': args[1],
                'mode': mode,
                'state': 'reserved',
            }
            self.values[child_claim_key] = {
                'source_task_id': args[6],
                'token': args[0],
            }
            self._patch_job(
                job_key,
                repair_available=False,
                repair_claimed=mode == 'repair',
                retry_claimed=True,
                retry_child_task_id=args[1],
                retry_dispatch_state='reserved',
            )
            return [1, raw] if mode == 'repair' else [2, '']
        if script == studio_state._MARK_RETRY_DISPATCH:
            job_key, dispatch_key = keys
            dispatch = self.values.get(dispatch_key)
            if not isinstance(dispatch, dict) or dispatch.get('token') != args[0]:
                return 0
            dispatch['state'] = args[1]
            self._patch_job(
                job_key,
                retry_claimed=True,
                retry_dispatch_state=args[1],
            )
            return 1
        if script == studio_state._ACQUIRE_RETRY_CHILD_EXECUTION:
            child_claim_key, execution_key = keys
            claim = self.values.get(child_claim_key)
            if (
                not isinstance(claim, dict)
                or claim.get('source_task_id') != args[1]
                or execution_key in self.values
            ):
                return 0
            self.values[execution_key] = claim['token']
            return 1
        raise AssertionError('unexpected Redis script')


def test_mark_failure_preserves_last_code_owned_stage():
    with (
        patch.object(
            studio_state,
            'get_job',
            return_value={'stage': 'render'},
        ),
        patch.object(
            studio_state,
            'update_job',
            return_value={'state': 'FAILURE'},
        ) as update_job,
    ):
        studio_state.mark_failure('job-id', RuntimeError('sensitive detail'))

    update_job.assert_called_once_with(
        'job-id',
        state='FAILURE',
        stage='failed',
        failure_stage='render',
        progress=100,
        message='Görev başarısız oldu.',
        error='sensitive detail',
    )


def test_mark_failure_does_not_persist_untrusted_stage_text():
    with (
        patch.object(
            studio_state,
            'get_job',
            return_value={'stage': 'render secret=value'},
        ),
        patch.object(
            studio_state,
            'update_job',
            return_value={'state': 'FAILURE'},
        ) as update_job,
    ):
        studio_state.mark_failure('job-id', 'failed')

    assert update_job.call_args.kwargs['failure_stage'] == 'unknown'


def test_mark_failure_keeps_original_failure_stage_on_resync():
    with (
        patch.object(
            studio_state,
            'get_job',
            return_value={
                'stage': 'failed',
                'failure_stage': 'final_visual_qc',
            },
        ),
        patch.object(
            studio_state,
            'update_job',
            return_value={'state': 'FAILURE'},
        ) as update_job,
    ):
        studio_state.mark_failure('job-id', 'failed again')

    assert update_job.call_args.kwargs['failure_stage'] == 'final_visual_qc'


def test_repair_checkpoint_is_private_and_atomically_consumed(monkeypatch):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    checkpoint = {
        'version': 1,
        'source_task_id': task_id,
        'package_sha256': 'a' * 64,
        'approved_package': {'title': 'Locked storyboard'},
    }

    studio_state.save_repair_checkpoint(task_id, checkpoint)

    assert all(
        not key.startswith(studio_state.JOB_PREFIX)
        for key in client.values
    )
    assert studio_state.consume_repair_checkpoint(task_id) == checkpoint
    assert studio_state.consume_repair_checkpoint(task_id) is None


def test_checkpoint_sync_recovers_missing_flag_without_opening_payload(
    monkeypatch,
):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    job_key = studio_state._job_key(task_id)
    client.values[job_key] = json.dumps({
        'task_id': task_id,
        'state': 'FAILURE',
        'repair_available': False,
    })
    checkpoint = {
        'version': 1,
        'source_task_id': task_id,
        'package_sha256': 'a' * 64,
        'approved_package': {'title': 'Never expose this package'},
    }

    studio_state.save_repair_checkpoint(task_id, checkpoint)
    # Simulate the late create_job/mark_failure write seen in production.
    client._patch_job(
        job_key,
        repair_available=False,
        repair_claimed=False,
    )
    state = studio_state.sync_repair_checkpoint_state(task_id)
    public_job = json.loads(client.values[job_key])

    assert state == {'repair_available': True, 'repair_claimed': False}
    assert public_job['repair_available'] is True
    assert 'Never expose this package' not in client.values[job_key]
    assert "redis.call('GET', KEYS[2])" not in (
        studio_state._SYNC_REPAIR_CHECKPOINT_STATE
    )
    assert "redis.call('DEL', KEYS[2])" not in (
        studio_state._SYNC_REPAIR_CHECKPOINT_STATE
    )


def test_consumed_checkpoint_claim_survives_late_stale_job_write(monkeypatch):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    job_key = studio_state._job_key(task_id)
    client.values[job_key] = json.dumps({
        'task_id': task_id,
        'state': 'FAILURE',
        'repair_available': False,
        'repair_claimed': False,
    })
    checkpoint = {
        'version': 1,
        'source_task_id': task_id,
        'approved_package': {'title': 'Locked'},
    }
    studio_state.save_repair_checkpoint(task_id, checkpoint)

    assert studio_state.consume_repair_checkpoint(task_id) == checkpoint
    # A concurrent stale save must not resurrect the button or erase the
    # at-most-once claim; the non-secret tombstone is authoritative.
    client._patch_job(
        job_key,
        repair_available=True,
        repair_claimed=False,
    )

    assert studio_state.sync_repair_checkpoint_state(task_id) == {
        'repair_available': False,
        'repair_claimed': True,
    }
    public_job = json.loads(client.values[job_key])
    assert public_job['repair_available'] is False
    assert public_job['repair_claimed'] is True
    assert studio_state.consume_repair_checkpoint(task_id) is None


def test_missing_checkpoint_and_claim_clear_stale_available_flag(monkeypatch):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    job_key = studio_state._job_key(task_id)
    client.values[job_key] = json.dumps({
        'task_id': task_id,
        'state': 'FAILURE',
        'repair_available': True,
        'repair_claimed': False,
    })

    assert studio_state.sync_repair_checkpoint_state(task_id) == {
        'repair_available': False,
        'repair_claimed': False,
    }
    assert json.loads(client.values[job_key])['repair_available'] is False


def test_retry_dispatch_atomically_claims_checkpoint_and_execution_once(
    monkeypatch,
):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    source_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    child_id = '11111111-1111-4111-8111-111111111111'
    token = 't' * 32
    client.values[studio_state._job_key(source_id)] = json.dumps({
        'task_id': source_id,
        'state': 'FAILURE',
        'repair_available': False,
    })
    checkpoint = {
        'version': 1,
        'source_task_id': source_id,
        'approved_package': {'title': 'Locked'},
    }
    studio_state.save_repair_checkpoint(source_id, checkpoint)

    first = studio_state.claim_retry_dispatch(
        source_id,
        child_id,
        token,
        allow_repair=True,
    )
    second = studio_state.claim_retry_dispatch(
        source_id,
        '22222222-2222-4222-8222-222222222222',
        'u' * 32,
        allow_repair=True,
    )

    assert first['claimed'] is True
    assert first['mode'] == 'repair'
    assert first['checkpoint'] == checkpoint
    assert second == {'claimed': False, 'child_task_id': child_id}
    assert studio_state.mark_retry_dispatch(
        source_id,
        'wrong-token-value',
        'dispatched',
    ) is False
    assert studio_state.mark_retry_dispatch(
        source_id,
        token,
        'uncertain',
    ) is True
    assert studio_state.acquire_retry_child_execution(child_id, source_id)
    assert not studio_state.acquire_retry_child_execution(child_id, source_id)


def test_full_retry_dispatch_is_single_claim_and_requires_failed_source(
    monkeypatch,
):
    client = RepairStateRedis()
    monkeypatch.setattr(studio_state, '_client', lambda: client)
    source_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    child_id = '11111111-1111-4111-8111-111111111111'
    job_key = studio_state._job_key(source_id)
    client.values[job_key] = json.dumps({
        'task_id': source_id,
        'state': 'FAILURE',
    })

    first = studio_state.claim_retry_dispatch(
        source_id,
        child_id,
        't' * 32,
        allow_repair=True,
    )
    second = studio_state.claim_retry_dispatch(
        source_id,
        '22222222-2222-4222-8222-222222222222',
        'u' * 32,
        allow_repair=True,
    )

    assert first['mode'] == 'full'
    assert second == {'claimed': False, 'child_task_id': child_id}

    other_source = '33333333-3333-4333-8333-333333333333'
    client.values[studio_state._job_key(other_source)] = json.dumps({
        'task_id': other_source,
        'state': 'PROGRESS',
    })
    try:
        studio_state.claim_retry_dispatch(
            other_source,
            '44444444-4444-4444-8444-444444444444',
            'v' * 32,
            allow_repair=True,
        )
    except ValueError as exc:
        assert 'failed job' in str(exc)
    else:
        raise AssertionError('nonfailed job reserved a retry')


def test_repair_checkpoint_rejects_foreign_source(monkeypatch):
    monkeypatch.setattr(
        studio_state,
        '_client',
        lambda: SimpleNamespace(eval=lambda *_args, **_kwargs: None),
    )
    task_id = 'caaf8ccf-352a-4fea-9e77-5028d683c659'
    checkpoint = {
        'version': 1,
        'source_task_id': '00000000-0000-0000-0000-000000000000',
        'approved_package': {'title': 'Foreign'},
    }

    try:
        studio_state.save_repair_checkpoint(task_id, checkpoint)
    except ValueError as exc:
        assert 'invalid' in str(exc)
    else:
        raise AssertionError('foreign repair checkpoint was accepted')
