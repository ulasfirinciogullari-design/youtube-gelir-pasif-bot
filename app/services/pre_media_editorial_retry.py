"""Keep fresh editorial checks on an already claimed retry before any media.

This read-only predicate authorizes no dispatch, voice replacement, spending,
approval or ledger reset. The worker has already acquired its ordinary retry
execution. Every ancestor must still precede media, and the original native
credit ledger must show no voice intent at all for that episode.
"""
import json
import re

from app.services import production_spend_runtime as runtime, production_included_router as included
from app.services import studio_state as jobs
from app.services.production_credit_ledger import CreditLedger
from app.services.fresh_story_binding import fresh_scheduled_spec_matches


_TASK = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_MEDIA = ('result', 'audio_candidate_checkpoint', 'audio_candidate_checkpoint_error',
    'voice_candidate_reuse', 'voice_replacement', 'generated_asset_candidates',
    'repair_checkpoint', 'qa_workprint', 'youtube', 'youtube_automation', 'video_key', 'youtube_video_id')


def eligible(task_id, source_id, spec):
    if not (runtime.enforcement_enabled() and included.enabled()):
        return False
    if not (all(type(value) is str and _TASK.fullmatch(value) for value in (task_id, source_id))
            and task_id != source_id and type(spec) is dict and spec.get('workflow') == 'auto'
            and spec.get('mode') == 'production' and spec.get('format') == 'shorts'
            and spec.get('duration_minutes') == .5 and spec.get('production_scheduled') is True
            and spec.get('publish_after_render') is True):
        return False
    try:
        foundation = runtime.configured_ledger(read_timeout=2)
        client = foundation.client
        voice = CreditLedger(client, foundation=foundation, clock=foundation.clock)
        with client.pipeline() as pipe:
            voice._watch(pipe)
            _, native_state, _, _ = voice._read(pipe, foundation.clock())
            router_state, _ = included.IncludedRouterLedger(foundation)._read(pipe)
            current, child, lineage = task_id, None, []
            for _ in range(16):
                if type(current) is not str or not _TASK.fullmatch(current) or current in lineage:
                    return False
                lineage.append(current)
                job_key = jobs.JOB_PREFIX + current
                budget_key = jobs.PAID_CREATE_BUDGET_PREFIX + current
                blocked = [prefix + current for prefix in (
                    jobs.REPAIR_CHECKPOINT_PREFIX, jobs.REPAIR_CHECKPOINT_CLAIM_PREFIX,
                    jobs.RENDER_CANCELLATION_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX)]
                pipe.watch(job_key, budget_key, *blocked)
                if pipe.exists(*blocked):
                    return False
                job = json.loads(pipe.get(job_key))
                budget = pipe.hgetall(budget_key)
                if not (job.get('task_id') == current and job.get('kind') == 'render'
                        and fresh_scheduled_spec_matches(job.get('spec'), spec)
                        and all(job.get(field) is None for field in _MEDIA)
                        and budget.get('used') == '0' and budget.get('cap') in {'1', '2', '3', '4', '5', '6'}):
                    return False
                parent = job.get('parent_id')
                if child is None:
                    if (parent != source_id or job.get('state') not in {'PENDING', 'STARTED', 'PROGRESS', 'RETRY'}
                            or job.get('retry_child_task_id')):
                        return False
                else:
                    if not (job.get('state') == 'FAILURE' and job.get('failure_stage') in {'research', 'director_qc'}
                            and job.get('retry_child_task_id') == child and job.get('retry_claimed') is True):
                        return False
                    dispatch_key = jobs.RETRY_DISPATCH_PREFIX + current
                    claim_key = jobs.RETRY_CHILD_CLAIM_PREFIX + child
                    execution_key = jobs.RETRY_CHILD_EXECUTION_PREFIX + child
                    pipe.watch(dispatch_key, claim_key, execution_key)
                    dispatch, claim = pipe.hgetall(dispatch_key), pipe.hgetall(claim_key)
                    token = dispatch.get('token')
                    if not (type(token) is str and 16 <= len(token) <= 256
                            and dispatch.get('child_task_id') == child and dispatch.get('mode') == 'full'
                            and dispatch.get('state') in {'reserved', 'dispatched', 'uncertain'}
                            and claim == {'source_task_id': current, 'token': token}
                            and pipe.get(execution_key) == token):
                        return False
                if parent is None:
                    break
                child, current = current, parent
            else:
                return False
            context = {'channel_id': spec['production_channel_id'],
                'connection_id': spec['production_connection_id'], 'lineage_id': current, 'kind': 'shorts'}
            if any(entry['reservation']['intent']['root_lineage_id'] == current
                    for entry in native_state['intents'].values()):
                return False
            # Existing root identity is mandatory. A newly claimed child may
            # receive its own ordinary binding only when its first request runs.
            if json.loads(pipe.hget(runtime.LEDGER_KEY, 'binding:' + current) or 'null') != context:
                return False
            for task in lineage:
                binding = pipe.hget(runtime.LEDGER_KEY, 'binding:' + task)
                if binding is not None and json.loads(binding) != context:
                    return False
            channel_key = runtime._CHANNEL_PREFIX + context['channel_id']
            pipe.watch(channel_key, runtime._CHANNEL_INDEX)
            channel = json.loads(pipe.get(channel_key))
            if not (context['channel_id'] in router_state['policy']['allowed_channels']
                    and pipe.sismember(runtime._CHANNEL_INDEX, context['channel_id'])
                    and channel.get('id') == context['channel_id']
                    and channel.get('connection_id') == context['connection_id']
                    and channel.get('requires_reconnect') is not True):
                return False
            pipe.multi(); pipe.ping()
            return pipe.execute() == [True]
    except Exception:
        return False
