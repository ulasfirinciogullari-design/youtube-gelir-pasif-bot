import ast
import copy
from pathlib import Path
from types import SimpleNamespace


SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'studio.py'


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class DelayedTask:
    def __init__(self, task_id):
        self.id = task_id


class DelayRecorder:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def apply_async(self, *, args, task_id):
        self.calls.append((tuple(args), task_id))
        if self.error is not None:
            raise self.error
        return DelayedTask(task_id)


def _load_retry(
    record,
    checkpoint=None,
    *,
    sync_error=None,
    publish_error=None,
):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == 'studio_retry'
    )
    function = copy.deepcopy(function)
    function.decorator_list = []
    render = DelayRecorder(publish_error)
    plan = DelayRecorder(publish_error)
    created = []
    updated = []
    claimed = []
    restored = []
    checkpoint_box = {'value': checkpoint}

    def claim_retry_dispatch(
        task_id,
        child_task_id,
        token,
        *,
        allow_repair,
    ):
        if sync_error is not None:
            raise sync_error
        if record.get('state') != 'FAILURE':
            raise ValueError('retry source must be a failed job')
        claimed.append((task_id, child_task_id, token, allow_repair))
        if record.get('repair_claimed') or record.get('retry_claimed'):
            return {
                'claimed': False,
                'child_task_id': record.get('retry_child_task_id'),
            }
        value = checkpoint_box['value'] if allow_repair else None
        checkpoint_box['value'] = None
        return {
            'claimed': True,
            'mode': 'repair' if value is not None else 'full',
            'child_task_id': child_task_id,
            'checkpoint': value,
        }
    namespace = {
        'Cookie': lambda **_kwargs: None,
        'COOKIE_NAME': 'studio',
        'HTTPException': HTTPException,
        'RedirectResponse': lambda url, status_code: {
            'url': url,
            'status_code': status_code,
        },
        '_require_auth': lambda _token: None,
        'get_job': lambda _task_id: record,
        'claim_retry_dispatch': claim_retry_dispatch,
        'mark_retry_dispatch': lambda *args: restored.append(args) or True,
        'run_video_pipeline': render,
        'plan_video_pipeline': plan,
        'create_job': lambda *args, **kwargs: created.append(
            (args, kwargs)
        ),
        'update_job': lambda *args, **kwargs: updated.append(
            (args, kwargs)
        ),
        'uuid4': lambda: '11111111-1111-4111-8111-111111111111',
        'secrets': SimpleNamespace(token_urlsafe=lambda _size: 't' * 32),
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return SimpleNamespace(
        retry=namespace['studio_retry'],
        render=render,
        plan=plan,
        created=created,
        updated=updated,
        claimed=claimed,
        restored=restored,
    )


def _record(*, repair_available=False):
    return {
        'kind': 'render',
        'state': 'FAILURE',
        'repair_available': repair_available,
        'spec': {
            'topic': 'Locked topic',
            'duration_minutes': 0.5,
            'language': 'tr',
            'channel_id': 'immutable-channel-id',
            'mode': 'preview',
            'workflow': 'auto',
            'visual_mix': 'ai_first',
        },
    }


def _load_sync_job(record, repair_state=None, *, sync_error=None):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_sync_job'
    )

    class FailedResult:
        state = 'FAILURE'
        result = RuntimeError('final visual rejection')
        info = None

    def sync_repair_checkpoint_state(_task_id):
        if sync_error is not None:
            raise sync_error
        return dict(repair_state or {
            'repair_available': False,
            'repair_claimed': False,
        })

    namespace = {
        'AsyncResult': lambda *_args, **_kwargs: FailedResult(),
        'celery': object(),
        'get_job': lambda _task_id: record,
        'mark_failure': lambda _task_id, error: {
            **record,
            'state': 'FAILURE',
            'error': error,
        },
        'mark_success': lambda *_args, **_kwargs: record,
        'sync_repair_checkpoint_state': sync_repair_checkpoint_state,
        'update_job': lambda *_args, **_kwargs: record,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_sync_job']


def test_failed_job_without_checkpoint_keeps_normal_retry_and_target():
    record = _record(repair_available=False)
    original_spec = dict(record['spec'])
    boundary = _load_retry(record)

    response = boundary.retry('source-task-id', studio_token='token')

    assert response['status_code'] == 303
    assert len(boundary.render.calls) == 1
    call, child_task_id = boundary.render.calls[0]
    assert call[3] == 'immutable-channel-id'
    assert call[5] is None
    assert call[6] == 'source-task-id'
    assert child_task_id == '11111111-1111-4111-8111-111111111111'
    assert len(boundary.claimed) == 1
    assert record['spec'] == original_spec
    child_spec = boundary.created[0][0][1]
    assert child_spec['channel_id'] == 'immutable-channel-id'
    assert child_spec['workflow'] == 'auto'


def test_failure_sync_recovers_real_checkpoint_flag_after_late_job_write():
    record = {
        **_record(repair_available=False),
        'state': 'FAILURE',
        'task_id': 'source-task-id',
    }
    sync = _load_sync_job(
        record,
        {
            'repair_available': True,
            'repair_claimed': False,
        },
    )

    result = sync('source-task-id')

    assert result['repair_available'] is True
    assert result['repair_claimed'] is False


def test_failure_sync_never_exposes_stale_true_when_probe_fails():
    record = {
        **_record(repair_available=True),
        'state': 'FAILURE',
        'task_id': 'source-task-id',
    }
    sync = _load_sync_job(
        record,
        sync_error=RuntimeError('redis unavailable'),
    )

    result = sync('source-task-id')

    assert result['repair_available'] is False


def test_repair_retry_claims_exact_package_once_and_keeps_target_snapshot():
    record = _record(repair_available=True)
    original_spec = dict(record['spec'])
    approved_package = {
        'title': 'Exact locked storyboard',
        '_recovered_generated_media': {'version': 2},
        '_recovered_voice': {'version': 1},
    }
    checkpoint = {
        'version': 1,
        'source_task_id': 'source-task-id',
        'approved_package': approved_package,
    }
    boundary = _load_retry(record, checkpoint)

    boundary.retry('source-task-id', studio_token='token')

    assert boundary.claimed[0][0] == 'source-task-id'
    assert len(boundary.render.calls) == 1
    call, child_task_id = boundary.render.calls[0]
    assert call[3] == 'immutable-channel-id'
    assert call[5] is approved_package
    assert call[6] == 'source-task-id'
    assert child_task_id == '11111111-1111-4111-8111-111111111111'
    assert record['spec'] == original_spec
    child_spec = boundary.created[0][0][1]
    assert child_spec['channel_id'] == 'immutable-channel-id'
    assert child_spec['workflow'] == 'scene_repair'
    assert boundary.updated == []
    assert boundary.restored[-1][-1] == 'dispatched'


def test_checkpoint_presence_recovers_missing_public_flag_before_retry():
    record = _record(repair_available=False)
    checkpoint = {
        'version': 1,
        'source_task_id': 'source-task-id',
        'approved_package': {'title': 'Recovered exact package'},
    }
    boundary = _load_retry(record, checkpoint)

    boundary.retry('source-task-id', studio_token='token')

    assert boundary.claimed[0][0] == 'source-task-id'
    assert boundary.render.calls[0][0][5] is checkpoint['approved_package']
    assert boundary.created[0][0][1]['workflow'] == 'scene_repair'


def test_claimed_repair_never_falls_back_to_a_second_full_retry():
    record = _record(repair_available=False)
    record['repair_claimed'] = True
    record['retry_claimed'] = True
    record['retry_child_task_id'] = '22222222-2222-4222-8222-222222222222'
    boundary = _load_retry(record, checkpoint=None)

    response = boundary.retry('source-task-id', studio_token='token')

    assert response['status_code'] == 303
    assert response['url'].endswith(record['retry_child_task_id'])
    assert boundary.render.calls == []


def test_unprovable_repair_state_fails_closed_before_paid_retry():
    record = _record(repair_available=True)
    boundary = _load_retry(
        record,
        checkpoint=None,
        sync_error=RuntimeError('redis unavailable'),
    )

    try:
        boundary.retry('source-task-id', studio_token='token')
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError('unverified state started a paid retry')

    assert boundary.render.calls == []


def test_ambiguous_broker_error_preserves_claim_and_deterministic_child():
    record = _record(repair_available=True)
    checkpoint = {
        'version': 1,
        'source_task_id': 'source-task-id',
        'approved_package': {'title': 'Locked repair'},
    }
    boundary = _load_retry(
        record,
        checkpoint,
        publish_error=TimeoutError('broker response timed out'),
    )

    response = boundary.retry('source-task-id', studio_token='token')

    assert response['status_code'] == 303
    assert response['url'].endswith(
        '11111111-1111-4111-8111-111111111111'
    )
    assert len(boundary.render.calls) == 1
    assert boundary.restored[-1][-1] == 'uncertain'
    assert boundary.updated[-1][1]['stage'] == 'dispatch_uncertain'


def test_nonfailed_source_cannot_reserve_or_publish_retry():
    record = _record(repair_available=False)
    record['state'] = 'PROGRESS'
    boundary = _load_retry(record)

    try:
        boundary.retry('source-task-id', studio_token='token')
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError('nonfailed source started a retry')

    assert boundary.render.calls == []
