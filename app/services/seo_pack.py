"""
Search and discovery package for every upload.

One Gemini call turns the finished script into a fuller Turkish description,
20-25 tags that fit YouTube's 500-character tag limit, a few hashtags, and the
title and description in 18 more languages (snippet localizations), so the
video can surface in searches outside Turkey. Without Gemini, or when its
answer does not validate, the script's own description and tags are used.
The curiosity series gets its own prompt: the history prompt's fixed
"Bugün için ders" line and subscribe call ended up in every merak Short.
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
# YouTube's own advice is that tags mainly help with commonly misspelled words, and Turkish viewers often type without
# Turkish letters ("baykus"), so the most specific Turkish tags also go in folded, in whatever budget is left.
FOLDED_TAGS = 5
TURKISH_FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")

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

# The owl Short carried the history template's "Bugün için ders: ..." line and 15 of its 30 tags were English. A fixed
# line in every description is the "generic template" signal of YouTube's inauthentic-content policy, so the curiosity
# description is built only from this video's own question, facts and numbers.
MERAK_PROMPT_TEMPLATE = """Bir Türkçe YouTube Shorts merak videosunun arama ve keşfet paketini hazırlıyorsun.

Video başlığı: {title}
Konu: {topic}
Seslendirilen metin: {narration}

Kurallar:
- "description": Türkçe, 3 kısa paragraf, aralarında boş satır:
  1) İnsanların arama kutusuna yazacağı haliyle videonun sorusu, tek cümle (ör. "Baykuşlar başını nasıl 270 derece
     çevirir?").
  2) Cevap, 2-3 cümle. Seslendirilen metindeki sayıları ve adları aynen kullan; metinde olmayan bilgi ekleme.
  3) Videonun yorum sorusu: {question}
     İki seçenekliyse aynen yaz; değilse ya da yoksa bu videoya özel, iki seçenekli bir soru yaz (ör. "Sence
     insan boynu kaç derece döner: 90 mı 180 mi?").
  Ders çıkaran ya da abone olmaya, beğenmeye, kanalı takip etmeye çağıran, her videoda aynı kalan kalıp cümleler
  yazma. Kaynak, telif, görüntü veya yapay zekâ bilgisi yazma. Hashtag yazma.
- "hashtags": 3-5 Türkçe hashtag, # ile başlasın: önce videodaki canlı ya da nesne (ör. #baykuş), sonra konu
  (ör. #hayvanlar). #shorts yazma.
- "tags": 20-25 arama etiketi, en az %80'i Türkçe. Önce en özel olanlar: videodaki canlı ya da nesnenin adı,
  anlatılan olgu, insanların yazacağı soru kalıpları (ör. "baykuş boynu", "baykuş kafasını nasıl çevirir").
  Sonra genel olanlar (hayvanlar, bilim, ilginç bilgiler). İngilizce en fazla 4 etiket, sadece canlının ya da
  olgunun adı. Her biri en fazla 30 karakter, # yok.
- "localizations": şu dil kodlarının her biri için {{"title": "...", "description": "..."}}: {languages}.
  Başlık o dilde doğal ve merak uyandıran bir cümle, en fazla 90 karakter, sonunda Türkçe başlıktaki emoji.
  Açıklama o dilde 2-3 cümle. Çeviri kokmasın, o dilin YouTube izleyicisine yazılmış gibi olsun.

Sadece şu JSON'u döndür:
{{"description": "...", "hashtags": ["#..."], "tags": ["..."], "localizations": {{"en": {{"title": "...", "description": "..."}}}}}}
"""


def build_prompt(title: str, topic: str, narration: str, series: str = "story", question: str = "") -> str:
    """`question` is the video's own comment question; the curiosity description ends on it (the history one
    ignores it), so the description and the pinned comment ask the same thing."""
    template = MERAK_PROMPT_TEMPLATE if series == "merak" else PROMPT_TEMPLATE
    return template.format(
        title=title, topic=topic, narration=narration, languages=", ".join(LANGUAGES),
        question=question.strip() or "(yok)",
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


def fold_turkish(text: str) -> str:
    return text.translate(TURKISH_FOLD)


def _as_list(value, sep: str | None = None) -> list:
    """A list field of Gemini's answer as a list. A single string is split (list() of it would make one tag or
    hashtag per letter, and the letters would now lead the hashtags above the title); anything else is dropped."""
    if isinstance(value, str):
        return value.replace(",", " ").split() if sep is None else value.split(sep)
    return list(value) if isinstance(value, (list, tuple)) else []


def clean_tags(tags: list, limit: int = MAX_TAG_TOTAL, fold: int = 0) -> list[str]:
    """Tags in order within YouTube's limit; then ASCII copies of the first `fold` tags with Turkish letters.

    The folded copies only take what the real tags left of the character budget, and MAX_TAGS (our own cap,
    not YouTube's) does not count them, so they never push out a tag but still fit after a full list."""
    seen, out, total = set(), [], 0
    for raw in tags:
        tag = " ".join(re.sub(r"[<>#,\"]", " ", EMOJI_RE.sub("", str(raw))).split())
        key = tag.lower().replace(" ", "")  # "MarcusAurelius" repeats "Marcus Aurelius"
        if not tag or len(tag) > MAX_TAG_CHARS or key in seen:
            continue
        if total + _tag_cost(tag) > limit or len(out) >= MAX_TAGS:
            break
        seen.add(key)
        out.append(tag)
        total += _tag_cost(tag)
    for tag in [fold_turkish(t) for t in out if fold_turkish(t) != t][:fold]:
        key = tag.lower().replace(" ", "")
        if key not in seen and total + _tag_cost(tag) <= limit:
            seen.add(key)
            out.append(tag)
            total += _tag_cost(tag)
    return out


def _hashtag_words(items) -> list[str]:
    out = []
    for raw in items:
        word = re.sub(r"[^\w]", "", str(raw))
        if word and f"#{word}".lower() not in {h.lower() for h in out}:
            out.append(f"#{word}")
    return out


def clean_hashtags(hashtags: list, base: str, limit: int = 8) -> str:
    """Topical hashtags first: YouTube shows the first three above the title, and #baykuş tells a viewer more
    than #shorts. The base ones follow, so #shorts stays in every description."""
    base_words = _hashtag_words(base.split())
    known = {h.lower() for h in base_words}
    topical = [h for h in _hashtag_words(_as_list(hashtags)) if h.lower() not in known]
    return " ".join((topical[:max(3, limit - len(base_words))] + base_words)[:limit])


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
                   base_hashtags: str, marker: str, call=_call_gemini_text, series: str = "story") -> dict:
    """Returns {"description", "tags", "localizations", "seo_engine"} ready for the upload body."""
    pinned = f"💬 {script['pinned_comment']}" if script.get("pinned_comment") else ""
    fallback_localizations = {}
    if script.get("en_title"):
        fallback_localizations["en"] = {"title": script["en_title"], "description": script.get("en_description", "")}
    pack = {
        "description": script.get("description", ""),
        "hashtags": clean_hashtags([], base_hashtags),
        "tags": clean_tags(list(script.get("tags", [])) + base_tags, fold=FOLDED_TAGS),
        "localizations": fallback_localizations,
        "seo_engine": "script",
    }
    try:
        raw = call(build_prompt(title, topic, narration, series, str(script.get("pinned_comment") or "")))
        data = _extract_json(raw) if raw else None
    except Exception as e:  # SEO is a bonus; never block an upload on it
        print(f"ℹ️ SEO paketi notu: {str(e)[:200]}")
        data = None
    if isinstance(data, dict):
        description = str(data.get("description") or "").strip()
        localizations = clean_localizations(data.get("localizations"))
        # The long 18-language answer often comes back with only some languages; a good Turkish description and
        # tags are worth keeping anyway, and the script's English stays when Gemini left English out.
        if len(description) >= 120:
            pack.update(
                description=description,
                hashtags=clean_hashtags(data.get("hashtags"), base_hashtags),
                tags=clean_tags(_as_list(data.get("tags"), ",") + list(script.get("tags", [])) + base_tags,
                                fold=FOLDED_TAGS),
                localizations={**fallback_localizations, **localizations},
                seo_engine="gemini",
            )
        else:
            print(f"ℹ️ SEO paketi zayıf geldi (açıklama {len(description)} karakter, {len(localizations)} dil); "
                  "senaryonun açıklaması kullanılıyor.")

    if pack["seo_engine"] == "gemini":
        pinned = ""  # the Gemini description already ends with its own call to comment
    pack["description"] = "\n\n".join(part for part in [pack["description"], pinned, pack["hashtags"], marker] if part)
    for item in pack["localizations"].values():
        # Titles carry no #shorts: YouTube has not needed it to classify a Short since 15 Oct 2024.
        item["description"] = f"{item['description']}\n\n#shorts\n\n{marker}"
        item["title"] = item["title"][:100]
    del pack["hashtags"]
    return pack
