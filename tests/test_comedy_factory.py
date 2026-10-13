import json
import shutil
import subprocess

import pytest

import comedy_factory
from app.services import comedy_dub
from app.services.comedy_dub import ClipUnusable, ScriptError, validate_comedy_script, write_comedy_script

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

VALID = {
    "usable": True,
    "title": "Misafir sevmeyen kedinin iç sesi 😂 #shorts",
    "hook_text": "Sonuna kadar izle",
    "lines": [
        {"start": 3.0, "speaker": "Kedi", "text": "Yine mi misafir geldi bu eve? 🙄"},
        {"start": 0.4, "speaker": "kedi", "text": "Bu koltuk benim, kimse oturmasın."},
        {"start": 5.5, "speaker": "köpek", "text": "Ben kapıyı açmadım, yemin ederim."},
    ],
    "description": "Kedinin misafir günü.",
    "tags": ["kedi", "komik"],
    "pinned_comment": "Senin kedin de böyle mi?",
    "en_title": "The cat that hates guests",
    "en_description": "A cat's inner voice on guest day.",
}


def test_validate_comedy_script_sorts_clamps_and_cleans():
    script = validate_comedy_script(json.loads(json.dumps(VALID)), duration=12.0)
    assert script["title"] == "Misafir sevmeyen kedinin iç sesi 😂"
    assert [line["start"] for line in script["lines"]] == [0.4, 3.0, 5.5]
    assert script["lines"][1]["speaker"] == "kedi" and "🙄" not in script["lines"][1]["text"]
    assert script["hook_text"] == "Sonuna kadar izle"

    raw = json.loads(json.dumps(VALID))
    raw["lines"][2]["start"] = 30
    assert validate_comedy_script(raw, duration=12.0)["lines"][-1]["start"] == 11.5


def test_validate_comedy_script_rejections():
    raw = json.loads(json.dumps(VALID))
    raw["usable"] = False
    with pytest.raises(ClipUnusable):
        validate_comedy_script(raw, 12.0)

    raw = json.loads(json.dumps(VALID))
    raw["lines"] = raw["lines"][:1]
    with pytest.raises(ScriptError, match="line count"):
        validate_comedy_script(raw, 12.0)

    raw = json.loads(json.dumps(VALID))
    raw["lines"][0]["text"] = "Thomas Shelby gibi bakıyorum şu an."
    with pytest.raises(ScriptError, match="shelby"):
        validate_comedy_script(raw, 12.0)


def test_script_must_fit_the_clip():
    raw = json.loads(json.dumps(VALID))
    raw["lines"].append({"start": 6.0, "speaker": "kedi", "text": "Bir de üstüne mama kabını boş bırakmışlar, ben bu evde nasıl yaşayayım."})
    validate_comedy_script(json.loads(json.dumps(raw)), 20.0)
    with pytest.raises(ScriptError, match="do not fit"):
        validate_comedy_script(raw, 12.0)
    prompt = comedy_dub.build_prompt(12.0)
    assert "toplam en fazla 24 kelime" in prompt and "2-4 replik" in prompt and "9.0. saniyede" in prompt


def test_write_comedy_script_retries_then_gives_up(tmp_path):
    preview = tmp_path / "p.mp4"
    preview.write_bytes(b"x")
    replies = iter(["not json", json.dumps(VALID, ensure_ascii=False)])
    assert write_comedy_script(preview, 12.0, call=lambda p, v: next(replies))["lines"]
    with pytest.raises(ScriptError):
        write_comedy_script(preview, 12.0, call=lambda p, v: "nope")


def test_candidate_clips_skips_used_and_wrong_length():
    def search(query, per_page=50):
        return [
            {"id": 1, "duration": 12, "likes": 5},
            {"id": 2, "duration": 4, "likes": 50},
            {"id": 3, "duration": 20, "likes": 9},
            {"id": 4, "duration": 90, "likes": 99},
        ]

    picked = list(comedy_dub.candidate_clips({"kd-3"}, "seed", queries=["cat"], search=search))
    assert [(hit["id"], query) for hit, query in picked] == [(1, "cat")]


def test_place_lines_avoids_overlap():
    lines = [{"start": 0.5, "speaker": "a", "text": "x"}, {"start": 1.0, "speaker": "b", "text": "y"}]
    placed = comedy_dub.place_lines(lines, [2.0, 1.0])
    assert placed[0]["start"] == 0.5 and placed[0]["end"] == 2.5
    assert placed[1]["start"] == pytest.approx(2.62) and placed[1]["end"] == pytest.approx(3.62)


def test_main_falls_back_to_story_format(monkeypatch):
    calls = []
    monkeypatch.setattr(comedy_factory, "candidate_clips", lambda published, seed: iter([]))
    monkeypatch.setattr(comedy_factory.story, "main", lambda argv: calls.append(argv) or 0)
    assert comedy_factory.main(["--no-upload", "--privacy", "unlisted"]) == 0
    assert calls == [["--privacy", "unlisted", "--no-upload"]]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_produce_comedy_offline(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-f", "lavfi",
         "-i", "sine=frequency=300", "-t", "6", "-shortest", str(clip)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    voices = []

    def fake_tts(text, out, **context):
        voices.append(context["voice_id"])
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=180:duration={0.4 * len(text.split()):.2f}",
                        "-c:a", "libmp3lame", str(out)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out

    monkeypatch.setattr(comedy_factory, "OUTPUT_DIR", tmp_path)
    script = validate_comedy_script(json.loads(json.dumps(VALID)), 12.0)
    hit = {"id": 777, "user": "someone", "pageURL": "https://pixabay.com/videos/x-777/"}
    result = comedy_factory.produce_comedy(hit, "cat", script, clip, synthesize=fake_tts)

    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result["video"])], text=True))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920) and video["r_frame_rate"] == "60/1"
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    meta = result["meta"]
    assert meta["description"].endswith("Seri: kd-777") and "Pixabay / someone" in meta["description"]
    assert meta["category_id"] == "23" and meta["contains_synthetic_media"] is False
    # Two characters get two different voices; the cat keeps its voice across lines.
    assert voices[0] == voices[1] != voices[2]
    assert abs(float(probe["format"]["duration"]) - meta["duration_seconds"]) < 0.3
    assert meta["duration_seconds"] >= 6.0
