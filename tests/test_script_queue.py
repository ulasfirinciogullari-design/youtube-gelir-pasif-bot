import json
import sys
from types import SimpleNamespace

import original_factory
from app.services import script_queue, seo_pack

OCTOPUS = {
    "title": "Ahtapotun üç kalbi var 🐙",
    "hook_text": "Üç kalp, mavi kan",
    "scenes": [
        {"text": "Bu ahtapotun tam üç kalbi var.", "visual": "octopus close up", "stock_query": "octopus reef",
         "source": "stock", "callout": "3 KALP"},
        {"text": "İkisi kanı solungaçlara pompalar, üçüncüsü bütün vücuda.", "visual": "octopus gills",
         "stock_query": "octopus swimming", "source": "stock"},
        {"text": "Kanı kırmızı değil maviye çalar, çünkü bakır taşır.", "visual": "blue water octopus",
         "stock_query": "octopus deep sea", "source": "stock", "callout": "MAVİ KAN"},
        {"text": "Yüzerken ana kalp durur, bu yüzden ahtapot yürümeyi sever.", "visual": "octopus walking",
         "stock_query": "octopus walking seabed", "source": "stock"},
        {"text": "Sence hangisi daha tuhaf: üç kalp mi, mavi kan mı?", "visual": "octopus eye",
         "stock_query": "octopus eye", "source": "stock"},
    ],
    "tags": ["ahtapot", "deniz canlıları"],
    "pinned_comment": "Sence hangisi daha tuhaf: üç kalp mi, mavi kan mı?",
    "mood": "belgesel",
}
SEO = {
    "description": "Ahtapotun neden üç kalbi var?\n\nİki kalp kanı solungaçlara, üçüncüsü bütün vücuda pompalar. "
                   "Kanındaki bakır yüzünden kanı maviye çalar ve yüzerken ana kalbi durur.\n\n"
                   "Sence hangisi daha tuhaf: üç kalp mi, mavi kan mı?",
    "hashtags": ["#ahtapot", "#denizcanlıları", "#shorts"],
    "tags": ["ahtapot", "ahtapot kalbi", "mavi kan"],
    "localizations": {code: {"title": f"Octopus title in {code} 🐙", "description": f"Why an octopus has three hearts ({code})."}
                      for code in ("en", "de", "es")},
}


def _write(qdir, name, **item):
    qdir.mkdir(exist_ok=True)
    (qdir / name).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")


def test_take_returns_the_oldest_unused_valid_script(tmp_path):
    _write(tmp_path, "001.json", id="mk-c0001", topic="Ahtapotun kalbi", script=OCTOPUS)
    _write(tmp_path, "002.json", id="mk-c0002", topic="Bozuk", script={"title": "Kısa", "scenes": []})
    _write(tmp_path, "003.json", id="mk-c0003", topic="Ahtapot yine", kategori="hayvan", subject=["octopus"],
           stock_hint="octopus reef", script=OCTOPUS, seo=SEO)
    (tmp_path / "004.json").write_text("{yarım", encoding="utf-8")

    first = script_queue.take(set(), tmp_path)
    assert first["file"] == "001.json" and first["topic"]["id"] == "mk-c0001"
    assert first["topic"]["source"] == "claude" and "seo" not in first["script"]
    assert first["script"]["scenes"][0]["callout"] == "3 KALP"

    # used ids and broken files are passed over, never fatal
    taken = script_queue.take({"mk-c0001"}, tmp_path)
    assert taken["file"] == "003.json" and taken["script"]["seo"] == SEO
    assert taken["topic"] == {"id": "mk-c0003", "topic": "Ahtapot yine", "source": "claude", "kategori": "hayvan",
                              "stock_hint": "octopus reef", "subject": ["octopus"]}
    assert script_queue.take({"mk-c0001", "mk-c0003"}, tmp_path) is None


def test_take_skips_a_script_too_long_for_a_short(tmp_path):
    extra = [{"text": "Üstelik her kolunda ayrı bir beyin gibi çalışan sinir düğümleri vardır.",
              "visual": "octopus arm", "stock_query": "octopus arm", "source": "stock"}] * 2
    long = {**OCTOPUS, "scenes": OCTOPUS["scenes"] + extra}  # 7 scenes, 64 words
    _write(tmp_path, "001.json", id="mk-c0001", script=long)
    assert script_queue.take(set(), tmp_path) is None


def test_queue_dir_needs_an_existing_folder(tmp_path, monkeypatch):
    monkeypatch.delenv(script_queue.QUEUE_ENV, raising=False)
    assert script_queue.queue_dir() is None and script_queue.take(set()) is None
    monkeypatch.setenv(script_queue.QUEUE_ENV, str(tmp_path / "yok"))
    assert script_queue.queue_dir() is None
    monkeypatch.setenv(script_queue.QUEUE_ENV, str(tmp_path))
    assert script_queue.queue_dir() == tmp_path


def test_prewritten_seo_pack_skips_gemini():
    calls = []
    script = {**OCTOPUS, "en_title": "Why an octopus has three hearts", "en_description": "Three hearts."}
    pack = seo_pack.build_seo_pack(
        OCTOPUS["title"], "Ahtapotun kalbi", "metin", script, ["merak"], "#merak", "Seri: mk-c0003",
        call=lambda prompt: calls.append(prompt) or "", series="merak", prewritten=SEO)
    assert calls == [] and pack["seo_engine"] == "claude"
    assert pack["description"].startswith("Ahtapotun neden üç kalbi var?")
    assert pack["description"].endswith("Seri: mk-c0003") and "💬" not in pack["description"]
    assert set(pack["localizations"]) == {"en", "de", "es"}
    assert pack["tags"][:3] == ["ahtapot", "ahtapot kalbi", "mavi kan"]


def test_weak_prewritten_seo_pack_falls_back_to_the_script():
    pack = seo_pack.build_seo_pack(
        OCTOPUS["title"], "Ahtapotun kalbi", "metin", OCTOPUS, ["merak"], "#merak", "Seri: mk-c0003",
        call=lambda prompt: "", series="merak", prewritten={"description": "Kısa."})
    assert pack["seo_engine"] == "script" and pack["description"].endswith("Seri: mk-c0003")


def test_main_uses_a_queued_script_before_gemini(tmp_path, monkeypatch):
    qdir = tmp_path / "kuyruk"
    _write(qdir, "001.json", id="mk-c0001", topic="Ahtapotun kalbi", script=OCTOPUS, seo=SEO)
    produced = []

    def no_gemini(*args, **kwargs):
        raise AssertionError("Gemini akışı çalışmamalı")

    def produce(topic, script, provider, **kwargs):
        produced.append((topic["id"], script["seo"], provider, kwargs.get("clip_pool")))
        return {"meta": {"title": script["title"]}}

    monkeypatch.setenv(script_queue.QUEUE_ENV, str(qdir))
    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(original_factory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(original_factory, "load_dna", lambda *a, **k: {})
    monkeypatch.setattr(original_factory, "load_metrics", lambda *a, **k: {})
    monkeypatch.setattr(original_factory, "choose_topic", no_gemini)
    monkeypatch.setattr(original_factory, "merak_script", no_gemini)
    monkeypatch.setattr(original_factory, "produce", produce)
    assert original_factory.main(["--series", "merak", "--no-upload"]) == 0
    assert produced == [("mk-c0001", SEO, script_queue.PROVIDER, None)]


def test_main_keeps_the_gemini_flow_without_a_queue(tmp_path, monkeypatch):
    chosen = []

    class _Chosen(BaseException):
        pass

    def choose_topic(*args, **kwargs):
        chosen.append(kwargs.get("series"))
        raise _Chosen

    monkeypatch.delenv(script_queue.QUEUE_ENV, raising=False)
    monkeypatch.setattr(original_factory, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(original_factory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(original_factory, "load_dna", lambda *a, **k: {})
    monkeypatch.setattr(original_factory, "load_metrics", lambda *a, **k: {})
    monkeypatch.setattr(original_factory, "choose_topic", choose_topic)
    try:
        original_factory.main(["--series", "merak", "--no-upload"])
    except _Chosen:
        pass
    assert chosen == ["merak"]
