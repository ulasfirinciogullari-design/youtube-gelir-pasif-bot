"""
STUDIO COMMAND CENTER CLI.
Control panel for managing YouTube Studio browser automation:
- Interactive login and session verification
- Linking "İlgili Video" across individual or all Shorts
- Uploading multi-language audio tracks (seslendirme)
- Zero-quota comment pinning
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.services.studio_browser_bridge import (
    check_studio_auth_status,
    interactive_login_flow,
    link_related_video_async,
    upload_audio_track_async,
    pin_comment_async,
    MASTER_DOCUMENTARY_ID,
)


def get_channel_shorts(max_results: int = 50) -> list:
    """Fetches uploaded Shorts from YouTube Data API."""
    token_file = BASE_DIR / "token.json"
    if not token_file.exists():
        return []

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        with open(token_file, "r", encoding="utf-8") as f:
            token_data = json.load(f)

        creds = Credentials.from_authorized_user_info(token_data)
        yt = build("youtube", "v3", credentials=creds)

        # Get uploads playlist ID
        ch_resp = yt.channels().list(mine=True, part="contentDetails").execute()
        uploads_id = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Fetch playlist items
        items = []
        next_page = None
        while len(items) < max_results:
            pl_resp = yt.playlistItems().list(
                playlistId=uploads_id,
                part="snippet",
                maxResults=min(50, max_results - len(items)),
                pageToken=next_page,
            ).execute()
            for it in pl_resp.get("items", []):
                vid_id = it["snippet"]["resourceId"]["videoId"]
                title = it["snippet"]["title"]
                items.append({"video_id": vid_id, "title": title})
            next_page = pl_resp.get("nextPageToken")
            if not next_page:
                break
        return items
    except Exception as e:
        print(f"⚠️ YouTube API liste alma notu: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="YouTube Studio Browser Automation Command Center")
    parser.add_argument("--login", action="store_true", help="Açık tarayıcı penceresi ile YouTube Studio oturumu aç")
    parser.add_argument("--status", action="store_true", help="Oturum durumunu kontrol et")
    parser.add_argument("--link", type=str, default=None, help="Belirtilen Short ID için İlgili Video bağla")
    parser.add_argument("--target", type=str, default=MASTER_DOCUMENTARY_ID, help="Hedef Belgesel / Video ID")
    parser.add_argument("--link-all", action="store_true", help="Kanalın tüm Shorts videolarına İlgili Video bağla")
    parser.add_argument("--upload-audio", nargs=3, metavar=("VIDEO_ID", "LANG", "AUDIO_FILE"), help="Çok dilli ses parçası yükle")
    parser.add_argument("--upload-all-tracks", type=str, metavar="VIDEO_ID", help="output/multitrack_test içindeki tüm ses parçalarını yükle")
    parser.add_argument("--pin", nargs=2, metavar=("VIDEO_ID", "COMMENT_TEXT"), help="Yorum gönder ve başa sabitle")
    parser.add_argument("--visible", action="store_true", help="Tarayıcıyı görünür modda çalıştır (hata ayıklama için)")
    args = parser.parse_args()

    headless = not args.visible

    if args.login:
        print("🚀 İnteraktif YouTube Studio Girişi Başlatılıyor...")
        success = asyncio.run(interactive_login_flow(timeout_seconds=300))
        if success:
            print("✅ Giriş tamamlandı ve doğrulandı!")
        else:
            print("❌ Giriş tamamlanamadı.")
        return

    if args.status:
        print("🔍 YouTube Studio Oturum Durumu Kontrol Ediliyor...")
        is_logged_in = asyncio.run(check_studio_auth_status(headless=True))
        if is_logged_in:
            print("🟢 OTURUM AKTİF: YouTube Studio hazır!")
        else:
            print("🔴 OTURUM KAPALI: `python scripts/studio_command_center.py --login` çalıştırmanız gerekiyor.")
        return

    if args.link:
        short_id = args.link
        target = args.target
        print(f"🔗 Tekil Video Bağlama: {short_id} ➔ {target}")
        ok = asyncio.run(link_related_video_async(short_id, target, headless=headless))
        print("Sonuç:", "✅ Başarılı" if ok else "❌ Başarısız")
        return

    if args.link_all:
        print("\n" + "=" * 70)
        print("🌐 TÜM KANAL SHORTS VİDEOLARINA İLGİLİ VİDEO BAĞLAMA BAŞLATILIYOR")
        print(f"🎯 Hedef Belgesel: {args.target}")
        print("=" * 70)

        shorts = get_channel_shorts(max_results=50)
        print(f"📋 Toplam {len(shorts)} Video Bulundu.\n")

        success_count = 0
        for idx, item in enumerate(shorts, 1):
            vid_id = item["video_id"]
            title = item["title"]
            if vid_id == args.target:
                continue  # Kendisine bağlama

            print(f"[{idx}/{len(shorts)}] {title[:50]} ({vid_id})...")
            try:
                ok = asyncio.run(link_related_video_async(vid_id, args.target, headless=headless))
                if ok:
                    success_count += 1
            except Exception as e:
                print(f"⚠️ Hata: {e}")

        print("\n" + "=" * 70)
        print(f"👑 TAMAMLANDI: {success_count}/{len(shorts)} Video Başarıyla Bağlandı!")
        print("=" * 70)
        return

    if args.upload_audio:
        vid_id, lang, audio_path_str = args.upload_audio
        audio_path = Path(audio_path_str)
        print(f"🎧 Ses Yükleme: Video={vid_id}, Dil={lang}, Dosya={audio_path}")
        ok = asyncio.run(upload_audio_track_async(vid_id, lang, audio_path, headless=headless))
        print("Sonuç:", "✅ Başarılı" if ok else "❌ Başarısız")
        return

    if args.upload_all_tracks:
        vid_id = args.upload_all_tracks
        tracks_dir = BASE_DIR / "output" / "multitrack_test"
        if not tracks_dir.exists():
            print(f"❌ Dizin bulunamadı: {tracks_dir}")
            return

        print(f"\n🌍 TÜM DİLLERDE SES PARÇALARI YÜKLENİYOR ({vid_id})...")
        for audio_file in tracks_dir.glob("audio_track_*.mp3"):
            parts = audio_file.stem.split("_")
            if len(parts) >= 3:
                lang_code = parts[2]
                if lang_code == "tr":
                    continue  # Varsayılan dil zaten Türkçe
                print(f"\n--- Yükleniyor: {lang_code.upper()} ({audio_file.name}) ---")
                try:
                    asyncio.run(upload_audio_track_async(vid_id, lang_code, audio_file, headless=headless))
                except Exception as e:
                    print(f"⚠️ Hata ({lang_code}): {e}")
        print("\n👑 Tüm ses parçaları işlendi!")
        return

    if args.pin:
        vid_id, text = args.pin
        ok = asyncio.run(pin_comment_async(vid_id, text, headless=headless))
        print("Sonuç:", "✅ Başarılı" if ok else "❌ Başarısız")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
