"""Bounded three-minute documentaries explicitly installed in the owner's queue.

No existing Short or historical grant is reclassified. Every actual provider
adapter checks the durable long-form dispatch before admitting this new scope.
"""
from copy import deepcopy
import hashlib
import json

from app.services.production_spend import SpendBlocked

MAX_SECONDS = 240
MAX_SCENES = 32


def _require(value):
    if not value:
        raise SpendBlocked('commissioning_longform_unverified')


def authorize(reader, context):
    from app.services import content_plan as plan, production_continuation as continuation
    from app.services import production_spend_runtime as runtime
    _require(context.get('kind') == 'long' and 'purpose' not in context)
    root_key = runtime._JOB_PREFIX + context['lineage_id']
    reader.watch(root_key)
    root = plan._object(reader.get(root_key)); spec = root.get('spec') or {}
    _require(root.get('task_id') == context['lineage_id'] and root.get('parent_id') is None
        and root.get('kind') == 'render' and spec.get('mode') == 'production'
        and spec.get('format') == 'landscape' and spec.get('duration_minutes') == 3
        and spec.get('publish_after_render') is True and spec.get('production_scheduled') is True
        and spec.get('production_channel_id') == context['channel_id']
        and spec.get('production_connection_id') == context['connection_id'])
    entry = plan._id(spec.get('content_plan_item_id'))
    dispatch_key, plan_key = plan.DISPATCH_PREFIX + entry, plan.PLAN_PREFIX + context['channel_id']
    reader.watch(dispatch_key, plan_key)
    dispatch = plan._object(reader.get(dispatch_key))
    _require(dispatch.get('task_id') == context['lineage_id']
        and dispatch.get('channel_id') == context['channel_id']
        and dispatch.get('connection_id') == context['connection_id']
        and dispatch.get('spec_sha256') == plan._sha(spec)
        and dispatch['item']['format'] == 'long')
    document = plan._plan(reader.get(plan_key), context['channel_id'])
    _require(any(row == dispatch['item'] for row in document['items']))
    for previous in document['items']:
        if previous['id'] == entry:
            break
        key = plan.COMPLETION_PREFIX + previous['id']; reader.watch(key)
        _require(reader.exists(key))
    _require(continuation.authority(reader, context['channel_id']) is not None)
    return plan._sha(dispatch)


def active():
    from app.services import production_spend_runtime as runtime
    if not runtime.enforcement_enabled() or not runtime._TASK_ID.get():
        return False
    foundation = runtime.configured_ledger(read_timeout=3)
    context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
    if context.get('kind') != 'long':
        return False
    with foundation.client.pipeline() as pipe:
        authorize(pipe, context)
        pipe.multi(); pipe.ping(); _require(pipe.execute() == [True])
    return True


def review_story(package, topic, language):
    """Review every narrated sentence against retrieved sources before media."""
    from app.services import included_factual_audit as audit, included_research_sources as sources
    from app.services.production_included_router import generate_text_json, _LAST_OBSERVED
    from app.services.director import ProductionContentError
    _require(active())
    scenes = package['scenes']; _require(8 <= len(scenes) <= MAX_SCENES)
    pages = [sources.fetch_page(v['url']) for v in package['sources']]
    checks = ('accurate_complete_documentary', 'clear_question_and_payoff',
              'natural_narration_and_transitions', 'illustration_not_misrepresented_as_archive')
    schema = {'type': 'object', 'properties': {k: {'type': 'boolean'} for k in checks},
              'required': list(checks), 'additionalProperties': False}
    overview = json.dumps({'topic': topic, 'language': language, 'complete_story': scenes}, ensure_ascii=False)
    reviews = []
    for offset in range(0, len(scenes), 10):
        batch = [{'position': i, 'narration': row['narration']}
                 for i, row in enumerate(scenes[offset:offset + 10])]
        prompt, contract = audit.request('Review the complete documentary independently. '
            'Assess every global editorial check; use false for an unsupported or uncertain claim. '
            'The factual audit below covers this exact batch; global checks concern the complete story.\n'
            + overview, schema, batch, pages)
        response = generate_text_json(prompt, purpose='story_review', schema=contract)
        editorial, evidence, failures = audit.validate(response, batch, pages)
        if failures or set(editorial) != set(checks) or any(editorial[k] is not True for k in checks):
            raise ProductionContentError('Long documentary failed independent source or editorial review')
        reviews.append({'offset': offset, 'sentence_count': len(batch), 'source_audit': evidence,
                        'provider_evidence': deepcopy(_LAST_OBSERVED.get())})
    result = deepcopy(package)
    result['longform_story_qc'] = {'version': 1, 'accepted': True, 'reviewed_scene_count': len(scenes),
        'story_sha256': hashlib.sha256(json.dumps(scenes, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'reviews': reviews}
    return result
