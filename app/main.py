from fastapi import FastAPI
from pydantic import BaseModel, Field
from app.tasks import run_video_pipeline

app = FastAPI(title='YouTube 7/24 Content Factory', version='0.1.0')

class JobCreate(BaseModel):
    topic: str = Field(min_length=2)
    duration_minutes: float = 5
    language: str = 'tr'
    channel_id: str | None = None

@app.get('/health')
def health():
    return {'ok': True}

@app.post('/jobs')
def create_job(payload: JobCreate):
    task = run_video_pipeline.delay(
        payload.topic,
        payload.duration_minutes,
        payload.language,
        payload.channel_id,
    )
    return {'task_id': task.id, 'status': 'queued'}
