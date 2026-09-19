"""Read the actual VISUAL component whose predecessor is a saved STORY capture.

The two components keep their different provenance. The old STORY slot remains
unknown; its authenticated body is interpreted again through the existing pure
reader. The new VISUAL requires an observed journal receipt, private artifact
and the actual thirty sampled JPEGs from the original six timed cuts. Neither
component is a final audio/render check or permission to reserve, claim or publish.

All controller/source/anchor records remain watched until the final read ACK.
Expiry limits requests, not this historical read. Private object storage and
Redis are trusted evidence stores; coordinated replacement of every record is
outside the proof. A later source claim invalidates this pre-claim reader.
"""
from copy import deepcopy
import weakref

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_artifacts as artifacts
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import immutable_story_review_contract as story_contract
from app.services import preserved_visual_recovery as recovery
from app.services import production_connection_continuity as continuity
from app.services import retained_audio_review_evidence as audio_reader
from app.services import retained_cut_evidence as cuts
from app.services import retained_review_captured_story_continuation as continuation
from app.services import retained_review_completion_plan as completion
from app.services import retained_sampled_input_linkage as sampled
from app.services import retained_story_visual_evidence as shared
from app.services import retained_transport_story_evidence as story_reader
from app.services import strict_visual_review_semantics as visual_semantics
from app.services.voice_candidate_recovery import _voice_result, require_unchanged_voice_narration

_STORY, _VISUAL = journal.PURPOSES
_FLAGS = {**shared._FLAGS, 'story_settlement_observed': False,
          'story_live_observer_verified': False, 'audio_qa_complete': False}
_ISSUED = weakref.WeakKeyDictionary()
_raw, _hash, _sha, _object, _fixed = shared._raw, shared._hash, shared._sha, shared._object, shared._fixed


class RetainedCapturedStoryVisualEvidenceError(RuntimeError):
    """Fixed rejection text, excluding private source, body and backend strings."""


def _require(value, reason='retained_captured_story_visual_unverified'):
    if not value:
        raise RetainedCapturedStoryVisualEvidenceError(reason)


class RetainedCapturedStoryVisualEvidence:
    """Issuer-owned immutable component evidence, never an admission capability."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_captured_story_visual_private')

    def __repr__(self):
        return '<RetainedCapturedStoryVisualEvidence diagnostic-only>'

    @property
    def record(self):
        _require(type(self) is RetainedCapturedStoryVisualEvidence and self in _ISSUED)
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


def _artifact(sink, pipe, state, source, tag):
    anchor = sink._read_anchor(pipe, _VISUAL)
    _require(anchor['journal_state_sha256'] == _hash(state)
             and anchor['policy_sha256'] == _hash(state['policy'])
             and anchor['continuity_sha256'] == _hash(source)
             and anchor['prior_story_anchor_sha256'] is None
             and _raw(anchor['story_predecessor']) == _raw(tag))
    manifest = _object(sink._read_blob(anchor['manifest'], _VISUAL, 'manifest'))
    _require(set(manifest) == {'version', 'kind', 'purpose', 'source', 'journal_state',
        'reservation', 'evidence', 'response_status_code', 'bodies', 'prior_story_anchor_sha256',
        'journal_keys', 'story_predecessor', *artifacts._FLAGS})
    _fixed(manifest, {'version': 2, 'kind': 'retained_router_review_artifact',
                     'purpose': _VISUAL, **artifacts._FLAGS})
    _require(manifest['journal_keys'] == list(sink._keys)
             and _raw(manifest['source']) == _raw(source)
             and _raw(manifest['journal_state']) == _raw(state)
             and manifest['prior_story_anchor_sha256'] is None
             and _raw(manifest['story_predecessor']) == _raw(tag)
             and type(manifest['bodies']) is dict
             and set(manifest['bodies']) == set(artifacts._LIMITS) - {'manifest'})
    bodies = {kind: sink._read_blob(pointer, _VISUAL, kind) for kind, pointer in manifest['bodies'].items()}
    artifacts._validate_capture(_VISUAL, bodies, manifest['reservation'], manifest['evidence'],
                                manifest['response_status_code'], state)
    return anchor, manifest, bodies


def _sample_link(pipe, s3, bucket, keys, state, source, visual_anchor, visual_manifest, tag):
    reservation = visual_manifest['reservation']
    key = keys[0].rsplit(':', 1)[0] + ':sampled_cut_link:v1:' + reservation['reservation_sha256']
    pipe.watch(key)
    ttl = pipe.pttl(key)
    _require(type(ttl) is int and ttl == -1)
    anchor = _object(pipe.get(key))
    _require(set(anchor) == {'version', 'kind', 'pointer', 'journal_keys', 'journal_state_sha256',
        'visual_artifact_anchor_sha256', 'cut_manifest_sha256', 'sampled_wire_linkage_verified',
        'story_predecessor', *sampled._FLAGS})
    _fixed(anchor, {'version': 2, 'kind': 'retained_sampled_cut_link_anchor',
                   'sampled_wire_linkage_verified': True, **sampled._FLAGS})
    _require(anchor['journal_keys'] == list(keys) and anchor['journal_state_sha256'] == _hash(state)
             and anchor['visual_artifact_anchor_sha256'] == _hash(visual_anchor)
             and _raw(anchor['story_predecessor']) == _raw(tag))
    pointer = anchor['pointer']
    _require(type(pointer) is dict and pointer.get('content_type') == 'application/json'
             and pointer.get('key') == f"recovery/{continuity.LEAF_ID}/sampled_cut_link/v1/{pointer.get('sha256')}.json"
             and type(pointer.get('size')) is int and 0 < pointer['size'] <= cuts.MAX_MANIFEST_BYTES)
    record = _object(cuts._read_private(s3, bucket, pointer))
    _require(set(record) == {'version', 'kind', 'source', 'cut_manifest', 'cut_package_sha256', 'cut_build',
        'journal_keys', 'journal_state_sha256', 'reservation', 'visual_artifact_anchor_sha256',
        'visual_artifact_manifest', 'evidence', 'events', 'actual_sampling_events_verified',
        'sampled_wire_linkage_verified', 'story_predecessor', *sampled._FLAGS})
    _fixed(record, {'version': 2, 'kind': 'retained_sampled_cut_link',
        'actual_sampling_events_verified': True, 'sampled_wire_linkage_verified': True, **sampled._FLAGS})
    _require(record['journal_keys'] == list(keys) and record['journal_state_sha256'] == _hash(state)
             and _raw(record['source']) == _raw(source)
             and _raw(record['reservation']) == _raw(reservation)
             and _raw(record['evidence']) == _raw(visual_manifest['evidence'])
             and _raw(record['story_predecessor']) == _raw(tag)
             and record['visual_artifact_anchor_sha256'] == _hash(visual_anchor)
             and record['visual_artifact_manifest'] == visual_anchor['manifest']
             and record['cut_manifest']['sha256'] == anchor['cut_manifest_sha256'])
    return key, anchor, record


def _source(pipe, s3, bucket, original, audio_policy):
    pointer = audio_reader._source(pipe, audio_policy)
    metadata_raw = audio_reader._blob(s3, bucket, pointer['metadata_key'], pointer['metadata_sha256'],
        audio_reader.MAX_RECORD_BYTES, content_type='application/json')
    audio_journal._metadata(metadata_raw, audio_policy)
    metadata = _object(metadata_raw, audio_reader.MAX_RECORD_BYTES)
    voice = _voice_result(metadata['voice'], 6)
    manifests = recovery._manifests(s3, original)
    _require(len(manifests) == 6 and all(row['voice'] == metadata['voice'] for row in manifests))
    require_unchanged_voice_narration(metadata['package'], manifests[0]['package'])
    for row in manifests:
        raw = row['raw']
        cuts._read_private(s3, bucket, {'key': raw['key'], 'sha256': raw['sha256'],
            'size': raw['size'], 'content_type': 'video/mp4'})
    from app import tasks
    options = recovery._options(original)
    _require(tasks._normalized_options(options, .5) == options)
    package = recovery._immutable_shooting_package(manifests[0]['package'], {}, options)
    contract = story_contract.derive_immutable_story_review_contract(package, original['spec']['topic'],
        .5, original['spec']['language'], options,
        immutable_candidate_narrations=[scene['narration'] for scene in metadata['package']['scenes']])
    return contract, options, manifests, voice, _sha(metadata_raw)


def read_retained_captured_story_visual_evidence(client, s3, *, bucket,
        captured_story_continuation, story_evidence, audit_pointer):
    """Read a positive fixed VISUAL component; new request admission is separate."""
    try:
        qualification = continuation._evidence(story_evidence)
        keys = continuation.selected_keys(captured_story_continuation, 'story')
        _require(artifacts._captured_keys(keys))
        ledger = journal.RouterReviewJournal(client, captured_story_continuation=captured_story_continuation)
        sink = artifacts.RetainedRouterReviewArtifactSink(s3, bucket=bucket)
        sink._keys = keys
        with client.pipeline() as pipe:
            anchors = artifacts._anchor_keys(keys)
            pipe.watch(*keys, *anchors.values(), *recovery._keys(continuity.LEAF_ID))
            state = ledger._read(pipe)
            _require(set(state['slots']) == {_VISUAL} and state['slots'][_VISUAL]['response'] is not None,
                     'retained_captured_story_visual_response_unacknowledged')
            control, states, control_raw, _ = continuation._read_control(pipe, authorization=captured_story_continuation)
            control_keys = continuation.controller_keys(captured_story_continuation)
            _require(control_raw == continuation._issued_bytes(captured_story_continuation)
                     and states['story'] == state and _raw(control['story_qualification']) == _raw(qualification)
                     and pipe.exists(anchors[_STORY]) == 0)
            source = continuity._derive(pipe, state['policy']['profile_revision'])
            _require(_hash(source) == state['policy']['continuity_sha256'])
            original, fingerprint = recovery._state(continuity.LEAF_ID, pipe)
            # The outer transaction already watches all historical/source/intent
            # records. This separate genuine reader must succeed afresh, and any
            # concurrent change also aborts the outer final PING.
            old_cap = completion.read_retained_completion_plan(client)
            fresh_story = story_reader.read_retained_transport_story_evidence(
                client, s3, bucket=bucket, completion_plan=old_cap)
            _require(_raw(fresh_story.record) == _raw(qualification))
            tag = {'version': 1, 'kind': 'captured_transport_story',
                'continuation_manifest_sha256': _sha(control_raw), 'evidence': qualification}
            anchor, manifest, bodies = _artifact(sink, pipe, state, source, tag)
            link_key, link_anchor, link = _sample_link(pipe, s3, bucket, keys, state, source,
                                                      anchor, manifest, tag)
            contract, options, manifests, voice, metadata_sha = _source(
                pipe, s3, bucket, original, states['audio']['policy'])
            _require(metadata_sha == qualification['commitments']['source_metadata_sha256']
                     and _hash(contract['candidate']) == qualification['commitments']['immutable_core_sha256'])
            cut_record = shared._cut_record(s3, bucket, link, original, fingerprint, manifests, voice)
            audit = recovery._read_record(s3, audit_pointer, 'audit')
            _require(audit_pointer['source_task_id'] == continuity.LEAF_ID
                     and type(audit.get('package')) is dict
                     and _hash(audit['package']) == cut_record['package_sha256']
                     and _raw(recovery._story_fields(audit['package'])) == _raw(contract['candidate']))
            cuts._read_private(s3, bucket, {key: audit_pointer[key] for key in ('key', 'sha256', 'size')}
                               | {'content_type': 'application/json'})
            samples = shared._samples(link, bodies['prepared'], cut_record)
            scene_visuals = [[{'path': f'scene-{index:02d}.mp4', 'generated': True,
                'source_type': 'generated', 'generation_provider': row['raw']['provider'],
                'start_fraction': 0.0, 'preserve_start_fraction': True, 'forbid_loop': True}]
                for index, row in enumerate(cut_record['cuts'])]
            visual = visual_semantics.derive_strict_visual_review_contract(contract['candidate']['scenes'],
                scene_visuals, samples=samples, topic=original['spec']['topic'],
                story_scenes=contract['candidate']['scenes'], content_style=options.get('content_style', ''),
                evidence_sources=contract['candidate'].get('sources') or [])
            _require(shared._request_bytes(visual['request'],
                schema_compat=continuation.uses_schema_compatibility(captured_story_continuation)) == bodies['prepared'],
                     'retained_captured_story_visual_request_changed')
            parsed = adapter._parse_response_payload(bodies['response'],
                schema=visual['request']['json_schema'], max_tokens=visual['request']['max_tokens'])
            _require(_raw(parsed['result']) == bodies['result']
                     and _raw(parsed['usage']) == _raw(manifest['evidence']['usage']))
            rows = shared._visual_component(parsed['result'], visual, options['quality_threshold'])
            commitments = {'continuation_manifest_sha256': _sha(control_raw),
                'continuation_journal_sha256': _hash(pipe.hgetall(control_keys[1])),
                'continuation_anchor_sha256': pipe.get(control_keys[2]),
                'predecessor_snapshot_sha256': control['predecessors']['snapshot_sha256'],
                'story_evidence_sha256': _hash(qualification), 'story_predecessor': tag,
                'journal_keys': list(keys), 'journal_state_sha256': _hash(state),
                'policy_sha256': _hash(state['policy']), 'continuity_sha256': _hash(source),
                'source_state_sha256': fingerprint, 'source_spec_sha256': _hash(original['spec']),
                'source_journal_sha256': _hash(original['generated_asset_candidates']),
                'source_metadata_sha256': metadata_sha, 'audio': states['audio']['policy']['audio'],
                'visual_anchor_sha256': _hash(anchor), 'visual_manifest': anchor['manifest'],
                'sampled_link_anchor_key': link_key, 'sampled_link_anchor_sha256': _hash(link_anchor),
                'sampled_link': link_anchor['pointer'], 'cut_manifest': link['cut_manifest'],
                'cut_package_sha256': cut_record['package_sha256'],
                'immutable_core_sha256': _hash(contract['candidate']), 'audit_pointer': deepcopy(audit_pointer),
                'cuts': cut_record['objects'], 'historical_build': cut_record['build']}
            record = {'version': 1, 'kind': 'retained_captured_story_visual_component_evidence', **_FLAGS,
                'component_pass': True, 'preclaim_only': True, 'journal_read_acknowledged': True,
                'captured_story_read_acknowledged': True, 'visual_settlement_observed': True,
                'sampled_wire_linkage_verified': True, 'commitments': commitments,
                'visual_diagnostics': [rows[index] for index in range(6)]}
            encoded = _raw(record)
            cuts._storage_guard(s3, bucket)
            artifacts._ack_read(pipe)
        value = object.__new__(RetainedCapturedStoryVisualEvidence)
        _ISSUED[value] = encoded
        return value
    except RetainedCapturedStoryVisualEvidenceError:
        raise
    except Exception:
        raise RetainedCapturedStoryVisualEvidenceError('retained_captured_story_visual_read_unverified') from None
