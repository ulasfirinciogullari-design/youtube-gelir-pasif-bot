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
    beat_schedule={
        'channel-production-every-minute': {
            'task': 'app.production_tasks.production_tick',
            'schedule': 60.0,
            'options': {'expires': 55},
        },
    },
)
