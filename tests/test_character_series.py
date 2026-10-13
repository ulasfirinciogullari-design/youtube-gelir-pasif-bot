import json

import pytest

import original_factory
from app.services import channel_memory
from app.services.original_script import ScriptError, build_prompt, validate_script

SHELBY = {"character": "Thomas Shelby", "work": "Peaky Blinders", "names": ["Thomas Shelby", "Shelby", "Cillian Murphy"]}


def _scene(text, visual="a man in a flat cap seen from behind on a foggy street"):
    return {"text": text, "visual": visual, "stock_query": "foggy street", "source": "ai"}


CHARACTER_SCRIPT = {
    "title": "Thomas Shelby odaya girince neden susuyor? 🤫",
    "scenes": [
        _scene("Thomas Shelby bir pazarlık masasına oturduğunda ilk konuşan asla o olmaz.",
               "Thomas Shelby in a flat cap, Cillian Murphy face, smoking by a window"),
        _scene("Herkes konuştukça elindeki kartları farkında olmadan masaya serer."),
        _scene("Psikologlar buna sessizlik baskısı der; insan boşluğu doldurmak için taviz verir."),
        _scene("Müzakere uzmanları da aynı yolu izler, karşı tarafın cümlesini bitirmesini bekler."),
        _scene("Shelby'nin gücü bağırmasından değil, beklemeyi bilmesinden gelir."),
        _scene("Sen son pazarlığında ilk konuşan kişi miydin?"),
    ],
    "description": "Thomas Shelby'nin sessizliğini psikolojiyle yorumluyoruz.",
    "tags": ["thomas shelby", "peaky blinders"],
    "pinned_comment": "Sessizlik sence güç mü?",
    "en_title": "Why Thomas Shelby speaks last",
    "en_description": "The psychology of silence.",
}


def test_character_prompt_names_the_character_and_keeps_faces_out():
    prompt = build_prompt("Thomas Shelby neden en son konuşur?", ["Thomas Shelby: Odanın En Tehlikeli Adamı"], None, SHELBY)
    assert "Karakter: Thomas Shelby (Peaky Blinders)" in prompt
    assert "oyuncu, film veya dizi adı ASLA geçmesin" in prompt
    assert '"pinned_comment"' in prompt and "Thomas Shelby: Odanın En Tehlikeli Adamı" in prompt
    assert "Karakter:" not in build_prompt("Seneca neden servetini geri verdi?")


def test_character_script_keeps_the_name_in_narration_but_not_in_images():
    script = validate_script(json.loads(json.dumps(CHARACTER_SCRIPT)), SHELBY)
    assert script["scenes"][0]["text"].startswith("Thomas Shelby")
    visual = script["scenes"][0]["visual"].lower()
    assert "shelby" not in visual and "cillian" not in visual and "flat cap" in visual
    # The same script is still refused in the history series.
    with pytest.raises(ScriptError, match="shelby"):
        validate_script(json.loads(json.dumps(CHARACTER_SCRIPT)))


def test_karisik_alternates_with_the_last_upload(tmp_path):
    assert original_factory.pick_series("karisik", tmp_path) == "karakter"
    channel_memory.record_upload(tmp_path, "v1", "zk-001", "Tarih", "story")
    channel_memory.record_upload(tmp_path, "v2", "kd-1", "Penguen", "comedy")
    assert original_factory.pick_series("karisik", tmp_path) == "karakter"
    channel_memory.record_upload(tmp_path, "v3", "fk-001", "Shelby", "character")
    assert original_factory.pick_series("karisik", tmp_path) == "story"
    assert original_factory.pick_series("story", tmp_path) == "story"
    assert original_factory.pick_series("karisik", tmp_path, topic_id="fk-004") == "karakter"


def test_character_topics_run_out_into_gemini_topics(tmp_path, monkeypatch):
    lists = {"zk": [{"id": "zk-001", "topic": "Tarih"}], "fk": [dict(SHELBY, id="fk-001", topic="Shelby neden susar?")]}
    monkeypatch.setattr(original_factory, "load_topics",
                        lambda path=None: lists["fk" if path == original_factory.CHARACTER_TOPICS_FILE else "zk"])
    assert original_factory.choose_topic(set(), data_dir=tmp_path, series="karakter")["character"] == "Thomas Shelby"

    invented = {"topic": "Walter White neden yalan söyledi?", "character": "Walter White", "work": "Breaking Bad",
                "names": ["Walter White"]}
    topic = original_factory.choose_topic({"fk-001"}, data_dir=tmp_path, series="karakter", invent=lambda used: invented)
    assert topic["id"] == "fk-002" and topic["character"] == "Walter White" and topic["source"] == "gemini"
    # Generated character topics never leak into the history queue.
    assert original_factory.choose_topic(set(), data_dir=tmp_path)["id"] == "zk-001"


def test_character_topic_file_is_valid():
    topics = original_factory.load_topics(original_factory.CHARACTER_TOPICS_FILE)
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) >= 30
    assert all(original_factory.MARKER_RE.fullmatch(f"Seri: {i}") and i.startswith("fk-") for i in ids)
    common_words = {"light", "rocky", "professor", "profesör", "the"}
    for t in topics:
        assert t["character"] and t["work"] and t["names"]
        assert not {n.lower() for n in t["names"]} & common_words


def test_film_title_examples_come_from_the_old_clips(tmp_path):
    (tmp_path / "channel_audit.json").write_text(json.dumps({"videos": [
        {"title": "👑 Thomas Shelby: Odanın En Tehlikeli Adamı #shorts", "views": 1005},
        {"title": "👑 Marcus Aurelius: Zihinsel Kale #shorts", "views": 4000},
        {"title": "👑 Thomas Shelby vs Walter White: Kim Daha Tehlikeli? #shorts", "views": 5263},
    ]}), encoding="utf-8")
    assert original_factory.film_title_examples(tmp_path) == [
        "Thomas Shelby vs Walter White: Kim Daha Tehlikeli?", "Thomas Shelby: Odanın En Tehlikeli Adamı",
    ]
    assert original_factory.film_title_examples(tmp_path / "yok") == []
