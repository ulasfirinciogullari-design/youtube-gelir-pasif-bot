"""
ELEVENLABS HOLLYWOOD VOICE SYNTHESIS ENGINE.
Ultra-realistic AI voice synthesis using ElevenLabs v1 API with multilingual_v2 support.
Seamlessly falls back to Edge-TTS if no API key is provided or quota is depleted.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error

# Renowned ElevenLabs default voices
ELEVENLABS_VOICE_MAP = {
    "shelby": "pNInz6obpgDQGcFmaJgB",      # Deep Alpha Baritone (Adam)
    "godfather": "VR6AewLTigWG4xSOukaG",   # Raspy Authority (Arnold)
    "narrator": "ErXwobaYiN019PkySvjV",    # Cinematic Documentary (Antoni)
    "stoic": "N2lVS1w4EtoT3dr4eOWO",       # Philosophical Gravitas (Callum)
    "female_power": "21m00Tcm4TlvDq8ikWAM" # Dramatic Female (Rachel)
}

DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB" # Adam


def get_elevenlabs_api_key() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key and len(key.strip()) > 10:
        return key.strip()
    
    # Try reading from .env
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("ELEVENLABS_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if len(val) > 10:
                            return val
        except Exception:
            pass
    return None


def synthesize_elevenlabs_speech(
    text: str,
    output_path: Path,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str = "eleven_multilingual_v2"
) -> bool:
    api_key = get_elevenlabs_api_key()
    if not api_key:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.85,
            "style": 0.35,
            "use_speaker_boost": True
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "User-Agent": "Antigravity/1.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(output_path, "wb") as f:
                f.write(resp.read())
        return True
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"⚠️ ElevenLabs API Hatası ({e.code}): {err}")
        return False
    except Exception as e:
        print(f"⚠️ ElevenLabs Bağlantı Hatası: {e}")
        return False
