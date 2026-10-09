"""
Cross-Link Router & Multi-Funnel Spiderweb Engine for Zirvenin Kanunu.
Automatically creates inescapable viewer funnels across Shorts and Long-Form Videos:
1. Links Part N to Part N+1 and Part N-1.
2. Directs Shorts viewers to full-length Long-Form Documentaries.
3. Automatically maps and updates YouTube descriptions and pinned comments with chain links.
"""

import json
from pathlib import Path
import sys
from typing import Dict, List, Optional

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

REGISTRY_FILE = BASE_DIR / "output" / "series_crosslink_registry.json"


def load_crosslink_registry() -> Dict:
    """Loads the channel's multi-part series registry."""
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_crosslink_registry(data: Dict):
    """Saves updated links into registry."""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def register_published_episode(
    series_id: str,
    part_number: int,
    video_id: str,
    video_url: str,
    title: str
) -> Dict:
    """
    Registers a new episode in the series spiderweb and determines
    which previous video needs a "NEXT EPISODE OUT" update.
    """
    reg = load_crosslink_registry()
    if series_id not in reg:
        reg[series_id] = {
            "episodes": {},
            "documentary_url": None
        }

    reg[series_id]["episodes"][str(part_number)] = {
        "video_id": video_id,
        "video_url": video_url,
        "title": title
    }
    save_crosslink_registry(reg)

    prev_part = str(part_number - 1)
    needs_update = None
    if prev_part in reg[series_id]["episodes"]:
        prev_data = reg[series_id]["episodes"][prev_part]
        needs_update = {
            "target_video_id": prev_data["video_id"],
            "append_text": f"\n▶️ SONRAKİ BÖLÜM (Part {part_number}) YAYINDA: {video_url}"
        }

    return {
        "status": "registered",
        "series_id": series_id,
        "part_number": part_number,
        "needs_update": needs_update
    }


def generate_spiderweb_description(
    series_id: str,
    current_part: int,
    base_description: str,
    playlist_url: str
) -> str:
    """
    Assembles a full interconnected spiderweb description linking to
    previous part, next part, full playlist, and long documentary.
    """
    reg = load_crosslink_registry()
    series_data = reg.get(series_id, {"episodes": {}, "documentary_url": None})
    episodes = series_data.get("episodes", {})

    lines = [base_description.strip(), "\n" + "═" * 40, "🔗 ZİRVE GÜÇ SERİSİ BAĞLANTILARI:"]

    prev_info = episodes.get(str(current_part - 1))
    if prev_info:
        lines.append(f"◀️ Önceki Bölüm (Part {current_part - 1}): {prev_info['video_url']}")

    next_info = episodes.get(str(current_part + 1))
    if next_info:
        lines.append(f"▶️ Sonraki Bölüm (Part {current_part + 1}): {next_info['video_url']}")

    lines.append(f"📁 Tüm Bölümleri Kesintisiz İzle (Oynatma Listesi): {playlist_url}")

    if series_data.get("documentary_url"):
        lines.append(f"🎬 10 Dakikalık Tam Belgesel Versiyonu: {series_data['documentary_url']}")

    lines.append("═" * 40)
    return "\n".join(lines)


if __name__ == "__main__":
    res = register_published_episode("shelby_silent_rules", 1, "L8Eze1G3OFw", "https://youtube.com/shorts/L8Eze1G3OFw", "Part 1")
    print("🕸️ Cross-Link Spiderweb Motoru Hazır:")
    print(res)
