"""Five ordered original Shorts followed by their standalone long adaptation.

Only a verified public final episode permits an automatic new case. Completed
plans and their receipts are archived, never rewritten as new production.
"""
from datetime import datetime, timezone
import json
from uuid import uuid5, NAMESPACE_URL

from app.services import content_plan as plan, studio_state as jobs
from app.services.framecase_cadence import CHANNEL_ID

PREFIX = 'youtube_studio:framecase_schedule:v1:'
STORY_PREFIX = PREFIX + 'story:'


def _require(value):
    plan._require(value, 'framecase_schedule_unverified')


def stored_story(dispatch):
    entry = dispatch['item']; series = entry['series']
    series_id = series['id'].removesuffix('_feature')
    document = json.loads(plan._client().get(STORY_PREFIX + series_id) or '{}')
    _require(document.get('series_id') == series_id and document.get('channel_id') == CHANNEL_ID
        and len(document.get('episodes', [])) == 5)
    number = series['number']
    return {'bible': document['visual_bible'], 'arc': document['episodes'],
        'episode': document['episodes'][number - 1] if entry['format'] == 'animation' else None,
        'locked_narration': entry['format'] == 'animation'}


def items(story, *, initial_long_only=False):
    rows = []; previous = None
    if not initial_long_only:
        for episode in story['episodes']:
            number = episode['number']
            identity = str(uuid5(NAMESPACE_URL, 'framecase:' + story['series_id'] + ':' + str(number)))
            brief = ('ORIGINAL FICTIONAL ANIMATION. Canonical story: ' + story['series_id']
                + '\n' + episode['narration'] + '\nShots: ' + ' | '.join(episode['shots'])
                + '\nStyle: ' + story['visual_bible'])[:4000]
            rows.append(plan.item(episode['title'], brief, 'animation', item_id=identity,
                series={'id': story['series_id'], 'name': story['series_name'], 'number': number, 'total': 5},
                depends_on=[previous] if previous else []))
            previous = identity
    identity = str(uuid5(NAMESPACE_URL, 'framecase:' + story['series_id'] + ':feature'))
    rows.append(plan.item(story['series_name'] + ' | The Complete Animated Mystery',
        'ORIGINAL FICTIONAL ANIMATION. Canonical story: ' + story['series_id']
        + '. A standalone three-minute landscape adaptation of all five episodes. '
        'New complete cinematic edit, motivated characters, clear clues and a resolved ending. '
        'Preserve the authored mystery and character bible. Do not turn fiction into documentary claims.',
        'long', item_id=identity, series={'id': story['series_id'] + '_feature',
        'name': story['series_name'], 'number': 1, 'total': 1}, depends_on=[previous] if previous else []))
    return rows


def _completed(client):
    document = plan.read(CHANNEL_ID, client=client)
    if (not document or document.get('enabled') is not True or not document['items']
            or CHANNEL_ID in plan._active(client)
            or any(not client.exists(plan.COMPLETION_PREFIX + row['id']) for row in document['items'])):
        return None
    last = document['items'][-1]
    _require(last['format'] == 'long')
    dispatch = plan._object(client.get(plan.DISPATCH_PREFIX + last['id']))
    proof = plan.publication_proof(client, dispatch)
    _require(proof is not None and proof['source_task_id'] == dispatch['task_id'])
    return document, dispatch


def maintain():
    from app.production_tasks import prepare_framecase_successor
    client = plan._client(); completed = _completed(client)
    if not completed:
        return {'status': 'working_or_waiting'}
    document, dispatch = completed
    task = str(uuid5(NAMESPACE_URL, 'framecase-next:' + dispatch['task_id']))
    key = PREFIX + 'preparation:' + document['revision']
    if not client.set(key, plan._raw({'task_id': task, 'source_task_id': dispatch['task_id'],
                                     'status': 'reserved'}), nx=True):
        return {'status': 'successor_preparing_or_stopped'}
    try:
        prepare_framecase_successor.apply_async(args=(document['revision'], dispatch['task_id']),
                                                task_id=task, retry=False)
    except Exception:
        return {'status': 'successor_dispatch_uncertain'}
    return {'status': 'successor_preparing'}


def prepare(revision, source_id, operation):
    from app.services.production_spend_runtime import _TASK_ID
    client = plan._client(); completed = _completed(client)
    _require(completed is not None)
    document, dispatch = completed
    expected = str(uuid5(NAMESPACE_URL, 'framecase-next:' + source_id))
    claim = json.loads(client.get(PREFIX + 'preparation:' + revision) or '{}')
    _require(document['revision'] == revision and dispatch['task_id'] == source_id
        and operation == expected == claim.get('task_id') and claim.get('source_task_id') == source_id)
    _require(client.set(PREFIX + 'execution:' + operation, 'started', nx=True))
    token = _TASK_ID.set(source_id)
    try:
        story, review, history = reviewed_story([row['title'] for row in document['items']])
    finally:
        _TASK_ID.reset(token)
    series_id = 'framecase_case_' + source_id.replace('-', '')[:16]
    story.update(channel_id=CHANNEL_ID, series_id=series_id, fiction_review=review,
                 predecessor_task_id=source_id, editorial_history=history)
    new_items = items(story)
    next_revision = str(uuid5(NAMESPACE_URL, 'framecase-plan:' + series_id))
    next_document = {**document, 'revision': next_revision, 'items': new_items,
        'updated_at': datetime.now(timezone.utc).isoformat()}
    plan._plan(plan._raw(next_document), CHANNEL_ID)
    with client.pipeline() as pipe:
        key = plan.PLAN_PREFIX + CHANNEL_ID
        pipe.watch(key, plan.ACTIVE_KEY, STORY_PREFIX + series_id, PREFIX + 'archive:' + revision)
        _require(pipe.get(key) == plan._raw(document) and CHANNEL_ID not in plan._active(pipe))
        for row in document['items']:
            pipe.watch(plan.COMPLETION_PREFIX + row['id'])
            _require(pipe.exists(plan.COMPLETION_PREFIX + row['id']))
        _require(not pipe.exists(STORY_PREFIX + series_id, PREFIX + 'archive:' + revision))
        pipe.multi(); pipe.set(STORY_PREFIX + series_id, plan._raw(story), nx=True)
        pipe.set(PREFIX + 'archive:' + revision, plan._raw(document), nx=True)
        pipe.set(key, plan._raw(next_document)); _require(pipe.execute() == [True, True, True])
    return {'status': 'next_case_ready', 'series_id': series_id, 'shorts': 5, 'long': 1}


def reviewed_story(history):
    """Correct explicit writer/critic findings before any new media exists."""
    from app.services.production_included_router import generate_text_json
    from app.services.framecase_pipeline import CHECKS
    episode_schema = {'type': 'object', 'properties': {
        'number': {'type': 'integer'}, 'title': {'type': 'string'}, 'narration': {'type': 'string'},
        'shots': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['number', 'title', 'narration', 'shots'], 'additionalProperties': False}
    schema = {'type': 'object', 'properties': {
        'series_name': {'type': 'string'}, 'visual_bible': {'type': 'string'},
        'episodes': {'type': 'array', 'items': episode_schema}},
        'required': ['series_name', 'visual_bible', 'episodes'], 'additionalProperties': False}
    feedback = []; attempts = []
    for attempt in range(3):
        story = generate_text_json('Write a fresh five-part original animated mystery for Framecase Stories. '
            'General audience, adult detective Mira Vale, dark wavy shoulder-length hair, hazel eyes, cream '
            'raincoat. Painterly 2D navy/amber/ivory/teal. Distinct supporting adults, no real people or '
            'franchises. No gore or child-directed story. Each of exactly five numbered episodes has an '
            'immediate intrigue, a concrete clue payoff and one honest next question; episode 5 resolves '
            'everything. One coherent mystery with a satisfying fair-play reveal; no repeated clock trick. '
            'Each English narration MUST have 55-70 words in exactly four filmable beats; four shot '
            'descriptions, each a clear character/object action, no slideshows, dense UI or need for '
            'readable generated documents. Titles <=95 characters; visual_bible <=1000 characters. '
            'Do not use unsupported current-event claims or copied plots. Avoid these prior titles: '
            + json.dumps({'prior_titles': history, 'correction_attempt': attempt, 'previous_findings': feedback}), schema, purpose='editorial')
        try:
            _require(type(story.get('episodes')) is list and len(story['episodes']) == 5
                and 1 <= len(story.get('series_name', '')) <= 80
                and 100 <= len(story.get('visual_bible', '')) <= 1000)
            for number, episode in enumerate(story['episodes'], 1):
                _require(episode.get('number') == number and 1 <= len(episode.get('title', '')) <= 95
                    and 55 <= len(episode.get('narration', '').split()) <= 70
                    and type(episode.get('shots')) is list and len(episode['shots']) == 4
                    and all(type(s) is str and 10 <= len(s) <= 350 for s in episode['shots']))
        except (plan.ContentPlanError, KeyError, TypeError):
            feedback = [{"reason": "Exact episode count, numbering, word, shot or metadata limits failed",
                         "candidate": story}]
            attempts.append({"candidate_sha256": plan._sha(story), "findings": feedback[0]["reason"]})
            continue
        review_schema = {'type': 'object', 'properties': {k: {'type': 'boolean'} for k in CHECKS},
                         'required': list(CHECKS), 'additionalProperties': False}
        review = generate_text_json('Independently reject any inconsistent, copied, unfilmable or weak '
            'five-episode fictional mystery. Check fair-play clues, motivated adults, a clear first-second '
            'hook in every part, distinct episode payoffs and a complete final solution. No factual-news '
            'claim, missing clue, unclear logic, static slideshow or textual UI requirement. Review the '
            'actual whole arc, never trust the writer\'s assertion of quality.\n' + json.dumps(story),
            review_schema, purpose='story_review')
        findings = [key for key in CHECKS if review.get(key) is not True]
        attempts.append({"candidate_sha256": plan._sha(story), "review": review, "findings": findings})
        if not findings:
            return story, review, attempts
        feedback = [{"candidate": story, "independent_rejections": findings}]
    raise plan.ContentPlanError("framecase_successor_story_rejected")
