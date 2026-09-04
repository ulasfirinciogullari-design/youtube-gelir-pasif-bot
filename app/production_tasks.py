"""Server-side recurring dispatch; rendering and publishing retain their gates."""
from app.celery_app import celery
from app.services.channel_production import ChannelProductionError, dispatch_due_productions
from app.services.youtube_auth import connection_status
from app.services.youtube_automation import list_channel_profiles


@celery.task(name='app.production_tasks.production_tick', acks_late=False)
def production_tick() -> dict:
    from app.tasks import run_video_pipeline

    try:
        status = connection_status(verify=False)
        return dispatch_due_productions(
            list_channel_profiles(),
            status.get('connections') or [],
            run_video_pipeline.apply_async,
        )
    except ChannelProductionError:
        return {'status': 'blocked', 'reason': 'production_state_unavailable'}
    except Exception:
        return {'status': 'blocked', 'reason': 'production_configuration_unavailable'}
