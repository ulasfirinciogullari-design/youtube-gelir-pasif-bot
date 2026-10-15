"""
Narration with Gemini's text-to-speech models, a much cheaper candidate than ElevenLabs.

At 24 Shorts a day the Alp narration (eleven_multilingual_v2, one credit a character, ~480 characters a Short)
needs the ~$99 ElevenLabs Pro plan. Gemini TTS bills ~$9-20 per million audio tokens at 25 tokens a second
(Google Cloud pricing, 2026-10-15), so a 25 s Short costs well under a cent. Ulaş has to hear it before it
replaces Alp: scripts/voice_samples.py --set maliyet voices the same lines with both.

The models are previews whose names change, so the newest flash TTS model the key can call is looked up at run
time; GEMINI_TTS_MODEL pins one.
"""

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

API = "https://generativelanguage.googleapis.com/v1beta"
# Gemini follows a reading instruction given before the text (Google's examples write it in English, which keeps it
# from being read aloud); without one it reads the line slowly and flat.
STYLE_PROMPT = "Say in Turkish, like an energetic, fast-talking documentary narrator of a viral YouTube Short:"
PCM_RATE = 24000  # Gemini returns raw 16-bit mono PCM at 24 kHz
RETRY_SECONDS = (20, 40)  # preview models have low per-minute limits; a 429 usually clears within a minute
last_error: dict = {}


def get_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API") or None


def _version(name: str) -> float:
    match = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(match.group(1)) if match else 0.0


def pick_model(names: list[str], tier: str = "flash") -> str | None:
    """The newest TTS model of the tier ("flash" or "pro"), else the newest TTS model of any tier for flash."""
    tts = [n for n in names if "tts" in n.lower()]
    wanted = [n for n in tts if tier in n.lower()]
    for group in (wanted, tts if tier == "flash" else []):
        if group:
            return max(group, key=_version)
    return None


def list_models(key: str) -> list[str]:
    req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        models = json.loads(resp.read().decode("utf-8")).get("models", [])
    return [m["name"].split("/", 1)[-1] for m in models
            if isinstance(m, dict) and m.get("name") and "generateContent" in (m.get("supportedGenerationMethods") or [])]


def resolve_model(key: str, tier: str = "flash") -> str | None:
    pinned = os.environ.get("GEMINI_TTS_MODEL", "").strip()
    if pinned and tier == "flash":
        return pinned
    try:
        return pick_model(list_models(key), tier)
    except Exception as e:
        last_error.update(code=None, message=f"model listesi okunamadı: {str(e)[:200]}")
        return None


def request_body(text: str, voice: str, style: str = STYLE_PROMPT) -> dict:
    return {
        "contents": [{"parts": [{"text": f"{style} {text}".strip()}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }


def audio_from_response(data: dict) -> bytes | None:
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def synthesize(text: str, out: Path, voice: str, model: str, key: str, style: str = STYLE_PROMPT) -> bool:
    """Writes `text` spoken by the Gemini voice to `out` (any ffmpeg audio format). False on failure, with the
    reason in last_error."""
    from app.services.turkish_phonetics import normalize_turkish_tts_text

    text = normalize_turkish_tts_text(text)  # "10.000" is read as a year or digit by digit otherwise
    last_error.clear()
    req = urllib.request.Request(
        f"{API}/models/{model}:generateContent",
        data=json.dumps(request_body(text, voice, style)).encode("utf-8"),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    for wait in (*RETRY_SECONDS, None):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = " ".join(e.read().decode("utf-8", "replace").split())[:300]
            last_error.update(code=e.code, message=detail)
            if e.code not in (429, 500, 503) or wait is None:
                return False
            time.sleep(wait)
        except Exception as e:
            last_error.update(code=None, message=str(e)[:300])
            return False
    pcm = audio_from_response(data)
    if not pcm:
        last_error.update(code=None, message="yanıtta ses yok")
        return False
    out = Path(out)
    raw = out.with_suffix(".pcm")
    raw.write_bytes(pcm)
    try:
        subprocess.run(["ffmpeg", "-y", "-f", "s16le", "-ar", str(PCM_RATE), "-ac", "1", "-i", str(raw), str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        raw.unlink(missing_ok=True)
    return out.exists() and out.stat().st_size > 1000
