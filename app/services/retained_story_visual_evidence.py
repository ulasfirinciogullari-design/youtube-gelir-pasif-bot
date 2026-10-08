"""Pre-claim, provider-free verification of retained STORY/VISUAL components.

Reads the closed completion namespace, all controller predecessors and original
source under WATCH/PING. Private immutable objects are checked against their
anchors; source-derived requests and shared semantic reducers are evaluated
without constructing an HTTPX witness or replaying an observer. An audit package
is only a hash witness for the full cut package, never an approval marker.

The historical cut/tool identity is retained, not compared to today's build or
re-rendered. Authenticated storage and Redis remain external evidence authorities;
coordinated replacement of all records is outside this reader's proof. Private
object ACL checks do not attest a bucket policy or CDN. No request, claim, reset,
render, publish or final QA authority is returned. A legitimate source claim
invalidates this pre-claim read; admission must separately freeze commitments.
"""
import base64
from copy import deepcopy
import hashlib
import json
import math
import re
import weakref

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import retained_audio_review_evidence as audio_reader
from app.services import retained_review_completion_plan as completion
from app.services import retained_cut_evidence as cuts
from app.services import retained_sampled_input_linkage as sampled
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity
from app.services import stock_story_critic_semantics as story_semantics
from app.services import strict_visual_review_semantics as visual_semantics
from app.services.voice_candidate_recovery import _voice_result
from app.services.whisper_transcription import inspect_bounded_short_audio

_STORY, _VISUAL = journal.PURPOSES
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
    'full_qa_complete': False, 'claim_authorized': False, 'render_authorized': False,
    'resume_authorized': False, 'automatic_retry_permitted': False}
_ISSUED = weakref.WeakKeyDictionary()


class RetainedStoryVisualEvidenceError(RuntimeError):
    """Fixed local rejection, excluding source, storage and response text."""


def _require(value, code='retained_story_visual_evidence_invalid'):
    if not value:
        raise RetainedStoryVisualEvidenceError(code)


def _raw(value, maximum=artifacts.MAX_MANIFEST_BYTES):
    return artifacts._raw(value, maximum)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(_raw(value))


def _object(raw, maximum=artifacts.MAX_MANIFEST_BYTES):
    value = artifacts._object(raw, maximum)
    _require(_raw(value, maximum) == (raw.encode() if type(raw) is str else raw))
    return value


def _fixed(value, expected):
    _require(type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v
                                       for k, v in expected.items()))


def _digest(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)


def _identity(value, *, maximum=cuts.MAX_CUT_BYTES):
    _require(type(value) is dict and set(value) == {'sha256', 'size'}
             and type(value['size']) is int and 0 < value['size'] <= maximum)
    _digest(value['sha256'])


def _number(value, *, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0))


class RetainedStoryVisualEvidence:
    """Immutable detached component evidence, never an admission capability."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_story_visual_evidence_private')

    def __repr__(self):
        return '<RetainedStoryVisualEvidence diagnostic-only>'

    @property
    def record(self):
        _require(type(self) is RetainedStoryVisualEvidence and self in _ISSUED)
        return _object(_ISSUED[self])

    @property
    def commitments(self):
        return self.record['commitments']

    @property
    def qa_approved(self):
        return False

    @property
    def publish_eligible(self):
        return False


def _artifact(sink, pipe, purpose, state, source, prior_sha):
    anchor = sink._read_anchor(pipe, purpose)
    _require(anchor['journal_state_sha256'] == _hash(state)
             and anchor['policy_sha256'] == _hash(state['policy'])
             and anchor['continuity_sha256'] == _hash(source)
             and anchor['prior_story_anchor_sha256'] == prior_sha,
             'retained_story_visual_anchor_history_changed')
    manifest = _object(sink._read_blob(anchor['manifest'], purpose, 'manifest'))
    _require(set(manifest) == {'version', 'kind', 'purpose', 'source', 'journal_state',
        'reservation', 'evidence', 'response_status_code', 'bodies', 'prior_story_anchor_sha256',
        'journal_keys', *artifacts._FLAGS})
    _fixed(manifest, {'version': 1, 'kind': 'retained_router_review_artifact',
                     'purpose': purpose, **artifacts._FLAGS})
    _require(manifest['journal_keys'] == list(sink._keys)
             and _raw(manifest['source']) == _raw(source)
             and _raw(manifest['journal_state']) == _raw(state)
             and manifest['prior_story_anchor_sha256'] == prior_sha
             and type(manifest['bodies']) is dict
             and set(manifest['bodies']) == set(artifacts._LIMITS) - {'manifest'})
    bodies = {kind: sink._read_blob(pointer, purpose, kind) for kind, pointer in manifest['bodies'].items()}
    artifacts._validate_capture(purpose, bodies, manifest['reservation'], manifest['evidence'],
                                manifest['response_status_code'], state)
    return anchor, manifest, bodies


def _request_bytes(request, *, schema_compat=False, schema_name=None, json_object=False):
    _require(type(request) is dict and set(request) == {
        'parts', 'purpose', 'system_instruction', 'json_schema', 'max_tokens'})
    _require(type(schema_compat) is bool and type(json_object) is bool
             and not (schema_compat and json_object))
    _require(schema_compat or schema_name is None)
    if json_object:
        from app.services.abacus_router_schema_compat import prepare_json_object_router_request
        return prepare_json_object_router_request(request['parts'], api_key='offline-source-rederivation',
            system_instruction=request['system_instruction'], json_schema=request['json_schema'],
            max_tokens=request['max_tokens'])._body_bytes
    if schema_compat:
        from app.services.abacus_router_schema_compat import prepare_compatible_router_request, SCHEMA_NAME
        # A pure local snapshot with an inert key: no HTTP request, observer or
        # send is created, and only its canonical body is compared to storage.
        return prepare_compatible_router_request(request['parts'], api_key='offline-source-rederivation',
            system_instruction=request['system_instruction'], json_schema=request['json_schema'],
            max_tokens=request['max_tokens'], schema_name=SCHEMA_NAME if schema_name is None else schema_name)._body_bytes
    body = {'model': adapter.MODEL, 'messages': [
        {'role': 'system', 'content': request['system_instruction']},
        {'role': 'user', 'content': request['parts']}],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'youtube_review', 'strict': True, 'schema': request['json_schema']}},
        'max_tokens': request['max_tokens'], 'stream': False, 'modalities': ['text']}
    # Pure body inspection, no key, request object, HTTPX or observer issuance.
    return adapter._inspect_body(body)


def _schema_result(result, schema):
    _require(adapter._matches_schema(result, schema)
             and adapter._unique_items_match(result, schema) and adapter._enum_match(result, schema))


def _story_component(result, contract):
    _schema_result(result, contract['request']['json_schema'])
    diagnostic = story_semantics.validate_stock_story_critic(result, **contract['semantic_arguments'])
    _require(not diagnostic['critic_global_error'] and not diagnostic['story_failure']
             and not diagnostic['critic_failures'] and not diagnostic['ending_failed_checks'],
             'retained_story_visual_semantics_rejected')
    return diagnostic


def _visual_component(result, contract, threshold):
    _schema_result(result, contract['request']['json_schema'])
    rows = visual_semantics.normalize_strict_visual_reviews(result, **contract['semantic_arguments'])
    rows = visual_semantics.annotate_visual_hard_gates(rows)
    rows = visual_semantics.close_router_score_reason_conflicts(rows,
        scenes=contract['semantic_arguments']['scenes'], documentary_sources=contract['documentary_sources'])
    _require(type(threshold) is int and 1 <= threshold <= 100 and set(rows) == set(range(6))
             and all(recovery._review_passes(recovery._review(rows[index]), threshold) for index in range(6)),
             'retained_story_visual_semantics_rejected')
    return rows


def _sample_link(pipe, s3, bucket, keys, state, source, visual_anchor, visual_manifest):
    reservation = visual_manifest['reservation']
    key = keys[0].rsplit(':', 1)[0] + ':sampled_cut_link:v1:' + reservation['reservation_sha256']
    pipe.watch(key)
    ttl = pipe.pttl(key)
    _require(type(ttl) is int and ttl == -1, 'retained_story_visual_sample_link_unavailable')
    anchor = _object(pipe.get(key))
    _require(set(anchor) == {'version', 'kind', 'pointer', 'journal_keys', 'journal_state_sha256',
        'visual_artifact_anchor_sha256', 'cut_manifest_sha256', 'sampled_wire_linkage_verified', *sampled._FLAGS})
    _fixed(anchor, {'version': 1, 'kind': 'retained_sampled_cut_link_anchor',
                   'sampled_wire_linkage_verified': True, **sampled._FLAGS})
    _require(anchor['journal_keys'] == list(keys) and anchor['journal_state_sha256'] == _hash(state)
             and anchor['visual_artifact_anchor_sha256'] == _hash(visual_anchor))
    pointer = anchor['pointer']
    _require(type(pointer) is dict and pointer.get('content_type') == 'application/json'
             and pointer.get('key') == f"recovery/{continuity.LEAF_ID}/sampled_cut_link/v1/{pointer.get('sha256')}.json"
             and type(pointer.get('size')) is int and 0 < pointer['size'] <= cuts.MAX_MANIFEST_BYTES)
    record = _object(cuts._read_private(s3, bucket, pointer))
    _require(set(record) == {'version', 'kind', 'source', 'cut_manifest', 'cut_package_sha256', 'cut_build',
        'journal_keys', 'journal_state_sha256', 'reservation', 'visual_artifact_anchor_sha256',
        'visual_artifact_manifest', 'evidence', 'events', 'actual_sampling_events_verified',
        'sampled_wire_linkage_verified', *sampled._FLAGS})
    _fixed(record, {'version': 1, 'kind': 'retained_sampled_cut_link',
        'actual_sampling_events_verified': True, 'sampled_wire_linkage_verified': True, **sampled._FLAGS})
    _require(record['journal_keys'] == list(keys) and record['journal_state_sha256'] == _hash(state)
             and _raw(record['source']) == _raw(source)
             and _raw(record['reservation']) == _raw(reservation)
             and _raw(record['evidence']) == _raw(visual_manifest['evidence'])
             and record['visual_artifact_anchor_sha256'] == _hash(visual_anchor)
             and record['visual_artifact_manifest'] == visual_anchor['manifest']
             and record['cut_manifest']['sha256'] == anchor['cut_manifest_sha256'])
    return key, anchor, record


def _build(value):
    _require(type(value) is dict and set(value) == {'tools', 'source_sha256'}
             and type(value['tools']) is dict and set(value['tools']) == {'ffmpeg', 'ffprobe'}
             and type(value['source_sha256']) is dict and set(value['source_sha256']) == {
                 'render.py', 'retained_cut_evidence.py', 'preserved_visual_recovery.py',
                 'visual_qc.py', 'retained_sampled_input_linkage.py'})
    for tool in value['tools'].values():
        _require(type(tool) is dict and set(tool) == {'executable', 'version_sha256', 'version_size'}
                 and type(tool['version_size']) is int and 0 < tool['version_size'] <= 65536)
        _identity(tool['executable'], maximum=256 * 1024 * 1024)
        _digest(tool['version_sha256'])
    for digest in value['source_sha256'].values():
        _digest(digest)


def _validate_timeline(record, scenes, voice):
    """Recompute the original cumulative allocation, without touching media.

    The producer has exactly one raw candidate per scene. Only its presence is
    relevant to these two pure functions; raw identity is checked separately.
    Historical executable/source hashes remain identities, not today's build.
    """
    from app.services import render
    timeline = render._scene_timeline(scenes,
        [[{'path': f'bound-source-{index}.mp4'}] for index in range(6)],
        voice['scene_durations'], record['measured_voice_duration'], [])
    _require(len(timeline) == 6 and [row[3] for row in timeline] == list(range(6)),
             'retained_story_visual_timeline_changed')
    counts = render._timeline_frame_counts(timeline, record['measured_voice_duration'])
    _require(type(record['cuts']) is list and len(record['cuts']) == 6
             and all(row['timeline_duration'] == expected[1] and row['transition'] == expected[2]
                     and type(row['target_frames']) is int and row['target_frames'] == count
                     and type(row['actual_frames']) is int and row['actual_frames'] == count
                     for row, expected, count in zip(record['cuts'], timeline, counts)),
             'retained_story_visual_timeline_changed')


def _cut_record(s3, bucket, link, source, fingerprint, manifests, voice):
    pointer = link['cut_manifest']
    prefix = f'recovery/{continuity.LEAF_ID}/retained_cuts/v1/'
    _require(type(pointer) is dict and pointer.get('content_type') == 'application/json'
             and pointer.get('key') == prefix + f"manifests/{pointer.get('sha256')}.json"
             and type(pointer.get('size')) is int and 0 < pointer['size'] <= cuts.MAX_MANIFEST_BYTES)
    record = _object(cuts._read_private(s3, bucket, pointer))
    _require(set(record) == {'version', 'kind', 'source', 'package_sha256', 'voice_metadata_sha256',
        'measured_voice_duration', 'fps', 'output_resolution', 'total_frames', 'build', 'cuts', 'objects', *cuts._FLAGS})
    _fixed(record, {'version': 1, 'kind': 'retained_cut_derivation', 'fps': 30,
                   'output_resolution': '1080x1920', **cuts._FLAGS})
    expected_source = {'source_task_id': continuity.LEAF_ID, 'source_state_sha256': fingerprint,
        'source_spec_sha256': _hash(source['spec']),
        'source_journal_sha256': _hash(source['generated_asset_candidates']),
        'source_metadata_sha256': source['audio_candidate_checkpoint']['metadata_sha256'],
        'audio': {'sha256': source['audio_candidate_checkpoint']['audio_sha256'],
                  'size': source['audio_candidate_checkpoint']['size']}}
    _require(_raw(record['source']) == _raw(expected_source)
             and record['package_sha256'] == link['cut_package_sha256']
             and record['voice_metadata_sha256'] == _hash(voice)
             and _raw(record['build']) == _raw(link['cut_build']))
    _build(record['build'])
    _number(record['measured_voice_duration'], positive=True)
    _require(28.7 <= record['measured_voice_duration'] <= 30.08
             and abs(record['measured_voice_duration'] - voice['duration_after_fit']) <= 0.12)
    _require(type(record['cuts']) is list and len(record['cuts']) == 6
             and type(record['objects']) is list and len(record['objects']) == 6
             and type(record['total_frames']) is int and record['total_frames'] > 0)
    expected_pointers, total = [], 0
    for index, row in enumerate(record['cuts']):
        _require(type(row) is dict and set(row) == {'scene_index', 'candidate_index', 'raw', 'cut',
            'timeline_duration', 'transition', 'target_frames', 'actual_frames', 'recipe', 'recipe_sha256'})
        _fixed(row, {'scene_index': index, 'candidate_index': 0})
        _identity(row['cut'])
        _require(row['raw'] == {key: manifests[index]['raw'][key] for key in ('sha256', 'size', 'provider')}
                 and type(row['target_frames']) is int and row['target_frames'] > 0
                 and type(row['actual_frames']) is int and row['actual_frames'] == row['target_frames']
                 and type(row['transition']) is str
                 and row['transition'] == str(manifests[index]['package']['scenes'][index].get('transition') or 'cut').lower())
        _number(row['timeline_duration'], positive=True)
        recipe = row['recipe']
        _require(type(recipe) is dict and set(recipe) == {'version', 'source_duration', 'duration',
            'shot_index', 'transition', 'output_resolution', 'fps', 'start_seconds', 'speed',
            'target_frames', 'forbid_loop', 'attempts', 'letterbox_measurements'}
            and _hash(recipe) == row['recipe_sha256'])
        _fixed(recipe, {'version': 1, 'shot_index': index, 'transition': row['transition'],
            'output_resolution': '1080x1920', 'fps': 30, 'target_frames': row['target_frames'], 'forbid_loop': True})
        for field in ('source_duration', 'duration', 'speed'):
            _number(recipe[field], positive=True)
        _number(recipe['start_seconds'])
        _require(recipe['duration'] == row['target_frames'] / 30
                 and type(recipe['attempts']) is list and 1 <= len(recipe['attempts']) <= 4
                 and type(recipe['letterbox_measurements']) is list
                 and len(recipe['letterbox_measurements']) <= 4)
        for measurement in recipe['letterbox_measurements']:
            _number(measurement)
        for attempt in recipe['attempts']:
            _require(type(attempt) is dict and set(attempt) == {'argv', 'actual_frames', 'scale_geometry',
                'source_crop', 'executed_argv_sha256'} and type(attempt['actual_frames']) is int
                and attempt['actual_frames'] == row['target_frames'] and type(attempt['argv']) is list
                and 1 <= len(attempt['argv']) <= 64 and all(type(arg) is str and len(arg) <= 4096 for arg in attempt['argv'])
                and attempt['argv'][0] == 'ffmpeg' and attempt['argv'][-1] == '$output'
                and attempt['argv'].count('$input') == 1 and type(attempt['scale_geometry']) is str
                and len(attempt['scale_geometry']) <= 64)
            _digest(attempt['executed_argv_sha256'])
            crop = attempt['source_crop']
            _require(crop is None or type(crop) is list and len(crop) == 2
                     and all(type(n) is int and n >= 0 for n in crop))
        total += row['actual_frames']
        expected_pointers.append({'key': prefix + f"scene-{index:02d}/{row['cut']['sha256']}.mp4",
                                  **row['cut'], 'content_type': 'video/mp4'})
    _require(record['objects'] == expected_pointers and record['total_frames'] == total
             and total == max(6, int(round(record['measured_voice_duration'] * 30))))
    _validate_timeline(record, manifests[0]['package']['scenes'], voice)
    for pointer in expected_pointers:
        cuts._read_private(s3, bucket, pointer)
    return record


def _samples(record, prepared, cut_record):
    events = record['events']
    _require(type(events) is list and len(events) == 30)
    body = _object(prepared, adapter.MAX_REQUEST_BYTES)
    content = body['messages'][1]['content']
    pairs = []
    for index, part in enumerate(content):
        if part.get('type') == 'image_url':
            _require(index > 0 and content[index-1].get('type') == 'text')
            url = part['image_url']['url']
            _require(type(url) is str and url.startswith('data:image/jpeg;base64,'))
            jpeg = base64.b64decode(url.split(',', 1)[1], validate=True)
            pairs.append((content[index-1]['text'], jpeg))
    _require(len(pairs) == 30)
    samples = []
    for number, (event, (label, jpeg)) in enumerate(zip(events, pairs)):
        index, position = divmod(number, 5)
        moment, fraction = sampled._ORDER[position]
        _require(type(event) is dict and set(event) == {'scene_index', 'candidate_index', 'moment_index',
            'fraction', 'recipe', 'image', 'label_sha256', 'label_size', 'cut_sha256'})
        _fixed(event, {'scene_index': index, 'candidate_index': 0, 'moment_index': moment, 'fraction': fraction})
        _require(event['image'] == {'sha256': _sha(jpeg), 'size': len(jpeg)}
                 and event['label_sha256'] == _sha(label.encode())
                 and type(event['label_size']) is int and event['label_size'] == len(label.encode())
                 and event['cut_sha256'] == cut_record['cuts'][index]['cut']['sha256'])
        recipe = event['recipe']
        _require(type(recipe) is dict and set(recipe) == {'source_duration', 'seconds',
            'duration_probe_argv_sha256', 'duration_probe_argv', 'executed_argv_sha256', 'argv'})
        _number(recipe['source_duration'], positive=True)
        _number(recipe['seconds'])
        _require(type(recipe['source_duration']) is float and type(recipe['seconds']) is float
            and recipe['seconds'] == recipe['source_duration'] * fraction
            and recipe['argv'] == ['$ffmpeg', '-y', '-ss', f"{recipe['seconds']:.3f}", '-i', '$cut',
                                   '-frames:v', '1', '-vf', 'scale=640:-2', '-q:v', '5', '$jpeg']
            and recipe['duration_probe_argv'] == ['$ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                                 '-of', 'default=noprint_wrappers=1:nokey=1', '$cut'])
        for field in ('duration_probe_argv_sha256', 'executed_argv_sha256'):
            _digest(recipe[field])
        samples.append({'scene_index': index, 'candidate_index': 0, 'moment_index': moment, 'jpeg': jpeg})
    return samples


def read_retained_story_visual_evidence(client, s3, *, bucket, completion_plan, audit_pointer):
    """One read-only historical transaction. Unknown outcomes fail before S3."""
    try:
        from app.services.immutable_story_review_contract import derive_immutable_story_review_contract
        keys = completion.selected_keys(completion_plan, 'story')
        _require(keys == completion.STORY_KEYS)
        ledger = journal.RouterReviewJournal(client, completion_plan=completion_plan)
        sink = artifacts.RetainedRouterReviewArtifactSink(s3, bucket=bucket)
        sink._keys = keys
        with client.pipeline() as pipe:
            pipe.watch(*keys, *artifacts._anchor_keys(keys).values(), *recovery._keys(continuity.LEAF_ID))
            state = ledger._read(pipe)
            _require(set(state['slots']) == {_STORY, _VISUAL}
                     and all(slot['response'] is not None for slot in state['slots'].values()),
                     'retained_story_visual_response_unacknowledged')
            control, states, control_raw, _ = completion._read_control(pipe)
            _require(control_raw == completion_plan._manifest_bytes and states['story'] == state)
            source = continuity._derive(pipe, state['policy']['profile_revision'])
            _require(_hash(source) == state['policy']['continuity_sha256'])
            original, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
            previous = deepcopy(state)
            previous['slots'] = {_STORY: state['slots'][_STORY]}
            previous['updated_at'] = state['slots'][_STORY]['response']['observed_at']
            first = _artifact(sink, pipe, _STORY, previous, source, None)
            final = _artifact(sink, pipe, _VISUAL, state, source, _hash(first[0]))
            link_key, link_anchor, link = _sample_link(pipe, s3, bucket, keys, state, source, final[0], final[1])
            audio_policy = states['audio']['policy']
            pointer = audio_reader._source(pipe, audio_policy)
            metadata_raw = audio_reader._blob(s3, bucket, pointer['metadata_key'],
                pointer['metadata_sha256'], audio_reader.MAX_RECORD_BYTES, content_type='application/json')
            audio_journal._metadata(metadata_raw, audio_policy)
            metadata = artifacts._object(metadata_raw)
            voice = _voice_result(metadata['voice'], 6)
            audio_raw = audio_reader._blob(s3, bucket, pointer['audio_key'], pointer['audio_sha256'],
                audio_reader.adapter.MAX_AUDIO_BYTES, size=pointer['size'], content_type='audio/mpeg')
            _require(inspect_bounded_short_audio(audio_raw, 'audio/mpeg') == audio_policy['audio'])
            manifests = recovery._manifests(s3, original)
            _require(len(manifests) == 6 and all(row['voice'] == metadata['voice'] for row in manifests))
            for row in manifests:
                raw = row['raw']
                audio_reader._blob(s3, bucket, raw['key'], raw['sha256'], recovery.assets.MAX_RAW_BYTES,
                                   size=raw['size'], content_type='video/mp4')
            from app import tasks
            options = recovery._options(original)
            _require(tasks._normalized_options(options, .5) == options)
            package = recovery._immutable_shooting_package(manifests[0]['package'], {}, options)
            story = derive_immutable_story_review_contract(package, original['spec']['topic'], .5,
                original['spec']['language'], options,
                immutable_candidate_narrations=[scene['narration'] for scene in metadata['package']['scenes']])
            _require(_request_bytes(story['request']) == first[2]['prepared'],
                     'retained_story_visual_request_changed')
            story_result = _object(first[2]['result'], adapter.MAX_RESPONSE_BYTES)
            story_diagnostic = _story_component(story_result, story)
            cut_record = _cut_record(s3, bucket, link, original, fingerprint, manifests, voice)
            # Exact full package hash proves which diagnostic package the cut
            # producer saw. Only its independently source-derived core is used.
            audit = recovery._read_record(s3, audit_pointer, 'audit')
            _require(audit_pointer['source_task_id'] == continuity.LEAF_ID
                     and type(audit.get('package')) is dict
                     and _hash(audit['package']) == cut_record['package_sha256']
                     and _raw(recovery._story_fields(audit['package'])) == _raw(story['candidate']),
                     'retained_story_visual_package_changed')
            cuts._read_private(s3, bucket, {key: audit_pointer[key] for key in ('key', 'sha256', 'size')}
                               | {'content_type': 'application/json'})
            samples = _samples(link, final[2]['prepared'], cut_record)
            scene_visuals = [[{'path': f'scene-{index:02d}.mp4', 'generated': True,
                'source_type': 'generated', 'generation_provider': row['raw']['provider'],
                'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True}]
                for index, row in enumerate(cut_record['cuts'])]
            visual = visual_semantics.derive_strict_visual_review_contract(story['candidate']['scenes'],
                scene_visuals, samples=samples, topic=original['spec']['topic'],
                story_scenes=story['candidate']['scenes'], content_style=options.get('content_style', ''),
                evidence_sources=story['candidate'].get('sources') or [])
            _require(_request_bytes(visual['request']) == final[2]['prepared'],
                     'retained_story_visual_request_changed')
            visual_result = _object(final[2]['result'], adapter.MAX_RESPONSE_BYTES)
            rows = _visual_component(visual_result, visual, options['quality_threshold'])
            commitments = {'completion_manifest_sha256': _sha(control_raw),
                'completion_journal_sha256': _hash(pipe.hgetall(completion.JOURNAL_KEY)),
                'completion_anchor_sha256': pipe.get(completion.ANCHOR_KEY),
                'predecessor_snapshot_sha256': control['predecessors']['snapshot_sha256'],
                'journal_keys': list(keys), 'journal_state_sha256': _hash(state),
                'continuity_sha256': _hash(source), 'source_state_sha256': fingerprint,
                'source_spec_sha256': _hash(original['spec']),
                'source_journal_sha256': _hash(original['generated_asset_candidates']),
                'source_metadata_sha256': _sha(metadata_raw), 'audio': audio_policy['audio'],
                'story_anchor_sha256': _hash(first[0]), 'visual_anchor_sha256': _hash(final[0]),
                'story_manifest': first[0]['manifest'], 'visual_manifest': final[0]['manifest'],
                'sampled_link_anchor_key': link_key, 'sampled_link_anchor_sha256': _hash(link_anchor),
                'sampled_link': link_anchor['pointer'], 'cut_manifest': link['cut_manifest'],
                'cut_package_sha256': cut_record['package_sha256'], 'immutable_core_sha256': _hash(story['candidate']),
                'audit_pointer': deepcopy(audit_pointer), 'cuts': cut_record['objects'],
                'historical_build': cut_record['build']}
            result = {'version': 1, 'kind': 'retained_story_visual_component_evidence', **_FLAGS,
                'component_pass': True, 'preclaim_only': True, 'journal_read_acknowledged': True,
                'sampled_wire_linkage_verified': True, 'commitments': commitments,
                'story_diagnostic': story_diagnostic, 'visual_diagnostics': [rows[index] for index in range(6)]}
            encoded = _raw(result)
            artifacts._ack_read(pipe)
        value = object.__new__(RetainedStoryVisualEvidence)
        _ISSUED[value] = encoded
        return value
    except RetainedStoryVisualEvidenceError:
        raise
    except Exception:
        raise RetainedStoryVisualEvidenceError('retained_story_visual_read_unverified') from None
