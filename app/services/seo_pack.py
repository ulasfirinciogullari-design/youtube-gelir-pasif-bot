"""
Search and discovery package for every upload.

One Gemini call turns the finished script into a fuller Turkish description,
20-25 tags that fit YouTube's 500-character tag limit, a few hashtags, and the
title and description in 18 more languages (snippet localizations), so the
video can surface in searches outside Turkey. Without Gemini, or when its
answer does not validate, the script's own description and tags are used.
"""

import os
import re

from app.services.original_script import EMOJI_RE, _extract_json, _post_json

LANGUAGES = {
    "en": "English", "es": "Spanish", "pt": "Portuguese", "de": "German", "fr": "French", "it": "Italian",
    "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "id": "Indonesian", "ja": "Japanese", "ko": "Korean",
    "pl": "Polish", "nl": "Dutch", "uk": "Ukrainian", "vi": "Vietnamese", "az": "Azerbaijani", "ro": "Romanian",
}
MAX_TAG_TOTAL = 480  # YouTube rejects more than 500 characters of tags
MAX_TAG_CHARS = 30
MAX_TAGS = 30
MIN_LOCALIZATIONS = 10

PROMPT_TEMPLATE = """Bir Türkçe YouTube Shorts videosunun arama ve keşfet paketini hazırlıyorsun.

Video başlığı: {title}
Konu: {topic}
Seslendirilen metin: {narration}

Kurallar:
- "description": Türkçe, 4 kısa paragraf, aralarında boş satır:
  1) Merak uyandıran tek cümlelik bir soru.
  2) Hikâyenin özü, 2-3 cümle; kişi, dönem ve kavram adlarını doğal şekilde geçir.
  3) "Bugün için ders:" ile başlayan tek cümle.
  4) İzleyiciyi yorum yazmaya ve kanala abone olmaya çağıran tek cümle.
  Kaynak, telif, görüntü veya yapay zekâ bilgisi yazma. Hashtag yazma.
- "hashtags": 3-5 Türkçe hashtag, # ile başlasın, #shorts yazma.
- "tags": 20-25 arama etiketi. Önce özel olanlar (kişi, olay, dönem, kavram), sonra genel olanlar
  (tarih, psikoloji, felsefe). Yarısı Türkçe, yarısı İngilizce; her biri en fazla 30 karakter, # yok.
- "localizations": şu dil kodlarının her biri için {{"title": "...", "description": "..."}}: {languages}.
  Başlık o dilde doğal ve merak uyandıran bir cümle, en fazla 90 karakter, sonunda Türkçe başlıktaki emoji.
  Açıklama o dilde 2-3 cümle. Çeviri kokmasın, o dilin YouTube izleyicisine yazılmış gibi olsun.

Sadece şu JSON'u döndür:
{{"description": "...", "hashtags": ["#..."], "tags": ["..."], "localizations": {{"en": {{"title": "...", "description": "..."}}}}}}
"""


def build_prompt(title: str, topic: str, narration: str) -> str:
    return PROMPT_TEMPLATE.format(
        title=title, topic=topic, narration=narration, languages=", ".join(LANGUAGES),
    )


def _call_gemini_text(prompt: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SEO_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
        },
        {"x-goog-api-key": key},
        timeout=150,
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _tag_cost(tag: str) -> int:
    # YouTube counts a tag with spaces as if it were quoted, plus a comma between tags.
    return len(tag) + (2 if " " in tag else 0) + 1


def clean_tags(tags: list, limit: int = MAX_TAG_TOTAL) -> list[str]:
    seen, out, total = set(), [], 0
    for raw in tags:
        tag = " ".join(re.sub(r"[<>#,\"]", " ", EMOJI_RE.sub("", str(raw))).split())
        if not tag or len(tag) > MAX_TAG_CHARS or tag.lower() in seen:
            continue
        if total + _tag_cost(tag) > limit or len(out) >= MAX_TAGS:
            break
        seen.add(tag.lower())
        out.append(tag)
        total += _tag_cost(tag)
    return out


def clean_hashtags(hashtags: list, base: str) -> str:
    out = []
    for raw in base.split() + [str(h) for h in hashtags or []]:
        word = re.sub(r"[^\w]", "", raw)
        if word and f"#{word}".lower() not in {h.lower() for h in out}:
            out.append(f"#{word}")
    return " ".join(out[:8])


def clean_localizations(raw: dict) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for code in LANGUAGES:
        item = raw.get(code)
        if not isinstance(item, dict):
            continue
        title = " ".join(re.sub(r"#\w+", "", str(item.get("title") or "")).split())[:90]
        description = str(item.get("description") or "").strip()[:1500]
        if len(title) >= 8 and len(description) >= 20:
            out[code] = {"title": title, "description": description}
    return out


def build_seo_pack(title: str, topic: str, narration: str, script: dict, base_tags: list[str],
                   base_hashtags: str, marker: str, call=_call_gemini_text) -> dict:
    """Returns {"description", "tags", "localizations", "seo_engine"} ready for the upload body."""
    pinned = f"💬 {script['pinned_comment']}" if script.get("pinned_comment") else ""
    fallback_localizations = {}
    if script.get("en_title"):
        fallback_localizations["en"] = {"title": script["en_title"], "description": script.get("en_description", "")}
    pack = {
        "description": script.get("description", ""),
        "hashtags": clean_hashtags([], base_hashtags),
        "tags": clean_tags(list(script.get("tags", [])) + base_tags),
        "localizations": fallback_localizations,
        "seo_engine": "script",
    }
    try:
        raw = call(build_prompt(title, topic, narration))
        data = _extract_json(raw) if raw else None
    except Exception as e:  # SEO is a bonus; never block an upload on it
        print(f"ℹ️ SEO paketi notu: {str(e)[:200]}")
        data = None
    if isinstance(data, dict):
        description = str(data.get("description") or "").strip()
        localizations = clean_localizations(data.get("localizations"))
        if len(description) >= 120 and len(localizations) >= MIN_LOCALIZATIONS:
            pack.update(
                description=description,
                hashtags=clean_hashtags(data.get("hashtags"), base_hashtags),
                tags=clean_tags(list(data.get("tags") or []) + list(script.get("tags", [])) + base_tags),
                localizations=localizations,
                seo_engine="gemini",
            )
        else:
            print(f"ℹ️ SEO paketi zayıf geldi (açıklama {len(description)} karakter, {len(localizations)} dil); "
                  "senaryonun açıklaması kullanılıyor.")

    pack["description"] = "\n\n".join(part for part in [pack["description"], pinned, pack["hashtags"], marker] if part)
    for item in pack["localizations"].values():
        item["description"] = f"{item['description']}\n\n#shorts\n\n{marker}"
        item["title"] = f"{item['title']} #shorts" if len(item["title"]) <= 92 else item["title"][:100]
    del pack["hashtags"]
    return pack
