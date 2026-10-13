import json
import shutil
import subprocess
from pathlib import Path

import pytest

import original_factory
from app.services import original_script, original_visuals
from app.services.original_script import ScriptError, validate_script, write_script

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _scene(text, source="ai"):
    return {"text": text, "visual": "ancient roman study at night, oil lamp", "stock_query": "oil lamp", "source": source}


VALID_SCRIPT = {
    "title": "İmparator neden notlarını kimseye göstermedi?",
    "scenes": [
        _scene("Dünyanın en güçlü adamı her gece gizlice kendine mektup yazıyordu."),
        _scene("Marcus Aurelius bu notları yayımlamak için değil, kendini düzeltmek için yazdı.", "stock"),
        _scene("Sayfalarda zafer yok; öfke, sabır ve ölüm üzerine sert hatırlatmalar var."),
        _scene("Kendine, başkalarının hatalarına değil kendi tepkine bakmasını söylüyordu."),
        _scene("Bugün Düşünceler adıyla okunan bu notlar, aslında bir imparatorun iç sesiydi."),
        _scene("Peki sen kimse görmeyecek olsa kendine ne yazardın?"),
    ],
    "description": "Marcus Aurelius'un Düşünceler'i neden yazdığını anlatıyoruz.",
    "tags": ["marcus aurelius", "stoacılık"],
    "pinned_comment": "Kendine yazacağın tek cümle ne olurdu?",
    "en_title": "Why the emperor hid his notes",
    "en_description": "Why Marcus Aurelius wrote Meditations.",
}


def test_validate_script_normalizes_and_strips_hashtags():
    raw = json.loads(json.dumps(VALID_SCRIPT))
    raw["title"] += " #shorts"
    script = validate_script(raw)
    assert script["title"] == VALID_SCRIPT["title"]
    assert len(script["scenes"]) == 6
    assert script["scenes"][1]["source"] == "stock"


def test_validate_script_rejects_film_characters():
    raw = json.loads(json.dumps(VALID_SCRIPT))
    raw["scenes"][0]["text"] = "Thomas Shelby bir odaya girdiğinde herkes susardı."
    with pytest.raises(ScriptError, match="shelby"):
        validate_script(raw)


def test_validate_script_rejects_wrong_scene_count():
    raw = json.loads(json.dumps(VALID_SCRIPT))
    raw["scenes"] = raw["scenes"][:3]
    with pytest.raises(ScriptError, match="scene count"):
        validate_script(raw)


def test_write_script_falls_through_providers():
    calls = []

    def unconfigured(prompt):
        calls.append("none")
        return None

    def broken(prompt):
        calls.append("broken")
        return "not json"

    def good(prompt):
        calls.append("good")
        return "```json\n" + json.dumps(VALID_SCRIPT, ensure_ascii=False) + "\n```"

    script, provider = write_script("konu", providers=[("a", unconfigured), ("b", broken), ("c", good)])
    assert provider == "c"
    assert calls == ["none", "broken", "broken", "good"]
    assert script["title"] == VALID_SCRIPT["title"]


def test_write_script_raises_when_nothing_valid():
    with pytest.raises(ScriptError):
        write_script("konu", providers=[("a", lambda p: None)])


def test_provider_http_error_reports_body(monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=0):
        body = io.BytesIO(b'{"error": {"message": "model claude-x not found"}}')
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, body)

    monkeypatch.setattr(original_script.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(original_script, "_model_overrides", {})
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://reseller.example/v1")
    with pytest.raises(ScriptError) as err:
        original_script._call_anthropic("prompt")
    assert "reseller.example/v1/messages" in str(err.value)
    assert "model claude-x not found" in str(err.value)


def test_refused_model_falls_back_to_one_the_endpoint_offers(monkeypatch):
    import io
    import urllib.error
    sent_models = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/v1/models"):
            return _Resp(json.dumps({"data": [
                {"id": "text-embedding-3-large"}, {"id": "gpt-5.4"}, {"id": "claude-haiku-4-5"},
            ]}).encode())
        model = json.loads(req.data)["model"]
        sent_models.append(model)
        if model == "claude-sonnet-4-6":
            body = io.BytesIO(b'{"error":{"message":"Model \\"claude-sonnet-4-6\\" is not available for this group"}}')
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, body)
        return _Resp(json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode())

    monkeypatch.setattr(original_script.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(original_script, "_model_overrides", {})
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://reseller.example")
    monkeypatch.delenv("ANTHROPIC_SCRIPT_MODEL", raising=False)

    assert original_script._call_anthropic("prompt") == "ok"
    assert original_script._call_anthropic("prompt") == "ok"
    # Claude models are preferred on the Messages endpoint; the second call reuses the fallback.
    assert sent_models == ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-haiku-4-5"]


def test_pick_fallback_model_prefers_chat_models():
    available = ["text-embedding-3", "gpt-image-1", "deepseek-v4", "gpt-5.4-mini"]
    assert original_script.pick_fallback_model(available) == "gpt-5.4-mini"
    assert original_script.pick_fallback_model(available, prefer="claude") == "gpt-5.4-mini"
    # What the reseller key offered on the first real run: no Claude, only DeepSeek.
    reseller = ["deepseek-flash", "deepseek-v4-flash-vision-exp", "deepseek-v4.1-flash-expires-on-0910", "deepseek-v4-pro"]
    assert original_script.pick_fallback_model(reseller, prefer="claude") == "deepseek-v4-pro"
    assert original_script.pick_fallback_model(["gpt-image-2", "gpt-image-2.5-flare"]) is None


def test_synthesize_line_prefers_elevenlabs_with_context(tmp_path, monkeypatch):
    from app.services import elevenlabs_engine
    calls = []

    def fake_eleven(text, out, voice_id=None, previous_text="", next_text=""):
        calls.append((text, previous_text, next_text))
        out.write_bytes(b"x" * 2000)
        return True

    monkeypatch.setattr(elevenlabs_engine, "get_elevenlabs_api_key", lambda: "key")
    monkeypatch.setattr(elevenlabs_engine, "synthesize_elevenlabs_speech", fake_eleven)
    monkeypatch.setitem(original_factory.voice_usage, "elevenlabs", 0)
    out = original_factory.synthesize_line("orta", tmp_path / "a.mp3", previous_text="önce", next_text="sonra")
    assert out.exists() and calls == [("orta", "önce", "sonra")]
    assert original_factory.voice_usage["elevenlabs"] == 1


def test_pick_topic_skips_published_ids():
    topics = [{"id": "zk-001", "topic": "a"}, {"id": "zk-002", "topic": "b"}]
    assert original_factory.pick_topic(topics, {"zk-001"})["id"] == "zk-002"
    with pytest.raises(SystemExit):
        original_factory.pick_topic(topics, {"zk-001", "zk-002"})


def test_topic_file_is_valid():
    topics = original_factory.load_topics()
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) >= 30
    assert all(original_factory.MARKER_RE.fullmatch(f"Seri: {i}") for i in ids)


class _FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeYouTube:
    def channels(self):
        return self

    def playlistItems(self):
        return self

    def list(self, **kwargs):
        if "mine" in kwargs:
            return _FakeRequest({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        return _FakeRequest({"items": [
            {"snippet": {"description": "metin\n\nSeri: zk-001"}},
            {"snippet": {"description": "eski film videosu"}},
        ]})


def test_published_topic_ids_reads_description_markers():
    assert original_factory.published_topic_ids(_FakeYouTube()) == {"zk-001"}


def test_split_duration_keeps_segments_short():
    parts = original_visuals.split_duration(7.0)
    assert max(parts) <= original_visuals.MAX_SEGMENT_SECONDS
    assert abs(sum(parts) - 7.0) < 1e-9


def test_validate_script_hook_text_and_emoji_cleanup():
    raw = json.loads(json.dumps(VALID_SCRIPT))
    raw["title"] = "Şifreli mesajı bir bakışta çözüyor 🧐"
    raw["scenes"][0]["text"] += " 🔥"
    raw["hook_text"] = "Kimse {fark} etmedi 😳"
    script = validate_script(raw)
    assert script["title"].endswith("🧐")
    assert "🔥" not in script["scenes"][0]["text"]
    assert script["hook_text"] == "Kimse fark etmedi"

    raw.pop("hook_text")
    assert validate_script(raw)["hook_text"] == "Şifreli mesajı bir bakışta"


def test_add_hook_overlay_inserts_style_and_event(tmp_path):
    from app.services.subtitle_engine import generate_shorts_ass_subtitles

    ass = tmp_path / "s.ass"
    generate_shorts_ass_subtitles([{"text": "bir iki üç", "start": 0.0, "end": 2.0}], ass)
    original_factory.add_hook_overlay(ass, "kimse fark etmedi")
    content = ass.read_text(encoding="utf-8")
    header, events = content.split("[Events]")
    assert "Style: HookStyle," in header
    assert "HookStyle,,0,0,0,,{\\fad" in events and events.rstrip().endswith("KİMSE FARK ETMEDİ")
    assert "@zirveninkanunu" in events and "👑" not in content


def test_build_prompt_includes_style_examples():
    prompt = original_script.build_prompt("konu", ["Ordusu kaçarken geri dönüyor 😳"])
    assert "Ordusu kaçarken geri dönüyor 😳" in prompt
    assert "Kanalda en çok izlenen" not in original_script.build_prompt("konu")


class _MetricsYouTube(_FakeYouTube):
    def __init__(self):
        self.items = [
            {"snippet": {"description": "a\n\nSeri: zk-001", "title": "Eski başlık 🧐",
                         "publishedAt": "2026-10-10T08:00:00Z", "resourceId": {"videoId": "v1"}}},
            {"snippet": {"description": "b\n\nSeri: zk-002", "title": "Yeni başlık 😳",
                         "publishedAt": "2026-10-12T08:00:00Z", "resourceId": {"videoId": "v2"}}},
            {"snippet": {"description": "film videosu", "title": "x",
                         "publishedAt": "2026-10-01T08:00:00Z", "resourceId": {"videoId": "v3"}}},
        ]

    def videos(self):
        return self

    def list(self, **kwargs):
        if "mine" in kwargs:
            return super().list(**kwargs)
        if kwargs.get("part") == "statistics,status":
            return _FakeRequest({"items": [
                {"id": "v1", "statistics": {"viewCount": "720", "likeCount": "30"}, "status": {"privacyStatus": "public"}},
                {"id": "v2", "statistics": {"viewCount": "480", "likeCount": "40"}, "status": {"privacyStatus": "public"}},
            ]})
        return _FakeRequest({"items": self.items})


def test_channel_metrics_rank_and_report(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.services import channel_metrics

    yt = _MetricsYouTube()
    videos = channel_metrics.list_series_videos(yt, original_factory.MARKER_RE)
    assert [v["video_id"] for v in videos] == ["v1", "v2"]
    channel_metrics.add_statistics(yt, videos)
    now = datetime(2026, 10, 13, 8, tzinfo=timezone.utc)
    ranked = channel_metrics.rank_videos(videos, now)
    # v1: 720 views / 72 h = 10/h, v2: 480 views / 24 h = 20/h
    assert [v["video_id"] for v in ranked] == ["v2", "v1"]
    report = channel_metrics.format_report(videos, "not")
    assert "| zk-002 | Yeni başlık 😳 | public | 480 | 40 |" in report and "_not_" in report

    monkeypatch.setattr(channel_metrics, "add_retention", lambda v: None)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    published, examples = channel_metrics.report_channel(yt, original_factory.MARKER_RE, tmp_path)
    assert published == {"zk-001", "zk-002"}
    assert examples == []  # fewer than 3 rankable videos: no style examples yet
    assert (tmp_path / "metrics.json").exists() and "zk-001" in (tmp_path / "summary.md").read_text(encoding="utf-8")


def _pixabay_hit(vid, width, height, duration=12):
    def rendition(w, h):
        return {"url": f"https://cdn.example/{vid}_{h}.mp4", "width": w, "height": h, "size": 1000}
    return {"id": vid, "duration": duration, "videos": {
        "large": rendition(width * 2, height * 2), "medium": rendition(width, height),
        "small": rendition(width * 2 // 3, height * 2 // 3), "tiny": {"url": "", "width": 0, "height": 0, "size": 0},
    }}


def test_fetch_pixabay_videos_prefers_vertical_and_skips_used(tmp_path, monkeypatch):
    payload = {"hits": [
        _pixabay_hit(1, 1920, 1080),
        _pixabay_hit(2, 1080, 1920),
        _pixabay_hit(3, 1920, 1080, duration=2),
        _pixabay_hit(4, 1920, 1080),
    ]}
    seen_urls, downloaded = [], []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=0):
        seen_urls.append(req.full_url)
        return _Resp()

    def fake_download(url, dest, headers=None, timeout=60):
        downloaded.append(url)
        dest.write_bytes(b"x")
        return dest

    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    monkeypatch.setattr(original_visuals.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(original_visuals, "_download", fake_download)

    used = {"pixabay:4"}
    paths = original_visuals.fetch_pixabay_videos("chess board", 3, tmp_path, used)

    assert "q=chess+board" in seen_urls[0] and "safesearch=true" in seen_urls[0]
    # Vertical clip first at its 1920 px rendition, then the landscape clip at 1080p (not 4K);
    # the 2-second clip and the already used clip are skipped.
    assert downloaded == ["https://cdn.example/2_1920.mp4", "https://cdn.example/1_1080.mp4"]
    assert [p.name for p in paths] == ["pixabay_2.mp4", "pixabay_1.mp4"]
    assert used == {"pixabay:4", "pixabay:2", "pixabay:1"}


def test_fetch_stock_videos_falls_back_to_pixabay(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(original_visuals, "fetch_pexels_videos",
                        lambda q, n, d, u: calls.append(("pexels", n)) or [])
    monkeypatch.setattr(original_visuals, "fetch_pixabay_videos",
                        lambda q, n, d, u: calls.append(("pixabay", n)) or [d / "a.mp4"] * n)
    assert len(original_visuals.fetch_stock_videos("ruins", 2, tmp_path, set())) == 2
    assert calls == [("pexels", 2), ("pixabay", 2)]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_full_render_with_offline_assets(tmp_path, monkeypatch):
    def fake_tts(text, out, **context):
        seconds = 0.35 * len(text.split())
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=180:duration={seconds:.2f}",
                        "-c:a", "libmp3lame", "-b:a", "96k", str(out)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out

    def fake_flux(prompt, count, dest_dir, tag):
        paths = []
        for i in range(count):
            p = dest_dir / f"flux_{tag}_{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=768x1344", "-frames:v", "1", str(p)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            paths.append(p)
        return paths

    def fake_pexels(query, count, dest_dir, used_ids):
        p = dest_dir / "pexels_fake.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30", "-t", "5", str(p)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return [p]

    monkeypatch.setattr(original_visuals, "generate_flux_images", fake_flux)
    monkeypatch.setattr(original_visuals, "fetch_pexels_videos", fake_pexels)
    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path)

    topic = {"id": "zk-001", "topic": "test"}
    result = original_factory.produce(topic, validate_script(VALID_SCRIPT), "test", synthesize=fake_tts)

    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result["video"])], text=True))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["r_frame_rate"] == "60/1"
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert result["thumbnail"].exists()
    meta = result["meta"]
    assert meta["description"].endswith("Seri: zk-001")
    assert meta["hook_text"]
    assert meta["contains_synthetic_media"] is True
    assert meta["stock_videos"] >= 1
    assert abs(float(probe["format"]["duration"]) - meta["duration_seconds"]) < 0.3
