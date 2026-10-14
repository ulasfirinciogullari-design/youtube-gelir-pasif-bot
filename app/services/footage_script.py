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
import subprocess
import urllib.error

from app.services import original_visuals
from app.services.clip_matcher import _call_gemini_parts, fetch_thumbnail, pixabay_thumbnail
from app.services.original_script import (
    EMOJI_RE, MAX_SCENE_CHARS, MAX_SCENES, MAX_WORDS, MIN_SCENES, MIN_WORDS, ScriptError, _call_gemini, _extract_json,
    validate_script,
)

MIN_COVERAGE = 6  # out of 10; below this a topic is skipped when the run picked it itself
WORDS_PER_SHOT = 4  # ~1.6 s of narration: the reference Shorts cut about this often
MIN_POOL = 12
MAX_POOL = 40
FRAME_HEIGHT = 200

QUERY_PROMPT = """Pixabay'de stok video arayacağım. Konu: {topic}
Videoda görünecek ana özne: {subject}
Bu konuyu anlatan bir Shorts videosu için gerekecek görüntüleri bulacak 8 farklı arama yaz. Her biri 2-3 basit İngilizce
kelime olsun ve ana öznelerden birini içersin (ör. "cat jumping", "cat landing", "kitten playing").
Sadece şu JSON'u döndür: {{"queries": ["..."]}}"""

SCRIPT_PROMPT = """Sen "Zirvenin Kanunu" adlı Türkçe YouTube Shorts kanalının baş senaristisin. Bu seride bilim, hayvanlar,
insan vücudu ve psikoloji hakkında merak edilen soruları gerçek stok videolarla anlatıyoruz (Keşif Vakti tarzı).

Konu: {topic}

Aşağıda numaralı aday videolar var. Her adaydan soldan sağa üç kare görüyorsun: 1 = başı, 2 = ortası, 3 = sonu.
Önce bütün adaylara bak, sonra SADECE bu adaylarla gösterilebilecek 25-40 saniyelik bir senaryo yaz.

En önemli kural: Her sahnede söylenen cümle, o sahnenin videolarında görünen şeyle birebir örtüşsün. İzleyici cümleyi
duyarken anlatılanı ekranda görmeli. Videolarda olmayan bir anı anlatma; konuyu, videolarda gerçekten görünen hareketler
ve ayrıntılar üzerinden açıkla (ör. düşen kedi görüntüsü yoksa zıplayan ve yere inen kediyi anlat).

Anlatım kuralları:
- İlk cümle en fazla 12 kelimelik, şaşırtıcı bir iddia veya soru olsun. "Biliyor muydun", "Merhaba", "Bugün" ile başlama.
- Toplam {min_words}-{max_words} kelime, {min_scenes}-{max_scenes} sahne. Her sahne 1-2 kısa cümle.
- Akış: kanca, sorunun açılması, şaşırtıcı cevap, cevabı somutlaştıran örnek, akılda kalan bir final cümlesi.
- Son sahne izleyicinin aklında kalacak somut bir bilgi, kendisinin deneyebileceği bir şey veya şaşırtıcı bir sonuç olsun;
  "doğa muhteşem", "fizik kurallarıyla açıklanıyor" gibi genel bir lafla bitirme.
- Enerjik ve samimi konuş; yeri gelince ekrana gönderme yap ("şu kediye bakın", "gördüğünüz gibi").
- Sadece bilimsel olarak doğrulanmış bilgiler. Sayı ve karşılaştırma (süre, yüzde, ölçü, "milyon kat", "iki katı") yalnızca
  ansiklopedilerde geçen, kesin bilinen bir değerse kullan; emin değilsen sayı verme.
- Anlatımda emoji kullanma, metin seslendirilecek.

Görüntü kuralları:
- Video her 1,5 saniyede bir kesilecek. Bu yüzden her sahneye 2 ya da 3 FARKLI aday ver (8 kelimeden uzun sahnelere 3),
  hepsi o cümleyi göstersin; aynı anın farklı çekimleri de olur. Sadece 5 kelimeden kısa sahnelere tek aday verebilirsin.
- "clips": o sahnede sırayla gösterilecek adaylar, her biri {{"n": aday numarası, "kare": cümleye en uyan kare (1, 2 ya da 3)}}.
  Bir aday yalnızca bir sahnede kullanılabilir.
- Yazı, logo, filigran olan, bulanık veya konuyla ilgisiz adayları kullanma.
- "visual": o sahnede ekranda görünen şeyin kısa İngilizce tarifi.
- "coverage": adayların bu konuyu ne kadar iyi gösterebildiği, 1-10. Konunun ana sorusunu gösteren aday yoksa 5'in altında ver.

Başlık ve kanca:
- "title": en fazla 60 karakter; merak uyandıran soru veya şaşırtıcı iddia ve sonunda tek bir uygun emoji. #shorts yazma.
- "hook_text": konunun sorusunu soran 3-7 kelimelik büyük yazı (ör. "KEDİLER NEDEN HEP AYAKTA DÜŞER?"); video boyunca
  ekranın üstünde durur, emoji yok.

Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "coverage": 8,
  "title": "Türkçe başlık + tek emoji",
  "hook_text": "3-7 kelimelik kanca yazısı",
  "scenes": [
    {{"text": "Türkçe anlatım", "visual": "what is on screen",
      "clips": [{{"n": 4, "kare": 2}}, {{"n": 11, "kare": 1}}, {{"n": 17, "kare": 3}}]}}
  ],
  "description": "2-3 cümlelik Türkçe açıklama",
  "tags": ["8-12 Türkçe etiket"],
  "pinned_comment": "İzleyiciyi tartışmaya çağıran tek bir soru",
  "en_title": "English title, max 70 chars",
  "en_description": "1-2 sentence English description"
}}"""


FACT_PROMPT = """Bir bilim editörüsün. Aşağıdaki Türkçe Shorts anlatımı yayına girmeden önce son kez kontrol ediliyor.
Konu: {topic}

{scenes}

Her cümleyi tek tek kontrol et: yanlış, abartılı veya yanıltıcı bir bilgi (sayı, oran, karşılaştırma, neden-sonuç) var mı?
Örnek: "kalp günde 1 milyon kez atar" yanlıştır, yaklaşık 100 bin kezdir.
Hatalı her cümle için düzeltilmiş halini yaz: aynı konuyu ve aynı görüntüyü anlatan, yaklaşık aynı uzunlukta, sadece hatalı
kısmı değiştiren bir cümle. Doğru cümleleri listeye koyma; hata yoksa boş liste döndür.
Sadece şu JSON'u döndür: {{"fixes": [{{"scene": 3, "text": "düzeltilmiş cümle", "why": "kısa gerekçe"}}]}}"""


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
    """On-subject candidates from every query, interleaved so each search contributes its best clips.
    Each hit remembers the search that found it (`_query`), which later helps fill a scene with similar shots."""
    lists = [[{**h, "_query": q} for h in original_visuals.pixabay_candidates(q, used_ids, subject, limit=10)]
             for q in queries]
    pool, seen = [], set()
    for rank in range(10):
        for hits in lists:
            if rank < len(hits) and hits[rank].get("id") not in seen and len(pool) < limit:
                seen.add(hits[rank].get("id"))
                pool.append(hits[rank])
    return pool


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
        src, out = work_dir / f"preview_{hit['id']}.mp4", work_dir / f"sheet_{hit['id']}.jpg"
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
    """Keeps clip numbers that exist and are not used by an earlier scene; every scene must keep at least one."""
    taken: set[int] = set()
    for i, scene in enumerate(scenes, start=1):
        refs = []
        for ref in scene.get("clips") or []:
            if 1 <= ref["n"] <= shown and ref["n"] not in taken:
                taken.add(ref["n"])
                refs.append(ref)
        if not refs:
            raise ScriptError(f"sahne {i} için kullanılabilir video yok")
        scene["clips"] = refs
    return scenes


def thin_scenes(scenes: list[dict]) -> list[int]:
    """Scenes (from 1) long enough for several cuts that got a single clip: one long shot per sentence
    made the lightning sample (2026-10-13) feel slow next to the reference Shorts."""
    return [i for i, scene in enumerate(scenes, start=1) if len(scene["clips"]) < min(2, wanted_shots(scene))]


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
    try:
        raw = call(FACT_PROMPT.format(topic=topic, scenes="\n".join(f"{i}. {s['text']}" for i, s in enumerate(scenes, 1))))
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
        if not 0 <= i < len(scenes) or not text or len(text) > MAX_SCENE_CHARS or text == scenes[i]["text"]:
            continue
        words = sum(len(s["text"].split()) for s in scenes) - len(scenes[i]["text"].split()) + len(text.split())
        if MIN_WORDS <= words <= MAX_WORDS:
            scenes[i]["text"] = text
            notes.append(f"{i + 1}. sahne: {' '.join(str(fix.get('why') or '').split())[:150]}")
    return notes


def write_footage_script(topic: dict, work_dir: Path, used_ids: set | None = None, call=_call_gemini_parts,
                         plan=plan_queries, sheet=contact_sheet, check=check_facts, attempts: int = 2) -> dict | None:
    """Returns {"script", "pool", "coverage", "queries"}; scene clip numbers index `pool` from 1. None on failure."""
    used_ids = used_ids if used_ids is not None else set()
    subject = list(topic.get("subject") or [])
    queries = plan(topic["topic"], subject, topic.get("stock_hint", ""))
    pool = gather_pool(queries, subject, used_ids)
    print(f"       Görüntü havuzu: {len(pool)} aday ({len(queries)} arama)")
    if len(pool) < MIN_POOL:
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        sheets = list(ex.map(lambda h: sheet(h, work_dir), pool))
    shown = [(hit, image) for hit, image in zip(pool, sheets) if image]
    if len(shown) < MIN_POOL:
        return None

    parts: list[dict] = [{"text": SCRIPT_PROMPT.format(
        topic=topic["topic"], min_words=MIN_WORDS, max_words=MAX_WORDS, min_scenes=MIN_SCENES, max_scenes=MAX_SCENES,
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

    thin_answer = None  # a usable script whose scenes were too thin; kept in case the retry fails
    for attempt in range(1, attempts + 1):
        try:
            raw = call(parts, temperature=0.7)
            if not raw:
                break
            data = _extract_json(raw)
            script = validate_script(data, {**topic, "stock_hint": topic.get("stock_hint", "")})
            script["scenes"] = resolve_clips(script["scenes"], len(shown))
            try:
                coverage = int(data.get("coverage") or 0)
            except (TypeError, ValueError):
                coverage = 0
        except (ScriptError, ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError) as e:
            note = str(e)[:200]
        else:
            thin = thin_scenes(script["scenes"])
            if not thin or attempt == attempts:
                return finish(script, coverage)
            thin_answer = thin_answer or (script, coverage)
            note = (f"{', '.join(map(str, thin))}. sahneye tek video verilmiş. Video 1,5 saniyede bir kesilecek, "
                    f"5 kelimeden uzun her sahneye 2-3 farklı video ver")
        print(f"ℹ️ Görüntüye göre senaryo denemesi reddedildi (#{attempt}): {note}")
        parts = parts[:1] + [{"text": f"Önceki yanıtın reddedildi: {note}. Kurallara uyarak yeniden yaz."}] + parts[1:]
    return finish(*thin_answer) if thin_answer else None
