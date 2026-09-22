"""Audio understanding from existing prepaid Abacus credits, never a top-up.

RouteLLM itself rejects input_audio. A separately commissioned fixed Gemini
route is required. The owner-reported balance is historical evidence, not a
current available balance, cash allowance or credit-to-USD conversion. This
policy requires confirmed automatic purchases off, bounds requests, and stops
on provider rejection. No balance/entitlement initialization or renewal occurs
in a production request; old unknown requests remain occupied in their ledger.
"""
from copy import deepcopy
from datetime import timedelta

from app.services.production_included_router import (
    IncludedRouterLedger, _require, _hash, _date, validate_policy as router_policy,
)
from app.services.abacus_router_audio_adapter import MODEL, PREPAID_MODEL

PREFIX = 'youtube_studio:{production_spend}:prepaid_audio:v1:'
STATE_KEY, JOURNAL_KEY, ANCHOR_KEY = (PREFIX + name for name in ('state', 'journal', 'anchor'))
MODE_FIELD = 'prepaid_audio_policy'
KIND = 'existing_subscription_prepaid_audio'


def enabled():
    from app.config import settings
    return getattr(settings, 'studio_abacus_prepaid_audio', False) is True


def validate_policy(policy, now):
    _require(type(policy) is dict and policy.get('kind') == KIND
             and policy.get('model') == PREPAID_MODEL, 'prepaid_audio_policy_invalid')
    extras = {'max_requests_total', 'reported_credit_balance', 'credit_balance_observed_at',
              'balance_source', 'current_available_credits', 'billing_controls_evidence_sha256',
              'model_catalogue_evidence_sha256', 'funding_basis'}
    _require(extras <= set(policy), 'prepaid_audio_policy_invalid')
    # Reuse the strict channel, lifetime, credential, request ceilings and
    # zero-cash validation without granting this policy a RouteLLM entitlement.
    common = {k: v for k, v in policy.items() if k not in extras}
    common.update(kind='existing_subscription_included_router', model=MODEL)
    router_policy(common, now)
    _require(type(policy['max_requests_total']) is int and 1 <= policy['max_requests_total'] <= 1000
        and type(policy['reported_credit_balance']) is int and 1 <= policy['reported_credit_balance'] <= 1000000
        and policy['balance_source'] == 'owner_report_not_provider_api'
        and policy['current_available_credits'] is None
        and policy['funding_basis'] == 'prepaid_credits_with_automatic_purchases_disabled'
        and all(_hash(policy[k]) for k in ('billing_controls_evidence_sha256', 'model_catalogue_evidence_sha256')),
        'prepaid_audio_policy_invalid')
    observed, start = _date(policy['credit_balance_observed_at']), _date(policy['valid_from'])
    _require(observed <= start <= observed + timedelta(days=1), 'prepaid_audio_opening_evidence_stale')
    return deepcopy(policy)


class PrepaidAudioLedger(IncludedRouterLedger):
    prefix, state_key, journal_key, anchor_key = PREFIX, STATE_KEY, JOURNAL_KEY, ANCHOR_KEY
    mode_field = MODE_FIELD
    purposes = frozenset({'blind_asr', 'prosody'})
    validate_policy = staticmethod(validate_policy)

    def _supports_continuation(self):
        # Lift only the trial-day request ceiling under the owner's setup
        # authority. Prepaid funding, total capacity and no-top-up checks stay.
        return type(self) is PrepaidAudioLedger

    def recover_saved_response(self, context, purpose, prepared, *, failure_record_sha256):
        """Explicit local recovery of a pinned HTTP-200 diagnostic, never a resend.

        No runtime automatically invokes this operation. The operator must pin
        the full original failure record independently before recovery. Unknown
        transport outcomes and uncaptured responses cannot be reconstructed.
        All requests remain counted, including this one, and failure evidence
        is retained unchanged. The output still needs the ordinary QA gates.
        """
        import hashlib
        import json
        from app.services import production_spend_runtime as runtime
        from app.services.abacus_router_audio_adapter import (
            PreparedPrepaidAudioRequest, AudioReviewPurpose,
            observe_stored_prepaid_audio_response,
        )
        from app.services.production_included_router import _cipher, _raw, _sha, _stamp
        from app.services.production_spend import LEDGER_KEY, SpendBlocked

        _require(type(prepared) is PreparedPrepaidAudioRequest and _hash(failure_record_sha256)
            and {'blind_asr': AudioReviewPurpose.BLIND_ASR,
                 'prosody': AudioReviewPurpose.PROSODY}.get(purpose) is prepared.purpose,
            'prepaid_audio_recovery_invalid')
        identity = self.identity(context, purpose, prepared.request_sha256)
        failure_key = self.prefix + 'failure:' + identity
        try:
            with self.client.pipeline() as pipe:
                state, journal = self._read(pipe)
                row = journal['requests'].get(identity)
                policy = state['policy']
                _require(row is not None and row['outcome'] is None
                    and row['context'] == context and row['purpose'] == purpose
                    and row['request_sha256'] == prepared.request_sha256
                    and policy['credential_sha256'] == prepared.credential_sha256
                    and policy['model'] == prepared.model and policy['endpoint'] == prepared.endpoint,
                    'prepaid_audio_recovery_binding_invalid')
                channel_key = runtime._CHANNEL_PREFIX + context['channel_id']
                pipe.watch(failure_key, channel_key, runtime._CHANNEL_INDEX, LEDGER_KEY)
                _require(pipe.hget(LEDGER_KEY, 'binding:' + context['lineage_id']) == _raw(context))
                channel = runtime._object(pipe.get(channel_key))
                _require(channel.get('id') == context['channel_id']
                    and channel.get('connection_id') == context['connection_id']
                    and channel.get('requires_reconnect') is not True
                    and pipe.sismember(runtime._CHANNEL_INDEX, context['channel_id']),
                    'included_router_channel_changed')
                record_raw = pipe.get(failure_key)
                _require(type(record_raw) is str and pipe.pttl(failure_key) == -1
                    and hashlib.sha256(record_raw.encode()).hexdigest() == failure_record_sha256,
                    'prepaid_audio_capture_changed')
                failure = json.loads(record_raw)
                _require(type(failure) is dict and set(failure) == {
                    'version', 'request_sha256', 'credential_sha256', 'observed_at',
                    'error_type', 'http_status', 'response_sha256', 'encrypted_response', 'retry_allowed'}
                    and type(failure['version']) is int and failure['version'] == 1
                    and type(failure['http_status']) is int and failure['http_status'] == 200
                    and failure['error_type'] == 'AbacusRouterAudioError'
                    and failure['request_sha256'] == prepared.request_sha256
                    and failure['credential_sha256'] == prepared.credential_sha256
                    and failure['retry_allowed'] is False
                    and _date(row['reserved_at']) <= _date(failure['observed_at']) <= self.clock()
                    and _hash(failure['response_sha256'])
                    and type(failure['encrypted_response']) is str
                    and 0 < len(failure['encrypted_response']) <= 24000,
                    'prepaid_audio_capture_invalid')
                raw = _cipher().decrypt(failure['encrypted_response'].encode('ascii'))
                _require(hashlib.sha256(raw).hexdigest() == failure['response_sha256'],
                         'prepaid_audio_capture_changed')
                observed = observe_stored_prepaid_audio_response(prepared, raw,
                    failure_record_sha256=failure_record_sha256, http_status=failure['http_status'])
                outcome = {'observed_at': _stamp(self.clock()), 'evidence': observed.evidence,
                           'encrypted_result': _cipher().encrypt(_raw(observed.result).encode()).decode('ascii')}
                _require(len(outcome['encrypted_result']) <= 400000)
                row['outcome'] = outcome
                pipe.multi()
                pipe.set(self.journal_key, _raw(journal))
                pipe.set(self.anchor_key, _sha({'state': state, 'journal': journal}))
                self._ack(pipe, [True, True])
                return deepcopy(outcome)
        except SpendBlocked:
            raise
        except Exception:
            # A lost commit reply never authorizes another transport attempt.
            raise SpendBlocked('prepaid_audio_recovery_uncertain') from None
