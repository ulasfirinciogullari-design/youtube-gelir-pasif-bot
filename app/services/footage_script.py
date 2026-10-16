"""
Footage-first scripts for the curiosity series.

Writing the narration first and looking for footage afterwards gave videos that
looked "filled with random cat clips" (Ulaş, 2026-10-13): a free library rarely
holds the exact moment a sentence describes. This goes the other way round:
gather on-subject Pixabay clips, show Gemini three frames of each, and let it
write the narration around what the clips actually show, naming for every scene
the clips and the frame that fits the sentence. Gemini also rates how well the
footage covers the topic, so a topic the library cannot show can be skipped.

One text call plans the searches, one vision call writes the script and one more
text call re-reads it as a science editor, all on the Gemini free tier. Any
failure returns None and the caller falls back to the script-first flow.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import subprocess
import urllib.error

from app.services import original_visuals
from app.services.clip_matcher import _call_gemini_parts, fetch_thumbnail, pixabay_thumbnail
from app.services.original_script import (
    EMOJI_RE, MAX_SCENE_CHARS, MAX_WORDS, ScriptError, _call_gemini, _extract_json,
    ENDING_RULE, MOOD_RULE, OPENING_RULE, clean_callout, validate_script,
)

MIN_COVERAGE = 6  # out of 10; below this a topic is skipped when the run picked it itself
WORDS_PER_SHOT = 4  # ~1.6 s of narration: the reference Shorts cut about this often
MIN_POOL = 12
MAX_POOL = 40
# Tags of clips shot in a zoo. The polar bear Short (Q7Fdwf2i1KI, 2026-10-15) was all painted walls and concrete pens;
# such clips now come after every wild one and fill the pool only when the wild ones run out.
CAPTIVE_TAGS = {"zoo", "zoos", "captivity", "captive", "cage", "caged", "enclosure"}
FRAME_HEIGHT = 200
# Every public merak Short stopped near 1,000 views (2026-10-16): the feed shows a Short to a first group and only
# goes on when they watch it through. A 15-20 s Short (~3.3 words/s with Alp at speed 1.15) is watched through and
# replayed more often than the 22-30 s ones were. validate_script keeps MAX_WORDS as the hard limit and
# MERAK_MIN_WORDS as the floor; going over MERAK_MAX_WORDS is only a reason to ask again (hook_problems).
MERAK_MIN_WORDS = 38
MERAK_MAX_WORDS = 55
MERAK_MIN_SCENES = 4
MERAK_MAX_SCENES = 6
MAX_OPENING_WORDS = 9  # the prompt asks for at most 8; one word of slack before the opener is sent back
HOOK_REPEAT_SHARE = 0.6  # share of the banner's content words that may not reappear in the spoken first line
# Fillers that slipped through in real merak scripts ("İnanılmaz değil mi?", "İşte şaşırtıcı gerçek!",
# "Açıklayalım.", 2026-10) and the openers and lecture phrases the prompt already forbids. They match from the
# start of a word, so suffixed forms ("Biliyor muydunuz", "Merhabalar") count too.
# "değil mi" also caught the cat Short's second line, a sentence that only asked the viewer to agree
# ("Genellikle mırlamayı mutlulukla ilişkilendiririz, değil mi?", LZUWW_pFBIw).
BANNED_PHRASES = ("biliyor muydun", "merhaba", "açıklayalım", "bunun nedeni şudur", "değil mi",
                  "işte şaşırtıcı gerçek", "hepimiz biliriz")
# A last line opening with one of these summed the video up ("Yani...", 9 of 14 scripts) instead of looping back.
# The owl Short's own ending was the longer form "Bu adaptasyon sayesinde...", so "bu ... sayesinde" counts as well.
SUMMARY_OPENERS = ("yani", "böylece", "kısacası", "bu sayede", "sonuç olarak", "özetle")
# A last line opening on praise closed the video on a platitude instead of a fact (dolphin sample, 2026-10-15: "İnanılmaz
# bir dengeyle hem uyur hem de..."). Matched from the start of the first word, so "Muhteşem" and "Harikadır" count.
PRAISE_OPENERS = ("inanılmaz", "muhteşem", "harika", "mükemmel", "müthiş", "şaşırtıcı")
# The loop connector belongs at the very end ("...ve işte bu yüzden" -> first line). The plane Short (WADWjFRfYYw,
# 2026-10-15) opened its last sentence with it and summed up instead: "İşte bu yüzden, camların yuvarlak olması hayati
# önem taşır." A connector followed by more words, or a last sentence on how important something is, counts as a summary.
REASON_OPENERS = ("işte bu yüzden", "bu yüzden", "işte bu nedenle", "bu nedenle")
IMPORTANCE_PHRASES = ("önem taşır", "hayati önem", "ne kadar önemli", "çok önemli")
# Question and filler words a banner may share with the first line without repeating it.
HOOK_STOPWORDS = {"neden", "nasıl", "ne", "niye", "niçin", "bu", "şu", "o", "bir", "ve", "ile", "mi", "mı", "mu", "mü",
                  "da", "de", "ki", "en", "çok", "kadar", "hep", "her"}

QUERY_PROMPT = """Pixabay'de stok video arayacağım. Konu: {topic}
Videoda görünecek ana özne: {subject}
Bu konuyu anlatan bir Shorts videosu için gerekecek görüntüleri bulacak 8 farklı arama yaz. Her biri 2-3 basit İngilizce
kelime olsun ve ana öznelerden birini içersin (ör. "cat jumping", "cat landing", "kitten playing"). En az 3'ü öznenin
ekranı dolduracağı yakın çekim arasın (ör. "cat face close", "owl eyes close up").
Sadece şu JSON'u döndür: {{"queries": ["..."]}}"""

SCRIPT_PROMPT = """Sen "Zirvenin Kanunu" adlı Türkçe YouTube Shorts kanalının baş senaristisin. Bu seride bilim, hayvanlar,
insan vücudu ve psikoloji hakkında merak edilen soruları gerçek stok videolarla anlatıyoruz (Keşif Vakti tarzı).

Konu: {topic}

Aşağıda numaralı aday videolar var. Her adaydan soldan sağa üç kare görüyorsun: 1 = başı, 2 = ortası, 3 = sonu.
Önce bütün adaylara bak, sonra SADECE bu adaylarla gösterilebilecek 15-20 saniyelik bir senaryo yaz.

En önemli kural: Her sahnede söylenen cümle, o sahnenin videolarında görünen şeyle birebir örtüşsün. İzleyici cümleyi
duyarken anlatılanı ekranda görmeli. Videolarda olmayan bir anı anlatma; konuyu, videolarda gerçekten görünen hareketler
ve ayrıntılar üzerinden açıkla (ör. düşen kedi görüntüsü yoksa zıplayan ve yere inen kediyi anlat).

Anlatım kuralları:
""" + OPENING_RULE + """- Toplam {min_words}-{max_words} kelime, {min_scenes}-{max_scenes} sahne. Her sahne 1-2 kısa cümle.
- Akış: ekrandaki iddia, sorunun açılması, şaşırtıcı cevap, cevabı somutlaştıran örnek, başa bağlanan son cümle.
- Her cümle yeni bir bilgi versin. İzleyiciden onay isteyen ("..., değil mi?") ya da herkesin bildiğini tekrar eden
  ("Hepimiz biliriz ki...", "Genellikle ... düşünürüz") dolgu cümle yazma; video kısa, her kelime işe yarasın.
""" + ENDING_RULE + """- Enerjik ve samimi konuş; yeri gelince ekrana gönderme yap ("şu kediye bakın", "gördüğünüz gibi").
- Ders anlatır gibi değil: "Açıklayalım", "Bunun nedeni şudur" gibi kalıplar yok. Kısa, vurucu cümleler kur; her bir iki
  cümlede yeni bir şaşırtıcı bilgi ver ve ortada bir dönüş yap ("Ama asıl şaşırtıcı olan..." gibi).
- Sadece bilimsel olarak doğrulanmış bilgiler. Sayı ve karşılaştırma (süre, yüzde, ölçü, "milyon kat", "iki katı") yalnızca
  ansiklopedilerde geçen, kesin bilinen bir değerse kullan; emin değilsen sayı verme.
- Anlatımda emoji kullanma, metin seslendirilecek.

Görüntü kuralları:
- Video her 1,5 saniyede bir kesilecek. Bu yüzden her sahneye 2 ya da 3 FARKLI aday ver (8 kelimeden uzun sahnelere 3),
  hepsi o cümleyi göstersin; aynı anın farklı çekimleri de olur. Sadece 5 kelimeden kısa sahnelere tek aday verebilirsin.
- "clips": o sahnede sırayla gösterilecek adaylar, her biri {{"n": aday numarası, "kare": cümleye en uyan kare (1, 2 ya da 3)}}.
  Bir aday yalnızca bir sahnede kullanılabilir.
- Yazı, logo, filigran olan, bulanık veya konuyla ilgisiz adayları kullanma.
- Ana özne ekranda büyük ve net görünsün. Öznenin uzakta nokta gibi kaldığı ya da karede hiç görünmediği (boş deniz,
  boş gökyüzü, uzak manzara) adayları kullanma; yakın çekimleri tercih et.
- Hayvanat bahçesinde çekildiği belli olan (kafes, çit, boyalı duvar, beton havuz) adayları, doğal ortamda çekilmiş aday
  varken kullanma.
- "visual": o sahnede ekranda görünen şeyin kısa İngilizce tarifi.
- "callout": o sahnede ekranın ortasında büyük harflerle beliren 1-3 kelimelik anahtar bilgi ya da birimiyle bir sayı
  (ör. "270 DERECE", "14 OMUR", "SANİYEDE 340 METRE"). Cümledeki bilgiyle birebir aynı olsun. Sadece en çarpıcı 3-4 sahneye
  koy, diğer sahnelerde "" bırak.
- "coverage": adayların bu konuyu ne kadar iyi gösterebildiği, 1-10. Konunun ana sorusunu gösteren aday yoksa 5'in altında ver.

Başlık ve kanca:
- "title": en fazla 60 karakter; merak uyandıran soru veya şaşırtıcı iddia ve sonunda tek bir uygun emoji. #shorts yazma.
- "hook_text": konunun sorusunu soran 3-7 kelimelik büyük yazı (ör. "KEDİLER NEDEN HEP AYAKTA DÜŞER?"); video boyunca
  ekranın üstünde durur, emoji yok.
- "pinned_comment": bu videonun şaşırtıcı bilgisine bağlı, iki seçenekli bir tahmin sorusu (ör. "Sence insan boynu kaç
  derece döner: 90 mı 180 mi?"). Sadece bu videoya özgü olsun; "yorumlarda buluşalım" gibi genel bir çağrı yazma.
""" + MOOD_RULE + """
Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "coverage": 8,
  "mood": "belgesel",
  "title": "Türkçe başlık + tek emoji",
  "hook_text": "3-7 kelimelik kanca yazısı",
  "scenes": [
    {{"text": "Türkçe anlatım", "visual": "what is on screen", "callout": "270 DERECE",
      "clips": [{{"n": 4, "kare": 2}}, {{"n": 11, "kare": 1}}, {{"n": 17, "kare": 3}}]}}
  ],
  "description": "2-3 cümlelik Türkçe açıklama",
  "tags": ["8-12 Türkçe etiket"],
  "pinned_comment": "Videonun bilgisine bağlı iki seçenekli tahmin sorusu",
  "en_title": "English title, max 70 chars",
  "en_description": "1-2 sentence English description"
}}"""


FACT_PROMPT = """Bir bilim editörüsün. Aşağıdaki Türkçe Shorts anlatımı yayına girmeden önce son kez kontrol ediliyor.
Konu: {topic}

{scenes}

Köşeli parantezdeki "ekranda" yazıları o sahnede ekrana büyük harflerle çıkacak.
Her cümleyi ve ekran yazısını tek tek kontrol et: yanlış, abartılı veya yanıltıcı bir bilgi (sayı, oran, karşılaştırma,
neden-sonuç) var mı?
Örnek: "kalp günde 1 milyon kez atar" yanlıştır, yaklaşık 100 bin kezdir.
Hatalı her sahne için düzeltilmiş halini yaz: aynı konuyu ve aynı görüntüyü anlatan, yaklaşık aynı uzunlukta, sadece hatalı
kısmı değiştiren bir cümle; sahnenin ekran yazısı varsa onun doğru halini de "callout" olarak ver. Doğru sahneleri listeye
koyma; hata yoksa boş liste döndür.
Sadece şu JSON'u döndür: {{"fixes": [{{"scene": 3, "text": "düzeltilmiş cümle", "callout": "", "why": "kısa gerekçe"}}]}}"""


def plan_queries(topic: str, subject: list[str], hint: str = "", call=_call_gemini) -> list[str]:
    """Searches that should find the footage the topic needs; the stock hint and subject terms always follow."""
    queries: list[str] = []
    try:
        raw = call(QUERY_PROMPT.format(topic=topic, subject=", ".join(subject) or hint))
        queries = [" ".join(str(q).split())[:60] for q in (_extract_json(raw).get("queries") or [])] if raw else []
    except (ScriptError, ValueError, urllib.error.URLError, TimeoutError) as e:
        print(f"ℹ️ Arama planı notu: {str(e)[:200]}")
    out: list[str] = []
    for q in [*queries, hint, *subject]:
        if q and q.lower() not in [o.lower() for o in out]:
            out.append(q)
    return out[:12]


def gather_pool(queries: list[str], subject: list[str], used_ids: set, limit: int = MAX_POOL) -> list[dict]:
    """On-subject candidates from every query and both libraries, interleaved so each search contributes
    its best clips; Pexels' vertical HD clips come first within a rank. Each hit remembers the search that
    found it (`_query`), which later helps fill a scene with similar shots. Zoo clips come after all wild ones."""
    lists = []
    for q in queries:
        for search in (original_visuals.pexels_candidates, original_visuals.pixabay_candidates):
            # Wild clips first before each search keeps its best 10, so a page of zoo hits cannot push them out.
            hits = sorted(search(q, used_ids, subject), key=is_captive)[:10]
            lists.append([{**h, "_query": q} for h in hits])
    pool, seen = [], set()
    for rank in range(10):
        for hits in lists:
            if rank < len(hits) and original_visuals.hit_key(hits[rank]) not in seen:
                seen.add(original_visuals.hit_key(hits[rank]))
                pool.append(hits[rank])
    pool.sort(key=is_captive)  # stable: the interleaved order holds within wild and within zoo clips
    return pool[:limit]


def is_captive(hit: dict) -> bool:
    return bool(original_visuals._words(hit.get("tags", "")) & CAPTIVE_TAGS)


def frame_time(duration: float, frame: int) -> float:
    """Where frame 1-3 of a contact sheet sits in the clip (sheets sample 10 %, 37 % and 63 %)."""
    return 0.1 * duration + (min(3, max(1, frame)) - 1) * 0.8 * duration / 3


def _preview_rendition(hit: dict) -> dict | None:
    renditions = [r for r in (hit.get("videos") or {}).values() if isinstance(r, dict) and r.get("url") and r.get("size")]
    return min(renditions, key=lambda r: r["size"]) if renditions else None


def contact_sheet(hit: dict, work_dir: Path) -> tuple[bytes, str] | None:
    """Three frames of the clip side by side from its smallest rendition; the Pixabay thumbnail if that fails."""
    preview = _preview_rendition(hit)
    duration = float(hit.get("duration") or 0)
    if preview and duration >= 1:
        name = original_visuals.hit_key(hit).replace(":", "_")
        src, out = work_dir / f"preview_{name}.mp4", work_dir / f"sheet_{name}.jpg"
        try:
            original_visuals._download(preview["url"], src, timeout=60)
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{frame_time(duration, 1):.2f}", "-i", str(src),
                 "-vf", f"fps=3/{0.8 * duration:.3f},scale=-2:{FRAME_HEIGHT},tile=3x1",
                 "-frames:v", "1", "-q:v", "4", str(out)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
            )
            return out.read_bytes(), "image/jpeg"
        except Exception:
            pass
    return fetch_thumbnail(pixabay_thumbnail(hit))


def wanted_shots(scene: dict) -> int:
    """How many different clips a scene's narration needs for the reference Shorts' ~1.5 s cuts (1-3)."""
    return min(3, max(1, round(len(scene["text"].split()) / WORDS_PER_SHOT)))


def resolve_clips(scenes: list[dict], shown: int) -> list[dict]:
    """Keeps clip numbers that exist and are not used by an earlier scene. A scene can end up with none
    (Gemini gave its clips to an earlier scene); like a thin scene it is asked about again, then filled.
    Rejecting the whole script for it sent the owl sample (2026-10-14) back to the old script-first flow."""
    taken: set[int] = set()
    for scene in scenes:
        refs = []
        for ref in scene.get("clips") or []:
            if 1 <= ref["n"] <= shown and ref["n"] not in taken:
                taken.add(ref["n"])
                refs.append(ref)
        scene["clips"] = refs
    return scenes


def thin_scenes(scenes: list[dict]) -> list[int]:
    """Scenes (from 1) without a clip of their own, or long enough for several cuts with a single clip: one long
    shot per sentence made the lightning sample (2026-10-13) feel slow next to the reference Shorts."""
    return [i for i, scene in enumerate(scenes, start=1) if len(scene["clips"]) < min(2, wanted_shots(scene))]


def tr_lower(text: str) -> str:
    """Turkish lowercase: Python's lower() turns "I" into "i" and "İ" into "i" plus a combining dot."""
    return str(text or "").replace("I", "ı").replace("İ", "i").lower()


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", tr_lower(text))


def _content_prefixes(text: str) -> list[str]:
    """Words that carry meaning, cut to 5 letters so Turkish suffixes still match (boynu / boynunu)."""
    return [w[:5] for w in _words(text) if w not in HOOK_STOPWORDS]


def _summary_opener(text: str) -> str | None:
    """The summing-up connector a line opens with ("yani", "bu adaptasyon sayesinde"), or None."""
    words = _words(text)
    line = " ".join(words) + " "
    opener = next((o for o in SUMMARY_OPENERS if line.startswith(o + " ")), None)
    if not opener:  # the connector alone ("İşte bu yüzden.") is the loop, not a summary
        opener = next((o for o in REASON_OPENERS if line.startswith(o + " ") and len(words) > len(o.split())), None)
    if not opener and words[:1] == ["bu"] and "sayesinde" in words[1:5]:
        opener = " ".join(words[:words.index("sayesinde") + 1])
    return opener


def hook_problems(script: dict) -> list[str]:
    """What breaks the opening and ending rules, each as a short note Gemini can be asked to fix.

    11 of 14 merak first lines were questions, filler openers got through ("İnanılmaz değil mi?"), only 4 of 14
    first scenes had a key fact on screen, the owl Short's banner repeated its first line, and 9 of 14 endings
    summed up ("Yani..."). None of this makes a script unusable, so it only ever leads to another attempt."""
    scenes = script.get("scenes") or []
    if not scenes:
        return []
    first, last = scenes[0]["text"], scenes[-1]["text"]
    problems = []
    if "?" in first:
        problems.append("ilk cümle soru olmasın, ekrandakini gösteren ve sayı ya da çelişki içeren bir iddia olsun")
    if len(first.split()) > MAX_OPENING_WORDS:
        problems.append(f"ilk cümle {len(first.split())} kelime, en fazla {MAX_OPENING_WORDS - 1} kelime olsun")
    if not scenes[0].get("callout"):
        problems.append("ilk sahneye iddiadaki sayıyı ya da anahtar bilgiyi gösteren bir callout koy")
    said = [" " + " ".join(_words(scene["text"])) + " " for scene in scenes]
    problems += [f'"{phrase}" kalıbını kullanma' for phrase in BANNED_PHRASES if any(f" {phrase}" in s for s in said)]
    banner, spoken = _content_prefixes(script.get("hook_text", "")), {w[:5] for w in _words(first)}
    if banner and sum(1 for w in banner if w in spoken) / len(banner) >= HOOK_REPEAT_SHARE:
        problems.append("hook_text ilk cümleyi tekrarlıyor, soruyu başka bir açıdan ve başka kelimelerle sor")
    # The rule is about the last sentence, and a last scene can hold two ("Gözleri dönmez. Yani...").
    last_sentence = re.split(r"(?<=[.!?…])\s+", last.strip())[-1]
    opener = _summary_opener(last) or _summary_opener(last_sentence)
    if opener:
        problems.append(f'son cümle "{opener}" ile başlamasın, '
                        'özetlemeden ilk cümleye bağlanan şaşırtıcı bir sonuçla bitsin')
    praise = next((w for w in _words(last_sentence)[:1] if w.startswith(PRAISE_OPENERS)), None)
    said_last = " " + " ".join(_words(last_sentence)) + " "
    praise = praise or next((p for p in IMPORTANCE_PHRASES if f" {p}" in said_last), None)
    if praise and not opener:
        problems.append(f'son cümle "{praise}" gibi genel bir lafla bitmesin, '
                        'somut bir bilgiyle bitip ilk cümleye bağlansın')
    words = sum(len(scene["text"].split()) for scene in scenes)
    if words > MERAK_MAX_WORDS:
        problems.append(f"anlatım {words} kelime, en fazla {MERAK_MAX_WORDS} kelime olsun")
    return problems


def fill_clips(scenes: list[dict], pool: list[dict]) -> list[dict]:
    """Tops up scenes that got fewer clips than cuts with unused clips most like the ones Gemini chose:
    same search first, then tags sharing the most words with the scene's on-screen description."""
    taken = {ref["n"] for scene in scenes for ref in scene["clips"]}
    for scene in scenes:
        queries = {pool[ref["n"] - 1].get("_query") for ref in scene["clips"]}
        while len(scene["clips"]) < wanted_shots(scene):
            free = [n for n in range(1, len(pool) + 1) if n not in taken]
            if not free:
                break
            best = max(free, key=lambda n: (pool[n - 1].get("_query") in queries,
                                            original_visuals.tag_score(pool[n - 1].get("tags", ""), scene["visual"]),
                                            -n))
            taken.add(best)
            scene["clips"].append({"n": best, "kare": 2})
    return scenes


def _ask_editor(prompt: str) -> str | None:
    return _call_gemini_parts([{"text": prompt}], temperature=0.1)


def check_facts(script: dict, topic: str, call=_ask_editor) -> list[str]:
    """Has Gemini re-read the narration as a science editor and swaps in its corrected sentences; returns why.
    A wrong claim ("sound is millions of times slower than light", 2026-10-13 sample) got past the writing prompt.
    A fix that is empty, too long or would push the narration out of its word range is ignored."""
    scenes = script["scenes"]
    lines = "\n".join(f"{i}. {s['text']}" + (f" [ekranda: {s['callout']}]" if s.get("callout") else "")
                      for i, s in enumerate(scenes, 1))
    try:
        raw = call(FACT_PROMPT.format(topic=topic, scenes=lines))
        fixes = (_extract_json(raw).get("fixes") or []) if raw else []
    except (ScriptError, ValueError, urllib.error.URLError, TimeoutError) as e:
        print(f"ℹ️ Bilgi kontrolü yapılamadı: {str(e)[:200]}")
        return []
    notes = []
    for fix in fixes if isinstance(fixes, list) else []:
        try:
            i = int(fix.get("scene")) - 1
        except (AttributeError, TypeError, ValueError):
            continue
        text = " ".join(EMOJI_RE.sub("", str(fix.get("text") or "")).split())
        if not 0 <= i < len(scenes) or not text or len(text) > MAX_SCENE_CHARS:
            continue
        had = scenes[i].get("callout", "")
        callout = clean_callout(fix.get("callout")) if had else ""  # the editor corrects key facts, never adds them
        if text == scenes[i]["text"] and callout == had:
            continue
        words = sum(len(s["text"].split()) for s in scenes) - len(scenes[i]["text"].split()) + len(text.split())
        if MERAK_MIN_WORDS <= words <= MAX_WORDS:
            scenes[i]["text"] = text
            # A corrected sentence never keeps an on-screen number the editor did not confirm.
            if callout:
                scenes[i]["callout"] = callout
            else:
                scenes[i].pop("callout", None)
            notes.append(f"{i + 1}. sahne: {' '.join(str(fix.get('why') or '').split())[:150]}")
    return notes


class ThinFootage(Exception):
    """Free libraries hold too few on-subject clips for the topic. Unlike a failed Gemini call this says something
    about the topic itself: the octopus sample (2026-10-15) found 11 clips, fell back to the script-first flow and
    left three scenes without a matching clip."""

    def __init__(self, found: int):
        super().__init__(f"{found} uygun video bulundu, en az {MIN_POOL} gerekiyor")
        self.found = found


def write_footage_script(topic: dict, work_dir: Path, used_ids: set | None = None, call=_call_gemini_parts,
                         plan=plan_queries, sheet=contact_sheet, check=check_facts, attempts: int = 3) -> dict | None:
    """Returns {"script", "pool", "coverage", "queries"}; scene clip numbers index `pool` from 1. None on failure;
    raises ThinFootage when the libraries hold too few clips for the topic.

    An answer with thin scenes or hook problems is asked about again; after the last attempt the best answer
    is used, so these never send the run to the weaker script-first flow. None only when no answer parsed."""
    used_ids = used_ids if used_ids is not None else set()
    subject = list(topic.get("subject") or [])
    queries = plan(topic["topic"], subject, topic.get("stock_hint", ""))
    pool = gather_pool(queries, subject, used_ids)
    pexels = sum(1 for h in pool if h.get("source") == "pexels")
    print(f"       Görüntü havuzu: {len(pool)} aday ({pexels} Pexels, {len(pool) - pexels} Pixabay; {len(queries)} arama)")
    if len(pool) < MIN_POOL:
        raise ThinFootage(len(pool))
    work_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        sheets = list(ex.map(lambda h: sheet(h, work_dir), pool))
    shown = [(hit, image) for hit, image in zip(pool, sheets) if image]
    if len(shown) < len(pool):
        print(f"       Kare alınamayan aday: {len(pool) - len(shown)}")
    if len(shown) < MIN_POOL:
        raise ThinFootage(len(shown))

    parts: list[dict] = [{"text": SCRIPT_PROMPT.format(
        topic=topic["topic"], min_words=MERAK_MIN_WORDS + 2, max_words=MERAK_MAX_WORDS,
        min_scenes=MERAK_MIN_SCENES, max_scenes=MERAK_MAX_SCENES,
    )}]
    for n, (hit, (data, mime)) in enumerate(shown, start=1):
        parts.append({"text": f"Aday {n} ({int(hit.get('duration') or 0)} sn):"})
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})

    clips = [hit for hit, _ in shown]

    def finish(script: dict, coverage: int) -> dict:
        for note in check(script, topic["topic"]):
            print(f"       Bilgi düzeltmesi: {note}")
        fill_clips(script["scenes"], clips)
        return {"script": script, "pool": clips, "coverage": coverage, "queries": queries}

    best = None  # (rank, attempt, script, coverage): the best usable answer so far, in case no retry is clean
    request = parts
    for attempt in range(1, attempts + 1):
        try:
            raw = call(request, temperature=0.7)
            if not raw:
                break
            data = _extract_json(raw)
            script = validate_script(data, {**topic, "stock_hint": topic.get("stock_hint", "")},
                                     min_words=MERAK_MIN_WORDS, min_scenes=MERAK_MIN_SCENES)
            script["scenes"] = resolve_clips(script["scenes"], len(shown))
            try:
                coverage = int(data.get("coverage") or 0)
            except (TypeError, ValueError):
                coverage = 0
        except (ScriptError, ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError) as e:
            note = str(e)[:200]
        else:
            thin = thin_scenes(script["scenes"])
            problems = hook_problems(script)
            if not thin and not problems:
                return finish(script, coverage)
            # Missing footage weighs more than a weak opener: a thin scene shows one shot for several seconds.
            rank = (bool(thin), len(thin), len(problems))
            if best is None or rank < best[0]:
                best = (rank, attempt, script, coverage)
            notes = [f"{', '.join(map(str, thin))}. sahnede yeterli video yok (tek video var ya da videoları başka "
                     f"sahnede kullanılmış; bir video yalnızca bir sahnede sayılır). Video 1,5 saniyede bir kesilecek, "
                     f"5 kelimeden uzun her sahneye kendine ait 2-3 farklı video ver"] if thin else []
            note = "; ".join(notes + problems)
        print(f"ℹ️ Görüntüye göre senaryo denemesi reddedildi (#{attempt}): {note}")
        # One note about the latest answer only; stacking every earlier note would keep listing problems it fixed.
        request = parts[:1] + [{"text": f"Önceki yanıtın reddedildi: {note}. Kurallara uyarak yeniden yaz."}] + parts[1:]
    if best is None:
        return None
    print(f"ℹ️ Kurallara tam uyan senaryo gelmedi; en iyi deneme (#{best[1]}) kullanılıyor")
    return finish(best[2], best[3])
