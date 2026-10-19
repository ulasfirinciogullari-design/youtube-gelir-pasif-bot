"""
Instagram watch for Zirvenin Kanunu: answers comments and DMs within a minute and posts the day's Reels.

The same setup as Ya Olsaydı?'s watch (Ulaş, 2026-10-18: "bunu bu kanal içinde yapalım"). One long GitHub Actions
job (.github/workflows/zk_instagram_nobet.yml; the repository is public, so the waiting runner costs nothing): every
minute it takes the channel-data branch as it is on GitHub, runs instagram_zk.cevap(), and at each Reel hour
instagram_zk.reel_slot(). It commits channel-data/instagram.json (plus the reply report) when something happened,
and every 30 minutes as a heartbeat ("nobet" in that file). It is the only writer of instagram.json besides one-off
Reels, whose records it merges, and of instagram_token.enc, so the Shorts and comment workflows never conflict with
it. Instagram answers "too many calls" with a 10 minute pause, any other failure with a 5 minute one; one bad
cycle never ends the watch. Before the job's 6 hour limit it dispatches its own successor, which waits in the
concurrency group. Standard library plus cryptography.

  python3 scripts/instagram_watch.py            watch for 5 h 40 min, then start the next watch and end
  python3 scripts/instagram_watch.py --ensure   start a watch unless one is running or queued (every Shorts run)

Stop: repository variable ZK_IG_NOBET_KAPALI=1, or Actions > Zirvenin Kanunu Instagram nöbeti > Disable workflow.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instagram_zk as ig  # noqa: E402

WORKFLOW = "zk_instagram_nobet.yml"
DURATION = float(os.environ.get("ZK_IG_NOBET_SURE") or 5 * 3600 + 40 * 60)
EVERY = 60
BEAT = 1800
RATE_PAUSE, ERROR_PAUSE = 600, 300
LIVE = ("queued", "in_progress", "waiting", "pending", "requested")


def github(method: str, path: str, body=None) -> tuple[int, dict]:
    repo = os.environ.get("GITHUB_REPOSITORY", "ulasfirinciogullari-design/youtube-gelir-pasif-bot")
    status, raw = ig.http(method, f"{ig.GITHUB_API}/{repo}/{path}", body=body, timeout=30, headers={
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json"})
    try:
        return status, json.loads(raw) if raw.strip() else {}
    except ValueError:
        return status, {}


def start_next() -> tuple[bool, str]:
    status, data = github("POST", f"actions/workflows/{WORKFLOW}/dispatches", {"ref": "main"})
    return status == 204, f"{status} {str(data)[:200]}"


def ensure() -> str:
    """Makes sure a watch is running or queued; every Shorts run calls this, so a broken chain heals itself."""
    if os.environ.get("ZK_IG_NOBET_KAPALI"):
        return "📸 Instagram nöbeti kapalı (ZK_IG_NOBET_KAPALI)"
    if not os.environ.get("ZK_IG_HAZIR"):
        return "📸 Instagram nöbeti başlatılmadı: ZK_IG_TOKEN henüz yok"
    status, data = github("GET", f"actions/workflows/{WORKFLOW}/runs?per_page=10")
    if status == 404:
        return "📸 Instagram nöbeti iş akışı henüz yok"
    if status >= 400:
        return f"📸 Instagram nöbeti durumu okunamadı ({status})"
    live = [run for run in data.get("workflow_runs", []) if run.get("status") in LIVE]
    if live:
        return f"📸 Instagram nöbeti çalışıyor ({live[0].get('status')})"
    ok, why = start_next()
    return "📸 Instagram nöbeti yeniden başlatıldı" if ok else f"📸 Instagram nöbeti başlatılamadı ({why})"


class Watch:
    def __init__(self, data_dir: Path, out_dir: Path):
        self.data, self.out = Path(data_dir), Path(out_dir)
        self.started = time.time()
        self.end = self.started + DURATION
        self.pending = {}  # file name -> content we want on the branch and have not pushed yet
        self.beat = {"basladi": ig.iso(self.started), "run": os.environ.get("GITHUB_RUN_ID"), "tur": 0,
                     "cevap": 0, "reel": 0, "son_hata": None}
        self.beat_at = 0.0
        self.next_ig = 0.0
        self.successor = False
        ig.REFRESH = True
        ig.CACHE = {}

    def cycle(self) -> None:
        t = time.time()
        ig.sync(self.data)
        for name, content in self.pending.items():  # a push that failed earlier
            path = self.data / name
            path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content, encoding="utf-8")
        state = ig.load_state(self.data)
        before = ig.dump(state)
        keyfile = self.data / ig.KEY_FILE
        key_before = keyfile.read_bytes() if keyfile.exists() else None
        report, sent, reels = {}, 0, 0
        if t >= self.next_ig:
            rep = {"started": ig.iso(t), "mod": "nöbet"}
            try:
                ig.cevap(self.data, state, rep)
            except Exception as e:  # noqa: BLE001
                rep["error"] = f"{type(e).__name__}: {e}"[:1000]
            try:
                result = ig.reel_slot(self.data, state, rep, datetime.now(timezone.utc), self.out)
                reels = int(bool(result) and not result.startswith(("hata", "atlandı")))
            except Exception as e:  # noqa: BLE001
                rep["error"] = f"{rep.get('error', '')} Reel: {type(e).__name__}: {e}".strip()[:1000]
            if rep.get("error") or rep.get("rate_limit"):
                limited = rep.get("rate_limit") or ig.rate_limited(rep.get("error", ""))
                self.next_ig = t + (RATE_PAUSE if limited else ERROR_PAUSE)
                self.beat["son_hata"] = f"{ig.iso(t)} {rep.get('error') or 'istek sınırı'}"[:300]
            sent = sum(1 for i in rep.get("items", []) if i.get("result") == "gönderildi")
            if rep.get("items") or rep.get("error") or rep.get("reel"):
                report = {**rep, "finished": ig.iso()}
        self.beat["cevap"] += sent
        self.beat["reel"] += reels
        self.beat["tur"] += 1
        self.beat["son_tur"] = ig.iso()
        key_after = keyfile.read_bytes() if keyfile.exists() else None
        changed = ig.dump(state) != before or report or key_after != key_before
        if changed or self.pending or time.time() - self.beat_at >= BEAT:
            self.beat_at = time.time()
            state["nobet"] = dict(self.beat)
            snapshot = dict(state)
            files = {}
            if report:
                files[ig.REPORT_FILE] = json.dumps(report, ensure_ascii=False, indent=1)
            if key_after is not None and key_after != key_before:
                files[ig.KEY_FILE] = key_after
            self.pending = {**self.pending, **files}
            make = lambda: {**self.pending, ig.STATE_FILE: ig.merged(self.data, snapshot)}  # noqa: E731
            msg = ("Instagram nöbeti: Reel" if reels else f"Instagram nöbeti: {sent} cevap" if sent
                   else "Instagram nöbeti: hata" if report.get("error") else "Instagram nöbeti: nabız")
            if ig.push(self.data, make, msg):
                self.pending = {}
            else:
                self.pending[ig.STATE_FILE] = make()[ig.STATE_FILE]
            if sent or reels or report.get("error"):
                print(f"{ig.iso()} {msg}: {report.get('error') or report.get('reel') or report.get('new', 0)}",
                      flush=True)

    def run(self) -> None:
        print(f"📸 Instagram nöbeti başladı, {DURATION / 3600:.1f} saat sürecek", flush=True)
        while time.time() < self.end:
            t = time.time()
            if os.environ.get("ZK_IG_NOBET_KAPALI"):
                print("ZK_IG_NOBET_KAPALI: nöbet bitti", flush=True)
                return
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001  (git or disk trouble: try again next minute)
                self.beat["son_hata"] = f"{ig.iso()} döngü: {type(e).__name__}: {e}"[:300]
                print(self.beat["son_hata"], file=sys.stderr, flush=True)
            if not self.successor and self.end - time.time() < 10 * 60:
                self.successor = True
                ok, why = start_next()
                print(f"Sıradaki nöbet {'istendi' if ok else 'istenemedi: ' + why}", flush=True)
            time.sleep(max(1.0, min(EVERY - (time.time() - t), self.end - time.time())))
        print(f"📸 Instagram nöbeti bitti: {self.beat['tur']} tur, {self.beat['cevap']} cevap, "
              f"{self.beat['reel']} Reel", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Zirvenin Kanunu Instagram nöbeti")
    parser.add_argument("--ensure", action="store_true", help="Nöbet çalışmıyorsa başlat ve çık")
    parser.add_argument("--data", default="channel-data", help="Kanal hafızası klasörü")
    parser.add_argument("--out", default="output/instagram", help="Yayın paketlerinin açılacağı klasör")
    args = parser.parse_args(argv)
    if args.ensure:
        try:
            print(ensure())
        except (OSError, urllib.error.URLError, ValueError) as e:  # never red: the next Shorts run tries again
            print(f"⚠️ Instagram nöbeti kontrol edilemedi: {e!r}")
        return 0
    if not os.environ.get("ZK_IG_TOKEN"):
        print("ZK_IG_TOKEN yok: Instagram nöbeti başlamadı (token eklenince bir Short çalışması onu başlatır)")
        return 0
    Watch(Path(args.data), Path(args.out)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
