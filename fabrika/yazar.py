"""Gemini writes each episode: script + visuals (grounded in Google Search), a fact check, translations.

All calls go to the Gemini API with GEMINI_API_KEY. The best available Flash model is picked from the
model list; on a quota or server error the next one is tried.
"""
import json
import os
import re
import time
from urllib.parse import urlsplit

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
LANGS = ["en", "es", "pt", "ar", "hi", "id", "fr", "de", "ru", "az"]
VISUALS = ["space", "icon", "icons", "number", "compare", "meter", "question", "split", "timeline", "outro"]
BGS = ["space", "sky", "sunset", "night", "ocean", "underwater", "desert", "forest", "ice", "lab", "body", "city",
       "volcano", "storm"]
BODIES = ["earth", "moon", "sun", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto",
          "blackhole", "asteroid", "comet", "star", "galaxy"]
CTA = "Sıradaki soruyu sen seç! Yorumlara yaz."
BAD_EMOJI = {"📅": "🗓️", "📆": "🗓️"}  # these show English month names
SKIP_DOMAINS = ("youtube.", "youtu.be", "tiktok.", "instagram.", "facebook.", "reddit.", "quora.", "pinterest.",
                "x.com", "twitter.", "medium.com", "blogspot.", "wordpress.", "eksisozluk", "vertexaisearch")
LOG = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


class GeminiError(RuntimeError):
    pass


_models = None


def models():
    """Flash text models this key can use, newest first (YO_GEMINI_MODEL first when set)."""
    global _models
    if _models is None:
        key = os.environ["GEMINI_API_KEY"]
        names = []
        try:
            page = ""
            while True:
                r = requests.get(f"{BASE}/models?pageSize=200{page}", headers={"x-goog-api-key": key}, timeout=30)
                r.raise_for_status()
                d = r.json()
                names += [m["name"].split("/")[-1] for m in d.get("models", [])
                          if "generateContent" in m.get("supportedGenerationMethods", [])]
                if not d.get("nextPageToken"):
                    break
                page = f"&pageToken={d['nextPageToken']}"
        except requests.RequestException as e:
            log(f"Gemini model listesi alınamadı: {e}")
        skip = ("lite", "tts", "image", "live", "audio", "embedding", "aqa", "learnlm", "gemma", "robotics", "computer",
                "nano", "transcribe", "omni", "8b")
        flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]

        def ver(n):
            m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
            return (float(m.group(1)) if m else 0, "preview" not in n and "exp" not in n, -len(n))
        flash.sort(key=ver, reverse=True)
        _models = ([os.environ["YO_GEMINI_MODEL"]] if os.environ.get("YO_GEMINI_MODEL") else []) + flash[:4] + ["gemini-2.5-flash"]
        _models = list(dict.fromkeys(_models))
        log(f"Gemini modelleri: {', '.join(_models)}")
    return _models


def call(prompt, search=False, json_mode=False, temperature=0.8):
    """-> (text, grounding chunks). Tries the models in order on quota/server errors."""
    key = os.environ["GEMINI_API_KEY"]
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if search:
        body["tools"] = [{"google_search": {}}]
    last = None
    for model in models():
        for attempt in range(2):
            try:
                r = requests.post(f"{BASE}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                                  json=body, timeout=180)
            except requests.RequestException as e:
                last = f"{model}: {e}"
                continue
            if r.status_code in (429, 500, 503, 504):
                last = f"{model} {r.status_code}: {r.text[:200]}"
                if attempt == 0 and r.status_code != 429:
                    time.sleep(8)
                    continue
                break
            if r.status_code in (400, 403, 404):
                last = f"{model} {r.status_code}: {r.text[:300]}"
                break
            if not r.ok:
                raise GeminiError(f"{model} {r.status_code}: {r.text[:300]}")
            d = r.json()
            cand = (d.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
            if not text.strip():
                last = f"{model}: boş cevap ({cand.get('finishReason')})"
                break
            chunks = [c.get("web", {}) for c in cand.get("groundingMetadata", {}).get("groundingChunks", [])]
            call.model = model
            return text, chunks
        log(f"Gemini denemesi olmadı: {last}")
    raise GeminiError(f"Hiçbir Gemini modeli cevap vermedi: {last}")


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"}


def resolve(url):
    """Grounding links are Google redirects: follow them to the real page."""
    try:
        if "vertexaisearch" in url or "grounding-api-redirect" in url:
            r = requests.get(url, allow_redirects=False, timeout=15, headers=UA)
            url = r.headers.get("Location", url)
        return url
    except requests.RequestException:
        return url


def page_ok(url):
    try:
        r = requests.get(url, timeout=20, headers=UA, allow_redirects=True)
        return r.status_code < 400, r.url
    except requests.RequestException:
        return False, url


def good_domain(url):
    host = urlsplit(url).netloc.lower()
    return host and not any(s in host for s in SKIP_DOMAINS)


SPEC = """
VISUAL FOR EACH LINE ("v"). Pick the type that SHOWS what the line says. Fields:
- {"t":"space","bg":"space","bodies":[{"b":BODY,"size":"xs|s|m|l|xl","at":"c|l|r|t|b|tl|tr|bl|br"}],"do":"none|vanish|explode|grow|shrink|approach|recede|orbit|shake|spin|tilt|heat|freeze","target":INDEX,"key":WORD,"label":LABEL}
  BODY is one of: earth, moon, sun, mercury, venus, mars, jupiter, saturn, uranus, neptune, pluto, blackhole, asteroid, comet, star, galaxy. 1-3 bodies. "do" acts on bodies[target].
- {"t":"icon","bg":BG,"e":EMOJI,"do":"pulse|grow|shrink|shake|spin|float|vanish|fall|rise|multiply|burn|freeze","key":WORD,"label":LABEL}  one big emoji
- {"t":"icons","bg":BG,"items":[{"e":EMOJI,"label":LABEL}, ...]}  2-4 emoji appearing one by one (a list of things)
- {"t":"number","bg":BG,"e":EMOJI,"value":"3,8","unit":"cm","label":LABEL,"key":WORD}  a big number counting up. value uses Turkish format (3,8 / 384.400 / %40 / 1/3)
- {"t":"compare","bg":BG,"a":{"e":EMOJI or "b":BODY,"label":LABEL,"value":NUMBER},"b":{...},"mode":"size|bar","key":WORD}  two things scaled by their real numbers (sizes, distances, amounts). value must be plain numbers in the same unit.
- {"t":"meter","bg":BG,"label":LABEL,"from":NUMBER,"to":NUMBER,"unit":"°C","e":EMOJI,"key":WORD}  a gauge moving (temperature, percent, speed)
- {"t":"question","bg":BG,"e":EMOJI,"label":LABEL}  big question mark (for Bit's questions)
- {"t":"split","bg":BG,"left":{"e":EMOJI,"label":LABEL},"right":{"e":EMOJI,"label":LABEL},"key":WORD}  before -> after
- {"t":"timeline","bg":BG,"steps":[{"when":"1. GÜN","e":EMOJI,"label":LABEL}, ...]}  2-4 steps in time order
- {"t":"outro","bg":BG,"e":EMOJI}  only for the last line
BG is one of: space, sky, sunset, night, ocean, underwater, desert, forest, ice, lab, body, city, volcano, storm.
EMOJI is exactly ONE standard emoji (never text, never 📅 or 📆, no flags). LABEL is 1-3 Turkish words, max 18 characters.
"key" is one word copied from that line's text: the moment the visual's change, label or number appears.
Consecutive lines may reuse the exact same "v" to keep one scene going. Vary the types: use at least 5 different types per episode, at least one "number" or "compare" with a real figure.
"""

RULES = """
You write one episode of "YA OLSAYDI?", a Turkish YouTube Shorts science channel. Two cartoon characters talk:
- "bit": a small curious flying robot. Asks, reacts, speaks for the viewer. Short lines.
- "pi": Profesör Pi, a calm, witty scientist. Explains in short, clear spoken Turkish.

Script pattern (8-11 lines, 380-560 characters of speech in total, about 35-45 seconds):
1. Hook (bit): asks the episode question in a few words, same meaning as the title.
2. pi: the obvious first effect, in one or two sentences.
3. bit: a short push such as "Sadece bu mu?" / "Bu kadar mı?" / "Ciddi misin?".
4-8. pi: 2-3 surprising, specific facts, each with a real number. One idea per line. bit may react in between.
9. pi: an unexpected twist or the most surprising fact as the payoff; bit reacts in a few words.
Last line (bit), exactly: "Sıradaki soruyu sen seç! Yorumlara yaz."

Writing rules:
- Natural spoken Turkish, short sentences, no filler ("hepimiz biliriz", "işte bu", "değil mi?" endings), no greetings, no "merhaba".
- Every claim must be true and checkable; say "yaklaşık" / "olabilir" when science is not certain. Never invent numbers.
- Numbers are written with digits in Turkish format (3,8 / 384.400 / %40). Units as words or km, cm, kg, °C.
- The title is a Turkish question, max 55 characters, curiosity-driven, no clickbait lies, no emoji, Title Case like "Ay Bir Anda Yok Olsaydı Ne Olurdu?".
- "bar" is the on-screen title split in two: "hi" = the 1-2 key words (shown yellow), "rest" = the remainder, together max 24 characters, e.g. {"hi":"AY","rest":"YOK OLSAYDI?"}.
"""

FORMAT = """
Answer with ONLY this JSON (no markdown):
{"title": "...", "bar": {"hi": "...", "rest": "..."}, "summary": "two Turkish sentences that make people want to watch",
 "lines": [{"who": "bit", "text": "...", "v": {...}}, ...],
 "facts": [{"text": "one Turkish sentence with the key number", "source": "site or organisation name", "url": "page URL if you know it exactly, else empty"}],
 "tags": ["12-18 tags: Turkish and English, specific to the topic first, then: ya olsaydı, bilim, animasyon, what if, science"],
 "hashtags": ["#3 topic hashtags in Turkish without spaces"],
 "cover": INDEX of the line whose picture is the most striking}
"facts": 3-5 items, the facts the video states, each from a reliable source (NASA, ESA, NOAA, NIH, WHO, universities, peer-reviewed journals, Britannica, National Geographic).
"""

SERIES = {
    "Ya Olsaydı?": "A hypothetical scenario explained with real science: what would actually happen, step by step.",
    "Ne Kadar Büyük?": "Scale: how big / far / heavy / old something really is. Use compare, number and space visuals with real figures; one comparison the viewer can picture.",
    "1 Gün Boyunca": "What happens in ONE day (in the body, on Earth, in space): surprising daily numbers. Use number, timeline and meter visuals.",
}


def write(topic, recent_titles):
    prompt = (RULES + SPEC + f"\nSERIES: {topic['series']} — {SERIES[topic['series']]}\n"
              f"EPISODE QUESTION: {topic['q']}\n"
              + (f"Extra angle: {topic['angle']}\n" if topic.get("angle") else "")
              + "Do not repeat these recent episodes: " + "; ".join(recent_titles[-30:]) + "\n"
              "First search the web to check every fact and number you use.\n" + FORMAT)
    try:
        text, chunks = call(prompt, search=True, temperature=0.85)
        grounded = True
    except GeminiError as e:
        log(f"Aramalı yazım olmadı, aramasız deneniyor: {e}")
        text, chunks = call(prompt.replace("First search the web to check every fact and number you use.\n", ""),
                            json_mode=True, temperature=0.85)
        grounded = False
    ep = parse_json(text)
    ep["_grounded"] = grounded
    ep["_chunks"] = chunks
    return ep


def check(ep):
    """Second pass with search: is every spoken claim correct? Returns fixes for wrong lines."""
    script = "\n".join(f"{i}. {l['who']}: {l['text']}" for i, l in enumerate(ep["lines"]))
    prompt = ("You are a strict science fact-checker for a Turkish kids-and-adults science channel. Search the web and "
              "check every factual claim and number in this script. Only flag real errors (wrong number, false claim, "
              "stated as certain when it is speculative). For each wrong line give a corrected Turkish line of similar "
              "length that keeps the same role in the dialogue and keeps any number written with digits.\n\n"
              f"Title: {ep['title']}\n{script}\n\n"
              'Answer with ONLY JSON: {"ok": true/false, "fixes": [{"line": INDEX, "text": "corrected line", "why": "short reason"}]}')
    try:
        text, chunks = call(prompt, search=True, temperature=0.2)
        res = parse_json(text)
        res["_chunks"] = chunks
        return res
    except (GeminiError, json.JSONDecodeError, ValueError) as e:
        log(f"Bilgi kontrolü yapılamadı: {e}")
        return {"ok": None, "fixes": [], "_chunks": []}


def translate(ep):
    src = {"title": ep["title"], "summary": ep["summary"], "facts": [f["text"] for f in ep["facts"]]}
    prompt = ("Translate this Turkish YouTube Shorts title, summary and fact list into these languages: "
              + ", ".join(LANGS) + ". Keep numbers exact, keep the question form and the curiosity of the title "
              "(max 70 characters per title), natural native phrasing, no emoji. 'az' is Azerbaijani.\n"
              + json.dumps(src, ensure_ascii=False)
              + '\nAnswer with ONLY JSON: {"en": {"title": "...", "summary": "...", "facts": ["..."]}, "es": {...}, ...}')
    text, _ = call(prompt, json_mode=True, temperature=0.3)
    out = parse_json(text)
    for l in LANGS:
        t = out.get(l) or {}
        if not t.get("title") or not t.get("summary") or len(t.get("facts", [])) != len(src["facts"]):
            raise GeminiError(f"çeviri eksik: {l}")
    return out


def new_topics(series, recent, n=12):
    prompt = (f"Suggest {n} new episode questions for the Turkish science Shorts series '{series}' "
              f"({SERIES[series]}). Each is a short Turkish question that makes people stop scrolling, about space, "
              "the human body, animals, Earth, physics or everyday science, answerable with solid, sourced science. "
              "Avoid these existing ones: " + "; ".join(recent[-80:]) +
              '\nAnswer with ONLY JSON: {"topics": ["...", "..."]}')
    text, _ = call(prompt, json_mode=True, temperature=1.0)
    return [t.strip() for t in parse_json(text).get("topics", []) if isinstance(t, str) and t.strip()]


# ---------- validation ----------
def is_emoji(s):
    return isinstance(s, str) and 0 < len(s) <= 8 and not re.search(r"[A-Za-z0-9ÇĞİÖŞÜçğıöşü]", s)


def clean_visual(v, i, n):
    """Fix what can be fixed, return a list of problems that cannot."""
    probs = []
    if not isinstance(v, dict) or v.get("t") not in VISUALS:
        return {"t": "question", "bg": "space", "e": "🤔"}, [f"satır {i}: görsel türü yok"]
    if v.get("bg") not in BGS:
        v["bg"] = "space"
    for k in ("e",):
        if k in v:
            v[k] = BAD_EMOJI.get(v[k], v[k])
            if not is_emoji(v[k]):
                v[k] = "✨"
    for lst in ("items", "steps"):
        for it in v.get(lst, []) or []:
            if "e" in it:
                it["e"] = BAD_EMOJI.get(it["e"], it["e"])
                if not is_emoji(it["e"]):
                    it["e"] = "✨"
    for side in ("a", "b", "left", "right"):
        it = v.get(side)
        if isinstance(it, dict):
            if it.get("b") and it["b"] not in BODIES:
                it.pop("b")
            if "e" in it:
                it["e"] = BAD_EMOJI.get(it["e"], it["e"])
                if not is_emoji(it["e"]):
                    it.pop("e")
            if not it.get("b") and not it.get("e"):
                it["e"] = "✨"
    if v["t"] == "space":
        v["bodies"] = [b for b in v.get("bodies", []) if isinstance(b, dict) and b.get("b") in BODIES][:3] or [{"b": "earth", "size": "l", "at": "c"}]
    if v["t"] in ("icons", "timeline"):
        key = "items" if v["t"] == "icons" else "steps"
        if not 2 <= len(v.get(key) or []) <= 4:
            probs.append(f"satır {i}: {key} 2-4 olmalı")
    if v["t"] == "compare":
        for side in ("a", "b"):
            try:
                float(str((v.get(side) or {}).get("value")).replace(".", "").replace(",", "."))
            except ValueError:
                probs.append(f"satır {i}: karşılaştırma değeri sayı değil")
    if v["t"] == "number" and not re.search(r"\d", str(v.get("value", ""))):
        probs.append(f"satır {i}: sayı yok")
    if v["t"] == "outro" and i != n - 1:
        v["t"] = "question"
    for k in ("label",):
        if v.get(k) and len(v[k]) > 22:
            v[k] = " ".join(v[k].split()[:3])[:22]
    return v, probs


def validate(ep):
    probs = []
    lines = ep.get("lines") or []
    if not 7 <= len(lines) <= 13:
        probs.append(f"{len(lines)} replik (8-11 olmalı)")
    for i, l in enumerate(lines):
        if l.get("who") not in ("pi", "bit") or not str(l.get("text", "")).strip():
            probs.append(f"satır {i}: konuşan ya da metin yok")
            continue
        l["text"] = re.sub(r"^\s*(pi|bit|prof\w*\.? ?pi)\s*:\s*", "", re.sub(r"\s+", " ", l["text"]).strip(), flags=re.I)
        l["v"], p = clean_visual(l.get("v"), i, len(lines))
        probs += p
    if lines and lines[-1].get("text") != CTA:
        if "yorum" in lines[-1].get("text", "").lower():
            lines[-1]["text"] = CTA
        else:
            lines.append({"who": "bit", "text": CTA, "v": {"t": "outro", "bg": "space"}})
    if lines:
        lines[-1]["who"] = "bit"
        if lines[-1]["v"].get("t") != "outro":
            lines[-1]["v"] = {"t": "outro", "bg": lines[-1]["v"].get("bg", "space")}
        if lines[0].get("who") != "bit":
            probs.append("ilk replik Bit'in sorusu olmalı")
    chars = sum(len(l.get("text", "")) for l in lines)
    if not 330 <= chars <= 620:
        probs.append(f"toplam {chars} karakter (380-560 olmalı)")
    title = str(ep.get("title", "")).strip()
    if not title or len(title) > 70:
        probs.append("başlık yok ya da çok uzun")
    bar = ep.get("bar") or {}
    if not bar.get("hi") or len(bar.get("hi", "") + bar.get("rest", "")) > 30:
        probs.append("ekran başlığı (bar) bozuk")
    types = {l["v"]["t"] for l in lines if isinstance(l.get("v"), dict)}
    if len(types) < 4:
        probs.append(f"sadece {len(types)} görsel türü (en az 5)")
    if not ({"number", "compare", "meter"} & types):
        probs.append("rakam gösteren görsel yok")
    if not 2 <= len(ep.get("facts") or []) <= 6:
        probs.append("bilgi listesi 3-5 olmalı")
    tags = [t for t in ep.get("tags", []) if isinstance(t, str) and t.strip()]
    ep["tags"] = list(dict.fromkeys(t.strip().lstrip("#") for t in tags))
    while len(", ".join(ep["tags"])) > 450:
        ep["tags"].pop()
    ep["hashtags"] = ["#" + re.sub(r"\W", "", h.lstrip("#")) for h in (ep.get("hashtags") or []) if isinstance(h, str)][:3]
    if not isinstance(ep.get("cover"), int) or not 0 <= ep["cover"] < len(lines) - 1:
        ep["cover"] = 1
    return probs


def find_sources(ep):
    """Fallback when the writer named only home pages: a search pass that looks up article pages for the
    facts. -> (url, title) candidates from its grounding results and from the URLs it names."""
    facts = "\n".join(f"- {f['text']}" for f in ep.get("facts", []))
    prompt = ("Search the web and find 3 specific pages (an article or fact sheet, not a home page) on reputable "
              "science sites (NASA, ESA, NOAA, NIH, universities, Britannica, National Geographic, Smithsonian, "
              f"Space.com, Live Science) that support these facts from a video titled '{ep['title']}':\n{facts}\n"
              'Answer with ONLY JSON: {"urls": ["https://...", "https://...", "https://..."]}')
    try:
        text, chunks = call(prompt, search=True, temperature=0.2)
    except GeminiError as e:
        log(f"Kaynak araması yapılamadı: {e}")
        return []
    cands = [(c.get("uri", ""), c.get("title", "")) for c in chunks]
    try:
        cands += [(u, "") for u in parse_json(text).get("urls", []) if isinstance(u, str)]
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass
    return cands


def sources(ep, extra_chunks=(), search_more=True):
    """Up to 4 working source links: the search results the writer used, else the URLs it named."""
    found, seen = [], set()
    cands = [(c.get("uri", ""), c.get("title", "")) for c in list(ep.get("_chunks") or []) + list(extra_chunks)]
    cands = [(resolve(u), t) for u, t in cands if u]
    cands += [(f.get("url", ""), f.get("source", "")) for f in ep.get("facts", [])]
    for url, _ in cands:
        if not url.startswith("http") or not good_domain(url) or urlsplit(url).path.strip("/") == "":
            continue  # a bare home page is not a source
        host = urlsplit(url).netloc.lower().removeprefix("www.")
        if host in seen:
            continue
        ok, final = page_ok(url)
        if ok and good_domain(final) and urlsplit(final).path.strip("/"):
            seen.add(host)
            found.append(final.split("#")[0].split("?")[0] if "?rsource" in final or "utm_" in final else final.split("#")[0])
        if len(found) >= 4:
            break
    if not found and search_more:
        more = [{"uri": u, "title": t} for u, t in find_sources(ep)]
        if more:
            return sources({"facts": []}, more, search_more=False)
    return found
