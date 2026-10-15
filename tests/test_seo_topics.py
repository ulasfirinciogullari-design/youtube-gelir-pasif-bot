"""Curiosity topic queue, the competitor scan's niche and the curiosity search package."""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import original_factory
from app.services import competitor_scan, seo_pack, subtitle_engine

MERAK = original_factory.SERIES["merak"]
USED = {"mk-001", "mk-010", "mk-028"}
OWL_SCRIPT = {
    "title": "Baykuş boynunu nasıl bu kadar çevirir? 🦉",
    "scenes": [{"text": "Bu baykuş başını 270 derece çevirir."}, {"text": "Boynunda 14 omur var."}],
    "description": "Baykuşların boynunu anlatıyoruz.",
    "tags": ["baykuş", "boyun"],
    "pinned_comment": "Sence insan boynu kaç derece döner: 90 mı 180 mi?",
    "en_title": "How owls turn their heads",
    "en_description": "Why an owl's neck does not break.",
}
OWL_ANSWER = {
    "description": "Baykuşlar başını nasıl 270 derece çevirir?\n\n"
                   "Baykuşun boynunda 14 omur var, insanda ise 7. Boynundaki atardamarlar dönüşte sıkışmasın diye "
                   "kanı yedekleyen geniş ceplerle korunur.\n\n"
                   "Sence insan boynu kaç derece döner: 90 mı 180 mi?",
    "hashtags": ["#baykuş", "#baykuşlar", "#hayvanlar", "#shorts"],
    "tags": ["baykuş", "baykuş boynu", "gece kuşu", "owl", "hayvanlar"],
    "localizations": {code: {"title": f"Owl title in {code} 🦉", "description": f"Owl description in {code}."}
                      for code in ("de", "nl", "az")},
}


def test_merak_topics_have_unique_ids_and_every_field():
    topics = original_factory.load_topics(original_factory.MERAK_TOPICS_FILE)
    ids = [t["id"] for t in topics]
    assert len(set(ids)) == len(ids) and set(ids) == {f"mk-{n:03d}" for n in range(1, 41)}
    for topic in topics:
        assert topic["topic"].strip() and topic["stock_hint"].strip() and topic["subject"], topic["id"]
        assert topic["kategori"] in {"hayvan", "vücut", "psikoloji", "doğa", "bilim"}, topic["id"]


def test_strong_animal_topics_come_first_and_weak_ones_last():
    topics = original_factory.load_topics(original_factory.MERAK_TOPICS_FILE)
    assert original_factory.pick_topic(topics, USED)["id"] == "mk-019"
    assert original_factory.pick_topic(topics, set())["id"] == "mk-019"  # a no-upload run knows no published ids
    unused = [t["id"] for t in topics if t["id"] not in USED]
    assert unused[:8] == ["mk-019", "mk-006", "mk-004", "mk-025", "mk-005", "mk-002", "mk-014", "mk-015"]
    assert unused[-8:] == ["mk-016", "mk-017", "mk-013", "mk-009", "mk-008", "mk-018", "mk-023", "mk-027"]
    kinds = [t["kategori"] for t in topics if t["id"] in unused[:25]]
    assert kinds.count("hayvan") >= 16  # about two animal topics per other topic up front


class _Exec:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class ScanYouTube:
    """Records the scan's searches; finds no videos, so only the queries and the prompt matter."""

    def __init__(self):
        self.queries = []

    def channels(self):
        return SimpleNamespace(list=lambda **kw: _Exec({"items": [{"id": "UC_OURS"}]}))

    def search(self):
        def search_list(**kw):
            self.queries.append(kw["q"])
            return _Exec({"items": []})
        return SimpleNamespace(list=search_list)

    def videos(self):
        return SimpleNamespace(list=lambda **kw: _Exec({"items": []}))


def test_competitor_scan_searches_the_series_niche(tmp_path):
    prompts = []

    def call(prompt):
        prompts.append(prompt)
        return json.dumps({"our_rules": ["Her videoda bir sayı söyle"]})

    yt = ScanYouTube()
    result = competitor_scan.run_scan(yt, tmp_path, [], call=call, series="merak")
    assert yt.queries == ["ilginç bilgiler", "hayvanlar hakkında ilginç bilgiler", "bunu biliyor muydun",
                          "bilim shorts", "vücudumuz hakkında"]
    assert result["series"] == "merak" and result["queries"] == yt.queries
    assert "hayvanlar, insan vücudu" in prompts[0] and "Fatih Sultan Mehmet" not in prompts[0]
    assert competitor_scan.load_dna(tmp_path)["our_rules"] == ["Her videoda bir sayı söyle"]

    yt = ScanYouTube()
    competitor_scan.run_scan(yt, tmp_path, [], call=call, series="karakter")
    assert yt.queries == competitor_scan.NICHE_QUERIES and "Fatih Sultan Mehmet" in prompts[1]

    yt = ScanYouTube()
    assert competitor_scan.run_scan(yt, tmp_path, [], call=call)["series"] == "merak"
    assert yt.queries == competitor_scan.MERAK_QUERIES


class _ScanReached(BaseException):
    """main() catches every Exception around the scan; a BaseException stops the run right there."""


@pytest.mark.parametrize("requested,scanned", [("karisik", "merak"), ("merak", "merak"), ("story", "story"),
                                               ("karakter", "karakter")])
def test_main_scans_the_requested_series_niche(tmp_path, monkeypatch, requested, scanned):
    scans = []

    def run_scan(*args, **kwargs):
        scans.append(kwargs.get("series"))
        raise _ScanReached

    monkeypatch.setitem(sys.modules, "publish_to_youtube", SimpleNamespace(get_authenticated_service=object))
    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(original_factory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(original_factory, "report_channel", lambda *a, **k: (set(), []))
    monkeypatch.setattr(original_factory, "load_dna", lambda *a, **k: {})  # no analysis yet: the scan is due
    monkeypatch.setattr(original_factory, "run_scan", run_scan)
    with pytest.raises(_ScanReached):
        original_factory.main(["--series", requested])
    assert scans == [scanned]


def test_merak_prompt_drops_the_history_template():
    merak = seo_pack.build_prompt("Başlık", "Baykuşlar başını neden çevirebilir?", "metin", "merak")
    assert "Bugün için ders" not in merak and "abone" in merak  # only as "do not ask to subscribe"
    assert "iki seçenekli" in merak and "%80'i Türkçe" in merak
    assert "Videonun yorum sorusu: (yok)" in merak
    assert "Bugün için ders:" in seo_pack.build_prompt("Başlık", "konu", "metin")
    assert "yorum sorusu" not in seo_pack.build_prompt("Başlık", "konu", "metin", "story", "Sence A mı B mi?")


def test_seo_pack_survives_list_fields_answered_as_one_string():
    answer = {**OWL_ANSWER, "hashtags": "#baykuş #hayvanlar, #gece", "tags": "baykuş boynu, gece kuşu"}
    pack = seo_pack.build_seo_pack(
        OWL_SCRIPT["title"], "konu", "metin", OWL_SCRIPT, MERAK["tags"], MERAK["hashtags"], "Seri: mk-028",
        call=lambda prompt: json.dumps(answer, ensure_ascii=False), series="merak")
    assert pack["description"].split("\n\n")[-2].split()[:3] == ["#baykuş", "#hayvanlar", "#gece"]
    assert pack["tags"][:3] == ["baykuş boynu", "gece kuşu", "baykuş"]
    answer = {**OWL_ANSWER, "hashtags": 7, "tags": None}
    pack = seo_pack.build_seo_pack(
        OWL_SCRIPT["title"], "konu", "metin", OWL_SCRIPT, MERAK["tags"], MERAK["hashtags"], "Seri: mk-028",
        call=lambda prompt: json.dumps(answer, ensure_ascii=False), series="merak")
    assert pack["seo_engine"] == "gemini" and pack["tags"][:2] == ["baykuş", "boyun"]
    assert pack["description"].split("\n\n")[-2] == MERAK["hashtags"]


def test_merak_seo_pack_keeps_a_good_description_with_few_languages():
    prompts = []
    pack = seo_pack.build_seo_pack(
        OWL_SCRIPT["title"], "Baykuşlar başını neden çevirebilir?", "metin", OWL_SCRIPT, MERAK["tags"],
        MERAK["hashtags"], "Seri: mk-028",
        call=lambda prompt: prompts.append(prompt) or json.dumps(OWL_ANSWER, ensure_ascii=False), series="merak")
    assert "Bugün için ders" not in prompts[0]
    assert f"Videonun yorum sorusu: {OWL_SCRIPT['pinned_comment']}" in prompts[0]  # the video's own question
    assert pack["seo_engine"] == "gemini"
    assert set(pack["localizations"]) == {"en", "de", "nl", "az"}  # the script's English fills the gap
    assert pack["description"].startswith("Baykuşlar başını nasıl 270 derece çevirir?")
    assert "💬" not in pack["description"] and pack["description"].endswith("Seri: mk-028")
    hashtags = pack["description"].split("\n\n")[-2].split()
    assert hashtags[:3] == ["#baykuş", "#baykuşlar", "#hayvanlar"] and "#shorts" in hashtags
    assert all("#shorts" not in item["title"] for item in pack["localizations"].values())
    assert all("#shorts" in item["description"] for item in pack["localizations"].values())
    tags = pack["tags"]
    assert tags[:3] == ["baykuş", "baykuş boynu", "gece kuşu"]
    assert tags[-4:] == ["baykus", "baykus boynu", "gece kusu", "ilginc bilgiler"]


def test_folded_tags_only_take_the_budget_left():
    assert seo_pack.clean_tags(["Baykuş", "kuş", "owl", "baykus"], fold=5) == ["Baykuş", "kuş", "owl", "baykus", "kus"]
    assert seo_pack.clean_tags(["baykuş"]) == ["baykuş"]  # off unless asked
    many = [f"kuş {i}" for i in range(40)]
    assert seo_pack.clean_tags(many, fold=5)[30:] == [f"kus {i}" for i in range(5)]  # past our own 30-tag cap
    long = ["çok özel bir etiket"] + [f"etiket numarasi {i}" for i in range(60)]
    plain, folded = seo_pack.clean_tags(long), seo_pack.clean_tags(long, fold=5)
    assert folded[:len(plain)] == plain  # a folded copy never pushes a real tag out
    assert sum(seo_pack._tag_cost(t) for t in folded) <= seo_pack.MAX_TAG_TOTAL


def test_topical_hashtags_go_first_and_shorts_stays():
    base = "#shorts #bilim #merak #keşfet"
    assert seo_pack.clean_hashtags(["#baykuş", "hayvanlar", "#Shorts", "#gece kuşu"], base) == \
        "#baykuş #hayvanlar #gecekuşu #shorts #bilim #merak #keşfet"
    crowded = seo_pack.clean_hashtags([f"#konu{i}" for i in range(10)], base).split()
    assert len(crowded) == 8 and crowded[:3] == ["#konu0", "#konu1", "#konu2"] and "#shorts" in crowded
    assert seo_pack.clean_hashtags([], base) == base


def test_produce_title_has_no_shorts_hashtag(tmp_path, monkeypatch):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"")
    seo_calls = []
    real_seo = original_factory.build_seo_pack
    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # SEO falls back to the script; no critic
    monkeypatch.delenv("KEEP_AUDIO", raising=False)
    monkeypatch.setattr(original_factory, "build_narration", lambda scenes, *a, **k: (voice, [2.0] * len(scenes), []))
    monkeypatch.setattr(original_factory, "build_visual_track", lambda *a, **k: {
        "segments": [], "thumbnail": None, "ai_images": 0, "stock_videos": 2})
    monkeypatch.setattr(subtitle_engine, "generate_shorts_ass_subtitles", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "add_hook_overlay", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "add_callouts", lambda *a, **k: [])
    monkeypatch.setattr(original_factory, "pick_music", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "mix_soundtrack", lambda *a, **k: voice)
    monkeypatch.setattr(original_factory, "render_final", lambda *a, **k: None)
    monkeypatch.setattr(original_factory, "storyboard", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(original_factory, "build_seo_pack",
                        lambda *a, **k: seo_calls.append(k) or real_seo(*a, **k))

    result = original_factory.produce({"id": "mk-028", "topic": "Baykuşlar", "stock_hint": "owl", "subject": ["owl"]},
                                      dict(OWL_SCRIPT), "test", series="merak")
    assert result["meta"]["title"] == OWL_SCRIPT["title"] and "#shorts" not in result["meta"]["title"]
    assert seo_calls[0]["series"] == "merak"
    assert "#shorts" in result["meta"]["description"]
