"""
Reference Shorts breakdown: Gemini watches the Shorts Ulaş points to and
returns how each one is made (visual source, cut length, motion, grade,
subtitles, voice, music, sound effects, hook, structure), then one call turns
those breakdowns into a production spec compared with our own pipeline.

Gemini reads public YouTube links directly, so nothing is downloaded. The
YouTube Data API adds title, channel, length and views (1 unit per 50 videos).
"""

import json
import os
import re

from app.services.original_script import _extract_json, _post_json

ID_RE = re.compile(r"(?:shorts/|watch\?v=|youtu\.be/|^)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")

VIDEO_PROMPT = """Bu YouTube Shorts videosunu baştan sona izle ve nasıl üretildiğini bir yapımcının tekrar üretebileceği
kadar somut anlat. Tahmin ettiğin yerleri "muhtemelen" diye belirt. Türkçe yaz, sadece şu JSON'u döndür:
{
  "topic": "videonun konusu",
  "length_seconds": 0,
  "visual_source": "gerçek çekim / film-dizi sahnesi / AI görsel / AI video / stok video / karışık; oranlarıyla",
  "visual_style": "görsellerin tarzı, gerçekçilik, dönem, tutarlılık",
  "avg_shot_seconds": 0,
  "camera_motion": "zoom, pan, parallax, sabit vb.",
  "color_grade": "renk ve ışık",
  "subtitles": "altyazı yazı tipi, boyut, renk, konum, kelime kelime mi, vurgu rengi, emoji",
  "on_screen_text": "kanca yazısı veya başka ekran yazıları",
  "voice": "anlatıcı cinsiyeti, tonu, hızı, insan mı yapay mı",
  "music": "tür, ruh hali, sese göre seviyesi, değişiyor mu",
  "sfx": "kullanılan ses efektleri ve nerede",
  "hook_first_3s": "ilk 3 saniyede tam olarak ne görülüyor ve duyuluyor",
  "structure": "saniye saniye akış (kanca, gelişme, dönüş, final)",
  "ending": "final: döngü, soru, çağrı vb.",
  "why_it_works": "neden izletiyor",
  "cheap_to_copy": "düşük bütçeyle taklit edilebilecek teknikler"
}"""

SYNTHESIS_PROMPT = """Aşağıda Ulaş'ın "işte böyle olmalı" dediği Shorts videolarının üretim analizleri var.
Bizim sistemimiz şu an şöyle çalışıyor:
{ours}

Analizler:
{analyses}

Bu videoların ortak özelliklerinden bizim için somut bir üretim şartnamesi çıkar. Her kural uygulanabilir ve ölçülebilir olsun
(ör. "her 1,5 saniyede kesme", "altyazı ekranın ortasında, sarı vurgu"). Türkçe yaz, sadece şu JSON'u döndür:
{{
  "common_traits": ["videoların ortak özellikleri"],
  "visual_rules": ["görsel kaynağı, tarzı, hareketi, renk"],
  "edit_rules": ["kesme sıklığı, geçişler, ekran yazıları, altyazı"],
  "audio_rules": ["ses, müzik türü ve seviyesi, efektler"],
  "script_rules": ["kanca, akış, uzunluk, final"],
  "title_rules": ["başlık ve kapak"],
  "gaps": ["bizim sistemin bu videolardan en çok geride kaldığı noktalar, önem sırasıyla"],
  "music_prompts": ["ElevenLabs ile üretilecek 3-6 enstrümantal müzik için İngilizce tarif"]
}}"""

OUR_PIPELINE = (
    "LLM senaryo (25-40 sn, 5-8 sahne), ElevenLabs Türkçe erkek sesi. Merak serisinde en fazla 1,6 sn'lik Pexels/Pixabay "
    "stok video kesmeleri; tarih ve karakter serilerinde 2,5 sn'lik Flux AI görselleri + yavaş zoom/pan. Kelime kelime "
    "sarı vurgulu kalın beyaz altyazı alt üçte birde, kanca sorusu video boyunca ekranın üstünde, seri başına ElevenLabs "
    "ile üretilmiş enstrümantal müzik (sesin altında, %9) ve whoosh/vuruş/yükselme efektleri. Karakter serisinde oyuncu "
    "yüzü ve film sahnesi yok."
)


def extract_ids(text: str) -> list[str]:
    ids = []
    for part in re.split(r"[\s,]+", text or ""):
        match = ID_RE.search(part.strip())
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def video_metadata(youtube, ids: list[str]) -> dict:
    if not youtube or not ids:
        return {}
    items = youtube.videos().list(part="snippet,contentDetails,statistics", id=",".join(ids[:50])).execute()
    return {
        v["id"]: {
            "title": v["snippet"].get("title", ""),
            "channel": v["snippet"].get("channelTitle", ""),
            "duration": v.get("contentDetails", {}).get("duration", ""),
            "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
        }
        for v in items.get("items", [])
    }


def _call_gemini_video(video_id: str, prompt: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [
                {"fileData": {"fileUri": f"https://www.youtube.com/watch?v={video_id}"}},
                {"text": prompt},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        },
        {"x-goog-api-key": key},
        timeout=240,
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_gemini_text(prompt: str) -> str | None:
    from app.services.seo_pack import _call_gemini_text as call
    return call(prompt)


def analyze_references(ids: list[str], youtube=None, watch=_call_gemini_video, call=_call_gemini_text) -> dict:
    meta = video_metadata(youtube, ids)
    analyses = []
    for video_id in ids:
        entry = {"video_id": video_id, **meta.get(video_id, {})}
        try:
            raw = watch(video_id, VIDEO_PROMPT)
            entry["analysis"] = _extract_json(raw) if raw else {}
        except Exception as e:  # one unreadable video should not sink the report
            entry["error"] = str(e)[:300]
        analyses.append(entry)
        print(f"🎬 {video_id}: {'analiz edildi' if entry.get('analysis') else entry.get('error', 'boş yanıt')}", flush=True)

    usable = [{k: a[k] for k in ("title", "channel", "views", "analysis") if k in a} for a in analyses if a.get("analysis")]
    spec = {}
    if usable:
        raw = call(SYNTHESIS_PROMPT.format(ours=OUR_PIPELINE, analyses=json.dumps(usable, ensure_ascii=False, indent=1)))
        spec = _extract_json(raw) if raw else {}
    return {"videos": analyses, "spec": spec}


def format_report(result: dict) -> str:
    lines = ["### Referans Shorts analizi", ""]
    for v in result.get("videos", []):
        a = v.get("analysis") or {}
        head = f"**{v.get('title') or v['video_id']}** ({v.get('channel', '?')}, {v.get('views', '?')} izlenme)"
        if not a:
            lines += [f"- {head}: analiz edilemedi ({v.get('error', 'boş yanıt')})"]
            continue
        lines += [f"- {head}", f"  - Görsel: {a.get('visual_source', '-')} | {a.get('visual_style', '-')}",
                  f"  - Kurgu: {a.get('avg_shot_seconds', '-')} sn/kesme, {a.get('camera_motion', '-')}, renk: {a.get('color_grade', '-')}",
                  f"  - Altyazı: {a.get('subtitles', '-')}",
                  f"  - Ses: {a.get('voice', '-')} | müzik: {a.get('music', '-')} | efekt: {a.get('sfx', '-')}",
                  f"  - Kanca: {a.get('hook_first_3s', '-')}"]
    spec = result.get("spec") or {}
    titles = {"common_traits": "Ortak özellikler", "visual_rules": "Görsel", "edit_rules": "Kurgu",
              "audio_rules": "Ses", "script_rules": "Senaryo", "title_rules": "Başlık", "gaps": "Bizim eksiklerimiz",
              "music_prompts": "Müzik tarifleri"}
    for key, title in titles.items():
        if spec.get(key):
            lines += ["", f"#### {title}", *[f"- {item}" for item in spec[key]]]
    return "\n".join(lines) + "\n"
