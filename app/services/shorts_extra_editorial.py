"""Use an existing dated output slot for a verified replacement draft.

The shared editor requires a settled terminal rejection and a conclusively
stopped retained-media recovery before detaching a failed visual production.
Original approval, failures, paid receipts and reservations remain intact.
"""
from app.services import shorts_extra_release as extra, shorts_experiment as batch
from app.services import shorts_experiment_editorial as editorial


class _Authority:
    ARCHIVE = extra.PREFIX + 'editorial_archive:'
    resolved = staticmethod(extra.resolved)
    replacement_key = staticmethod(extra.replacement_key)
    root_id = staticmethod(batch.root_id)

    def __init__(self, channel):
        batch._require(channel in extra.formats.CHANNELS)
        self.channel = channel

    def _manifest(self, reader, now):
        return extra.read(reader, self.channel, now=now)

    def produced_key(self, day, channel):
        batch._require(channel == self.channel)
        return extra.keys(channel, day)[2]


def replace(channel, item_id, new_item, *, expected_job_sha256=None, client=None,
            now=None, observe_only=False):
    return editorial.replace(item_id, new_item, expected_job_sha256=expected_job_sha256,
        client=client, now=now, observe_only=observe_only, _authority=_Authority(channel))
