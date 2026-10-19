import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess

import pytest

_path = Path(__file__).resolve().parents[1] / "scripts" / "compilation.py"
_spec = importlib.util.spec_from_file_location("compilation", _path)
compilation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compilation)

NOW = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg yok")


def upload(vid, hours_ago, privacy="public", title=None):
    return {"video_id": vid, "uploaded_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "privacy": privacy,
            "title": title or f"{vid} neden böyle? 🤯", "topic_id": f"mk-{vid}"}


def write(data_dir, uploads, compilations=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads.json").write_text(json.dumps(uploads), encoding="utf-8")
    if compilations is not None:
        (data_dir / "compilations.json").write_text(json.dumps(compilations), encoding="utf-8")


def test_candidates_are_the_weeks_public_shorts_not_in_a_published_compilation(tmp_path):
    write(tmp_path, [upload("a", 2), upload("b", 24 * 6), upload("old", 24 * 8), upload("priv", 3, "private"),
                     upload("used", 5), upload("sample", 6), {"video_id": "broken"}],
          [{"privacy": "public", "shorts": ["used"]}, {"privacy": "private", "shorts": ["sample"]}])
    assert [u["video_id"] for u in compilation.candidates(tmp_path, NOW)] == ["a", "sample", "b"]


def test_rank_puts_the_most_viewed_first_and_breaks_ties_by_the_newer_short():
    pool = [upload("a", 10), upload("b", 5), upload("c", 1)]
    ranked = compilation.rank(pool, {"a": 900, "b": 50, "c": 50})
    assert [u["video_id"] for u in ranked] == ["a", "c", "b"]


def test_chapters_start_at_zero_and_drop_emoji_and_hashtags():
    lines = compilation.chapters([("Filler Neden Asla Unutmaz? 🐘", 16.4), ("Kutup ayısı #shorts", 49.0),
                                  ("Son", 10)])
    assert lines == ["0:00 Filler Neden Asla Unutmaz?", "0:16 Kutup ayısı", "1:05 Son"]
    assert compilation.stamp(3725) == "1:02:05"


def test_title_names_the_count_and_day_and_fits_youtube():
    title = compilation.title_for(15, NOW)
    assert "15" in title and "25 Ekim" in title and len(title) <= 100


def test_description_lists_the_chapters():
    text = compilation.description_for(3, ["0:00 A", "0:16 B", "0:30 C"])
    assert "Bölümler:\n0:00 A\n0:16 B\n0:30 C\n" in text
    assert compilation.CHANNEL_SHORTS in text


def test_tags_skip_shorts_and_hashtags_dedupe_and_stay_under_the_limit():
    metas = [{"tags": ["shorts", "Merak", "fil", "#keşfet", "fil"]}, {"tags": ["x" * 40] * 3 + [f"t{i}" * 20
                                                                                          for i in range(30)]}]
    tags = compilation.tags_for(metas)
    assert tags[:len(compilation.FIXED_TAGS)] == compilation.FIXED_TAGS
    assert "shorts" not in tags and "#keşfet" not in tags and tags.count("fil") == 1 and "Merak" not in tags
    assert sum(len(t) + 1 for t in tags) <= compilation.TAGS_CHARS


def fake_short(folder: Path, name: str, seconds: float, freq: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=60:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-frames:v", "1",
                    str(folder / f"{name}_thumb.jpg")], check=True)
    return video


@needs_ffmpeg
def test_build_joins_the_most_viewed_shorts_best_first_and_skips_expired_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(compilation, "MIN_COUNT", 2)
    data = tmp_path / "data"
    write(data, [upload("a", 30, title="Arılar neden altıgen yapar? 🐝"), upload("b", 20, title="Baykuş 🦉"),
                 upload("gone", 10), upload("c", 5, title="Ceylan #shorts")])
    calls = []

    def find(upload_, out_dir, repo, token, extras=()):
        calls.append((upload_["video_id"], extras))
        if upload_["video_id"] == "gone":
            return None
        return fake_short(out_dir, f"ORIGINAL_{upload_['topic_id']}_1", 1.5, 440), {
            "title": upload_["title"], "tags": ["arı", "shorts"], "contains_synthetic_media": False}

    views = lambda ids, env: {"a": 10, "b": 500, "gone": 900, "c": 50}
    result = compilation.build(data, tmp_path / "out", NOW, count=2, env={}, find=find, views=views)
    assert [c[0] for c in calls] == ["gone", "b", "c"] and calls[0][1] == ("_thumb.jpg",)
    assert result["shorts"] == ["b", "c"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(result["video"])], capture_output=True, text=True, check=True).stdout.split()
    assert "1920,1080" in probe
    assert 2.8 < compilation.duration(result["video"]) < 3.6
    snippet = result["body"]["snippet"]
    assert "0:00 Baykuş\n0:01 Ceylan\n" in snippet["description"]
    assert snippet["title"].startswith("Haftanın En Çok İzlenen 2 ")
    assert "arı" in snippet["tags"] and "shorts" not in snippet["tags"]
    assert result["body"]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                        "containsSyntheticMedia": False}
    assert result["cover"].exists() and result["story"].exists()


def test_build_refuses_a_week_with_too_few_shorts(tmp_path):
    write(tmp_path, [upload("a", 2)])
    with pytest.raises(compilation.CompilationError, match="en az"):
        compilation.build(tmp_path, tmp_path / "out", NOW, env={}, find=lambda *a, **k: None,
                          views=lambda ids, env: {})


class FakeResponse(contextlib.AbstractContextManager):
    def __init__(self, body=b"", headers=None):
        self.body, self.headers = body, headers or {}

    def read(self):
        return self.body

    def __exit__(self, *exc):
        return False


def test_upload_is_resumable_then_sets_the_cover(tmp_path, monkeypatch):
    monkeypatch.setattr(compilation.comment_replies, "access_token", lambda env: "tok")
    video, cover = tmp_path / "v.mp4", tmp_path / "c.jpg"
    video.write_bytes(b"video-bytes")
    cover.write_bytes(b"jpeg")
    sent = []

    def send(url, data, headers, method="POST"):
        body = data.read() if hasattr(data, "read") else data
        sent.append((method, url, headers, body))
        if "uploadType=resumable" in url:
            return FakeResponse(headers={"Location": "https://upload.example/session"})
        if url == "https://upload.example/session":
            return FakeResponse(b'{"id": "VID123"}')
        raise compilation.CompilationError("YouTube 403: thumbnails not allowed")

    body = {"snippet": {"title": "t"}, "status": {"privacyStatus": "private"}}
    assert compilation.upload(video, cover, body, env={}, send=send) == "VID123"
    start, put, thumb = sent
    assert "part=snippet%2Cstatus" in start[1] and json.loads(start[3]) == body
    assert start[2]["X-Upload-Content-Length"] == str(len(b"video-bytes"))
    assert put[0] == "PUT" and put[3] == b"video-bytes" and put[2]["Authorization"] == "Bearer tok"
    assert thumb[1].endswith("thumbnails/set?videoId=VID123") and thumb[3] == b"jpeg"


def test_main_records_an_upload_and_keeps_the_storyboard(tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "out"
    out.mkdir()
    story = out / "DERLEME_20261025_story.jpg"
    story.write_bytes(b"jpg")
    body = {"snippet": {"title": "Başlık", "description": "d"}, "status": {"privacyStatus": "private"}}
    monkeypatch.setattr(compilation, "build", lambda *a, **k: {
        "video": out / "v.mp4", "cover": None, "story": story, "body": body, "shorts": ["a", "b"], "seconds": 75})
    privacies = []
    monkeypatch.setattr(compilation, "upload", lambda video, cover, b: privacies.append(b["status"]["privacyStatus"])
                        or "NEW1")
    assert compilation.main(["--data", str(data), "--out", str(out), "--privacy", "public"]) == 0
    assert privacies == ["public"]
    record = json.loads((data / "compilations.json").read_text(encoding="utf-8"))
    assert record[0]["video_id"] == "NEW1" and record[0]["shorts"] == ["a", "b"] and record[0]["privacy"] == "public"
    assert (data / "history" / story.name).read_bytes() == b"jpg"


def test_main_without_upload_records_nothing(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    body = {"snippet": {"title": "Başlık", "description": "d"}, "status": {"privacyStatus": "private"}}
    monkeypatch.setattr(compilation, "build", lambda *a, **k: {
        "video": tmp_path / "v.mp4", "cover": None, "story": None, "body": body, "shorts": ["a"], "seconds": 30})
    monkeypatch.setattr(compilation, "upload", lambda *a: pytest.fail("yüklememeli"))
    assert compilation.main(["--data", str(data), "--no-upload"]) == 0
    assert not (data / "compilations.json").exists()
    assert "Yükleme istenmedi" in capsys.readouterr().out
