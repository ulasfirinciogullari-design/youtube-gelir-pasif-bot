"""
Hourly watch that keeps the 24-a-day Shorts schedule going without GitHub's cron.

GitHub ran only 5 of the 48 '7,37 * * * *' ticks of the Shorts workflow between 2026-10-15 08:40 and
2026-10-16 08:40 UTC. Most Shorts were started by a Claude session dispatching the workflow by hand, and
every gap in that session (a usage limit 18:42-23:19 UTC, an outage 01:42-04:38 UTC) cost the channel its
Shorts. This script runs in the "Saatlik Short Nöbeti" workflow (.github/workflows/slot_heartbeat.yml) and
waits on the runner instead: at minute 25 and 45 of every hour it applies the slot gate (scripts/slot_gate.py)
to the live channel data and starts the Shorts workflow when the slot is open. After RUN_FOR it starts its
own successor and ends, before GitHub's 6 h job limit. The Shorts workflow calls --ensure on every run, so
a broken chain restarts with the next cron tick or hand dispatch.

A check starts a Short only when all of these hold:
  - no run of the Shorts workflow is queued or in progress (a cron tick or a hand dispatch is already on it);
  - slot_gate.decide() says go, with this watch's own dispatches in the slot counted like scheduled ticks:
    they skip the workflow's own gate (manual runs always go ahead), so one that got through the produce
    step fills the slot and two failures leave it for the next one.
Any API error means no dispatch for that check.

The repository is public, so the waiting runner costs no Actions minutes. Standard library only.

  python scripts/slot_heartbeat.py --once --dry-run   # one check against the live data, nothing started
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slot_gate  # noqa: E402

CHECK_MINUTES = (25, 45)  # the Claude dispatcher fires at :20, so :25 finds its run in progress
RUN_FOR = timedelta(hours=5, minutes=40)
SHORTS_WORKFLOW = slot_gate.WORKFLOW_FILE
WATCH_WORKFLOW = "slot_heartbeat.yml"
SHORTS_INPUTS = {"task": "video", "privacy": "public", "format": "merak"}
BOT = "github-actions[bot]"  # the actor of every run started with the job's GITHUB_TOKEN
HANDOVER_ATTEMPTS = 3


class GitHub:
    """The few REST calls the watch needs, for one repository."""

    def __init__(self, repo: str, token: str, timeout: float = 30.0):
        if not repo:
            raise ValueError("GITHUB_REPOSITORY tanımlı değil")
        self.repo, self.token, self.timeout = repo, token, timeout

    def request(self, method: str, path: str, body=None, raw: bool = False):
        headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "zirve-slot-heartbeat"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{slot_gate.API}/repos/{self.repo}/{path}", data=data,
                                         method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read().decode("utf-8")
        if raw:
            return payload
        return json.loads(payload) if payload.strip() else {}

    def get(self, path: str, raw: bool = False):
        return self.request("GET", path, raw=raw)

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)


def next_check(now: datetime) -> datetime:
    """The first check moment strictly after now."""
    hour = slot_gate._utc(now).replace(minute=0, second=0, microsecond=0)
    return min(start.replace(minute=minute) for start in (hour, hour + timedelta(hours=1))
               for minute in CHECK_MINUTES if start.replace(minute=minute) > now)


def _runs(gh, workflow: str, query: str) -> list[dict]:
    return gh.get(f"actions/workflows/{workflow}/runs?{query}").get("workflow_runs") or []


def _live(runs: list[dict]) -> list[dict]:
    return [run for run in runs if run.get("status") != "completed"]


def slot_runs(gh, slot: datetime) -> list[dict]:
    """Scheduled ticks and this watch's dispatches since the slot started, shaped for slot_gate.decide()."""
    since = slot_gate._utc(slot).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = []
    for run in _runs(gh, SHORTS_WORKFLOW, f"created=%3E%3D{since}&per_page=30"):
        ours = run.get("event") == "workflow_dispatch" and (run.get("actor") or {}).get("login") == BOT
        if run.get("event") != "schedule" and not ours:
            continue  # Ulaş's and Claude's hand dispatches are covered by the busy check and uploads.json
        jobs = gh.get(f"actions/runs/{run.get('id')}/jobs")
        runs.append({"id": run.get("id"), "event": "schedule", "created_at": run.get("created_at"),
                     "conclusion": run.get("conclusion"),
                     "steps": [{"name": s.get("name"), "conclusion": s.get("conclusion")}
                               for job in jobs.get("jobs") or [] for s in job.get("steps") or []]})
    return runs


def load_uploads(gh) -> list[dict]:
    data = json.loads(gh.get("contents/uploads.json?ref=channel-data", raw=True))
    return [u for u in data if isinstance(u, dict)] if isinstance(data, list) else []


def check(gh, now: datetime) -> tuple[bool, list[str]]:
    """Should the watch start a Short at `now`? Returns (go, reasons in Turkish)."""
    busy = _live(_runs(gh, SHORTS_WORKFLOW, "per_page=10"))
    if busy:
        return False, [f"⛔ Short iş akışında {len(busy)} çalışma sırada veya sürüyor "
                       f"({busy[0].get('event')}, {busy[0].get('id')}); bu kontrol atlanıyor"]
    return slot_gate.decide(now, load_uploads(gh), slot_runs(gh, slot_gate.slot_start(now)))


def dispatch(gh, workflow: str, inputs: dict | None = None) -> None:
    body = {"ref": "main"}
    if inputs:
        body["inputs"] = inputs
    gh.post(f"actions/workflows/{workflow}/dispatches", body)


def watch(gh, started: datetime, clock, sleep, dry_run: bool = False, once: bool = False, log=print) -> None:
    deadline = started + RUN_FOR
    log(f"🫀 Nöbet başladı: {slot_gate._clock(started)}, yerini en geç {slot_gate._clock(deadline)} "
        f"yenisine bırakacak")
    try:
        _loop(gh, deadline, clock, sleep, dry_run, once, log)
    except Exception as e:  # the chain must outlive a bug; a cancel (KeyboardInterrupt) stops it on purpose
        log(f"⚠️ Nöbet beklenmedik bir hata verdi: {e!r}")
    if not (dry_run or once):
        hand_over(gh, sleep, log)


def _loop(gh, deadline: datetime, clock, sleep, dry_run: bool, once: bool, log) -> None:
    while True:
        now = clock()
        moment = now if once else next_check(now)
        if moment > deadline:
            break
        if moment > now:
            sleep((moment - now).total_seconds())
        now = clock()
        try:
            go, reasons = check(gh, now)
        except Exception as e:  # a missed check is retried at the next one
            go, reasons = False, [f"⚠️ Kontrol yapılamadı: {str(e)[:200]}"]
        log(f"🚦 Kontrol, şimdi {slot_gate._clock(now)}:")
        for reason in reasons:
            log(f"   {reason}")
        if go and dry_run:
            log("🟢 Short başlatılırdı (deneme, bir şey başlatılmadı)")
        elif go:
            try:
                dispatch(gh, SHORTS_WORKFLOW, SHORTS_INPUTS)
                log("🟢 Short iş akışı başlatıldı")
            except Exception as e:
                log(f"⚠️ Short iş akışı başlatılamadı: {str(e)[:200]}")
        else:
            log("⏸️ Bu kontrolde Short başlatılmıyor")
        if once:
            return


def hand_over(gh, sleep, log=print) -> bool:
    """Start the next watch; it waits in the concurrency group until this job ends."""
    for attempt in range(1, HANDOVER_ATTEMPTS + 1):
        try:
            dispatch(gh, WATCH_WORKFLOW)
            log("🫀 Sıradaki nöbet başlatıldı")
            return True
        except Exception as e:
            log(f"⚠️ Sıradaki nöbet başlatılamadı ({attempt}/{HANDOVER_ATTEMPTS}): {str(e)[:200]}")
            if attempt < HANDOVER_ATTEMPTS:
                sleep(30)
    log("⚠️ Nöbet zinciri koptu; bir sonraki Short çalışması onu yeniden başlatacak")
    return False


def ensure(gh, log=print) -> bool:
    """Start the watch when no run of it is queued or in progress. Returns True when it started one."""
    live = _live(_runs(gh, WATCH_WORKFLOW, "per_page=10"))
    if live:
        log(f"🫀 Nöbet çalışıyor ({live[0].get('id')}, {live[0].get('status')})")
        return False
    dispatch(gh, WATCH_WORKFLOW)
    log("🫀 Nöbet çalışmıyordu, yeniden başlatıldı")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Saatlik Short nöbeti")
    parser.add_argument("--once", action="store_true", help="Tek kontrol yap ve çık")
    parser.add_argument("--dry-run", action="store_true", help="Hiçbir iş akışını başlatma")
    parser.add_argument("--ensure", action="store_true", help="Nöbet çalışmıyorsa başlat ve çık")
    args = parser.parse_args(argv)
    try:
        gh = GitHub(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))
        if args.ensure:
            ensure(gh)
            return 0
    except Exception as e:  # never red: a skipped restart is retried by the next Shorts run
        print(f"⚠️ Nöbet kontrol edilemedi: {e!r}")
        return 0
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    watch(gh, clock(), clock, time.sleep, dry_run=args.dry_run, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
