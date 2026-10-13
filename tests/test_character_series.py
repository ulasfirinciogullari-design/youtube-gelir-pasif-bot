import json
import re

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


def test_karisik_rotates_after_the_last_upload(tmp_path):
    assert original_factory.pick_series("karisik", tmp_path) == "merak"
    channel_memory.record_upload(tmp_path, "v1", "zk-001", "Tarih", "story")
    channel_memory.record_upload(tmp_path, "v2", "kd-1", "Penguen", "comedy")
    assert original_factory.pick_series("karisik", tmp_path) == "merak"
    channel_memory.record_upload(tmp_path, "v3", "mk-001", "Kedi", "merak")
    assert original_factory.pick_series("karisik", tmp_path) == "karakter"
    channel_memory.record_upload(tmp_path, "v4", "fk-001", "Shelby", "character")
    assert original_factory.pick_series("karisik", tmp_path) == "story"
    assert original_factory.pick_series("story", tmp_path) == "story"
    assert original_factory.pick_series("karisik", tmp_path, topic_id="fk-004") == "karakter"
    assert original_factory.pick_series("karisik", tmp_path, topic_id="mk-007") == "merak"


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


def test_character_prompt_carries_the_visual_world():
    topic = original_factory.load_topics(original_factory.CHARACTER_TOPICS_FILE)[0]
    prompt = build_prompt(topic["topic"], None, None, topic)
    assert "Görsel dünya: 1919 Birmingham" in prompt and '"source": her sahnede "ai"' in prompt


def test_character_scenes_use_generated_images_only(tmp_path, monkeypatch):
    from app.services import original_visuals
    calls = []
    monkeypatch.setattr(original_visuals, "fetch_stock_videos", lambda *a: calls.append("stock") or [tmp_path / "s.mp4"])
    monkeypatch.setattr(original_visuals, "generate_flux_images",
                        lambda prompt, count, dest, tag, style: calls.append(style) or [tmp_path / f"{tag}.jpg"] * count)
    scene = {"visual": "a man in a cap", "stock_query": "street", "source": "stock"}
    assets = original_visuals.collect_scene_assets(scene, 2, tmp_path, "s00", set(), "noir style", ai_only=True)
    assert [k for k, _ in assets] == ["image", "image"] and calls == ["noir style"]
    original_visuals.collect_scene_assets(scene, 1, tmp_path, "s01", set())
    assert calls[-1] == "stock"


def test_image_model_comes_from_the_environment(monkeypatch):
    from app.services import original_visuals
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    assert original_visuals.image_model() == "fal-ai/flux/schnell"
    monkeypatch.setenv("IMAGE_MODEL", "fal-ai/flux/dev")
    assert original_visuals.image_model() == "fal-ai/flux/dev"
    monkeypatch.setenv("IMAGE_MODEL", "https://evil.example/model")
    assert original_visuals.image_model() == "fal-ai/flux/schnell"


def test_music_and_effects_come_from_the_generated_library(tmp_path):
    (tmp_path / "music").mkdir()
    (tmp_path / "sfx").mkdir()
    for name in ("story_1", "karakter_1", "karakter_2"):
        (tmp_path / "music" / f"{name}.mp3").write_bytes(b"ID3")
    assert original_factory.pick_music("fk-001", "karakter", tmp_path).name.startswith("karakter_")
    assert original_factory.pick_music("zk-001", "story", tmp_path).name == "story_1.mp3"
    assert original_factory.sfx("whoosh", tmp_path) is None  # the old sine-wave synths are not used any more
    (tmp_path / "sfx" / "whoosh.mp3").write_bytes(b"ID3")
    assert original_factory.sfx("whoosh", tmp_path).name == "whoosh.mp3"


def test_reference_analysis_reads_links_and_builds_a_spec():
    from app.services import reference_analysis as ra
    text = ("https://www.youtube.com/shorts/D4RVfi_5l30 , https://www.youtube.com/shorts/msrST6TrWn8,"
            "https://youtu.be/sMGpFnFu9gk https://www.youtube.com/watch?v=k951xfBlETg&t=3 D4RVfi_5l30")
    assert ra.extract_ids(text) == ["D4RVfi_5l30", "msrST6TrWn8", "sMGpFnFu9gk", "k951xfBlETg"]

    def watch(video_id, prompt):
        if video_id == "msrST6TrWn8":
            raise RuntimeError("video unavailable")
        return json.dumps({"visual_source": "AI görsel", "avg_shot_seconds": 1.5, "music": "dark phonk"})

    prompts = []
    result = ra.analyze_references(["D4RVfi_5l30", "msrST6TrWn8"], watch=watch,
                                   call=lambda p: prompts.append(p) or json.dumps({"gaps": ["kesmeler yavaş"]}))
    assert result["spec"] == {"gaps": ["kesmeler yavaş"]}
    assert "error" in result["videos"][1] and "dark phonk" in prompts[0] and "ElevenLabs Türkçe" in prompts[0]
    report = ra.format_report(result)
    assert "1.5 sn/kesme" in report and "analiz edilemedi" in report and "kesmeler yavaş" in report


MERAK_SCRIPT = {
    "title": "Kediler neden hep dört ayak üstüne düşer? 🐱",
    "hook_text": "KEDİLER NEDEN AYAKTA DÜŞER?",
    "scenes": [
        {"text": "Bu kedi bilim insanlarını yıllarca şaşırttı.", "visual": "a cat mid-air", "stock_query": "cat jumping"},
        {"text": "Bir kedi baş aşağı düşerken bile yere ayakları üstüne iner.", "visual": "a cat falling",
         "stock_query": "cat falling", "source": "stock"},
        {"text": "Sırrı, iç kulağındaki denge organında saklı.", "visual": "a cat ear close-up", "stock_query": "cat ear"},
        {"text": "Kedi önce başını çevirir, ardından gövdesini bir yay gibi büker.", "visual": "a cat stretching",
         "stock_query": "cat stretching"},
        {"text": "Bu hareket yarım saniyeden kısa sürer ve hiçbir fizik kuralını bozmaz, sadece çok iyi kullanır.", "visual": "a cat landing",
         "stock_query": "cat landing"},
        {"text": "Yani kedi düşerken havada kendi kendini çevirir. Peki sen bunu biliyor muydun?",
         "visual": "a cat looking at camera", "stock_query": "cat looking"},
    ],
    "description": "Kedilerin düşerken nasıl döndüğünü anlatıyoruz.",
    "tags": ["kedi", "bilim"],
    "pinned_comment": "Senin kedin hiç düştü mü?",
    "en_title": "Why cats always land on their feet",
    "en_description": "The cat righting reflex.",
}


def test_merak_topics_are_valid_and_have_stock_hints():
    topics = original_factory.load_topics(original_factory.MERAK_TOPICS_FILE)
    ids = [t["id"] for t in topics]
    assert len(topics) >= 30 and len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"mk-\d{3}", i) for i in ids)
    assert all(t["topic"].strip() and 1 <= len(t["stock_hint"].split()) <= 4 for t in topics)


def test_merak_prompt_asks_for_real_stock_footage():
    topic = original_factory.load_topics(original_factory.MERAK_TOPICS_FILE)[0]
    prompt = build_prompt(topic["topic"], None, None, topic)
    assert f"Görüntü ipucu: {topic['stock_hint']}" in prompt and '"source": her sahnede "stock"' in prompt
    assert "Karakter:" not in prompt and "doğrulanmış" in prompt


def test_merak_script_validates_and_still_refuses_film_references():
    topic = {"topic": "Kediler neden ayakta düşer?", "stock_hint": "cat jumping"}
    script = validate_script(json.loads(json.dumps(MERAK_SCRIPT)), topic)
    assert len(script["scenes"]) == 6 and script["scenes"][0]["visual"] == "a cat mid-air"
    bad = json.loads(json.dumps(MERAK_SCRIPT))
    bad["scenes"][0]["text"] = "Thomas Shelby bile kedilerin bu hareketine şaşırırdı."
    with pytest.raises(ScriptError, match="shelby"):
        validate_script(bad, topic)


def _hit(vid, tags, type_="film", duration=10, vertical=False, thumb=True):
    w, h = (1080, 1920) if vertical else (1920, 1080)
    rendition = {"url": f"https://cdn.example/{vid}.mp4", "width": w, "height": h, "size": 1000}
    if thumb:
        rendition["thumbnail"] = f"https://cdn.example/{vid}.jpg"
    return {"id": vid, "tags": tags, "type": type_, "duration": duration, "videos": {"medium": rendition}}


def test_stock_clips_must_show_the_subject():
    from app.services.original_visuals import matches_subject, tag_score
    cat = ["cat", "kitten"]
    assert matches_subject("cat, pet, animal", cat) and matches_subject("kittens, cute", cat)
    assert not matches_subject("mountain, landscape, nature", cat)
    assert not matches_subject("skeleton, human, anatomy, spine", cat)
    assert not matches_subject("clock, time, watch", cat)
    assert matches_subject("polar, bear, arctic", ["polar bear"]) and not matches_subject("bear, brown, forest", ["polar bear"])
    assert matches_subject("anything", None)
    assert tag_score("cat, jumping, pet", "cat jumping slow motion") == 2
    assert tag_score("cat, sleeping", "cat jumping") == 1


def test_pixabay_candidates_drop_animation_and_off_topic_clips(monkeypatch):
    from app.services import original_visuals
    hits = [
        _hit(1, "mountain, hills, landscape"),
        _hit(2, "cat, sleeping, pet"),
        _hit(3, "cat, jumping, garden", vertical=True),
        _hit(4, "cat, cartoon", type_="animation"),
        _hit(5, "cat, jumping", duration=2),
        _hit(6, "kitten, jump, play"),
    ]
    monkeypatch.setattr(original_visuals, "search_pixabay", lambda q, **kw: hits)
    found = original_visuals.pixabay_candidates("cat jumping", {"pixabay:6"}, ["cat", "kitten"])
    assert [h["id"] for h in found] == [3, 2]


def test_merak_falls_back_to_subject_queries(tmp_path, monkeypatch):
    from app.services import original_visuals
    queries = []

    def fake_stock(query, count, dest, used, subject=None):
        queries.append((query, tuple(subject or ())))
        return [tmp_path / "c.mp4"] * count if query == "cat" else []

    monkeypatch.setattr(original_visuals, "fetch_stock_videos", fake_stock)
    found = original_visuals.fetch_stock_with_fallbacks("a fluffy cat turning", "cat jumping", 2, tmp_path, set(),
                                                        ["cat", "kitten"])
    assert [q for q, _ in queries] == ["a fluffy cat turning", "cat jumping", "a fluffy", "cat"]
    assert all(subj == ("cat", "kitten") for _, subj in queries) and len(found) == 2


def test_matched_stock_downloads_only_the_clips_gemini_picked(tmp_path, monkeypatch):
    from app.services import original_visuals
    pool_hits = [_hit(i, "cat, pet") for i in range(1, 9)]
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: pool_hits[:limit])
    downloaded = []

    def fake_download(hit, dest_dir, used):
        downloaded.append(hit["id"])
        used.add(f"pixabay:{hit['id']}")
        return dest_dir / f"pixabay_{hit['id']}.mp4"

    monkeypatch.setattr(original_visuals, "download_pixabay_hit", fake_download)
    scenes = [{"text": "Kedi zıplıyor.", "stock_query": "cat jumping"}, {"text": "Kedi uyuyor.", "stock_query": "cat sleeping"},
              {"text": "Kedi yere iniyor.", "stock_query": "cat landing"}]
    seen = {}

    def fake_match(topic, sc, wanted, pool):
        seen.update(topic=topic, wanted=wanted, pool=len(pool))
        return [[4, 0], [6], []]

    assets, how = original_visuals.collect_matched_stock(scenes, [[1.6, 1.6], [1.6], [1.6]], tmp_path, set(), ["cat"],
                                                         "cat jumping", "Kediler neden ayakta düşer?", match=fake_match)
    assert how == "gemini" and seen == {"topic": "Kediler neden ayakta düşer?", "wanted": [2, 1, 1], "pool": 8}
    assert downloaded == [5, 1, 7] and [len(a) for a in assets] == [2, 1, 0]

    # No Gemini answer: keyword order, still on-subject.
    calls = []
    monkeypatch.setattr(original_visuals, "fetch_stock_with_fallbacks",
                        lambda q, fb, n, d, u, subject=None: calls.append((q, n, tuple(subject))) or [d / "k.mp4"] * n)
    assets, how = original_visuals.collect_matched_stock(scenes, [[1.6, 1.6], [1.6], [1.6]], tmp_path, set(), ["cat"],
                                                         "cat jumping", "Kedi", match=lambda *a: None)
    assert how == "anahtar kelime" and calls[0] == ("cat jumping", 2, ("cat",)) and [len(a) for a in assets] == [2, 1, 1]


def test_clip_matcher_reads_gemini_picks():
    from app.services import clip_matcher
    candidates = [_hit(i, "cat") for i in range(1, 6)] + [_hit(6, "cat", thumb=False)]
    scenes = [{"text": "Kedi zıplıyor.", "stock_query": "cat jumping"}, {"text": "Kedi uyuyor.", "stock_query": "cat sleeping"}]
    sent = []

    def fake_call(parts):
        sent.append(parts)
        # scene 1 asks for clip 2 twice and an unknown clip; scene 2 repeats clip 2 and asks for the thumbless clip 6
        return json.dumps({"scenes": [{"scene": 1, "clips": [2, 2, 99, 4, 5]}, {"scene": 2, "clips": [2, 6, 3]},
                                      {"scene": 7, "clips": [1]}]})

    picks = clip_matcher.match_clips("Kediler", scenes, [2, 2], candidates, call=fake_call,
                                     fetch=lambda url: (b"\xff\xd8\xff", "image/jpeg") if url else None)
    assert picks == [[1, 3], [2]]
    images = [p for p in sent[0] if "inlineData" in p]
    assert len(images) == 5 and "Kedi zıplıyor." in sent[0][0]["text"] and "(2 aday)" in sent[0][0]["text"]
    assert clip_matcher.match_clips("Kediler", scenes, [2, 2], candidates, call=lambda parts: None,
                                    fetch=lambda url: (b"\xff\xd8\xff", "image/jpeg")) is None
    assert clip_matcher.match_clips("Kediler", scenes, [2, 2], candidates, call=fake_call, fetch=lambda url: None) is None


def test_merak_prompt_names_the_subject_and_topics_have_one():
    topics = original_factory.load_topics(original_factory.MERAK_TOPICS_FILE)
    assert all(t["subject"] and all(re.fullmatch(r"[a-z ]+", w) for w in t["subject"]) for t in topics)
    prompt = build_prompt(topics[0]["topic"], None, None, topics[0])
    assert "Ana özne (İngilizce stok etiketi): cat, kitten" in prompt and "birebir göstermeli" in prompt
    assert original_factory.topic_subject({"stock_hint": "octopus swimming"}) == ["octopus"]
    assert original_factory.topic_subject({"stock_hint": "", "subject": ["owl"]}) == ["owl"]


def test_merak_series_cuts_faster_and_runs_out_into_gemini_topics(tmp_path, monkeypatch):
    assert original_factory.SERIES["merak"]["cut"] < original_factory.SERIES["story"].get("cut", 2.5)
    from app.services.original_visuals import split_duration
    assert len(split_duration(10.0, 1.6)) == 7
    monkeypatch.setattr(original_factory, "load_topics", lambda path=None: [{"id": "mk-001", "topic": "Kedi", "stock_hint": "cat"}])
    topic = original_factory.choose_topic({"mk-001"}, data_dir=tmp_path, series="merak",
                                          invent=lambda used: {"topic": "Ahtapotun neden üç kalbi var?", "stock_hint": "octopus"})
    assert topic["id"] == "mk-002" and topic["stock_hint"] == "octopus"


def test_hook_banner_stays_for_the_whole_video(tmp_path):
    from app.services.subtitle_engine import generate_shorts_ass_subtitles
    ass = tmp_path / "s.ass"
    generate_shorts_ass_subtitles([{"text": "bir iki", "start": 0.0, "end": 31.0}], ass)
    original_factory.add_hook_overlay(ass, "KEDİLER NEDEN AYAKTA DÜŞER?", 31.4)
    events = ass.read_text(encoding="utf-8").split("[Events]")[1]
    assert "Dialogue: 1,0:00:00.00,0:00:31.40,HookStyle" in events
