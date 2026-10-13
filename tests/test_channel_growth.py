import json
from datetime import datetime, timezone

import pytest

import original_factory
from app.services import channel_audit, competitor_scan, seo_pack
from app.services.original_script import build_prompt

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
SCRIPT = {
    "description": "Kısa açıklama.",
    "tags": ["seneca", "nero"],
    "pinned_comment": "Sen olsan ne yapardın?",
    "en_title": "Seneca's move",
    "en_description": "How Seneca handled Nero.",
}


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Resource:
    def __init__(self, handler):
        self.handler = handler

    def __getattr__(self, method):
        return lambda **kwargs: _Call(self.handler(method, kwargs))


class FakeYouTube:
    """Answers channels/playlistItems/videos/search calls from canned data and records writes."""

    def __init__(self, uploads, videos, search=None, channels=None):
        self.uploads, self.videos_data, self.search_data = uploads, videos, search or {}
        self.channels_data, self.writes = channels or {}, []

    def channels(self):
        def handle(method, kw):
            if kw.get("mine"):
                return {"items": [{"id": "UC_OURS", "contentDetails": {"relatedPlaylists": {"uploads": "UU_OURS"}}}]}
            return {"items": [self.channels_data[i] for i in kw["id"].split(",") if i in self.channels_data]}
        return _Resource(handle)

    def playlistItems(self):
        def handle(method, kw):
            return {"items": self.uploads.get(kw["playlistId"], [])}
        return _Resource(handle)

    def videos(self):
        def handle(method, kw):
            if method == "list":
                return {"items": [self.videos_data[i] for i in kw["id"].split(",") if i in self.videos_data]}
            self.writes.append((method, kw))
            return {}
        return _Resource(handle)

    def search(self):
        return _Resource(lambda method, kw: {"items": [{"id": {"videoId": v}} for v in self.search_data.get(kw["q"], [])]})


def _upload(video_id, title, published, marker=""):
    return {"snippet": {"resourceId": {"videoId": video_id}, "title": title, "publishedAt": published,
                        "description": f"açıklama\n\nSeri: {marker}" if marker else "eski bot"},
            "contentDetails": {"videoId": video_id}}


def _video(video_id, views, privacy="public", seconds=30, title="", channel="UC_X", published="2026-10-01T00:00:00Z"):
    return {"id": video_id, "statistics": {"viewCount": str(views), "likeCount": "1", "commentCount": "0"},
            "status": {"privacyStatus": privacy, "uploadStatus": "processed", "madeForKids": False,
                       "embeddable": True, "license": "youtube"},
            "contentDetails": {"duration": f"PT{seconds}S"},
            "snippet": {"title": title or video_id, "channelId": channel, "channelTitle": channel,
                        "publishedAt": published, "tags": ["tarih"]}}


def test_clean_tags_respects_youtube_limit():
    tags = seo_pack.clean_tags(["#Seneca", "seneca", "x" * 31, "<roma>"] + [f"etiket numarasi {i}" for i in range(60)])
    assert tags[:2] == ["Seneca", "roma"]
    assert sum(seo_pack._tag_cost(t) for t in tags) <= seo_pack.MAX_TAG_TOTAL
    assert all(len(t) <= 30 for t in tags)


def test_seo_pack_uses_gemini_answer():
    answer = {
        "description": "Bir filozof imparatora servetini neden geri verir?\n\n" + "Seneca, Nero ve Roma. " * 8,
        "hashtags": ["#stoacılık", "#roma"],
        "tags": ["seneca", "nero", "stoicism"],
        "localizations": {code: {"title": f"Title in {code} 🧐", "description": f"Description in {code} language."}
                          for code in seo_pack.LANGUAGES},
    }
    pack = seo_pack.build_seo_pack("Başlık 🧐", "konu", "metin", SCRIPT, ["shorts"], "#shorts #tarih", "Seri: zk-003",
                                   call=lambda prompt: json.dumps(answer, ensure_ascii=False))
    assert pack["seo_engine"] == "gemini" and len(pack["localizations"]) == 18
    assert pack["description"].endswith("#shorts #tarih #stoacılık #roma\n\nSeri: zk-003")
    assert "💬 Sen olsan ne yapardın?" in pack["description"]
    assert pack["localizations"]["ja"]["title"].endswith("#shorts")
    assert pack["localizations"]["de"]["description"].endswith("Seri: zk-003")
    assert pack["tags"][:3] == ["seneca", "nero", "stoicism"] and "shorts" in pack["tags"]


@pytest.mark.parametrize("call", [lambda p: json.dumps({"description": "kısa", "localizations": {}}),
                                  lambda p: (_ for _ in ()).throw(TimeoutError("slow")),
                                  lambda p: None])
def test_seo_pack_falls_back_to_script(call):
    pack = seo_pack.build_seo_pack("Başlık", "konu", "metin", SCRIPT, ["shorts"], "#shorts", "Seri: zk-003", call=call)
    assert pack["seo_engine"] == "script"
    assert pack["description"].startswith("Kısa açıklama.") and pack["description"].endswith("Seri: zk-003")
    assert list(pack["localizations"]) == ["en"]


def test_audit_flags_legacy_repeats_and_unwatched():
    uploads = {"UU_OURS": [
        _upload("a", "Joker'in en sert sahnesi 🔥 #shorts", "2026-10-09T00:00:00Z"),
        _upload("b", "Joker'in en sert sahnesi 🔥", "2026-10-10T00:00:00Z"),
        _upload("c", "Seneca'nın stratejisi", "2026-10-13T00:00:00Z", "zk-003"),
        _upload("d", "Epiktetos ve özgürlük", "2026-10-19T12:00:00Z", "zk-004"),
        _upload("e", "Marcus Aurelius", "2026-10-13T00:00:00Z", "zk-001"),
    ]}
    videos = {v["id"]: v for v in [_video("a", 900), _video("b", 12), _video("c", 5000),
                                   _video("d", 3), _video("e", 4)]}
    yt = FakeYouTube(uploads, videos)
    listed = channel_audit.add_details(yt, channel_audit.list_uploads(yt))
    flagged = channel_audit.flag_candidates(listed, now=NOW)
    reasons = {v["video_id"]: v["reasons"] for v in flagged}
    assert reasons == {
        "b": ["eski film klibi botu", "izlenmedi", "tekrar başlık"],
        "a": ["eski film klibi botu"],
        "e": ["izlenmedi"],
    }
    assert "| b |" in channel_audit.format_audit(listed, flagged)


def test_cleanup_hides_keeping_status_fields_and_deletes():
    yt = FakeYouTube({}, {"a": _video("a", 1)})
    assert channel_audit.apply_cleanup(yt, ["a"], "private") == ["a: gizliye alındı"]
    method, kw = yt.writes[-1]
    assert method == "update" and kw["part"] == "status"
    assert kw["body"]["status"] == {"privacyStatus": "private", "embeddable": True, "license": "youtube",
                                   "selfDeclaredMadeForKids": False}
    assert channel_audit.apply_cleanup(yt, ["a"], "delete") == ["a: silindi"]
    assert yt.writes[-1] == ("delete", {"id": "a"})
    with pytest.raises(ValueError):
        channel_audit.apply_cleanup(yt, ["a"], "wipe")


def test_competitor_scan_profiles_rivals_and_saves_dna(tmp_path):
    rival_uploads = [_upload(f"r{i}", f"Sezar'ın son sözü {i}?", f"2026-10-{10 + i:02d}T00:00:00Z") for i in range(4)]
    videos = {f"r{i}": _video(f"r{i}", 1000 * (i + 1), seconds=40, title=f"Sezar'ın son sözü {i}? 😱", channel="UC_R",
                              published=f"2026-10-{10 + i:02d}T00:00:00Z") for i in range(4)}
    videos["mine"] = _video("mine", 10, channel="UC_OURS")
    channels = {"UC_R": {"id": "UC_R", "snippet": {"title": "Rakip Tarih"},
                         "statistics": {"subscriberCount": "12000", "videoCount": "300"},
                         "contentDetails": {"relatedPlaylists": {"uploads": "UU_R"}}}}
    yt = FakeYouTube({"UU_R": rival_uploads}, videos, search={"stoacılık": ["r0", "r3", "mine"]}, channels=channels)
    answer = {"channels": [{"name": "Rakip Tarih", "dna": {"hooks": ["soru"], "weaknesses": ["kaynak yok"]}}],
              "gaps": ["Osmanlı diplomasisi"], "our_rules": ["Her videoda bir tarih ve yer söyle"],
              "topic_ideas": ["Fatih neden gemileri karadan yürüttü?"]}
    result = competitor_scan.run_scan(yt, tmp_path, ["Seneca'nın stratejisi"], queries=["stoacılık"],
                                      call=lambda prompt: json.dumps(answer, ensure_ascii=False))
    profile = result["profiles"][0]
    assert len(result["profiles"]) == 1 and profile["name"] == "Rakip Tarih"
    assert profile["niche_hits"] == 2 and profile["niche_views"] == 5000
    assert profile["median_views"] == 2500 and profile["avg_seconds"] == 40
    assert profile["title_features"]["question_pct"] == 100 and profile["title_features"]["emoji_pct"] == 100
    assert competitor_scan.load_dna(tmp_path)["our_rules"] == ["Her videoda bir tarih ve yer söyle"]


def test_story_prompt_carries_competitor_rules():
    prompt = build_prompt("konu", ["Başlık A"], ["Her videoda bir tarih söyle"])
    assert "Rakip kanallardan ayrışmak" in prompt and "Her videoda bir tarih söyle" in prompt


def test_choose_topic_extends_the_list_when_it_runs_out(tmp_path, monkeypatch):
    monkeypatch.setattr(original_factory, "load_topics", lambda: [{"id": "zk-001", "topic": "Eski konu"}])
    dna = {"topic_ideas": ["Eski konu", "Fatih neden gemileri karadan yürüttü?"]}
    topic = original_factory.choose_topic({"zk-001"}, dna=dna, data_dir=tmp_path, invent=lambda used: 1 / 0)
    assert topic == {"id": "zk-002", "topic": "Fatih neden gemileri karadan yürüttü?", "source": "rakip boşluğu"}

    topic = original_factory.choose_topic({"zk-001", "zk-002"}, dna=dna, data_dir=tmp_path,
                                          invent=lambda used: "Sokrates baldıran zehrini neden içti?")
    assert topic["id"] == "zk-003" and topic["source"] == "gemini"
    assert [t["id"] for t in original_factory.load_generated_topics(tmp_path)] == ["zk-002", "zk-003"]

    # A generated topic that is not on the channel yet is picked before inventing another.
    assert original_factory.choose_topic({"zk-001", "zk-002"}, data_dir=tmp_path)["id"] == "zk-003"
