"""Real NASA photos for the engine (test, 2026-11-01: the first second loses most viewers, and a real photo may hold
them better than a drawn planet). Only NASA's own Image and Video Library (images.nasa.gov), whose NASA-made images
are public domain; each kept photo's id, centre and credit go into fabrika/motor/nasa/liste.json.

  python3 fabrika/nasa.py ara   candidates for each query in fabrika/motor/nasa/istek.json "ara": one contact sheet
                                per query in fabrika/motor/nasa/aday/<n>.jpg, ids in order in aday/aday.json
  python3 fabrika/nasa.py al    downloads istek.json "al" {"<name>": {"id": nasa_id, "disk": true|false}}:
                                disk photos are cut to the planet's disk on a transparent square PNG, the rest are
                                scaled to a 1080x1920 JPEG backdrop
Runs on GitHub Actions (calistir.sh nasa); needs ffmpeg, requests, numpy, pillow."""
import io
import json
import subprocess
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DIR = HERE / "motor" / "nasa"
API = "https://images-api.nasa.gov"
NASA_CENTERS = {"JPL", "GSFC", "HQ", "ARC", "JSC", "KSC", "MSFC", "LARC", "GRC", "AFRC", "SSC", "STScI"}


def get(url, **kw):
    r = requests.get(url, timeout=60, **kw)
    r.raise_for_status()
    return r


def assets(nasa_id):
    return [i["href"].replace("http://", "https://") for i in get(f"{API}/asset/{nasa_id}").json()["collection"]["items"]]


def pick(hrefs, *sizes):
    for s in sizes:
        for h in hrefs:
            if f"~{s}." in h and h.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
                return h
    return None


def ara(req):
    out = DIR / "aday"
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for n, q in enumerate(req.get("ara", []), 1):
        items = get(f"{API}/search", params={"q": q, "media_type": "image", "page_size": 30}).json()["collection"]["items"]
        tiles, ids = [], []
        for it in items:
            d = it["data"][0]
            if d.get("center") not in NASA_CENTERS:
                continue
            try:
                h = pick(assets(d["nasa_id"]), "small", "thumb", "medium")
                if not h:
                    continue
                p = out / f"_{n}_{len(tiles)}.jpg"
                p.write_bytes(get(h).content)
            except Exception as e:  # one missing asset must not stop the sheet
                print("atlandı", d.get("nasa_id"), e)
                continue
            tiles.append(p)
            ids.append({"id": d["nasa_id"], "center": d.get("center"), "title": d.get("title"),
                        "credit": d.get("secondary_creator") or d.get("photographer") or ""})
            if len(tiles) == 6:
                break
        if not tiles:
            meta.append({"q": q, "ids": []})
            continue
        args = ["ffmpeg", "-v", "error", "-y"]
        for p in tiles:
            args += ["-i", str(p)]
        f = "".join(f"[{i}:v]scale=300:300:force_original_aspect_ratio=decrease,pad=300:300:(ow-iw)/2:(oh-ih)/2:color=gray,"
                    f"[v{i}];"
                    for i in range(len(tiles)))
        f += "".join(f"[v{i}]" for i in range(len(tiles))) + f"hstack=inputs={len(tiles)}[o]" if len(tiles) > 1 else ""
        if len(tiles) > 1:
            args += ["-filter_complex", f, "-map", "[o]"]
        else:
            args += ["-vf", "scale=300:300:force_original_aspect_ratio=decrease"]
        subprocess.run(args + ["-q:v", "4", str(out / f"{n:02d}.jpg")], check=True)
        for p in tiles:
            p.unlink()
        meta.append({"q": q, "ids": ids})
    (out / "aday.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"NASA aday: {sum(len(m['ids']) for m in meta)} foto, {len(meta)} sayfa"


def disk_png(raw, size=1024):
    """The planet's disk from a photo on black: the box of pixels clearly brighter than the sky, made square and
    masked to a circle so the engine can draw it at any radius."""
    import numpy as np
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    a = np.asarray(im).astype(np.float32).max(axis=2)
    m = a > max(30.0, float(a.max()) * .12)
    ys, xs = np.nonzero(m)
    x0, x1, y0, y1 = np.percentile(xs, .2), np.percentile(xs, 99.8), np.percentile(ys, .2), np.percentile(ys, 99.8)
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) / 2
    crop = im.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r))).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    out = crop.convert("RGBA")
    out.putalpha(mask.resize((size, size), Image.LANCZOS))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def al(req):
    from PIL import Image
    lst_p = DIR / "liste.json"
    lst = json.loads(lst_p.read_text(encoding="utf-8")) if lst_p.exists() else {}
    done = []
    for name, spec in req.get("al", {}).items():
        d = get(f"{API}/search", params={"nasa_id": spec["id"]}).json()["collection"]["items"][0]["data"][0]
        if d.get("center") not in NASA_CENTERS:
            print("NASA dışı, alınmadı:", spec["id"])
            continue
        raw = get(pick(assets(spec["id"]), "large", "orig", "medium")).content
        if spec.get("disk"):
            (DIR / f"{name}.png").write_bytes(disk_png(raw))
            fn = f"{name}.png"
        else:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            k = max(1080 / im.width, 1920 / im.height)
            im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
            l, t = (im.width - 1080) // 2, (im.height - 1920) // 2
            im.crop((l, t, l + 1080, t + 1920)).save(DIR / f"{name}.jpg", "JPEG", quality=84)
            fn = f"{name}.jpg"
        lst[name] = {"file": fn, "id": spec["id"], "center": d.get("center"), "title": d.get("title"),
                     "credit": d.get("secondary_creator") or d.get("photographer") or "NASA", "disk": bool(spec.get("disk"))}
        done.append(name)
    lst_p.write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")
    # the engine reads the list as a script, since it runs from file:// where fetch is not allowed
    (DIR / "liste.js").write_text("window.NASA = " + json.dumps({k: v["file"] for k, v in lst.items()}) + ";\n", encoding="utf-8")
    return f"NASA al: {', '.join(done) or 'hiçbiri'}"


if __name__ == "__main__":
    req = json.loads((DIR / "istek.json").read_text(encoding="utf-8"))
    print({"ara": ara, "al": al}[sys.argv[1]](req))
