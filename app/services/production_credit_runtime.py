"""One acknowledged native-credit reservation, one selected ElevenLabs POST.

The commissioned ledger maps an evidenced account to the actual outgoing key;
this is not a live account lookup. This adapter never initializes a ledger,
converts native credits to USD, retries a send or falls back to another route.
Unknown outcomes keep the entire remaining native allocation held. An exact
terminal provider meter can release only its unused part, before returning the
original response to the unchanged audio parser and quality checks.
"""
from datetime import datetime, timezone
import hashlib
from time import monotonic, sleep

from app.services.production_spend import SpendBlocked

_POOL_WAIT_SECONDS = 60
_POOL_WAIT_INTERVAL = .5


def _reserve_when_available(ledger, intent, context, actual):
    """Wait only for a competing reservation, before this call has sent anything.

    The original atomic reservation still decides admission. An unknown prior
    response is never released or retried; if it does not settle, waiting ends.
    No transport or ambiguous commit exception may enter this local wait loop.
    """
    from app.services import production_spend_runtime as runtime
    deadline = monotonic() + _POOL_WAIT_SECONDS
    for attempt in range(121):
        if attempt and runtime.resolve_context(ledger.client, runtime._TASK_ID.get()) != context:
            raise SpendBlocked('credit_production_context_invalid')
        try:
            return ledger.reserve(intent=intent, production_context=context, **actual)
        except SpendBlocked as exc:
            if str(exc) != 'credit_pool_has_uncertain_intent':
                raise
            remaining = deadline - monotonic()
            if remaining <= 0 or attempt == 120:
                raise
        # Leave the exception context before a new local Redis transaction.
        sleep(min(_POOL_WAIT_INTERVAL, remaining))


def paid_credit_post(sender, url, kwargs):
    """Guard the explicit native-credit path; callers cannot supply authority."""
    # Imports stay local: the existing native dispatcher selects this adapter.
    from app.services import production_spend_runtime as runtime
    from app.services.elevenlabs_credit_adapter import (
        inspect_credit_request, observe_credit_response,
    )
    from app.services.production_credit_ledger import CreditLedger

    try:
        if (not runtime.enforcement_enabled()
                or getattr(runtime.settings, 'studio_elevenlabs_native_credits', False) is not True):
            raise SpendBlocked('credit_runtime_not_enabled')
        prepared = inspect_credit_request(url, runtime._freeze_native_request(kwargs))
        runtime._native_sender_identity(sender, prepared.wire_kwargs()['headers'], prepared.provider)
        foundation = runtime.configured_ledger()
        ledger = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
        binding = ledger.binding_snapshot()
        if (binding['credential_sha256'] != prepared.credential_sha256
                or binding['route'] != prepared.route
                or prepared.model not in [binding['model'], *binding.get('additional_models', [])]
                or binding['voice_id'] != prepared.voice_id):
            raise SpendBlocked('credit_actual_binding_mismatch')

        context = runtime.resolve_context(foundation.client, runtime._TASK_ID.get())
        if context['kind'] != 'shorts':
            raise SpendBlocked('credit_production_context_invalid')
        fingerprint = runtime._request_fingerprint(
            context, prepared.provider, prepared.operation, prepared.payload,
        )
        intent = {
            'intent_id': hashlib.sha256(
                ('elevenlabs-native-credit-v1\0' + fingerprint).encode('utf-8'),
            ).hexdigest(),
            'root_lineage_id': context['lineage_id'], 'channel_id': context['channel_id'],
            'source_connection_id': context['connection_id'], 'request_sha256': fingerprint,
            'route': prepared.route, 'model': prepared.model, 'voice_id': prepared.voice_id,
        }
        actual = {'actual_account_sha256': binding['account_sha256'],
                  'actual_credential_sha256': prepared.credential_sha256}
        receipt = _reserve_when_available(ledger, intent, context, actual)

        # A mutable shared HTTPX client may have drifted during reservation.
        # Stop before POST without releasing the conservative hold. Application
        # functions/custom transport code remain within the trusted-code boundary.
        wire = prepared.wire_kwargs()
        runtime._native_sender_identity(sender, wire['headers'], prepared.provider)
        response = sender(prepared.route, **wire)
        meter = observe_credit_response(prepared, response, reserved_credits=receipt['reserved_credits'])
        observed = foundation.clock()
        if (not isinstance(observed, datetime) or observed.tzinfo is None
                or observed.utcoffset() is None):
            raise SpendBlocked('credit_clock_invalid')
        observation = {
            'version': 1, 'terminal': True, 'source': 'verified_provider_meter',
            **{key: intent[key] for key in (
                'intent_id', 'root_lineage_id', 'channel_id', 'source_connection_id', 'request_sha256',
            )},
            'reservation_sha256': receipt['reservation_sha256'],
            'account_sha256': binding['account_sha256'],
            'credential_sha256': prepared.credential_sha256,
            'observed_at': observed.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            **meter,
        }
        ledger.settle(observation=observation, **actual)
        return response
    except SpendBlocked:
        raise
    except Exception:
        # Timeout, malformed transport/protocol and uncertain settlement are
        # terminal to the task. Never expose keys, narration or raw HTTP errors.
        raise SpendBlocked('credit_runtime_outcome_unverified') from None
