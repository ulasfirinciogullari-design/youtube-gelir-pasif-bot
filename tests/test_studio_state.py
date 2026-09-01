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
