import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tasks.py'


class Ignore(Exception):
    pass


def _load_guard(acquire):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_guard_retry_child_execution'
    )
    namespace = {
        'acquire_retry_child_execution': acquire,
        'render_cancellation_requested': lambda _task_id: False,
        'Ignore': Ignore,
    }
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(SOURCE_PATH),
            'exec',
        ),
        namespace,
    )
    return namespace['_guard_retry_child_execution']


def test_initial_duplicate_retry_delivery_is_ignored_before_pipeline(monkeypatch):
    task = SimpleNamespace(request=SimpleNamespace(retries=0))
    guard = _load_guard(lambda *_args: False)

    with pytest.raises(Ignore):
        guard(
            task,
            '11111111-1111-4111-8111-111111111111',
            '22222222-2222-4222-8222-222222222222',
        )


def test_celery_retry_delivery_bypasses_initial_execution_claim(monkeypatch):
    task = SimpleNamespace(request=SimpleNamespace(retries=1))

    def unexpected_acquire(*_args):
        raise AssertionError('Celery autoretry must not reacquire initial lock')

    guard = _load_guard(unexpected_acquire)

    guard(
        task,
        '11111111-1111-4111-8111-111111111111',
        '22222222-2222-4222-8222-222222222222',
    )


@pytest.mark.parametrize(
    'pipeline_name',
    ['plan_video_pipeline', 'run_video_pipeline'],
)
def test_plan_and_render_enter_guard_before_pipeline_work(pipeline_name):
    tree = ast.parse(SOURCE_PATH.read_text(encoding='utf-8'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == pipeline_name
    )
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == '_guard_retry_child_execution'
    ]

    assert len(calls) == 1
    guard_line = calls[0].lineno
    first_job_update = next(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'update_job'
    )
    assert guard_line < first_job_update
