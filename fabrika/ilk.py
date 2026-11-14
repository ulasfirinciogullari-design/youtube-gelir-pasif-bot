"""First-frame photos (test from 2026-11-13, /mnt/project-files/ya-olsaydi/karar-defteri.md item 8): line 1 of a Short
opens full-frame on a real photo of its most extreme moment instead of the drawn scene. Since 11-09 only 25-31% of
viewers stayed past the first seconds of some Shorts and the channel's reach fell after them; on Zirvenin Kanunu the
first frame decides most of the stayed-to-watch share. Only public-domain or CC0 files from Wikimedia Commons; each
file's licence is read from Commons itself, never taken on trust.

  python3 fabrika/ilk.py ara   for each episode in fabrika/deneme/ilk_istek.json "ara" {"<id>": ["query", ...]}: up to
                               8 public-domain/CC0 candidates on one numbered contact sheet fabrika/deneme/ilk_aday/<id>.jpg,
                               their details in fabrika/deneme/ilk_aday/<id>.json
  python3 fabrika/ilk.py al    for each "al" {"<id>": {"n": <candidate number>, "x": 0..1, "y": 0..1}} downloads that
                               candidate, cuts a 9:16 frame around the focus point (default the middle) and saves
                               fabrika/motor/ilk/<id>.jpg; its credit and licence go into fabrika/motor/ilk/liste.json
Runs on GitHub Actions (calistir.sh ilk); needs requests and pillow."""
import html
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ISTEK = HERE / "deneme" / "ilk_istek.json"
ADAY = HERE / "deneme" / "ilk_aday"
DIR = HERE / "motor" / "ilk"
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "YaOlsaydiFabrika/1.0 (science shorts; first-frame photos)"}
FREE = re.compile(r"public domain|^pd\b|^pd-|cc0|cc-zero", re.I)
# The whole run gets 10 minutes: on 2026-11-14 Commons throttled the runner so hard that one search took 26 minutes,
# and these runs share the factory's concurrency group, so a long one would hold back an upload slot.
T0, LIMIT = time.time(), 600


def get(url, **kw):
    """Commons throttles the shared GitHub runners (429 on the first run, 2026-11-13): wait and try again."""
    for k in range(5):
        if time.time() - T0 > LIMIT:
            raise RuntimeError("süre doldu, Commons çok yavaş")
        time.sleep(1.5)
        r = requests.get(url, headers=UA, timeout=60, **kw)
        if r.status_code not in (429, 500, 502, 503, 504) or k == 4:
            r.raise_for_status()
            return r
        time.sleep(min(30, int(r.headers.get("Retry-After") or 0) or 10 * (k + 1)))


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()  # one line for the description


def candidates(query, n=8):
    """Public-domain or CC0 bitmaps for a query, biggest first among the best matches."""
    hits = get(API, params={"action": "query", "format": "json", "list": "search", "srnamespace": 6, "srlimit": 40,
                            "srsearch": f"{query} filetype:bitmap"}).json()["query"]["search"]
    titles = [h["title"] for h in hits]
    out = []
    if titles:  # one call for all of them: fewer requests, less throttling
        pages = get(API, params={"action": "query", "format": "json", "prop": "imageinfo", "titles": "|".join(titles),
                                 "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1600}).json()["query"]["pages"]
        order = {t: k for k, t in enumerate(titles)}
        for p in sorted(pages.values(), key=lambda p: order.get(p.get("title"), 99)):
            ii = (p.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata") or {}
            lic = strip((md.get("LicenseShortName") or {}).get("value")) or strip((md.get("License") or {}).get("value"))
            if not FREE.search(lic) or ii.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if min(ii.get("width", 0), ii.get("height", 0)) < 900:
                continue
            out.append({"baslik": p["title"], "sayfa": ii.get("descriptionurl"), "url": ii.get("thumburl") or ii.get("url"),
                        "lisans": lic, "kredi": strip((md.get("Artist") or {}).get("value"))[:120] or "Wikimedia Commons",
                        "w": ii.get("width"), "h": ii.get("height")})
    return out[:n]


def sheet(items, path):
    from PIL import Image, ImageDraw
    cell, cols = 400, 4
    rows = max(1, (len(items) + cols - 1) // cols)
    out = Image.new("RGB", (cell * cols, cell * rows), (20, 20, 30))
    d = ImageDraw.Draw(out)
    for k, it in enumerate(items):
        try:
            small = it["url"].replace("/1600px-", "/400px-")  # Commons keeps standard thumbnail sizes ready
            im = Image.open(io.BytesIO(get(small).content)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            it["hata"] = str(e)[:100]
            continue
        im.thumbnail((cell - 10, cell - 40))
        x, y = (k % cols) * cell, (k // cols) * cell
        out.paste(im, (x + (cell - im.width) // 2, y + 35 + (cell - 40 - im.height) // 2))
        d.rectangle([x, y, x + 60, y + 32], fill=(255, 210, 0))
        d.text((x + 8, y + 6), str(k + 1), fill=(0, 0, 0))
        d.text((x + 70, y + 8), f"{it['w']}x{it['h']}", fill=(220, 220, 220))
    out.save(path, quality=85)


def ara(req):
    ADAY.mkdir(parents=True, exist_ok=True)
    done = []
    for ep_id, queries in req.items():
        seen, items = set(), []
        for q in ([queries] if isinstance(queries, str) else queries):
            try:
                found = candidates(q)
            except Exception as e:  # noqa: BLE001  keep what the other queries found
                print(f"{ep_id} '{q}': {e}"[:200])
                continue
            for it in found:
                if it["baslik"] not in seen:
                    seen.add(it["baslik"])
                    items.append(it)
        items = items[:8]
        (ADAY / f"{ep_id}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        if items:
            sheet(items, ADAY / f"{ep_id}.jpg")
            (ADAY / f"{ep_id}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        done.append(f"{ep_id} {len(items)}")
    return "İlk kare adayları: " + ", ".join(done)


def al(req):
    from PIL import Image
    DIR.mkdir(parents=True, exist_ok=True)
    lp = DIR / "liste.json"
    liste = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
    done = []
    for ep_id, pick in req.items():
        it = json.loads((ADAY / f"{ep_id}.json").read_text(encoding="utf-8"))[int(pick["n"]) - 1]
        try:
            im = Image.open(io.BytesIO(get(it["url"]).content)).convert("RGB")
        except Exception as e:  # noqa: BLE001  the others still go through; this one can be asked for again
            done.append(f"{ep_id} alınamadı ({str(e)[:60]})")
            continue
        w, h = im.size
        cw, ch = (round(h * 9 / 16), h) if w / h > 9 / 16 else (w, round(w * 16 / 9))
        x0 = min(max(0, round(float(pick.get("x", .5)) * w - cw / 2)), w - cw)
        y0 = min(max(0, round(float(pick.get("y", .5)) * h - ch / 2)), h - ch)
        im.crop((x0, y0, x0 + cw, y0 + ch)).resize((1080, 1920), Image.LANCZOS).save(DIR / f"{ep_id}.jpg", quality=88)
        liste[ep_id] = {"file": f"{ep_id}.jpg", "kredi": it["kredi"], "lisans": it["lisans"], "sayfa": it["sayfa"],
                        "baslik": it["baslik"]}
        done.append(ep_id)
    lp.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")
    return "İlk kare fotoğrafları: " + ", ".join(done)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ara"
    req = json.loads(ISTEK.read_text(encoding="utf-8")).get(cmd) or {}
    print(ara(req) if cmd == "ara" else al(req))
