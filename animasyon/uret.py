"""Zirvenin Kanunu animated episode: voice, draw, check, upload.

  python animasyon/uret.py --bolum k001 --mod deneme   video and storyboard only, nothing uploaded
  python animasyon/uret.py --bolum k001 --mod gizli    and upload it private to Zirvenin Kanunu

The episode script is animasyon/bolumler/<id>.json (written by Claude, see BOLUM_REHBERI.md). Music and stock
effects come from the channel-data branch (--data). Outcome: animasyon/rapor.json and
animasyon/cikti/<id>_story.jpg (one frame per second) plus <id>_mix.mp3 for checking the sound.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MOTOR = HERE / "motor"
OUT = HERE / "cikti"
REPORT = HERE / "rapor.json"
SCENES = {"usta", "meclis", "yaranma", "mektup", "gonder", "celiski", "cozum", "dost"}


def validate(ep):
    probs = []
    for k in ("id", "kanun", "title", "yt_title", "lines"):
        if not ep.get(k):
            probs.append(f"{k} eksik")
    lines = ep.get("lines") or []
    if not 5 <= len(lines) <= 9:
        probs.append(f"{len(lines)} satır (5-9 olmalı)")
    words = sum(len(str(l.get("text", "")).split()) for l in lines)
    if not 40 <= words <= 100:
        probs.append(f"{words} kelime (40-100 olmalı)")
    for i, l in enumerate(lines):
        if (l.get("v") or {}).get("t") not in SCENES:
            probs.append(f"satır {i + 1}: bilinmeyen sahne {(l.get('v') or {}).get('t')}")
    return probs


def render(ep, timings, mix, video):
    (MOTOR / "bolum.js").write_text("window.EP = " + json.dumps(
        {k: ep.get(k) for k in ("kanun", "title", "lines", "tablolar")}, ensure_ascii=False) + ";\n", encoding="utf-8")
    subprocess.run(["node", str(MOTOR / "render.js"), str(video), str(timings), str(mix)], check=True)
    story = OUT / "_story"
    shutil.rmtree(story, ignore_errors=True)
    story.mkdir(parents=True)
    subprocess.run(["node", str(MOTOR / "render.js"), "--story", str(story), str(timings)], check=True)
    shutil.copy(story / "story.jpg", OUT / f"{ep['id']}_story.jpg")
    shutil.rmtree(story, ignore_errors=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                               capture_output=True, text=True, check=True).stdout.strip())
    if not 12 <= dur <= 60:
        raise RuntimeError(f"video süresi beklenmedik: {dur:.1f} sn")
    return dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bolum", required=True)
    ap.add_argument("--mod", choices=["deneme", "gizli"], default="deneme")
    ap.add_argument("--data", default="channel-data")
    a = ap.parse_args()
    report = {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bolum": a.bolum, "mod": a.mod}
    try:
        import ses
        ep = json.loads((HERE / "bolumler" / f"{a.bolum}.json").read_text(encoding="utf-8"))
        probs = validate(ep)
        if probs:
            raise RuntimeError("Senaryo kurallara uymuyor: " + "; ".join(probs))
        work = HERE / "_is" / ep["id"]
        work.mkdir(parents=True, exist_ok=True)
        OUT.mkdir(exist_ok=True)
        import tablolar
        report["tablolar"] = tablolar.fetch(ep)
        timings, mix, chars = ses.make(ep, work, Path(a.data))
        report.update(chars=chars, voice={k: v for k, v in ses.VOICE.items() if k != "voice_id"})
        shutil.copy(mix, OUT / f"{ep['id']}_mix.mp3")
        shutil.copy(work / "timings.json", OUT / f"{ep['id']}_timings.json")  # lets frames be checked locally
        video = work / f"{ep['id']}.mp4"
        t = time.time()
        report["duration"] = round(render(ep, work / "timings.json", mix, video), 2)
        report["render_seconds"] = round(time.time() - t)
        report["title"] = ep["yt_title"]
        if a.mod == "gizli":
            import yukle
            report.update(yukle.upload(ep, video, "private"))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as e:  # the report is the only thing the thread can read
        report["error"] = f"{type(e).__name__}: {e}"[:600]
        report["trace"] = traceback.format_exc()[-1500:]
        print(report["trace"])
        return 1
    finally:
        report["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
