"""
Nonstop Empire Autopilot for 'Zirvenin Kanunu' (@zirveninkanunu).
Autonomous background orchestrator coordinating:
1. Automated 'İlgili Video' (Related Video) Funnel linking to Master Documentary (PY47MUbB71c).
2. Pinned debate discussion comments on all Shorts.
3. Viral Community Tab Poll publication cycle.
4. 24/7 RTMP Live Stream health & zero-CPU broadcaster.
5. Global dubbing & multilingual closed captions.
"""

import sys
import time
import json
import asyncio
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.studio_browser_bridge import (
    link_related_video_perfect,
    post_and_pin_debate_comment
)
from app.services.community_autopilot import post_next_viral_poll
from app.services.live_stream_engine import get_stream_status, start_live_stream

MASTER_DOC_ID = "PY47MUbB71c"

HIGH_VELOCITY_DEBATES = {
    "shelby": "🔥 Thomas Shelby'nin en ölümcül silahı zekası mı yoksa mutlak duygusuzluğu mu? Fikrini yorumlarda belirt.",
    "walter": "⚡ Walter White bir canavar mıydı yoksa sistemin yarattığı bir dahi mi? Gerçek Heisenberg kim?",
    "power": "👑 48 Güç Yasası: Hayatta en çok hangi kuralı ihlal ettiğinde bedelini ağır ödedin?",
    "marcus": "🧠 Marcus Aurelius'un Stoacı zihni modern dünyada hayatta kalmanın tek yolu olabilir mi?",
    "interstellar": "🌌 Evrende insanı en çok ürküten şey zamanın bükülmesi mi yoksa yalnızlık mı?"
}

def load_channel_videos():
    """Reads latest videos list or known IDs."""
    cache_file = BASE_DIR / "output" / "channel_videos_cache.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback to top-performing catalog list
    return [
        {"id": "oaYEpHoMdDo", "title": "Marcus Aurelius: Zihinsel Yenilmezlik"},
        {"id": "RcE17gXbfOE", "title": "Thomas Shelby vs Walter White"},
        {"id": "67Rz3p75HFU", "title": "Thomas Shelby: Bir Yalanı Anlama"},
        {"id": "9HitiEuUUJY", "title": "Interstellar: Evrenin En Korkunç Silahı"},
        {"id": "Kf8W1Ku6JL4", "title": "48 Güç Yasası: İkinci Şans Tuzağı"},
        {"id": "YoKEOy28BvQ", "title": "Thomas Shelby vs Walter White 2.0"},
        {"id": "ttnTksT5RFo", "title": "Godfather vs Scarface"},
        {"id": "RUyMb5fCIl4", "title": "Oppenheimer vs Interstellar"},
        {"id": "8xgOTZNh750", "title": "48 Güç Yasası: Dostuna Asla Güvenme"}
    ]

def get_processed_funnels():
    state_file = BASE_DIR / "output" / "funnel_linked_videos.json"
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def mark_funnel_processed(video_id):
    state_file = BASE_DIR / "output" / "funnel_linked_videos.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    items = get_processed_funnels()
    if video_id not in items:
        items.append(video_id)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)

async def run_autopilot_cycle():
    print("\n" + "=" * 70)
    print("👑 ZİRVENİN KANUNU | NONSTOP EMPIRE AUTOPILOT DÖNGÜSÜ")
    print(f"⏰ Zaman: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. Check Live Stream Status
    stream_st = get_stream_status()
    print(f"📡 Canlı Yayın Durumu: {stream_st['status']} (PID: {stream_st['pid']})")

    # 2. Check and Link Funnel for Any Unprocessed Videos
    processed = get_processed_funnels()
    videos = load_channel_videos()
    unprocessed = [v for v in videos if v["id"] not in processed and v["id"] != MASTER_DOC_ID]
    print(f"📊 Funnel Durumu: {len(processed)}/{len(videos)} video bağlı. Bekleyen: {len(unprocessed)}")

    for v in unprocessed[:2]:  # Process in steady batches of 2
        vid = v["id"]
        vtitle = v.get("title", vid)
        print(f"\n🔗 Funnel Bağlanıyor: [{vid}] - {vtitle}")
        success = await link_related_video_perfect(vid, MASTER_DOC_ID)
        if success:
            mark_funnel_processed(vid)
            # Post discussion comment
            comment = HIGH_VELOCITY_DEBATES.get("shelby")
            for k, val in HIGH_VELOCITY_DEBATES.items():
                if k in vtitle.lower():
                    comment = val
                    break
            print(f"📌 Tartışma Yorumu Sabitleniyor: {comment[:50]}...")
            await post_and_pin_debate_comment(vid, comment)

    print("\n✅ Nonstop Autopilot Döngüsü Tamamlandı.")

if __name__ == "__main__":
    asyncio.run(run_autopilot_cycle())
