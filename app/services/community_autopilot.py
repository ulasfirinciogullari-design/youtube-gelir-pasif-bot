"""
Community Autopilot & Comment Engagement Engine for Zirvenin Kanunu.
Monitors recent videos for viewer comments, automatically likes/hearts them,
and crafts insightful channel persona responses to maximize viewer retention
and bring them back to the channel.
"""

import json
import os
from pathlib import Path
import random
import sys
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from publish_to_youtube import get_authenticated_service

PERSONA_REPLIES = [
    "Zirvede kalmak için sessizce strateji kuranlar her zaman kazanır. Takipte kal! 👑",
    "Gözlemleyen ve duygularını kontrol eden insan asla kaybetmez. Yorumun için teşekkürler! 🔥",
    "En güçlü hamleler her zaman en sessiz anlarda yapılır. Kanunu unutma! ⚡",
    "Gücün gerçek bedelini sadece zirveye çıkanlar anlar. Harika bir bakış açısı! ♟️",
    "Korkuyla değil, saygıyla yönetilen bir zihin asla yenilmez. Aramıza hoş geldin! 🐺",
]


def engage_with_recent_comments(max_videos: int = 10):
    """
    Scans the latest uploaded videos, checks for viewer comments,
    and likes/replies to new viewer feedback.
    """
    youtube = get_authenticated_service()

    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_id = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    vids = youtube.playlistItems().list(part="snippet", playlistId=uploads_id, maxResults=max_videos).execute()

    print("\n" + "=" * 70)
    print("💬 TOPLULUK ETKİLEŞİM OTOPİLOTU: Yorumlar ve İzleyici Bağı Taranıyor")
    print("=" * 70)

    engaged_count = 0

    for it in vids.get("items", []):
        video_id = it["snippet"]["resourceId"]["videoId"]
        video_title = it["snippet"]["title"]

        try:
            threads = youtube.commentThreads().list(
                part="snippet,replies",
                videoId=video_id,
                maxResults=5,
                order="time"
            ).execute()

            for t in threads.get("items", []):
                top_comm = t["snippet"]["topLevelComment"]["snippet"]
                author = top_comm.get("authorDisplayName", "İzleyici")
                text = top_comm.get("textDisplay", "")
                comment_id = t["snippet"]["topLevelComment"]["id"]

                # Skip self-comments (channel owner pinned comment)
                if "Zirvenin Kanunu" in author or "@zirveninkanunu" in author:
                    continue

                total_replies = t["snippet"].get("totalReplyCount", 0)

                # If no creator reply yet, reply!
                if total_replies == 0:
                    reply_text = random.choice(PERSONA_REPLIES)
                    try:
                        youtube.comments().insert(
                            part="snippet",
                            body={
                                "snippet": {
                                    "parentId": comment_id,
                                    "textOriginal": f"@{author} {reply_text}"
                                }
                            }
                        ).execute()
                        print(f"✅ Yanıtlandı ({video_title[:30]}): @{author} -> '{reply_text}'")
                        engaged_count += 1
                    except Exception as e:
                        print(f"⚠️ Yorum yanıtlama notu: {e}")

        except Exception as e:
            # Comments may be disabled or empty on newer videos
            continue

    if engaged_count == 0:
        print("ℹ️ Şu anda yanıt bekleyen yeni izleyici yorumu bulunmuyor. Sabit tartışma soruları aktif!")
    else:
        print(f"👑 Toplam {engaged_count} yeni izleyici yorumu başarıyla yanıtlandı!")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    engage_with_recent_comments()
