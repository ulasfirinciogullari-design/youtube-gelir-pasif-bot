import importlib.util
from datetime import datetime, timedelta, timezone
import json
import re
import sys

import pytest

import original_factory

_scripts = original_factory.BASE_DIR / "scripts"
sys.path.insert(0, str(_scripts))
_spec = importlib.util.spec_from_file_location("slot_heartbeat", _scripts / "slot_heartbeat.py")
slot_heartbeat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slot_heartbeat)
slot_gate = slot_heartbeat.slot_gate


# The watch is checked on an hourly schedule so a check at any hour has a slot to fill.
@pytest.fixture(autouse=True)
def hourly_slots(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", tuple(range(24)))


SHORTS = "actions/workflows/viral_shorts_autopilot.yml/runs"
WATCH = "actions/workflows/slot_heartbeat.yml/runs"


def at(hhmm: str, day: int = 16) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(run_id: int, created: datetime, event: str = "workflow_dispatch", actor: str = slot_heartbeat.BOT,
        status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"id": run_id, "event": event, "created_at": stamp(created), "status": status,
            "conclusion": conclusion, "actor": {"login": actor}}


class FakeGitHub:
    def __init__(self, uploads=None, shorts=None, watches=None, steps=None, fail=None, comments=None):
        self.uploads = uploads or []
        self.shorts = shorts or []
        self.watches = watches or []
        self.comments = comments or []
        self.steps = steps or {}
        self.fail = fail or set()
        self.posts = []
        self.gets = []

    def get(self, path, raw=False):
        self.gets.append(path)
        if any(part in path for part in self.fail):
            raise OSError(f"boom {path}")
        if path == "contents/uploads.json?ref=channel-data":
            assert raw
            return json.dumps(self.uploads)
        if path.startswith(SHORTS):
            created = re.search(r"created=%3E%3D([0-9T:Z-]+)", path)
            runs = [r for r in self.shorts if not created or r["created_at"] >= created.group(1)]
            return {"workflow_runs": runs}
        if path.startswith(WATCH):
            return {"workflow_runs": self.watches}
        if path.startswith(f"actions/workflows/{slot_heartbeat.COMMENTS_WORKFLOW}/runs"):
            return {"workflow_runs": self.comments}
        match = re.fullmatch(r"actions/runs/(\d+)/jobs", path)
        if match:
            produced = self.steps.get(int(match.group(1)), "skipped")
            return {"jobs": [{"steps": [{"name": "🚦 Sıra Kontrolü", "conclusion": "success"},
                                        {"name": slot_gate.PRODUCE_STEP, "conclusion": produced}]}]}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body):
        if any(part in path for part in self.fail):
            raise OSError(f"boom {path}")
        self.posts.append((path, body))
        return {}


def upload(when: datetime, video_id: str = "v", privacy: str = "public") -> dict:
    return {"video_id": video_id, "topic_id": "mk-001", "uploaded_at": when.isoformat(timespec="seconds"),
            "privacy": privacy}


def test_checks_at_25_and_45_past_every_hour():
    assert slot_heartbeat.next_check(at("10:10")) == at("10:25")
    assert slot_heartbeat.next_check(at("10:25")) == at("10:45")
    assert slot_heartbeat.next_check(at("10:50")) == at("11:25")
    assert slot_heartbeat.next_check(at("23:46")) == at("00:25", day=17)


def test_open_slot_goes_ahead():
    gh = FakeGitHub(uploads=[upload(at("09:26"))])
    go, reasons = slot_heartbeat.check(gh, at("10:25"))
    assert go, reasons


def test_a_queued_or_running_short_blocks_the_check():
    gh = FakeGitHub(uploads=[upload(at("09:26"))],
                    shorts=[run(5, at("10:20"), actor="ulas", status="in_progress", conclusion=None)])
    go, reasons = slot_heartbeat.check(gh, at("10:25"))
    assert not go
    assert "sürüyor" in reasons[0]
    assert not any(path.startswith("contents/") for path in gh.gets)


def test_filled_slot_and_minimum_gap_come_from_the_slot_gate():
    assert not slot_heartbeat.check(FakeGitHub(uploads=[upload(at("10:21"))]), at("10:45"))[0]
    # A late previous Short at 10:55 keeps the 11:25 check waiting until 35 min have passed.
    assert not slot_heartbeat.check(FakeGitHub(uploads=[upload(at("10:55"))]), at("11:25"))[0]


def test_own_dispatch_that_uploaded_fills_the_slot_even_without_its_record():
    gh = FakeGitHub(uploads=[upload(at("09:26"))], shorts=[run(7, at("10:25"))], steps={7: "success"})
    go, reasons = slot_heartbeat.check(gh, at("10:45"))
    assert not go
    assert any("zaten yükledi" in r for r in reasons)


def test_two_failed_own_dispatches_close_the_slot_but_one_is_retried():
    one = FakeGitHub(uploads=[upload(at("09:26"))], shorts=[run(7, at("10:25"), conclusion="failure")])
    assert slot_heartbeat.check(one, at("10:45"))[0]
    two = FakeGitHub(uploads=[upload(at("09:26"))],
                     shorts=[run(8, at("10:45"), conclusion="failure"), run(7, at("10:25"), conclusion="failure")])
    assert not slot_heartbeat.check(two, at("10:55"))[0]


def test_hand_dispatches_are_not_counted_as_ticks():
    # Ulaş's own test run in the slot neither fills it nor counts as a failure.
    gh = FakeGitHub(uploads=[upload(at("09:26"))],
                    shorts=[run(9, at("10:05"), actor="ulas", conclusion="failure"),
                            run(10, at("10:10"), actor="ulas", conclusion="failure")], steps={9: "success"})
    assert slot_heartbeat.check(gh, at("10:25"))[0]


def test_unreadable_uploads_means_no_short():
    gh = FakeGitHub(fail={"contents/uploads.json"})
    log = []
    slot_heartbeat.watch(gh, at("10:25"), lambda: at("10:25"), lambda s: None, once=True, log=log.append)
    assert gh.posts == []
    assert any("Kontrol yapılamadı" in line for line in log)


class Clock:
    def __init__(self, start: datetime):
        self.now = start
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def test_watch_starts_shorts_then_hands_over_before_the_job_limit():
    clock = Clock(at("10:20"))
    gh = FakeGitHub(uploads=[upload(at("09:26"))])
    started = []

    def post(path, body):
        gh.posts.append((path, body))
        if "viral_shorts_autopilot" in path:  # the Short lands a few minutes later
            started.append(clock.now)
            gh.uploads.append(upload(clock.now + timedelta(minutes=6), f"v{len(started)}"))

    gh.post = post
    slot_heartbeat.watch(gh, clock.now, clock, clock.sleep, log=lambda line: None)
    shorts = [body for path, body in gh.posts if "viral_shorts_autopilot" in path]
    assert shorts and all(body == {"ref": "main", "inputs": slot_heartbeat.SHORTS_INPUTS} for body in shorts)
    assert started == [at("10:25"), at("11:25"), at("12:25"), at("13:25"), at("14:25"), at("15:25")]
    assert gh.posts[-1] == ("actions/workflows/slot_heartbeat.yml/dispatches", {"ref": "main"})
    assert clock.now <= at("10:20") + slot_heartbeat.RUN_FOR


def _broken(error):
    def next_check(now):
        raise error
    return next_check


def test_hand_over_survives_a_bug_and_retries(monkeypatch):
    gh = FakeGitHub()
    attempts = []

    def flaky(path, body):
        attempts.append(path)
        if len(attempts) < 2:
            raise OSError("502")
        gh.posts.append((path, body))

    gh.post = flaky
    monkeypatch.setattr(slot_heartbeat, "next_check", _broken(RuntimeError("bug")))
    slot_heartbeat.watch(gh, at("10:20"), lambda: at("10:20"), lambda s: None, log=lambda line: None)
    assert len(attempts) == 2
    assert gh.posts == [("actions/workflows/slot_heartbeat.yml/dispatches", {"ref": "main"})]


def test_a_cancelled_watch_does_not_restart_itself(monkeypatch):
    # Cancelling a run sends SIGINT; that is a person stopping the chain, not a crash to recover from.
    gh = FakeGitHub()
    monkeypatch.setattr(slot_heartbeat, "next_check", _broken(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        slot_heartbeat.watch(gh, at("10:20"), lambda: at("10:20"), lambda s: None, log=lambda line: None)
    assert gh.posts == []


def test_dry_run_starts_nothing():
    gh = FakeGitHub(uploads=[upload(at("09:26"))])
    log = []
    slot_heartbeat.watch(gh, at("10:25"), lambda: at("10:25"), lambda s: None, dry_run=True, once=True,
                         log=log.append)
    assert gh.posts == []
    assert any("deneme" in line for line in log)


def test_ensure_restarts_only_a_stopped_watch():
    running = FakeGitHub(watches=[run(1, at("08:00"), status="in_progress", conclusion=None)])
    assert not slot_heartbeat.ensure(running, log=lambda line: None)
    assert running.posts == []
    stopped = FakeGitHub(watches=[run(1, at("02:00"), conclusion="success")])
    assert slot_heartbeat.ensure(stopped, log=lambda line: None)
    assert stopped.posts == [("actions/workflows/slot_heartbeat.yml/dispatches", {"ref": "main"})]


def test_ensure_never_fails_the_shorts_job(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert slot_heartbeat.main(["--ensure"]) == 0
    assert "Nöbet kontrol edilemedi" in capsys.readouterr().out


def test_workflow_keeps_one_watch_with_write_access_and_time_to_hand_over():
    workflow = (original_factory.BASE_DIR / ".github" / "workflows" / slot_heartbeat.WATCH_WORKFLOW).read_text(
        encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert re.search(r"concurrency:\n  group: slot-heartbeat\n  cancel-in-progress: false", workflow)
    assert "actions: write" in workflow and "contents: read" in workflow
    timeout = int(re.search(r"timeout-minutes: (\d+)", workflow).group(1))
    # The last check can come up to an hour before RUN_FOR ends; the job must outlive RUN_FOR and stay under 6 h.
    assert slot_heartbeat.RUN_FOR + timedelta(minutes=10) <= timedelta(minutes=timeout) <= timedelta(hours=6)
    assert "python3 scripts/slot_heartbeat.py" in workflow


def test_shorts_workflow_treats_watch_runs_like_cron_ticks():
    # Gated again when the job starts (a late cron run may have filled the slot meanwhile) and published with
    # the repository's PUBLISH_PRIVACY and CONTENT_FORMAT instead of manual-run inputs.
    workflow = (original_factory.BASE_DIR / ".github" / "workflows" / slot_heartbeat.SHORTS_WORKFLOW).read_text(
        encoding="utf-8")
    watch_run = f"github.actor == '{slot_heartbeat.BOT}'"
    env = dict(re.findall(r"^\s+([A-Z_]+): (\$\{\{.*\}\})", workflow, re.M))
    assert env["EVENT_NAME"] == ("${{ (github.event_name == 'schedule' || " + watch_run
                                 + ") && 'schedule' || github.event_name }}")
    assert env["PRIVACY"].startswith("${{ (github.event_name == 'schedule' || " + watch_run + ") && (vars.PUBLISH_PRIVACY")
    assert env["CONTENT_FORMAT"].startswith(f"${{{{ github.actor != '{slot_heartbeat.BOT}' && github.event.inputs.format")
    assert set(slot_heartbeat.SHORTS_INPUTS) == {"task"}


def test_watch_starts_the_comment_responder_once_an_hour_at_45():
    clock = Clock(at("10:20"))
    gh = FakeGitHub(uploads=[upload(at("09:26"))])
    slot_heartbeat.watch(gh, clock.now, clock, clock.sleep, log=lambda line: None)
    comments = [path for path, body in gh.posts if slot_heartbeat.COMMENTS_WORKFLOW in path]
    assert len(comments) == 6  # 10:45, 11:45, ... 15:45
    assert gh.posts[-1] == ("actions/workflows/slot_heartbeat.yml/dispatches", {"ref": "main"})


def test_comment_responder_is_not_started_twice_or_in_a_dry_run():
    quiet = lambda line: None  # noqa: E731
    busy = FakeGitHub(comments=[run(3, at("10:44"), status="in_progress", conclusion=None)])
    assert not slot_heartbeat.start_comments(busy, log=quiet)
    idle = FakeGitHub(comments=[run(3, at("09:45"))])
    assert not slot_heartbeat.start_comments(idle, dry_run=True, log=quiet)
    assert busy.posts == idle.posts == []
    assert slot_heartbeat.start_comments(idle, log=quiet)
    assert idle.posts == [("actions/workflows/comment_replies.yml/dispatches", {"ref": "main"})]
    assert not slot_heartbeat.start_comments(FakeGitHub(fail={"comment_replies"}), log=quiet)  # never raises
