"""Dedicated server worker for an admitted retained final, with no paid path."""
from pathlib import Path
import re

from app.services import retained_production_admission as admission
from app.services import retained_delivery_dispatch as delivery
from app.services import retained_publication_plan as publication
from app.services import retained_publication_transport as transport
from app.services import retained_delivery_completion as completion
from app.services import storage, studio_state

STOP_KEY = admission.PREFIX + ':stopped'


def _completed(client, task_id, manifest_sha256):
    receipt = completion.read_retained_publication_history(client, task_id, manifest_sha256)
    return {'status': 'complete', 'task_id': task_id, 'video_id': receipt['video_id'],
        'receipt_sha256': admission._hash(receipt), 'next_production_authorized': False}


def _stop(execution, phase):
    state = delivery._execution(execution)
    record = {'version': 1, 'kind': 'retained_delivery_stopped',
        'child_id': state['manifest']['child_id'], 'manifest_sha256': admission._hash(state['manifest']),
        'execution_sha256': admission._hash(state['execution']), 'phase': phase,
        'observed_at': delivery._now(), 'automatic_retry_permitted': False}
    with state['client'].pipeline() as pipe:
        admission._require(delivery._stored(pipe, admission.MANIFEST_KEY) == state['manifest']
            and delivery._stored(pipe, delivery.EXECUTION_KEY) == state['execution'])
        pipe.watch(STOP_KEY)
        admission._require(pipe.exists(STOP_KEY) == 0)
        pipe.multi()
        pipe.set(STOP_KEY, admission._raw(record).decode(), nx=True)
        admission._ack(pipe.execute(), [True])
    with state['client'].pipeline() as pipe:
        admission._require(delivery._stored(pipe, STOP_KEY) == record)
        admission._read_ack(pipe)


def run_retained_delivery(task_id, manifest_sha256, *, work_root=Path('/tmp/youtube_retained_delivery')):
    """Never call ordinary rendering, retry, funding or scheduler functions."""
    execution, client, phase = None, None, 'admission'
    try:
        admission._uuid(task_id)
        admission._require(type(manifest_sha256) is str and re.fullmatch('[0-9a-f]{64}', manifest_sha256))
        client = studio_state._client()
        execution = delivery.acquire_retained_execution(client, task_id, manifest_sha256)
        phase = 'private_files'
        root = Path(work_root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        publication.render._directory(root)
        work = root / task_id
        work.mkdir(mode=0o700, exist_ok=False)
        s3 = storage._client(single_attempt=True)
        prepared = publication.prepare_retained_publication(execution, s3,
            bucket=storage.settings.bucket, workdir=work)
        phase = 'publication'
        transport.publish_retained_final(prepared)
        phase = 'completion'
        completion.complete_retained_publication(prepared)
        return _completed(client, task_id, manifest_sha256)
    except Exception:
        # A lost completion ACK or a duplicate broker delivery may only read an
        # already committed public result; it must never repeat a media effect.
        if client is not None:
            try:
                return _completed(client, task_id, manifest_sha256)
            except Exception:
                pass
        if execution is not None:
            try:
                _stop(execution, phase)
            except Exception:
                pass
        return {'status': 'stopped_unverified', 'phase': phase, 'automatic_retry_permitted': False,
                'next_production_authorized': False}
