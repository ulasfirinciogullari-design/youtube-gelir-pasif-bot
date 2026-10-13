"""
Original Shorts script writer for Zirvenin Kanunu.

Turns one topic from the topic queue into a short, factual, hook-first narration
with a visual brief per scene. Every scene is illustrated later with licensed
stock video or a generated image; no film footage is ever used.

Two series share the writer: history stories (no fiction at all) and character
commentary, which may talk about a film or series character but never quotes
scenes at length and never names a character or actor in an image prompt, so
the generated pictures cannot imitate an actor.

Providers are tried cheapest first: Gemini (free tier) -> Anthropic-compatible
endpoint -> OpenAI-compatible endpoint. The first script that passes validation
wins; if none does, the run stops instead of uploading a weak video.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

MIN_SCENES = 5
MAX_SCENES = 8
MIN_WORDS = 55
MAX_WORDS = 95  # ~40 s of narration; the first real run hit 48.6 s at 125 words
MAX_SCENE_CHARS = 230
MAX_HOOK_CHARS = 40
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")

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

# Names that would make the image model draw a real actor. Character scripts are
# scrubbed of these (plus the topic's own names) in every image prompt.
LIKENESS_NAMES = [
    "thomas shelby", "tommy shelby", "walter white", "heisenberg", "tyler durden", "joker", "batman",
    "corleone", "tony montana", "jordan belfort", "morpheus", "maximus", "polat alemdar", "patrick bateman",
    "cillian murphy", "bryan cranston", "brad pitt", "edward norton", "marlon brando", "al pacino",
    "heath ledger", "joaquin phoenix", "christian bale", "leonardo dicaprio", "russell crowe",
    "matthew mcconaughey", "keanu reeves", "anthony hopkins", "necati şaşmaz", "kenan imirzalıoğlu",
]

JSON_SPEC = """Sadece şu JSON'u döndür, başka hiçbir şey yazma:
{{
  "title": "Türkçe başlık + tek emoji",
  "hook_text": "2-4 kelimelik kanca yazısı",
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

PROMPT_TEMPLATE = """Sen "Zirvenin Kanunu" adlı Türkçe YouTube Shorts kanalının baş senaristisin.
Kanal; tarihten gerçek insanların gerilimli anlarını, dahice hamlelerini ve psikoloji deneylerini
kısa, vurucu hikâyeler olarak anlatıyor.

Konu: {topic}

Bu konu için 25-40 saniyelik bir Shorts senaryosu yaz. Bir belgesel anlatıcısı gibi akıcı, kendinden emin
ve vurucu konuş; cümleler birbirine bağlansın, kesik kesik olmasın.

Anlatım kuralları:
- İlk cümle en fazla 12 kelimelik, merak uyandıran bir kanca olsun. "Biliyor muydun", "Merhaba", "Bugün" ile başlama.
- Toplam {min_words}-{max_words} kelime, {min_scenes}-{max_scenes} sahne. Her sahne 1-2 akıcı cümle.
- Hikâyeyi bir an veya olay üzerinden anlat, ortada bir dönüş olsun, son cümle izleyiciye düşündürücü bir soru veya ders bıraksın.
- Sadece doğruluğu iyi bilinen, kaynaklarda yer alan bilgileri kullan. Emin olmadığın tarih, sayı veya alıntıyı yazma.
- Film, dizi veya kurgu karakterlerinden ve repliklerinden hiç bahsetme. Anlatımda emoji kullanma, metin seslendirilecek.
- Abartılı vaat, "sigma", "zengin olursun" gibi ifadeler yok. Sakin ama vurucu bir anlatım.

Görsel kuralları:
- Her sahne için İngilizce görsel tarifi yaz: o cümlede anlatılan anı gösteren somut bir film karesi (kim, nerede, ne yapıyor,
  kadraj ve ışık). Ardışık sahneler aynı dönem ve mekânda tutarlı olsun. Gerçek kişilerin yüzünü birebir canlandırma;
  silüet, el, göz, nesne, mekân ve dönem atmosferi kullan. Görselde yazı, logo, filigran olmasın.
- "stock_query": stok video aramak için 2-4 İngilizce kelime (ör. "ancient rome ruins", "chess board closeup").
- "source": sahne gerçek bir mekân veya nesneyle iyi anlatılıyorsa "stock", dönem veya sembolik bir sahne gerekiyorsa "ai".

Başlık ve kanca:
- "title": en fazla 60 karakter; sahneyi şimdiki zamanla anlatan, merak uyandıran tek cümle ve sonunda tek bir uygun emoji.
  Örnekler: "Şifreli mesajı bir bakışta çözüyor 🧐", "Ordusu kaçarken tek başına geri dönüyor 😳". #shorts yazma.
- "hook_text": videonun ilk 2 saniyesinde ekranın üstünde duracak 2-4 kelimelik büyük yazı, emoji yok (ör. "KİMSE FARK ETMEDİ").
{style_examples}
""" + JSON_SPEC

CHARACTER_PROMPT_TEMPLATE = """Sen "Zirvenin Kanunu" adlı Türkçe YouTube Shorts kanalının baş senaristisin.
Bu seride ünlü film ve dizi karakterlerinin kararlarını, kurallarını ve psikolojisini kendi yorumumuzla anlatıyoruz.
Film sahnesi, replik kaydı veya oyuncu görüntüsü kullanmıyoruz; videoyu bizim anlatımımız ve özgün görseller taşıyor.

Konu: {topic}
Karakter: {character} ({work})

Bu konu için 25-40 saniyelik bir Shorts senaryosu yaz. Bir belgesel anlatıcısı gibi akıcı, kendinden emin
ve vurucu konuş; cümleler birbirine bağlansın, kesik kesik olmasın.

Anlatım kuralları:
- İlk cümle en fazla 12 kelimelik, merak uyandıran bir kanca olsun ve karakterin adı ilk iki cümlede geçsin.
- Toplam {min_words}-{max_words} kelime, {min_scenes}-{max_scenes} sahne. Her sahne 1-2 akıcı cümle.
- Karakterin bilinen bir tavrını veya kararını anlat, sonra bunu gerçek bir psikoloji ilkesi, deney veya tarihten bir örnekle bağla.
  Son cümle izleyiciye düşündürücü bir soru veya ders bıraksın.
- Sahneyi baştan anlatma, yorumla: karakter ne yaptı, neden işe yaradı, gerçek hayatta ne anlama gelir.
- Sadece filmde veya dizide gerçekten olan, iyi bilinen şeyleri anlat. Emin olmadığın sahne, sezon, sayı veya replik uydurma.
- Replikleri birebir alıntılama; en fazla birkaç kelimelik meşhur bir ifadeyi anabilirsin, gerisini kendi cümlelerinle anlat.
- Anlatımda emoji kullanma, metin seslendirilecek. "Sigma", "zengin olursun" gibi ifadeler yok.

Görsel kuralları:
- Görsel dünya: {look}
- Her sahne için İngilizce görsel tarifi yaz: o cümlede anlatılan anı bu dünyada somut olarak gösteren tek bir film karesi.
  Genel tarif yazma ("a dark room" değil); kim, nerede, ne yapıyor, kadraj (close-up, wide, over-the-shoulder) ve ışık nereden geliyor yaz.
  Ardışık sahneler aynı filmden kareler gibi tutarlı olsun: aynı dönem, aynı kıyafet, aynı mekânlar.
- Görsel tarifinde karakter, oyuncu, film veya dizi adı ASLA geçmesin. Kimsenin yüzü net görünmesin: arkadan çekim, silüet,
  gölgede kalan yüz, eller, nesneler. Görselde yazı, logo, filigran olmasın.
- "stock_query": 2-4 İngilizce kelime. "source": her sahnede "ai".

Başlık ve kanca:
- "title": en fazla 60 karakter; karakterin adını içeren, merak uyandıran tek cümle ve sonunda tek bir uygun emoji.
  Örnekler: "Thomas Shelby odaya girince neden susuyor? 🤫", "Walter White'ı korkutucu yapan tek alışkanlık 😳". #shorts yazma.
- "hook_text": videonun ilk 2 saniyesinde ekranın üstünde duracak 2-4 kelimelik büyük yazı, emoji yok (ör. "ASLA ÖZÜR DİLEMEZ").
{style_examples}
""" + JSON_SPEC


class ScriptError(RuntimeError):
    pass


class ProviderHTTPError(ScriptError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def build_prompt(topic: str, examples: list[str] | None = None, rules: list[str] | None = None,
                 character: dict | None = None) -> str:
    """character = {"character": ..., "work": ...} switches to the character commentary series."""
    style = ""
    if examples:
        style = "- Kanalda en çok izlenen başlıklar bunlar; tarzını örnek al ama kopyalama:\n" + "".join(
            f"  - {t}\n" for t in examples
        )
    if rules:
        style += "- Rakip kanallardan ayrışmak için bu kurallara da uy:\n" + "".join(f"  - {r}\n" for r in rules[:8])
    template = CHARACTER_PROMPT_TEMPLATE if character else PROMPT_TEMPLATE
    return template.format(
        topic=topic.strip(),
        character=(character or {}).get("character", ""),
        work=(character or {}).get("work", ""),
        look=(character or {}).get("look") or "karakterin filmdeki/dizideki dönemi, kıyafeti ve mekânları",
        style_examples=style,
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # The body usually names the real problem (unknown model, bad key, wrong path).
        parts = urllib.parse.urlsplit(url)
        detail = " ".join(e.read().decode("utf-8", "replace").split())[:300]
        raise ProviderHTTPError(e.code, f"HTTP {e.code} from {parts.netloc}{parts.path}: {detail}") from e


# Reseller keys often only unlock part of the catalog ("model is not available for
# this group"). When the configured model is refused, the endpoint's own model list
# is printed and the first usable match from this list is tried instead.
MODEL_PREFERENCE = [
    "claude-sonnet", "gpt-5", "gpt-4.1", "gpt-4o", "claude", "gemini", "grok", "deepseek-v4-pro", "deepseek", "qwen",
]
NON_CHAT_MARKERS = (
    "embed", "image", "tts", "whisper", "audio", "realtime", "moderation", "dall-e", "transcribe", "vision", "expires",
)
_model_overrides: dict[str, str] = {}


def _available_models(models_url: str, headers: dict) -> list[str]:
    req = urllib.request.Request(models_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("data", [])
    except Exception as e:
        print(f"ℹ️ Model listesi alınamadı ({urllib.parse.urlsplit(models_url).netloc}): {e}")
        return []
    return [m["id"] for m in data if isinstance(m, dict) and m.get("id")]


def pick_fallback_model(available: list[str], prefer: str = "") -> str | None:
    usable = [m for m in available if not any(x in m.lower() for x in NON_CHAT_MARKERS)]
    usable = [m for m in usable if prefer and prefer in m.lower()] or usable
    for preferred in MODEL_PREFERENCE:
        for model in usable:
            if preferred in model.lower():
                return model
    return usable[0] if usable else None


def _post_with_model_fallback(provider: str, model: str, send, models_url: str, headers: dict, prefer: str = ""):
    """send(model) -> response dict. Retries once with a model the endpoint does offer."""
    model = _model_overrides.get(provider, model)
    try:
        return send(model)
    except ProviderHTTPError as e:
        if e.code not in (400, 403, 404) or "model" not in str(e).lower():
            raise
        available = _available_models(models_url, headers)
        print(f"ℹ️ {provider}: {model} bu uçta açık değil. Açık modeller: {', '.join(available[:40]) or 'liste yok'}")
        fallback = pick_fallback_model(available, prefer)
        if not fallback or fallback == model:
            raise
        print(f"ℹ️ {provider}: yerine {fallback} kullanılıyor")
        _model_overrides[provider] = fallback
        return send(fallback)


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
    base = base[:-3] if base.endswith("/v1") else base  # the path below already adds /v1
    model = os.environ.get("ANTHROPIC_SCRIPT_MODEL", "claude-sonnet-4-6")
    headers = {"Authorization": f"Bearer {token}", "x-api-key": token, "anthropic-version": "2023-06-01"}
    data = _post_with_model_fallback(
        "anthropic", model,
        lambda m: _post_json(
            f"{base}/v1/messages",
            {"model": m, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
            headers,
        ),
        f"{base}/v1/models", headers, prefer="claude",
    )
    return "".join(block.get("text", "") for block in data.get("content", []))


def _call_openai(prompt: str) -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_SCRIPT_MODEL", "gpt-5-mini")
    headers = {"Authorization": f"Bearer {key}"}
    data = _post_with_model_fallback(
        "openai", model,
        lambda m: _post_json(
            f"{base}/chat/completions",
            {"model": m, "messages": [{"role": "user", "content": prompt}]},
            headers,
        ),
        f"{base}/models", headers,
    )
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


PROVIDERS = [("gemini", _call_gemini), ("anthropic", _call_anthropic), ("openai", _call_openai)]


def scrub_names(text: str, names) -> str:
    """Replaces character/actor names in an image prompt so the model cannot draw a real actor."""
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?:'s)?(?!\w)", "a lone figure", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def validate_script(script: dict, character: dict | None = None) -> dict:
    """Normalizes a model reply and raises ScriptError if it is not publishable.

    History scripts may not mention fiction at all. Character scripts may, but
    their image prompts are scrubbed of every character and actor name."""
    likeness = LIKENESS_NAMES + list((character or {}).get("names", [])) + [(character or {}).get("character", "")]
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
        text = " ".join(EMOJI_RE.sub("", str(raw.get("text") or "")).split())
        visual = " ".join(str(raw.get("visual") or "").split())
        if character:
            visual = scrub_names(visual, likeness)
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
    if hits and not character:
        raise ScriptError(f"script references fiction/film: {', '.join(hits)}")

    hook = " ".join(re.sub(r"[{}\\]", "", EMOJI_RE.sub("", str(script.get("hook_text") or ""))).split())
    if not hook or len(hook) > MAX_HOOK_CHARS:
        hook = " ".join(EMOJI_RE.sub("", title).split()[:4])

    tags = [str(t).strip().lstrip("#") for t in (script.get("tags") or []) if str(t).strip()]
    return {
        "title": title,
        "hook_text": hook,
        "scenes": scenes,
        "description": str(script.get("description") or "").strip(),
        "tags": tags[:15],
        "pinned_comment": str(script.get("pinned_comment") or "").strip(),
        "en_title": str(script.get("en_title") or "").strip()[:90],
        "en_description": str(script.get("en_description") or "").strip(),
    }


def write_script(topic: str, providers=None, attempts_per_provider: int = 2,
                 style_examples: list[str] | None = None, rules: list[str] | None = None,
                 character: dict | None = None) -> tuple[dict, str]:
    """Returns (validated_script, provider_name). Raises ScriptError if all providers fail."""
    prompt = build_prompt(topic, style_examples, rules, character)
    errors = []
    for name, call in providers or PROVIDERS:
        for attempt in range(1, attempts_per_provider + 1):
            try:
                raw = call(prompt)
                if raw is None:
                    break  # provider not configured
                return validate_script(_extract_json(raw), character), name
            except (ScriptError, ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError) as e:
                errors.append(f"{name}#{attempt}: {e}")
                print(f"ℹ️ Senaryo denemesi reddedildi ({name} #{attempt}): {e}")
    raise ScriptError("no provider produced a valid script: " + " | ".join(errors or ["no provider configured"]))
