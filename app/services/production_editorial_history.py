"""Read public topic text from bounded, hash-verified prior series archives.

Only planning guidance: no credentials, task IDs, private media, approval or
ledger mutations. Corrupt or unavailable optional history yields no guidance.
"""
import re

from app.services import production_series_promotion as promotion


def _archived_topics(client, channel_id):
    try:
        promotion._require(re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel_id))
        raw = client.get(promotion.EPOCH_PREFIX + channel_id)
        epoch = promotion._object(raw) if raw is not None else None
        topics = []
        for _ in range(12):
            if epoch is None:
                break
            promotion._require(type(epoch.get('epoch')) is int and epoch['epoch'] > 0)
            receipt_key = epoch.get('receipt_key')
            promotion._require(type(receipt_key) is str and receipt_key.startswith(promotion.RECEIPT_PREFIX + channel_id + ':'))
            receipt = promotion._object(client.get(receipt_key))
            archive_key = receipt.get('archive_key')
            promotion._require(receipt.get('channel_id') == channel_id and receipt.get('epoch') == epoch['epoch']
                and type(archive_key) is str and archive_key.startswith(promotion.ARCHIVE_PREFIX + channel_id + ':'))
            archive = promotion._object(client.get(archive_key))
            promotion._require(promotion._digest(archive) == receipt.get('archive_sha256')
                and archive.get('channel_id') == channel_id and archive.get('epoch') == epoch['epoch'] - 1)
            values = archive.get('profile', {}).get('production_topics')
            promotion._require(type(values) is list and len(values) <= 60)
            for topic in values:
                text = promotion._plain(topic, 240)
                for url in re.findall(r'https?://\S+', text):
                    promotion._public_url(url)
                if text not in topics:
                    topics.append(text)
                if len(topics) == 40:
                    return topics
            previous = archive.get('previous_epoch')
            promotion._require(previous is None or type(previous) is dict and previous.get('epoch') == epoch['epoch'] - 1)
            epoch = previous
        return topics
    except Exception:
        return []


def recent_topics(client, channel_id):
    """Include the owner's queue without sending private production briefs."""
    titles = []
    try:
        from app.services import content_plan
        document = content_plan.read(channel_id, client=client)
        for entry in reversed((document or {}).get('items', [])):
            title = promotion._plain(entry['title'], 140)
            for url in re.findall(r'https?://\S+', title):
                promotion._public_url(url)
            titles.append(title)
    except Exception:
        titles = []
    return list(dict.fromkeys([*titles, *_archived_topics(client, channel_id)]))[:40]
