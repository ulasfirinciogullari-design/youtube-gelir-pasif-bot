"""
Channel SEO & Global Dominance Optimizer for Zirvenin Kanunu (@zirveninkanunu).
Updates channel-level keywords, descriptions, and global search tags via YouTube Data API v3.
"""

from pathlib import Path
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service

GLOBAL_CHANNEL_KEYWORDS = (
    "\"Zirvenin Kanunu\" \"The Law of Peak\" \"Dark Psychology\" \"48 Laws of Power\" "
    "\"Thomas Shelby\" \"Walter White\" \"Heisenberg\" \"Fight Club\" \"Tyler Durden\" "
    "\"Stoicism\" \"Marcus Aurelius\" \"Seneca\" \"Interstellar\" \"Oppenheimer\" "
    "\"Robert Greene\" \"Alpha Mindset\" \"Sigma Rule\" \"Power Laws\" \"Karanlık Psikoloji\" "
    "\"Sessiz Güç\" \"Felsefe\" \"Motivasyon\" \"Dizi Replikleri\" \"Sinema\" shorts "
    "\"Unwritten Rules\" \"Mental Toughness\" \"Cillian Murphy\" \"Bryan Cranston\""
)

GLOBAL_CHANNEL_DESCRIPTION = (
    "👑 Zirvenin Kanunu | The Unwritten Laws of Power, Dark Psychology & Cinema Masters\n\n"
    "Burada sıradan tavsiyeler yok. Sadece acımasız gerçekler, hayatın yazılmamış kuralları "
    "ve zirvede kalmanın bedeli var. Sinema tarihinin en unutulmaz karakterleri, stoacı felsefe "
    "ve Robert Greene'in 48 Güç Yasası.\n\n"
    "⚡ Her gün yeni 60 FPS sinematik analizler, çift dilli altyazılar ve nöro-akustik ses tasarımı.\n\n"
    "🌍 Daily 60 FPS master cinema breakdowns in 4K clarity. Unwritten power laws, dark psychology, "
    "and stoic dominance for the top 1%.\n\n"
    "📌 Resmi Oynatma Listeleri & Master Koleksiyonlar:\n"
    "- ♟️ 48 Güç Yasası (Tüm Bölümler)\n"
    "- 🚬 Thomas Shelby & Peaky Blinders (Sessiz Güç)\n"
    "- 🧪 Walter White & Heisenberg (Karanlık Dönüşüm)\n"
    "- 🏛️ Stoacılık & Marcus Aurelius (Çelik İrade)\n\n"
    "#shorts #zirveninkanunu #güçyasaları #thomasshelby #walterwhite #motivasyon #felsefe #sigma"
)


def update_channel_branding():
    youtube = get_authenticated_service()
    print("=" * 70)
    print("🚀 KANAL SEO VE GLOBAL MARKA AYARLARI GÜNCELLENİYOR...")
    print("=" * 70)

    # 1. Get channel ID
    res = youtube.channels().list(part="id,brandingSettings", mine=True).execute()
    items = res.get("items", [])
    if not items:
        print("❌ Kanal bulunamadı!")
        return False

    channel_id = items[0]["id"]
    current_branding = items[0].get("brandingSettings", {})
    channel_settings = current_branding.get("channel", {})

    print(f"📺 Kanal ID: {channel_id}")
    print(f"🔑 Mevcut Anahtar Kelimeler: {channel_settings.get('keywords', 'Yok')[:50]}...")

    # 2. Update branding
    body = {
        "id": channel_id,
        "brandingSettings": {
            "channel": {
                "title": "Zirvenin Kanunu",
                "description": GLOBAL_CHANNEL_DESCRIPTION,
                "keywords": GLOBAL_CHANNEL_KEYWORDS,
                "defaultLanguage": "tr",
                "country": "TR"
            }
        }
    }

    try:
        up_res = youtube.channels().update(part="brandingSettings", body=body).execute()
        new_keywords = up_res.get("brandingSettings", {}).get("channel", {}).get("keywords", "")
        print("✅ KANAL AYARLARI BAŞARIYLA GÜNCELLENDİ!")
        print(f"🌐 Yeni Global Anahtar Kelimeler ({len(new_keywords)} karakter):\n{new_keywords}")
        return True
    except Exception as e:
        print(f"⚠️ Kanal güncelleme notu: {e}")
        return False


if __name__ == "__main__":
    update_channel_branding()
