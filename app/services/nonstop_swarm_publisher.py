"""
Non-Stop Swarm Publisher & Continuous 24/7 Production Engine.
Executes non-stop high-throughput parallel rendering and publishing:
1. Concurrently renders 4 master 60 FPS Shorts simultaneously across 16 CPU cores.
2. Dispatches completed videos to YouTube as soon as each render completes.
3. Chains playlists, pinned comments, and multi-voice acting in every single video.
4. Operates continuously, filling the channel non-stop.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import time
from typing import List

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.parallel_render_swarm import launch_swarm_production
from app.services.mega_content_vault import MEGA_CATALOG
from publish_to_youtube import upload_short


def run_nonstop_swarm_publisher(batch_size: int = 4, max_batches: int = 25, delay_between_batches: int = 15):
    print("\n" + "=" * 70)
    print("⚡ NON-STOP SÜPERBİLGİSAYAR YAYIN MOTORU BAŞLATILDI")
    print(f"📊 Her Döngüde: {batch_size} Eşzamanlı Video | Toplam Hedef: {batch_size * max_batches} Video")
    print(f"🏛️ Katalog Genişliği: {len(MEGA_CATALOG)} Bölüm | 16 Çekirdek Paralel İşleme")
    print("=" * 70 + "\n")

    all_ids = [c["id"] for c in MEGA_CATALOG]
    random.shuffle(all_ids)
    
    total_published = 0
    pointer = 0

    for batch_idx in range(1, max_batches + 1):
        if pointer >= len(all_ids):
            random.shuffle(all_ids)
            pointer = 0

        current_batch_ids = all_ids[pointer:pointer + batch_size]
        pointer += batch_size

        print(f"\n🚀 [DÖNGÜ {batch_idx}/{max_batches}] {len(current_batch_ids)} Video Eşzamanlı Render Ediliyor...")
        results = launch_swarm_production(current_batch_ids, max_workers=batch_size, lang="tr")

        # Upload finished videos immediately
        for res in results:
            if res.get("status") == "success":
                v_path = Path(res["video_path"])
                print(f"\n📤 YOUTUBE'A YÜKLENİYOR: {v_path.name}")
                try:
                    url = upload_short(v_path, privacy_status="public")
                    total_published += 1
                    print(f"🎉 CANLI YAYINLANDI [{total_published}]: {url}")
                except Exception as up_err:
                    print(f"⚠️ Yükleme notu ({v_path.name}): {up_err}")

        print(f"⏳ Sonraki paralel parti için {delay_between_batches}s bekleniyor...")
        time.sleep(delay_between_batches)


if __name__ == "__main__":
    run_nonstop_swarm_publisher(batch_size=2, max_batches=5, delay_between_batches=5)
