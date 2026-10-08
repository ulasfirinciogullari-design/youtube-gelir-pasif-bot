"""
Edge-TTS Free Voice Synthesis Service.
Provides 100% free, unlimited, studio-grade neural voice synthesis
with millisecond-accurate word boundary timestamps for viral subtitle generation.
"""

import asyncio
from pathlib import Path
import re
import subprocess
import edge_tts


# Recommended voices
VOICE_MAP = {
    'tr_male': 'tr-TR-AhmetNeural',      # Deep, authoritative documentary narrator
    'tr_female': 'tr-TR-EmelNeural',     # Clear, articulate presenter
    'en_male': 'en-US-ChristopherNeural', # High-authority American narrator
    'en_female': 'en-US-JennyNeural',    # Engaging modern presenter
}


def _get_audio_duration(file_path: Path) -> float:
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(file_path)
        ], text=True).strip()
        return float(out)
    except Exception:
        return 0.0


async def _async_synthesize(
    text: str,
    output_path: Path,
    voice: str = 'tr-TR-AhmetNeural',
    rate: str = '+0%',
    pitch: str = '+0Hz',
) -> tuple[float, list[dict]]:
    """Synthesize text using edge-tts and collect word timing boundaries."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    
    words = []
    with open(output_path, 'wb') as audio_file:
        async for chunk in communicate.stream():
            chunk_type = chunk.get('type')
            if chunk_type == 'audio':
                audio_file.write(chunk['data'])
            elif chunk_type == 'WordBoundary':
                # offset and duration are in 100-nanosecond units (ticks)
                start_sec = chunk['offset'] / 10_000_000.0
                dur_sec = chunk['duration'] / 10_000_000.0
                words.append({
                    'word': chunk['text'],
                    'start': start_sec,
                    'end': start_sec + dur_sec,
                })
                
    duration = _get_audio_duration(output_path)
    return duration, words


def synthesize_edge_tts(
    text: str,
    output_path: str | Path,
    voice: str | None = None,
    language: str = 'tr',
    rate: str = '+5%',
) -> dict:
    """
    Synchronous wrapper to synthesize text via edge-tts.
    
    Returns:
        dict with keys:
            - 'path': Path to generated audio file
            - 'duration': Total audio length in seconds
            - 'words': List of dicts with word timings: [{'word': ..., 'start': ..., 'end': ...}]
            - 'voice': Voice identifier used
    """
    clean_text = text.strip()
    if not clean_text:
        raise ValueError('Narration text cannot be empty')
        
    out_file = Path(output_path)
    
    # Choose voice based on language and preference
    if not voice:
        if language.lower() in ('tr', 'turkish'):
            voice = VOICE_MAP['tr_male']
        else:
            voice = VOICE_MAP['en_male']
            
    loop = asyncio.new_event_loop()
    try:
        duration, words = loop.run_until_complete(
            _async_synthesize(clean_text, out_file, voice=voice, rate=rate)
        )
    finally:
        loop.close()
        
    return {
        'path': out_file,
        'duration': duration,
        'words': words,
        'voice': voice,
    }
