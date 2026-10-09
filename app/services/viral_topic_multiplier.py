"""
Viral Topic Multiplier & Velocity Intelligence for Zirvenin Kanunu.
Analyzes real-time performance across all channel videos, identifies breakout winners,
and dynamically multiplies production in the highest-ROI niches.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Dict, List, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service


def fetch_channel_performance_leaderboard() -> List[Dict]:
    """
    Fetches real view, like, and comment stats for all channel videos
    and ranks them by velocity and engagement.
    """
    youtube = get_authenticated_service()

    # Get uploaded videos playlist
    ch_res = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = ch_res.get("items", [])
    if not items:
        return []

    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Fetch last 30 videos
    pl_res = youtube.playlistItems().list(part="snippet", playlistId=uploads_id, maxResults=30).execute()
    video_ids = [item["snippet"]["resourceId"]["videoId"] for item in pl_res.get("items", [])]

    if not video_ids:
        return []

    # Get detailed stats
    vid_res = youtube.videos().list(part="snippet,statistics", id=",".join(video_ids)).execute()

    leaderboard = []
    now = datetime.now(timezone.utc)

    for v in vid_res.get("items", []):
        snip = v.get("snippet", {})
        stats = v.get("statistics", {})

        title = snip.get("title", "")
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))

        pub_time = datetime.fromisoformat(snip.get("publishedAt").replace("Z", "+00:00"))
        age_hours = max(0.5, (now - pub_time).total_seconds() / 3600.0)

        # Velocity score (views per hour weighted by comment/like engagement)
        vph = views / age_hours
        engagement_multiplier = 1.0 + (likes * 2.0 / max(1, views)) + (comments * 5.0 / max(1, views))
        score = vph * engagement_multiplier

        # Detect topic
        topic = "general"
        title_lower = title.lower()
        if any(k in title_lower for k in ["shelby", "peaky"]):
            topic = "shelby"
        elif any(k in title_lower for k in ["walter", "heisenberg", "breaking bad"]):
            topic = "walter_white"
        elif any(k in title_lower for k in ["durden", "fight club"]):
            topic = "fight_club"
        elif any(k in title_lower for k in ["yasa", "güç", "power"]):
            topic = "power_laws"
        elif any(k in title_lower for k in ["stoa", "aurelius", "seneca"]):
            topic = "stoic"
        elif any(k in title_lower for k in ["kara delik", "evren", "kozmik", "interstellar"]):
            topic = "cosmic"

        leaderboard.append({
            "video_id": v["id"],
            "title": title,
            "topic": topic,
            "views": views,
            "likes": likes,
            "comments": comments,
            "vph": round(vph, 2),
            "velocity_score": round(score, 2)
        })

    leaderboard.sort(key=lambda x: x["velocity_score"], reverse=True)
    return leaderboard


def compute_winning_topic_weights(leaderboard: List[Dict]) -> Dict[str, float]:
    """
    Computes production multipliers for each topic based on top performers.
    """
    topic_scores = {}
    for entry in leaderboard:
        top = entry["topic"]
        topic_scores[top] = topic_scores.get(top, 0.0) + entry["velocity_score"]

    total = sum(topic_scores.values()) or 1.0
    weights = {k: round(v / total, 3) for k, v in topic_scores.items()}
    return weights


if __name__ == "__main__":
    print("🧠 VİRAL KONU ÇARPANLARI VE KANAL LİDERLİK TABLOSU HESAPLANIYOR...")
    lb = fetch_channel_performance_leaderboard()
    print(f"📊 Analiz Edilen Video Sayısı: {len(lb)}")
    if lb:
        print("\n🏆 EN ÇOK İZLENEN İLK 5 VİDEO:")
        for idx, item in enumerate(lb[:5], 1):
            print(f"  {idx}. [{item['topic'].upper()}] {item['title'][:40]} -> {item['views']} İzlenme (Skor: {item['velocity_score']})")

        weights = compute_winning_topic_weights(lb)
        print("\n🎯 YENİ İÇERİK ÜRETİM AĞIRLIKLARI (EN ÇOK İZLENENE DAHA ÇOK AĞIRLIK):")
        for top, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {top.upper()}: %{int(w * 100)}")
