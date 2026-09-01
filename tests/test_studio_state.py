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
    class FakeRedis:
        def __init__(self):
            self.values = {}

        def setex(self, key, _ttl, value):
            self.values[key] = value

        def getdel(self, key):
            return self.values.pop(key, None)

    client = FakeRedis()
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


def test_repair_checkpoint_rejects_foreign_source(monkeypatch):
    monkeypatch.setattr(
        studio_state,
        '_client',
        lambda: SimpleNamespace(setex=lambda *_args, **_kwargs: None),
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
