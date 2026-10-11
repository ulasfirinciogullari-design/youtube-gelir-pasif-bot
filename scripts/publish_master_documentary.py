"""
Uploads the brand-new 1080p Master Longform Documentary to YouTube:
'GORUNMEZ_GUCUN_7_YASASI_1080P_FULL_DOCUMENTARY.mp4'
Sets title, chapters, description, tags, and category.
"""

import sys
import json
from pathlib import Path
from googleapiclient.http import MediaFileUpload

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service

VIDEO_FILE = BASE_DIR / "output" / "GORUNMEZ_GUCUN_7_YASASI_1080P_FULL_DOCUMENTARY.mp4"

TITLE = "👑 GÖRÜNMEZ GÜCÜN 7 YASASI | Zirvedeki Zihinlerin Karanlık Psikolojisi (1080p Full Belgesel)"
DESCRIPTION = """Dünyanın en tehlikeli insanı odadaki en çok bağıran değil; köşede oturup sessizce izleyendir.

Bu belgeselde Thomas Shelby, Walter White, Michael Corleone ve Marcus Aurelius'un zihin yapılarını, Robert Greene'in 48 Güç Yasası ile harmanlayarak inceliyoruz.

📌 ZAMAN DAMGALARI (BÖLÜMLER):
00:00 - Giriş: Zirvenin Yalnızlığı ve Görünmez Güç
00:35 - 1. Yasa: Efendini Asla Gölgede Bırakma (Robert Greene)
01:05 - 2. Yasa: Thomas Shelby Metodu: Duygularını Masadan Çek
01:38 - 3. Yasa: Walter White Paradoksu: Tehlikenin Kendisi Olmak
02:10 - 4. Yasa: Michael Corleone: Düşmanlarını Daha Yakın Tut
02:35 - 5. Yasa: Marcus Aurelius & Stoacı Çelik Zihin
02:58 - 6. Yasa: Niyetini Asla Belli Etme: Sessizliğin Kudreti
03:15 - 7. Yasa: Nihai Hüküm: Masadan Kim Galip Kalkar?

🔥 Zirvenin Kanunu kanalına abone olarak görünmez gücün kurallarını keşfedin.

#belgesel #thomasshelby #48güçyasası #walterwhite #karanlıkpsikoloji #godfather #marcusaurelius #felsefe"""

TAGS = [
    "belgesel", "48 güç yasası", "thomas shelby", "peaky blinders", "walter white",
    "breaking bad", "godfather", "marcus aurelius", "stoacılık", "karanlık psikoloji",
    "güç kuralları", "robert greene", "manipülasyon", "sinematik belgesel", "zirvenin kanunu"
]

def publish_documentary():
    if not VIDEO_FILE.exists():
        raise FileNotFoundError(f"Video bulunamadı: {VIDEO_FILE}")

    print("\n" + "=" * 70)
    print("🚀 YENİ 1080p MASTER UZUN METRAJ BELGESEL YÜKLENİYOR")
    print(f"🎬 Dosya: {VIDEO_FILE.name} ({VIDEO_FILE.stat().st_size / (1024*1024):.1f} MB)")
    print(f"📝 Başlık: {TITLE}")
    print("=" * 70)

    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "title": TITLE[:100],
            "description": DESCRIPTION[:5000],
            "tags": TAGS,
            "categoryId": "24",
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(VIDEO_FILE),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 10
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    print("📤 Video yükleniyor (YouTube API Resumable Upload)...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Yükleme İlerlemesi: %{int(status.progress() * 100)}")

    video_id = response.get("id")
    video_url = f"https://youtu.be/{video_id}"
    print("\n" + "=" * 70)
    print(f"🎉 MASTER BELGESEL YOUTUBE'DA YAYINLANDI!")
    print(f"🆔 Video ID: {video_id}")
    print(f"🔗 Video URL: {video_url}")
    print("=" * 70)

    # Save to output
    res_path = BASE_DIR / "output" / "master_documentary_published.json"
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump({
            "video_id": video_id,
            "video_url": video_url,
            "title": TITLE,
            "published_at": response.get("snippet", {}).get("publishedAt")
        }, f, indent=2)

    return video_id

if __name__ == "__main__":
    publish_documentary()
