"""
Original Shorts script writer for Zirvenin Kanunu.

Turns one topic from the topic queue into a short, factual, hook-first narration
with a visual brief per scene. No film characters, quotes or footage: every
scene is illustrated later with licensed stock video or a generated image.

Providers are tried cheapest first: Gemini (free tier) -> Anthropic-compatible
endpoint -> OpenAI-compatible endpoint. The first script that passes validation
wins; if none does, the run stops instead of uploading a weak video.
"""

import json
import os
import re
import urllib.error
import urllib.request

MIN_SCENES = 5
MAX_SCENES = 8
MIN_WORDS = 55
MAX_WORDS = 125
MAX_SCENE_CHARS = 230

# Film/series characters and quotes the old catalog was built on. A script that
# leans on them is rejected so the channel stays clear of copyright claims.
BANNED_REFERENCES = [
    "thomas shelby", "shelby", "peaky blinders", "walter white", "heisenberg",
    "breaking bad", "tyler durden", "fight club", "joker", "batman", "corleone",
    "godfather", "baba filmi", "tony montana", "scarface", "jordan belfort",
    "wolf of wall street", "matrix filmi", "morpheus", "interstellar",
    "oppenheimer filmi", "gladiator filmi", "maximus decimus", "kolpaçino",
    "kolpacino", "çakır", "polat alemdar", "kurtlar vadisi", "patrick bateman",
]

PROMPT_TEMPLATE = """Sen "Zirvenin Kanunu" adlı Türkçe YouTube Shorts kanalının baş senaristisin.
Kanal; güç, karar verme ve insan psikolojisini tarihten gerçek olaylar ve bilimsel
deneylerle anlatıyor.

Konu: {topic}

Bu konu için 25-40 saniyelik bir Shorts senaryosu yaz.

Kurallar:
- İlk cümle en fazla 12 kelimelik, merak uyandıran bir kanca olsun. "Biliyor muydun" ile başlama.
- Toplam {min_words}-{max_words} kelime, {min_scenes}-{max_scenes} sahne. Her sahne 1-2 kısa cümle.
- Sadece doğruluğu iyi bilinen, kaynaklarda yer alan bilgileri kullan. Emin olmadığın tarih, sayı veya alıntıyı yazma.
- Film, dizi veya kurgu karakterlerinden ve onların repliklerinden hiç bahsetme.
- Abartılı vaat, "sigma", "zengin olursun" gibi ifadeler yok. Sakin ama vurucu bir anlatım.
- Son cümle izleyiciye düşündürücü bir soru veya ders bıraksın.
- Her sahne için görsel tarifini İngilizce yaz: gerçek kişilerin yüzünü birebir canlandırma; sembolik sahneler,
  silüetler, mekânlar, nesneler, dönem atmosferi kullan. Görselde yazı, logo, filigran olmasın.
- "stock_query": stok video aramak için 2-4 İngilizce kelime (ör. "ancient rome ruins", "chess board closeup").
- "source": sahne gerçek bir mekân veya nesneyle iyi anlatılıyorsa "stock", dönem veya sembolik bir sahne gerekiyorsa "ai".

Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "title": "En fazla 70 karakterlik Türkçe başlık, emoji ve #shorts olmadan",
  "scenes": [
    {{"text": "Türkçe anlatım", "visual": "English image prompt", "stock_query": "english keywords", "source": "ai"}}
  ],
  "description": "2-3 cümlelik Türkçe açıklama",
  "tags": ["8-12 Türkçe etiket"],
  "pinned_comment": "İzleyiciyi tartışmaya çağıran tek bir soru",
  "en_title": "English title, max 70 chars",
  "en_description": "1-2 sentence English description"
}}
"""


class ScriptError(RuntimeError):
    pass


def build_prompt(topic: str) -> str:
    return PROMPT_TEMPLATE.format(
        topic=topic.strip(),
        min_words=MIN_WORDS,
        max_words=MAX_WORDS,
        min_scenes=MIN_SCENES,
        max_scenes=MAX_SCENES,
    )


def _extract_json(raw_text: str) -> dict:
    if not raw_text or "{" not in raw_text:
        raise ScriptError("model returned no JSON")
    return json.loads(raw_text[raw_text.find("{"):raw_text.rfind("}") + 1])


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_gemini(prompt: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SCRIPT_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    data = _post_json(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
        },
        {"x-goog-api-key": key},
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_anthropic(prompt: str) -> str | None:
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        return None
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("ANTHROPIC_SCRIPT_MODEL", "claude-sonnet-4-6")
    data = _post_json(
        f"{base}/v1/messages",
        {"model": model, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {token}", "x-api-key": token, "anthropic-version": "2023-06-01"},
    )
    return "".join(block.get("text", "") for block in data.get("content", []))


def _call_openai(prompt: str) -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_SCRIPT_MODEL", "gpt-5-mini")
    data = _post_json(
        f"{base}/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {key}"},
    )
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


PROVIDERS = [("gemini", _call_gemini), ("anthropic", _call_anthropic), ("openai", _call_openai)]


def validate_script(script: dict) -> dict:
    """Normalizes a model reply and raises ScriptError if it is not publishable."""
    if not isinstance(script, dict):
        raise ScriptError("script is not an object")

    title = str(script.get("title") or "").strip()
    title = re.sub(r"#\w+", "", title).strip(" -|")
    if not 8 <= len(title) <= 90:
        raise ScriptError(f"title length {len(title)} out of range")

    scenes = []
    for raw in script.get("scenes") or []:
        if not isinstance(raw, dict):
            continue
        text = " ".join(str(raw.get("text") or "").split())
        visual = " ".join(str(raw.get("visual") or "").split())
        if not text or not visual:
            continue
        if len(text) > MAX_SCENE_CHARS:
            raise ScriptError("scene narration too long")
        source = str(raw.get("source") or "ai").strip().lower()
        scenes.append({
            "text": text,
            "visual": visual,
            "stock_query": " ".join(str(raw.get("stock_query") or "").split())[:80],
            "source": source if source in ("ai", "stock") else "ai",
        })
    if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        raise ScriptError(f"scene count {len(scenes)} out of range")

    words = sum(len(s["text"].split()) for s in scenes)
    if not MIN_WORDS <= words <= MAX_WORDS:
        raise ScriptError(f"narration word count {words} out of range")

    narration = " ".join([title] + [s["text"] for s in scenes]).lower()
    hits = [name for name in BANNED_REFERENCES if name in narration]
    if hits:
        raise ScriptError(f"script references fiction/film: {', '.join(hits)}")

    tags = [str(t).strip().lstrip("#") for t in (script.get("tags") or []) if str(t).strip()]
    return {
        "title": title,
        "scenes": scenes,
        "description": str(script.get("description") or "").strip(),
        "tags": tags[:15],
        "pinned_comment": str(script.get("pinned_comment") or "").strip(),
        "en_title": str(script.get("en_title") or "").strip()[:90],
        "en_description": str(script.get("en_description") or "").strip(),
    }


def write_script(topic: str, providers=None, attempts_per_provider: int = 2) -> tuple[dict, str]:
    """Returns (validated_script, provider_name). Raises ScriptError if all providers fail."""
    prompt = build_prompt(topic)
    errors = []
    for name, call in providers or PROVIDERS:
        for attempt in range(1, attempts_per_provider + 1):
            try:
                raw = call(prompt)
                if raw is None:
                    break  # provider not configured
                return validate_script(_extract_json(raw)), name
            except (ScriptError, ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError) as e:
                errors.append(f"{name}#{attempt}: {e}")
                print(f"ℹ️ Senaryo denemesi reddedildi ({name} #{attempt}): {e}")
    raise ScriptError("no provider produced a valid script: " + " | ".join(errors or ["no provider configured"]))
