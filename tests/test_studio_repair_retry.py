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
    def __init__(self):
        self.calls = []

    def delay(self, *args):
        self.calls.append(args)
        return DelayedTask('child-task-id')


def _load_retry(record, checkpoint=None):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == 'studio_retry'
    )
    function = copy.deepcopy(function)
    function.decorator_list = []
    render = DelayRecorder()
    plan = DelayRecorder()
    created = []
    updated = []
    consumed = []
    restored = []
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
        'consume_repair_checkpoint': lambda task_id: (
            consumed.append(task_id) or checkpoint
        ),
        'save_repair_checkpoint': lambda task_id, value: restored.append(
            (task_id, value)
        ),
        'run_video_pipeline': render,
        'plan_video_pipeline': plan,
        'create_job': lambda *args, **kwargs: created.append(
            (args, kwargs)
        ),
        'update_job': lambda *args, **kwargs: updated.append(
            (args, kwargs)
        ),
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
        consumed=consumed,
        restored=restored,
    )


def _record(*, repair_available=False):
    return {
        'kind': 'render',
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


def test_failed_job_without_checkpoint_keeps_normal_retry_and_target():
    record = _record(repair_available=False)
    original_spec = dict(record['spec'])
    boundary = _load_retry(record)

    response = boundary.retry('source-task-id', studio_token='token')

    assert response['status_code'] == 303
    assert len(boundary.render.calls) == 1
    call = boundary.render.calls[0]
    assert call[3] == 'immutable-channel-id'
    assert call[5] is None
    assert boundary.consumed == []
    assert record['spec'] == original_spec
    child_spec = boundary.created[0][0][1]
    assert child_spec['channel_id'] == 'immutable-channel-id'
    assert child_spec['workflow'] == 'auto'


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

    assert boundary.consumed == ['source-task-id']
    assert len(boundary.render.calls) == 1
    call = boundary.render.calls[0]
    assert call[3] == 'immutable-channel-id'
    assert call[5] is approved_package
    assert record['spec'] == original_spec
    child_spec = boundary.created[0][0][1]
    assert child_spec['channel_id'] == 'immutable-channel-id'
    assert child_spec['workflow'] == 'scene_repair'
    assert boundary.updated == [(
        ('source-task-id',),
        {'repair_available': False, 'repair_claimed': True},
    )]


def test_claimed_repair_never_falls_back_to_a_second_full_retry():
    record = _record(repair_available=True)
    boundary = _load_retry(record, checkpoint=None)

    try:
        boundary.retry('source-task-id', studio_token='token')
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError('consumed repair was queued a second time')

    assert boundary.render.calls == []
