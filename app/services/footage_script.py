"""
Footage-first scripts for the curiosity series.

Writing the narration first and looking for footage afterwards gave videos that
looked "filled with random cat clips" (Ulaş, 2026-10-13): a free library rarely
holds the exact moment a sentence describes. This goes the other way round:
gather on-subject Pixabay clips, show Gemini three frames of each, and let it
write the narration around what the clips actually show, naming for every scene
the clips and the frame that fits the sentence. Gemini also rates how well the
footage covers the topic, so a topic the library cannot show can be skipped.

One text call plans the searches and one vision call writes the script, both on
the Gemini free tier. Any failure returns None and the caller falls back to the
script-first flow.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import urllib.error

from app.services import original_visuals
from app.services.clip_matcher import _call_gemini_parts, fetch_thumbnail, pixabay_thumbnail
from app.services.original_script import (
    MAX_SCENES, MAX_WORDS, MIN_SCENES, MIN_WORDS, ScriptError, _call_gemini, _extract_json, validate_script,
)

MIN_COVERAGE = 6  # out of 10; below this a topic is skipped when the run picked it itself
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
- Enerjik ve samimi konuş; yeri gelince ekrana gönderme yap ("şu kediye bakın", "gördüğünüz gibi").
- Sadece bilimsel olarak doğrulanmış bilgiler. Sayı (süre, yüzde, ölçü) yalnızca ansiklopedilerde geçen, kesin bilinen
  bir değerse kullan; emin değilsen sayı verme.
- Anlatımda emoji kullanma, metin seslendirilecek.

Görüntü kuralları:
- "clips": o sahnede sırayla gösterilecek 1-3 aday, her biri {{"n": aday numarası, "kare": cümleye en uyan kare (1, 2 ya da 3)}}.
  Uzun cümlelere 2-3, kısa cümlelere 1-2 aday ver. Bir aday yalnızca bir sahnede kullanılabilir.
- Yazı, logo, filigran olan, bulanık veya konuyla ilgisiz adayları kullanma.
- "visual": o sahnede ekranda görünen şeyin kısa İngilizce tarifi.
- "coverage": adayların bu konuyu ne kadar iyi gösterebildiği, 1-10. Konunun ana sorusunu gösteren aday yoksa 5'in altında ver.

Başlık ve kanca:
- "title": en fazla 60 karakter; merak uyandıran soru veya şaşırtıcı iddia ve sonunda tek bir uygun emoji. #shorts yazma.
- "hook_text": video boyunca ekranın üstünde duracak 3-7 kelimelik büyük yazı, emoji yok.

Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "coverage": 8,
  "title": "Türkçe başlık + tek emoji",
  "hook_text": "3-7 kelimelik kanca yazısı",
  "scenes": [
    {{"text": "Türkçe anlatım", "visual": "what is on screen", "clips": [{{"n": 4, "kare": 2}}, {{"n": 11, "kare": 1}}]}}
  ],
  "description": "2-3 cümlelik Türkçe açıklama",
  "tags": ["8-12 Türkçe etiket"],
  "pinned_comment": "İzleyiciyi tartışmaya çağıran tek bir soru",
  "en_title": "English title, max 70 chars",
  "en_description": "1-2 sentence English description"
}}"""


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
    """On-subject candidates from every query, interleaved so each search contributes its best clips."""
    lists = [original_visuals.pixabay_candidates(q, used_ids, subject, limit=10) for q in queries]
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


def write_footage_script(topic: dict, work_dir: Path, used_ids: set | None = None, call=_call_gemini_parts,
                         plan=plan_queries, sheet=contact_sheet, attempts: int = 2) -> dict | None:
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

    for attempt in range(1, attempts + 1):
        try:
            raw = call(parts, temperature=0.7)
            if not raw:
                return None
            data = _extract_json(raw)
            script = validate_script(data, {**topic, "stock_hint": topic.get("stock_hint", "")})
            script["scenes"] = resolve_clips(script["scenes"], len(shown))
            try:
                coverage = int(data.get("coverage") or 0)
            except (TypeError, ValueError):
                coverage = 0
            return {"script": script, "pool": [hit for hit, _ in shown], "coverage": coverage, "queries": queries}
        except (ScriptError, ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError) as e:
            print(f"ℹ️ Görüntüye göre senaryo denemesi reddedildi (#{attempt}): {str(e)[:200]}")
            parts = parts[:1] + [{"text": f"Önceki yanıtın reddedildi: {str(e)[:200]}. Kurallara uyarak yeniden yaz."}] + parts[1:]
    return None
