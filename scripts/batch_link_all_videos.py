import asyncio
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.studio_browser_bridge import link_related_video_async

TARGET_MASTER = "iFdWgrnk3ik"

async def batch_link_all():
    videos_file = BASE_DIR / "output" / "all_channel_videos.json"
    linked_file = BASE_DIR / "output" / "funnel_linked_videos.json"
    
    if not videos_file.exists():
        print("❌ all_channel_videos.json bulunamadı.")
        return

    with open(videos_file, "r", encoding="utf-8") as f:
        all_videos = json.load(f)

    linked = []
    if linked_file.exists():
        with open(linked_file, "r", encoding="utf-8") as f:
            linked = json.load(f)

    # Filter videos needing linking
    to_link = [v for v in all_videos if v["id"] not in linked and v["id"] != TARGET_MASTER]
    print("\n" + "=" * 70)
    print("🔗 TOPLU İLGİLİ VİDEO FUNNEL BAĞLAMA BAŞLATILDI")
    print(f"🎯 Hedef Ana Belgesel: {TARGET_MASTER}")
    print(f"📊 Toplam Video: {len(all_videos)} | Zaten Bağlı: {len(linked)} | Bağlanacak: {len(to_link)}")
    print("=" * 70)

    success_count = 0
    for idx, v in enumerate(to_link, 1):
        vid = v["id"]
        vtitle = v.get("title", vid)
        print(f"\n[{idx}/{len(to_link)}] İşleniyor: [{vid}] {vtitle[:40]}...")
        
        try:
            ok = await link_related_video_async(vid, TARGET_MASTER)
            if ok:
                linked.append(vid)
                with open(linked_file, "w", encoding="utf-8") as f:
                    json.dump(linked, f, indent=2)
                success_count += 1
                print(f"✅ Başarıyla bağlandı ve kaydedildi! ({success_count} tamamlandı)")
            else:
                print(f"⚠️ Bağlanamadı: {vid}")
        except Exception as e:
            print(f"❌ Hata ({vid}): {e}")
            
        await asyncio.sleep(2)

    print("\n" + "=" * 70)
    print(f"🎉 TOPLU BAĞLAMA TAMAMLANDI! {success_count} Yeni Video Funnel'a Eklendi.")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(batch_link_all())
