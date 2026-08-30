from celery import Celery
from app.config import settings

celery = Celery(
    'youtube_factory',
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=['app.tasks', 'app.publish_tasks'],
)
celery.conf.update(task_acks_late=True, worker_prefetch_multiplier=1)
