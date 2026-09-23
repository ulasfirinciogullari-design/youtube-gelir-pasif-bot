"""Close an observed removed worker's retained completion, without replaying it.

The private operator supplies an authenticated deployment response and its
task-received log. This is never inferred from elapsed time or a missing ping.
Ordinary completion admission still verifies all voice, media and owner state.
"""
from datetime import datetime, timezone
import hashlib
import re

from billiard.exceptions import WorkerLostError
from redis.exceptions import WatchError

from app.services import content_plan as plan, studio_state as jobs
from app.services.production_failures import classify_failure

PREFIX = plan.PREFIX + 'removed_completion:v1:'
ERROR = 'Saved completion interrupted by a confirmed removed production worker.'


def eligible(source):
    return (source.get('error') == ERROR and source.get('failure_stage') == 'visual_qc'
            and source.get('failure_observer') == 'authenticated_railway_completion_removal'
            and source.get('failure_classification') == classify_failure(WorkerLostError(ERROR), 'visual_qc'))


def proof(client, source):
    from app.services.content_plan_research_resume import fingerprint
    row = plan._object(client.get(PREFIX + source['task_id']))
    plan._require(eligible(source) and row.get('version') == 1
        and row.get('source_fingerprint') == fingerprint(source)
        and row.get('task_id') == source['task_id'] and row.get('parent_id') == source['parent_id']
        and row.get('publish_eligible') is False and row.get('retry_dispatched') is False
        and client.pttl(PREFIX + source['task_id']) == -1)
    return row


def record_removed(task, observation, *, client=None):
    """Operator-only: record positive platform/log evidence, never submit a job."""
    from app.services import content_plan_retained_completion as completion
    from app.services.content_plan_research_resume import fingerprint
    from app.services.production_worker_execution import _identity
    from app.services.youtube_publish_state import UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX
    client = client or plan._client()
    task = plan._id(task); key = jobs.JOB_PREFIX + task
    current = _identity()
    plan._require(type(observation) is dict and set(observation) == {
        'source', 'project_id', 'environment_id', 'service_id', 'deployment_id', 'git_sha',
        'status', 'observed_at', 'deployment_response_sha256', 'logs_sha256', 'received_at', 'received_line'})
    plan._require(observation['source'] == 'authenticated_railway_deployment_and_logs'
        and observation['status'] == 'REMOVED' and observation['deployment_id'] != current['deployment_id']
        and all(observation[k] == current[k] for k in ('project_id', 'environment_id', 'service_id'))
        and re.fullmatch('[0-9a-f-]{36}', observation['deployment_id'])
        and re.fullmatch('[0-9a-f]{40}', observation['git_sha'])
        and all(re.fullmatch('[0-9a-f]{64}', observation[k])
                for k in ('deployment_response_sha256', 'logs_sha256'))
        and re.fullmatch(r'\[[^\n]{1,100}: INFO/MainProcess\] Task app\.tasks\.run_video_pipeline\['
                         + re.escape(task) + r'\] received', observation['received_line']))
    now = datetime.now(timezone.utc)
    observed = datetime.fromisoformat(observation['observed_at'])
    received = datetime.fromisoformat(observation['received_at'])
    plan._require(observed.tzinfo is not None and received.tzinfo is not None
                  and 0 <= (now - observed).total_seconds() <= 120 and received < observed)
    original = client.get(key); source = plan._object(original)
    _, root, admission = completion.verify_child(task, source.get('parent_id'), source.get('spec'), client=client)
    plan._require(admission.get('motion_of') and not admission.get('interruption_of')
        and source.get('state') == 'PROGRESS' and source.get('stage') == 'visual_qc'
        and source.get('paid_create_slots_used') == 0 and not source.get('generated_asset_candidates')
        and (source.get('retained_long_media') or {}).get('stock_only') is True
        and (source.get('retained_long_media') or {}).get('new_tts_requests') == 0
        and source['audio_candidate_checkpoint']['audio_sha256'] == admission['transcript']['audio_sha256']
        and datetime.fromisoformat(source['created_at']) <= received
        <= datetime.fromisoformat(source['updated_at']) <= observed)
    blockers = [p + task for p in (PREFIX, UPLOAD_PREFIX, EXECUTION_LOCK_PREFIX,
        jobs.RENDER_CANCELLATION_PREFIX, jobs.RETRY_DISPATCH_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX,
        jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, 'youtube_studio:source_publication_hold:v1:')]
    failed = {**source, 'state': 'FAILURE', 'stage': 'failed', 'progress': 100,
        'failure_stage': 'visual_qc', 'error': ERROR, 'message': 'Üretim sunucusu değişti; kayıtlı çalışma korunuyor.',
        'failure_classification': classify_failure(WorkerLostError(ERROR), 'visual_qc'),
        'failure_observer': 'authenticated_railway_completion_removal', 'updated_at': now.isoformat()}
    record = {'version': 1, 'task_id': task, 'parent_id': source['parent_id'], 'root_task_id': root,
        'original_job_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'source_fingerprint': fingerprint(failed), 'observation': observation,
        'publish_eligible': False, 'retry_dispatched': False}
    try:
        with client.pipeline() as pipe:
            pipe.watch(key, *blockers)
            plan._require(pipe.get(key) == original and not pipe.exists(*blockers)
                          and not jobs._watch_retained_delivery(pipe, task))
            pipe.multi(); pipe.set(PREFIX + task, plan._raw(record), nx=True)
            pipe.setex(key, jobs.JOB_TTL_SECONDS, plan._raw(failed))
            plan._require(pipe.execute() == [True, True])
    except WatchError:
        return {'status': 'state_changed'}
    return {'status': 'failure_recorded', 'task_id': task, 'provider_requests': 0, 'queue_dispatches': 0}
