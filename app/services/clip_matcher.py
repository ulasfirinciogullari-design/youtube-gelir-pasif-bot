"""
Picks the stock clips that show what each sentence says.

Keyword search finds clips about the topic, but it cannot tell whether a clip
shows the moment a sentence describes; Ulaş rejected a cat video full of
mountains, clocks and skeletons. So the candidates' thumbnails go to Gemini
(free tier, one call per video) together with the narration, and Gemini gives
every scene only clips that show that sentence, or failing that the closest
on-topic clip. Any failure returns None and the caller keeps keyword order.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
import os
import urllib.request

from app.services.original_script import _extract_json, _post_json

USER_AGENT = "Mozilla/5.0 (zirvenin-kanunu original shorts)"

MATCH_PROMPT = """Bir Türkçe bilgi Shorts videosu için her sahneye stok video seçiyorsun.
Konu: {topic}

Sahneler (her biri, ekranda o cümle okunurken gösterilecek):
{scenes}

Aşağıda numaralı aday videoların birer karesi var. Her sahne için o cümlede anlatılan şeyi gösteren adayları seç.
Kurallar:
- Her sahne için parantezde yazan sayıda FARKLI aday seç; video hızlı kesmelerle bu adaylar arasında geçecek.
- Önce cümleyi birebir gösteren adaylar (ör. "kedi havada dönüyor" için havada veya zıplarken bir kedi), sonra cümledeki
  hareketi veya durumu en yakın gösteren, aynı özneye ait adaylar gelsin.
- Cümleyle çelişen adayı seçme (cümle zıplamayı anlatırken uyuyan kedi gibi). Konuyla ilgisiz bir adayı (manzara, saat,
  insan iskeleti, alakasız nesne vb.) asla seçme. Uygun aday yetmiyorsa daha az seç.
- Yazı, logo, filigran, çizim/animasyon olan veya bulanık adayları seçme.
- Bir aday yalnızca bir sahnede kullanılabilir. Adayları en uygun olandan başlayarak sırala.
Sadece şu JSON'u döndür: {{"scenes": [{{"scene": 1, "clips": [4, 9]}}]}}"""


def pixabay_thumbnail(hit: dict) -> str | None:
    videos = hit.get("videos") or {}
    for size in ("tiny", "small", "medium", "large"):
        thumb = (videos.get(size) or {}).get("thumbnail")
        if thumb:
            return thumb
    # Pexels hits (original_visuals.pexels_hit) name their renditions file0, file1, ...
    return next((r["thumbnail"] for r in videos.values() if isinstance(r, dict) and r.get("thumbnail")), None)


def _image_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def fetch_thumbnail(url: str | None) -> tuple[bytes, str] | None:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(2_000_000)
    except Exception:
        return None
    mime = _image_type(data)
    return (data, mime) if mime else None


def _call_gemini_parts(parts: list[dict], temperature: float = 0.2) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": parts}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": temperature}},
        {"x-goog-api-key": key},
        timeout=240,
    )
    out = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in out)


def match_clips(topic: str, scenes: list[dict], wanted: list[int], candidates: list[dict],
                call=_call_gemini_parts, fetch=fetch_thumbnail) -> list[list[int]] | None:
    """For every scene, indexes into `candidates` (Pixabay hits) in the order Gemini ranked them.

    Each candidate is used at most once and a scene gets at most `wanted[i]` clips.
    Returns None when nothing could be matched (no thumbnails, no Gemini, unreadable answer)."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        thumbs = list(pool.map(fetch, [pixabay_thumbnail(c) for c in candidates]))
    usable = {i for i, t in enumerate(thumbs) if t}
    if not usable:
        return None

    scene_lines = "\n".join(
        f"{n}. ({wanted[n - 1]} aday) {s.get('text', '')} [aranan görüntü: {s.get('stock_query', '')}]"
        for n, s in enumerate(scenes, start=1)
    )
    parts: list[dict] = [{"text": MATCH_PROMPT.format(topic=topic, scenes=scene_lines)}]
    for i in sorted(usable):
        data, mime = thumbs[i]
        parts.append({"text": f"Aday {i + 1}:"})
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})

    raw = call(parts)
    if not raw:
        return None
    answer = _extract_json(raw)
    picks: list[list[int]] = [[] for _ in scenes]
    taken: set[int] = set()
    for item in answer.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        try:
            scene = int(item.get("scene")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= scene < len(scenes):
            continue
        for number in item.get("clips") or []:
            try:
                idx = int(number) - 1
            except (TypeError, ValueError):
                continue
            if idx in usable and idx not in taken and len(picks[scene]) < wanted[scene]:
                picks[scene].append(idx)
                taken.add(idx)
    return picks if any(picks) else None
