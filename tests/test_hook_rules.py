"""Opening and ending rules for curiosity scripts (footage-first flow).

11 of 14 merak first lines were questions, the public owl Short (Tepqt4MN0lE) asked its question three times,
filler openers got through and 9 of 14 scripts ended on "Yani...". These tests pin the checks that send such a
script back to Gemini, and that a script breaking them is still used when no attempt is clean.
"""

import copy
import json

import pytest

from app.services import footage_script, original_visuals
from app.services.footage_script import MERAK_MAX_WORDS, hook_problems, tr_lower
from app.services.original_script import ENDING_RULE, MIN_WORDS, OPENING_RULE, build_prompt, validate_script

TOPIC = {"id": "mk-099", "topic": "Baykuşlar başlarını nasıl bu kadar çevirebiliyor?", "stock_hint": "owl",
         "subject": ["owl"]}

# 64 words; the last line runs back into the first when the Short replays.
OWL_SCENES = [
    ("Bu baykuş boynunu kırmadan 270 derece döner.", "270 DERECE"),
    ("Bizim boynumuz bu hareketi denese damarlarımız sıkışır, beynimize giden kan durur.", ""),
    ("Baykuşun boynunda bizdekinin iki katı, tam on dört omur var.", "14 OMUR"),
    ("Ama asıl şaşırtıcı olan, boyun damarlarının etrafındaki geniş kemik kanalları.", ""),
    ("Damarlar bu kanallarda rahatça kayar, kan da küçük ceplerde birikip beyni beslemeye devam eder.", ""),
    ("Gözleri yuvasında hiç dönmediği için başını çevirmek zorunda, ve işte bu yüzden", ""),
]
OWL_QUESTION = "Baykuşlar başlarını neden bu kadar geriye çevirebiliyor?"  # the public owl Short's first line


def _owl() -> dict:
    """A validated-shape script: scenes without a key fact on screen carry no "callout"."""
    return {"hook_text": "BOYNU NEDEN KIRILMIYOR?",
            "scenes": [{"text": text, **({"callout": callout} if callout else {})} for text, callout in OWL_SCENES]}


def _reply(first=None, first_callout=None, last=None, thin=False) -> dict:
    """A Gemini answer for the owl topic; every scene gets two clips of its own unless `thin`."""
    scenes = [{"text": text, "visual": "an owl turning its head", "callout": callout, "clips": [2 * i + 1, 2 * i + 2]}
              for i, (text, callout) in enumerate(OWL_SCENES)]
    if first is not None:
        scenes[0]["text"] = first
    if first_callout is not None:
        scenes[0]["callout"] = first_callout
    if last is not None:
        scenes[-1]["text"] = last
    if thin:
        scenes[1]["clips"] = [3]
    return {"coverage": 8, "mood": "belgesel", "title": "Baykuş boynunu nasıl bu kadar çeviriyor? 🦉",
            "hook_text": "BOYNU NEDEN KIRILMIYOR?", "scenes": scenes,
            "description": "Baykuşların boynunu anlatıyoruz.", "tags": ["baykuş"],
            "pinned_comment": "Sence insan boynu kaç derece döner: 90 mı 180 mi?", "en_title": "Owl",
            "en_description": "Owl."}


def test_a_claim_opener_with_a_key_fact_and_a_looping_end_passes():
    # The banner shares only "boynu" with the first line (1 of 2 content words), so it asks from another angle.
    assert hook_problems(_owl()) == []
    assert hook_problems(validate_script(_reply(), TOPIC)) == []


def test_the_owl_question_opener_is_sent_back():
    script = _owl()
    script["scenes"][0] = {"text": OWL_QUESTION}
    script["hook_text"] = "BAYKUŞLAR BAŞLARINI NEDEN ÇEVİREBİLİYOR?"
    problems = hook_problems(script)
    assert len(problems) == 3
    assert "ilk cümle soru olmasın" in problems[0] and "callout" in problems[1] and "tekrarlıyor" in problems[2]


@pytest.mark.parametrize("filler, phrase", [
    ("İnanılmaz değil mi?", "inanılmaz değil mi"),
    ("İşte şaşırtıcı gerçek!", "işte şaşırtıcı gerçek"),
    ("Açıklayalım.", "açıklayalım"),
])
def test_real_filler_openers_are_flagged_in_any_scene(filler, phrase):
    note = f'"{phrase}" kalıbını kullanma'
    script = _owl()
    script["scenes"][2]["text"] = f"{filler} {script['scenes'][2]['text']}"
    assert hook_problems(script) == [note]
    script = _owl()
    script["scenes"][0]["text"] = filler
    assert note in hook_problems(script)
    script["scenes"][0]["text"] = filler.replace("i", "İ").replace("ı", "I").upper()  # shouted Turkish
    assert note in hook_problems(script)


def test_suffixed_forms_of_a_banned_opener_are_flagged():
    for text, phrase in (("Biliyor muydunuz, bu baykuş boynunu 270 derece çevirir.", "biliyor muydun"),
                         ("Merhabalar, bu baykuş boynunu 270 derece çevirir.", "merhaba")):
        script = _owl()
        script["scenes"][0]["text"] = text
        assert f'"{phrase}" kalıbını kullanma' in hook_problems(script)


def test_turkish_lowercasing_keeps_dotless_i():
    assert tr_lower("KIRILMIYOR İNANILMAZ") == "kırılmıyor inanılmaz"


def test_an_ending_that_sums_up_is_sent_back():
    for opener in ("Yani", "Bu sayede", "Sonuç olarak"):
        script = _owl()
        script["scenes"][-1]["text"] = f"{opener} baykuş başını çevirmek zorunda, çünkü gözleri yuvasında dönmez."
        problems = hook_problems(script)
        assert len(problems) == 1 and f'"{tr_lower(opener)}" ile başlamasın' in problems[0]
    # The owl Short's real ending, and a summing-up second sentence in the last scene.
    for last, opener in (("Bu adaptasyon sayesinde avlarını kaçırmaz, çevrelerini her zaman kontrol altında tutarlar.",
                          "bu adaptasyon sayesinde"),
                         ("Gözleri yuvasında dönmez. Yani başını çevirmek zorunda.", "yani")):
        script = _owl()
        script["scenes"][-1]["text"] = last
        assert hook_problems(script) == [
            f'son cümle "{opener}" ile başlamasın, özetlemeden ilk cümleye bağlanan şaşırtıcı bir sonuçla bitsin']
    # Inside the sentence the word is fine.
    script = _owl()
    script["scenes"][-1]["text"] = "Gözleri yuvasında dönmez, yani başını çevirmek zorunda ve işte bu yüzden"
    assert hook_problems(script) == []


def test_a_banner_repeating_the_first_line_is_sent_back():
    script = _owl()
    script["scenes"][0] = {"text": "Bu baykuş başını 270 derece çevirir.", "callout": "270 DERECE"}
    script["hook_text"] = "BAYKUŞ BAŞINI NASIL ÇEVİRİR?"
    assert hook_problems(script) == [
        "hook_text ilk cümleyi tekrarlıyor, soruyu başka bir açıdan ve başka kelimelerle sor"]


def test_a_long_opener_and_a_long_script_are_sent_back():
    script = _owl()
    script["scenes"][0]["text"] = "Bu baykuş boynunu hiçbir yerini kırmadan tam 270 derece döndürebilir."
    script["scenes"][4]["text"] += " Üstelik bu kanallar boynun her iki yanında da vardır ve kan akışını hiç kesmez."
    problems = hook_problems(script)
    assert problems == ["ilk cümle 10 kelime, en fazla 8 kelime olsun",
                        f"anlatım 81 kelime, en fazla {MERAK_MAX_WORDS} kelime olsun"]


def _hit(vid):
    rendition = {"url": f"https://cdn.example/{vid}.mp4", "width": 1920, "height": 1080, "size": 1000,
                 "thumbnail": f"https://cdn.example/{vid}.jpg"}
    return {"id": vid, "tags": "owl", "type": "film", "duration": 12, "videos": {"medium": rendition}}


def _run(tmp_path, monkeypatch, replies, **kw):
    """write_footage_script with its default attempts, 14 fake owl clips and scripted Gemini answers."""
    hits = {"owl": [_hit(i) for i in range(1, 11)], "owl head": [_hit(i) for i in range(5, 15)]}
    monkeypatch.setattr(original_visuals, "pixabay_candidates", lambda q, used, subject=None, limit=50: hits[q][:limit])
    monkeypatch.setattr(original_visuals, "pexels_candidates", lambda q, used, subject=None, limit=15: [])
    replies, prompts = [r if isinstance(r, str) else json.dumps(r) for r in replies], []

    def fake_call(parts, temperature=0.2):
        prompts.append(parts)
        return replies.pop(0)

    result = footage_script.write_footage_script(
        TOPIC, tmp_path, call=fake_call, plan=lambda t, s, h: ["owl", "owl head"],
        sheet=lambda hit, d: (b"\xff\xd8\xff", "image/jpeg"), check=lambda script, topic: [], **kw,
    )
    return result, prompts


def test_a_question_opener_is_asked_again_and_the_clean_answer_wins(tmp_path, monkeypatch, capsys):
    result, prompts = _run(tmp_path, monkeypatch, [_reply(first=OWL_QUESTION, first_callout=""), _reply()])
    assert len(prompts) == 2 and prompts[0][1]["text"] == "Aday 1 (12 sn):"
    note = prompts[1][1]["text"]
    assert note.startswith("Önceki yanıtın reddedildi: ilk cümle soru olmasın") and "callout koy" in note
    assert "reddedildi (#1): ilk cümle soru olmasın" in capsys.readouterr().out
    script = result["script"]
    assert script["scenes"][0]["text"] == OWL_SCENES[0][0] and script["scenes"][0]["callout"] == "270 DERECE"
    assert script["hook_text"] == "BOYNU NEDEN KIRILMIYOR?"


def test_the_best_answer_is_used_when_no_attempt_follows_every_rule(tmp_path, monkeypatch, capsys):
    yani = "Yani baykuş başını çevirmek zorunda, çünkü gözleri yuvasında hiç dönmez."
    replies = [_reply(thin=True),  # hook rules kept, but scene 2 has a single clip
               _reply(last=yani),  # one hook problem
               _reply(first=OWL_QUESTION, first_callout="")]  # two hook problems
    result, prompts = _run(tmp_path, monkeypatch, copy.deepcopy(replies))
    assert result is not None and len(prompts) == 3
    # Missing footage ranks below a weak line; among full answers the fewest problems win, not the last answer.
    assert result["script"]["scenes"][-1]["text"] == yani and result["script"]["scenes"][0]["text"] == OWL_SCENES[0][0]
    assert "2. sahnede yeterli video yok" in prompts[1][1]["text"]
    # Each retry carries one note, about the answer before it only.
    assert '"yani" ile başlamasın' in prompts[2][1]["text"] and "video yok" not in prompts[2][1]["text"]
    assert prompts[2][2]["text"] == "Aday 1 (12 sn):"
    assert "en iyi deneme (#2) kullanılıyor" in capsys.readouterr().out


def test_no_parsable_answer_still_gives_up(tmp_path, monkeypatch):
    result, prompts = _run(tmp_path, monkeypatch, ["{}", "{}", "{}"])
    assert result is None and len(prompts) == 3


def test_prompts_ask_for_a_short_script_that_opens_on_a_claim_and_loops(tmp_path, monkeypatch):
    _, prompts = _run(tmp_path, monkeypatch, [_reply()])
    prompt = prompts[0][0]["text"]
    assert MERAK_MAX_WORDS == 80
    assert "22-30 saniyelik" in prompt and "25-40" not in prompt and f"Toplam {MIN_WORDS}-80 kelime" in prompt
    assert OPENING_RULE in prompt and ENDING_RULE in prompt and "12 kelimelik" not in prompt
    assert "90 mı 180 mi?" in prompt and "yorumlarda buluşalım" in prompt
    # The script-first fallback for the same series follows the same opening and ending.
    merak = build_prompt(TOPIC["topic"], None, None, TOPIC)
    assert OPENING_RULE in merak and ENDING_RULE in merak and "12 kelimelik" not in merak
    assert OPENING_RULE not in build_prompt("Seneca neden servetini geri verdi?")
