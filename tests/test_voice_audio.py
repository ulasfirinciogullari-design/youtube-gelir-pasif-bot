import importlib.util
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

import original_factory
from app.services import elevenlabs_engine as eleven

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
VOICE_VARIABLES = ("ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL", "ELEVENLABS_STABILITY", "ELEVENLABS_STYLE",
                   "ELEVENLABS_SPEED")


@pytest.fixture
def no_voice_variables(monkeypatch):
    for name in VOICE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Every request synthesize_elevenlabs_speech sends, answered with a fake mp3 and never with the network."""
    requests = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ID3" + b"x" * 2000

    def fake_urlopen(req, timeout=None):
        requests.append(req)
        return Reply()

    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "k" * 20)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def _payload(req) -> dict:
    return json.loads(req.data.decode("utf-8"))


def test_unset_variables_send_exactly_todays_request(tmp_path, sent, no_voice_variables, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_SPEED", "")  # an empty repo Variable counts as unset
    assert eleven.synthesize_elevenlabs_speech("Bu baykuş %90'ı döner.", tmp_path / "a.mp3",
                                               previous_text="önce", next_text="sonra")
    req = sent[0]
    assert req.full_url == "https://api.elevenlabs.io/v1/text-to-speech/HV5rAif1q1ITHdvxqyMw"
    assert req.data == json.dumps({
        "text": "Bu baykuş yüzde doksanı döner.",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.32, "similarity_boost": 0.75, "style": 0.45, "use_speaker_boost": True},
        "previous_text": "önce",
        "next_text": "sonra",
    }).encode("utf-8")
    assert dict(req.header_items()) == {"Xi-api-key": "k" * 20, "Content-type": "application/json",
                                        "Accept": "audio/mpeg", "User-agent": "Antigravity/1.0"}
    assert (tmp_path / "a.mp3").read_bytes().startswith(b"ID3")


def test_model_and_settings_come_from_repo_variables(tmp_path, sent, no_voice_variables, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.15")
    monkeypatch.setenv("ELEVENLABS_STYLE", "0,6")  # written the Turkish way
    eleven.synthesize_elevenlabs_speech("orta", tmp_path / "a.mp3", previous_text="önce", next_text="sonra")
    payload = _payload(sent[-1])
    assert payload["model_id"] == "eleven_turbo_v2_5"
    assert payload["voice_settings"] == {"stability": 0.32, "similarity_boost": 0.75, "style": 0.6,
                                         "use_speaker_boost": True, "speed": 1.15}
    assert payload["previous_text"] == "önce" and payload["next_text"] == "sonra"  # turbo reads the neighbours
    # Speed stays inside what ElevenLabs accepts, and a value that is not a number is ignored.
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.5")
    monkeypatch.setenv("ELEVENLABS_STABILITY", "çok")
    eleven.synthesize_elevenlabs_speech("orta", tmp_path / "a.mp3")
    settings = _payload(sent[-1])["voice_settings"]
    assert settings["speed"] == 1.2 and settings["stability"] == 0.32
    monkeypatch.setenv("ELEVENLABS_SPEED", "0.2")
    eleven.synthesize_elevenlabs_speech("orta", tmp_path / "a.mp3")
    assert _payload(sent[-1])["voice_settings"]["speed"] == 0.7


def test_eleven_v3_gets_no_neighbouring_lines(tmp_path, sent, no_voice_variables, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_MODEL", "eleven_v3")
    eleven.synthesize_elevenlabs_speech("[excited] Bu baykuş döner.", tmp_path / "a.mp3",
                                        previous_text="önce", next_text="sonra")
    payload = _payload(sent[-1])
    assert payload["model_id"] == "eleven_v3" and payload["text"].startswith("[excited] ")
    assert "previous_text" not in payload and "next_text" not in payload
    assert payload["voice_settings"]["stability"] == 0.5  # v3 takes 0.0 / 0.5 / 1.0 only
    assert "speed" not in payload["voice_settings"]


def test_explicit_arguments_override_repo_variables(tmp_path, sent, no_voice_variables, monkeypatch):
    for name, value in (("ELEVENLABS_MODEL", "eleven_v3"), ("ELEVENLABS_SPEED", "1.2"),
                        ("ELEVENLABS_STYLE", "0.9"), ("ELEVENLABS_STABILITY", "1.0")):
        monkeypatch.setenv(name, value)
    eleven.synthesize_elevenlabs_speech("orta", tmp_path / "a.mp3", voice_id="QftNgZhlSOYKrYbk7WR7",
                                        model_id="eleven_multilingual_v2", previous_text="önce",
                                        stability=0.35, style=0.6, speed=0.9)
    payload = _payload(sent[-1])
    assert sent[-1].full_url.endswith("/text-to-speech/QftNgZhlSOYKrYbk7WR7")
    assert payload["model_id"] == "eleven_multilingual_v2" and payload["previous_text"] == "önce"
    assert payload["voice_settings"] == {"stability": 0.35, "similarity_boost": 0.75, "style": 0.6,
                                         "use_speaker_boost": True, "speed": 0.9}


def test_a_refused_request_is_remembered(tmp_path, monkeypatch, no_voice_variables):
    import io
    import urllib.error

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {},
                                     io.BytesIO(b'{"detail": {"status": "voice_not_found"}}'))

    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "k" * 20)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert eleven.synthesize_elevenlabs_speech("orta", tmp_path / "a.mp3", voice_id="yok") is False
    assert eleven.last_error["code"] == 404 and "voice_not_found" in eleven.last_error["message"]


def test_synthesize_line_takes_the_voice_from_repo_variables(tmp_path, monkeypatch, no_voice_variables):
    voices = []

    def fake_eleven(text, out, voice_id=None, previous_text="", next_text=""):
        voices.append(voice_id)
        out.write_bytes(b"x" * 2000)
        return True

    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "key")
    monkeypatch.setattr(eleven, "synthesize_elevenlabs_speech", fake_eleven)
    monkeypatch.setitem(original_factory.voice_usage, "elevenlabs", 0)
    original_factory.synthesize_line("orta", tmp_path / "a.mp3")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", " QftNgZhlSOYKrYbk7WR7 ")
    original_factory.synthesize_line("orta", tmp_path / "b.mp3")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "")
    original_factory.synthesize_line("orta", tmp_path / "c.mp3")
    assert voices == ["HV5rAif1q1ITHdvxqyMw", "QftNgZhlSOYKrYbk7WR7", "HV5rAif1q1ITHdvxqyMw"]


def test_edge_only_lines_never_ask_elevenlabs(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("ElevenLabs must not be called")

    async def fake_edge(text, out, voice=None):
        out.write_bytes(b"x" * 2000)

    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "key")
    monkeypatch.setattr(eleven, "synthesize_elevenlabs_speech", refuse)
    monkeypatch.setattr(original_factory, "_edge_tts", fake_edge)
    monkeypatch.setattr(original_factory, "voice_usage", {"elevenlabs": 0, "edge-tts": 0})
    assert original_factory.synthesize_line("orta", tmp_path / "a.mp3", edge_only=True).exists()
    assert original_factory.voice_usage == {"elevenlabs": 0, "edge-tts": 1}


class _Seo(dict):
    def __missing__(self, key):
        return ""


@pytest.fixture
def light_produce(tmp_path, monkeypatch):
    """produce() with its narration recorded and everything after it stubbed out (no ffmpeg, no network)."""
    seen = {}

    def fake_build_narration(scenes, work_dir, synthesize=None, tempo=1.0):
        seen.setdefault("tempo", []).append(tempo)
        for idx, scene in enumerate(scenes):
            synthesize(scene["text"], work_dir / f"line_{idx:02d}.mp3")
        return work_dir / "voice.m4a", [2.0] * len(scenes), [{"text": "bir", "start": 0.0, "end": 2.0}]

    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(original_factory, "build_narration", fake_build_narration)
    monkeypatch.setattr(original_factory, "build_visual_track", lambda *a, **k: {
        "segments": [], "thumbnail": None, "ai_images": 0, "stock_videos": 2, "durations": None})
    monkeypatch.setattr(original_factory, "pick_music", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "mix_soundtrack", lambda *a, **k: tmp_path / "soundtrack.m4a")
    monkeypatch.setattr(original_factory, "render_final", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "build_seo_pack", lambda *a, **k: _Seo(
        description="açıklama", tags=["a"], localizations={}, seo_engine="test"))
    monkeypatch.setattr(original_factory.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "voice_usage", {"elevenlabs": 0, "edge-tts": 0})
    for name in ("GEMINI_API_KEY", "KEEP_AUDIO", *VOICE_VARIABLES):
        monkeypatch.delenv(name, raising=False)
    script = {"title": "Baykuş", "hook_text": "BOYNU NEDEN KIRILMIYOR?", "pinned_comment": "?",
              "scenes": [{"text": "Bu baykuş boynunu kırmadan 270 derece döner.", "callout": "270 DERECE"},
                         {"text": "Bizim boynumuzda 7 omur var."}]}

    def run(synthesize=lambda text, out, **kw: out):
        return original_factory.produce({"id": "mk-028", "topic": "baykuş"}, script, "test",
                                        synthesize=synthesize, series="merak")

    return seen, run


def test_native_speed_replaces_the_ffmpeg_speed_up(light_produce, monkeypatch):
    seen, run = light_produce
    run()
    assert seen["tempo"] == [original_factory.SERIES["merak"]["tempo"]] == [1.1]
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.15")
    run()
    assert seen["tempo"][-1] == 1.0  # not sped up twice
    assert original_factory.narration_tempo(1.1) == 1.0
    monkeypatch.setenv("ELEVENLABS_SPEED", "")
    assert original_factory.narration_tempo(1.1) == 1.1


def test_meta_records_the_voice_right_after_the_engine(light_produce, monkeypatch):
    seen, run = light_produce

    def eleven_line(text, out, **kw):
        original_factory.voice_usage["elevenlabs"] += 1
        return out

    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "QftNgZhlSOYKrYbk7WR7")
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.15")
    monkeypatch.setenv("ELEVENLABS_STABILITY", "0.35")
    meta = run(eleven_line)["meta"]
    keys = list(meta)
    assert keys[keys.index("voice_engine") + 1] == "voice"
    assert meta["voice_engine"] == "elevenlabs x2"
    assert meta["voice"] == {"voice_id": "QftNgZhlSOYKrYbk7WR7", "model": "eleven_multilingual_v2",
                             "speed": 1.15, "stability": 0.35}
    assert list(meta["voice"]) == ["voice_id", "model", "speed", "stability"]
    # Nothing set: Alp on today's model, no settings listed; the engine count is this Short's only.
    for name in VOICE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    meta = run(eleven_line)["meta"]
    assert meta["voice"] == {"voice_id": "HV5rAif1q1ITHdvxqyMw", "model": "eleven_multilingual_v2"}
    assert meta["voice_engine"] == "elevenlabs x2"
    assert original_factory.voice_meta({"elevenlabs": 0, "edge-tts": 2}) == {"voice_id": "tr-TR-AhmetNeural",
                                                                             "model": "edge-tts"}


def test_a_short_never_switches_narrator_mid_video(light_produce, monkeypatch):
    seen, run = light_produce
    calls = []

    def credits_run_out(text, out, edge_only=False, **kw):
        # ElevenLabs voices the first line, then the credits run out and the next line falls back to edge-tts.
        engine = "edge-tts" if edge_only or calls.count("elevenlabs") >= 1 else "elevenlabs"
        calls.append(engine if not edge_only else "edge-only")
        original_factory.voice_usage[engine] += 1
        return out

    result = run(credits_run_out)
    assert calls == ["elevenlabs", "edge-tts", "edge-only", "edge-only"]
    assert seen["tempo"] == [1.1, 1.1]
    meta = result["meta"]
    assert meta["voice_engine"] == "edge-tts x2"
    assert meta["voice"] == {"voice_id": "tr-TR-AhmetNeural", "model": "edge-tts"}
    # With ElevenLabs' own speed the first pass is not sped up; the edge-tts pass gets the series tempo back.
    calls.clear()
    monkeypatch.setenv("ELEVENLABS_SPEED", "1.15")
    run(credits_run_out)
    assert seen["tempo"][-2:] == [1.0, 1.1]


def test_an_all_edge_tts_short_keeps_the_series_speed(light_produce, monkeypatch):
    # ELEVENLABS_SPEED is set, so the first pass leaves the speed-up to ElevenLabs, but the credits are gone before
    # the first line: the edge-tts narration must not play at 1.0x.
    seen, run = light_produce
    calls = []

    def no_credits(text, out, edge_only=False, **kw):
        calls.append("edge-only" if edge_only else "edge-tts")
        original_factory.voice_usage["edge-tts"] += 1
        return out

    monkeypatch.setenv("ELEVENLABS_SPEED", "1.15")
    meta = run(no_credits)["meta"]
    assert seen["tempo"] == [1.0, 1.1]
    assert calls == ["edge-tts", "edge-tts", "edge-only", "edge-only"]
    assert meta["voice_engine"] == "edge-tts x2"
    assert meta["voice"] == {"voice_id": "tr-TR-AhmetNeural", "model": "edge-tts"}
    # Without ELEVENLABS_SPEED the first pass already has the series speed: no second pass.
    calls.clear()
    monkeypatch.delenv("ELEVENLABS_SPEED")
    run(no_credits)
    assert seen["tempo"] == [1.0, 1.1, 1.1] and calls == ["edge-tts", "edge-tts"]


def test_the_mixed_narration_is_kept_when_edge_tts_fails_too(tmp_path, monkeypatch):
    passes = []

    def fake_build_narration(scenes, work_dir, synthesize=None, tempo=1.0):
        passes.append(work_dir)
        if len(passes) == 2:
            raise RuntimeError("seslendirme başarısız: ağ yok")
        original_factory.voice_usage["elevenlabs"] += 1
        original_factory.voice_usage["edge-tts"] += 1
        return work_dir / "voice.m4a", [2.0, 2.0], []

    monkeypatch.setattr(original_factory, "build_narration", fake_build_narration)
    monkeypatch.setattr(original_factory, "voice_usage", {"elevenlabs": 3, "edge-tts": 0})  # an earlier Short
    voice, durations, _, used = original_factory.narrate([{"text": "a"}, {"text": "b"}], tmp_path)
    assert passes == [tmp_path, tmp_path / "edge_only"]
    assert voice == tmp_path / "voice.m4a" and used == {"elevenlabs": 1, "edge-tts": 1}


def test_first_key_fact_is_on_screen_at_frame_one(tmp_path):
    from app.services.subtitle_engine import generate_shorts_ass_subtitles
    ass = tmp_path / "s.ass"
    generate_shorts_ass_subtitles([{"text": "bir iki", "start": 0.0, "end": 6.0}], ass)
    scenes = [{"text": "a", "callout": "270 DERECE"}, {"text": "b", "callout": "14 OMUR"}]
    assert original_factory.add_callouts(ass, scenes, [2.0, 3.0]) == [0.0, 2.25]
    lines = [line for line in ass.read_text(encoding="utf-8").splitlines() if "CalloutStyle," in line
             and line.startswith("Dialogue")]
    assert lines[0].startswith("Dialogue: 2,0:00:00.00,0:00:01.60,CalloutStyle")


def test_short_ends_on_its_last_word_and_the_pops_lead_their_key_facts(tmp_path, monkeypatch):
    lib = tmp_path / "audio"
    (lib / "sfx").mkdir(parents=True)
    for name in ("pop", "riser"):
        (lib / "sfx" / f"{name}.mp3").write_bytes(b"ID3")
    track = tmp_path / "merak_3.mp3"
    track.write_bytes(b"ID3")
    monkeypatch.setattr(original_factory, "AUDIO_LIBRARY", lib)
    monkeypatch.setattr(original_factory, "mix_levels", lambda voice, music, duration, start: ("anull", 0.02))
    monkeypatch.setattr(original_factory, "music_start", lambda music, duration: 0.0)
    commands = []
    monkeypatch.setattr(original_factory.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    original_factory.mix_soundtrack(tmp_path / "v.m4a", tmp_path / "o.m4a", 25.0, [4.0], track, [0.0, 6.25])
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "afade=t=out:st=24.70:d=0.3[m]" in graph  # no 1.5 s fade that gives the restart away
    assert "adelay=0|0[p0]" in graph and "adelay=6220|6220[p1]" in graph
    assert f"volume={original_factory.POP_VOLUME},adelay" in graph and original_factory.POP_VOLUME == 0.8
    assert "adelay=22500|22500[ris]" in graph  # the riser still lands on the replay's opening impact
    assert original_factory.END_PAUSE_SECONDS == 0.2


def _speech_seconds(path: Path) -> float:
    """Time above -50 dB (silencedetect, gaps of 50 ms and more count as silence)."""
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                          "silencedetect=n=-50dB:d=0.05", "-f", "null", "-"], capture_output=True, text=True).stderr
    total = original_factory._probe_duration(path)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)] + [total]
    return total - sum(ends[i] - max(0.0, s) for i, s in enumerate(starts))


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_pauses_inside_a_sentence_are_capped(tmp_path, monkeypatch):
    def line(name, pause):
        # Two words at the raw ElevenLabs level (about -38 dBFS RMS) with a pause between them.
        tone = "aevalsrc=0.0178*sin(2*PI*220*t):s=44100:d=1"
        path = tmp_path / f"{name}.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", tone, "-f", "lavfi", "-i",
                        f"anullsrc=r=44100:cl=mono:d={pause}", "-f", "lavfi", "-i", tone,
                        "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "libmp3lame", str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    half, full = line("half", 0.5), line("full", 1.0)
    assert abs(original_factory.loudness(half) - (-38.0)) < 2.5
    duration = original_factory._probe_duration
    capped = original_factory.tighten_line(half, tmp_path / "capped.wav", 0.15)
    long_capped = original_factory.tighten_line(full, tmp_path / "long.wav", 0.15)
    monkeypatch.setattr(original_factory, "MIDLINE_PAUSE_CAP", "")
    whole = original_factory.tighten_line(half, tmp_path / "whole.wav", 0.15)
    # Without the cap the whole pause stays; with it about 0.27 s is left (ffmpeg 6.1 keeps stop_duration +
    # stop_silence + its 20 ms window), as the longest pauses of the owl Short came out, and a longer pause
    # shrinks to the same length.
    assert abs(duration(whole) - (2.0 + 0.5 + 0.15)) < 0.05
    assert duration(whole) - duration(capped) >= 0.2 and duration(capped) - (2.0 + 0.15) <= 0.3
    assert abs(duration(long_capped) - duration(capped)) < 0.03
    # Both words survive whole: only the pause got shorter.
    assert _speech_seconds(capped) >= 1.95 and abs(_speech_seconds(capped) - _speech_seconds(whole)) < 0.05


def _voice_samples():
    spec = importlib.util.spec_from_file_location("voice_samples",
                                                  original_factory.BASE_DIR / "scripts" / "voice_samples.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_sample_plan_compares_one_change_at_a_time():
    vs = _voice_samples()
    plan = vs.sample_plan()
    assert [s["slug"] for s in plan] == ["alp_simdiki", "alp_hizli_enerjik", "alp_v3_duygulu", "taner_torun",
                                         "emrullah_tuna", "ozgur"]
    today, faster, v3 = plan[:3]
    assert {s["voice_id"] for s in plan[:3]} == {eleven.DEFAULT_TURKISH_VOICE_ID}
    assert (today["model"], today["stability"], today["style"], today["speed"], today["tempo"]) == (
        "eleven_multilingual_v2", 0.32, 0.45, None, 1.1)
    assert (faster["model"], faster["stability"], faster["style"], faster["speed"], faster["tempo"]) == (
        "eleven_multilingual_v2", 0.35, 0.6, 1.15, 1.0)
    assert v3["model"] == "eleven_v3" and v3["tags"] == {0: "[excited]", 2: "[surprised]"} and v3["tempo"] == 1.1
    # The other voices keep today's settings, so only the voice differs from sample 1.
    for other in plan[3:]:
        assert (other["model"], other["stability"], other["style"], other["speed"], other["tempo"]) == (
            "eleven_multilingual_v2", 0.32, 0.45, None, 1.1)
    assert [s["voice_id"] for s in plan[3:]] == ["G5sdncpgy3DkDmvKJnFb", "QftNgZhlSOYKrYbk7WR7", "Mcb3pz5eWjKXcBhMLF4v"]
    assert vs.repo_variables(today) == {}
    assert vs.repo_variables(faster) == {"ELEVENLABS_SPEED": "1.15", "ELEVENLABS_STYLE": "0.6",
                                         "ELEVENLABS_STABILITY": "0.35"}
    assert vs.repo_variables(v3) == {"ELEVENLABS_MODEL": "eleven_v3", "ELEVENLABS_STABILITY": "0.5"}
    assert vs.repo_variables(plan[4]) == {"ELEVENLABS_VOICE_ID": "QftNgZhlSOYKrYbk7WR7"}
    assert sum(len(line) for line in vs.LINES) < 200  # ElevenLabs spend per sample stays small


def test_shared_voices_prefer_energetic_narrators_most_cloned_first():
    vs = _voice_samples()
    voices = [
        {"voice_id": "calm", "name": "Sakin", "use_case": "narrative_story", "descriptive": "calm", "cloned_by_count": 900},
        {"voice_id": "hype", "name": "Enerjik Anlatıcı", "use_case": "social_media", "descriptive": "energetic",
         "cloned_by_count": 50, "public_owner_id": "o1"},
        {"voice_id": "chat", "name": "Sohbet", "use_case": "conversational", "descriptive": "energetic",
         "cloned_by_count": 5000},
        {"voice_id": "pricey", "name": "Pahalı", "use_case": "narrative_story", "descriptive": "energetic",
         "cloned_by_count": 9000, "rate": 2},
        {"voice_id": eleven.DEFAULT_TURKISH_VOICE_ID, "name": "Alp", "use_case": "narrative_story"},
        {"name": "kimliksiz"},
    ]
    ranked = vs.rank_shared(voices, exclude={eleven.DEFAULT_TURKISH_VOICE_ID})
    assert [s["voice_id"] for s in ranked] == ["hype", "calm", "chat"]
    assert ranked[0]["slug"] == "enerjik_anlatici" and ranked[0]["public_owner_id"] == "o1"
    assert ranked[0]["model"] == "eleven_multilingual_v2" and ranked[0]["tempo"] == 1.1


def test_voice_samples_explain_a_missing_key(monkeypatch, capsys, tmp_path):
    vs = _voice_samples()
    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: None)
    assert vs.main(["--out", str(tmp_path)]) == 1
    assert "ELEVENLABS_API_KEY yok" in capsys.readouterr().out


def test_voice_samples_stop_when_the_credits_are_gone(tmp_path, monkeypatch, capsys):
    # Every voice would be refused the same way, and each library voice would first be added to the account.
    vs = _voice_samples()
    calls, api = [], []

    def no_credits(text, out, voice_id=None, **kw):
        calls.append(voice_id)
        eleven.last_error.update(code=401, message='{"detail": {"status": "quota_exceeded"}}')
        return False

    def fake_api(path, key, payload=None, timeout=60):
        api.append(path)
        return {}

    monkeypatch.setattr(vs.shutil, "which", lambda name: f"/usr/bin/{name}")  # refused before any ffmpeg call
    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "k" * 20)
    monkeypatch.setattr(eleven, "synthesize_elevenlabs_speech", no_credits)
    monkeypatch.setattr(vs, "_api", fake_api)
    for name in VOICE_VARIABLES[1:]:
        monkeypatch.setenv(name, "")
    out = tmp_path / "voice_samples"
    out.mkdir()
    (out / "1_alp_simdiki.mp3").write_bytes(b"ID3")
    assert vs.main(["--out", str(out)]) == 1
    assert calls == [eleven.DEFAULT_TURKISH_VOICE_ID]  # one line asked, no other voice tried
    assert not any(path.startswith(("/shared-voices", "/voices/add")) for path in api)
    assert (out / "1_alp_simdiki.mp3").exists() and not (out / "index.json").exists()  # the last set stays
    assert "kredisi bitmiş" in capsys.readouterr().out


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_voice_samples_fill_six_slots_and_skip_voices_that_fail(tmp_path, monkeypatch, capsys):
    vs = _voice_samples()
    tone = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "aevalsrc=0.02*sin(2*PI*180*t):s=44100:d=0.8",
                    "-c:a", "libmp3lame", str(tone)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in VOICE_VARIABLES[1:]:
        monkeypatch.setenv(name, "1.2")  # the repo Variables must not leak into the samples
    calls, api = [], []
    refused = {"G5sdncpgy3DkDmvKJnFb": (400, "voice_not_ready"), "Mcb3pz5eWjKXcBhMLF4v": (500, "server error"),
               "shared_a": (404, '{"detail": {"status": "voice_not_found"}}')}

    def fake_speech(text, out, voice_id=None, model_id=None, previous_text="", next_text="", stability=None,
                    style=None, speed=None):
        assert vs.os.environ["ELEVENLABS_SPEED"] == ""
        calls.append((voice_id, model_id, text, speed))
        if voice_id in refused:
            code, message = refused[voice_id]
            eleven.last_error.update(code=code, message=message)
            return False
        shutil.copy(tone, out)
        return True

    def fake_api(path, key, payload=None, timeout=60):
        api.append((path, payload))
        if path.startswith("/shared-voices"):
            return {"voices": [
                {"voice_id": "shared_b", "public_owner_id": "ob", "name": "Deniz", "use_case": "conversational",
                 "cloned_by_count": 10},
                {"voice_id": "shared_a", "public_owner_id": "oa", "name": "Kaan Anlatıcı", "use_case": "narrative_story",
                 "descriptive": "energetic", "cloned_by_count": 99},
                {"voice_id": eleven.DEFAULT_TURKISH_VOICE_ID, "public_owner_id": "x", "name": "Alp"},
            ]}
        if path == "/voices/add/oa/shared_a":
            return {"voice_id": "added_a"}
        if path == "/user/subscription":
            return {"character_count": 1000 + 600 * (len([p for p, _ in api if p == path]) - 1),
                    "character_limit": 100000}
        raise AssertionError(path)

    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "k" * 20)
    monkeypatch.setattr(eleven, "synthesize_elevenlabs_speech", fake_speech)
    monkeypatch.setattr(vs, "_api", fake_api)
    out = tmp_path / "voice_samples"
    out.mkdir()
    (out / "9_eski.mp3").write_bytes(b"ID3")
    assert vs.main(["--out", str(out)]) == 0

    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    files = [s["file"] for s in index["samples"]]
    assert files == ["1_alp_simdiki.mp3", "2_alp_hizli_enerjik.mp3", "3_alp_v3_duygulu.mp3",
                     "4_emrullah_tuna.mp3", "5_kaan_anlatici.mp3", "6_deniz.mp3"]
    assert sorted(p.name for p in out.glob("*.mp3")) == sorted(files)  # the old set is gone
    assert [s["label"] for s in index["skipped"]] == ["Taner Torun, şimdiki ayarlar", "Özgür, şimdiki ayarlar"]
    # The shared voice the account did not know was added once and voiced under its new id.
    assert ("/voices/add/oa/shared_a", {"new_name": "Kaan Anlatıcı"}) in api
    assert index["samples"][4]["voice_id"] == "added_a"
    assert index["samples"][4]["repo_variables"] == {"ELEVENLABS_VOICE_ID": "added_a"}
    v3_lines = [text for voice, model, text, _ in calls if model == "eleven_v3"]
    assert v3_lines[0].startswith("[excited] Bu baykuş") and v3_lines[2].startswith("[surprised] Ama")
    assert all(speed is None for voice, model, text, speed in calls if model == "eleven_v3")
    for sample in index["samples"]:
        assert abs(sample["lufs"] - original_factory.VOICE_LUFS) < 1.5  # every sample at the Short's level
        assert sample["words_per_second"] > 0 and sample["characters"] >= 190
    report = capsys.readouterr().out
    assert "### Ses örnekleri" in report and "Harcanan ElevenLabs kredisi: 600 karakter" in report
