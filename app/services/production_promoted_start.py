"""Prove an untouched, already-promoted series before an owner advances its due time."""
import json
import re


def promotion_start_evidence(pipe, profile, state, connection):
    from app.services.production_schedule_control import _require
    from app.services import production_series_promotion as promotion

    channel_id, revision = profile['channel_id'], profile['profile_revision']
    epoch_key = promotion.EPOCH_PREFIX + channel_id
    history_key = promotion.TOPIC_HISTORY_PREFIX + channel_id
    receipt_key = state.get('last_series_promotion')
    prefix = promotion.RECEIPT_PREFIX + channel_id + ':'
    _require(type(receipt_key) is str and receipt_key.startswith(prefix)
             and re.fullmatch('[0-9a-f]{32}', receipt_key[len(prefix):]),
             'schedule_promotion_unverified')
    pipe.watch(epoch_key, history_key, receipt_key)
    epoch = json.loads(pipe.get(epoch_key) or 'null')
    receipt_raw = pipe.get(receipt_key)
    receipt = json.loads(receipt_raw or 'null')
    _require(type(epoch) is dict and type(receipt) is dict, 'schedule_promotion_unverified')
    promotion._receipt(receipt_raw, channel_id, receipt.get('old_profile_revision'), receipt_key[len(prefix):])
    _require(type(epoch.get('version')) is int and epoch['version'] == 1
             and epoch.get('channel_id') == channel_id
             and type(epoch.get('epoch')) is int and epoch['epoch'] > 0
             and type(profile.get('series_epoch')) is int and profile['series_epoch'] == epoch['epoch']
             and state.get('series_epoch') == str(epoch['epoch'])
             and epoch.get('series_id') == profile.get('series_id') == receipt['new_series_id']
             and epoch.get('profile_revision') == revision == state.get('profile_revision')
             and epoch.get('receipt_key') == receipt_key
             and receipt['new_profile_revision'] == revision and receipt['epoch'] == epoch['epoch']
             and receipt['new_profile_sha256'] == promotion._digest(profile)
             and type(profile.get('series_total')) is int
             and receipt['topic_count'] == profile['series_total'] == len(profile['production_topics'])
             and state.get('dispatch_status') == 'series_promoted' and state.get('cursor') == '0'
             and not any(state.get(k) for k in ('last_task_id', 'last_result', 'active_task_id',
                                               'paused_reason', 'quality_hold_task_id')),
             'schedule_promotion_unverified')
    archive_key = receipt['archive_key']
    pipe.watch(archive_key)
    archive = json.loads(pipe.get(archive_key) or 'null')
    _require(type(archive) is dict and promotion._digest(archive) == receipt['archive_sha256'],
             'schedule_promotion_unverified')
    old_profile, pending, old_state = (archive.get(k) for k in ('profile', 'pending_batch', 'state'))
    _require(type(old_profile) is dict and type(pending) is dict and type(old_state) is dict
             and archive.get('channel_id') == channel_id
             and type(archive.get('epoch')) is int and archive['epoch'] == epoch['epoch'] - 1
             and old_profile.get('profile_revision') == receipt['old_profile_revision']
             and old_profile.get('series_id') == receipt['old_series_id']
             and pending.get('attempt_id') == receipt['attempt_id']
             and promotion._digest(pending) == receipt['pending_sha256']
             and old_state.get('connection_id') == connection['connection_id']
             and pipe.type(history_key) == 'set' and type(epoch.get('history_count')) is int
             and pipe.scard(history_key) == epoch['history_count']
             and all(pipe.sismember(history_key, promotion._digest(promotion._text_key(topic)))
                     for topic in profile['production_topics'])
             and all(pipe.pttl(key) == -1 for key in (epoch_key, history_key, receipt_key, archive_key)),
             'schedule_promotion_unverified')
    return {'promotion_receipt_key': receipt_key,
            'promotion_receipt_sha256': promotion._digest(receipt)}
