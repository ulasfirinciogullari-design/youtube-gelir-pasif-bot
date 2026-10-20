"""Worker maintenance for existing ElevenLabs credits; no purchases or TTS."""
from datetime import datetime, timezone
import hashlib

import httpx

from app.services.production_credit_ledger import CreditLedger, _object, _json, _require
from app.services.production_credit_periods import PREFIX, _date, _stamp, renewal_snapshot, renew
from app.services.production_spend import SpendBlocked

ENDPOINT = 'https://api.elevenlabs.io/v1/user'
CHECK_KEY, STATUS_KEY = PREFIX + 'renewal_check', PREFIX + 'renewal_status'
MAX_BYTES = 2_000_000


def read_allowance(api_key, *, expected_account_sha256, expected_credential_sha256, clock):
    """One bounded authenticated GET; retain only non-secret account evidence."""
    code = 'credit_period_account_unverified'
    _require(type(api_key) is str and 8 <= len(api_key) <= 8192
        and all(32 < ord(char) < 127 for char in api_key), code)
    credential = hashlib.sha256(('elevenlabs\0' + api_key).encode()).hexdigest()
    _require(credential == expected_credential_sha256, code)
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', ENDPOINT, headers={'xi-api-key': api_key, 'Accept': 'application/json'}) as response:
                _require(response.status_code == 200 and not response.history
                    and str(response.request.url) == ENDPOINT and response.request.method == 'GET', code)
                chunks, count = [], 0
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    _require(count <= MAX_BYTES, code)
                    chunks.append(chunk)
                wire = b''.join(chunks)
        data = _object(wire.decode('utf-8'))
        user, subscription = data.get('user_id'), data.get('subscription')
        _require(type(user) is str and 8 <= len(user) <= 256 and user.isascii()
            and type(subscription) is dict, code)
        account = hashlib.sha256(('elevenlabs\0account\0' + user).encode()).hexdigest()
        _require(account == expected_account_sha256, code)
        reset = subscription.get('next_character_count_reset_unix')
        _require(type(reset) is int and 0 < reset < 253402300799, code)
        fields = {name: subscription.get(name) for name in (
            'status', 'max_credit_limit_extension', 'can_extend_character_limit')}
        quota, used = subscription.get('character_limit'), subscription.get('character_count')
        _require(type(quota) is int and type(used) is int and 0 <= used < quota <= 1_000_000_000
            and fields['status'] == 'active' and fields['can_extend_character_limit'] is False
            and type(fields['max_credit_limit_extension']) is int and fields['max_credit_limit_extension'] == 0, code)
        return {'version': 1, 'source': 'verified_GET_v1_user',
            'account_sha256': account, 'credential_sha256': credential, 'observed_at': _stamp(clock()),
            'quota_credits': quota, 'used_credits': used,
            'provider_reset_at': _stamp(datetime.fromtimestamp(reset, timezone.utc)),
            'response_sha256': hashlib.sha256(wire).hexdigest(), **fields}
    except SpendBlocked:
        raise
    except Exception:
        raise SpendBlocked(code) from None


def maintain_native_credit_period():
    """Ordinary worker tick; valid periods perform no provider request or write."""
    from app.services import production_spend_runtime as runtime
    if not runtime.enforcement_enabled() or getattr(runtime.settings, 'studio_elevenlabs_native_credits', False) is not True:
        return {'status': 'disabled'}
    try:
        foundation = runtime.configured_ledger(read_timeout=2)
        cash = foundation.snapshot()
        _require(cash.get('mode') == 'cash_disabled_unknown_history'
            and cash.get('cash_spending_enabled') is False and cash.get('new_cash_allowance_micro') == 0,
            'credit_period_cash_mode_unverified')
        ledger = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
        snapshot = renewal_snapshot(ledger)
        policy, state = snapshot['policy'], snapshot['state']
        if foundation.clock() < _date(policy['valid_until']):
            return {'status': 'current', 'valid_until': policy['valid_until']}
        if state['reserved_credits'] or any(row['settlement'] is None for row in state['intents'].values()):
            return {'status': 'blocked', 'reason': 'credit_period_has_uncertain_usage'}
        if state['spent_credits'] > policy['allocation_credits']:
            return {'status': 'blocked', 'reason': 'credit_period_has_overrun'}
        # This short-lived throttle owns only a free account GET. Its expiry
        # never authorizes a generation or resets any financial receipt.
        claimed = foundation.client.set(CHECK_KEY, _stamp(foundation.clock()), nx=True, ex=600)
        if claimed is not True:
            return {'status': 'waiting_for_account_check'}
        observation = read_allowance(runtime.settings.elevenlabs_api_key,
            expected_account_sha256=policy['account_sha256'],
            expected_credential_sha256=policy['credential_sha256'], clock=foundation.clock)
        result = renew(ledger, observation, expected_policy_sha256=snapshot['policy_sha256'],
                       expected_state_sha256=snapshot['state_sha256'])
        foundation.client.set(STATUS_KEY, _json({**result, 'observed_at': _stamp(foundation.clock())}))
        return result
    except SpendBlocked as error:
        return {'status': 'blocked', 'reason': str(error)}
    except Exception:
        return {'status': 'unavailable'}
