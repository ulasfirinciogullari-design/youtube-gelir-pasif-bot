"""Two permanent, original-source-bound subscription audio review purposes.

Explicit operator admission only: no sender, funding initialization, retry,
reconfirmation, production hook or QA grant. Source metadata is authenticated
against the watched original checkpoint before its spoken contract is bound.
An acknowledged first reservation permits one send. Unknown acknowledgements
leave capacity occupied; settlement can recover only the same actual response.
Loss or rollback of all three records requires external recovery, never reset.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from redis.exceptions import WatchError

from app.services import audio_qc, production_connection_continuity as continuity
from app.services.abacus_router_audio_adapter import (
    ENDPOINT, MODEL, OPERATION, MAX_AUDIO_BYTES, MAX_DECODED_SAMPLES, MAX_OUTPUT_TOKENS,
    AudioReviewPurpose, PreparedAudioRouterRequest, inspect_audio_router_request,
    observe_audio_router_response, _asr_timing, _ASR_SCHEMA, _PROSODY_PREFIX,
    _PROSODY_SUFFIX, _usage,
)
from app.services.gemini_generation import _matches_schema
from app.services.production_spend import LEDGER_KEY, SpendBlocked
from app.services.production_spend_runtime import _request_fingerprint


_PREFIX = 'youtube_studio:{production_spend}:included_router_audio_review:v1:' + continuity.ROOT_ID
STATE_KEY = _PREFIX + ':state'
JOURNAL_KEY = _PREFIX + ':journal'
ANCHOR_KEY = _PREFIX + ':commissioned'
PURPOSES = tuple(AudioReviewPurpose)
_PURPOSE_VALUES = {purpose.value for purpose in PURPOSES}
_MAX_BYTES = 256 * 1024
_MAX_METADATA_BYTES = 512 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False}
_HASH_FIELDS = {
    'credential_sha256', 'continuity_sha256', 'entitlement_evidence_sha256',
    'audio_checkpoint_sha256', 'source_metadata_sha256', 'audio_candidate_package_sha256',
    'original_full_package_sha256', 'voice_contract_sha256', 'scene_durations_sha256',
    'expected_narration_sha256',
}
_POLICY_FIELDS = {
    'version', 'kind', 'endpoint', 'model', 'original_task_id', 'leaf_task_id',
    'channel_id', 'profile_revision', 'old_connection_id', 'current_connection_id',
    'entitlement_source', 'valid_from', 'valid_until', 'language', 'audio',
    'historical_extra_cash_micro', 'new_cash_allowance_micro', *_HASH_FIELDS,
}
_EVIDENCE_FIELDS = {
    'version', 'provider', 'endpoint', 'operation', 'purpose', 'requested_model',
    'returned_model', 'underlying_model_verified', 'credential_sha256', 'request_sha256',
    'wire_body_sha256', 'response_body_sha256', 'status_code', 'provider_request_id_sha256',
    'audio', 'parsed_result_sha256', 'usage', 'response_proof_sha256',
}


class RouterAudioReviewBlocked(SpendBlocked):
    """Fixed local reason; never credentials, source text or provider details."""


def _require(condition, reason='router_audio_review_store_invalid'):
    if not condition:
        raise RouterAudioReviewBlocked(reason)


def _json(value, maximum=_MAX_BYTES):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    _require(len(raw.encode('utf-8')) <= maximum)
    return raw


def _object(raw, maximum=_MAX_BYTES):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    _require(type(raw) is str and len(raw.encode('utf-8')) <= maximum)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: _require(False))
    _require(type(value) is dict)
    _json(value, maximum)  # Reject overflowed finite-looking JSON numbers too.
    return value


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(_json(value).encode('utf-8'))


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None)


def _date(value):
    _require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value))
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _audio(value):
    _require(type(value) is dict and set(value) == {
        'sha256', 'bytes', 'mime_type', 'decoded_sample_rate', 'decoded_samples', 'decoded_pcm_sha256'})
    _digest(value['sha256']); _digest(value['decoded_pcm_sha256'])
    _require(type(value['bytes']) is int and 1024 <= value['bytes'] <= MAX_AUDIO_BYTES
             and value['mime_type'] == 'audio/mpeg'
             and type(value['decoded_sample_rate']) is int and value['decoded_sample_rate'] == 48000
             and type(value['decoded_samples']) is int and 0 < value['decoded_samples'] <= MAX_DECODED_SAMPLES)


def _policy(value):
    _require(type(value) is dict and set(value) == _POLICY_FIELDS, 'router_audio_review_policy_invalid')
    expected = {'version': 1, 'kind': 'existing_subscription_retained_audio_review',
        'endpoint': ENDPOINT, 'model': MODEL, 'original_task_id': continuity.ROOT_ID,
        'leaf_task_id': continuity.LEAF_ID, 'channel_id': continuity.CHANNEL_ID, 'language': 'tr',
        'entitlement_source': 'owner_subscription_and_official_router_api_terms',
        'historical_extra_cash_micro': None, 'new_cash_allowance_micro': 0}
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()),
             'router_audio_review_policy_invalid')
    for field in _HASH_FIELDS:
        _digest(value[field])
    for field in ('profile_revision', 'old_connection_id', 'current_connection_id'):
        _require(type(value[field]) is str and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value[field]))
    _require(value['old_connection_id'] != value['current_connection_id'])
    _require(0 < (_date(value['valid_until']) - _date(value['valid_from'])).total_seconds() <= 86400)
    _audio(value['audio'])
    return value


def _metadata(raw, policy):
    """Pure validation of metadata bytes; authoritative checkpoint binding follows under WATCH."""
    from app.services.audio_checkpoint import _candidate_package
    from app.services.voice_candidate_recovery import _voice_result
    from app.services.voice import normalize_turkish_tts

    _require(type(raw) is bytes and 0 < len(raw) <= _MAX_METADATA_BYTES
             and _sha(raw) == policy['source_metadata_sha256'], 'router_audio_review_metadata_invalid')
    value = _object(raw.decode('utf-8'), _MAX_METADATA_BYTES)
    _require(set(value) == {'version', 'status', 'qa_approved', 'requires_full_qa',
        'source_task_id', 'audio', 'package_sha256', 'package', 'voice'})
    _require(type(value['version']) is int and value['version'] == 1
             and value['status'] == 'unapproved_candidate' and value['qa_approved'] is False
             and value['requires_full_qa'] is True and value['source_task_id'] == continuity.LEAF_ID)
    package = value['package']
    _require(type(package) is dict and _candidate_package(package) == package
             and len(package['scenes']) == 6
             and _hash(package) == value['package_sha256'] == policy['audio_candidate_package_sha256'])
    voice = _voice_result(value['voice'], 6)
    spoken = [normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5)
              for index, scene in enumerate(package['scenes'])]
    legacy = [normalize_turkish_tts(scene['narration'], ensure_terminal=index == 5,
                                  legacy_numeric_spacing=True)
              for index, scene in enumerate(package['scenes'])]
    _require(voice['spoken_texts'] in (spoken, legacy), 'router_audio_review_spoken_contract_changed')
    _require(_hash(value['voice']) == policy['voice_contract_sha256']
             and _hash(voice['scene_durations']) == policy['scene_durations_sha256']
             and _sha(' '.join(voice['spoken_texts']).encode('utf-8')) == policy['expected_narration_sha256'])
    # Preserve the original timing contract; frozen edit-duration QA remains
    # separate. Request inspection independently bounds the complete decode.
    expected_audio = {'key': f"audio_candidates/{continuity.LEAF_ID}/{policy['audio']['sha256']}/candidate.mp3",
                      'sha256': policy['audio']['sha256'], 'size': policy['audio']['bytes']}
    _require(type(value['audio']) is dict and _json(value['audio']) == _json(expected_audio))


def _receipt(policy, purpose, slot):
    return {'policy_sha256': _hash(policy), 'purpose': purpose,
        'request_sha256': slot['request_sha256'], 'root_request_fingerprint': slot['root_request_fingerprint'],
        'reserved_at': slot['reserved_at'], 'asr_binding': slot['asr_binding']}


def _journal(state):
    result = {'policy_sha256': _hash(state['policy']), 'state_sha256': _hash(state)}
    for purpose, slot in state['slots'].items():
        result['reservation:' + purpose] = _json(_receipt(state['policy'], purpose, slot))
        if slot['response'] is not None:
            result['response:' + purpose] = _json(slot['response'])
    return result


def _prepare(purpose, prepared):
    _require(type(purpose) is AudioReviewPurpose, 'router_audio_review_purpose_invalid')
    _require(type(prepared) is PreparedAudioRouterRequest and prepared.purpose is purpose,
             'router_audio_review_request_invalid')
    checked = inspect_audio_router_request(ENDPOINT, prepared.wire_kwargs(), purpose=purpose)
    _require(checked == prepared, 'router_audio_review_request_invalid')
    return checked


def _request(prepared, policy):
    _require(prepared.credential_sha256 == policy['credential_sha256']
             and _json(prepared.audio) == _json(policy['audio']), 'router_audio_review_request_invalid')
    if prepared.purpose is AudioReviewPurpose.PROSODY:
        body = prepared.payload
        text = body['messages'][1]['content'][1]['text']
        expected = json.loads(text[len(_PROSODY_PREFIX):-len(_PROSODY_SUFFIX)])
        _require(body['messages'][0]['content'] == audio_qc._PROSODY_SYSTEM_INSTRUCTION
                 and _json(body['response_format']['json_schema']['schema']) == _json(audio_qc._PROSODY_REVIEW_SCHEMA)
                 and _sha(expected.encode('utf-8')) == policy['expected_narration_sha256'],
                 'router_audio_review_prosody_contract_changed')


def _asr_binding(policy, slot, expected, result):
    _require(slot is not None and slot['response'] is not None, 'router_audio_review_asr_unobserved')
    evidence = slot['response']['evidence']
    _require(type(expected) is str and 0 < len(expected.encode('utf-8')) <= 24_000
             and _sha(expected.encode('utf-8')) == policy['expected_narration_sha256']
             and _hash(result) == evidence['parsed_result_sha256'], 'router_audio_review_asr_binding_invalid')
    _require(_matches_schema(result, _ASR_SCHEMA) and result['language'] == 'tr',
             'router_audio_review_asr_invalid')
    _asr_timing(result, policy['audio'])
    comparison = audio_qc._require_word_timing_evidence(audio_qc.compare_transcript(
        expected, result['text'], words=result['words'], language_code='tr', provider='abacus_router',
        comparison_language='tr'), 'Abacus router')
    _require(comparison['available'] is True and comparison['pass'] is True and comparison['score'] == 100
             and comparison['mismatch_details']['exact_match'] is True
             and comparison['mismatch_details']['timestamp_sequence_match'] is True
             and audio_qc._validated_prosody_timestamp_evidence(comparison, allow_coarse=False) is not None,
             'router_audio_review_asr_rejected')
    return {'reservation_sha256': slot['response']['reservation_sha256'],
        'response_proof_sha256': evidence['response_proof_sha256'],
        'parsed_result_sha256': evidence['parsed_result_sha256'],
        'comparison_sha256': _hash(comparison), 'expected_narration_sha256': policy['expected_narration_sha256']}


class RouterAudioReviewJournal:
    """Explicit synchronous API; its receipts are never publication approval."""

    def __init__(self, client, *, clock=None, successor=None, completion_plan=None,
                 captured_story_continuation=None):
        _require(sum(value is not None for value in (successor, completion_plan, captured_story_continuation)) <= 1,
                 'router_audio_review_multiple_authorizations')
        self.client = client
        self._successor = successor
        self._completion_plan = completion_plan
        self._captured_story_continuation = captured_story_continuation
        if captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import selected_keys
            selected_keys(captured_story_continuation, 'audio')
        if completion_plan is not None:
            from app.services.retained_review_completion_plan import selected_keys
            selected_keys(completion_plan, 'audio')
        if successor is not None:
            from app.services.retained_review_credential_successor import selected_keys
            selected_keys(successor, 'audio')
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def keys(self):
        if self._captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import selected_keys
            return selected_keys(self._captured_story_continuation, 'audio')
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import selected_keys
            return selected_keys(self._completion_plan, 'audio')
        if self._successor is None:
            return STATE_KEY, JOURNAL_KEY, ANCHOR_KEY
        from app.services.retained_review_credential_successor import selected_keys
        return selected_keys(self._successor, 'audio')

    @property
    def state_key(self): return self.keys[0]

    @property
    def journal_key(self): return self.keys[1]

    @property
    def anchor_key(self): return self.keys[2]

    def _admit(self, pipe, *, reserve=False):
        if self._captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import guard_mutation
            return guard_mutation(pipe, self._captured_story_continuation, 'audio', reserve=reserve)
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import guard_mutation
            return guard_mutation(pipe, self._completion_plan, 'audio', reserve=reserve)
        from app.services.retained_review_credential_successor import guard_mutation
        guard_mutation(pipe, self._successor, 'audio', reserve=reserve)

    def _now(self):
        now = self.clock()
        _require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None,
                 'router_audio_review_clock_invalid')
        return now.astimezone(timezone.utc).replace(microsecond=0)

    def _fresh(self, pipe, policy, now):
        _require(_date(policy['valid_from']) <= now < _date(policy['valid_until']),
                 'router_audio_review_entitlement_expired')
        record = continuity._derive(pipe, policy['profile_revision'])
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
                 'router_audio_review_source_changed')

    def _read(self, pipe):
        state = self._read_records(pipe)
        if self._captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import guard_selected
            guard_selected(pipe, self._captured_story_continuation, 'audio', state)
        elif self._completion_plan is not None:
            from app.services.retained_review_completion_plan import guard_selected
            guard_selected(pipe, self._completion_plan, 'audio', state)
        elif self._successor is not None:
            from app.services.retained_review_credential_successor import guard_selected
            guard_selected(pipe, self._successor, 'audio', state)
        return state

    def _read_records(self, pipe):
        _require(all(type(ttl) is int and ttl == -1 for ttl in
                     (pipe.pttl(key) for key in (self.state_key, self.journal_key, self.anchor_key))),
                 'router_audio_review_not_initialized_or_durable')
        state = _object(pipe.get(self.state_key))
        _require(set(state) == {'policy', 'slots', 'updated_at'})
        policy = _policy(state['policy'])
        stamp = _date(state['updated_at'])
        _require(stamp >= _date(policy['valid_from']) and type(state['slots']) is dict
                 and set(state['slots']) <= _PURPOSE_VALUES)
        _require(state['slots'] or stamp < _date(policy['valid_until']))
        seen = set()
        for purpose, slot in state['slots'].items():
            _require(type(slot) is dict and set(slot) == {
                'request_sha256', 'root_request_fingerprint', 'reserved_at', 'response', 'asr_binding'})
            _digest(slot['request_sha256']); _digest(slot['root_request_fingerprint'])
            _require(slot['request_sha256'] not in seen)
            seen.add(slot['request_sha256'])
            reserved = _date(slot['reserved_at'])
            _require(_date(policy['valid_from']) <= reserved < _date(policy['valid_until']) and reserved <= stamp)
            observed = slot['response']
            if observed is not None:
                _require(type(observed) is dict and set(observed) == {'reservation_sha256', 'evidence', 'observed_at'}
                         and observed['reservation_sha256'] == _hash(_receipt(policy, purpose, slot))
                         and reserved <= _date(observed['observed_at']) <= stamp)
                evidence = observed['evidence']
                _require(type(evidence) is dict and set(evidence) == _EVIDENCE_FIELDS
                         and type(evidence['version']) is int and evidence['version'] == 1
                         and evidence['provider'] == 'abacus' and evidence['purpose'] == purpose
                         and evidence['request_sha256'] == slot['request_sha256']
                         and evidence['credential_sha256'] == policy['credential_sha256']
                         and evidence['endpoint'] == ENDPOINT and evidence['operation'] == OPERATION
                         and evidence['requested_model'] == MODEL and evidence['underlying_model_verified'] is False
                         and _json(evidence['audio']) == _json(policy['audio'])
                         and type(evidence['status_code']) is int and 200 <= evidence['status_code'] < 300
                         and type(evidence['returned_model']) is str
                         and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}', evidence['returned_model']))
                for name in ('wire_body_sha256', 'response_body_sha256',
                             'parsed_result_sha256', 'response_proof_sha256'):
                    _digest(evidence[name])
                # Only a genuinely absent body ID is represented as None by
                # the observer; local request/body hashes remain mandatory.
                if evidence['provider_request_id_sha256'] is not None:
                    _digest(evidence['provider_request_id_sha256'])
                if evidence['usage'] is not None:
                    _usage(evidence['usage'], MAX_OUTPUT_TOKENS)
                _require(evidence['response_proof_sha256'] == _hash({
                    k: v for k, v in evidence.items() if k != 'response_proof_sha256'}))
            binding = slot['asr_binding']
            if purpose == AudioReviewPurpose.BLIND_ASR.value:
                _require(binding is None)
            else:
                asr = state['slots'].get(AudioReviewPurpose.BLIND_ASR.value)
                _require(type(binding) is dict and set(binding) == {
                    'reservation_sha256', 'response_proof_sha256', 'parsed_result_sha256',
                    'comparison_sha256', 'expected_narration_sha256'} and asr is not None and asr['response'] is not None)
                for digest in binding.values():
                    _digest(digest)
                _require(binding['reservation_sha256'] == asr['response']['reservation_sha256']
                         and binding['response_proof_sha256'] == asr['response']['evidence']['response_proof_sha256']
                         and binding['parsed_result_sha256'] == asr['response']['evidence']['parsed_result_sha256']
                         and binding['expected_narration_sha256'] == policy['expected_narration_sha256']
                         and _date(asr['response']['observed_at']) <= reserved)
        _require(pipe.hlen(self.journal_key) <= 6 and pipe.hgetall(self.journal_key) == _journal(state)
                 and pipe.get(self.anchor_key) == _hash(state), 'router_audio_review_replay_evidence_mismatch')
        return state

    def _commit(self, pipe, state, old_journal):
        if self._captured_story_continuation is not None:
            from app.services.retained_review_captured_story_continuation import commit_selected
            return commit_selected(pipe, self._captured_story_continuation, 'audio', state, old_journal)
        if self._completion_plan is not None:
            from app.services.retained_review_completion_plan import commit_selected
            return commit_selected(pipe, self._completion_plan, 'audio', state, old_journal)
        if self._successor is not None:
            from app.services.retained_review_credential_successor import commit_selected
            return commit_selected(pipe, self._successor, 'audio', state, old_journal)
        journal = _journal(state)
        pipe.multi()
        pipe.set(self.state_key, _json(state))
        pipe.hset(self.journal_key, mapping=journal)
        pipe.set(self.anchor_key, _hash(state))
        ack = pipe.execute()
        _require(type(ack) is list and len(ack) == 3 and ack[0] is True and ack[2] is True
                 and type(ack[1]) is int and ack[1] == len(set(journal) - set(old_journal)),
                 'router_audio_review_commit_uncertain')

    def _operate(self, action):
        for _ in range(8):
            try:
                with self.client.pipeline() as pipe:
                    pipe.watch(self.state_key, self.journal_key, self.anchor_key)
                    return action(pipe, self._now())
            except WatchError:
                continue  # EXEC did not run; this API has no provider sender.
            except RouterAudioReviewBlocked:
                raise
            except Exception:
                raise RouterAudioReviewBlocked('router_audio_review_state_or_outcome_unverified') from None
        raise RouterAudioReviewBlocked('router_audio_review_store_contention')

    def commission(self, policy, *, source_metadata_bytes):
        """Explicit one-time creation from authenticated original metadata bytes."""
        try:
            checked = _policy(_object(_json(policy)))
            _metadata(source_metadata_bytes, checked)
        except RouterAudioReviewBlocked:
            raise
        except Exception:
            raise RouterAudioReviewBlocked('router_audio_review_policy_or_metadata_invalid') from None

        def action(pipe, now):
            self._admit(pipe, reserve=False)
            _require(self._successor is None and self._completion_plan is None
                     and self._captured_story_continuation is None,
                     'router_audio_review_successor_invalid')
            _require(pipe.exists(self.state_key, self.journal_key, self.anchor_key) == 0,
                     'router_audio_review_already_commissioned_or_partial')
            self._fresh(pipe, checked, now)
            state = {'policy': checked, 'slots': {}, 'updated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
            self._commit(pipe, state, {})
            return {'policy_sha256': _hash(checked), **_FLAGS}
        return self._operate(action)

    def reserve(self, purpose, prepared, *, expected_narration=None, asr_result=None):
        """Only a first acknowledged return permits one send of these exact bytes."""
        try:
            prepared = _prepare(purpose, prepared)
            if purpose is AudioReviewPurpose.BLIND_ASR:
                _require(expected_narration is None and asr_result is None, 'router_audio_review_blind_inputs_invalid')
            else:
                _require(type(expected_narration) is str)
                asr_result = _object(_json(asr_result))
            fingerprint = _request_fingerprint({'lineage_id': continuity.ROOT_ID}, 'abacus', OPERATION, prepared.payload)
        except RouterAudioReviewBlocked:
            raise
        except Exception:
            raise RouterAudioReviewBlocked('router_audio_review_request_invalid') from None

        def action(pipe, now):
            self._admit(pipe, reserve=True)
            state = self._read(pipe)
            policy = state['policy']
            _require(purpose.value not in state['slots'], 'router_audio_review_request_already_reserved')
            _request(prepared, policy)
            binding = None if purpose is AudioReviewPurpose.BLIND_ASR else _asr_binding(
                policy, state['slots'].get(AudioReviewPurpose.BLIND_ASR.value), expected_narration, asr_result)
            _require(all(slot['request_sha256'] != prepared.request_sha256 for slot in state['slots'].values()),
                     'router_audio_review_request_already_reserved')
            pipe.watch(LEDGER_KEY)
            field = _sha(fingerprint.encode('utf-8'))
            _require(not pipe.hexists(LEDGER_KEY, 'request:' + field)
                     and not pipe.hexists(LEDGER_KEY, 'native_request:' + field),
                     'router_audio_review_cross_mode_request_conflict')
            _require(now >= _date(state['updated_at']), 'router_audio_review_clock_invalid')
            self._fresh(pipe, policy, now)
            old_journal = _journal(state)
            state['updated_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
            slot = {'request_sha256': prepared.request_sha256, 'root_request_fingerprint': fingerprint,
                    'reserved_at': state['updated_at'], 'response': None, 'asr_binding': binding}
            state['slots'][purpose.value] = slot
            self._commit(pipe, state, old_journal)
            receipt = _receipt(policy, purpose.value, slot)
            return {**receipt, 'reservation_sha256': _hash(receipt), **_FLAGS}
        return self._operate(action)

    def settle(self, purpose, prepared, response):
        """Observe actual HTTPX bytes, including a late response; never grant a resend."""
        try:
            prepared = _prepare(purpose, prepared)
            observed = observe_audio_router_response(prepared, response)
        except RouterAudioReviewBlocked:
            raise
        except Exception:
            raise RouterAudioReviewBlocked('router_audio_review_response_unverified') from None

        def action(pipe, now):
            self._admit(pipe, reserve=False)
            state = self._read(pipe)
            _require(purpose.value in state['slots'], 'router_audio_review_reservation_missing')
            policy, slot = state['policy'], state['slots'][purpose.value]
            _request(prepared, policy)
            _require(slot['request_sha256'] == prepared.request_sha256, 'router_audio_review_request_invalid')
            _require(now >= _date(state['updated_at']), 'router_audio_review_clock_invalid')
            if slot['response'] is not None:
                _require(_json(slot['response']['evidence']) == _json(observed.evidence),
                         'router_audio_review_settlement_conflict')
                pipe.multi(); pipe.ping()
                ack = pipe.execute()
                _require(type(ack) is list and len(ack) == 1 and ack[0] is True, 'router_audio_review_commit_uncertain')
            else:
                old_journal = _journal(state)
                state['updated_at'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
                slot['response'] = {'reservation_sha256': _hash(_receipt(policy, purpose.value, slot)),
                    'observed_at': state['updated_at'], 'evidence': observed.evidence}
                self._commit(pipe, state, old_journal)
            return {'result': observed.result, 'evidence': observed.evidence, **_FLAGS}
        return self._operate(action)
