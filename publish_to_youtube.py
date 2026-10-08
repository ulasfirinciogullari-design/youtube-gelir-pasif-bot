"""
Direct YouTube Shorts Publisher.
Uploads 60 FPS master Shorts, burns SEO metadata and custom thumbnails,
and returns the live YouTube video link.
"""

import argparse
import json
from pathlib import Path
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"
OUTPUT_DIR = BASE_DIR / "output"


import os

def get_authenticated_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/youtube.force-ssl",
            ],
        )
        return build("youtube", "v3", credentials=creds)

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(f"token.json bulunamadı: {TOKEN_FILE}")

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    return build("youtube", "v3", credentials=creds)


def upload_short(video_path: Path, privacy_status: str = "public") -> str:
    if not video_path.exists():
        raise FileNotFoundError(f"Video bulunamadı: {video_path}")

    # Look for matching metadata and thumbnail
    meta_path = video_path.with_name(f"{video_path.stem}_meta.json")
    thumb_path = video_path.with_name(f"{video_path.stem}_thumb.jpg")

    title = "👑 Zirvenin Kanunu #shorts"
    description = (
        "Görünmeyen Güç, Karanlık Psikoloji ve Sinema Tarihinin Zirve Anları.\n\n"
        "#shorts #keşfet #sinema #motivasyon"
    )
    tags = ["shorts", "keşfet", "motivasyon", "sinema", "güç yasaları", "viral"]

    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            title = meta.get("title_options", [title])[0]
            description = meta.get("description", description)
            tags = meta.get("tags", tags)

    print("\n" + "=" * 70)
    print(f"🚀 YOUTUBE SHORTS YAYINLANIYOR: {video_path.name}")
    print(f"📝 Başlık: {title}")
    print(f"🔒 Görünürlük: {privacy_status.upper()}")
    print("=" * 70)

    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "24", # Entertainment
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=1024 * 1024 * 4,
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    print("📤 Video yükleniyor...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   İlerleme: %{int(status.progress() * 100)}")

    video_id = response.get("id")
    video_url = f"https://youtube.com/shorts/{video_id}"

    print(f"\n🎉 VİDEO BAŞARIYLA YÜKLENDİ!")
    print(f"🔗 Video URL: {video_url}")

    # Upload custom thumbnail if exists
    if thumb_path.exists():
        try:
            print("🖼️ Özel kapak görseli (Thumbnail) yükleniyor...")
            thumb_media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            print("✅ Kapak görseli yüklendi!")
        except Exception as e:
            print(f"ℹ️ Kapak görseli notu: {e} (Kanal telefon doğrulaması gerektirebilir)")

    print("=" * 70 + "\n")
    return video_url


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube Shorts Publisher")
    parser.add_argument("--file", type=str, default=None, help="Yüklenecek video dosya yolu")
    parser.add_argument("--privacy", type=str, default="public", choices=["public", "unlisted", "private"], help="Video gizlilik durumu")
    args = parser.parse_args()

    if args.file:
        target = Path(args.file)
    else:
        # Pick latest VIRAL_MASTER video
        videos = sorted(OUTPUT_DIR.glob("VIRAL_MASTER_*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not videos:
            raise FileNotFoundError("Yüklenecek hiçbir video bulunamadı!")
        target = videos[0]

    upload_short(target, privacy_status=args.privacy)
