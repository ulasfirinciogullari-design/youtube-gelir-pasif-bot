import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import publish_to_youtube

sys.stdout.reconfigure(encoding="utf-8")

yt = publish_to_youtube.get_authenticated_service()
video_id = sys.argv[1] if len(sys.argv) > 1 else "4NTZfsb8igk"
resp = yt.commentThreads().list(part="snippet", videoId=video_id).execute()
items = resp.get("items", [])
print(f"=== TOTAL COMMENTS UNDER {video_id}: {len(items)} ===")
for i, item in enumerate(items, 1):
    author = item["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"]
    text = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
    print(f"[{i}] {author}: {text}")
