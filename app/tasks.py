from app.celery_app import celery
from app.services.research import research_and_script

@celery.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def run_video_pipeline(self, topic: str, duration_minutes: float = 5, language: str = 'tr', channel_id: str | None = None):
    package = research_and_script(topic, duration_minutes, language)
    return {
        'status': 'researched',
        'channel_id': channel_id,
        'package': package,
    }
