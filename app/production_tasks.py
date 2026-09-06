"""Server-side recurring dispatch; rendering and publishing retain their gates."""
from app.celery_app import celery
from app.services.channel_production import (
    ChannelProductionError, dispatch_due_productions, reconcile_active_production,
)
from app.services.production_reconciliation import reconcile_public_retry_deliveries
from app.services.youtube_auth import connection_status
from app.services.youtube_automation import list_channel_profiles


@celery.task(name='app.production_tasks.production_tick', acks_late=False)
def production_tick() -> dict:
    from app.tasks import run_video_pipeline

    try:
        status = connection_status(verify=False)
        profiles = list_channel_profiles()
        connections = status.get('connections') or []
        linked_ids = {item['id'] for item in connections if isinstance(item, dict)
                      and isinstance(item.get('id'), str)}
        linked_profiles = [profile for profile in profiles if isinstance(profile, dict)
                           and isinstance(profile.get('channel_id'), str)
                           and profile['channel_id'] in linked_ids]
        # Clear only already-finished active claims before looking for public
        # retry receipts. Recovery still requires the existing global-idle CAS.
        reconcile_active_production()
        recovered = reconcile_public_retry_deliveries(linked_profiles)
        dispatched = dispatch_due_productions(
            profiles,
            connections,
            run_video_pipeline.apply_async,
        )
        return {**dispatched, 'public_retry_reconciliation': recovered}
    except ChannelProductionError:
        return {'status': 'blocked', 'reason': 'production_state_unavailable'}
    except Exception:
        return {'status': 'blocked', 'reason': 'production_configuration_unavailable'}
