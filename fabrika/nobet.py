"""Reply watcher: answers Instagram comments and DMs within a minute and YouTube comments within five.

  python fabrika/nobet.py          watch for YO_NOBET_SURE seconds (5 h 40 min), then start the next watch and end
  python fabrika/nobet.py --sor    start a watch unless one is running or queued (every factory tick calls this)

Ulaş asked on 2026-10-17 for replies "dakikada bir": the factory ticks only six times a day. This runs as one long
GitHub Actions job (ya_olsaydi_nobet.yml on main; the repository is public, so the waiting runner costs nothing).
Every minute it pulls the branch, runs instagram.cevap() and every fifth minute yorum.run(), and commits
fabrika/cevap_durum.json (plus the reply reports) when something was answered, or every 10 minutes as a heartbeat
("nobet" in that file). It owns that file alone; the factory writes fabrika/durum.json. Instagram answers "too many
calls" with a 10 minute pause, any other failure with a 5 minute pause; one bad cycle never ends the watch.
Before the job's 6 hour limit it dispatches its own successor, which waits in the concurrency group.
Stop: repository variable YO_NOBET_KAPALI=1, or Actions > YA OLSAYDI cevap nöbeti > Disable workflow.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cevapdurum  # noqa: E402
import instagram  # noqa: E402
import yazar  # noqa: E402
import yorum  # noqa: E402

BRANCH = "ya-olsaydi"
WORKFLOW = "ya_olsaydi_nobet.yml"
DURATION = float(os.environ.get("YO_NOBET_SURE") or 5 * 3600 + 40 * 60)
EVERY = float(os.environ.get("YO_NOBET_ARALIK") or 60)
YT_EVERY = float(os.environ.get("YO_NOBET_YT") or 300)
BEAT = 600
RATE_PAUSE, ERROR_PAUSE = 600, 300


def iso(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).isoformat(timespec="seconds")


def git(*args, check=False):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def sync(mine):
    """Take the branch as it is on GitHub, then put back our own files that are not pushed yet."""
    if git("fetch", "-q", "origin", BRANCH).returncode == 0:
        git("reset", "-q", "--hard", f"origin/{BRANCH}")
    for path, text in mine.items():
        path.write_text(text, encoding="utf-8")


def publish(mine, msg):
    """Commit our files on top of the newest branch and push -> True when pushed."""
    for attempt in range(4):
        sync(mine)
        git("add", *[str(p.relative_to(ROOT)) for p in mine])
        if git("diff", "--cached", "--quiet").returncode == 0:
            return True  # nothing new to say
        git("commit", "-q", "-m", f"{msg} [skip ci]")
        if git("push", "-q", "origin", f"HEAD:{BRANCH}").returncode == 0:
            return True
        time.sleep(5 * (attempt + 1))
    return False


def gh(method, path, **kw):
    repo = os.environ.get("GITHUB_REPOSITORY", "ulasfirinciogullari-design/youtube-gelir-pasif-bot")
    return requests.request(method, f"https://api.github.com/repos/{repo}/{path}", timeout=30, headers={
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}", "Accept": "application/vnd.github+json"},
        **kw)


def start_next():
    r = gh("POST", f"actions/workflows/{WORKFLOW}/dispatches", json={"ref": "main"})
    return r.status_code == 204, f"{r.status_code} {r.text[:200]}"


def sor():
    """Make sure a watch is running or queued; the factory's ticks call this, so a broken chain heals itself."""
    if os.environ.get("YO_NOBET_KAPALI"):
        return "Nöbet: kapalı (YO_NOBET_KAPALI)"
    r = gh("GET", f"actions/workflows/{WORKFLOW}/runs", params={"per_page": 10})
    if r.status_code == 404:
        return "Nöbet: iş akışı henüz yok"
    if not r.ok:
        return f"Nöbet: durum okunamadı ({r.status_code})"
    live = [w for w in r.json().get("workflow_runs", [])
            if w.get("status") in ("queued", "in_progress", "waiting", "pending", "requested")]
    if live:
        return f"Nöbet: çalışıyor ({live[0]['status']})"
    ok, why = start_next()
    return "Nöbet: yeniden başlatıldı" if ok else f"Nöbet: başlatılamadı ({why})"


class Watch:
    def __init__(self):
        self.started = time.time()
        self.end = self.started + DURATION
        self.mine = {}  # path -> text we want on the branch and have not pushed yet
        self.beat = {"basladi": iso(self.started), "run": os.environ.get("GITHUB_RUN_ID"), "tur": 0,
                     "ig_cevap": 0, "yt_cevap": 0, "son_hata": None}
        self.beat_at = 0
        self.next_ig = self.next_yt = 0
        self.successor = False
        instagram.REFRESH = False
        instagram.CACHE = {}

    def cycle(self):
        t = time.time()
        sync(self.mine)
        state = cevapdurum.load()
        before = cevapdurum.dump(state)
        reports, sent = {}, 0
        if t >= self.next_ig:
            self.next_ig = t
            rep = {"started": iso(t), "mod": "nöbet"}
            try:
                instagram.cevap(state, rep, False)
            except (Exception, SystemExit) as e:  # noqa: BLE001
                rep["error"] = f"{type(e).__name__}: {e}"[:1000]
            if rep.get("error") or rep.get("rate_limit"):
                pause = RATE_PAUSE if rep.get("rate_limit") or instagram.rate_limited(rep.get("error", "")) \
                    else ERROR_PAUSE
                self.next_ig = t + pause
                self.beat["son_hata"] = f"{iso(t)} Instagram: {rep.get('error') or 'istek sınırı'}"[:300]
            n = sum(1 for i in rep.get("items", []) if i.get("result") == "gönderildi")
            self.beat["ig_cevap"] += n
            sent += n
            if rep.get("items") or rep.get("error"):
                rep["finished"] = iso()
                reports[instagram.REPORT] = rep
        if t >= self.next_yt:
            self.next_yt = t + YT_EVERY
            rep = {"started": iso(t), "deneme": False, "mod": "nöbet"}
            try:
                yorum.run(state, rep, False)
            except (Exception, SystemExit) as e:  # noqa: BLE001
                rep["error"] = f"{type(e).__name__}: {e}"[:1000]
                self.next_yt = t + max(YT_EVERY, ERROR_PAUSE)
                self.beat["son_hata"] = f"{iso(t)} YouTube: {rep['error']}"[:300]
            n = sum(1 for i in rep.get("items", []) if i.get("result") == "gönderildi")
            self.beat["yt_cevap"] += n
            sent += n
            if rep.get("items") or rep.get("error"):
                rep["log"] = yazar.LOG[-30:]
                rep["finished"] = iso()
                reports[yorum.REPORT] = rep
        self.beat["tur"] += 1
        self.beat["son_tur"] = iso()
        del yazar.LOG[:-200]  # hours of cycles: keep only the recent lines for the reports
        changed = cevapdurum.dump(state) != before or reports
        if changed or self.mine or time.time() - self.beat_at >= BEAT:  # self.mine: a push that failed earlier
            self.beat_at = time.time()
            self.mine[cevapdurum.CEVAP] = cevapdurum.dump(state, nobet=dict(self.beat))
            for path, rep in reports.items():
                self.mine[path] = json.dumps(rep, ensure_ascii=False, indent=1)
            msg = f"Nöbet: {sent} cevap" if sent else ("Nöbet: hata" if reports else "Nöbet: nabız")
            if publish(self.mine, msg):
                self.mine = {}
            if sent or reports:
                print(f"{iso()} {msg}: " + "; ".join(
                    f"{p.name}: {r.get('error') or r.get('new', 0)}" for p, r in reports.items()), flush=True)

    def run(self):
        print(f"Nöbet başladı, {DURATION / 3600:.1f} saat sürecek", flush=True)
        git("config", "user.name", "ya-olsaydi-bot")
        git("config", "user.email", "ya-olsaydi-bot@users.noreply.github.com")
        while time.time() < self.end:
            t = time.time()
            if os.environ.get("YO_NOBET_KAPALI"):
                print("YO_NOBET_KAPALI: nöbet bitti", flush=True)
                return
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001  (git or disk trouble: try again next minute)
                self.beat["son_hata"] = f"{iso()} döngü: {type(e).__name__}: {e}"[:300]
                print(self.beat["son_hata"], file=sys.stderr, flush=True)
            if not self.successor and self.end - time.time() < 10 * 60:
                self.successor = True
                ok, why = start_next()
                print(f"Sıradaki nöbet {'istendi' if ok else 'istenemedi: ' + why}", flush=True)
            time.sleep(max(1.0, min(EVERY - (time.time() - t), self.end - time.time())))
        if self.mine:
            publish(self.mine, "Nöbet: son kayıt")
        print(f"Nöbet bitti: {self.beat['tur']} tur, Instagram {self.beat['ig_cevap']}, "
              f"YouTube {self.beat['yt_cevap']} cevap", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sor", action="store_true")
    a = ap.parse_args()
    if a.sor:
        print(sor())
        return
    Watch().run()


if __name__ == "__main__":
    main()
