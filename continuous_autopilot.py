"""
Continuous Autonomous Cloud Autopilot Engine.
Produces 60 FPS master Shorts across 12 high-RPM niches and multi-languages,
and uploads them to YouTube non-stop until reaching YouTube's daily API upload limit.
"""

import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from googleapiclient.errors import HttpError
from publish_to_youtube import upload_short, OUTPUT_DIR

BASE_DIR = Path(__file__).resolve().parent

ALL_CAMPAIGNS = [
    "fight_club_truth",
    "scarface_ascent",
    "joker_anarchy",
    "batman_shadows",
    "matrix_illusion",
    "wolf_greed",
    "shelby_power",
    "godfather_rules",
    "oppenheimer_doom",
    "interstellar_abyss",
    "breaking_bad_danger",
    "gladiator_stoic",
]


def run_continuous_autopilot(max_videos: int = 10, delay_seconds: int = 120):
    print("=" * 70)
    print("🚀 KESİNTİSİZ BULUT OTOPİLOTU BAŞLATILDI")
    print(f"Hedef: Günlük limite ulaşana kadar durmaksızın üretim & yayınlama")
    print(f"Maksimum Deneme: {max_videos} video | Bekleme: {delay_seconds}s")
    print("=" * 70)

    # Shuffle campaigns to ensure niche variety
    campaign_queue = list(ALL_CAMPAIGNS)
    random.shuffle(campaign_queue)

    uploaded_count = 0

    for i, campaign_id in enumerate(campaign_queue, 1):
        if uploaded_count >= max_videos:
            print(f"\n🎯 Hedeflenen video sayısına ({max_videos}) ulaşıldı.")
            break

        print(f"\n🎬 [{i}/{len(campaign_queue)}] Sıradaki Kampanya Üretiliyor: {campaign_id}")

        # 1. Produce 60 FPS Master Video via ultimate_factory.py
        factory_cmd = [sys.executable, str(BASE_DIR / "ultimate_factory.py"), "--id", campaign_id]
        render_res = subprocess.run(factory_cmd, capture_output=True, text=True)
        if render_res.returncode != 0:
            print(f"⚠️ Render hatası ({campaign_id}): {render_res.stderr[-300:]}")
            continue

        # Find newly produced file
        produced_files = sorted(OUTPUT_DIR.glob(f"VIRAL_MASTER_{campaign_id}_*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not produced_files:
            print(f"⚠️ Çıktı dosyası bulunamadı: {campaign_id}")
            continue

        target_file = produced_files[0]
        print(f"✅ Render Tamamlandı: {target_file.name}")

        # 2. Upload to YouTube
        try:
            video_url = upload_short(target_file, privacy_status="public")
            uploaded_count += 1
            print(f"🏆 Toplam Başarılı Yayın Sayısı: {uploaded_count}")
        except HttpError as e:
            err_str = str(e)
            if "quotaExceeded" in err_str or "uploadLimitExceeded" in err_str or "dailyLimitExceeded" in err_str:
                print("\n" + "=" * 70)
                print("🛑 GÜNLÜK YOUTUBE YÜKLEME LİMİTİNE ULAŞILDI!")
                print("YouTube günlük API kota sınırını (10.000 puan / günlük video limiti) doldurduk.")
                print("Sistem bir sonraki zamanlanmış döngüde (kota sıfırlandığında) otomatik olarak devam edecektir.")
                print("=" * 70)
                break
            else:
                print(f"⚠️ YouTube API Hatası: {e}")
                break
        except Exception as e:
            print(f"⚠️ Beklenmeyen Hata: {e}")
            break

        if uploaded_count < max_videos:
            print(f"⏳ Bir sonraki video için {delay_seconds} saniye bekleniyor (YouTube rate-limit koruması)...")
            time.sleep(delay_seconds)

    print("\n" + "=" * 70)
    print(f"✨ Otopilot Oturumu Tamamlandı. Bu oturumda {uploaded_count} video yayınlandı.")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous YouTube Autopilot")
    parser.add_argument("--max", type=int, default=8, help="Maksimum denenecek video sayısı")
    parser.add_argument("--delay", type=int, default=90, help="Videolar arası bekleme süresi (saniye)")
    args = parser.parse_args()

    run_continuous_autopilot(max_videos=args.max, delay_seconds=args.delay)
