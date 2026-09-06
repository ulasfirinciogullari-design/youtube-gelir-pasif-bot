"""Discover already-public retry deliveries; never retry media or publication.

Discovery is read-only and bounded. The existing public recovery service is
the sole writer and rechecks all lineage, QA, delivery and connection evidence
atomically before clearing one failed-render pause. A candidate is not proof.

Known limitation: with three or more channels, two continuously overlapping
healthy channels can keep the global active claim nonempty and indefinitely
defer another channel's recovery. This retains the existing global-idle CAS;
it does not implement multi-channel recovery fairness beyond the current
two-channel delivery scope.
"""
from __future__ import annotations

import json

from app.services.channel_production import ACTIVE_KEY, CHANNEL_STATE_PREFIX, _redis
from app.services.production_recovery import (
    MAX_RETRY_HOPS, ProductionRecoveryError, _ID, _TASK_ID, resume_after_public_retry,
)
from app.services.studio_state import JOB_PREFIX
from app.services.youtube_auth import MAX_CONNECTIONS


def _public_leaf(client, original_id: str, channel_id: str, revision: str) -> str | None:
    """Find a terminal candidate through reciprocal links, without trusting it."""
    current, parent, seen = original_id, None, set()
    for hop in range(MAX_RETRY_HOPS + 1):
        if not isinstance(current, str) or not _TASK_ID.fullmatch(current) or current in seen:
            return None
        seen.add(current)
        raw = client.get(JOB_PREFIX + current)
        if not isinstance(raw, str) or not 0 < len(raw) <= 2_000_000:
            return None
        job = json.loads(raw)
        if (not isinstance(job, dict) or job.get('task_id') != current
                or job.get('kind') != 'render' or job.get('parent_id') != parent):
            return None
        spec = job.get('spec')
        if (not isinstance(spec, dict) or spec.get('production_channel_id') != channel_id
                or spec.get('production_profile_revision') != revision
                or spec.get('production_scheduled') is not True):
            return None
        child = job.get('retry_child_task_id')
        if job.get('state') == 'SUCCESS':
            result = job.get('result')
            youtube = result.get('youtube') if isinstance(result, dict) else None
            if (hop > 0 and not child and isinstance(youtube, dict)
                    and youtube.get('privacy_status') == 'public'
                    and youtube.get('release_status') == 'public'):
                return current
            return None
        if job.get('state') != 'FAILURE' or job.get('retry_claimed') is not True:
            return None
        parent, current = current, child
    return None


def reconcile_public_retry_deliveries(profiles: list[dict], *, now: float | None = None) -> dict:
    """Consume only genuine completed-public proof through the existing guard.

    Missing, private, uncertain or changed evidence stays paused. Per-channel
    failures cannot prevent ordinary dispatch for another healthy channel.
    """
    channels, resumed, seen = {}, 0, set()
    if not isinstance(profiles, list) or len(profiles) > MAX_CONNECTIONS:
        return {'status': 'unavailable', 'resumed_count': 0, 'channels': {}}
    try:
        client = _redis()
        # Do not steal or expire a sibling's claim. The recovery helper also
        # compares global idleness atomically, closing the race after this read.
        if client.exists(ACTIVE_KEY):
            return {'status': 'active', 'resumed_count': 0, 'channels': {}}
    except Exception:
        return {'status': 'unavailable', 'resumed_count': 0, 'channels': {}}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        channel_id, revision = profile.get('channel_id'), profile.get('profile_revision')
        if (not isinstance(channel_id, str) or not _ID.fullmatch(channel_id) or channel_id in seen
                or not isinstance(revision, str) or not 0 < len(revision) <= 128
                or profile.get('production_enabled') is not True
                or profile.get('auto_publish') is not True or profile.get('release_mode') != 'public'):
            continue
        seen.add(channel_id)
        try:
            state = client.hgetall(CHANNEL_STATE_PREFIX + channel_id)
            if (state.get('paused_reason') != 'previous_render_failed'
                    or state.get('last_result') != 'FAILURE' or state.get('dispatch_status') != 'finished'
                    or state.get('active_task_id') or state.get('profile_revision') != revision):
                continue
            original_id = state.get('last_task_id')
            recovered_id = _public_leaf(client, original_id, channel_id, revision)
            if recovered_id is None:
                channels[channel_id] = 'waiting_for_public_retry'
                continue
            result = resume_after_public_retry(
                channel_id, original_id, recovered_id, revision,
                now=now, continue_immediately=True,
            )
            channels[channel_id] = result['status']
            resumed += int(result['status'] == 'resumed')
        except ProductionRecoveryError:
            channels[channel_id] = 'public_retry_not_verified'
        except Exception:
            # Do not expose raw jobs, provider errors, credentials or claim tokens.
            channels[channel_id] = 'reconciliation_unavailable'
    return {'status': 'resumed' if resumed else 'idle', 'resumed_count': resumed, 'channels': channels}
