"""Channel page that matches the videos (2026-11-01 audit): description, keywords, translations and banner.

The banner, keywords and description still described the old film/power channel (Thomas Shelby, The Godfather and
Walter White on the banner; "Güç, Strateji, Finans", "10 dilde yayın", "7/24 canlı yayın" in the description) while
the channel publishes animal, body and psychology Shorts plus power-psychology animations.

    python scripts/kanal_profili.py onizle                     # banner preview + photo candidates, nothing sent
    python scripts/kanal_profili.py uygula <pexels id> x4      # same, then the texts and the banner go to YouTube
    python scripts/kanal_profili.py eski-listeler              # hide the old film/power playlists (Ulaş, 11-01)

Everything lands in channel-data/profil/: the current profile as a backup before any change (yedek-<time>.json),
banner.jpg, adaylar.jpg (numbered candidate photos) and sonuc.json. Stdlib plus Pillow; the channel title stays.
"""
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path("channel-data/profil")
YT = "https://www.googleapis.com/youtube/v3/"
FONT_DIR = Path("/usr/share/fonts/opentype/montserrat")

DESCRIPTION = """Doğanın ve insan zihninin yazılmamış kanunları.

Her gün hayvanlar, insan vücudu ve psikoloji hakkında yanlış bildiğin ya da hiç duymadığın bir gerçek. Her video 15-20 saniye, her bilgi yayından önce kaynaklardan kontrol ediliyor.

Haftada üç gün güç psikolojisi animasyonları: insanları etkileyen, ikna eden ve yöneten görünmez kurallar; gerçek deneyler ve tarihten hikâyelerle.

Yeni Short'lar her gün saat 10:00 ile 24:00 arasında, iki saatte bir.

Yanlış bulduğun bir bilgi ya da merak ettiğin bir konu varsa yorumlara yaz, her yoruma cevap veriyoruz."""

DESCRIPTION_EN = """The unwritten laws of nature and the human mind.

Turkish-language Shorts: every day a surprising, fact-checked truth about animals, the human body and psychology, plus power-psychology animations three times a week."""

KEYWORDS = ('"Zirvenin Kanunu" "ilginç bilgiler" "bilmediğin gerçekler" hayvanlar "hayvan bilgileri" '
            '"hayvanlar alemi" "insan vücudu" psikoloji "insan psikolojisi" "güç psikolojisi" '
            '"ikna psikolojisi" bilim merak "bilimsel gerçekler" shorts')

# One photo per pillar, left to right: the dog and the crow Shorts were the two most watched, the deer is nature,
# the eye stands for the human mind.
QUERIES = ["dog close up portrait", "crow bird portrait", "deer antlers portrait", "human eye close up"]


def call(url, token=None, form=None, body=None, method=None, content_type="application/json"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def pexels(path):
    req = urllib.request.Request("https://api.pexels.com/v1/" + path,
                                 headers={"Authorization": os.environ["PEXELS_API_KEY"], "User-Agent": "zk-profil"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def photo(url):
    req = urllib.request.Request(url, headers={"User-Agent": "zk-profil"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return Image.open(BytesIO(r.read())).convert("RGB")


def font(weight, size):
    return ImageFont.truetype(str(FONT_DIR / f"Montserrat-{weight}.otf"), size)


def cover(img, w, h):
    """Scale and centre-crop to fill w x h (faces sit a little above the middle in portraits)."""
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, max(0, int((img.height - h) * 0.4))
    return img.crop((left, top, left + w, top + h))


def gold_text(canvas, xy, text, fnt, spacing=0):
    """Gold gradient text with a soft dark shadow, centred on xy."""
    draw = ImageDraw.Draw(canvas)
    box = draw.textbbox((0, 0), text, font=fnt)
    w, h = box[2] - box[0] + spacing * (len(text) - 1), box[3] - box[1]
    x, y = xy[0] - w // 2, xy[1] - h // 2 - box[1]
    shadow = Image.new("L", canvas.size, 0)
    sd = ImageDraw.Draw(shadow)
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    cx = x
    for ch in text:
        sd.text((cx + 6, y + 8), ch, font=fnt, fill=200)
        md.text((cx, y), ch, font=fnt, fill=255)
        cx += draw.textlength(ch, font=fnt) + spacing
    canvas.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(10)))
    grad = Image.new("RGB", canvas.size)
    gd = ImageDraw.Draw(grad)
    top, bottom = y + box[1], y + box[3]
    for row in range(canvas.size[1]):
        t = min(1, max(0, (row - top) / max(1, bottom - top)))
        # light gold on top, deep gold below, like the avatar's lettering
        c = [round(a + (b - a) * t) for a, b in zip((255, 236, 170), (196, 140, 44))]
        gd.line([(0, row), (canvas.size[0], row)], fill=tuple(c))
    canvas.paste(grad, (0, 0), mask)


def spaced(canvas, xy, text, fnt, fill, spacing):
    draw = ImageDraw.Draw(canvas)
    w = sum(draw.textlength(ch, font=fnt) for ch in text) + spacing * (len(text) - 1)
    x = xy[0] - w / 2
    for ch in text:
        draw.text((x, xy[1]), ch, font=fnt, fill=fill, anchor="lm")
        x += draw.textlength(ch, font=fnt) + spacing


def banner(images):
    """2560 x 1440: four photo panels, darkened toward the middle band where all text sits.

    YouTube's safe area for every device is the centred 1546 x 423 box; the panels fill the rest for TV and desktop.
    """
    W, H = 2560, 1440
    canvas = Image.new("RGB", (W, H), (10, 10, 12))
    pw = W // len(images)
    for i, img in enumerate(images):
        canvas.paste(cover(img, pw, H), (i * pw, 0))
    # darken: a strong band behind the text, a lighter veil elsewhere, black seams between panels
    veil = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(veil)
    for row in range(H):
        d = abs(row - H / 2) / (H / 2)
        vd.line([(0, row), (W, row)], fill=round(215 - 120 * min(1, d * 1.6)))
    canvas = Image.composite(Image.new("RGB", (W, H), (8, 8, 10)), canvas, veil)
    seams = ImageDraw.Draw(canvas)
    for i in range(1, len(images)):
        seams.rectangle([i * pw - 3, 0, i * pw + 3, H], fill=(8, 8, 10))
    gold_text(canvas, (W // 2, H // 2 - 70), "ZİRVENİN KANUNU", font("Black", 128), spacing=8)
    line = ImageDraw.Draw(canvas)
    line.line([(W // 2 - 480, H // 2 + 30), (W // 2 + 480, H // 2 + 30)], fill=(196, 150, 60), width=3)
    spaced(canvas, (W // 2, H // 2 + 95), "DOĞANIN VE İNSAN ZİHNİNİN YAZILMAMIŞ KANUNLARI",
           font("Bold", 40), (245, 240, 228), 4)
    spaced(canvas, (W // 2, H // 2 + 160), "HER GÜN YENİ BİR GERÇEK", font("SemiBold", 34), (214, 170, 80), 8)
    return canvas


def candidates():
    """Four candidates per pillar from Pexels, as a numbered sheet to pick from."""
    found, tiles = {}, []
    for q in QUERIES:
        res = pexels("search?" + urllib.parse.urlencode({"query": q, "orientation": "portrait", "per_page": 4}))
        found[q] = [{"id": p["id"], "url": p["src"]["large"], "big": p["src"]["large2x"],
                     "photographer": p["photographer"], "page": p["url"]} for p in res.get("photos", [])]
        for p in found[q]:
            tiles.append((p["id"], photo(p["url"])))
    sheet = Image.new("RGB", (4 * 300, 4 * 440), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, (pid, img) in enumerate(tiles[:16]):
        x, y = (i % 4) * 300, (i // 4) * 440
        sheet.paste(cover(img, 296, 400), (x + 2, y + 2))
        d.rectangle([x + 2, y + 404, x + 298, y + 438], fill=(0, 0, 0))
        d.text((x + 10, y + 410), str(pid), font=font("Bold", 24), fill=(255, 220, 120))
    sheet.save(OUT / "adaylar.jpg", quality=85)
    return found


def token():
    return call("https://oauth2.googleapis.com/token", form={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"], "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"})["access_token"]


# The five playlists of the old channel. Ulaş chose "Gizle" for the old videos and playlists on 2026-11-01.
OLD_PLAYLISTS = ("Worldwide Alpha Mindset", "Kozmik Dehşet", "Sinematik Yeraltı Dünyası", "Stoacılık & Çelik İrade",
                 "48 Güç Yasası")


def hide_old_playlists(tok, stamp):
    """Old playlists go private (not deleted); the full playlist list is kept as a backup first."""
    lists = call(YT + "playlists?part=snippet,status&mine=true&maxResults=50", tok).get("items", [])
    (OUT / f"listeler-{stamp}.json").write_text(json.dumps(lists, ensure_ascii=False, indent=1))
    done = []
    for p in lists:
        title = p["snippet"]["title"]
        if p["status"]["privacyStatus"] == "private" or not any(o in title for o in OLD_PLAYLISTS):
            continue
        body = {"id": p["id"], "snippet": {"title": title, "description": p["snippet"].get("description", "")},
                "status": {"privacyStatus": "private"}}
        call(YT + "playlists?part=snippet,status", tok, body=body, method="PUT")
        done.append(title)
    return done


def main(argv):
    mode = argv[0] if argv else "onizle"
    if mode not in ("onizle", "uygula", "eski-listeler"):
        sys.exit("kullanım: kanal_profili.py onizle | uygula <4 Pexels foto id> | eski-listeler")
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tok = token()
    if mode == "eski-listeler":
        hidden = hide_old_playlists(tok, stamp)
        (OUT / "listeler-sonuc.json").write_text(json.dumps({"at": stamp, "gizlendi": hidden}, ensure_ascii=False))
        print("gizlenen listeler:", hidden)
        return
    current = call(YT + "channels?part=snippet,brandingSettings,localizations&mine=true", tok)["items"][0]
    (OUT / f"yedek-{stamp}.json").write_text(json.dumps(current, ensure_ascii=False, indent=1))

    found = candidates()
    chosen = [int(x) for x in argv[1:5]] if len(argv) >= 5 else [found[q][0]["id"] for q in QUERIES if found[q]]
    picked = [pexels(f"photos/{pid}") for pid in chosen]
    img = banner([photo(p["src"]["large2x"]) for p in picked])
    img.save(OUT / "banner.jpg", quality=92)
    result = {"mode": mode, "at": stamp, "photos": [{"id": p["id"], "photographer": p["photographer"],
                                                      "page": p["url"]} for p in picked],
              "candidates": found, "description": DESCRIPTION, "keywords": KEYWORDS}

    if mode == "uygula":
        buf = BytesIO()
        img.save(buf, "JPEG", quality=92)
        uploaded = call("https://www.googleapis.com/upload/youtube/v3/channelBanners/insert?uploadType=media", tok,
                        body=buf.getvalue(), method="POST", content_type="image/jpeg")
        branding = current["brandingSettings"]
        channel = {k: v for k, v in branding.get("channel", {}).items() if k not in ("title",)}
        channel.update({"description": DESCRIPTION, "keywords": KEYWORDS, "defaultLanguage": "tr", "country": "TR"})
        body = {"id": current["id"], "brandingSettings": {"channel": channel,
                                                          "image": {"bannerExternalUrl": uploaded["url"]}}}
        result["branding"] = call(YT + "channels?part=brandingSettings", tok, body=body, method="PUT").get(
            "brandingSettings", {}).get("channel", {}).get("description", "")[:80]
        # Only Turkish and English: the other eight translations promised content in languages the channel
        # does not publish in.
        loc = {"id": current["id"], "localizations": {
            "tr_TR": {"title": "Zirvenin Kanunu", "description": DESCRIPTION},
            "en_US": {"title": "Zirvenin Kanunu", "description": DESCRIPTION_EN}}}
        result["localizations"] = sorted(call(YT + "channels?part=localizations", tok, body=loc,
                                              method="PUT").get("localizations", {}))
        after = call(YT + "channels?part=snippet,brandingSettings,localizations&mine=true", tok)["items"][0]
        result["after"] = {"title": after["snippet"]["title"], "description": after["snippet"]["description"][:120],
                           "banner": after["brandingSettings"].get("image", {}).get("bannerExternalUrl"),
                           "localizations": sorted(after.get("localizations", {}))}
    (OUT / "sonuc.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k not in ("candidates", "description")},
                     ensure_ascii=False)[:1500])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")
