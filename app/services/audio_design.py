from __future__ import annotations

from pathlib import Path
import subprocess

import httpx

from app.config import settings
from app.services.production_spend_runtime import paid_post

ELEVENLABS_BASE = 'https://api.elevenlabs.io/v1'

STYLE_PROMPTS = {
    'documentary': 'cinematic documentary underscore, restrained pulse, organic textures, intelligent and modern, no vocals',
    'technology': 'premium modern technology documentary underscore, subtle electronic pulse, precise, curious, no vocals',
    'story': 'emotional cinematic storytelling underscore, gentle build, intimate and human, no vocals',
    'cinematic': 'high-end cinematic underscore, controlled tension and release, rich atmosphere, no vocals',
    'explainer': 'clean contemporary explainer background music, light rhythmic pulse, unobtrusive and polished, no vocals',
}


def _media_duration(path: str | Path) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
    ], text=True).strip()
    return float(out)


def generate_music_bed(
    topic: str,
    style: str,
    output_path: str | Path,
    *,
    loop_seconds: float = 36.0,
) -> dict:
    """Generate a short instrumental bed that can be looped under narration.

    Music API access depends on the ElevenLabs subscription. Callers should treat
    failures as a graceful no-music fallback rather than fail the video job.
    """
    if not settings.elevenlabs_api_key:
        raise RuntimeError('ELEVENLABS_API_KEY is not configured')

    style_text = STYLE_PROMPTS.get(style, STYLE_PROMPTS['documentary'])
    prompt = (
        f'{style_text}. Topic context: {topic[:300]}. '
        'Create a seamless-feeling background bed for spoken YouTube narration. '
        'No lead melody competing with speech, no vocals, no dramatic ending.'
    )
    duration_ms = int(max(12000, min(loop_seconds * 1000, 60000)))
    response = paid_post(httpx.post,
        f'{ELEVENLABS_BASE}/music',
        headers={
            'xi-api-key': settings.elevenlabs_api_key,
            'Content-Type': 'application/json',
            'Accept': 'audio/mpeg',
        },
        params={'output_format': 'mp3_44100_128'},
        json={
            'prompt': prompt,
            'music_length_ms': duration_ms,
            'model_id': 'music_v2',
            'force_instrumental': True,
        },
        timeout=600,
    )
    if response.status_code >= 400:
        detail = response.text[:500]
        raise RuntimeError(f'ElevenLabs Music unavailable ({response.status_code}): {detail}')

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError('ElevenLabs Music returned an empty audio file')
    return {
        'path': str(output),
        'duration': _media_duration(output),
        'model': 'music_v2',
        'prompt': prompt,
    }


def mix_voice_and_music(
    voice_path: str | Path,
    music_path: str | Path,
    output_path: str | Path,
    *,
    music_volume: float = 0.075,
) -> str:
    """Loop a quiet music bed under narration while keeping speech dominant."""
    duration = _media_duration(voice_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fade_out_start = max(0.0, duration - 2.0)
    filter_graph = (
        f'[1:a]volume={max(0.01, min(music_volume, 0.25)):.4f},'
        f'atrim=0:{duration:.3f},asetpts=N/SR/TB,'
        f'afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out_start:.3f}:d=2.0[m];'
        '[0:a][m]amix=inputs=2:duration=first:dropout_transition=1.5,'
        'loudnorm=I=-15:TP=-1.5:LRA=8[a]'
    )
    subprocess.run([
        'ffmpeg', '-y', '-i', str(voice_path), '-stream_loop', '-1', '-i', str(music_path),
        '-filter_complex', filter_graph,
        '-map', '[a]', '-t', f'{duration:.3f}',
        '-c:a', 'libmp3lame', '-b:a', '192k', str(output),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(output)
