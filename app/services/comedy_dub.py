"""
Comedy dub Shorts: a free Pixabay animal clip plus a funny Turkish inner voice.

Gemini watches a small rendition of the clip and writes timed lines for the
animals in it; the clip itself is licensed for commercial use and edited
(re-framed, dubbed, subtitled), and every clip is used once: its Pixabay id
becomes the "Seri: kd-<id>" marker in the video description.
"""

import base64
import os
from pathlib import Path
import random
import re

from app.services.original_script import (
    BANNED_REFERENCES, EMOJI_RE, MAX_HOOK_CHARS, ScriptError, _extract_json, _post_json,
)
from app.services.original_visuals import _download, _pick_pixabay_file, search_pixabay

COMEDY_QUERIES = [
    "funny cat", "cat", "kitten", "funny dog", "dog", "puppy", "goat", "parrot", "duck", "hamster",
    "squirrel", "raccoon", "cow", "horse", "monkey", "penguin", "sheep", "chicken", "llama", "rabbit",
]
# The first real run took a 9 s clip; five voiced lines ran to 14.8 s and the clip looped.
MIN_CLIP_SECONDS = 12
MAX_CLIP_SECONDS = 35
MAX_PREVIEW_BYTES = 18 * 1024 * 1024
MIN_LINES, MAX_LINES = 2, 8
MAX_LINE_WORDS = 14
MAX_SPEAKERS = 3
WORDS_PER_SECOND = 2.0  # budget given to the model; ElevenLabs Turkish reads ~2.5 words/s plus pauses
MAX_WORDS_SLACK = 1.15  # validation allows a little overshoot before asking again

PROMPT_TEMPLATE = """Türkçe bir YouTube Shorts komik dublaj kanalı için yazıyorsun. Ekteki {duration:.1f} saniyelik
klibi izle ve klipteki hayvan(lar)ın iç sesini komik Türkçe repliklerle yaz.

Kurallar:
- Klipte gerçekten olan hareketlere göre yaz. Her repliğin "start" değeri, o hareketin başladığı saniye olsun.
- {min_lines}-{max_lines} replik, her biri en fazla 12 kelime, hepsi toplam en fazla {max_words} kelime.
  Replikler klibin içinde bitsin ve üst üste binmesin; son replik en geç {last_start:.1f}. saniyede başlasın.
- Türk internet mizahı: ev hali, misafir, diyet, pazartesi, anne-baba, iş yeri gibi herkesin bildiği durumlar.
  En komik replik en sonda olsun.
- Küfür, hakaret, siyaset, din ve cinsellik yok. Gerçek kişilerden, markalardan, film ve dizilerden bahsetme.
- Konuşanlar klipteki karakterler (ör. "kedi", "köpek") ve gerekirse "anlatıcı"; en fazla 2 karakter artı anlatıcı.
- Repliklerde emoji kullanma, metin seslendirilecek.
- "title": en fazla 60 karakter, durumu anlatan komik bir cümle ve sonunda tek emoji
  (ör. "Misafir sevmeyen kedinin iç sesi 😂"). #shorts yazma.
- "hook_text": ilk 2 saniye ekranın üstünde duracak 2-5 kelime, emoji yok (ör. "SONUNA KADAR İZLE").
- Klipte hayvan yoksa, hiçbir şey olmuyorsa ya da klipte bir insanın yüzü öne çıkıyorsa "usable": false döndür.

Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "usable": true,
  "title": "Komik başlık + tek emoji",
  "hook_text": "2-5 kelime",
  "lines": [{{"start": 0.5, "speaker": "kedi", "text": "Türkçe replik"}}],
  "description": "1-2 cümlelik Türkçe açıklama",
  "tags": ["8-12 Türkçe etiket"],
  "pinned_comment": "İzleyiciyi yorum yazmaya çağıran tek bir soru",
  "en_title": "English title, max 70 chars",
  "en_description": "1 sentence English description"
}}
"""


class ClipUnusable(ScriptError):
    pass


def candidate_clips(published: set[str], seed: str, queries: list[str] | None = None, search=search_pixabay):
    """Yields (hit, query) for unused clips of the right length, queries in a seed-dependent order."""
    order = list(queries or COMEDY_QUERIES)
    random.Random(seed).shuffle(order)
    for query in order:
        hits = [
            h for h in search(query, per_page=50)
            if f"kd-{h.get('id')}" not in published
            and MIN_CLIP_SECONDS <= (h.get("duration") or 0) <= MAX_CLIP_SECONDS
        ]
        hits.sort(key=lambda h: (h.get("likes", 0), h.get("downloads", 0)), reverse=True)
        top = hits[:12]
        random.Random(seed + query).shuffle(top)
        for hit in top:
            yield hit, query


def _smallest_rendition(hit: dict) -> dict | None:
    renditions = [
        {"link": r.get("url"), "width": r.get("width"), "height": r.get("height"), "size": r.get("size") or 0}
        for r in (hit.get("videos") or {}).values() if isinstance(r, dict) and r.get("url") and r.get("size")
    ]
    renditions = [r for r in renditions if r["size"] <= MAX_PREVIEW_BYTES]
    return min(renditions, key=lambda r: r["size"]) if renditions else None


def download_clip(hit: dict, dest_dir: Path) -> tuple[Path, Path]:
    """Returns (preview for Gemini, best rendition for the render)."""
    preview_info, render_info = _smallest_rendition(hit), _pick_pixabay_file(hit)
    if not preview_info or not render_info:
        raise ClipUnusable("no suitable rendition")
    dest_dir.mkdir(parents=True, exist_ok=True)
    render = _download(render_info["link"], dest_dir / f"clip_{hit['id']}.mp4", timeout=180)
    if preview_info["link"] == render_info["link"]:
        return render, render
    preview = _download(preview_info["link"], dest_dir / f"clip_{hit['id']}_preview.mp4", timeout=120)
    return preview, render


def word_budget(duration: float) -> int:
    return max(8, int(duration * WORDS_PER_SECOND))


def line_budget(duration: float) -> int:
    return max(MIN_LINES, min(MAX_LINES, int(duration // 2.5)))


def build_prompt(duration: float) -> str:
    return PROMPT_TEMPLATE.format(
        duration=duration, min_lines=MIN_LINES, max_lines=line_budget(duration),
        max_words=word_budget(duration), last_start=max(0.0, duration - 3.0),
    )


def _call_gemini_video(prompt: str, video: Path) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ScriptError("comedy dub needs GEMINI_API_KEY (video understanding)")
    model = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video.read_bytes()).decode()}},
                {"text": prompt},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0},
        },
        {"x-goog-api-key": key},
        timeout=120,
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def validate_comedy_script(script: dict, duration: float) -> dict:
    if not isinstance(script, dict):
        raise ScriptError("script is not an object")
    if script.get("usable") is False:
        raise ClipUnusable("model marked the clip unusable")

    title = re.sub(r"#\w+", "", str(script.get("title") or "")).strip(" -|")
    if not 8 <= len(title) <= 90:
        raise ScriptError(f"title length {len(title)} out of range")

    lines = []
    for raw in script.get("lines") or []:
        if not isinstance(raw, dict):
            continue
        text = " ".join(EMOJI_RE.sub("", str(raw.get("text") or "")).split())
        speaker = " ".join(str(raw.get("speaker") or "anlatıcı").split()).lower()[:20] or "anlatıcı"
        try:
            start = float(raw.get("start", 0))
        except (TypeError, ValueError):
            continue
        if not text:
            continue
        if len(text.split()) > MAX_LINE_WORDS:
            raise ScriptError("line too long")
        lines.append({"start": min(max(0.0, start), max(0.0, duration - 0.5)), "speaker": speaker, "text": text})
    lines.sort(key=lambda line: line["start"])
    if not MIN_LINES <= len(lines) <= MAX_LINES:
        raise ScriptError(f"line count {len(lines)} out of range")
    if len({line["speaker"] for line in lines}) > MAX_SPEAKERS:
        raise ScriptError("too many speakers")
    words = sum(len(line["text"].split()) for line in lines)
    if words > word_budget(duration) * MAX_WORDS_SLACK:
        raise ScriptError(f"{words} words do not fit a {duration:.1f}s clip")

    spoken = " ".join([title] + [line["text"] for line in lines]).lower()
    hits = [name for name in BANNED_REFERENCES if name in spoken]
    if hits:
        raise ScriptError(f"script references fiction/film: {', '.join(hits)}")

    hook = " ".join(re.sub(r"[{}\\]", "", EMOJI_RE.sub("", str(script.get("hook_text") or ""))).split())
    if not hook or len(hook) > MAX_HOOK_CHARS:
        hook = "SONUNA KADAR İZLE"

    tags = [str(t).strip().lstrip("#") for t in (script.get("tags") or []) if str(t).strip()]
    return {
        "title": title,
        "hook_text": hook,
        "lines": lines,
        "description": str(script.get("description") or "").strip(),
        "tags": tags[:15],
        "pinned_comment": str(script.get("pinned_comment") or "").strip(),
        "en_title": str(script.get("en_title") or "").strip()[:90],
        "en_description": str(script.get("en_description") or "").strip(),
    }


def write_comedy_script(preview: Path, duration: float, attempts: int = 2, call=_call_gemini_video) -> dict:
    prompt = build_prompt(duration)
    errors = []
    for attempt in range(1, attempts + 1):
        try:
            return validate_comedy_script(_extract_json(call(prompt, preview)), duration)
        except ClipUnusable:
            raise
        except (ScriptError, ValueError, KeyError, IndexError, TimeoutError, OSError) as e:
            errors.append(f"#{attempt}: {e}")
            print(f"ℹ️ Dublaj senaryosu reddedildi (#{attempt}): {e}")
    raise ScriptError("no valid comedy script: " + " | ".join(errors))


def place_lines(lines: list[dict], durations: list[float], gap: float = 0.12) -> list[dict]:
    """Keeps each line at its requested start unless the previous one is still talking."""
    placed, cursor = [], 0.0
    for line, dur in zip(lines, durations):
        start = max(line["start"], cursor)
        placed.append({**line, "start": round(start, 3), "end": round(start + dur, 3)})
        cursor = start + dur + gap
    return placed
