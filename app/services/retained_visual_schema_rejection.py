"""Authenticate one saved VISUAL schema rejection without reopening its slot.

This is diagnostic evidence for an explicit correction, never visual QA, a
settlement, free-usage evidence or permission to send. The old captured-STORY
controller, all 36 records and the exact encrypted HTTP 400 remain unchanged.
"""
import re
import weakref

from app.config import settings
from app.services import abacus_router_adapter as adapter
from app.services import abacus_router_review_journal as journal
from app.services import abacus_router_transport_capture as capture
from app.services import abacus_router_schema_compat as compat
from app.services import retained_transport_story_evidence as saved
from app.services import retained_review_captured_story_continuation as continuation
from app.services import production_connection_continuity as continuity
from app.services import retained_cut_evidence as cuts

PURPOSE = journal.PURPOSES[1]
HISTORY_KEYS = (*continuation.HISTORICAL_KEYS, *continuation.ALL_KEYS)
_ISSUED = weakref.WeakKeyDictionary()
_FLAGS = {'diagnostic_only': True, 'qa_approved': False, 'publish_eligible': False,
          'claim_authorized': False, 'render_authorized': False, 'resume_authorized': False,
          'retry_authorized': False, 'automatic_retry_permitted': False,
          'settlement_observed': False, 'usage_or_billing_verified': False}


class RetainedVisualSchemaRejectionError(RuntimeError):
    """Fixed diagnostic text; no raw source or provider error leaves this reader."""


def _require(value):
    if not value:
        raise RetainedVisualSchemaRejectionError('retained_visual_schema_rejection_unverified')


class RetainedVisualSchemaRejection:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_visual_schema_rejection_private')

    @property
    def record(self):
        _require(type(self) is RetainedVisualSchemaRejection and self in _ISSUED)
        return saved._object(_ISSUED[self], capture._CONTEXT_LIMIT)

    def __repr__(self):
        return '<RetainedVisualSchemaRejection diagnostic-only>'


def history_records(pipe):
    pipe.watch(*HISTORY_KEYS)
    _require(all(type(ttl) is int and ttl == -1 for ttl in (pipe.pttl(k) for k in HISTORY_KEYS)))
    return {key: saved._hash(pipe.hgetall(key) if index % 3 == 1 else pipe.get(key))
            for index, key in enumerate(HISTORY_KEYS)}


def validate_record(value):
    """Validate a stored diagnostic's fixed shape, without issuing a capability."""
    fixed = {'version': 1, 'kind': 'retained_visual_unique_items_rejection', **_FLAGS,
        'response_status_code': 400, 'rejected_keyword': 'uniqueItems', 'read_acknowledged': True,
        'historical_record_count': 36, 'prior_occupied_count': 6, 'prior_unknown_count': 5}
    _require(type(value) is dict and set(value) == {*fixed, 'commitments'})
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in fixed.items()))
    bound = value['commitments']
    hashes = {'controller_manifest_sha256', 'history_sha256', 'captured_story_qualification_sha256',
        'continuity_sha256', 'journal_state_sha256', 'policy_sha256', 'credential_sha256',
        'intent_sha256', 'anchor_sha256', 'capture_context_sha256', 'reservation_sha256',
        'request_sha256', 'prepared_sha256', 'wire_sha256', 'response_sha256', 'full_schema_sha256'}
    _require(type(bound) is dict and set(bound) == hashes | {'history_records', 'journal_keys', 'binding',
        'intent_key', 'anchor_key', 'encrypted_capture', 'summary', 'unique_items_rule_count', 'image_count'})
    for name in hashes:
        continuation._digest(bound[name])
    _require(type(bound['history_records']) is dict and set(bound['history_records']) == set(HISTORY_KEYS)
             and saved._hash(bound['history_records']) == bound['history_sha256']
             and bound['journal_keys'] == list(continuation.VISUAL_KEYS)
             and type(bound['unique_items_rule_count']) is int and 1 <= bound['unique_items_rule_count'] <= 50000
             and type(bound['image_count']) is int and 1 <= bound['image_count'] <= adapter.MAX_IMAGES)
    for digest in bound['history_records'].values():
        continuation._digest(digest)
    prefix = continuation.VISUAL_KEYS[0].rsplit(':', 1)[0] + ':transport_capture:v1:' + PURPOSE + ':' + bound['reservation_sha256']
    _require(bound['intent_key'] == prefix + ':intent' and bound['anchor_key'] == prefix + ':anchor'
             and bound['summary']['http_status'] == 400 and bound['summary']['response_complete'] is True
             and bound['summary']['response_sha256'] == bound['response_sha256'])
    return value


def _rejection(body):
    payload = adapter._json_loads(body.decode('utf-8'))
    _require(type(payload) is dict and set(payload) == {'success', 'error', 'errorType'}
             and payload['success'] is False and payload['errorType'] == 'UserFeedbackError'
             and type(payload['error']) is str and len(payload['error']) <= 160)
    # Provider punctuation/capitalization is not an identity. The complete
    # bounded word sequence identifies only this rejected schema keyword.
    _require(payload['error'].isascii()
             and re.fullmatch(r"[A-Za-z_\s:.,'\"()\[\]-]+", payload['error']) is not None
             and re.findall(r'[a-z_]+', payload['error'].lower()) ==
             ['validation', 'error', 'uniqueitems', 'extra', 'inputs', 'are', 'not', 'permitted'])


def read_visual_schema_rejection(client, s3, *, bucket, captured_story_continuation):
    """Read only; a final WATCH/PING ACK precedes the sealed diagnostic."""
    try:
        _require(continuation.selected_keys(captured_story_continuation, 'story') == continuation.VISUAL_KEYS)
        cuts._storage_guard(s3, bucket)
        material = settings.app_encryption_key
        with client.pipeline() as pipe:
            manifest, states, manifest_raw, _ = continuation._read_control(pipe)
            _require(manifest_raw == continuation._issued_bytes(captured_story_continuation))
            state = states['story']
            _require(set(state['slots']) == {PURPOSE} and state['slots'][PURPOSE]['response'] is None
                     and states['audio']['slots'] == {})
            source = continuity._derive(pipe, state['policy']['profile_revision'])
            _require(saved._hash(source) == state['policy']['continuity_sha256'])
            records = history_records(pipe)
            binding, reservation = saved._binding(state, source, continuation.VISUAL_KEYS, purpose=PURPOSE)
            intent_key, intent, anchor_key, anchor, cipher = saved._records(
                pipe, s3, bucket, binding, purpose=PURPOSE)
            context, bodies = saved._packet(cipher, material, binding=binding, reservation=reservation,
                state=state, source=source, summary=anchor['summary'], expected_status=400)
            _require(len(bodies['response']) <= 16384)
            _rejection(bodies['response'])
            body = adapter._json_loads(bodies['prepared'].decode('utf-8'))
            _require(adapter._inspect_body(body) == bodies['prepared']
                     and body['response_format']['json_schema']['name'] == 'youtube_review')
            schema = body['response_format']['json_schema']['schema']
            _, rules = compat._lower(schema)
            _require(rules > 0)
            images = sum(part['type'] == 'image_url' for part in body['messages'][1]['content'])
            _require(0 < images <= adapter.MAX_IMAGES)
            record = {'version': 1, 'kind': 'retained_visual_unique_items_rejection', **_FLAGS,
                'response_status_code': 400, 'rejected_keyword': 'uniqueItems',
                'read_acknowledged': True, 'historical_record_count': 36,
                'prior_occupied_count': 6, 'prior_unknown_count': 5,
                'commitments': {'controller_manifest_sha256': saved._sha(manifest_raw),
                    'history_records': records, 'history_sha256': saved._hash(records),
                    'captured_story_qualification_sha256': saved._hash(manifest['story_qualification']),
                    'continuity_sha256': saved._hash(source), 'journal_keys': list(continuation.VISUAL_KEYS),
                    'journal_state_sha256': saved._hash(state), 'policy_sha256': saved._hash(state['policy']),
                    'credential_sha256': binding['credential_sha256'], 'binding': binding,
                    'intent_key': intent_key, 'intent_sha256': saved._hash(intent),
                    'anchor_key': anchor_key, 'anchor_sha256': saved._hash(anchor),
                    'encrypted_capture': anchor['encrypted_blob'], 'summary': anchor['summary'],
                    'capture_context_sha256': saved._hash(context),
                    'reservation_sha256': reservation['reservation_sha256'],
                    'request_sha256': binding['request_sha256'],
                    **{name + '_sha256': saved._sha(bodies[name]) for name in ('prepared', 'wire', 'response')},
                    'full_schema_sha256': saved._hash(schema), 'unique_items_rule_count': rules,
                    'image_count': images}}
            encoded = saved._raw(validate_record(record))
            _require(len(encoded) <= capture._CONTEXT_LIMIT and material == settings.app_encryption_key
                     and history_records(pipe) == records)
            cuts._storage_guard(s3, bucket)
            continuation._ping(pipe)
        result = object.__new__(RetainedVisualSchemaRejection)
        _ISSUED[result] = encoded
        return result
    except RetainedVisualSchemaRejectionError:
        raise
    except Exception:
        raise RetainedVisualSchemaRejectionError('retained_visual_schema_rejection_unverified') from None
