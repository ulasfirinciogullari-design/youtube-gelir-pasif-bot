"""Allow affordable Shorts beside one untouched automatic daily documentary.

Only a local funding/queue observation. No credit release, reordered owner
series, synthetic completion, provider call or publication authorization.
"""
from datetime import date
import json
from uuid import NAMESPACE_URL, uuid5

from app.services import content_plan as plan, channel_cadence as cadence


def eligible(channel_id, *, client, pipe=None):
    if channel_id not in cadence.CHANNELS:
        return False
    if pipe is not None:
        return _checked(channel_id, pipe, client)
    try:
        with client.pipeline() as transaction:
            answer = _checked(channel_id, transaction, client)
            transaction.multi(); transaction.ping()
            return answer and transaction.execute() == [True]
    except Exception:
        return False


def _checked(channel_id, pipe, client):
    from app.services import production_spend_runtime as runtime
    from app.services.production_credit_ledger import CreditLedger
    from app.services.production_credit_funding import credit_funding_summary
    from app.services.production_spend import SpendLedger
    key = plan.PLAN_PREFIX + channel_id
    pipe.watch(key, plan.ACTIVE_KEY)
    document = plan.read(channel_id, client=pipe)
    if not (document and document['enabled'] and document['after_queue'] == 'auto_shorts'
            and channel_id not in plan._active(pipe)):
        return False
    pending = []
    for entry in document['items']:
        completion = plan.COMPLETION_PREFIX + entry['id']; pipe.watch(completion)
        if not pipe.exists(completion): pending.append(entry)
    if len(pending) != 1: return False
    entry = pending[0]
    if entry['format'] != 'long' or entry['series'] is not None or entry['depends_on']:
        return False
    # Only the exact automatic daily item and original receipt qualify. A
    # manually queued long film or an owner-edited brief still owns its place.
    prefix = 'Günün uzun videosu · '
    if not entry['title'].startswith(prefix): return False
    day = entry['title'][len(prefix):]
    if date.fromisoformat(day).isoformat() != day: return False
    if entry['id'] != str(uuid5(NAMESPACE_URL, 'owner-daily-long:' + channel_id + ':' + day)):
        return False
    receipt_key = cadence.PREFIX + 'daily_plan:' + channel_id + ':' + day
    dispatch_key = plan.DISPATCH_PREFIX + entry['id']
    task = str(uuid5(NAMESPACE_URL, 'youtube-owner-plan:' + channel_id + ':' + entry['id']))
    job_key = plan.jobs.JOB_PREFIX + task
    pipe.watch(receipt_key, dispatch_key, job_key)
    receipt = json.loads(pipe.get(receipt_key) or '{}')
    if not (receipt.get('channel_id') == channel_id and receipt.get('item_id') == entry['id']
            and receipt.get('day') == day and receipt.get('item_sha256') == plan._sha(entry)
            and not pipe.exists(dispatch_key, job_key)):
        return False
    if not runtime.enforcement_enabled(): return False
    configured = runtime.configured_ledger(read_timeout=2)
    foundation = SpendLedger(client, configured.policy, clock=configured.clock)
    # The supplied pipeline is the same Redis database used by reservation;
    # financial reads and all queue checks join its WATCH transaction.
    credit = CreditLedger(foundation.client, foundation=foundation, clock=foundation.clock)
    credit._watch(pipe)
    policy, state, _, _ = credit._read(pipe, foundation.clock())
    summary = credit_funding_summary(policy, state, now=foundation.clock())
    return summary['reserved_credits'] == 0 and 1000 <= summary['available_credits'] < 5000
