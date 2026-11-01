"""Record a scheduled episode (uret.py --mod zamanla) in channel-data's uploads.json.

The record carries the slot start the episode goes public at, as uploaded_at. main's scripts/slot_gate.py ignores
it until then and from that moment counts the slot as filled, so the merak Short of that slot is skipped and the
day stays at eight Shorts. The views report and the comment replies also find the episode there.

The main Shorts workflow appends to the same file and gives up its own push when a rebase conflicts, so this runs
between slots (calistir.sh is started by a push of tetik.txt, never by the slot schedule).

  python animasyon/kayit.py animasyon/rapor.json animasyon/bolumler/k002.json
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

BRANCH = "channel-data"


def record(report, ep):
    return {"video_id": report["video_id"], "topic_id": ep["id"], "title": ep["yt_title"][:100],
            "format": "animasyon", "uploaded_at": report["publish_at"], "privacy": "public", "scheduled": True}


def merged(uploads, rec):
    """uploads with rec added; the same list when the video is already there."""
    if any(isinstance(u, dict) and u.get("video_id") == rec["video_id"] for u in uploads):
        return uploads
    return uploads + [rec]


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def push(rec, attempts=4):
    for attempt in range(1, attempts + 1):
        tmp = tempfile.mkdtemp(prefix="cd-")
        git("fetch", "-q", "origin", BRANCH)
        git("worktree", "add", "-q", "--detach", tmp, f"origin/{BRANCH}")
        try:
            path = Path(tmp) / "uploads.json"
            uploads = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            new = merged(uploads, rec)
            if new is uploads:
                print(f"{rec['video_id']} zaten uploads.json'da")
                return True
            path.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            git("add", "uploads.json", cwd=tmp)
            git("-c", "user.name=zk-animasyon-bot", "-c", "user.email=zk-animasyon-bot@users.noreply.github.com",
                "commit", "-q", "-m", f"Animasyon {rec['topic_id']} zamanlandı: {rec['uploaded_at']}", cwd=tmp)
            if git("push", "-q", "origin", f"HEAD:{BRANCH}", cwd=tmp, check=False).returncode == 0:
                print(f"uploads.json: {rec['topic_id']} {rec['video_id']} {rec['uploaded_at']}")
                return True
        finally:
            git("worktree", "remove", "--force", tmp, check=False)
        time.sleep(attempt * 5)
    return False


def main(report_path, episode_path):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not (report.get("video_id") and report.get("publish_at")):
        print("Zamanlanmış yükleme yok, kayıt atlandı")
        return 0
    ep = json.loads(Path(episode_path).read_text(encoding="utf-8"))
    return 0 if push(record(report, ep)) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
