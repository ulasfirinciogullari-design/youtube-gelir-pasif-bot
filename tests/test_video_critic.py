import base64
import json
import shutil
import subprocess

import pytest

from app.services import video_critic
from app.services.original_script import ScriptError

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _shrunk(video, out):
    out.write_bytes(b"small-mp4")
    return out


def test_critic_compares_our_short_with_the_reference(tmp_path):
    video = tmp_path / "ORIGINAL_mk-028_1.mp4"
    video.write_bytes(b"full")
    work = tmp_path / "work"
    work.mkdir()
    reply = {"score": 14,
             "issues": [{"area": "ses", "problem": "Anlatıcı  çok yavaş", "fix": "Hızlandır"}, {"area": "yazı"}, "bozuk"]
             + [{"area": "görüntü", "problem": f"sorun {i}", "fix": "düzelt"} for i in range(10)],
             "strengths": ["Net görüntü", "  "]}
    sent = []
    result = video_critic.critique(video, work, call=lambda parts: sent.append(parts) or json.dumps(reply), shrink=_shrunk)
    # The reference goes first as a YouTube link, then our preview inline, then the instructions.
    assert sent[0][0] == {"fileData": {"fileUri": "https://www.youtube.com/watch?v=k951xfBlETg"}}
    assert sent[0][1]["inlineData"] == {"mimeType": "video/mp4", "data": base64.b64encode(b"small-mp4").decode()}
    assert "Sınırlarımız" in sent[0][2]["text"]
    # The preview stays in the work folder, out of the upload globs next to the video.
    assert (work / "ORIGINAL_mk-028_1_critic.mp4").exists() and not (tmp_path / "ORIGINAL_mk-028_1_critic.mp4").exists()
    assert result["score"] == 10 and len(result["issues"]) == 8 and result["strengths"] == ["Net görüntü"]
    assert result["issues"][0] == {"area": "ses", "problem": "Anlatıcı çok yavaş", "fix": "Hızlandır"}
    text = video_critic.format_critique(result)
    assert text.splitlines()[0] == "🎬 Eleştirmen puanı (referans k951xfBlETg): 10/10"
    assert "- [ses] Anlatıcı çok yavaş → Hızlandır" in text and text.endswith("+ Net görüntü")


def test_critic_never_stops_the_run(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    def rate_limited(parts):
        raise ScriptError("HTTP 429")

    def no_ffmpeg(video, out):
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    assert video_critic.critique(video, call=rate_limited, shrink=_shrunk) is None
    assert video_critic.critique(video, call=lambda p: '{"score": 5}', shrink=no_ffmpeg) is None
    for raw in (None, "metin", "{bozuk", '{"score": "iyi"}', "[1, 2]"):
        assert video_critic.critique(video, call=lambda p: raw, shrink=_shrunk) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_critic_preview_is_small_and_keeps_the_sound(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30", "-f", "lavfi",
                    "-i", "sine=frequency=300", "-t", "2", "-c:v", "libx264", "-c:a", "aac", str(video)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = video_critic.preview(video, tmp_path / "small.mp4")
    streams = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)], text=True))["streams"]
    picture = next(s for s in streams if s["codec_type"] == "video")
    assert (picture["width"], picture["height"]) == (360, 640) and any(s["codec_type"] == "audio" for s in streams)
    assert out.stat().st_size < video.stat().st_size
