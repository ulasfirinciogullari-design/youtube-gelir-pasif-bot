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
    default_lang = "tr"
    default_audio = "tr"
    pinned_comment = None

    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            title = meta.get("title_options", [title])[0]
            description = meta.get("description", description)
            tags = meta.get("tags", tags)
            default_lang = meta.get("defaultLanguage", "tr")
            default_audio = meta.get("defaultAudioLanguage", "tr")
            pinned_comment = meta.get("pinned_comment")
            if pinned_comment and pinned_comment not in description:
                description = f"💬 TARTIŞMA: {pinned_comment}\n\n" + description

    # Always ensure the master documentary funnel link is at the very top
    funnel_header = (
        "🎬 İLGİLİ VİDEO (1080p Full Belgesel): https://youtu.be/PY47MUbB71c\n"
        "👑 Hollywood Trilogy | Thomas Shelby - Walter White - Tyler Durden\n\n"
    )
    if "PY47MUbB71c" not in description:
        description = funnel_header + description

    print("\n" + "=" * 70)
    print(f"🚀 YOUTUBE SHORTS YAYINLANIYOR: {video_path.name}")
    print(f"📝 Başlık: {title}")
    print(f"🌐 Dil: {default_lang.upper()} (Ses: {default_audio.upper()})")
    print(f"🔒 Görünürlük: {privacy_status.upper()}")
    print("=" * 70)

    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:30],
            "categoryId": "24", # Entertainment
            "defaultLanguage": default_lang,
            "defaultAudioLanguage": default_audio,
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

    # Add to Official Channel Playlist
    try:
        playlist_id = "PLNK3n7jEGHrw"
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id}
                }
            }
        ).execute()
        print(f"➕ Resmi Oynatma Listesine Eklendi ({playlist_id})")
    except Exception as e:
        print(f"ℹ️ Oynatma listesi notu: {e}")

    # Add to Niche Binge-Watch Playlists
    try:
        from app.services.playlist_master_factory import OFFICIAL_PLAYLIST_DEFINITIONS, add_video_to_playlist, get_existing_playlists
        ex_playlists = get_existing_playlists(youtube)
        for pldef in OFFICIAL_PLAYLIST_DEFINITIONS:
            if pldef["key"] != "master" and any(k in title.lower() for k in pldef["keywords"]):
                for pl_title, pl_id in ex_playlists.items():
                    if pldef["title"].lower()[:20] in pl_title:
                        if add_video_to_playlist(youtube, video_id, pl_id):
                            print(f"📁 Niş Binge-Watch Listesine Eklendi: {pldef['title'][:35]}")
                        break
    except Exception as pl_err:
        pass

    # Ensure Pinned Comment is also injected at the very top of Description as a bulletproof fallback
    if pinned_comment and pinned_comment not in description:
        description = f"💬 TARTIŞMA: {pinned_comment}\n\n" + description

    # Add Pinned Discussion Engagement Comment
    if pinned_comment:
        try:
            youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {
                                "textOriginal": pinned_comment
                            }
                        }
                    }
                }
            ).execute()
            print(f"💬 Etkileşim Tartışma Yorumu Eklendi: {pinned_comment[:50]}...")
        except Exception as e:
            print(f"ℹ️ Yorum ekleme notu: {e}")
            # Persist to pending comments queue for automatic retry
            try:
                pending_file = OUTPUT_DIR / "pending_comments.json"
                pending_data = []
                if pending_file.exists():
                    try:
                        with open(pending_file, "r", encoding="utf-8") as pf:
                            pending_data = json.load(pf)
                    except Exception:
                        pass
                pending_data.append({
                    "video_id": video_id,
                    "video_url": video_url,
                    "comment": pinned_comment,
                    "title": title,
                    "timestamp": int(time.time())
                })
                with open(pending_file, "w", encoding="utf-8") as pf:
                    json.dump(pending_data, pf, ensure_ascii=False, indent=2)
                print(f"💾 Yorum Bekleyenler Kasasına Kaydedildi ({video_id})")
            except Exception as save_err:
                pass

    # Add 10-Language Global Localizations (EN, ES, DE, FR, PT, IT, AR, JA, HI, RU)
    try:
        base_clean = title.replace("👑 ", "").replace(" #shorts", "").strip()
        locs = {
            "en": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nUnwritten power laws, dark psychology & cinema masters.\n⚡ Subscribe for daily 60 FPS analysis: @zirveninkanunu\n\n#shorts #powerlaws #darkpsychology #stoic #sigma"[:5000]
            },
            "es": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeyes del poder, psicología oscura y maestros del cine.\n⚡ Suscríbete para análisis en 60 FPS: @zirveninkanunu\n\n#shorts #leyesdelpoder #psicologiaoscura #motivacion"[:5000]
            },
            "de": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nGesetze der Macht, dunkle Psychologie und Filmgeschichte.\n⚡ Täglich neue 60 FPS Analysen: @zirveninkanunu\n\n#shorts #psychologie #macht #motivation"[:5000]
            },
            "fr": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLois du pouvoir, psychologie sombre et légendes du cinéma.\n⚡ Analyses quotidiennes en 60 FPS: @zirveninkanunu\n\n#shorts #pouvoir #psychologie #motivation"[:5000]
            },
            "pt": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeis do poder, psicologia sombria e mestres do cinema.\n⚡ Inscreva-se para análises diárias em 60 FPS: @zirveninkanunu\n\n#shorts #leisdopoder #psicologia #sucesso"[:5000]
            },
            "it": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nLeggi del potere, psicologia oscura e maestri del cinema.\n⚡ Iscriviti per analisi in 60 FPS: @zirveninkanunu\n\n#shorts #potere #psicologia #cinema"[:5000]
            },
            "ar": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nقوانين القوة وعلم النفس المظلم وروائع السينما.\n⚡ اشترك للحصول على تحليلات يومية بدقة 60 إطارًا: @zirveninkanunu\n\n#shorts #علم_النفس #القوة #تحفيز"[:5000]
            },
            "ja": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\n力の法則、ダーク心理学、そして映画の巨匠たち。\n⚡ 毎日60 FPSで分析をお届け: @zirveninkanunu\n\n#shorts #心理学 #マインドセット #映画"[:5000]
            },
            "hi": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nशक्ति के नियम, डार्क साइकोलॉजी और सिनेमा के महानतम क्षण।\n⚡ दैनिक 60 FPS विश्लेषण के लिए सब्सक्राइब करें: @zirveninkanunu\n\n#shorts #प्रेरणा #सफलता #माइंडसेट"[:5000]
            },
            "ru": {
                "title": f"👑 {base_clean} #shorts"[:100],
                "description": f"{base_clean}\n\nЗаконы власти, темная психология и шедевры кино.\n⚡ Подпишитесь на ежедневный разбор в 60 FPS: @zirveninkanunu\n\n#shorts #психология #власть #мотивация"[:5000]
            }
        }
        youtube.videos().update(
            part="localizations",
            body={"id": video_id, "localizations": locs}
        ).execute()
        print("🌍 10 Küresel Dilde (EN, ES, DE, FR, PT, IT, AR, JA, HI, RU) Yerelleştirme Mühürlendi!")
    except Exception as e:
        print(f"ℹ️ Dil yerelleştirme notu: {e}")

    # Auto-engage with community comments
    try:
        from app.services.community_autopilot import engage_with_recent_comments
        engage_with_recent_comments(max_videos=10)
    except Exception as c_err:
        pass

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
