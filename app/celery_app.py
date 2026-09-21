from celery import Celery
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
    },
    beat_schedule={
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
