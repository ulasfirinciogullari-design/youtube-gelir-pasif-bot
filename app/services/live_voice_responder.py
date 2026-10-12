"""
Real-Time Live Stream Human Streamer Voice Engine for 'Zirvenin Kanunu'.
Transforms real YouTube Live chat messages into 100% authentic human streamer voice responses.
Features:
- Sounds like a real Turkish gamer/streamer sitting at his PC with a microphone.
- ZERO AI/bot mentions, zero corporate fluff, natural friendly Turkish streamer jargon.
- Intelligently cleans usernames (no '@' or trailing numbers read aloud).
- Handles multi-viewer chat storms with natural batched shoutouts.
- Organically reminds viewers to subscribe for the 100K goal.
- Mastered with broadcast loudnorm (-14 LUFS, peak -1 dB) for maximum vocal clarity.
"""

import os
import sys
import re
import json
import time
import queue
import random
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

ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY", "")
# Alp: Turkish Deep, Charismatic Voice (Persona: Channel Owner / Streamer)
ALP_VOICE_ID = "HV5rAif1q1ITHdvxqyMw"

GROK_KEY = os.getenv("GROK_API_KEY", "")
GROK_BASE = os.getenv("GROK_BASE_URL", "https://codex-everywhere.com/v1")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://codex-everywhere.com/v1")


def clean_streamer_username(raw_author: str) -> str:
    """
    Cleans raw YouTube usernames into natural spoken names or friendly nicknames.
    e.g. '@aylakyak9841' -> 'Aylak', '@ahmet_34' -> 'Ahmet', '@gamer_pro' -> 'Gamer'.
    Never outputs '@' or random digits.
    """
    name = raw_author.strip().lstrip('@')
    # Strip trailing numbers and special characters
    name = re.sub(r'[\d_.-]+$', '', name)
    name = re.sub(r'[_.-]+', ' ', name).strip()
    words = name.split()
    if not words:
        return "kral"
    cand = words[0].capitalize()
    if cand.lower().startswith("user") or cand.lower().startswith("kullanici"):
        return "kral"
    if cand.lower() == "aylakyak":
        return "Aylak"
    if len(cand) > 10:
        cand = cand[:8]
    return cand


# Dynamic Human Streamer Fallback Responses (Zero AI, Pure Authentic Gamer/Streamer)
FALLBACK_GREETINGS = [
    "Aleyküm selam {name}, hoş geldin kardeşim! Eyvallah desteğin için.",
    "Aleyküm selam {name} kanka, hoş geldin yayına! Keyifli seyirler.",
    "Selamlar {name}, hoş geldin kral! Eyvallah, bakalım rekoru kovalıyoruz.",
    "Aleyküm selam {name} kardeşim, sohbette kal kralsın!",
    "Hoş geldin {name} kardeşim, aleyküm selam! Dinozor fena gidiyor bu akşam."
]

FALLBACK_RECORD_QUESTIONS = [
    "Valla {name}, hedef 1 milyon puan kanka! Dinozor bu gece fena yardırıyor, durduramıyoruz.",
    "Senin rekor kaç {name}? Bizimki durdurulamıyor şu an valla, bakalım nereye kadar gidecek.",
    "Şu an fena gidiyor {name} kardeşim, gözümüzü kırpmadan izliyoruz valla!",
    "Rekor denemesindeyiz {name} kanka, inşallah bu gece kıracağız o rekoru!"
]

FALLBACK_SUPPORT = [
    "Adamsın {name}! Eyvallah kral, cansın desteğin için çok sağ ol.",
    "Cansın {name}, desteğin için çok teşekkürler kardeşim! Hedef 100 bin abone, kralsın.",
    "Eyvallah {name} kardeşim, cansın! Hep beraber zirveye inşallah, abone olan eller dert görmesin."
]

FALLBACK_GENERAL = [
    "Hoş geldin {name} kardeşim! Yayındayız tam gaz, keyifli seyirler.",
    "Eyvallah {name} kanka, sohbette kal kralsın! 100k yolunda desteğiniz çok değerli.",
    "Çok sağ ol {name} kardeşim, rekoru izliyoruz hep beraber!"
]

ORGANIC_SUB_CTAS = [
    " Bu arada krallar hedefimiz 100 bin abone, henüz abone olmayanlar bir tıkla aileye katılsın hadi!",
    " Bu arada aramıza yeni katılan krallar abone olup bildirimleri açarsa çok sevinirim, hedef 100k!",
    " Hedef 100 bin abone beyler, bir abone ol butonunu kıralım hep beraber!"
]

PERIODIC_IDLE_TALKS = [
    "Beyler hoş geldiniz yayına! Dinozor 1 milyona doğru koşuyor, rekoru zorluyoruz. Sohbetten tahminlerinizi yazın krallar, kanala da abone olmayı unutmayın, hedef 100 bin!",
    "Krallar yayındayız tam gaz! Dinozor bu gece hiç affetmiyor, engelleri tek tek aşıyoruz. Sohbete yazın selamınızı alalım, abone olmayı da unutmayın!",
    "Hoş geldiniz beyler! Google T-Rex dünya rekorunu zorluyoruz. Hedefimiz 100 bin abone, aileye katılmak için bir abone ol butonuna çökelim hadi!"
]


class LiveVoiceResponder:
    """
    Handles live chat message intelligence and high-speed voice synthesis.
    Behaves 100% like a real human Turkish streamer.
    """
    def __init__(self, voice_id=ALP_VOICE_ID):
        self.voice_id = voice_id
        self.audio_queue = queue.Queue()
        self.history_replied = set()
        self.lock = threading.Lock()
        self.response_counter = 0

        # Multi-viewer debounce buffer
        self.pending_messages = []
        self.last_message_time = 0
        self.debounce_timer = None

    def generate_human_streamer_reply(self, author_name: str, message_text: str) -> str:
        """
        Generates a 100% natural, casual, friendly Turkish streamer reply.
        Zero AI mentions, authentic human gamer vibe.
        """
        clean_name = clean_streamer_username(author_name)
        self.response_counter += 1
        include_sub_cta = (self.response_counter % 3 == 0)

        prompt = f"""Sen YouTube'da 7/24 kesintisiz oyun canlı yayını yapan gerçek, samimi bir Türk yayıncısın (Kanal Sahibi).
Karakterin: 25-30 yaşlarında, mikrofon başında, oyun oynarken chat'e cevap veren çok samimi, doğal, sıcak bir yayıncı (Twitch / YouTube tarzı).
Şu an chat'e bir izleyici yazdı.
İzleyici Adı: {clean_name}
Mesajı: {message_text}

GÖREV: Sanki o an mikrofona konuşuyormuş gibi {clean_name}'e 1 veya en fazla 2 cümlelik ÇOK DOĞAL, SAMİMİ ve SICAK bir yayıncı cevabı ver.

ÖNEMLİ KURALLAR:
1. KESİNLİKLE YAPAY ZEKA GİBİ KONUŞMA! Asla robotik, resmi, edebi veya kitabi cümle kurma.
2. İzleyen kişi kesinlikle senin bilgisayar başında oturan gerçek bir insan olduğunu düşünmeli.
3. Yayıncı jargonu kullan: 'Eyvallah kardeşim', 'Aleyküm selam kral', 'Hoş geldin kanka', 'Cansın desteğin için', 'Bakalım rekor gelecek mi'.
4. ASLA tırnak işareti, emoji listesi, '@' işareti veya sayıları okuma.
5. ASLA 'Zirvenin Kanunu'nda yerin hazır', 'Harika soru', 'Karanlık psikoloji gereği' gibi kurumsal/yapay zeka klişeleri KULLANMA.
6. Maksimum 15-20 kelime, mikrofona söylenecek doğrudan Türkçe konuşma metni döndür."""

        if include_sub_cta:
            prompt += "\n7. Cümlenin sonuna çok doğal bir şekilde 'Hedefimiz 100 bin abone, abone olmayı unutmayın krallar' gibi samimi bir abone olma çağrısı ekle."

        # 1. Try Grok LLM
        if GROK_KEY:
            try:
                headers = {"Authorization": f"Bearer {GROK_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "grok-beta",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 70,
                    "temperature": 0.8
                }
                res = requests.post(f"{GROK_BASE}/chat/completions", headers=headers, json=payload, timeout=4)
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        clean_res = text.replace('"', '').replace("'", "").replace('@', '').strip()
                        return clean_res
            except Exception:
                pass

        # 2. Try OpenAI LLM
        if OPENAI_KEY:
            try:
                headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 70,
                    "temperature": 0.8
                }
                res = requests.post(f"{OPENAI_BASE}/chat/completions", headers=headers, json=payload, timeout=4)
                if res.status_code == 200:
                    text = res.json()["choices"][0]["message"]["content"].strip()
                    if text:
                        clean_res = text.replace('"', '').replace("'", "").replace('@', '').strip()
                        return clean_res
            except Exception:
                pass

        # 3. Contextual Rule-Based Fallback (Instant & Natural)
        msg_lower = message_text.lower()
        if any(w in msg_lower for w in ["selam", "merhaba", "sa", "hey", "günaydın", "iyi yayınlar", "kolay gelsin"]):
            base = random.choice(FALLBACK_GREETINGS).format(name=clean_name)
        elif any(w in msg_lower for w in ["rekor", "kaç", "skor", "puan", "yanar", "ölür", "hız"]):
            base = random.choice(FALLBACK_RECORD_QUESTIONS).format(name=clean_name)
        elif any(w in msg_lower for w in ["abone", "takip", "destek", "like", "beğen", "helal", "adamsın", "kral"]):
            base = random.choice(FALLBACK_SUPPORT).format(name=clean_name)
        else:
            base = random.choice(FALLBACK_GENERAL).format(name=clean_name)

        if include_sub_cta:
            base += random.choice(ORGANIC_SUB_CTAS)
        return base

    def generate_batch_reply(self, message_batch: list) -> str:
        """
        Generates a natural unified reply when multiple viewers write at the same time.
        e.g. 'Aleyküm selam Aylak, Caner hoş geldiniz beyler! Eyvallah krallar...'
        """
        names = [clean_streamer_username(m["author_name"]) for m in message_batch]
        # Unique names
        unique_names = list(dict.fromkeys(names))
        if len(unique_names) == 1:
            return self.generate_human_streamer_reply(unique_names[0], message_batch[0]["message_text"])

        names_str = ", ".join(unique_names[:-1]) + " ve " + unique_names[-1] if len(unique_names) > 1 else unique_names[0]
        greeting = f"Aleyküm selam {names_str}! Beyler hepiniz hoş geldiniz yayına, eyvallah krallar! Hedef 100 bin abone, destekleriniz için çok sağ olun!"
        return greeting

    def synthesize_speech(self, text: str, out_filename: str) -> Path:
        """
        Synthesizes voice using ElevenLabs Alp with ultra-natural human conversational settings.
        Applies professional loudnorm audio mastering (-14 LUFS, peak -1 dB) so speech is crisp,
        punchy, and louder than the background music.
        """
        mp3_path = VOICE_DIR / f"{out_filename}.mp3"
        wav_path = VOICE_DIR / f"{out_filename}.wav"

        # 1. ElevenLabs Alp High-Fidelity Synthesis
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
                        "stability": 0.38,          # Natural human variation & breathing
                        "similarity_boost": 0.78,   # Authentic voice likeness
                        "style": 0.28,              # Dynamic conversational expressiveness
                        "use_speaker_boost": True
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

                    # Master with loudnorm filter for broadcast voice clarity (-14 LUFS)
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", str(mp3_path),
                        "-af", "loudnorm=I=-14:TP=-1.0:LRA=7",
                        "-ar", "44100",
                        "-ac", "2",
                        "-f", "wav",
                        str(wav_path)
                    ]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    return wav_path
            except Exception as e:
                print(f"⚠️ [LiveVoiceResponder] ElevenLabs error: {e}")

        # 2. Edge-TTS Fallback (Turkish Natural Baritone)
        try:
            edge_cmd = [
                sys.executable, "-m", "edge_tts",
                "--voice", "tr-TR-AhmetNeural",
                "--pitch", "-2Hz",
                "--text", text,
                "--write-media", str(mp3_path)
            ]
            subprocess.run(edge_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if mp3_path.exists():
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(mp3_path),
                    "-af", "loudnorm=I=-14:TP=-1.0:LRA=7",
                    "-ar", "44100",
                    "-ac", "2",
                    "-f", "wav",
                    str(wav_path)
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                return wav_path
        except Exception as e:
            print(f"⚠️ [LiveVoiceResponder] Edge-TTS fallback error: {e}")

        return wav_path if wav_path.exists() else None

    def _flush_pending_batch(self):
        """Flushes debounced message batch and synthesizes voice."""
        with self.lock:
            batch = list(self.pending_messages)
            self.pending_messages.clear()
            self.debounce_timer = None

        if not batch:
            return

        if len(batch) == 1:
            m = batch[0]
            reply_text = self.generate_human_streamer_reply(m["author_name"], m["message_text"])
            author_label = clean_streamer_username(m["author_name"])
        else:
            reply_text = self.generate_batch_reply(batch)
            author_label = "Krallar"

        print(f"🎙️ [LiveVoiceResponder] Yayıncı Konuşuyor: \"{reply_text}\"")

        h = hashlib.md5(f"{time.time()}_{reply_text}".encode()).hexdigest()[:8]
        out_name = f"resp_{h}"
        wav_path = self.synthesize_speech(reply_text, out_name)

        if wav_path and wav_path.exists():
            item = {
                "author": author_label,
                "reply_text": reply_text,
                "audio_path": wav_path,
                "created_at": time.time()
            }
            self.audio_queue.put(item)
            print(f"🔊 [LiveVoiceResponder] Ses canlı yayın kuyruğuna girdi! -> {wav_path.name}")

    def enqueue_async(self, author_name: str, message_text: str, msg_id: str = None):
        """
        Dispatches incoming message with multi-viewer debounce buffer (2.5 seconds window).
        Ensures multiple simultaneous chatters are grouped without being dropped!
        """
        key = msg_id or f"{author_name}_{message_text}"
        with self.lock:
            if key in self.history_replied:
                return
            self.history_replied.add(key)
            self.pending_messages.append({
                "author_name": author_name,
                "message_text": message_text,
                "msg_id": msg_id,
                "time": time.time()
            })

            # Reset or start debounce timer
            if self.debounce_timer is not None:
                self.debounce_timer.cancel()

            self.debounce_timer = threading.Timer(2.5, self._flush_pending_batch)
            self.debounce_timer.daemon = True
            self.debounce_timer.start()

    def get_periodic_idle_speech(self) -> Path:
        """Synthesizes a natural spontaneous streamer talk when chat is quiet."""
        text = random.choice(PERIODIC_IDLE_TALKS)
        h = hashlib.md5(f"{time.time()}_idle".encode()).hexdigest()[:8]
        out_name = f"idle_{h}"
        wav_path = self.synthesize_speech(text, out_name)
        if wav_path and wav_path.exists():
            item = {
                "author": "Yayıncı",
                "reply_text": text,
                "audio_path": wav_path,
                "created_at": time.time()
            }
            self.audio_queue.put(item)
            return wav_path
        return None

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
    print("Testing LiveVoiceResponder with human streamer persona...")
    resp.enqueue_async("@aylakyak9841", "selamlar rekor kaç şu an?", "test_msg_1")
    time.sleep(6)
    item = resp.get_next_audio_response()
    print("Synthesized Item:", item)
