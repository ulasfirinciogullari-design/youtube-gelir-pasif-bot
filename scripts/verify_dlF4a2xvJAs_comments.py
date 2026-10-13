import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import publish_to_youtube

sys.stdout.reconfigure(encoding="utf-8")

yt = publish_to_youtube.get_authenticated_service()
resp = yt.commentThreads().list(part="snippet", videoId="dlF4a2xvJAs").execute()
items = resp.get("items", [])
print(f"=== TOTAL COMMENTS UNDER dlF4a2xvJAs: {len(items)} ===")
for i, item in enumerate(items, 1):
    author = item["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"]
    text = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
    print(f"[{i}] {author}: {text}")
