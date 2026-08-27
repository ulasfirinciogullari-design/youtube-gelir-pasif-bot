from pathlib import Path
import httpx
from runwayml import RunwayML
from app.config import settings


def generate_scene(prompt: str, duration: int = 5) -> str:
    if not settings.runwayml_api_secret:
        raise RuntimeError('RUNWAYML_API_SECRET is not configured')
    client = RunwayML(api_key=settings.runwayml_api_secret)
    completed = client.image_to_video.create(
        model='gen4.5',
        prompt_text=prompt,
        ratio='1280:720',
        duration=max(2, min(duration, 10)),
    ).wait_for_task_output(timeout=600)
    if not completed.output:
        raise RuntimeError('Runway returned no video output')
    return completed.output[0]


def download_generated_scene(url: str, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream('GET', url, timeout=180, follow_redirects=True) as response:
        response.raise_for_status()
        with output.open('wb') as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    return str(output)
