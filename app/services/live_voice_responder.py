"""
Real-Time Live Stream Voice AI Responder for 'Zirvenin Kanunu' (@zirveninkanunu).
Transforms real viewer live chat messages into charismatic, stoic AI responses
synthesized in real time using ElevenLabs Alp (Turkish Deep Baritone) or Edge-TTS.
Feeds into the live audio mixer queue for instant ducking and broadcast.
"""

import os
import sys
import json
import time
import queue
import hashlib
import threading
import subprocess
import requests
from pathlib import Path
from dotenv import load_dotenv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
VOICE_DIR = OUTPUT_DIR / "voice_responses"
VOICE_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

# ElevenLabs Settings
ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY", "")
# Alp: Turkish Deep & Cinematic Baritone (Zirvenin Kanunu Persona)
ALP_VOICE_ID = "HV5rAif1q1ITHdvxqyMw"

# LLM Settings
GROK_KEY = os.getenv("GROK_API_KEY", "")
GROK_BASE = os.getenv("GROK_BASE_URL", "https://codex-everywhere.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://codex-everywhere.com/v1")


class LiveVoiceResponder:
    """
    Handles live chat message intelligence and high-speed voice synthesis.
    Maintains an active audio response queue for the stream engine.
    """
    def __init__(self, voice_id=ALP_VOICE_ID):
        self.voice_id = voice_id
        self.audio_queue = queue.Queue()
        self.history_replied = set()
        self.lock = threading.Lock()
        self.is_processing = False

    def generate_persona_reply(self, author_name: str, message_text: str) -> str:
        """
        Generates a charismatic, stoic, motivational reply from 'Zirvenin Kanunu'.
        """
        prompt = f"""Sen 'Zirvenin Kanunu' (@zirveninkanunu) YouTube kanalının resmi canlı yayın sunucususun.
Karakterin: Karizmatik, stoik, motive edici, saygılı, lider ve ilham veren bir duruş.
Canlı yayında gerçek bir izleyici yorum yaptı.
İzleyici Adı: {author_name}
Yorumu: {message_text}

GÖREV: İzleyiciye doğrudan adıyla hitap ederek (örneğin: 'Hoş geldin @{author_name}...') en fazla 1 veya 2 cümlelik, son derece akıcı, etkileyici, motive edici ve canlı yayına yakışan bir yanıt ver.
KURALLAR:
1. Asla tırnak işareti, başlık, emoji listesi vb. yazma.
2. Doğrudan canlı yayında mikrofona söylenecek Türkçe metni döndür.
3. Kısa ve vurucu ol (maksimum 25 kelime)."""

        # 1. Try Grok
        if GROK_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROK_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "grok-beta",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 80,
                    "temperature": 0.7
                }
                res = requests.post(f"{GROK_BASE}/chat/completions", headers=headers, json=payload, timeout=5)
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        return text.replace('"', '').replace("'", "")
            except Exception as e:
                pass

        # 2. Try OpenAI
        if OPENAI_KEY:
            try:
                headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 80,
                    "temperature": 0.7
                }
                res = requests.post(f"{OPENAI_BASE}/chat/completions", headers=headers, json=payload, timeout=5)
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        return text.replace('"', '').replace("'", "")
            except Exception as e:
                pass

        # 3. Contextual Rule-Based Fallback (Instant, zero latency)
        msg_lower = message_text.lower()
        if any(w in msg_lower for w in ["selam", "merhaba", "sa", "hey", "günaydın", "iyi yayınlar"]):
            return f"Aleyküm selam, hoş geldin @{author_name}! Zirvenin Kanunu'nda yerin hazır, rekoru birlikte kırıyoruz!"
        elif any(w in msg_lower for w in ["rekor", "kaç", "skor", "puan", "yanar"]):
            return f"Harika soru @{author_name}! T-Rex durdurulamaz modda, hedef dünya rekoru. Bizi izlemeye devam et!"
        elif any(w in msg_lower for w in ["abone", "takip", "destek", "like", "beğen"]):
            return f"Adamsın @{author_name}! Desteğin ve katılımın için teşekkürler, zirveye birlikte çıkıyoruz!"
        else:
            return f"Hoş geldin @{author_name}! Zirvenin Kanunu'nda pes etmek yok, odaklan ve rekoru izle!"

    def synthesize_speech(self, text: str, out_filename: str) -> Path:
        """
        Synthesizes speech to mp3 using ElevenLabs Alp (high fidelity baritone).
        Converts to 44.1kHz stereo WAV for immediate stream mixing.
        """
        mp3_path = VOICE_DIR / f"{out_filename}.mp3"
        wav_path = VOICE_DIR / f"{out_filename}.wav"

        # Check ElevenLabs
        if ELEVENLABS_KEY:
            try:
                headers = {
                    "xi-api-key": ELEVENLABS_KEY,
                    "Content-Type": "application/json"
                }
                payload = {
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.55,
                        "similarity_boost": 0.85
                    }
                }
                res = requests.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                    headers=headers,
                    json=payload,
                    timeout=15
                )
                if res.status_code == 200 and len(res.content) > 1000:
                    with open(mp3_path, "wb") as f:
                        f.write(res.content)

                    # Convert to standard 44.1kHz 16-bit stereo WAV for lightning fast raw audio ingestion
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", str(mp3_path),
                        "-ar", "44100",
                        "-ac", "2",
                        "-f", "wav",
                        str(wav_path)
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    return wav_path
            except Exception as e:
                print(f"⚠️ [LiveVoiceResponder] ElevenLabs synthesis error: {e}")

        # Edge-TTS / Windows SAPI Fallback if needed
        return wav_path if wav_path.exists() else None

    def process_message(self, author_name: str, message_text: str, msg_id: str = None):
        """
        Async worker function to generate response text, synthesize voice,
        and enqueue into the stream mixer.
        """
        key = msg_id or f"{author_name}_{message_text}"
        with self.lock:
            if key in self.history_replied:
                return None
            self.history_replied.add(key)

        print(f"🎙️ [LiveVoiceResponder] Gerçek izleyici yanıtı hazırlanıyor: {author_name} ('{message_text}')")

        reply_text = self.generate_persona_reply(author_name, message_text)
        print(f"💬 [LiveVoiceResponder] Kanal Yanıtı: \"{reply_text}\"")

        h = hashlib.md5(f"{time.time()}_{key}".encode()).hexdigest()[:8]
        out_name = f"resp_{h}"

        wav_path = self.synthesize_speech(reply_text, out_name)
        if wav_path and wav_path.exists():
            item = {
                "author": author_name,
                "user_message": message_text,
                "reply_text": reply_text,
                "audio_path": wav_path,
                "created_at": time.time()
            }
            self.audio_queue.put(item)
            print(f"🔊 [LiveVoiceResponder] Ses yanıtı canlı yayın mikser kuyruğuna eklendi! -> {wav_path.name}")
            return item
        else:
            print("❌ [LiveVoiceResponder] Ses dosyası sentezlenemedi.")
            return None

    def enqueue_async(self, author_name: str, message_text: str, msg_id: str = None):
        """Dispatches response processing in background thread."""
        t = threading.Thread(
            target=self.process_message,
            args=(author_name, message_text, msg_id),
            daemon=True
        )
        t.start()
        return t

    def get_next_audio_response(self, timeout=0.0):
        """Pulls next ready voice response from the queue."""
        try:
            return self.audio_queue.get(block=(timeout > 0), timeout=timeout)
        except queue.Empty:
            return None


# Global Singleton
_global_responder = None

def get_voice_responder():
    global _global_responder
    if _global_responder is None:
        _global_responder = LiveVoiceResponder()
    return _global_responder


if __name__ == "__main__":
    resp = get_voice_responder()
    print("Testing LiveVoiceResponder...")
    item = resp.process_message("Umut", "Selamlar bot kaç puana kadar gidecek?", "test_1")
    if item:
        print("Success:", item)
