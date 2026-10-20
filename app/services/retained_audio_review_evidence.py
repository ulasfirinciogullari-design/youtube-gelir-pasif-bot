"""Read-only verification of original retained audio diagnostic artifacts.

The v1 operator sink persists full parsed results, receipts and diagnostics.
This reader checks them against durable journal/anchors, watched original source
metadata and the complete original MP3 decode. It never constructs a live HTTPX
observation or grants a request. Historical reservations must be inside their
original policy window; reading settled evidence after expiry does not extend
that window. Continuity still requires the original idle, unclaimed lineage.
Story/visual, edit-duration and final-render QA remain separate.

An optional closed successor or completion plan selects its existing audio
journal and artifact anchors. Its watched read also verifies the controller, both child
heads, exact legacy records and archived replacement. Selection never renews
an entitlement or reinterprets an old unknown reservation.

Trusted Redis and configured storage are evidence authorities, not cryptographic
witnesses of provider receipt. Coordinated replacement/rollback of all external
records cannot be detected here. Private ACL checks retain the sink's known
owner/Tigris-admin contract; bucket policy/CDN privacy remains operator-owned
and is neither verified nor changed here.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from app.services import audio_qc, storage
from app.services import abacus_router_audio_adapter as adapter
from app.services import abacus_router_audio_review_journal as audio_journal
from app.services import production_connection_continuity as continuity
from app.services.whisper_transcription import inspect_bounded_short_audio

_PREFIX = audio_journal.STATE_KEY.rsplit(':', 1)[0]
ASR_ANCHOR_KEY = _PREFIX + ':asr_artifact'
FINAL_ANCHOR_KEY = _PREFIX + ':final_artifact'
MAX_RECORD_BYTES = 512 * 1024
_TIGRIS_ENDPOINTS = frozenset(('https://t3.storageapi.dev', 'https://t3.storage.dev',
                             'https://fly.storage.tigris.dev'))
_TIGRIS_ADMINS = 'https://groups.tigris.dev/org/admins'
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False}
_DIAGNOSTIC_FLAGS = {'version': 1, **_FLAGS, 'full_qa_complete': False,
                     'edit_duration_qa_complete': False, 'journal_acknowledged': False}
_ASR, _PROSODY = adapter.AudioReviewPurpose
_WATCH = (audio_journal.STATE_KEY, audio_journal.JOURNAL_KEY, audio_journal.ANCHOR_KEY,
          ASR_ANCHOR_KEY, FINAL_ANCHOR_KEY)


class RetainedAudioEvidenceError(RuntimeError):
    """Fixed local rejection; no source, response body or storage credentials."""


def _require(value, code='retained_audio_evidence_invalid'):
    if not value:
        raise RetainedAudioEvidenceError(code)


def _raw(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    _require(len(raw) <= MAX_RECORD_BYTES, 'audio_evidence_too_large')
    return raw

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

def _hash(value):
    return _sha(_raw(value))

def _object(raw):
    _require(type(raw) in (bytes, str)
             and len(raw.encode('utf-8') if type(raw) is str else raw) <= MAX_RECORD_BYTES)
    def pairs(items):
        out = {}
        for key, value in items:
            _require(key not in out)
            out[key] = value
        return out
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    _raw(value)
    return value

def _flags(value):
    return all(value.get(name) is expected for name, expected in _FLAGS.items())

def _source_acl_shape(acl, *, code='audio_evidence_source_acl_invalid'):
    """Recognize a bounded ACL API response without classifying its grants."""
    _require(type(acl) is dict and {'Owner', 'Grants', 'ResponseMetadata'} <= set(acl)
             and set(acl) <= {'Owner', 'Grants', 'ResponseMetadata', 'RequestCharged'}
             and len(_raw(acl)) <= 64 * 1024, code)
    metadata, owner, grants = acl['ResponseMetadata'], acl['Owner'], acl['Grants']
    _require(type(metadata) is dict and type(metadata.get('HTTPStatusCode')) is int
             and metadata['HTTPStatusCode'] == 200
             and type(owner) is dict and {'ID'} <= set(owner) <= {'ID', 'DisplayName'}
             and type(owner['ID']) is str and 1 <= len(owner['ID']) <= 256
             and ('DisplayName' not in owner or type(owner['DisplayName']) is str
                  and len(owner['DisplayName']) <= 256)
             and type(grants) is list and len(grants) <= 100
             and ('RequestCharged' not in acl or acl['RequestCharged'] == 'requester'), code)
    identities = {'CanonicalUser': ('ID', 256), 'Group': ('URI', 2048),
                  'AmazonCustomerByEmail': ('EmailAddress', 256)}
    for grant in grants:
        _require(type(grant) is dict and set(grant) == {'Grantee', 'Permission'}
                 and type(grant['Permission']) is str
                 and grant['Permission'] in {'FULL_CONTROL', 'WRITE', 'WRITE_ACP', 'READ', 'READ_ACP'}
                 and type(grant['Grantee']) is dict, code)
        grantee = grant['Grantee']
        kind = grantee.get('Type')
        _require(type(kind) is str and kind in identities, code)
        field, maximum = identities[kind]
        _require({'Type', field} <= set(grantee) <= {'Type', field, 'DisplayName'}
                 and type(grantee[field]) is str and 1 <= len(grantee[field]) <= maximum
                 and ('DisplayName' not in grantee or type(grantee['DisplayName']) is str
                      and len(grantee['DisplayName']) <= 256), code)

def _asr_diagnostic(result, evidence, expected):
    _require(adapter._matches_schema(result, adapter._ASR_SCHEMA) and result['language'] == 'tr')
    adapter._asr_timing(result, evidence['audio'])
    comparison = audio_qc._require_word_timing_evidence(audio_qc.compare_transcript(
        expected, result['text'], language_code='tr', words=result['words'],
        provider='abacus_router', comparison_language='tr'), 'Abacus router')
    _require(comparison['available'] is True and comparison['pass'] is True and comparison['score'] == 100
             and comparison['mismatch_details']['exact_match'] is True
             and comparison['mismatch_details']['timestamp_sequence_match'] is True
             and audio_qc._validated_prosody_timestamp_evidence(comparison, allow_coarse=False) is not None,
             'audio_evidence_asr_rejected')
    audio = evidence['audio']
    return {**_DIAGNOSTIC_FLAGS, 'kind': 'retained_router_asr_diagnostic',
        'provider': 'abacus_router', 'component_pass': True,
        'expected_narration_sha256': _sha(expected.encode('utf-8')),
        'audio': audio, 'audio_duration_seconds': audio['decoded_samples'] / audio['decoded_sample_rate'],
        'word_timing_consistent': True, 'comparison': comparison,
        'comparison_sha256': _hash(comparison), 'observation': evidence}

def _validate_record(record, state):
    _require(type(record) is dict and set(record) == {'version', 'kind', 'source', 'reviews', *_FLAGS}
             and type(record['version']) is int and record['version'] == 1
             and record['kind'] in ('asr', 'final') and _flags(record))
    policy, source = state['policy'], record['source']
    _require(type(source) is dict and set(source) == {'source_task_id', 'policy_sha256', 'continuity_sha256',
             'expected_narration', 'expected_narration_sha256', 'audio'}
             and source['source_task_id'] == continuity.LEAF_ID
             and source['policy_sha256'] == _hash(policy)
             and source['continuity_sha256'] == policy['continuity_sha256']
             and type(source['expected_narration']) is str
             and 0 < len(source['expected_narration'].encode('utf-8')) <= adapter.MAX_METADATA_BYTES
             and source['expected_narration_sha256'] == policy['expected_narration_sha256']
             == _sha(source['expected_narration'].encode('utf-8'))
             and _raw(source['audio']) == _raw(policy['audio']), 'audio_evidence_source_changed')
    purposes = {_ASR.value} if record['kind'] == 'asr' else {_ASR.value, _PROSODY.value}
    reviews = record['reviews']
    _require(type(reviews) is dict and set(reviews) == purposes and purposes <= set(state['slots']))
    for purpose, review in reviews.items():
        _require(type(review) is dict and set(review) == {'reservation', 'settlement', 'diagnostic'})
        slot = state['slots'][purpose]
        _require(slot['response'] is not None, 'audio_evidence_response_unacknowledged')
        receipt = audio_journal._receipt(policy, purpose, slot)
        expected_receipt = {**receipt, 'reservation_sha256': _hash(receipt), **_FLAGS}
        _require(_raw(review['reservation']) == _raw(expected_receipt), 'audio_evidence_reservation_changed')
        settled = review['settlement']
        _require(type(settled) is dict and set(settled) == {'result', 'evidence', *_FLAGS}
                 and _flags(settled) and _raw(settled['evidence']) == _raw(slot['response']['evidence'])
                 and _hash(settled['result']) == slot['response']['evidence']['parsed_result_sha256'],
                 'audio_evidence_settlement_changed')
    first = reviews[_ASR.value]['settlement']
    asr = _asr_diagnostic(first['result'], first['evidence'], source['expected_narration'])
    _require(_raw(reviews[_ASR.value]['diagnostic']) == _raw(asr), 'audio_evidence_diagnostic_changed')
    if record['kind'] == 'final':
        binding = audio_journal._asr_binding(policy, state['slots'][_ASR.value],
                                            source['expected_narration'], first['result'])
        _require(_raw(binding) == _raw(state['slots'][_PROSODY.value]['asr_binding']),
                 'audio_evidence_asr_binding_changed')
        second = reviews[_PROSODY.value]['settlement']
        output = second['result']
        _require(adapter._matches_schema(output, audio_qc._PROSODY_REVIEW_SCHEMA))
        prosody = audio_qc._validate_prosody_review(output, source['expected_narration'],
            audio_duration_seconds=asr['audio_duration_seconds'], transcript_evidence=asr['comparison'],
            language='tr', provider='abacus_router')
        _require(prosody is not None, 'audio_evidence_prosody_invalid')
        prosody['review_attempts'] = 1
        diagnostic = {**_DIAGNOSTIC_FLAGS, 'kind': 'retained_router_prosody_diagnostic',
            'provider': 'abacus_router', 'component_pass': prosody['pass'] is True,
            'expected_narration_sha256': asr['expected_narration_sha256'], 'audio': asr['audio'],
            'audio_duration_seconds': asr['audio_duration_seconds'],
            'rubric_sha256': _sha(audio_qc._PROSODY_SYSTEM_INSTRUCTION.encode('utf-8')),
            'schema_sha256': _hash(audio_qc._PROSODY_REVIEW_SCHEMA),
            'asr_binding': {name: first['evidence'][name] for name in
                ('request_sha256', 'parsed_result_sha256', 'response_proof_sha256')},
            'asr_comparison': asr['comparison'], 'prosody': prosody, 'observation': second['evidence']}
        diagnostic['asr_binding']['comparison_sha256'] = asr['comparison_sha256']
        _require(_raw(reviews[_PROSODY.value]['diagnostic']) == _raw(diagnostic), 'audio_evidence_diagnostic_changed')
        _require(prosody['pass'] is True, 'audio_evidence_prosody_rejected')

def _source(pipe, policy):
    """Recheck source identity, without renewing historical request entitlement."""
    record = continuity._derive(pipe, policy['profile_revision'])
    return _source_pointer(record, policy)


def _source_pointer(record, policy):
    """Validate an audio pointer in already authenticated source data."""
    candidates = record['legacy_candidates']
    pointer = candidates['audio_pointer']
    _require(_hash(record) == policy['continuity_sha256']
             and all(record[field] == policy[field] for field in ('old_connection_id', 'current_connection_id'))
             and all(row['spec']['language'] == 'tr' for row in record['lineage'])
             and _hash(pointer) == policy['audio_checkpoint_sha256']
             and pointer['metadata_sha256'] == policy['source_metadata_sha256']
             and pointer['audio_sha256'] == policy['audio']['sha256']
             and pointer['size'] == policy['audio']['bytes']
             and pointer['package_sha256'] == policy['audio_candidate_package_sha256']
             and candidates['audio_candidate_package_sha256'] == policy['audio_candidate_package_sha256']
             and candidates['original_full_package_sha256'] == policy['original_full_package_sha256'],
             'audio_evidence_source_changed')
    return pointer


def _artifact_keys(journal):
    """Derive fixed artifact keys only from an actual closed journal selection."""
    _require(type(journal) is audio_journal.RouterAudioReviewJournal,
             'audio_evidence_journal_invalid')
    keys = journal.keys
    if keys != _WATCH[:3]:
        from app.services.retained_review_credential_successor import AUDIO_KEYS
        if keys != AUDIO_KEYS:
            from app.services.retained_review_completion_plan import AUDIO_KEYS as COMPLETION_AUDIO_KEYS
            if keys != COMPLETION_AUDIO_KEYS:
                from app.services.retained_review_captured_story_continuation import AUDIO_KEYS as CAPTURED_AUDIO_KEYS
                from app.services.retained_visual_schema_repair import AUDIO_KEYS as CORRECTED_AUDIO_KEYS
                from app.services.retained_visual_enum_repair import AUDIO_KEYS as ENUM_AUDIO_KEYS
                from app.services.retained_json_object_continuation import AUDIO_KEYS as OBJECT_AUDIO_KEYS
                _require(keys in (CAPTURED_AUDIO_KEYS, CORRECTED_AUDIO_KEYS, ENUM_AUDIO_KEYS, OBJECT_AUDIO_KEYS),
                         'audio_evidence_journal_invalid')
    prefix = keys[0].rsplit(':', 1)[0]
    return {'asr': prefix + ':asr_artifact', 'final': prefix + ':final_artifact'}


def _anchor(pipe, kind, state, journal):
    _require(type(kind) is str and kind in ('asr', 'final'), 'audio_evidence_anchor_invalid')
    key = _artifact_keys(journal)[kind]
    ttl = pipe.pttl(key)
    _require(type(ttl) is int and ttl == -1, 'audio_evidence_anchor_not_durable')
    anchor = _object(pipe.get(key))
    _require(set(anchor) == {'version', 'kind', 'source_task_id', 'original_task_id',
             'policy_sha256', 'continuity_sha256', 'journal_state_sha256', 'pointer',
             'asr_anchor_sha256', *_FLAGS}
             and type(anchor['version']) is int and anchor['version'] == 1
             and anchor['kind'] == kind and _flags(anchor)
             and anchor['source_task_id'] == continuity.LEAF_ID
             and anchor['original_task_id'] == continuity.ROOT_ID
             and anchor['policy_sha256'] == _hash(state['policy'])
             and anchor['continuity_sha256'] == state['policy']['continuity_sha256'],
             'audio_evidence_anchor_invalid')
    pointer = anchor['pointer']
    _require(type(pointer) is dict and set(pointer) == {'key', 'sha256', 'size', 'content_type'}
             and type(pointer['size']) is int and 1 <= pointer['size'] <= MAX_RECORD_BYTES
             and pointer['content_type'] == 'application/json'
             and type(pointer['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', pointer['sha256'])
             and pointer['key'] == f"recovery/{continuity.LEAF_ID}/included_router_audio_review/{kind}/{pointer['sha256']}.json",
             'audio_evidence_pointer_invalid')
    return anchor


def _blob(s3, bucket, key, digest, maximum, *, size=None, content_type):
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response.get('Body')
    try:
        length = response.get('ContentLength')
        status = response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        _require(type(status) is int and status == 200
                 and type(length) is int and 0 < length <= maximum
                 and (size is None or size == length)
                 and response.get('ContentType') == content_type, 'audio_evidence_blob_invalid')
        chunks, total = [], 0
        while True:
            chunk = body.read(min(64 * 1024, length - total + 1))
            _require(type(chunk) is bytes, 'audio_evidence_blob_invalid')
            if not chunk:
                break
            total += len(chunk)
            _require(total <= length, 'audio_evidence_blob_too_large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        _require(total == length and _sha(raw) == digest, 'audio_evidence_blob_changed')
        return raw
    finally:
        if body is not None:
            body.close()


def _private_blob(s3, bucket, pointer):
    raw = _blob(s3, bucket, pointer['key'], pointer['sha256'], MAX_RECORD_BYTES,
                size=pointer['size'], content_type='application/json')
    acl = s3.get_object_acl(Bucket=bucket, Key=pointer['key'])
    _source_acl_shape(acl, code='audio_evidence_blob_not_private')
    owner, grants = acl['Owner'], acl['Grants']
    owner_grants = [grant for grant in grants if grant['Permission'] == 'FULL_CONTROL'
                    and grant['Grantee'].get('Type') == 'CanonicalUser'
                    and grant['Grantee'].get('ID') == owner['ID']]
    _require(len(owner_grants) == 1 and len(grants) in (1, 2), 'audio_evidence_blob_not_private')
    if len(grants) == 2:
        endpoint = getattr(getattr(s3, 'meta', None), 'endpoint_url', None)
        admins = {'Grantee': {'Type': 'Group', 'URI': _TIGRIS_ADMINS}, 'Permission': 'FULL_CONTROL'}
        _require(type(endpoint) is str and endpoint in _TIGRIS_ENDPOINTS
                 and endpoint == storage.settings.endpoint
                 and sum(grant == admins for grant in grants) == 1, 'audio_evidence_blob_not_private')
    return raw


@dataclass(frozen=True, repr=False, init=False)
class RetainedAudioReviewEvidence:
    """Detached component evidence; its type is never a final-QA admission."""

    _record_bytes: bytes

    def __init__(self, *args, **kwargs):
        raise TypeError('Use read_retained_audio_review_evidence to verify persisted components.')

    def __repr__(self):
        return 'RetainedAudioReviewEvidence(<private diagnostic evidence>)'

    @property
    def diagnostics(self):
        return _object(self._record_bytes)

    @property
    def component_pass(self):
        return True

    @property
    def diagnostic_only(self):
        return True

    @property
    def qa_approved(self):
        return False

    @property
    def publish_eligible(self):
        return False

    @property
    def full_qa_complete(self):
        return False

    @property
    def edit_duration_qa_complete(self):
        return False


def _verified_component(raw):
    value = object.__new__(RetainedAudioReviewEvidence)
    object.__setattr__(value, '_record_bytes', raw)
    return value


def read_retained_audio_review_evidence(client, s3_client, *, bucket, successor=None, completion_plan=None,
                                      captured_story_continuation=None):
    """Read both positive components and original source under one WATCH ACK.

    Only GET/ACL reads and a Redis MULTI/PING occur. A race, unknown outcome,
    negative component or partial history fails without an automatic retry.
    The optional authority is a closed selection, verified with its controller
    and original records in this same transaction. Historical reads require no
    fresh request window. The snapshot is not a claim or story/render permission.
    """
    try:
        _require(type(bucket) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,62}', bucket)
                 and bucket == storage.settings.bucket
                 and type(storage.settings.endpoint) is str and storage.settings.endpoint
                 and getattr(getattr(s3_client, 'meta', None), 'endpoint_url', None) == storage.settings.endpoint,
                 'audio_evidence_storage_changed')
        journal = audio_journal.RouterAudioReviewJournal(
            client, successor=successor, completion_plan=completion_plan,
            captured_story_continuation=captured_story_continuation)
        anchors = _artifact_keys(journal)
        with client.pipeline() as pipe:
            pipe.watch(*journal.keys, *anchors.values())
            state = journal._read(pipe)
            _require(set(state['slots']) == {_ASR.value, _PROSODY.value}
                     and all(slot['response'] is not None for slot in state['slots'].values()),
                     'audio_evidence_response_unacknowledged')
            policy = state['policy']
            pointer = _source(pipe, policy)
            asr_anchor = _anchor(pipe, 'asr', state, journal)
            final_anchor = _anchor(pipe, 'final', state, journal)
            asr_slot = state['slots'][_ASR.value]
            asr_state = {'policy': policy, 'slots': {_ASR.value: asr_slot},
                         'updated_at': asr_slot['response']['observed_at']}
            _require(asr_anchor['journal_state_sha256'] == _hash(asr_state)
                     and asr_anchor['asr_anchor_sha256'] is None
                     and final_anchor['journal_state_sha256'] == _hash(state)
                     and final_anchor['asr_anchor_sha256'] == _hash(asr_anchor),
                     'audio_evidence_anchor_history_changed')
            metadata_raw = _blob(s3_client, bucket, pointer['metadata_key'],
                policy['source_metadata_sha256'], MAX_RECORD_BYTES, content_type='application/json')
            audio_journal._metadata(metadata_raw, policy)
            metadata = _object(metadata_raw)
            expected = ' '.join(metadata['voice']['spoken_texts'])
            audio_raw = _blob(s3_client, bucket, pointer['audio_key'], policy['audio']['sha256'],
                adapter.MAX_AUDIO_BYTES, size=policy['audio']['bytes'], content_type='audio/mpeg')
            measured = inspect_bounded_short_audio(audio_raw, 'audio/mpeg')
            _require(_raw(measured) == _raw(policy['audio']), 'audio_evidence_original_audio_changed')
            records = []
            for kind, anchor in (('asr', asr_anchor), ('final', final_anchor)):
                record = _object(_private_blob(s3_client, bucket, anchor['pointer']))
                _require(record.get('kind') == kind)
                _validate_record(record, state)
                _require(record['source']['expected_narration'] == expected,
                         'audio_evidence_source_changed')
                records.append(record)
            first, final = records
            _require(_raw(first['source']) == _raw(final['source'])
                     and _raw(first['reviews'][_ASR.value]) == _raw(final['reviews'][_ASR.value]),
                     'audio_evidence_prior_review_changed')
            result = {**final, 'kind': 'retained_audio_persisted_component_evidence',
                'component_pass': True, 'full_qa_complete': False, 'edit_duration_qa_complete': False,
                'journal_read_acknowledged': True, 'journal_state_sha256': _hash(state),
                'asr_anchor_sha256': _hash(asr_anchor), 'final_anchor_sha256': _hash(final_anchor)}
            result_bytes = _raw(result)
            pipe.multi()
            pipe.ping()
            ack = pipe.execute()
            _require(type(ack) is list and len(ack) == 1 and ack[0] is True,
                     'audio_evidence_read_ack_uncertain')
            return _verified_component(result_bytes)
    except RetainedAudioEvidenceError:
        raise
    except Exception:
        raise RetainedAudioEvidenceError('audio_evidence_read_unverified') from None
