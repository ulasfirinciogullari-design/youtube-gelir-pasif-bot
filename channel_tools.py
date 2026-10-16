"""
Channel tools for Zirvenin Kanunu, run from the autopilot workflow.

  python channel_tools.py report            # cleanup candidates + competitor scan, changes nothing
  python channel_tools.py cleanup --action private --ids ID1,ID2
  python channel_tools.py cleanup --action delete --ids ID1,ID2
  python channel_tools.py referans --ids URL1,URL2  # Gemini breaks down reference Shorts
  python channel_tools.py durum --ids ID1,ID2       # YouTube's status fields for each video, changes nothing

Cleanup touches only the ids it is given; pick them from a report first.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.channel_audit import apply_cleanup, audit_channel, junk_ids
from app.services.channel_memory import our_video_ids
from app.services.competitor_scan import dna_age_days, load_dna, run_scan

OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "channel-data"
SCAN_EVERY_DAYS = 6


def _summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def elevenlabs_plan() -> str:
    """One line on the ElevenLabs plan: free-plan speech carries no commercial rights."""
    from app.services.elevenlabs_engine import get_elevenlabs_api_key

    key = get_elevenlabs_api_key()
    if not key:
        return "ElevenLabs anahtarı yok; ses edge-tts ile üretiliyor."
    request = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            sub = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return f"ElevenLabs planı okunamadı ({str(e)[:120]})."
    tier = str(sub.get("tier", "?"))
    rights = "yok (ücretsiz plan)" if tier == "free" else "var"
    return (f"ElevenLabs planı: {tier} | kredi {sub.get('character_count', '?')}/{sub.get('character_limit', '?')} | "
            f"anında klon: {'evet' if sub.get('can_use_instant_voice_cloning') else 'hayır'} | ticari kullanım hakkı: {rights}")


def report(youtube, force_scan: bool = False) -> int:
    _summary(f"{elevenlabs_plan()}\n\n")
    ours = our_video_ids(DATA_DIR)
    videos, flagged, text = audit_channel(youtube, keep_ids=ours)
    (OUTPUT_DIR / "channel_audit.json").write_text(
        json.dumps({"videos": videos, "candidates": [v["video_id"] for v in flagged], "junk": junk_ids(flagged)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _summary(text)
    age = dna_age_days(load_dna(DATA_DIR))
    if force_scan or age is None or age >= SCAN_EVERY_DAYS:
        our_titles = [v["title"] for v in sorted(videos, key=lambda v: -v.get("views", 0))
                      if v.get("topic_id") or v["video_id"] in ours]
        run_scan(youtube, OUTPUT_DIR, our_titles)
    else:
        print(f"Rakip analizi {age:.1f} gün önce yapıldı; {SCAN_EVERY_DAYS} günü geçince yenilenecek.")
    return 0


def cleanup(youtube, ids: list[str], action: str) -> int:
    if not ids:
        print("Temizlik için video id verilmedi; hiçbir şey değişmedi.")
        return 1
    results = apply_cleanup(youtube, ids, action)
    _summary("### Kanal temizliği\n\n" + "\n".join(f"- {r}" for r in results) + "\n")
    return 0 if all("hata" not in r for r in results) else 1


def references(youtube, text: str) -> int:
    from app.services.reference_analysis import analyze_references, extract_ids, format_report

    ids = extract_ids(text)
    if not ids:
        print("Referans için video linki verilmedi.")
        return 1
    result = analyze_references(ids, youtube)
    (OUTPUT_DIR / "reference_style.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _summary(format_report(result))
    return 0 if result["spec"] else 1


def status(youtube, ids: list[str]) -> int:
    """What YouTube reports about each video (processing, privacy, restrictions, counts), printed and saved to
    channel-data/video_status.json, which the backup step commits."""
    parts = "status,statistics,contentDetails,processingDetails,snippet"
    items = youtube.videos().list(part=parts, id=",".join(ids[:50])).execute().get("items", [])
    found = {item["id"]: item for item in items}
    report = {}
    for video_id in ids[:50]:
        item = found.get(video_id)
        if item is None:
            print(f"{video_id}: YouTube bu videoyu döndürmedi (silinmiş, gizli ya da başka kanalın)")
            report[video_id] = None
            continue
        report[video_id] = {"title": item["snippet"].get("title", ""),
                            "published_at": item["snippet"].get("publishedAt", ""),
                            **{key: item.get(key) for key in ("status", "statistics", "contentDetails",
                                                              "processingDetails")}}
        print(f"--- {video_id}")
        print(json.dumps(report[video_id], ensure_ascii=False, indent=1))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "video_status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Zirvenin Kanunu channel tools")
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--scan", action="store_true", help="Rakip analizini güncel olsa da yenile")
    clean = sub.add_parser("cleanup")
    clean.add_argument("--action", choices=["private", "delete"], default="private")
    clean.add_argument("--ids", default="", help="Virgülle ayrılmış video id listesi")
    ref = sub.add_parser("referans")
    ref.add_argument("--ids", default="", help="Virgülle ayrılmış Shorts linkleri veya id'leri")
    stat = sub.add_parser("durum")
    stat.add_argument("--ids", default="", help="Virgülle ayrılmış video id listesi")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    from publish_to_youtube import get_authenticated_service
    youtube = get_authenticated_service()
    if args.command == "report":
        return report(youtube, force_scan=args.scan)
    if args.command == "referans":
        return references(youtube, args.ids)
    ids = [i.strip() for i in args.ids.replace("\n", ",").split(",") if i.strip()]
    if args.command == "durum":
        return status(youtube, ids)
    return cleanup(youtube, ids, args.action)


if __name__ == "__main__":
    sys.exit(main())
