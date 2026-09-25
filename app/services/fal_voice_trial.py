"""Two bounded full-length trials after three observed Kie speech failures.

One exact request per language; every previous Kie/ASR receipt stays intact.
This operator-only grant does not activate a production provider or publish.
"""
from datetime import datetime, timezone
from contextlib import nullcontext
import json

import httpx

from app.services import fal_voice_adapter as api, kie_voice_ledger as kie

PREFIX = 'youtube_studio:commissioning:v1:fal_voice_trial:'


def _key(language):
    api.require(language in {'tr', 'en'})
    return PREFIX + language


def proof(foundation, task, body, *, reader=None, claimed=False):
    from app.services import production_spend_runtime as runtime, production_continuation as continuation
    from app.services import commissioning_audio as asr, commissioning_video as video, youtube_auth
    from app.services import kie_voice_production as production, kie_gemini_voice as gemini
    from app.services import content_plan as plan, studio_state as jobs, audio_qc
    from app.services.source_publication_hold import HOLD_PREFIX
    from app.services.youtube_publish_state import UPLOAD_PREFIX
    c = foundation.client; context = runtime.resolve_context(c, task); root = context['lineage_id']
    api.require(context['channel_id'] in kie.CHANNELS and context['kind'] == 'long')
    if reader is not None:
        reader.watch(jobs.JOB_PREFIX + task, jobs.JOB_PREFIX + root,
            'celery-task-meta-' + task, plan.ACTIVE_KEY,
            plan.production.PROFILE_PREFIX + context['channel_id'])
    source = plan._object(c.get(jobs.JOB_PREFIX + task)); language = body['language_code']
    api.require(source.get('state') == 'FAILURE' and source.get('kind') == 'render'
        and source.get('failure_stage') == 'voice_and_visuals' and source['spec']['language'] == language
        and source['spec']['duration_minutes'] == 3 and source['spec']['mode'] == 'production'
        and source.get('paid_create_slots_used') == 0 and (claimed or not source.get('retry_child_task_id'))
        and not source.get('owner_cancellation') and not source.get('publication_hold')
        and source.get('error', '').startswith('Voice synthesis quality rejected before paid media: '))
    failure = json.loads(source['error'].split(': ', 1)[1])
    api.require(failure['generation_attempts'] == 3
        and {x['generation_attempt'] for x in failure['quality_errors']} == {0, 1, 2})
    terminal = plan._object(c.get('celery-task-meta-' + task))
    api.require(terminal.get('status') == 'FAILURE' and terminal.get('task_id') == task
        and terminal['result']['exc_type'] == 'FinalAudioQualityError'
        and terminal['result']['exc_message'] == [source['error']])
    dispatch = plan._object(c.get(plan.DISPATCH_PREFIX + source['spec']['content_plan_item_id']))
    profile = plan._object(c.get(plan.production.PROFILE_PREFIX + context['channel_id']))
    leaf=plan._leaf(c,dispatch)
    api.require(leaf['task_id'] == (source.get('retry_child_task_id') if claimed else task)
        and plan.dispatch_spec_matches(dispatch, source['spec'])
        and plan._active(c).get(context['channel_id']) == source['spec']['content_plan_item_id'])
    plan.publication_series(leaf, profile, client=c)
    with (c.pipeline() if reader is None else nullcontext(reader)) as pipe:
        from app.services import commissioning_longform, production_credit_ledger
        commissioning_longform.authorize(pipe, context)
        pipe.watch(production_credit_ledger.STATE_KEY, production_credit_ledger.JOURNAL_KEY)
        policy, journal, _ = kie._read(pipe); kie._foundation(pipe, foundation)
        active = production.activation(pipe, policy)
        choice = production._choice(pipe, active, context, language)
        api.require(choice is not None)
        kie._binding(pipe, context['channel_id'], context['connection_id'])
        authority = continuation.authority(pipe, context['channel_id']); api.require(authority is not None)
        for job_id in {root, task}:
            keys = [p + job_id for p in (HOLD_PREFIX, UPLOAD_PREFIX, jobs.RENDER_CANCELLATION_PREFIX,
                jobs.QUALITY_HOLD_JOB_FENCE_PREFIX, jobs.EXTERNAL_EPISODE_LEAF_PREFIX, jobs.REPAIR_CHECKPOINT_PREFIX,
                video.PREFIX)]
            pipe.watch(*keys); api.require(not pipe.exists(*keys))
        original_body, _ = gemini.request_body(body['text'], choice['voice_id'], language=language)
        rows = {key: row for key,row in journal['requests'].items() if row['scope'] == context}
        api.require(len(rows) == 3 and {r['descriptor']['attempt'] for r in rows.values()} == {0,1,2})
        _, audio = asr._read(pipe); evidence = {}
        for identity, row in rows.items():
            api.require(row['descriptor']['request_sha256'] == kie.sha(kie.raw(original_body))
                and row['create'] is not None and row['result'] is not None)
            api.require(kie.restore(row['result']).json()['data']['state'] == 'success')
            key = kie.PREFIX + 'media:' + identity; pipe.watch(key)
            media = plan._object(pipe.get(key)); api.require(pipe.pttl(key) == -1)
            candidates = [r for r in audio['requests'].values() if r['context'] == context
                and r['request']['audio']['sha256'] == media['mp3_sha256']]
            api.require(len(candidates) == 1 and candidates[0]['outcome'] is not None)
            outcome = candidates[0]['outcome']
            raw = youtube_auth._decrypt_json(outcome['encrypted_response'])['response']
            api.require(kie.sha(raw) == outcome['response_sha256'])
            payload = json.loads(raw)
            review = audio_qc.compare_transcript(body['text'], payload['text'], words=payload['words'],
                provider='openai', comparison_language=language)
            api.require(review['pass'] is False)
            evidence[identity] = {'kie_record_sha256': kie.sha(kie.raw(row)),
                'media_sha256': kie.sha(kie.raw(media)), 'asr_sha256': kie.sha(kie.raw(candidates[0]))}
        api.require(not any(r['reservation']['intent']['root_lineage_id'] == root
            for r in production._native_intents(pipe, foundation)))
        if reader is None:
            pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
    return {'context': context, 'source_task_id': task, 'source_spec_sha256': kie.sha(kie.raw(source['spec'])),
        'source_error_sha256': kie.sha(source['error']), 'continuation_authority_sha256': authority,
        'prior_voice_records': evidence, 'original_kie_policy_sha256': kie.sha(kie.raw(policy))}


def stage(foundation, task, body, secret, *, owner_evidence_sha256):
    """Explicit private operator. Never called by a timer or read-only route."""
    import re
    ceiling = api.describe(body); api.require(ceiling <= 250000
        and type(owner_evidence_sha256) is str and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256))
    key = _key(body['language_code']); c = foundation.client
    with c.pipeline() as pipe:
        pipe.watch(key, key + ':anchor')
        api.require(not pipe.exists(key, key + ':anchor'), 'fal_voice_trial_already_staged')
        evidence = proof(foundation, task, body, reader=pipe)
        grant = {'version':1, 'purpose':'full_length_voice_validation', **evidence,
            'route':api.ROUTE, 'model':api.MODEL, 'request_sha256':kie.sha(kie.raw(body)),
            'credential_sha256':kie.sha('fal\0' + secret), 'maximum_requests':1,
            'max_list_cost_micro_usd':ceiling, 'owner_evidence_sha256':owner_evidence_sha256,
            'authorized_at':datetime.now(timezone.utc).isoformat()}
        encoded = kie.raw(grant)
        pipe.multi(); pipe.hset(key, 'grant', encoded); pipe.hset(key + ':anchor','grant',kie.sha(encoded))
        api.require(pipe.execute() == [1,1])
    return grant


def _read(pipe, key, *, purpose='full_length_voice_validation'):
    pipe.watch(key, key + ':anchor')
    rows, hashes = pipe.hgetall(key), pipe.hgetall(key + ':anchor')
    api.require(rows and set(rows) == set(hashes) and {'grant'} <= set(rows)
        and set(rows) <= {'grant','request','create','result','media','prepared_media','qualification'}
        and pipe.pttl(key) == pipe.pttl(key + ':anchor') == -1)
    api.require(all(kie.sha(value) == hashes[field] for field,value in rows.items()))
    decoded = {k:json.loads(v) for k,v in rows.items()}
    grant = decoded['grant']
    api.require(grant['version'] == 1 and grant['purpose'] == purpose
        and grant['route'] == api.ROUTE and grant['model'] == api.MODEL
        and grant['maximum_requests'] == 1 and 0 < grant['max_list_cost_micro_usd'] <= 250000)
    if 'request' in decoded:
        api.require(decoded['request']['grant_sha256'] == kie.sha(kie.raw(grant)))
    api.require('create' not in decoded or 'request' in decoded)
    api.require('result' not in decoded or 'create' in decoded)
    return decoded


class Journal:
    def __init__(self, foundation, task, body, secret):
        self.foundation, self.task, self.body, self.secret = foundation, task, body, secret
        self.key = _key(body['language_code']); self.prior = None

    def _read(self, pipe):
        return _read(pipe, self.key)

    def submit(self, sender, url, **kwargs):
        from app.services import production_continuation as continuation
        api.require(url == api.ROUTE and kwargs['json'] == self.body
            and kwargs['headers']['Authorization'] == 'Key ' + self.secret)
        with self.foundation.client.pipeline() as pipe:
            rows = self._read(pipe); grant = rows['grant']
            api.require(grant['source_task_id'] == self.task and grant['request_sha256'] == kie.sha(kie.raw(self.body))
                and grant['credential_sha256'] == kie.sha('fal\0' + self.secret)
                and grant['max_list_cost_micro_usd'] == api.describe(self.body) and grant['maximum_requests'] == 1)
            if 'request' in rows:
                api.require('create' in rows, 'fal_voice_trial_submit_unknown')
                self.prior = rows
                pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
                return kie.restore(rows['create'])
            api.require(continuation.authority(pipe, grant['context']['channel_id']) == grant['continuation_authority_sha256'])
            evidence = proof(self.foundation, self.task, self.body, reader=pipe)
            api.require(all(grant[k] == value for k,value in evidence.items()))
            request = {'grant_sha256':kie.sha(kie.raw(grant)), 'reserved_at':datetime.now(timezone.utc).isoformat()}
            encoded = kie.raw(request)
            pipe.multi(); pipe.hset(self.key,'request',encoded); pipe.hset(self.key+':anchor','request',kie.sha(encoded))
            api.require(pipe.execute() == [1,1])
        response = sender(url, **kwargs)
        self.observe('create', response)
        return response

    def observe(self, field, response):
        api.require(field in {'create','result'} and len(response.content) <= api.MAX_RESPONSE)
        receipt = kie._seal(response, self.secret); encoded = kie.raw(receipt)
        with self.foundation.client.pipeline() as pipe:
            rows = self._read(pipe)
            api.require('request' in rows and field not in rows and (field == 'create' or 'create' in rows))
            pipe.multi(); pipe.hset(self.key,field,encoded); pipe.hset(self.key+':anchor',field,kie.sha(encoded))
            api.require(pipe.execute() == [1,1])

    def result_response(self):
        value = self.prior.get('result') if self.prior else None
        return kie.restore(value) if value is not None else None

    def observe_result(self, response): self.observe('result', response)

    def read(self):
        with self.foundation.client.pipeline() as pipe:
            rows = self._read(pipe)
            pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
        return rows

    def append(self, field, value):
        api.require(field in {'media', 'prepared_media', 'qualification'})
        encoded = kie.raw(value)
        with self.foundation.client.pipeline() as pipe:
            rows = self._read(pipe)
            api.require('result' in rows and (field == 'media' or 'media' in rows)
                and (field != 'qualification' or 'prepared_media' in rows))
            if field in rows:
                api.require(rows[field] == value, 'fal_voice_trial_observation_conflict')
                pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
                return
            pipe.multi(); pipe.hset(self.key, field, encoded)
            pipe.hset(self.key + ':anchor', field, kie.sha(encoded))
            api.require(pipe.execute() == [1,1])
