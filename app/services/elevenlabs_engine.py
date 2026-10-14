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

from app.services.turkish_phonetics import normalize_turkish_tts_text

# Renowned ElevenLabs voices (Native Turkish & Alpha English)
ELEVENLABS_VOICE_MAP = {
    # 🇹🇷 Native Turkish Voices (Zero Foreign Accent, Deep Cinema Baritone)
    "tr_alpha": "HV5rAif1q1ITHdvxqyMw",      # Alp (Deep & Cinematic Baritone - Natural Human Acting)
    "tr_whisper": "G5sdncpgy3DkDmvKJnFb",    # Taner Torun (Zihne Fısıldayan Adam / Karizma)
    "tr_emrullah": "QftNgZhlSOYKrYbk7WR7",   # Emrullah Tuna (Full Baritone)
    "tr_warm": "Mcb3pz5eWjKXcBhMLF4v",       # Ozgur (Deep Warm Male)

    # 🇬🇧/🇺🇸 English Voices
    "shelby": "JBFqnCBsd6RMkjVDRZzb",        # George (Deep British Peaky Cadence)
    "godfather": "VR6AewLTigWG4xSOukaG",     # Raspy Authority (Arnold)
    "narrator": "ErXwobaYiN019PkySvjV",      # Cinematic Documentary (Antoni)
    "stoic": "N2lVS1w4EtoT3dr4eOWO",         # Philosophical Gravitas (Callum)
    "female_power": "21m00Tcm4TlvDq8ikWAM"   # Dramatic Female (Rachel)
}

DEFAULT_TURKISH_VOICE_ID = "HV5rAif1q1ITHdvxqyMw" # Alp - Deep & Cinematic Baritone
DEFAULT_VOICE_ID = "HV5rAif1q1ITHdvxqyMw"


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
    model_id: str = "eleven_multilingual_v2",
    previous_text: str = "",
    next_text: str = "",
) -> bool:
    api_key = get_elevenlabs_api_key()
    if not api_key:
        return False

    # Apply Turkish Phonetic Normalization (e.g. %90'ı -> yüzde doksanı)
    clean_text = normalize_turkish_tts_text(text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    payload = {
        "text": clean_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.32,         # Lower stability = Rich emotional human cadence, zero robotic drone
            "similarity_boost": 0.75,
            "style": 0.45,             # Higher style = Cinema trailer dramatic tension
            "use_speaker_boost": True
        }
    }
    # Neighbouring lines keep intonation continuous when a script is voiced line by line.
    if previous_text:
        payload["previous_text"] = normalize_turkish_tts_text(previous_text)
    if next_text:
        payload["next_text"] = normalize_turkish_tts_text(next_text)
    
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
