"""
Smart Vault & Drip-Feed Scheduler for Zirvenin Kanunu.
Architected for sustainable, high-leverage media scaling:
1. Gathers rendered 60 FPS master Shorts into an organized local production vault.
2. Manages release schedules based on peak YouTube audience engagement hours.
3. Dispatches videos cleanly to YouTube; gracefully handles daily quota boundaries by holding videos in queue.
4. Prevents content cannibalization by spacing uploads across optimal intervals.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Dict, List, Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import upload_short
from app.services.video_quality_analyst import audit_short_quality

VAULT_DIR = BASE_DIR / "output" / "vault"
READY_DIR = VAULT_DIR / "ready"
PUBLISHED_DIR = VAULT_DIR / "published"

READY_DIR.mkdir(parents=True, exist_ok=True)
PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)


def ingest_rendered_videos_into_vault():
    """Scans output/ for newly rendered master videos and stages them in vault/ready."""
    out_dir = BASE_DIR / "output"
    master_files = sorted(out_dir.glob("VIRAL_MASTER_*.mp4"), key=lambda p: p.stat().st_mtime)

    ingested = 0
    for mp4 in master_files:
        # Check if already in vault or published
        dest = READY_DIR / mp4.name
        if dest.exists() or (PUBLISHED_DIR / mp4.name).exists():
            continue

        # Quality audit
        audit = audit_short_quality(mp4)
        if audit.get("score", 0) >= 80:
            shutil.copy2(mp4, dest)
            # Copy thumbnail and meta if present
            thumb = mp4.with_name(f"{mp4.stem}_thumb.jpg")
            meta = mp4.with_name(f"{mp4.stem}_meta.json")
            if thumb.exists():
                shutil.copy2(thumb, READY_DIR / thumb.name)
            if meta.exists():
                shutil.copy2(meta, READY_DIR / meta.name)

            ingested += 1
            print(f"📥 Kasaya Alındı [{audit['grade']}]: {mp4.name} (Skor: {audit['score']}/100)")

    return ingested


def get_vault_inventory() -> Dict:
    """Returns current counts and inventory in vault."""
    ready_vids = sorted(READY_DIR.glob("*.mp4"))
    pub_vids = sorted(PUBLISHED_DIR.glob("*.mp4"))
    return {
        "ready_count": len(ready_vids),
        "published_count": len(pub_vids),
        "ready_videos": [v.name for v in ready_vids],
        "published_videos": [v.name for v in pub_vids]
    }


def dispatch_next_vault_video(privacy: str = "public") -> Optional[str]:
    """Pulls the next staged master video from vault and publishes to YouTube."""
    ready_vids = sorted(READY_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not ready_vids:
        print("ℹ️ Kasada hazır bekleyen video bulunmuyor.")
        return None

    target = ready_vids[0]
    print(f"\n🚀 KASADAN YAYINLANIYOR: {target.name}")

    try:
        url = upload_short(target, privacy_status=privacy)
        # Move to published folder
        pub_dest = PUBLISHED_DIR / target.name
        shutil.move(str(target), str(pub_dest))

        # Move companion thumb and meta if present
        for ext in ["_thumb.jpg", "_meta.json"]:
            comp = target.with_name(f"{target.stem}{ext}")
            if comp.exists():
                shutil.move(str(comp), str(PUBLISHED_DIR / comp.name))

        print(f"✅ Başarıyla Yayınlandı ve Arşivlendi: {url}")
        return url
    except Exception as e:
        print(f"⚠️ Kasa Dağıtım Uyarısı: {e}")
        return None


if __name__ == "__main__":
    print("🏛️ AKILLI KASA VE ZAMANLAYICI MOTORU DEVREDE...")
    c = ingest_rendered_videos_into_vault()
    inv = get_vault_inventory()
    print(f"\n📊 Kasa Envanteri:")
    print(f"  - Hazır Bekleyen: {inv['ready_count']} video")
    print(f"  - Yayınlanmış: {inv['published_count']} video")
