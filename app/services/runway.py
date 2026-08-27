from runwayml import RunwayML
from app.config import settings


def generate_scene(prompt: str, duration: int = 5) -> str:
    if not settings.runwayml_api_secret:
        raise RuntimeError('RUNWAYML_API_SECRET is not configured')

    client = RunwayML(api_key=settings.runwayml_api_secret)
    task = client.image_to_video.create(
        model='gen4.5',
        prompt_text=prompt,
        ratio='1280:720',
        duration=duration,
    )
    completed = task.wait_for_task_output(timeout=600)
    if not completed.output:
        raise RuntimeError('Runway returned no video output')
    return completed.output[0]
