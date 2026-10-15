"""
What the factories have uploaded, kept on the channel-data branch.

The "Seri: <id>" line in a description is how a run sees which topics are
done, but a Studio edit can remove it (Ulaş published two test videos and
their markers were gone, so the next run picked zk-001 again). Every upload is
therefore also recorded here, and the topic queue and the cleanup report read
both.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

UPLOADS_FILE = "uploads.json"


def load_uploads(data_dir: Path) -> list[dict]:
    path = Path(data_dir) / UPLOADS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [u for u in data if isinstance(u, dict)] if isinstance(data, list) else []


def record_upload(data_dir: Path, video_id: str, topic_id: str, title: str, content_format: str,
                  *, privacy: str = "") -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads = [u for u in load_uploads(data_dir) if u.get("video_id") != video_id]
    record = {
        "video_id": video_id,
        "topic_id": topic_id,
        "title": title,
        "format": content_format,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if privacy:
        # scripts/slot_gate.py lets a private test upload neither fill a schedule slot nor delay the next one.
        record["privacy"] = privacy
    uploads.append(record)
    (data_dir / UPLOADS_FILE).write_text(json.dumps(uploads, ensure_ascii=False, indent=2), encoding="utf-8")


def used_topic_ids(data_dir: Path) -> set[str]:
    return {u["topic_id"] for u in load_uploads(data_dir) if u.get("topic_id")}


def our_video_ids(data_dir: Path) -> frozenset:
    return frozenset(u["video_id"] for u in load_uploads(data_dir) if u.get("video_id"))
