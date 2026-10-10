"""
AI SCRIPT ORCHESTRATOR & HOOK ENGINE
Integrates Grok 4.6, Claude Sonnet, and OpenAI APIs with resilient fallbacks
to craft high-adrenaline, psychological, seamless-loop viral scripts for YouTube Shorts.
"""

import json
import os
from pathlib import Path
import random
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import urllib.request
import urllib.error

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read credentials from environment or .env
GROK_API_KEY = os.getenv("GROK_API_KEY", "sk-c2fbc846c947b5af91e5fb41623af8f3c814ff83e9fbeaddd71e04f35f942c32")
GROK_BASE_URL = os.getenv("GROK_BASE_URL", "https://codex-everywhere.com/v1")

ANTHROPIC_AUTH_TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN", "sk-d6180a6515594d3046c4aca3a460fdcdc8d82bcd0e1fa382f359524550e210e1")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://codex-everywhere.com")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "sk-5ccb8fa60c1a0afa2f663c257ba76f9f637c9ce99944425978a2b73bc68cb01f")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://codex-everywhere.com/v1")


SYSTEM_PROMPT = """Sen dünyanın en iyi karanlık psikoloji, güç yasaları ve viral YouTube Shorts senaristi ve kurgu yönetmenisin.
Görevin: İzleyiciyi ilk 1.5 saniyede ekrana çivileyen ve videonun son kelimesi ilk kelimesine kusursuzca bağlanan (sonsuz döngü / seamless loop) 15-18 saniyelik hipnotik bir metin üretmek.

Format (kesinlikle JSON olarak yanıt ver):
{
  "title": "👑 Başlık #shorts",
  "scenes": [
    "1. Cümle: Sarsıcı kanca soru (Hook)",
    "2. Cümle: Beklenmedik psikolojik gerçek",
    "3. Cümle: Zirveye tırmanış / Soğuk gerçek",
    "4. Cümle: Döngü kancası (cümlenin sonu 1. cümlenin başına akacak şekilde '...çünkü', '...işte bu yüzden')"
  ],
  "pinned_comment": "İzleyicinin egosuna dokunan ve tartışma başlatan 1 soru."
}
"""


def generate_with_grok(topic: str) -> dict | None:
    """Attempts generation via Grok 4.6 on codex-everywhere."""
    url = f"{GROK_BASE_URL}/responses"
    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"Konu: {topic}\nLütfen yukarıdaki sistem talimatına göre JSON formatında 15 saniyelik sonsuz döngü metni üret."
    payload = {
        "model": "grok-4.6",
        "input": prompt
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            output = data.get("output", [])
            if output:
                raw_text = output[0].get("summary", [{}])[0].get("text", "") or output[0].get("content", "")
                if "{" in raw_text and "}" in raw_text:
                    json_str = raw_text[raw_text.find("{"):raw_text.rfind("}") + 1]
                    return json.loads(json_str)
    except Exception as e:
        print(f"ℹ️ Grok motoru çağrı notu: {e}")
    return None


def generate_with_claude(topic: str) -> dict | None:
    """Attempts generation via Claude on codex-everywhere."""
    url = f"{ANTHROPIC_BASE_URL}/v1/messages"
    headers = {
        "Authorization": f"Bearer {ANTHROPIC_AUTH_TOKEN}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 300,
        "messages": [
            {"role": "user", "content": f"{SYSTEM_PROMPT}\n\nKonu: {topic}"}
        ]
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_text = data.get("content", [{}])[0].get("text", "")
            if "{" in raw_text and "}" in raw_text:
                json_str = raw_text[raw_text.find("{"):raw_text.rfind("}") + 1]
                return json.loads(json_str)
    except Exception as e:
        print(f"ℹ️ Claude motoru çağrı notu: {e}")
    return None


def get_viral_script(topic: str = "Thomas Shelby Güç Yasaları") -> dict:
    """
    Orchestrates AI generation: Tries Claude -> Grok -> Fallback Hypersonic Catalog.
    Guarantees 100% uptime with no pipeline failure.
    """
    print(f"\n🧠 [AI ORCHESTRATOR] '{topic}' için en üst düzey senaryo üretiliyor...")
    
    # 1. Try Claude Sonnet
    res = generate_with_claude(topic)
    if res and "scenes" in res:
        print("⚡ [AI SCRIPT] Claude Sonnet tarafından üretildi!")
        return res

    # 2. Try Grok 4.6
    res = generate_with_grok(topic)
    if res and "scenes" in res:
        print("⚡ [AI SCRIPT] Grok 4.6 tarafından üretildi!")
        return res

    # 3. High-Voltage Hypersonic Precision Fallback
    print("⚡ [AI SCRIPT] Yüksek Gerilimli Hipersonik Kasa devrede!")
    curated_fallbacks = [
        {
            "title": "👑 Thomas Shelby: Odanın En Tehlikeli Adamı #shorts",
            "scenes": [
                "Bir ortamdaki en tehlikeli insanın kim olduğunu biliyor musun?",
                "Herkes sesini yükseltirken o sadece dinler.",
                "Zayıf insanlar öfkesini kusar, güçlü olanlar ise hamlesini planlar.",
                "İşte bu yüzden asla unutma: Bir masada herkes konuşurken tek bir adam susuyorsa..."
            ],
            "pinned_comment": "👑 Zayıf bir insan öfkelenir, güçlü bir insan susar ve sonucunu bekler. Sen hangisisin? Dürüstçe yaz."
        },
        {
            "title": "👑 48 Güç Yasası: Asla Yapmaman Gereken Ölümcül Hata #shorts",
            "scenes": [
                "İnsanların kariyerini bitiren en ölümcül hata nedir bilir misin?",
                "Liderinden daha zeki görünmeye çalışmak.",
                "Kendi yeteneğini gizle, zaferi daima ona bırak.",
                "Çünkü güç sarhoşları gölgede kalmayı asla affetmez, işte bu yüzden..."
            ],
            "pinned_comment": "👑 Liderinden daha akıllı olduğunu ona hissettirmek cesaret midir yoksa aptallık mı? Fikrini yaz."
        },
        {
            "title": "👑 Walter White: Tehlikenin Ta Kendisi #shorts",
            "scenes": [
                "Korkak bir adam ne zaman en büyük canavara dönüşür bilir misin?",
                "Kaybedecek hiçbir şeyi kalmadığı an.",
                "Artık o tehlikede değildir, tehlikenin ta kendisidir.",
                "Ve dünya ona diz çöktürmeye çalıştığında tek bir gerçeği anlar..."
            ],
            "pinned_comment": "👑 Kaybedecek bir şeyi kalmayan adam mı daha tehlikelidir, yoksa her şeyi olan mı? Cevabını bırak."
        }
    ]
    return random.choice(curated_fallbacks)


if __name__ == "__main__":
    script = get_viral_script("Sessiz Güç ve Manipülasyon")
    print(json.dumps(script, ensure_ascii=False, indent=2))
