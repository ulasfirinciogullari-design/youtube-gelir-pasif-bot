"""
Global Dubbing & Multilingual Audio Synthesis Engine for 'Zirvenin Kanunu'.
Powered by ElevenLabs Creator API tier.
Synthesizes cinematic baritone voice tracks across Turkish, English, Spanish, and German.
"""

import os
import sys
import json
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "sk_5eb48c74be289f0ece0f80d358fa318715fee8c9c266490f")

VOICE_PROFILES = {
    "tr": {
        "name": "Alp (Native Turkish Baritone)",
        "voice_id": "HV5rAif1q1ITHdvxqyMw",
        "model_id": "eleven_multilingual_v2",
        "stability": 0.55,
        "similarity_boost": 0.85
    },
    "en": {
        "name": "Adam (Deep Cinematic English)",
        "voice_id": "pNInz6obpgDQGcFmaJgB",
        "model_id": "eleven_multilingual_v2",
        "stability": 0.50,
        "similarity_boost": 0.85
    },
    "es": {
        "name": "Arnold (Dramatic Spanish)",
        "voice_id": "VR6AewLTigWG4xSOukaG",
        "model_id": "eleven_multilingual_v2",
        "stability": 0.55,
        "similarity_boost": 0.80
    },
    "de": {
        "name": "Antoni (German Authority Baritone)",
        "voice_id": "ErXwobaYiN019PkySvjV",
        "model_id": "eleven_multilingual_v2",
        "stability": 0.55,
        "similarity_boost": 0.80
    }
}

class GlobalDubbingEngine:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or ELEVENLABS_API_KEY

    def synthesize(self, text: str, lang: str = "tr", output_path: str = None) -> Path:
        lang = lang.lower()
        if lang not in VOICE_PROFILES:
            raise ValueError(f"Desteklenmeyen dil: {lang}. Desteklenen diller: {list(VOICE_PROFILES.keys())}")

        profile = VOICE_PROFILES[lang]
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{profile['voice_id']}"
        
        payload = {
            "text": text,
            "model_id": profile["model_id"],
            "voice_settings": {
                "stability": profile["stability"],
                "similarity_boost": profile["similarity_boost"],
                "style": 0.25,
                "use_speaker_boost": True
            }
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "xi-api-key": self.api_key,
                "User-Agent": "ZirveninKanunu-Engine"
            }
        )

        if not output_path:
            output_dir = BASE_DIR / "output" / "global_audio"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"dub_{lang}_{hash(text) % 100000}.mp3"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"🎙️ [{profile['name']}] Seslendiriliyor ({len(text)} karakter)...")
        try:
            with urllib.request.urlopen(req) as response:
                with open(output_path, "wb") as f:
                    f.write(response.read())
            print(f"✅ Ses dosyası oluşturuldu: {output_path.name}")
            return output_path
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            print(f"❌ ElevenLabs Sentez Hatası: {e.code} - {err_msg}")
            raise

    def generate_srt_subtitles(self, scene_texts: list, output_srt_path: str):
        """
        Creates a timed SRT subtitle file from scene texts.
        """
        output_srt = Path(output_srt_path)
        output_srt.parent.mkdir(parents=True, exist_ok=True)
        
        current_time = 0.0
        lines = []
        for idx, (scene_text, duration_sec) in enumerate(scene_texts, 1):
            start_s = current_time
            end_s = current_time + duration_sec
            current_time = end_s

            start_str = self._format_timestamp(start_s)
            end_str = self._format_timestamp(end_s)

            lines.append(f"{idx}")
            lines.append(f"{start_str} --> {end_str}")
            lines.append(f"{scene_text.strip()}\n")

        with open(output_srt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"📝 SRT altyazı dosyası oluşturuldu: {output_srt.name}")
        return output_srt

    def _format_timestamp(self, seconds: float) -> str:
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

if __name__ == "__main__":
    engine = GlobalDubbingEngine()
    print("🌐 GlobalDubbingEngine hazır. Voice profiles:", list(VOICE_PROFILES.keys()))
