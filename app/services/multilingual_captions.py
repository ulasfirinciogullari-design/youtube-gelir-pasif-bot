"""
Global Multilingual Captions Engine for Zirvenin Kanunu.
Generates and uploads compliant .srt / .vtt Closed Caption tracks
in 6 major global languages (Spanish, German, French, Portuguese, Arabic, Indonesian)
to maximize international discovery and allow non-Turkish speakers to watch smoothly.
"""

from pathlib import Path
import re
import sys
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from googleapiclient.http import MediaFileUpload
from publish_to_youtube import get_authenticated_service

# Dictionary of high-velocity translations for core cinematic cues
GLOBAL_LANG_CODES = {
    "es": "Spanish (Español)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
    "pt": "Portuguese (Português)",
    "ar": "Arabic (العربية)",
    "id": "Indonesian (Bahasa Indonesia)",
}


def format_srt_time(seconds: float) -> str:
    """Formats float seconds into SRT timestamp: 00:00:00,000"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def build_srt_file(timed_phrases: List[Tuple[float, float, str]], output_path: Path):
    """
    Writes a valid SubRip (.srt) file from a list of (start_s, end_s, text).
    """
    lines = []
    for idx, (start_s, end_s, text) in enumerate(timed_phrases, 1):
        lines.append(str(idx))
        lines.append(f"{format_srt_time(start_s)} --> {format_srt_time(end_s)}")
        lines.append(text.strip())
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def upload_caption_to_youtube(video_id: str, srt_file: Path, lang_code: str, lang_name: str) -> bool:
    """
    Uploads a Closed Caption track to an existing YouTube video using YouTube Data API v3.
    """
    if not srt_file.exists():
        return False

    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "videoId": video_id,
            "language": lang_code,
            "name": lang_name,
            "isDraft": False,
        }
    }

    media = MediaFileUpload(str(srt_file), mimetype="application/x-subrip", resumable=True)

    try:
        req = youtube.captions().insert(
            part="snippet",
            body=body,
            media_body=media
        )
        req.execute()
        print(f"🌍 Altyazı Eklendi: [{lang_code.upper()}] {lang_name} -> Video {video_id}")
        return True
    except Exception as e:
        # Note: YouTube API sometimes limits caption inserts if already exists or quota
        print(f"ℹ️ Altyazı yükleme notu ({lang_code}): {e}")
        return False
