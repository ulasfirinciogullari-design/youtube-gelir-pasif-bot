from celery import Celery
from celery.signals import beat_init, worker_init
from app.config import settings

celery = Celery(
    'youtube_factory',
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=['app.tasks', 'app.publish_tasks', 'app.production_tasks'],
)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        'app.production_tasks.production_tick': {'queue': 'production_control'},
        'app.production_tasks.observe_youtube_metrics': {'queue': 'production_control'},
        'app.production_tasks.observe_youtube_analytics': {'queue': 'production_control'},
        'app.production_tasks.observe_audience_trends': {'queue': 'production_control'},
        'app.production_tasks.maintain_video_languages': {'queue': 'production_control'},
    },
    beat_schedule={
        'optional-video-languages-every-five-minutes': {
            'task': 'app.production_tasks.maintain_video_languages',
            'schedule': 300.0,
            'options': {'expires': 290},
        },
        'audience-interest-check-every-five-minutes': {
            'task': 'app.production_tasks.observe_audience_trends',
            # Redis keeps the two-hour fetch window across deployments. A
            # restarted beat must not postpone stale observations for hours.
            'schedule': 300.0,
            'options': {'expires': 290},
        },
        'channel-production-every-minute': {
            'task': 'app.production_tasks.production_tick',
            'schedule': 60.0,
            'options': {'expires': 55},
        },
        'owned-youtube-metrics-every-five-minutes': {
            'task': 'app.production_tasks.observe_youtube_metrics',
            'schedule': 300.0,
            'options': {'expires': 290},
        },
        'owned-youtube-analytics-every-five-minutes': {
            'task': 'app.production_tasks.observe_youtube_analytics',
            'schedule': 300.0,
            'options': {'expires': 290},
        },
    },
)


@worker_init.connect(weak=False)
@beat_init.connect(weak=False)
def _claim_redis(**_kwargs):
    # Never consume or schedule another deployment's tasks.
    from app.services.deployment_guard import claim
    claim()
