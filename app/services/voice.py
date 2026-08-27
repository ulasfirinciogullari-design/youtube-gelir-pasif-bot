from pathlib import Path
import httpx
from app.config import settings


def synthesize_voice(text: str, job_id: str) -> str:
    if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
        raise RuntimeError('ElevenLabs is not configured')

    url = f'https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}'
    response = httpx.post(
        url,
        headers={
            'xi-api-key': settings.elevenlabs_api_key,
            'Accept': 'audio/mpeg',
            'Content-Type': 'application/json',
        },
        json={
            'text': text,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {
                'stability': 0.45,
                'similarity_boost': 0.75,
                'style': 0.2,
                'use_speaker_boost': True,
            },
        },
        timeout=180,
    )
    response.raise_for_status()
    output = Path('/tmp') / f'{job_id}.mp3'
    output.write_bytes(response.content)
    return str(output)
