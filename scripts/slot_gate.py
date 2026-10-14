"""
Slot gate for the scheduled Shorts runs.

GitHub drops or delays top-of-hour cron events: scheduled runs on main started
up to 3.6 h late, and on 2026-10-14 the 11:00 and 14:00 UTC slots never ran
while the 18:00 UTC slot had still not started at 19:00 UTC. The workflow
therefore ticks at :07 and :37 through each slot's 3 h window, and this script,
the first step after the channel-data checkout, decides whether a tick should
actually produce a Short. A dropped or failed run simply leaves its slot open
and the next tick tries again, so no separate retry loop is needed.

A scheduled tick goes ahead only when all of these hold:
  - the latest slot (05/10/14/18 UTC = 08/13/17/21 TR) started at most 3 h ago;
  - no public or unlisted upload has happened since that slot started;
  - the newest public or unlisted upload is at least 2.5 h old. Bursts kill
    reach: in channel_audit.json, Shorts with no upload in the previous 2 h had
    a median of 950 views, those with 6+ uploads a median of 10;
  - fewer than 5 uploads of any privacy happened since the last quota reset;
  - no scheduled run since the slot already got through the produce step (an
    upload whose uploads.json record was lost when the channel-data push failed);
  - fewer than 3 scheduled runs since the slot failed (a broken run is not
    retried every half hour until the next slot).

Private uploads (Ulaş's test runs) neither fill a slot nor delay the next one,
but they do spend quota. Manual runs (workflow_dispatch) always go ahead.

The gate runs on the runner's system Python before setup-python and pip, so it
uses the standard library only and a skipped tick costs seconds. It always
exits 0 because a gate bug must never turn the job red; on an unexpected error
it outputs go=false so nothing uploads twice.

  python scripts/slot_gate.py --now 2026-10-15T10:07:00Z   # dry run against the local channel-data
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import urllib.request

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DATA_DIR = BASE_DIR / "channel-data"
SLOTS_UTC = (5, 10, 14, 18)  # 08:00, 13:00, 17:00, 21:00 TR
WINDOW = timedelta(hours=3)
MIN_GAP = timedelta(hours=2, minutes=30)
# YouTube's daily quota resets at midnight Pacific time. An upload costs ~1,600 of the 10,000 daily
# units and Ulaş's PC uploader shares the quota, so 5 a day leaves room for it.
QUOTA_RESET_UTC = 7
MAX_PER_QUOTA_DAY = 5
MAX_FAILED_RUNS = 3
PRODUCE_STEP = "🎬 Özgün Short Üret ve Yayınla"
WORKFLOW_FILE = "viral_shorts_autopilot.yml"
API = "https://api.github.com"
TR_OFFSET = timedelta(hours=3)  # Turkey has no daylight saving time


def parse_time(value) -> datetime | None:
    """ISO 8601 to an aware UTC datetime; None when missing or unreadable."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def slot_start(now: datetime) -> datetime:
    """The latest slot start at or before now; before 05:00 UTC that is yesterday's 18:00."""
    now = _utc(now)
    today = now.replace(minute=0, second=0, microsecond=0)
    starts = [today.replace(hour=h) for h in SLOTS_UTC if today.replace(hour=h) <= now]
    return starts[-1] if starts else today.replace(hour=SLOTS_UTC[-1]) - timedelta(days=1)


def quota_day_start(now: datetime) -> datetime:
    now = _utc(now)
    reset = now.replace(hour=QUOTA_RESET_UTC, minute=0, second=0, microsecond=0)
    return reset if reset <= now else reset - timedelta(days=1)


def is_public(upload: dict) -> bool:
    # Records written before the privacy field existed count as public: the scheduled runs uploaded them.
    return upload.get("privacy") != "private"


def _clock(moment: datetime) -> str:
    return f"{moment:%d.%m %H:%M} UTC ({moment + TR_OFFSET:%H:%M} TR)"


def _duration(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} sa {minutes} dk" if hours else f"{minutes} dk"


def _steps(run: dict) -> list[dict]:
    # main() flattens the jobs into "steps"; a raw API-shaped run with "jobs" works as well.
    if run.get("steps"):
        return run["steps"]
    return [step for job in run.get("jobs") or [] for step in job.get("steps") or []]


def decide(now: datetime, uploads: list[dict], runs: list[dict] | None, run_id=None) -> tuple[bool, list[str]]:
    """Should a scheduled tick at `now` produce a Short? Returns (go, reasons in Turkish).

    uploads are channel-data/uploads.json records; runs are this workflow's runs (dicts with id, event,
    created_at, conclusion and steps), or None when the Actions API could not be read.
    """
    now = _utc(now)
    slot = slot_start(now)
    reasons = [f"Slot {_clock(slot)}, {_duration(now - slot)} önce başladı"]
    if now - slot > WINDOW:
        reasons.append(f"⛔ Slot pencere dışında ({_duration(WINDOW)} geçti); sıradaki slot beklenecek")
        return False, reasons

    go = True
    stamped = [(t, u) for u in uploads if isinstance(u, dict) and (t := parse_time(u.get("uploaded_at"))) is not None]
    public = sorted(((t, u) for t, u in stamped if is_public(u)), key=lambda pair: pair[0])
    in_slot = [u for t, u in public if t >= slot]
    if in_slot:
        go = False
        reasons.append(f"⛔ Slot dolu: {in_slot[-1].get('video_id') or '?'} bu slotta yüklendi")
    elif public:
        gap = now - public[-1][0]
        if gap < MIN_GAP:
            go = False
            reasons.append(f"⛔ Son herkese açık yükleme {_duration(gap)} önce; arada en az {_duration(MIN_GAP)} "
                           f"olmalı, sonraki turda yeniden denenecek")
        else:
            reasons.append(f"✅ Slot boş, son herkese açık yükleme {_duration(gap)} önce")
    else:
        reasons.append("✅ Slot boş, kayıtlı herkese açık yükleme yok")

    reset = quota_day_start(now)
    used = sum(1 for t, _ in stamped if t >= reset)
    if used >= MAX_PER_QUOTA_DAY:
        go = False
        reasons.append(f"⛔ Kota: {_clock(reset)} sıfırlamasından beri {used} yükleme var (sınır {MAX_PER_QUOTA_DAY})")
    else:
        reasons.append(f"✅ Kota: sıfırlamadan beri {used}/{MAX_PER_QUOTA_DAY} yükleme")

    if runs is None:
        reasons.append("ℹ️ Actions geçmişi okunamadı; önceki çalışma kontrolleri atlandı, karar uploads.json'a göre")
        return go, reasons
    earlier = [r for r in runs
               if isinstance(r, dict) and r.get("event") == "schedule"
               and (run_id is None or str(r.get("id")) != str(run_id))
               and (t := parse_time(r.get("created_at"))) is not None and t >= slot]
    uploaded = [r for r in earlier
                if any(s.get("name") == PRODUCE_STEP and s.get("conclusion") == "success" for s in _steps(r))]
    failed = [r for r in earlier if r.get("conclusion") == "failure"]
    if uploaded:
        go = False
        reasons.append(f"⛔ Bu slottaki {uploaded[0].get('id')} numaralı çalışma zaten yükledi "
                       f"(uploads.json kaydı kanal hafızasına gönderilememiş olabilir)")
    if len(failed) >= MAX_FAILED_RUNS:
        go = False
        reasons.append(f"⛔ Bu slotta {len(failed)} çalışma hata verdi; bozuk çalışma sıradaki slota kadar denenmeyecek")
    if not uploaded and len(failed) < MAX_FAILED_RUNS:
        reasons.append(f"✅ Actions: bu slotta {len(earlier)} önceki zamanlanmış çalışma, {len(failed)} hatalı")
    return go, reasons


def load_uploads(data_dir: Path) -> list[dict]:
    # channel_memory needs only the standard library today; reading the file directly keeps the gate
    # working before pip install even if that module ever gains a third-party import.
    try:
        from app.services.channel_memory import load_uploads as load
    except Exception:
        load = None
    if load is not None:
        return load(data_dir)
    path = Path(data_dir) / "uploads.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [u for u in data if isinstance(u, dict)] if isinstance(data, list) else []


def _api_get(url: str, token: str, timeout: float = 20.0) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "zirve-slot-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_runs(repo: str, token: str, slot: datetime, run_id=None) -> list[dict]:
    """This workflow's scheduled runs created since the slot, each with its steps' conclusions."""
    if not repo:
        raise ValueError("GITHUB_REPOSITORY tanımlı değil")
    since = _utc(slot).strftime("%Y-%m-%dT%H:%M:%SZ")
    listing = _api_get(f"{API}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
                       f"?event=schedule&created=%3E%3D{since}&per_page=20", token)
    runs = []
    for run in listing.get("workflow_runs") or []:
        entry = {"id": run.get("id"), "event": run.get("event"), "created_at": run.get("created_at"),
                 "conclusion": run.get("conclusion"), "steps": []}
        if run_id is None or str(run.get("id")) != str(run_id):  # this run's own steps are still running
            jobs = _api_get(f"{API}/repos/{repo}/actions/runs/{run.get('id')}/jobs", token)
            entry["steps"] = [{"name": s.get("name"), "conclusion": s.get("conclusion")}
                              for job in jobs.get("jobs") or [] for s in job.get("steps") or []]
        runs.append(entry)
    return runs


def _gate(args) -> bool:
    # A --now without an event is a local dry run of a scheduled tick.
    event = os.environ.get("EVENT_NAME") or os.environ.get("GITHUB_EVENT_NAME") or ("schedule" if args.now else "")
    if event != "schedule":
        print(f"🚦 Tetikleyici '{event or 'yerel'}': sıra kontrolü sadece zamanlanmış çalışmalar için, devam")
        return True
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise ValueError(f"--now okunamadı: {args.now}")
    uploads = load_uploads(Path(args.data_dir))
    run_id = os.environ.get("GITHUB_RUN_ID") or None
    try:
        runs = fetch_runs(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""),
                          slot_start(now), run_id)
    except Exception as e:  # the uploads.json checks still stand on their own
        print(f"ℹ️ Actions geçmişi okunamadı: {str(e)[:200]}")
        runs = None
    go, reasons = decide(now, uploads, runs, run_id)
    print(f"🚦 Sıra kontrolü, şimdi {_clock(now)}:")
    for reason in reasons:
        print(f"   {reason}")
    print("🟢 Short üretilecek" if go else "⏸️ Bu tur atlanıyor")
    return go


def write_output(go: bool) -> None:
    line = f"go={'true' if go else 'false'}"
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Zamanlanmış Shorts çalışması için sıra kontrolü")
    parser.add_argument("--now", default=None, help="Deneme zamanı (ISO 8601, ör. 2026-10-15T10:07:00Z)")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="uploads.json'un bulunduğu klasör")
    args = parser.parse_args(argv)
    try:
        go = _gate(args)
    except Exception as e:  # never red, and never a second upload for the same slot
        print(f"⚠️ Sıra kontrolü beklenmedik bir hata verdi, çift yükleme olmasın diye bu tur atlanıyor: {e!r}")
        go = False
    try:
        write_output(go)
    except Exception as e:  # without an output every later step is skipped, which is the safe side
        print(f"⚠️ Karar GITHUB_OUTPUT'a yazılamadı: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
