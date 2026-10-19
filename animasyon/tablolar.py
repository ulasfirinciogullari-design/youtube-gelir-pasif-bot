"""Public-domain paintings for an episode, from Wikimedia Commons.

An episode lists them as "tablolar": [{"id": "franklin", "files": ["<Commons file name>", ...], "ara": "<search words>",
"eser": "...", "yil": 1778, "zorunlu": true}]. The first file that downloads is saved as animasyon/motor/tablolar/<id>.jpg,
where motor.html loads it; when "ara" is given, the Commons search results are tried after "files" and listed in
<id>_adaylar.json so a better one can be picked by name. Files already there are kept, so a painting is fetched once
and then lives in the branch. A painting that is not "zorunlu" may be missing; the scene then draws without it.
"""
import json
import sys
import time
from pathlib import Path

import requests

DIR = Path(__file__).resolve().parent / "motor" / "tablolar"
UA = "ZirveninKanunuBot/1.0 (https://github.com/ulasfirinciogullari-design/youtube-gelir-pasif-bot)"


def search(words, n=10):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers={"User-Agent": UA}, timeout=60, params={
        "action": "query", "list": "search", "srsearch": words, "srnamespace": 6, "srlimit": n, "format": "json"})
    r.raise_for_status()
    return [h["title"].split(":", 1)[1].replace(" ", "_") for h in r.json()["query"]["search"]]


def fetch(ep, width=1600):
    DIR.mkdir(parents=True, exist_ok=True)
    got = {}
    for t in ep.get("tablolar") or []:
        out = DIR / f"{t['id']}.jpg"
        if out.exists() and out.stat().st_size > 20000:
            got[t["id"]] = out.name
            continue
        names = list(t.get("files") or [])
        if t.get("ara"):
            found = search(t["ara"])
            (DIR / f"{t['id']}_adaylar.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
            names += [f for f in found if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))]
        for name in names:
            url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width={width}"
            for attempt in range(3):
                try:
                    r = requests.get(url, headers={"User-Agent": UA}, timeout=60, allow_redirects=True)
                    if r.ok and r.headers.get("content-type", "").startswith("image/") and len(r.content) > 20000:
                        out.write_bytes(r.content)
                        got[t["id"]] = name
                        break
                    print(f"tablo {t['id']}: {name} -> {r.status_code} {r.headers.get('content-type')}")
                    break
                except requests.RequestException as e:
                    print(f"tablo {t['id']}: {name} -> {e}")
                    time.sleep(3 * (attempt + 1))
            if t["id"] in got:
                break
        if t["id"] not in got and t.get("zorunlu", True):
            raise RuntimeError(f"tablo indirilemedi: {t['id']}")
    return got


if __name__ == "__main__":
    ep = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(fetch(ep), ensure_ascii=False))
