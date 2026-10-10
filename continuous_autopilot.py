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
from ultimate_factory import MASTER_CAMPAIGNS

BASE_DIR = Path(__file__).resolve().parent

try:
    from app.services.hypersonic_engine import HYPERSONIC_MASTER_CAMPAIGNS
    HYPERSONIC_IDS = [c["id"] for c in HYPERSONIC_MASTER_CAMPAIGNS]
except Exception:
    HYPERSONIC_IDS = []

try:
    from app.services.mega_content_vault import MEGA_CATALOG
    ALL_CAMPAIGNS = [c["id"] for c in MEGA_CATALOG]
except Exception:
    ALL_CAMPAIGNS = [c["id"] for c in MASTER_CAMPAIGNS]


def run_continuous_autopilot(max_videos: int = 10, delay_seconds: int = 120, lang: str = "auto"):
    print("=" * 70)
    print("🚀 KESİNTİSİZ BULUT OTOPİLOTU BAŞLATILDI (HYPERSONIC HYPE-BREAKER ACTIVE)")
    print(f"Hedef: Günlük limite ulaşana kadar durmaksızın üretim & yayınlama")
    print(f"Dil Modu: {lang.upper()} (Global & Yerel Hibrit Dağıtım)")
    print(f"Maksimum Deneme: {max_videos} video | Bekleme: {delay_seconds}s")
    print("=" * 70)
    from app.services.community_autopilot import engage_with_recent_comments

    # Prioritize Hypersonic Hype-Breaker campaigns first (15-18s infinite seamless loop)
    hypersonic_shuffled = list(HYPERSONIC_IDS)
    random.shuffle(hypersonic_shuffled)

    # Prioritize high-velocity niches based on real-time channel analytics
    high_priority = [c for c in ALL_CAMPAIGNS if any(k in c.lower() for k in ["shelby", "power_law", "interstellar", "stoic", "matrix", "walter"]) and c not in HYPERSONIC_IDS]
    others = [c for c in ALL_CAMPAIGNS if c not in high_priority and c not in HYPERSONIC_IDS]
    random.shuffle(high_priority)
    random.shuffle(others)
    campaign_queue = hypersonic_shuffled + high_priority + others

    uploaded_count = 0

    for i, campaign_id in enumerate(campaign_queue, 1):
        if uploaded_count >= max_videos:
            print(f"\n🎯 Hedeflenen video sayısına ({max_videos}) ulaşıldı.")
            break

        # Determine language for this video
        if lang == "auto":
            # Alternate: even -> TR, odd -> EN
            current_lang = "tr" if (uploaded_count % 2 == 0) else "en"
        else:
            current_lang = lang

        print(f"\n🎬 [{i}/{len(campaign_queue)}] Sıradaki Kampanya ({current_lang.upper()}): {campaign_id}")

        # 1. Produce 60 FPS Master Video via ultimate_factory.py
        factory_cmd = [
            sys.executable,
            str(BASE_DIR / "ultimate_factory.py"),
            "--id", campaign_id,
            "--lang", current_lang
        ]
        render_res = subprocess.run(factory_cmd, capture_output=True, text=True)
        if render_res.returncode != 0:
            print(f"⚠️ Render hatası ({campaign_id}): {render_res.stderr[-300:]}")
            continue

        # Find newly produced file
        safe_slug = "".join(c if c.isalnum() else "_" for c in campaign_id.lower()).strip("_")
        pattern = f"VIRAL_MASTER_*_{safe_slug}_*.mp4"
        produced_files = sorted(OUTPUT_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        if not produced_files:
            produced_files = sorted(OUTPUT_DIR.glob("VIRAL_MASTER_*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)

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
            try:
                engage_with_recent_comments(max_videos=5)
            except Exception as comm_err:
                print(f"ℹ️ Topluluk etkileşim notu: {comm_err}")
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
    parser.add_argument("--lang", type=str, default="auto", choices=["auto", "tr", "en"], help="Dil modu: auto, tr veya en")
    args = parser.parse_args()

    run_continuous_autopilot(max_videos=args.max, delay_seconds=args.delay, lang=args.lang)
