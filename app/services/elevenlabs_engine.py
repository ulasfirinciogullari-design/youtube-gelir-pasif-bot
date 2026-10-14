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

# The narrator is the critic's most-cited problem ("monoton, enerjisiz ve yavaş", 14 voice issues in 8 critiques,
# 2026-10-14), and until now the model and settings were fixed in code, so nothing could be compared. The repo
# Variables ELEVENLABS_MODEL / ELEVENLABS_STABILITY / ELEVENLABS_STYLE / ELEVENLABS_SPEED now change them without a
# code change; unset (or empty) variables keep exactly the request below.
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_STABILITY = 0.32
DEFAULT_STYLE = 0.45
SPEED_RANGE = (0.7, 1.2)  # what ElevenLabs accepts for voice_settings.speed
# Models that read previous_text/next_text. eleven_v3 does not stitch lines that way and refuses them, and a refused
# line falls back to edge-tts, so an unknown model gets no context rather than a failed line.
CONTEXT_MODELS = ("eleven_multilingual_v2", "eleven_turbo_v2", "eleven_flash_v2", "eleven_monolingual_v1")
# eleven_v3 takes only these stability steps (Creative, Natural, Robust); any other value is snapped to the nearest.
V3_STABILITY_STEPS = (0.0, 0.5, 1.0)
last_error: dict = {}  # code and message of the last refused request (voice_samples adds a library voice on 404)


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


def _env_number(name: str, low: float, high: float) -> float | None:
    raw = os.environ.get(name, "").strip().replace(",", ".")  # "1,1" is how a Turkish owner writes 1.1
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print(f"ℹ️ {name}={raw!r} sayı değil, yok sayıldı")
        return None
    return round(min(high, max(low, value)), 3)


def voice_config(model_id: str | None = None, stability: float | None = None, style: float | None = None,
                 speed: float | None = None) -> dict:
    """The model and whichever voice settings were set: explicit arguments first (scripts/voice_samples.py compares
    settings side by side), then the repo Variables. Settings left out keep their defaults; speed is then not sent."""
    config = {"model": model_id or os.environ.get("ELEVENLABS_MODEL", "").strip() or DEFAULT_MODEL_ID}
    for name, value, (low, high) in (("speed", speed, SPEED_RANGE), ("style", style, (0.0, 1.0)),
                                     ("stability", stability, (0.0, 1.0))):
        value = _env_number(f"ELEVENLABS_{name.upper()}", low, high) if value is None else \
            round(min(high, max(low, float(value))), 3)
        if value is not None:
            config[name] = value
    return config


def synthesize_elevenlabs_speech(
    text: str,
    output_path: Path,
    voice_id: str = DEFAULT_VOICE_ID,
    model_id: str | None = None,
    previous_text: str = "",
    next_text: str = "",
    stability: float | None = None,
    style: float | None = None,
    speed: float | None = None,
) -> bool:
    last_error.clear()
    api_key = get_elevenlabs_api_key()
    if not api_key:
        return False

    # Apply Turkish Phonetic Normalization (e.g. %90'ı -> yüzde doksanı)
    clean_text = normalize_turkish_tts_text(text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    config = voice_config(model_id, stability, style, speed)
    model = config["model"]
    settings = {
        # Lower stability = Rich emotional human cadence, zero robotic drone
        "stability": config.get("stability", DEFAULT_STABILITY),
        "similarity_boost": 0.75,
        "style": config.get("style", DEFAULT_STYLE),  # Higher style = Cinema trailer dramatic tension
        "use_speaker_boost": True
    }
    if model.startswith("eleven_v3"):
        settings["stability"] = min(V3_STABILITY_STEPS, key=lambda step: abs(step - settings["stability"]))
    if "speed" in config:
        # Native speed changes the delivery itself; ffmpeg atempo only time-stretches a flat read.
        settings["speed"] = config["speed"]
    payload = {
        "text": clean_text,
        "model_id": model,
        "voice_settings": settings
    }
    # Neighbouring lines keep intonation continuous when a script is voiced line by line.
    if model.startswith(CONTEXT_MODELS):
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
        last_error.update(code=e.code, message=err)
        return False
    except Exception as e:
        print(f"⚠️ ElevenLabs Bağlantı Hatası: {e}")
        last_error.update(code=None, message=str(e))
        return False
