"""Private, create-only persistence of acknowledged retained router captures.

Only the actual owning runtime scope supplies the journal and source authority.
Full prepared/wire/response/result bytes remain separate bounded objects. A
manifest binds them to the complete unchanged source and acknowledged journal;
only private readback plus a permanent NX anchor ACK returns a receipt.

No provider, admission, reset, retry, semantic review or consumer grant lives
here. Source pointers bind original/candidate packages, voice and six manifests;
raw media, selected cuts/JPEGs and their rendering identity remain unverified.
Owner/Tigris-admin ACL checks do not prove bucket-policy or CDN privacy. Trusted
storage/Redis replacement or coordinated rollback needs external reconciliation.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
import threading
import weakref

from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_review_runtime as runtime
from app.services import production_connection_continuity as continuity, storage
from app.services.retained_audio_review_evidence import _source_acl_shape


_STORY, _VISUAL = journal.PURPOSES
_PREFIX = journal.STATE_KEY.rsplit(':', 1)[0]
STORY_ANCHOR_KEY = _PREFIX + ':story_artifact:v1'
VISUAL_ANCHOR_KEY = _PREFIX + ':visual_artifact:v1'
MAX_MANIFEST_BYTES = 512 * 1024
_LIMITS = {'prepared': adapter.MAX_REQUEST_BYTES, 'wire': adapter.MAX_REQUEST_BYTES,
           'response': adapter.MAX_RESPONSE_BYTES, 'result': adapter.MAX_RESPONSE_BYTES,
           'manifest': MAX_MANIFEST_BYTES}
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'full_qa_complete': False, 'requires_full_qa': True}
_TIGRIS_ENDPOINTS = frozenset(('https://t3.storageapi.dev', 'https://t3.storage.dev',
                             'https://fly.storage.tigris.dev'))
_TIGRIS_ADMINS = {'Grantee': {'Type': 'Group', 'URI': 'https://groups.tigris.dev/org/admins'},
                 'Permission': 'FULL_CONTROL'}
# Weak references keep the fence across Sink instances without retaining private
# scopes or their captures after exit. No external reservation/key is created.
_ATTEMPTS = {}
_ATTEMPT_LOCK = threading.Lock()


class RouterReviewArtifactError(RuntimeError):
    """Fixed local failure without source text, body, URL or credentials."""


def _require(value, code='router_review_artifact_invalid'):
    if not value:
        raise RouterReviewArtifactError(code)


def _raw(value, maximum=MAX_MANIFEST_BYTES):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    _require(len(raw) <= maximum, 'router_review_artifact_too_large')
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(_raw(value))


def _object(raw, maximum=MAX_MANIFEST_BYTES):
    _require(type(raw) in (bytes, str))
    raw = raw.encode('utf-8') if type(raw) is str else raw
    _require(0 < len(raw) <= maximum, 'router_review_artifact_too_large')
    value = adapter._json_loads(raw.decode('utf-8'))
    _require(type(value) is dict)
    return value


def _flags(value):
    return all(value.get(name) is expected for name, expected in _FLAGS.items())


def _journal_keys(actual):
    _require(type(actual) is journal.RouterReviewJournal, 'router_review_artifact_journal_invalid')
    keys = actual.keys
    _require(type(keys) is tuple and len(keys) == 3 and len(set(keys)) == 3
             and all(type(key) is str and 1 <= len(key) <= 512 for key in keys)
             and keys[0].endswith(':state'), 'router_review_artifact_journal_invalid')
    return keys


def _anchor_keys(keys):
    prefix = keys[0].rsplit(':', 1)[0]
    return {_STORY: prefix + ':story_artifact:v1', _VISUAL: prefix + ':visual_artifact:v1'}


def _captured_keys(keys):
    from app.services.retained_review_captured_story_continuation import VISUAL_KEYS
    from app.services.retained_visual_schema_repair import VISUAL_KEYS as CORRECTED_VISUAL_KEYS
    return keys in (VISUAL_KEYS, CORRECTED_VISUAL_KEYS)


def _captured_tag(value, keys):
    """Validate the closed diagnostic tag, without treating its JSON as a grant."""
    from app.services import retained_review_captured_story_continuation as continuation
    _require(_captured_keys(keys) and type(value) is dict and set(value) == {
        'version', 'kind', 'continuation_manifest_sha256', 'evidence'}
        and type(value['version']) is int and value['version'] == 1
        and value['kind'] == 'captured_transport_story'
        and type(value['continuation_manifest_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', value['continuation_manifest_sha256']) is not None,
        'router_review_artifact_captured_predecessor_invalid')
    continuation._qualification(value['evidence'])
    return value


def captured_story_predecessor_tag(scope, story_evidence):
    """Derive a tag only from the actual bound predecessor in this owner scope."""
    from app.services import retained_captured_story_scope as captured_scope
    from app.services import retained_review_captured_story_continuation as continuation
    _require(runtime._artifact_scope() is scope,
             'router_review_artifact_captured_predecessor_invalid')
    bound = captured_scope.captured_story_predecessor(scope)
    _require(story_evidence is bound, 'router_review_artifact_captured_predecessor_invalid')
    cap = scope.journal._captured_story_continuation
    manifest = continuation._checked(cap)
    evidence = continuation._evidence(bound)
    keys = _journal_keys(scope.journal)
    _require(keys == continuation.selected_keys(cap, 'story')
             and _raw(evidence) == _raw(manifest['story_qualification']),
             'router_review_artifact_captured_predecessor_changed')
    return _captured_tag({'version': 1, 'kind': 'captured_transport_story',
        'continuation_manifest_sha256': cap.receipt['manifest_sha256'], 'evidence': evidence}, keys)


def _mark_attempt(scope, purpose):
    identity = id(scope)
    def remove(reference):
        with _ATTEMPT_LOCK:
            if _ATTEMPTS.get(identity, (None,))[0] is reference:
                _ATTEMPTS.pop(identity, None)
    with _ATTEMPT_LOCK:
        current = _ATTEMPTS.get(identity)
        if current is None or current[0]() is not scope:
            current = (weakref.ref(scope, remove), set())
            _ATTEMPTS[identity] = current
        _require(purpose not in current[1], 'router_review_artifact_already_attempted')
        current[1].add(purpose)


@dataclass(frozen=True, repr=False, init=False, slots=True)
class PersistedRouterReviewArtifact:
    """Detached storage receipt; never semantic, render, QA or publish approval."""
    _receipt_bytes: bytes

    def __new__(cls, *args, **kwargs):
        raise TypeError('router_review_artifact_receipt_private')

    def __repr__(self):
        return '<PersistedRouterReviewArtifact diagnostic redacted>'

    @property
    def receipt(self):
        return _object(self._receipt_bytes)

    @property
    def diagnostic_only(self):
        return True

    @property
    def qa_approved(self):
        return False

    @property
    def publish_eligible(self):
        return False


def _receipt(anchor):
    result = {'version': 1, 'purpose': anchor['purpose'], 'source_task_id': continuity.LEAF_ID,
            'anchor_key': _anchor_keys(anchor['journal_keys'])[anchor['purpose']], 'anchor_sha256': _hash(anchor),
            'manifest': anchor['manifest'], **_FLAGS}
    if _captured_keys(tuple(anchor['journal_keys'])):
        result.update(version=2, story_predecessor=_captured_tag(anchor['story_predecessor'], tuple(anchor['journal_keys'])))
    return result


def _pointer(purpose, kind, raw, keys):
    _require(type(raw) is bytes and 0 < len(raw) <= _LIMITS[kind], 'router_review_artifact_too_large')
    digest = _sha(raw)
    namespace = _hash(list(keys))
    return {'key': f'recovery/{continuity.LEAF_ID}/included_router_review/v1/{namespace}/{purpose}/{kind}/{digest}.json',
            'sha256': digest, 'size': len(raw), 'content_type': 'application/json'}


def _checked_pointer(value, purpose, kind, keys):
    namespace = _hash(list(keys))
    _require(type(value) is dict and set(value) == {'key', 'sha256', 'size', 'content_type'}
             and type(value['size']) is int and 0 < value['size'] <= _LIMITS[kind]
             and type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256'])
             and value['content_type'] == 'application/json'
             and value['key'] == f"recovery/{continuity.LEAF_ID}/included_router_review/v1/{namespace}/{purpose}/{kind}/{value['sha256']}.json",
             'router_review_artifact_pointer_invalid')
    return value


def _scope(artifact, expected=None, keys=None):
    _require(type(artifact) is runtime.AcknowledgedRouterReview, 'router_review_artifact_capture_required')
    scope = runtime._artifact_scope()
    _require(expected is None or scope is expected, 'router_review_artifact_scope_changed')
    _require(artifact.purpose in journal.PURPOSES, 'router_review_artifact_capture_required')
    _require(keys is None or _journal_keys(scope.journal) == keys, 'router_review_artifact_journal_changed')
    return scope


def _state(pipe, scope, artifact, keys):
    _scope(artifact, scope, keys)
    pipe.watch(*keys, *_anchor_keys(keys).values())
    state = scope.journal._read(pipe)
    # Settlement accepts late responses. Persistence checks historical state and
    # current source, never renews entitlement or pretends to make a new request.
    source = continuity._derive(pipe, state['policy']['profile_revision'])
    _require(_hash(source) == state['policy']['continuity_sha256']
             and all(source[name] == state['policy'][name] for name in (
                 'old_connection_id', 'current_connection_id')), 'router_review_artifact_source_changed')
    if _captured_keys(keys):
        from app.services.retained_captured_story_scope import captured_story_predecessor
        _require(artifact.purpose == _VISUAL and pipe.exists(_anchor_keys(keys)[_STORY]) == 0,
                 'router_review_artifact_captured_predecessor_invalid')
        captured_story_predecessor_tag(scope, captured_story_predecessor(scope))
        expected = {_VISUAL}
    else:
        expected = {_STORY} if artifact.purpose == _STORY else {_STORY, _VISUAL}
    _require(set(state['slots']) == expected and all(
        state['slots'][purpose]['response'] is not None for purpose in expected),
        'router_review_artifact_slot_unacknowledged')
    return state, source


def _ack_read(pipe):
    pipe.multi()
    pipe.ping()
    ack = pipe.execute()
    _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
             'router_review_artifact_read_ack_uncertain')


def _validate_capture(purpose, bodies, reservation, evidence, status, state):
    slot = state['slots'][purpose]
    expected = journal._receipt(state['policy'], purpose, slot)
    _require(_raw(reservation) == _raw({**expected, 'reservation_sha256': journal._hash(expected)})
             and _raw(evidence) == _raw(slot['response']['evidence']),
             'router_review_artifact_journal_mismatch')
    prepared = _object(bodies['prepared'], _LIMITS['prepared'])
    wire = _object(bodies['wire'], _LIMITS['wire'])
    response = _object(bodies['response'], _LIMITS['response'])
    result = _object(bodies['result'], _LIMITS['result'])
    _require(_raw(prepared, _LIMITS['prepared']) == bodies['prepared']
             and _raw(wire, _LIMITS['wire']) == bodies['prepared']
             and _raw(result, _LIMITS['result']) == bodies['result']
             and _sha(_raw({'version': 1, 'method': 'POST', 'endpoint': adapter.ENDPOINT,
                            'body': prepared}, _LIMITS['prepared'] + 1024)) == evidence['request_sha256']
             and _sha(bodies['wire']) == evidence['wire_body_sha256']
             and _sha(bodies['response']) == evidence['response_body_sha256']
             and _sha(bodies['result']) == evidence['parsed_result_sha256']
             and type(status) is int and 200 <= status < 300 and status == evidence['status_code']
             and response['model'] == evidence['returned_model']
             and _raw(_object(response['choices'][0]['message']['content'], _LIMITS['result']),
                      _LIMITS['result']) == bodies['result'], 'router_review_artifact_body_mismatch')


class RetainedRouterReviewArtifactSink:
    def __init__(self, s3_client, *, bucket):
        self._s3, self._bucket = s3_client, bucket
        self._keys = None
        try:
            self._client_guard()
        except RouterReviewArtifactError:
            raise
        except Exception:
            raise RouterReviewArtifactError('router_review_artifact_storage_unavailable') from None

    def __repr__(self):
        return '<RetainedRouterReviewArtifactSink private>'

    def _client_guard(self):
        meta = getattr(self._s3, 'meta', None)
        retries = getattr(getattr(meta, 'config', None), 'retries', None)
        _require(type(retries) is dict and (
            type(retries.get('total_max_attempts')) is int and retries['total_max_attempts'] == 1
            if 'total_max_attempts' in (retries or {}) else
            type(retries.get('max_attempts')) is int and retries['max_attempts'] == 0),
            'router_review_artifact_client_retry_policy_invalid')
        _require(type(self._bucket) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,62}', self._bucket)
                 and self._bucket == storage.settings.bucket
                 and type(getattr(meta, 'endpoint_url', None)) is str
                 and meta.endpoint_url == storage.settings.endpoint,
                 'router_review_artifact_storage_changed')

    def _read_blob(self, pointer, purpose, kind):
        self._client_guard()
        pointer = _checked_pointer(pointer, purpose, kind, self._keys)
        response = self._s3.get_object(Bucket=self._bucket, Key=pointer['key'])
        body = response.get('Body')
        try:
            status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            _require(type(status) is int and status == 200
                     and type(response.get('ContentLength')) is int
                     and response['ContentLength'] == pointer['size']
                     and response.get('ContentType') == 'application/json', 'router_review_artifact_blob_invalid')
            chunks, size = [], 0
            while True:
                chunk = body.read(min(64 * 1024, pointer['size'] - size + 1))
                _require(type(chunk) is bytes, 'router_review_artifact_blob_invalid')
                if not chunk:
                    break
                size += len(chunk)
                _require(size <= pointer['size'], 'router_review_artifact_too_large')
                chunks.append(chunk)
            raw = b''.join(chunks)
            _require(size == pointer['size'] and _sha(raw) == pointer['sha256'],
                     'router_review_artifact_blob_changed')
        finally:
            if body is not None:
                body.close()
        self._client_guard()
        acl = self._s3.get_object_acl(Bucket=self._bucket, Key=pointer['key'])
        _source_acl_shape(acl, code='router_review_artifact_blob_not_private')
        owner, grants = acl['Owner'], acl['Grants']
        owner_grants = [grant for grant in grants if grant['Permission'] == 'FULL_CONTROL'
            and grant['Grantee'].get('Type') == 'CanonicalUser' and grant['Grantee'].get('ID') == owner['ID']]
        _require(len(owner_grants) == 1 and len(grants) in (1, 2), 'router_review_artifact_blob_not_private')
        if len(grants) == 2:
            _require(self._s3.meta.endpoint_url in _TIGRIS_ENDPOINTS
                     and sum(grant == _TIGRIS_ADMINS for grant in grants) == 1,
                     'router_review_artifact_blob_not_private')
        return raw

    def _put_blob(self, raw, purpose, kind, scope, artifact):
        _scope(artifact, scope, self._keys)
        self._client_guard()
        pointer = _pointer(purpose, kind, raw, self._keys)
        response = self._s3.put_object(Bucket=self._bucket, Key=pointer['key'], Body=raw,
            ContentLength=len(raw), ContentType='application/json', CacheControl='private, no-store',
            Metadata={'sha256': pointer['sha256']}, ACL='private', IfNoneMatch='*')
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        _require(type(status) is int and status == 200, 'router_review_artifact_put_ack_uncertain')
        _require(self._read_blob(pointer, purpose, kind) == raw, 'router_review_artifact_blob_changed')
        _scope(artifact, scope, self._keys)
        return pointer

    def _read_anchor(self, pipe, purpose):
        key = _anchor_keys(self._keys)[purpose]
        ttl = pipe.pttl(key)
        _require(type(ttl) is int and ttl == -1, 'router_review_artifact_anchor_not_durable')
        anchor = _object(pipe.get(key))
        captured = _captured_keys(self._keys)
        _require(set(anchor) == {'version', 'kind', 'purpose', 'original_task_id', 'source_task_id',
            'policy_sha256', 'continuity_sha256', 'journal_state_sha256', 'manifest',
            'prior_story_anchor_sha256', 'journal_keys', *_FLAGS} | ({'story_predecessor'} if captured else set())
            and type(anchor['version']) is int and anchor['version'] == (2 if captured else 1)
            and anchor['kind'] == 'retained_router_review_artifact_anchor' and anchor['purpose'] == purpose
            and anchor['original_task_id'] == continuity.ROOT_ID and anchor['source_task_id'] == continuity.LEAF_ID
            and anchor['journal_keys'] == list(self._keys)
            and _flags(anchor), 'router_review_artifact_anchor_invalid')
        if captured:
            _require(purpose == _VISUAL and anchor['prior_story_anchor_sha256'] is None,
                     'router_review_artifact_captured_predecessor_invalid')
            _captured_tag(anchor['story_predecessor'], self._keys)
        _checked_pointer(anchor['manifest'], purpose, 'manifest', self._keys)
        return anchor

    def _prior(self, pipe, state, source, prior):
        _require(type(prior) is PersistedRouterReviewArtifact, 'router_review_artifact_prior_required')
        anchor = self._read_anchor(pipe, _STORY)
        _require(_raw(prior.receipt) == _raw(_receipt(anchor)), 'router_review_artifact_prior_changed')
        previous = deepcopy(state)
        previous['slots'] = {_STORY: state['slots'][_STORY]}
        previous['updated_at'] = state['slots'][_STORY]['response']['observed_at']
        # Keep the optional complete reconfirmation history in the predecessor.
        _require(anchor['journal_state_sha256'] == _hash(previous)
                 and anchor['policy_sha256'] == _hash(state['policy'])
                 and anchor['continuity_sha256'] == _hash(source)
                 and anchor['prior_story_anchor_sha256'] is None, 'router_review_artifact_prior_changed')
        manifest = _object(self._read_blob(anchor['manifest'], _STORY, 'manifest'))
        _require(set(manifest) == {'version', 'kind', 'purpose', 'source', 'journal_state', 'reservation',
            'evidence', 'response_status_code', 'bodies', 'prior_story_anchor_sha256', 'journal_keys', *_FLAGS}
            and type(manifest['version']) is int and manifest['version'] == 1
            and manifest['kind'] == 'retained_router_review_artifact' and manifest['purpose'] == _STORY
            and _flags(manifest) and manifest['prior_story_anchor_sha256'] is None
            and manifest['journal_keys'] == list(self._keys)
            and _raw(manifest['source']) == _raw(source)
            and _raw(manifest['journal_state']) == _raw(previous)
            and type(manifest['bodies']) is dict and set(manifest['bodies']) == set(_LIMITS) - {'manifest'},
            'router_review_artifact_prior_changed')
        bodies = {kind: self._read_blob(pointer, _STORY, kind) for kind, pointer in manifest['bodies'].items()}
        _validate_capture(_STORY, bodies, manifest['reservation'], manifest['evidence'],
                          manifest['response_status_code'], previous)
        return anchor

    def persist(self, artifact, *, prior_story_anchor=None, captured_story_predecessor=None):
        """One scope/purpose attempt; incomplete writes remain non-authorizing."""
        scope = None
        try:
            scope = _scope(artifact)
            purpose = artifact.purpose
            _mark_attempt(scope, purpose)
            keys = _journal_keys(scope.journal)
            _require(self._keys is None or self._keys == keys, 'router_review_artifact_journal_changed')
            self._keys = keys
            anchors = _anchor_keys(keys)
            self._client_guard()
            captured = _captured_keys(keys)
            if captured:
                _require(purpose == _VISUAL and prior_story_anchor is None,
                         'router_review_artifact_captured_predecessor_invalid')
                predecessor_tag = captured_story_predecessor_tag(scope, captured_story_predecessor)
            else:
                _require(captured_story_predecessor is None
                         and (prior_story_anchor is None) is (purpose == _STORY),
                         'router_review_artifact_prior_required')
                predecessor_tag = None
            bodies = {'prepared': artifact.prepared_body_bytes, 'wire': artifact.request_body_bytes,
                      'response': artifact.response_body_bytes, 'result': _raw(artifact.result, _LIMITS['result'])}
            reservation, evidence, status = artifact.reservation, artifact.evidence, artifact.response_status_code
            with scope.journal.client.pipeline() as pipe:
                state, source = _state(pipe, scope, artifact, keys)
                _require(pipe.exists(anchors[purpose]) == 0
                         and (purpose != _STORY or pipe.exists(anchors[_VISUAL]) == 0),
                         'router_review_artifact_anchor_exists')
                _validate_capture(purpose, bodies, reservation, evidence, status, state)
                prior = self._prior(pipe, state, source, prior_story_anchor) if purpose == _VISUAL and not captured else None
                _ack_read(pipe)
            prior_sha = _hash(prior) if prior is not None else None
            pointers = {kind: self._put_blob(raw, purpose, kind, scope, artifact) for kind, raw in bodies.items()}
            manifest = {'version': 1, 'kind': 'retained_router_review_artifact', 'purpose': purpose,
                'source': source, 'journal_state': state, 'reservation': reservation, 'evidence': evidence,
                'response_status_code': status, 'bodies': pointers, 'prior_story_anchor_sha256': prior_sha,
                'journal_keys': list(keys), **_FLAGS}
            if captured:
                manifest.update(version=2, story_predecessor=predecessor_tag)
            manifest_pointer = self._put_blob(_raw(manifest), purpose, 'manifest', scope, artifact)
            anchor = {'version': 1, 'kind': 'retained_router_review_artifact_anchor', 'purpose': purpose,
                'original_task_id': continuity.ROOT_ID, 'source_task_id': continuity.LEAF_ID,
                'policy_sha256': _hash(state['policy']), 'continuity_sha256': _hash(source),
                'journal_state_sha256': _hash(state), 'manifest': manifest_pointer,
                'prior_story_anchor_sha256': prior_sha, 'journal_keys': list(keys), **_FLAGS}
            if captured:
                anchor.update(version=2, story_predecessor=predecessor_tag)
            with scope.journal.client.pipeline() as pipe:
                current, current_source = _state(pipe, scope, artifact, keys)
                _require(_raw(current) == _raw(state) and _raw(current_source) == _raw(source)
                         and pipe.exists(anchors[purpose]) == 0, 'router_review_artifact_source_changed')
                if prior is not None:
                    _require(_raw(self._read_anchor(pipe, _STORY)) == _raw(prior), 'router_review_artifact_prior_changed')
                if captured:
                    _require(_raw(captured_story_predecessor_tag(scope, captured_story_predecessor)) == _raw(predecessor_tag),
                             'router_review_artifact_captured_predecessor_changed')
                _scope(artifact, scope, keys)
                pipe.multi()
                pipe.set(anchors[purpose], _raw(anchor).decode('utf-8'), nx=True)
                ack = pipe.execute()
                _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                         'router_review_artifact_anchor_ack_uncertain')
            with scope.journal.client.pipeline() as pipe:
                current, current_source = _state(pipe, scope, artifact, keys)
                _require(_raw(current) == _raw(state) and _raw(current_source) == _raw(source)
                         and _raw(self._read_anchor(pipe, purpose)) == _raw(anchor),
                         'router_review_artifact_anchor_readback_changed')
                if prior is not None:
                    _require(_raw(self._read_anchor(pipe, _STORY)) == _raw(prior), 'router_review_artifact_prior_changed')
                if captured:
                    _require(_raw(captured_story_predecessor_tag(scope, captured_story_predecessor)) == _raw(predecessor_tag),
                             'router_review_artifact_captured_predecessor_changed')
                _ack_read(pipe)
            _scope(artifact, scope, keys)
            receipt = object.__new__(PersistedRouterReviewArtifact)
            object.__setattr__(receipt, '_receipt_bytes', _raw(_receipt(anchor)))
            return receipt
        except RouterReviewArtifactError:
            if scope is not None:
                scope.failed = True
            raise
        except Exception:
            if scope is not None:
                scope.failed = True
            raise RouterReviewArtifactError('router_review_artifact_write_or_outcome_unverified') from None
