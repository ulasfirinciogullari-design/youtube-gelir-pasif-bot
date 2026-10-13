"""
Channel tools for Zirvenin Kanunu, run from the autopilot workflow.

  python channel_tools.py report            # cleanup candidates + competitor scan, changes nothing
  python channel_tools.py cleanup --action private --ids ID1,ID2
  python channel_tools.py cleanup --action delete --ids ID1,ID2

Cleanup touches only the ids it is given; pick them from a report first.
"""

import argparse
import json
import os
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.channel_audit import apply_cleanup, audit_channel
from app.services.competitor_scan import run_scan

OUTPUT_DIR = BASE_DIR / "output"


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def report(youtube) -> int:
    videos, flagged, text = audit_channel(youtube)
    (OUTPUT_DIR / "channel_audit.json").write_text(
        json.dumps({"videos": videos, "candidates": [v["video_id"] for v in flagged]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _summary(text)
    our_titles = [v["title"] for v in sorted(videos, key=lambda v: -v.get("views", 0)) if v.get("topic_id")]
    run_scan(youtube, OUTPUT_DIR, our_titles)
    return 0


def cleanup(youtube, ids: list[str], action: str) -> int:
    if not ids:
        print("Temizlik için video id verilmedi; hiçbir şey değişmedi.")
        return 1
    results = apply_cleanup(youtube, ids, action)
    _summary("### Kanal temizliği\n\n" + "\n".join(f"- {r}" for r in results) + "\n")
    return 0 if all("hata" not in r for r in results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Zirvenin Kanunu channel tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report")
    clean = sub.add_parser("cleanup")
    clean.add_argument("--action", choices=["private", "delete"], default="private")
    clean.add_argument("--ids", default="", help="Virgülle ayrılmış video id listesi")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    from publish_to_youtube import get_authenticated_service
    youtube = get_authenticated_service()
    if args.command == "report":
        return report(youtube)
    ids = [i.strip() for i in args.ids.replace("\n", ",").split(",") if i.strip()]
    return cleanup(youtube, ids, args.action)


if __name__ == "__main__":
    sys.exit(main())
