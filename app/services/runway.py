from pathlib import Path
import httpx
from runwayml import RunwayML
from app.config import settings


def generate_scene(prompt: str, duration: int = 5) -> str:
    if not settings.runwayml_api_secret:
        raise RuntimeError('RUNWAYML_API_SECRET is not configured')

    prompt_text = str(prompt).strip()
    if not prompt_text:
        raise ValueError('Runway prompt is empty')
    if len(prompt_text.encode('utf-16-le')) // 2 > 1000:
        raise ValueError('Runway prompt exceeds 1000 UTF-16 code units')

    seconds = max(2, min(int(round(duration)), 10))
    client = RunwayML(api_key=settings.runwayml_api_secret)
    completed = client.text_to_video.create(
        model='gen4.5',
        prompt_text=prompt_text,
        ratio='1280:720',
        duration=seconds,
    ).wait_for_task_output(timeout=600)
    output = completed.output or []
    if not output:
        raise RuntimeError('Runway returned no video output')
    return str(output[0])


def download_generated_scene(url: str, output_path: str | Path) -> str:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream('GET', url, timeout=180, follow_redirects=True) as response:
        response.raise_for_status()
        with output.open('wb') as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    return str(output)
