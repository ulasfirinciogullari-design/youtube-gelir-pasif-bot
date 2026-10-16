import importlib.util
from datetime import datetime, timezone
import json
import re
import sys
import types

import pytest

import original_factory
from app.services import channel_memory

_spec = importlib.util.spec_from_file_location("slot_gate", original_factory.BASE_DIR / "scripts" / "slot_gate.py")
slot_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slot_gate)


# The gate's rules (gap, quota, failed runs) are checked on an hourly schedule so a tick at any hour has a slot;
# the tests about the schedule itself use the real one.
REAL_SLOTS = slot_gate.SLOTS_UTC


@pytest.fixture(autouse=True)
def hourly_slots(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", tuple(range(24)))


def at(hhmm: str, day: int = 14) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def upload(when: datetime, privacy: str | None = None, video_id: str = "v") -> dict:
    record = {"video_id": video_id, "topic_id": "mk-001", "uploaded_at": when.isoformat(timespec="seconds")}
    if privacy is not None:
        record["privacy"] = privacy
    return record


def run(run_id: int, created: datetime, conclusion: str | None = "success", produced: str = "skipped",
        event: str = "schedule") -> dict:
    return {"id": run_id, "event": event, "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conclusion": conclusion,
            "steps": [{"name": "🚦 Sıra Kontrolü", "conclusion": "success"},
                      {"name": slot_gate.PRODUCE_STEP, "conclusion": produced}]}


def test_empty_slot_goes_ahead_on_the_first_tick():
    go, reasons = slot_gate.decide(at("10:07"), [upload(at("05:37"))], [])
    assert go, reasons
    assert any("10:00 UTC (13:00 TR)" in r for r in reasons)


def test_filled_slot_waits_for_the_next_one():
    go, reasons = slot_gate.decide(at("10:37"), [upload(at("10:09"), "public", "abc")], [])
    assert not go
    assert any("Slot dolu" in r and "abc" in r for r in reasons)


def test_too_soon_after_the_last_public_upload():
    # A late 13:00 run uploaded at 13:50; the 14:00 slot waits until 35 min have passed.
    go, reasons = slot_gate.decide(at("14:07"), [upload(at("13:50"))], [])
    assert not go
    assert any("35 dk" in r for r in reasons)
    assert slot_gate.decide(at("14:37"), [upload(at("13:50"))], [])[0]
    # An on-time run of the previous slot leaves the next slot's first tick free.
    assert slot_gate.decide(at("14:07"), [upload(at("13:14"))], [])[0]


def test_eight_slots_every_two_hours_of_the_turkish_day(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", REAL_SLOTS)
    assert [(h + 3) % 24 for h in slot_gate.SLOTS_UTC] == [10, 12, 14, 16, 18, 20, 22, 0]
    assert slot_gate.slot_start(at("13:37")) == at("13:00")
    assert slot_gate.slot_start(at("14:20")) == at("13:00")
    assert slot_gate.slot_start(at("03:00")) == at("21:00", day=13)


def test_a_slot_older_than_the_window_is_given_up(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", REAL_SLOTS)
    go, reasons = slot_gate.decide(at("14:30"), [upload(at("11:10"))], [])
    assert not go
    assert any("pencere dışında" in r for r in reasons)


def test_no_short_between_the_slots_or_at_night(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", REAL_SLOTS)
    for tick in ("14:07", "14:37", "03:07"):
        go, reasons = slot_gate.decide(at(tick), [upload(at("21:12", day=13))], [])
        assert not go, (tick, reasons)
    assert slot_gate.decide(at("15:07"), [upload(at("13:12"))], [])[0]


def test_daily_quota_counts_every_privacy_since_the_reset():
    tests = [upload(at(f"{7 + m // 60:02d}:{m % 60:02d}"), "private") for m in range(5, 185, 6)]
    assert len(tests) == 30
    go, reasons = slot_gate.decide(at("10:07"), tests, [])
    assert not go
    assert any("Kota" in r and "30 yükleme" in r for r in reasons)
    assert slot_gate.decide(at("10:07"), tests[1:], [])[0]
    # Uploads before 07:00 UTC belong to the previous quota day.
    early = [upload(at(t), "private") for t in ("06:00", "06:20", "06:40")]
    assert slot_gate.decide(at("10:07"), early + tests[3:], [])[0]



def test_quota_day_follows_pacific_daylight_saving():
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)
    # Summer (PDT): the reset is at 07:00 UTC.
    assert slot_gate.quota_day_start(utc(2026, 10, 15, 7, 7)) == utc(2026, 10, 15, 7)
    assert slot_gate.quota_day_start(utc(2026, 10, 15, 6, 59)) == utc(2026, 10, 14, 7)
    # Winter (PST): the reset is at 08:00 UTC, so a 07:07 tick is still in the previous quota day.
    assert slot_gate.quota_day_start(utc(2026, 12, 1, 7, 7)) == utc(2026, 11, 30, 8)
    assert slot_gate.quota_day_start(utc(2026, 12, 1, 8, 0)) == utc(2026, 12, 1, 8)
    # The day daylight saving ends (2026-11-01) started at midnight PDT.
    assert slot_gate.quota_day_start(utc(2026, 11, 2, 7, 30)) == utc(2026, 11, 1, 7)
    winter = [upload(utc(2026, 11, 30, 8 + m // 60, m % 60), "private") for m in range(15, 15 + 29 * 30, 30)] + [
        upload(utc(2026, 12, 1, 7, 20), "private")]
    assert len(winter) == 30
    go, reasons = slot_gate.decide(utc(2026, 12, 1, 7, 37), winter, [])
    assert not go
    assert any(r.startswith("⛔ Kota") for r in reasons)
    assert slot_gate.decide(utc(2026, 12, 1, 7, 37), winter[1:], [])[0]


def test_quota_day_without_a_time_zone_database_assumes_the_winter_reset(monkeypatch):
    monkeypatch.setattr(slot_gate, "PACIFIC", None)
    assert slot_gate.quota_day_start(at("07:30", day=15)) == at("08:00", day=14)
    assert slot_gate.quota_day_start(at("08:00", day=15)) == at("08:00", day=15)

def test_private_upload_neither_fills_the_slot_nor_delays_it():
    go, reasons = slot_gate.decide(at("10:37"), [upload(at("05:20")), upload(at("10:15"), "private")], [])
    assert go, reasons
    # Unlisted counts as public, and so do records written before the privacy field existed.
    assert not slot_gate.decide(at("10:37"), [upload(at("10:15"), "unlisted")], [])[0]
    assert not slot_gate.decide(at("10:37"), [upload(at("10:15"))], [])[0]


def test_a_run_that_already_uploaded_in_this_slot_blocks_a_second_upload():
    # Its uploads.json record was lost when the channel-data push failed.
    runs = [run(101, at("10:07"), "failure", produced="success")]
    go, reasons = slot_gate.decide(at("10:37"), [upload(at("05:20"))], runs, run_id=102)
    assert not go
    assert any("101" in r for r in reasons)


def test_only_earlier_scheduled_runs_in_this_slot_count():
    runs = [
        run(90, at("05:07"), produced="success"),  # previous slot
        run(91, at("10:08"), produced="success", event="workflow_dispatch"),
        run(102, at("10:37"), None, produced="success"),  # this run itself
        run(101, at("10:07"), "success", produced="skipped"),  # gate said no
    ]
    go, reasons = slot_gate.decide(at("10:37"), [upload(at("05:20"))], runs, run_id="102")
    assert go, reasons


def test_two_failed_runs_stop_retrying_until_the_next_slot():
    # GitHub starts ticks late, so a third tick can still land inside the slot.
    failed = [run(100 + i, at(t), "failure", produced="failure") for i, t in enumerate(("10:07", "10:37"))]
    go, reasons = slot_gate.decide(at("10:52"), [], failed)
    assert not go
    assert any("2 çalışma hata verdi" in r for r in reasons)
    assert slot_gate.decide(at("10:52"), [], failed[:1])[0]
    assert slot_gate.decide(at("11:07"), [], failed)[0]


def test_without_the_actions_history_the_uploads_still_decide():
    go, reasons = slot_gate.decide(at("10:07"), [], None)
    assert go
    assert any("Actions geçmişi okunamadı" in r for r in reasons)
    assert not slot_gate.decide(at("10:37"), [upload(at("10:09"))], None)[0]


def test_unreadable_records_are_ignored():
    records = [{"video_id": "x"}, {"video_id": "y", "uploaded_at": "dün"}, "bozuk"]
    assert slot_gate.decide(at("10:07"), records, [])[0]


def test_fetch_runs_reads_this_workflows_scheduled_runs_and_their_steps(monkeypatch):
    calls = []
    jobs = {"jobs": [{"name": slot_gate.PRODUCE_STEP, "steps": [
        {"name": "📥 Repoyu Çek", "conclusion": "success"},
        {"name": slot_gate.PRODUCE_STEP, "conclusion": "success"}]}]}

    def fake_get(url, token, timeout=20.0):
        calls.append((url, token))
        if url.endswith("/jobs"):
            return jobs
        return {"workflow_runs": [
            {"id": 7, "event": "schedule", "created_at": "2026-10-14T10:07:12Z", "conclusion": "failure"},
            {"id": 8, "event": "schedule", "created_at": "2026-10-14T10:37:05Z", "conclusion": None}]}

    monkeypatch.setattr(slot_gate, "_api_get", fake_get)
    runs = slot_gate.fetch_runs("owner/repo", "tok", at("10:00"), run_id="8")
    assert calls[0] == ("https://api.github.com/repos/owner/repo/actions/workflows/viral_shorts_autopilot.yml/runs"
                        "?event=schedule&created=%3E%3D2026-10-14T10:00:00Z&per_page=20", "tok")
    assert [c[0] for c in calls[1:]] == ["https://api.github.com/repos/owner/repo/actions/runs/7/jobs"]
    assert runs[0]["steps"][1] == {"name": slot_gate.PRODUCE_STEP, "conclusion": "success"}
    assert runs[1]["steps"] == []
    assert not slot_gate.decide(at("10:37"), [], runs, run_id="8")[0]


def test_workflow_matches_the_gate(monkeypatch):
    monkeypatch.setattr(slot_gate, "SLOTS_UTC", REAL_SLOTS)
    # The lost-record guard matches the produce step by name and the windows assume the cron hours, so a rename
    # or a cron edit must fail here instead of silently switching the guard off.
    workflow = (original_factory.BASE_DIR / ".github" / "workflows" / slot_gate.WORKFLOW_FILE).read_text(encoding="utf-8")
    steps = workflow.split("\n      - name: ")[1:]
    names = [block.splitlines()[0].strip() for block in steps]
    assert slot_gate.PRODUCE_STEP in names
    gate = names.index("🚦 Sıra Kontrolü")
    assert "id: gate" in steps[gate] and "python3 scripts/slot_gate.py" in steps[gate]
    assert names[:gate] == ["📥 Repoyu Çek", "🧠 Kanal Hafızasını Getir"]
    # The watch restart runs on skipped ticks too: it is a few API calls and must never fail the job.
    watch = names.index("🫀 Nöbeti Kontrol Et")
    assert watch == gate + 1
    assert "continue-on-error: true" in steps[watch] and "scripts/slot_heartbeat.py --ensure" in steps[watch]
    for name, block in zip(names[watch + 1:], steps[watch + 1:]):
        assert "if: " in block and "steps.gate.outputs.go == 'true'" in block, name

    cron = re.search(r"cron: '([^']+)'", workflow).group(1).split()
    minutes = [int(m) for m in cron[0].split(",")]
    hours = set(range(24)) if cron[1] == "*" else set()
    for part in cron[1].split(",") if cron[1] != "*" else []:
        first, _, last = part.partition("-")
        hours.update(range(int(first), int(last or first) + 1))
    window_hours = int(slot_gate.WINDOW.total_seconds() // 3600)
    assert hours == {h + i for h in slot_gate.SLOTS_UTC for i in range(window_hours)}
    for hour in hours:
        for minute in minutes:
            tick = at(f"{hour:02d}:{minute:02d}")
            assert tick - slot_gate.slot_start(tick) <= slot_gate.WINDOW


def _clear_event(monkeypatch):
    for name in ("EVENT_NAME", "GITHUB_EVENT_NAME", "GITHUB_RUN_ID", "GITHUB_REPOSITORY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_manual_run_always_goes_ahead_without_reading_anything(tmp_path, monkeypatch):
    _clear_event(monkeypatch)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")

    def must_not_read(*_args):
        raise AssertionError("a manual run must not read uploads.json")

    monkeypatch.setattr(slot_gate, "load_uploads", must_not_read)
    monkeypatch.setattr(slot_gate, "fetch_runs", must_not_read)
    assert slot_gate.main([]) == 0
    assert out.read_text(encoding="utf-8") == "go=true\n"


def test_scheduled_tick_reads_uploads_json_and_survives_an_api_failure(tmp_path, monkeypatch, capsys):
    _clear_event(monkeypatch)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    def offline(*_args, **_kwargs):
        raise OSError("ağ yok")

    monkeypatch.setattr(slot_gate, "_api_get", offline)
    channel_memory.record_upload(tmp_path, "v1", "mk-001", "Kedi", "merak", privacy="public")
    data = channel_memory.load_uploads(tmp_path)
    data[0]["uploaded_at"] = "2026-10-14T10:12:00+00:00"
    (tmp_path / channel_memory.UPLOADS_FILE).write_text(json.dumps(data), encoding="utf-8")

    assert slot_gate.main(["--now", "2026-10-14T10:37:00Z", "--data-dir", str(tmp_path)]) == 0
    assert slot_gate.main(["--now", "2026-10-14T14:07:00Z", "--data-dir", str(tmp_path)]) == 0
    assert out.read_text(encoding="utf-8") == "go=false\ngo=true\n"
    assert "Actions geçmişi okunamadı" in capsys.readouterr().out


def test_a_gate_bug_skips_the_tick_but_never_fails_the_job(tmp_path, monkeypatch):
    _clear_event(monkeypatch)
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setattr(slot_gate, "decide", lambda *_a, **_k: 1 / 0)
    monkeypatch.setattr(slot_gate, "fetch_runs", lambda *_a, **_k: [])
    assert slot_gate.main(["--data-dir", str(tmp_path)]) == 0
    assert out.read_text(encoding="utf-8") == "go=false\n"


def test_upload_record_keeps_the_privacy_only_when_given(tmp_path):
    channel_memory.record_upload(tmp_path, "v1", "mk-001", "Kedi", "merak", privacy="private")
    channel_memory.record_upload(tmp_path, "v2", "kd-1", "Penguen", "comedy")
    records = {u["video_id"]: u for u in channel_memory.load_uploads(tmp_path)}
    assert records["v1"]["privacy"] == "private"
    assert "privacy" not in records["v2"]


class _Call:
    def __init__(self, result=None):
        self.result = result

    def execute(self):
        return self.result

    def next_chunk(self):
        return None, self.result


class _YouTube:
    def __init__(self):
        self.comments = []

    def videos(self):
        return types.SimpleNamespace(insert=lambda **_: _Call({"id": "vid1"}))

    def thumbnails(self):
        return types.SimpleNamespace(set=lambda **_: _Call())

    def commentThreads(self):
        return types.SimpleNamespace(insert=lambda **kw: self.comments.append(kw) or _Call())


def test_private_upload_skips_the_pinned_comment(tmp_path, monkeypatch, capsys):
    # Commenting on a private video returns 403 and still spends 50 quota units.
    http = types.ModuleType("googleapiclient.http")
    http.MediaFileUpload = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "googleapiclient", types.ModuleType("googleapiclient"))
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    result = {"video": tmp_path / "v.mp4", "thumbnail": tmp_path / "t.jpg", "meta": {
        "title": "Başlık", "description": "Açıklama", "tags": [], "contains_synthetic_media": False,
        "pinned_comment": "Sen ne düşünüyorsun?"}}

    private = _YouTube()
    assert original_factory.upload(private, result, "private") == "vid1"
    assert private.comments == []
    assert "yorum atlandı" in capsys.readouterr().out

    public = _YouTube()
    original_factory.upload(public, result, "public")
    assert len(public.comments) == 1
