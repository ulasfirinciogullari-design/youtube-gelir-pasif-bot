"""Worker lifecycle and actual Celery route selection, without live queues."""
import ast
from pathlib import Path
import signal
from types import SimpleNamespace

import pytest
from celery import Celery

from app import worker_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    tree = ast.parse((ROOT / 'app/celery_app.py').read_text())
    call = next(node.value for node in tree.body if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == 'update')
    return {item.arg: ast.literal_eval(item.value) for item in call.keywords}


def test_real_celery_routes_keep_control_available_and_media_on_existing_queue():
    app = Celery('isolated-test', broker='memory://')
    app.conf.update(configuration())
    for name in ('production_tick', 'observe_youtube_metrics', 'observe_youtube_analytics'):
        route = app.amqp.router.route({}, 'app.production_tasks.' + name)
        assert route['queue'].name == 'production_control'
    for name in ('app.tasks.run_video_pipeline', 'app.publish_tasks.publish_video',
                 'app.production_tasks.prepare_series_batch', 'app.production_tasks.finalize_retained_child'):
        assert app.amqp.router.route({}, name)['queue'].name == 'celery'
    render, control = runtime.commands()
    assert '--concurrency=2' in render and '--queues=celery' in render and '--hostname=render@%h' in render
    assert '--concurrency=1' in control and '--queues=production_control' in control and '--hostname=control@%h' in control


@pytest.mark.parametrize('failure', ['none', 'control_exit', 'render_exit', 'second_spawn'])
def test_parent_warmly_stops_other_worker_and_never_restarts_or_requeues_it(monkeypatch, failure):
    handlers, children, stopped, waited, spawned = {}, [], [], [], []
    def register(sig, callback):
        old = handlers.get(sig, signal.SIG_DFL);handlers[sig] = callback;return old
    def spawn(command, **options):
        spawned.append(command);assert options == {'start_new_session': True}
        if failure == 'second_spawn' and len(spawned) == 2:
            raise OSError('Synthetic process failure')
        child = SimpleNamespace(pid=100 + len(children), exitcode=None)
        child.poll = lambda: child.exitcode
        child.wait = lambda: waited.append(child.pid)
        children.append(child);return child
    def terminate(pid, sig):
        assert sig == signal.SIGTERM;stopped.append(pid)
        next(child for child in children if child.pid == pid).exitcode = 0
    def tick(_):
        if failure == 'none':
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            handlers[signal.SIGINT](signal.SIGINT, None)
        else:
            children[1 if failure == 'control_exit' else 0].exitcode = 1
    monkeypatch.setattr(runtime.signal, 'signal', register)
    monkeypatch.setattr(runtime.subprocess, 'Popen', spawn)
    monkeypatch.setattr(runtime.os, 'killpg', terminate)
    monkeypatch.setattr(runtime.time, 'sleep', tick)
    assert runtime.run() == (0 if failure == 'none' else 1)
    assert len(spawned) == 2 and waited == [child.pid for child in children]
    assert len(stopped) == len(set(stopped))
    assert stopped == ([100, 101] if failure == 'none' else [100] if failure != 'render_exit' else [101])
    assert handlers == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}


def test_control_tick_is_bounded_and_has_no_automatic_replay():
    tree = ast.parse((ROOT / 'app/production_tasks.py').read_text())
    tick = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'production_tick')
    options = {item.arg: ast.literal_eval(item.value) for item in tick.decorator_list[0].keywords}
    assert options['acks_late'] is False and options['autoretry_for'] == () and options['max_retries'] == 0
    assert options['soft_time_limit'] < options['time_limit'] <= 120
    assert 'python -m app.worker_runtime' in (ROOT / 'Dockerfile').read_text()
    assert 'command: python -m app.worker_runtime' in (ROOT / 'docker-compose.yml').read_text()


def test_control_task_runs_while_both_actual_celery_render_slots_are_occupied():
    from threading import Event
    from celery.contrib.testing.worker import start_worker
    app = Celery('isolated-queues-integration', broker='memory://', backend='cache+memory://')
    app.conf.update(configuration())
    app.conf.beat_schedule = {}
    started = [Event(), Event()];release = Event();checked = Event()
    @app.task(name='app.tasks.run_video_pipeline')
    def render(index):
        started[index].set()
        return release.wait(10)
    @app.task(name='app.production_tasks.production_tick')
    def control():
        checked.set();return 'checked'
    with start_worker(app, pool='threads', concurrency=2, queues=['celery'],
                      perform_ping_check=False, shutdown_timeout=15):
        with start_worker(app, pool='threads', concurrency=1, queues=['production_control'],
                          perform_ping_check=False, shutdown_timeout=15):
            try:
                render.delay(0);render.delay(1)
                assert all(event.wait(5) for event in started)
                control.delay()
                assert checked.wait(5) and not release.is_set()
            finally:
                release.set()
    app.close()
