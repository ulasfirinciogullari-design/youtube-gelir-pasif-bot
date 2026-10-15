import json
import re
import shutil
import subprocess

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


def test_music_follows_the_mood_the_script_picked(tmp_path):
    (tmp_path / "music").mkdir()
    for n in (1, 2, 3):
        (tmp_path / "music" / f"merak_{n}.mp3").write_bytes(b"ID3")
    pick = original_factory.pick_music
    # By its id alone the owl Short drew the bouncy marimba track; a wildlife topic now gets the documentary one.
    assert pick("mk-028", "merak", tmp_path).name == "merak_2.mp3"
    assert pick("mk-028", "merak", tmp_path, mood="belgesel").name == "merak_3.mp3"
    assert pick("mk-028", "merak", tmp_path, mood="eglenceli").name == "merak_2.mp3"
    assert pick("mk-028", "merak", tmp_path, mood="sakin").name == "merak_1.mp3"
    # A mood whose track is missing from the library still gets a track of the series.
    (tmp_path / "music" / "merak_3.mp3").unlink()
    assert pick("mk-028", "merak", tmp_path, mood="belgesel").name in {"merak_1.mp3", "merak_2.mp3"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_narration_is_brought_to_one_level_and_the_music_sits_under_it(tmp_path):
    def tone(name, source):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-t", "6", str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    f = original_factory
    music = tone("music.wav", "sine=frequency=880,volume=6")
    # A quiet voice (like the ElevenLabs narration) and a loud one end up at the same level, the music the same
    # distance under each.
    for voice in (tone("quiet.wav", "sine=frequency=220,volume=0.2"), tone("loud.wav", "sine=frequency=220,volume=6")):
        chain, level = f.mix_levels(voice, music, 6.0)
        assert abs(f.loudness(voice, chain=chain) - f.VOICE_LUFS) < 0.5
        assert abs(f.loudness(music, chain=f"volume={level}") - (f.VOICE_LUFS - f.MUSIC_UNDER_VOICE)) < 0.5
    # Nothing to measure: the old fixed levels.
    silent = tone("silent.wav", "anullsrc=r=44100:cl=mono")
    assert f.loudness(silent) is None
    assert f.mix_levels(silent, music, 6.0) == (f.voice_chain(1.0), f.MUSIC_VOLUME)
    assert f.mix_levels(voice, silent, 6.0)[1] == f.MUSIC_VOLUME


def test_music_is_mixed_at_the_measured_level_and_leaves_room_for_the_voice(tmp_path, monkeypatch):
    lib = tmp_path / "audio"
    (lib / "sfx").mkdir(parents=True)
    monkeypatch.setattr(original_factory, "AUDIO_LIBRARY", lib)
    track = tmp_path / "merak_3.mp3"
    track.write_bytes(b"ID3")
    monkeypatch.setattr(original_factory, "mix_levels",
                        lambda voice, music, duration, start: (original_factory.voice_chain(2.5, 0.8), 0.0123))
    monkeypatch.setattr(original_factory, "music_start", lambda music, duration: 7.5)
    commands = []
    monkeypatch.setattr(original_factory.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    original_factory.mix_soundtrack(tmp_path / "v.m4a", tmp_path / "o.m4a", 30.0, [4.0], track)
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert graph.startswith("[0:a]highpass=f=70,volume=2.5,acompressor=") and "release=60,volume=0.8[v]" in graph
    assert "[1:a]atrim=start=7.5,asetpts=PTS-STARTPTS,volume=0.0123,equalizer=f=2500:t=o:w=2:g=-4,afade=t=in" in graph


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_music_starts_past_a_quiet_intro_but_never_runs_out(tmp_path):
    def track(name, intro_seconds, seconds):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={intro_seconds},volume=0.01",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds},volume=4",
                        "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    # Like merak_3: near silence for 8 s, then the track plays; the Short starts about where it arrives.
    assert 6.0 <= original_factory.music_start(track("intro.wav", 8, 40), 20.0) <= 8.5
    # No intro to skip; and a Short as long as the rest of the track starts early enough to finish inside it.
    assert original_factory.music_start(track("steady.wav", 0.1, 40), 20.0) == 0.0
    assert original_factory.music_start(track("intro.wav", 8, 40), 44.0) == 3.0
    assert original_factory.music_start(tmp_path / "missing.mp3", 20.0) == 0.0


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
    assert "Ana özne (İngilizce stok etiketi): dog, puppy" in prompt and "birebir göstermeli" in prompt
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


def test_stock_scene_with_fewer_clips_plays_each_clip_once(tmp_path, monkeypatch):
    from app.services import original_visuals
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"]
    monkeypatch.setattr(original_visuals, "collect_matched_stock",
                        lambda *a, **kw: ([[("video", clips[0])], [("video", clips[1]), ("video", clips[2])]], "gemini"))
    rendered = []

    def fake_render(video, out, duration, offset):
        rendered.append((video.name, round(duration, 2)))
        return out

    monkeypatch.setattr(original_visuals, "render_video_segment", fake_render)
    scenes = [{"text": "a", "visual": "a"}, {"text": "b", "visual": "b"}]
    result = original_visuals.build_visual_track(scenes, [4.8, 3.2], tmp_path, stock_only=True, segment_seconds=1.6)
    # 4.8 s would be three cuts, but only one clip matched: it plays once for the whole scene.
    # Every file runs a little past its slot so it can dissolve into the next shot.
    assert result["durations"] == [4.8, 1.6, 1.6]
    assert rendered == [("a.mp4", 5.15), ("b.mp4", 1.95), ("c.mp4", 1.95)]
    assert result["clip_match"] == "gemini" and result["cut_times"] == [4.8, 6.4]


FOOTAGE_REPLY = {
    "coverage": 8,
    "title": "Kediler neden hep ayakta düşer? 🐱",
    "hook_text": "KEDİLER NEDEN AYAKTA DÜŞER?",
    "scenes": [
        {"text": s["text"], "visual": s["visual"], "clips": clips}
        for s, clips in zip(MERAK_SCRIPT["scenes"], [
            [{"n": 3, "kare": 2}, {"n": 5, "kare": 9}], [{"n": 3, "kare": 1}, {"n": 1}], [2], [{"n": 4, "kare": 3}],
            [{"n": 6, "kare": 1}], [{"n": 7, "kare": 2}, {"n": 99}],
        ])
    ],
    "description": "Kedilerin düşerken nasıl döndüğünü anlatıyoruz.",
    "tags": ["kedi"], "pinned_comment": "Senin kedin hiç düştü mü?", "en_title": "Cats", "en_description": "Cats.",
}


def _footage_run(tmp_path, monkeypatch, replies, check=lambda script, topic: [], pexels=None):
    from app.services import footage_script, original_visuals
    hits = {"cat jumping": [_hit(i, "cat", duration=12) for i in range(1, 11)],
            "cat landing": [_hit(i, "cat", duration=12) for i in range(5, 15)]}
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: hits[q][:limit])
    monkeypatch.setattr(original_visuals, "pexels_candidates",
                        lambda q, used, subject=None, limit=15: (pexels or {}).get(q, [])[:limit])
    replies, prompts = [r if isinstance(r, str) else json.dumps(r) for r in replies], []

    def fake_call(parts, temperature=0.2):
        prompts.append(parts)
        return replies.pop(0)

    topic = {"id": "mk-001", "topic": "Kediler neden ayakta düşer?", "stock_hint": "cat jumping", "subject": ["cat"]}
    result = footage_script.write_footage_script(
        topic, tmp_path, call=fake_call, plan=lambda t, s, h: ["cat jumping", "cat landing"],
        sheet=lambda hit, d: (b"\xff\xd8\xff", "image/jpeg"), check=check,
        attempts=2,  # these tests are about clips; the third attempt and the hook rules are in test_hook_rules.py
    )
    return result, prompts


def test_footage_first_script_names_clips_for_every_scene(tmp_path, monkeypatch):
    from app.services import footage_script
    result, prompts = _footage_run(tmp_path, monkeypatch, [{"scenes": []}, FOOTAGE_REPLY])
    # Both searches contribute in turn and the 6 clips they share appear once.
    assert result["coverage"] == 8 and [h["id"] for h in result["pool"][:6]] == [1, 5, 2, 6, 3, 7]
    assert len(result["pool"]) == 14
    scenes = result["script"]["scenes"]
    # Clip 3 is taken by scene 1, so scene 2 keeps only clip 1; frame 9 is clamped to 3; clip 99 does not exist.
    assert scenes[0]["clips"] == [{"n": 3, "kare": 2}, {"n": 5, "kare": 3}]
    assert scenes[1]["clips"][0] == {"n": 1, "kare": 2} and scenes[5]["clips"][0] == {"n": 7, "kare": 2}
    # The last attempt is kept even with single-clip scenes; unused clips top them up to one per ~4 words.
    assert [len(s["clips"]) for s in scenes] == [2, 2, 2, 2, 3, 3]
    assert len({ref["n"] for s in scenes for ref in s["clips"]}) == 14
    # The first reply was rejected and the retry tells Gemini why.
    assert len(prompts) == 2 and "reddedildi" in prompts[1][1]["text"]
    assert sum(1 for p in prompts[0] if "inlineData" in p) == 14 and "Aday 1 (12 sn):" in prompts[0][1]["text"]
    assert footage_script.frame_time(12, 1) == pytest.approx(1.2) and footage_script.frame_time(12, 3) == pytest.approx(7.6)


RICH_FOOTAGE_REPLY = {**FOOTAGE_REPLY, "scenes": [
    {**scene, "clips": clips} for scene, clips in zip(FOOTAGE_REPLY["scenes"], [
        [1, 2], [3, 4], [5, 6], [7, 8], [9, 10, 11], [12, 13, 14]])
]}


def test_footage_first_asks_again_when_a_sentence_gets_a_single_clip(tmp_path, monkeypatch):
    result, prompts = _footage_run(tmp_path, monkeypatch, [FOOTAGE_REPLY, RICH_FOOTAGE_REPLY])
    assert len(prompts) == 2 and "2, 3, 4, 5, 6. sahnede yeterli video yok" in prompts[1][1]["text"]
    assert [[ref["n"] for ref in s["clips"]] for s in result["script"]["scenes"]] == [
        [1, 2], [3, 4], [5, 6], [7, 8], [9, 10, 11], [12, 13, 14]]


def test_a_scene_whose_clips_went_to_earlier_scenes_is_filled_not_thrown_away(tmp_path, monkeypatch):
    # Scene 5 names only clips that scenes 1-4 already took, in both answers (the owl sample, 2026-10-14).
    taken = {**RICH_FOOTAGE_REPLY, "scenes": [
        {**scene, "clips": clips} for scene, clips in zip(RICH_FOOTAGE_REPLY["scenes"], [
            [1, 2], [3, 4], [5, 6], [7, 8], [1, 3], [12, 13, 14]])
    ]}
    result, prompts = _footage_run(tmp_path, monkeypatch, [taken, taken])
    assert len(prompts) == 2 and "5. sahnede yeterli video yok" in prompts[1][1]["text"]
    clips = [[ref["n"] for ref in s["clips"]] for s in result["script"]["scenes"]]
    assert clips[4] and not set(clips[4]) & {n for i, c in enumerate(clips) if i != 4 for n in c}
    assert len(clips[4]) == 3  # topped up to one clip per ~4 words with clips nobody used


def test_footage_first_keeps_the_thin_script_when_the_retry_fails(tmp_path, monkeypatch):
    result, prompts = _footage_run(tmp_path, monkeypatch, [FOOTAGE_REPLY, "{}"])
    assert len(prompts) == 2 and result["coverage"] == 8
    assert [len(s["clips"]) for s in result["script"]["scenes"]] == [2, 2, 2, 2, 3, 3]


def test_finished_footage_script_goes_through_the_fact_check(tmp_path, monkeypatch):
    checked = []

    def check(script, topic):
        checked.append(topic)
        script["scenes"][0]["text"] = "Bu kedi bilim insanlarını uzun yıllar şaşırttı."
        return ["1. sahne: süre belirsiz"]

    result, _ = _footage_run(tmp_path, monkeypatch, [RICH_FOOTAGE_REPLY], check=check)
    assert checked == ["Kediler neden ayakta düşer?"]
    assert result["script"]["scenes"][0]["text"] == "Bu kedi bilim insanlarını uzun yıllar şaşırttı."


def test_fact_check_swaps_in_corrected_sentences_only():
    from app.services.footage_script import check_facts
    script = validate_script(json.loads(json.dumps(MERAK_SCRIPT)), {"topic": "Kedi", "stock_hint": "cat"})
    original = [s["text"] for s in script["scenes"]]
    fixed = "Bu hareket bir saniyeden kısa sürer ve hiçbir fizik kuralını bozmaz, sadece çok iyi kullanır."
    reply = {"fixes": [
        {"scene": 5, "text": fixed, "why": "yarım saniye kesin bir ölçüm değil"},
        {"scene": 9, "text": "olmayan sahne"}, {"scene": 2, "text": ""}, {"scene": "x", "text": "bozuk"},
        {"scene": 3, "text": "çok uzun " * 60}, {"scene": 1, "text": "Kısa."},  # too long / too few words left
    ]}
    prompts = []
    notes = check_facts(script, "Kediler neden ayakta düşer?", call=lambda p: prompts.append(p) or json.dumps(reply))
    assert notes == ["5. sahne: yarım saniye kesin bir ölçüm değil"]
    assert [s["text"] for s in script["scenes"]] == original[:4] + [fixed] + original[5:]
    assert "1. Bu kedi bilim insanlarını yıllarca şaşırttı." in prompts[0]
    # No answer or a failed call leaves the script as it was.
    assert check_facts(script, "Kedi", call=lambda p: None) == []

    def broken(prompt):
        raise ScriptError("HTTP 429")

    assert check_facts(script, "Kedi", call=broken) == [] and script["scenes"][4]["text"] == fixed


def test_fact_check_corrects_the_key_facts_on_screen_too():
    from app.services.footage_script import check_facts
    raw = json.loads(json.dumps(MERAK_SCRIPT))
    for i, callout in ((1, "300 METRE"), (2, "İÇ KULAK"), (3, "YAY GİBİ")):
        raw["scenes"][i]["callout"] = callout
    script = validate_script(raw, {"topic": "Kedi", "stock_hint": "cat"})
    second, third = script["scenes"][1]["text"], script["scenes"][2]["text"]
    reply = {"fixes": [
        {"scene": 2, "text": second, "callout": "340 METRE", "why": "ses saniyede 340 metre gider"},
        {"scene": 3, "text": "Sırrı, kulağının derinindeki denge organında saklı.", "why": "ifade"},
        {"scene": 4, "text": script["scenes"][3]["text"], "callout": "YAY GİBİ", "why": "aynı"},
        {"scene": 5, "text": "Bu hareket bir saniyeden kısa sürer ve hiçbir fizik kuralını bozmaz, sadece çok iyi kullanır.",
         "callout": "1 SANİYE", "why": "yarım saniye kesin değil"},
    ]}
    prompts = []
    notes = check_facts(script, "Kedi", call=lambda p: prompts.append(p) or json.dumps(reply))
    assert "2. Bir kedi baş aşağı düşerken bile yere ayakları üstüne iner. [ekranda: 300 METRE]" in prompts[0]
    scenes = script["scenes"]
    # Only the number on screen changed; a rewritten sentence drops a key fact the editor did not confirm;
    # an unchanged scene is skipped; and the editor cannot add a key fact to a scene that had none.
    assert scenes[1]["text"] == second and scenes[1]["callout"] == "340 METRE"
    assert scenes[2]["text"] != third and "callout" not in scenes[2]
    assert scenes[3]["callout"] == "YAY GİBİ" and "callout" not in scenes[4]
    assert [n.split(":")[0] for n in notes] == ["2. sahne", "3. sahne", "5. sahne"]


def test_key_facts_are_cleaned_and_kept_to_a_few():
    raw = json.loads(json.dumps(MERAK_SCRIPT))
    callouts = ["270 DERECE 🦉", "{\\b1}14 OMUR", "BU ÇOK UZUN BİR EKRAN YAZISI OLURDU", "7 OMUR", "BÜYÜK GÖZLER", "SON"]
    for scene, callout in zip(raw["scenes"], callouts):
        scene["callout"] = callout
    scenes = validate_script(raw, {"topic": "Baykuş", "stock_hint": "owl"})["scenes"]
    # Emoji and subtitle control characters go, a callout too long for the screen is dropped, at most four stay.
    assert [s.get("callout") for s in scenes] == ["270 DERECE", "14 OMUR", None, "7 OMUR", "BÜYÜK GÖZLER", None]


def _pexels_video(vid, slug, duration=12, files=None):
    files = files if files is not None else [
        {"file_type": "video/mp4", "quality": "sd", "width": 540, "height": 960, "link": f"https://px.example/{vid}-sd.mp4"},
        {"file_type": "video/mp4", "quality": "hd", "width": 1080, "height": 1920, "link": f"https://px.example/{vid}-hd.mp4"},
        {"file_type": "video/mp4", "quality": "uhd", "width": 2160, "height": 3840, "link": f"https://px.example/{vid}-4k.mp4"},
    ]
    return {"id": vid, "duration": duration, "url": f"https://www.pexels.com/video/{slug}-{vid}/",
            "image": f"https://images.example/{vid}.jpg", "video_files": files}


def test_pexels_videos_join_the_pool_in_pixabay_shape(tmp_path, monkeypatch):
    from app.services import clip_matcher, footage_script, original_visuals
    videos = [
        _pexels_video(11, "an-owl-turning-its-head"),
        _pexels_video(12, "close-up-video-of-a-bird"),  # the slug does not name the subject
        _pexels_video(13, "owl-in-the-dark", duration=2),  # too short
        _pexels_video(14, "owl-on-a-branch"),  # already used in an earlier Short
        _pexels_video(15, "owl-blinking", files=[{"file_type": "video/mp4", "width": 360, "height": 640,
                                                    "link": "https://px.example/15.mp4"}]),  # too small for 1080p
        _pexels_video(16, "snowy-owl-flying"),
    ]
    monkeypatch.setattr(original_visuals, "search_pexels", lambda q, per_page=15: videos)
    found = original_visuals.pexels_candidates("owl", {"pexels:14"}, ["owl"])
    assert [h["id"] for h in found] == [11, 16]
    hit = found[0]
    assert hit["source"] == "pexels" and hit["tags"] == "an, owl, turning, its, head"
    # The full HD rendition is rendered, the smallest one previewed, and Pexels' still is the fallback thumbnail.
    assert original_visuals._pick_pixabay_file(hit)["link"] == "https://px.example/11-hd.mp4"
    assert footage_script._preview_rendition(hit)["url"] == "https://px.example/11-sd.mp4"
    assert clip_matcher.pixabay_thumbnail(hit) == "https://images.example/11.jpg"
    downloaded = []
    monkeypatch.setattr(original_visuals, "_download", lambda url, dest, timeout=60: downloaded.append(url) or dest)
    used = set()
    assert original_visuals.download_pixabay_hit(hit, tmp_path, used) == tmp_path / "pexels_11.mp4"
    assert used == {"pexels:11"} and downloaded == ["https://px.example/11-hd.mp4"]


def test_footage_pool_offers_pexels_first_and_keeps_the_libraries_apart(monkeypatch):
    from app.services import footage_script, original_visuals
    pexels = {"owl": [{**_hit(i, "owl", vertical=True), "source": "pexels"} for i in (5, 6)],
              "owl head": [{**_hit(7, "owl", vertical=True), "source": "pexels"}]}
    pixabay = {"owl": [_hit(i, "owl") for i in (5, 8)], "owl head": [_hit(8, "owl"), _hit(9, "owl")]}
    monkeypatch.setattr(original_visuals, "pexels_candidates", lambda q, used, subject=None, limit=15: pexels[q][:limit])
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: pixabay[q][:limit])
    pool = footage_script.gather_pool(["owl", "owl head"], ["owl"], set())
    # Pexels clip 5 and Pixabay clip 5 are different videos; Pixabay clip 8 turns up in both searches but once here.
    assert [original_visuals.hit_key(h) for h in pool] == [
        "pexels:5", "pixabay:5", "pexels:7", "pixabay:8", "pexels:6", "pixabay:9"]
    assert [h["_query"] for h in pool] == ["owl", "owl", "owl head", "owl head", "owl", "owl head"]


def test_zoo_clips_come_after_every_wild_one(monkeypatch):
    from app.services import footage_script, original_visuals
    pexels = [{**_hit(i, tags, vertical=True), "source": "pexels"}
              for i, tags in ((1, "polar, bear, in, a, zoo"), (2, "polar, bear, walking, on, ice"))]
    pixabay = [_hit(3, "polar bear, captivity, enclosure"), _hit(4, "polar bear, arctic, snow")]
    monkeypatch.setattr(original_visuals, "pexels_candidates", lambda q, used, subject=None, limit=15: pexels[:limit])
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: pixabay[:limit])
    pool = footage_script.gather_pool(["polar bear"], ["polar bear"], set())
    assert [original_visuals.hit_key(h) for h in pool] == ["pexels:2", "pixabay:4", "pexels:1", "pixabay:3"]
    # Zoo clips still fill the pool when there is room, and a full pool keeps the wild ones.
    assert [original_visuals.hit_key(h) for h in footage_script.gather_pool(["polar bear"], ["polar bear"], set(), limit=2)] == [
        "pexels:2", "pixabay:4"]


def test_extra_clips_come_from_the_same_search_then_from_matching_tags():
    from app.services.footage_script import fill_clips, wanted_shots
    pool = [{"_query": "lightning strike", "tags": "lightning"}, {"_query": "storm clouds", "tags": "storm, clouds"},
            {"_query": "lightning strike", "tags": "lightning, night"}, {"_query": "rain", "tags": "rain, window"},
            {"_query": "night sky", "tags": "lightning, bolt, night, sky"},
            {"_query": "rain", "tags": "storm, rain, clouds, dark"}]
    scenes = [{"text": "Bir şimşek havayı saniyenin altında güneşten sıcak yapar.", "visual": "lightning bolt at night",
               "clips": [{"n": 1, "kare": 2}]},
              {"text": "Ardından gök gürültüsü gelir.", "visual": "dark storm clouds", "clips": [{"n": 4, "kare": 1}]},
              {"text": "Bulutlar karardıkça gökyüzü gürler, yağmur da hemen arkasından başlar.",
               "visual": "dark storm clouds rolling", "clips": [{"n": 2, "kare": 2}]}]
    assert [wanted_shots(s) for s in scenes] == [2, 1, 2]
    fill_clips(scenes, pool)
    # Scene 1 takes the other "lightning strike" clip over a better-tagged one from another search, scene 2 is
    # short enough for one shot, and scene 3 gets the clip whose tags share most words with what is on screen.
    assert [[ref["n"] for ref in s["clips"]] for s in scenes] == [[1, 3], [4], [2, 6]]


def test_footage_first_gives_up_without_enough_footage(tmp_path, monkeypatch):
    from app.services import footage_script, original_visuals
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: [_hit(1, "cat")])
    monkeypatch.setattr(original_visuals, "pexels_candidates", lambda q, used, subject=None, limit=15: [])
    topic = {"id": "mk-001", "topic": "Kedi", "subject": ["cat"]}
    with pytest.raises(footage_script.ThinFootage) as thin:
        footage_script.write_footage_script(topic, tmp_path, call=lambda p, temperature=0.2: "{}",
                                            plan=lambda t, s, h: ["cat"])
    assert thin.value.found == 1


def test_assigned_clips_start_at_the_frame_the_script_was_written_to(tmp_path, monkeypatch):
    from app.services import original_visuals
    pool = [_hit(i, "cat", duration=12) for i in range(1, 5)]
    monkeypatch.setattr(original_visuals, "download_pixabay_hit",
                        lambda hit, d, used: d / f"pixabay_{hit['id']}.mp4")
    rendered = []
    monkeypatch.setattr(original_visuals, "render_video_segment",
                        lambda video, out, duration, offset: rendered.append((video.name, round(duration, 2), offset)) or out)
    scenes = [{"text": "a", "visual": "a", "clips": [{"n": 2, "kare": 3}, {"n": 4, "kare": 1}]},
              {"text": "b", "visual": "b", "clips": [{"n": 1, "kare": 2}]}]
    result = original_visuals.build_visual_track(scenes, [3.0, 2.0], tmp_path, stock_only=True, segment_seconds=1.6,
                                                 clip_pool=pool)
    # Frames of a 12 s clip sit at 1.2, 4.4 and 7.6 s; each shot starts 0.4 s before its frame.
    assert rendered == [("pixabay_2.mp4", 1.85, 7.2), ("pixabay_4.mp4", 1.85, 0.8), ("pixabay_1.mp4", 2.35, 4.0)]
    assert result["durations"] == [1.5, 1.5, 2.0]
    assert result["clip_match"] == "senaryo görüntüye göre"


def test_whoosh_plays_only_after_the_hook_and_into_the_last_scene(tmp_path, monkeypatch):
    lib = tmp_path / "audio"
    (lib / "sfx").mkdir(parents=True)
    for name in ("whoosh", "impact", "riser"):
        (lib / "sfx" / f"{name}.mp3").write_bytes(b"ID3")
    monkeypatch.setattr(original_factory, "AUDIO_LIBRARY", lib)
    commands = []
    monkeypatch.setattr(original_factory.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    original_factory.mix_soundtrack(tmp_path / "v.m4a", tmp_path / "o.m4a", 30.0, [4.0, 9.0, 14.0, 20.0, 26.0], None)
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    f = original_factory
    assert graph.count(f"volume={f.WHOOSH_VOLUME},adelay") == 2 and "adelay=3800|3800" in graph
    assert "adelay=25800|25800" in graph
    assert f"volume={f.IMPACT_VOLUME}[imp]" in graph and f"volume={f.RISER_VOLUME},adelay" in graph


def test_key_facts_pop_up_in_the_middle_of_the_frame(tmp_path):
    from app.services.subtitle_engine import generate_shorts_ass_subtitles
    ass = tmp_path / "s.ass"
    generate_shorts_ass_subtitles([{"text": "bir iki", "start": 0.0, "end": 9.0}], ass)
    scenes = [{"text": "a"}, {"text": "b", "callout": "270 derece"}, {"text": "c", "callout": "SANİYEDE 340 METRE"},
              {"text": "d", "callout": "KISA"}, {"text": "e", "callout": "14 {OMUR}"}]
    times = original_factory.add_callouts(ass, scenes, [2.0, 3.0, 1.8, 0.9, 2.4])
    # A scene too short for a pop (0.9 s) gets none; the rest appear 0.25 s in and stay up to 1.6 s.
    assert times == [2.25, 5.25, 7.95]
    content = ass.read_text(encoding="utf-8")
    styles, events = content.split("[Events]")
    assert "Style: CalloutStyle,Montserrat Black,118," in styles
    lines = [line for line in events.splitlines() if "CalloutStyle" in line]
    assert lines[0].startswith("Dialogue: 2,0:00:02.25,0:00:03.85,CalloutStyle")
    assert lines[0].endswith("270 DERECE") and "\\fs118\\pos(540,860)" in lines[0]
    # A long key fact gets a smaller font so it stays on one line; it never outlasts its scene.
    assert "\\fs92\\pos" in lines[1] and lines[1].split(",")[2] == "0:00:06.70"
    assert lines[2].endswith("}14 OMUR") and len(lines) == 3
    assert original_factory.add_callouts(ass, [{"text": "a"}], [3.0]) == []


def test_pop_sounds_under_each_key_fact(tmp_path, monkeypatch):
    lib = tmp_path / "audio"
    (lib / "sfx").mkdir(parents=True)
    monkeypatch.setattr(original_factory, "AUDIO_LIBRARY", lib)
    commands = []
    monkeypatch.setattr(original_factory.subprocess, "run", lambda cmd, **kw: commands.append(cmd))
    original_factory.mix_soundtrack(tmp_path / "v.m4a", tmp_path / "o.m4a", 30.0, [4.0, 9.0], None, [2.25, 5.25])
    pop = f"volume={original_factory.POP_VOLUME},adelay"
    assert pop not in commands[-1][commands[-1].index("-filter_complex") + 1]  # no pop.mp3 yet
    (lib / "sfx" / "pop.mp3").write_bytes(b"ID3")
    original_factory.mix_soundtrack(tmp_path / "v.m4a", tmp_path / "o.m4a", 30.0, [4.0, 9.0], None, [2.25, 5.25])
    graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert f"{pop}=2220|2220" in graph and f"{pop}=5220|5220" in graph  # 30 ms ahead of each key fact


def test_merak_script_skips_topics_free_footage_cannot_show(tmp_path, monkeypatch):
    topics = [{"id": f"mk-00{i}", "topic": f"Konu {i}", "stock_hint": "cat", "subject": ["cat"]} for i in (1, 2, 3)]
    monkeypatch.setattr(original_factory, "load_topics", lambda path=None: topics)
    coverage = {"mk-001": 3, "mk-002": 8}

    def footage(topic, work_dir):
        return {"coverage": coverage[topic["id"]], "script": {"title": topic["topic"]}, "pool": ["clip"]}

    topic, script, provider, pool = original_factory.merak_script(topics[0], set(), False, data_dir=tmp_path,
                                                                  footage=footage)
    assert topic["id"] == "mk-002" and pool == ["clip"] and "görüntüye göre" in provider
    assert original_factory.skipped_topic_ids(tmp_path) == {"mk-001"}
    # Asked for by id: made even with weak footage.
    topic, *_ = original_factory.merak_script(topics[0], set(), True, data_dir=tmp_path, footage=footage)
    assert topic["id"] == "mk-001"
    # No footage script at all: the script-first flow takes over.
    topic, script, provider, pool = original_factory.merak_script(
        topics[2], set(), False, data_dir=tmp_path, footage=lambda t, d: None,
        fallback=lambda topic, **kw: ({"title": "eski yol"}, "gemini"))
    assert pool is None and script == {"title": "eski yol"} and topic["id"] == "mk-003"


def test_merak_script_skips_a_topic_the_libraries_have_too_few_clips_for(tmp_path, monkeypatch):
    from app.services import footage_script
    topics = [{"id": f"mk-00{i}", "topic": f"Konu {i}", "stock_hint": "cat", "subject": ["cat"]} for i in (1, 2)]
    monkeypatch.setattr(original_factory, "load_topics", lambda path=None: topics)

    def footage(topic, work_dir):
        if topic["id"] == "mk-001":
            raise footage_script.ThinFootage(11)
        return {"coverage": 8, "script": {"title": topic["topic"]}, "pool": ["clip"]}

    topic, script, provider, pool = original_factory.merak_script(topics[0], set(), False, data_dir=tmp_path,
                                                                  footage=footage)
    # The octopus sample (2026-10-15) went script-first instead and left scenes without a matching clip.
    assert topic["id"] == "mk-002" and pool == ["clip"]
    assert original_factory.skipped_topic_ids(tmp_path) == {"mk-001"}
    # Asked for by id: the script-first flow still makes it.
    topic, script, provider, pool = original_factory.merak_script(
        topics[0], set(), True, data_dir=tmp_path / "explicit", footage=footage,
        fallback=lambda topic, **kw: ({"title": "eski yol"}, "gemini"))
    assert topic["id"] == "mk-001" and pool is None and script == {"title": "eski yol"}


def test_audio_library_replaces_only_the_requested_series(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_audio_library", original_factory.BASE_DIR / "scripts" / "build_audio_library.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    monkeypatch.setattr(lib, "LIBRARY", tmp_path)
    (tmp_path / "music").mkdir()
    (tmp_path / "sfx").mkdir()
    for name in ("merak_1", "story_1"):
        (tmp_path / "music" / f"{name}.mp3").write_bytes(b"old")
    for name in lib.SFX:
        (tmp_path / "sfx" / f"{name}.mp3").write_bytes(b"old")
    made = []
    monkeypatch.setattr(lib, "generate", lambda path, key, payloads, dest: made.append(dest.name) or "yazıldı")
    monkeypatch.setattr(lib, "credits_used", lambda key: 0)
    monkeypatch.setattr(lib, "is_silent", lambda path: path.name == "pop.mp3")
    import app.services.elevenlabs_engine as eleven
    monkeypatch.setattr(eleven, "get_elevenlabs_api_key", lambda: "k")
    assert lib.main(["merak", "--replace"]) == 0
    # The silent pop counts as missing and is made again; the other effects are kept.
    assert made == ["merak_1.mp3", "merak_2.mp3", "merak_3.mp3", "pop.mp3"]
    made.clear()
    lib.main(["--replace"])  # no series: only missing tracks, never the whole library
    assert "merak_1.mp3" not in made and "story_1.mp3" not in made and "story_2.mp3" in made


def test_audio_library_tries_again_when_a_sound_comes_back_silent(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_audio_library", original_factory.BASE_DIR / "scripts" / "build_audio_library.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    replies = [b"ID3 quiet", b"ID3 loud"]
    sent = []
    monkeypatch.setattr(lib, "_request", lambda path, key, payload=None: sent.append(payload) or replies.pop(0))
    monkeypatch.setattr(lib, "peak_db", lambda path: -46.4 if path.read_bytes() == b"ID3 quiet" else -0.5)
    dest = tmp_path / "pop.mp3"
    assert lib.generate("/sound-generation", "k", [{"text": "pop", "duration_seconds": 1.0}, {"text": "pop"}],
                        dest).startswith("yazıldı")
    assert dest.read_bytes() == b"ID3 loud" and sent[1] == {"text": "pop"}
    replies[:] = [b"ID3 quiet"]
    assert lib.generate("/sound-generation", "k", [{"text": "pop"}], dest) == "hata (sessiz dosya döndü)"
    assert not dest.exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_audio_library_hears_the_difference_between_a_pop_and_silence(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_audio_library", original_factory.BASE_DIR / "scripts" / "build_audio_library.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    for name, volume in (("loud", "1.0"), ("quiet", "0.004")):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=900:duration=0.3", "-af", f"volume={volume}",
                        str(tmp_path / f"{name}.mp3")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert not lib.is_silent(tmp_path / "loud.mp3") and lib.is_silent(tmp_path / "quiet.mp3")
    (tmp_path / "broken.mp3").write_bytes(b"old")
    assert lib.peak_db(tmp_path / "broken.mp3") is None and not lib.is_silent(tmp_path / "broken.mp3")
