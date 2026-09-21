"""Server-side recurring dispatch; rendering and publishing retain their gates."""
from app.celery_app import celery
from app.services.channel_production import (
    ChannelProductionError, dispatch_due_productions, reconcile_active_production,
)
from app.services.production_reconciliation import reconcile_public_retry_deliveries
from app.services.production_scheduler import maintain_production_series
from app.services.youtube_auth import connection_status
from app.services.youtube_automation import list_channel_profiles
from app.services.production_spend_runtime import spending_task
from celery.signals import task_postrun


@celery.task(name='app.production_tasks.prepare_series_batch', bind=True, acks_late=False,
             autoretry_for=(), max_retries=0, soft_time_limit=110, time_limit=120)
@spending_task
def prepare_series_batch(self, execution_binding: dict) -> dict:
    from app.services.production_scheduler import run_series_preparation

    return run_series_preparation(execution_binding, self.request.id)


@celery.task(name='app.production_tasks.render_delivery_family', bind=True, acks_late=False,
             autoretry_for=(), max_retries=0, soft_time_limit=2200, time_limit=2300)
def render_delivery_family(self, source_task_id: str) -> dict:
    from app.services.production_delivery_runtime import render_delivery_family as render_family

    return render_family(source_task_id, self.request.id)


@celery.task(name='app.production_tasks.finalize_retained_child', bind=True, acks_late=False,
             reject_on_worker_lost=False, autoretry_for=(), max_retries=0,
             soft_time_limit=1100, time_limit=1200)
def finalize_retained_child(self, manifest_sha256: str) -> dict:
    from app.services.retained_delivery_runtime import run_retained_delivery

    if type(self.request.retries) is not int or self.request.retries != 0:
        return {'status': 'stopped_unverified', 'automatic_retry_permitted': False}
    return run_retained_delivery(self.request.id, manifest_sha256)


@celery.task(name='app.production_tasks.observe_youtube_metrics', acks_late=False,
             autoretry_for=(), max_retries=0, soft_time_limit=250, time_limit=270)
def observe_youtube_metrics() -> dict:
    """Refresh owned-video observations without depending on an open Studio."""
    try:
        from app.services.studio_state import list_jobs, MAX_INDEXED_JOBS
        from app.services.youtube_metrics import refresh_dashboard_metrics

        jobs = list_jobs(limit=MAX_INDEXED_JOBS)
        if not jobs:
            # The registry's read helper also returns [] on store failure.
            # An empty inventory cannot authorize replacing cached video
            # observations, including prior owner-observed absence evidence.
            return {'status': 'unavailable', 'channel_count': 0, 'video_count': 0}
        metrics = refresh_dashboard_metrics(jobs, background=True)
        return {'status': 'unavailable' if metrics.get('error') else 'checked',
                'channel_count': len(metrics.get('channels') or []),
                'video_count': len(metrics.get('videos') or {})}
    except Exception:
        # Observation does not alter jobs, credentials, profiles, or dispatch.
        # The next ordinary observation may retry; no immediate task replay.
        return {'status': 'unavailable', 'channel_count': 0, 'video_count': 0}


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
        try:
            from app.services.production_credit_renewal import maintain_native_credit_period
            credit_period = maintain_native_credit_period()
        except Exception:
            credit_period = {'status': 'unavailable'}
        try:
            from app.services.production_quality_hold_periods import maintain_quality_hold_period
            hold_period = maintain_quality_hold_period()
        except Exception:
            hold_period = {'status': 'unavailable'}
        try:
            from app.services.production_delivery_runtime import maintain_delivery_families

            maintain_delivery_families()
        except Exception:
            pass  # Private derivatives never suppress an independent normal job.
        recovered = reconcile_public_retry_deliveries(linked_profiles)
        from app.services.production_quality_holds import maintain_quality_holds
        quality_holds = maintain_quality_holds(linked_profiles)
        dispatched = dispatch_due_productions(
            profiles,
            connections,
            run_video_pipeline.apply_async,
        )
        # No model request runs inside this minute tick. Maintenance failure
        # cannot undo or suppress already-completed normal dispatch.
        try:
            maintenance = maintain_production_series(profiles, connections, prepare_series_batch.apply_async)
        except Exception:
            maintenance = {'status': 'unavailable', 'channels': {}}
        return {**dispatched, 'public_retry_reconciliation': recovered,
                'quality_holds': quality_holds, 'series_maintenance': maintenance,
                'native_credit_period': credit_period, 'quality_hold_period': hold_period}
    except ChannelProductionError:
        return {'status': 'blocked', 'reason': 'production_state_unavailable'}
    except Exception:
        return {'status': 'blocked', 'reason': 'production_configuration_unavailable'}


@task_postrun.connect(weak=False)
def observe_production_tick(sender=None, state=None, retval=None, **_kwargs):
    if getattr(sender, 'name', None) != 'app.production_tasks.production_tick':
        return
    try:
        from app.services.studio_operations import record_tick
        record_tick(state, retval)
    except Exception:
        pass  # Owner status is never part of dispatch or financial authority.
