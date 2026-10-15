"""Run bounded production and control workers in one managed server service.

Two render slots retain their existing concurrency. A separate one-slot
worker consumes scheduler/observation jobs even while both render slots are
occupied. The platform owns restart policy; this parent never replays a task.
"""
import os
import signal
import subprocess
import sys
import time


def commands():
    common = [sys.executable, '-m', 'celery', '-A', 'app.celery_app:celery',
              'worker', '--loglevel=INFO']
    return [common + ['--concurrency=2', '--queues=celery', '--hostname=render@%h'],
            common + ['--concurrency=1', '--queues=production_control', '--hostname=control@%h']]


def run():
    children = []
    stopping = False
    stopped = set()

    def stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.pid not in stopped and child.poll() is None:
                try:
                    # A warm shutdown lets Celery finish its current work;
                    # repeated parent signals cannot become a cold shutdown.
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                stopped.add(child.pid)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    failed = False
    try:
        for command in commands():
            if stopping:
                break
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stopping:
            if any(child.poll() is not None for child in children):
                failed = True
                stop()
                break
            time.sleep(.25)
    except Exception:
        failed = True
        stop()
    finally:
        stop()
        for child in children:
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(run())
