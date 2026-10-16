"""YA OLSAYDI? YouTube step: check the channel, keep its settings and translations in line with youtube.json,
make the series playlists, upload finished episodes (private by default) with localized titles and
descriptions, related-video links and Turkish + English subtitles.

What has been done is kept in youtube_state.json (with a hash of what was sent), so a rerun only sends what
changed. Settings someone edited in Studio are left alone. Without the YA_YT_* secrets the step only says so
and exits cleanly.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CONF = json.loads((HERE / "youtube.json").read_text(encoding="utf-8"))
STATE_PATH = HERE / "youtube_state.json"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
LANGS = CONF["languages"]  # translations on top of Turkish, the channel's own language


def log(msg):
    print(msg, flush=True)


def token():
    env = {k: os.environ.get(f"YA_YT_{k}", "").strip() for k in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN")}
    missing = [f"YA_YT_{k}" for k, v in env.items() if not v]
    if missing:
        log(f"YouTube adımı atlandı: {', '.join(missing)} henüz GitHub'a eklenmemiş.")
        return None
    r = requests.post(TOKEN_URL, timeout=30, data={
        "client_id": env["CLIENT_ID"], "client_secret": env["CLIENT_SECRET"],
        "refresh_token": env["REFRESH_TOKEN"], "grant_type": "refresh_token"})
    if not r.ok:
        raise SystemExit(f"Google bağlantısı reddedildi ({r.status_code}): {r.text[:300]}")
    return r.json()["access_token"]


class YT:
    def __init__(self, access):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {access}"

    def call(self, method, path, base=None, ok=(200,), **kw):
        r = self.s.request(method, f"{base or API}/{path}", timeout=kw.pop("timeout", 120), **kw)
        if r.status_code not in ok:
            try:
                err = r.json()["error"]
                reason = (err.get("errors") or [{}])[0].get("reason", "")
                detail = f"{err.get('message')} [{reason}]"
            except Exception:
                detail = r.text[:400]
            raise RuntimeError(f"{method} {path.split('&')[0]} -> {r.status_code}: {detail}")
        return r.json() if r.content else {}


def keywords(words):
    return " ".join(f'"{w}"' if " " in w else w for w in words)


def same(key, a, b):
    """YouTube re-formats keywords (quotes, spacing), so compare those as word sets."""
    if key == "keywords":
        norm = lambda v: sorted((q or w).lower() for q, w in re.findall(r'"([^"]+)"|(\S+)', v or ""))
        return norm(a) == norm(b)
    return a == b


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def multipart(meta, data, ctype):
    """multipart/related body for the API's metadata + media uploads (captions)."""
    b = "yaolsaydi" + digest([meta, len(data)])
    head = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta, ensure_ascii=False)}\r\n"
            f"--{b}\r\nContent-Type: {ctype}\r\n\r\n").encode()
    return head + data + f"\r\n--{b}--\r\n".encode(), f"multipart/related; boundary={b}"


def check_channel(yt, report, state):
    items = yt.call("GET", "channels?part=snippet,brandingSettings,status,statistics,localizations&mine=true").get("items", [])
    if not items:
        raise SystemExit("Bu anahtarla bağlı bir YouTube kanalı yok. OAuth Playground'da yeni Gmail ile ve Ya Olsaydı? kanalı seçilerek giriş yapılmalı.")
    ch = items[0]
    title = ch["snippet"]["title"]
    report["channel"] = {"id": ch["id"], "title": title, "handle": ch["snippet"].get("customUrl"),
                         "subscribers": ch["statistics"].get("subscriberCount"),
                         "videos": ch["statistics"].get("videoCount")}
    log(f"Kanal: {title} ({ch['snippet'].get('customUrl', 'kullanıcı adı yok')}), id {ch['id']}")
    if CONF["channel"]["expect_title"] not in title.lower():
        raise SystemExit(f"Yanlış kanal: anahtar '{title}' kanalına bağlı. Hiçbir şey değiştirilmedi. "
                 "OAuth Playground'da Ya Olsaydı? kanalını seçerek yeni refresh token alınmalı.")
    state["channel_id"] = ch["id"]
    if ch["snippet"].get("customUrl"):
        state["handle"] = ch["snippet"]["customUrl"]
    return ch


WRITABLE = ("country", "description", "defaultLanguage", "keywords", "trackingAnalyticsAccountId", "unsubscribedTrailer")


def fill_channel(yt, ch, state, report):
    """Keep the channel settings we manage in line with youtube.json. A field someone changed in Studio
    (it no longer holds what we last wrote) is left alone. Each step fails on its own, so one refusal
    does not stop the playlists and episodes."""
    c = CONF["channel"]
    changed, problems = [], report.setdefault("warnings", [])
    cur = {k: v for k, v in ch.get("brandingSettings", {}).get("channel", {}).items() if k in WRITABLE}
    report["channel_now"] = {k: cur.get(k) for k in ("keywords", "country", "defaultLanguage")}
    ours = state.setdefault("channel_set", {})
    want = {}
    for key, val in (("description", c["description"]["tr"]), ("keywords", keywords(c["keywords"])),
                     ("country", c["country"]), ("defaultLanguage", c["language"])):
        if cur.get(key) and not same(key, cur.get(key), ours.get(key)):
            continue  # edited in Studio
        if not same(key, cur.get(key), val):
            want[key] = val
    bs = {"channel": {**cur, **want}}  # only writable fields: sending back read-only ones is refused
    if not state.get("banner") and not ch.get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl"):
        img = (HERE / c["banner"]).read_bytes()
        url = yt.call("POST", "channelBanners/insert?uploadType=media", base=UPLOAD,
                      headers={"Content-Type": "image/jpeg"}, data=img)["url"]
        bs["image"] = {"bannerExternalUrl": url}
        want["banner"] = url
    if want:
        try:
            yt.call("PUT", "channels?part=brandingSettings", json={"id": ch["id"], "brandingSettings": bs})
            ours.update({k: v for k, v in want.items() if k != "banner"})
            changed += list(want)
            log(f"Kanal ayarları güncellendi: {', '.join(want)}")
        except RuntimeError as e:
            problems.append(str(e))
            log(f"Kanal ayarları güncellenemedi: {e}")
    if want.get("banner") or ch.get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl"):
        state["banner"] = True
    if not state.get("audience") and ch.get("status", {}).get("selfDeclaredMadeForKids") is None:
        try:
            yt.call("PUT", "channels?part=status", json={"id": ch["id"], "status": {"selfDeclaredMadeForKids": False}})
            state["audience"] = True
            changed.append("kitle: çocuklara özel değil")
            log("Kitle ayarı: çocuklara özel değil")
        except RuntimeError as e:
            problems.append(str(e))
            log(f"Kitle ayarı API'den yapılamadı, Studio'dan elle yapılmalı: {e}")
    # channel name and description in every language (needs defaultLanguage, set above)
    loc = {l: {"title": c["title"][l], "description": c["description"][l]} for l in LANGS}
    if state.get("channel_l10n") != digest(loc):
        for attempt, body in (("ad ve açıklama", loc),
                              ("sadece açıklama", {l: {"description": v["description"]} for l, v in loc.items()})):
            try:
                yt.call("PUT", "channels?part=localizations", json={"id": ch["id"], "localizations": body})
                state["channel_l10n"] = digest(loc)
                changed.append(f"çeviriler: {attempt} ({len(loc)} dil)")
                log(f"Kanal çevirileri ({attempt}) {len(loc)} dile yüklendi")
                break
            except RuntimeError as e:
                problems.append(f"{attempt}: {e}")
                log(f"Kanal çevirileri ({attempt}) yüklenemedi: {e}")
    if not state.get("watermark") and c.get("watermark"):
        meta = {"timing": {"type": "offsetFromStart", "offsetMs": 0},
                "position": {"type": "corner", "cornerPosition": "topRight"}}
        data, ctype = multipart(meta, (HERE / c["watermark"]).read_bytes(), "image/png")
        try:
            yt.call("POST", f"watermarks/set?channelId={ch['id']}&uploadType=multipart", base=UPLOAD,
                    ok=(200, 204), headers={"Content-Type": ctype}, data=data)
            state["watermark"] = True
            changed.append("filigran")
            log("Filigran kondu")
        except RuntimeError as e:
            problems.append(str(e))
            log(f"Filigran konamadı: {e}")
    report["channel_filled"] = changed
    if not problems:
        report.pop("warnings")


def playlists(yt, state, report):
    have = {}
    page = ""
    while True:
        d = yt.call("GET", f"playlists?part=snippet&mine=true&maxResults=50{page}")
        have.update({p["snippet"]["title"]: p["id"] for p in d.get("items", [])})
        if not d.get("nextPageToken"):
            break
        page = f"&pageToken={d['nextPageToken']}"
    ids = state.setdefault("playlists", {})
    sent = state.setdefault("playlist_meta", {})
    for p in CONF["playlists"]:
        key = p["title"]["tr"]
        snippet = {"title": key, "description": p["description"]["tr"], "defaultLanguage": "tr"}
        loc = {l: {"title": p["title"][l], "description": p["description"][l]} for l in LANGS}
        if key not in have:
            made = yt.call("POST", "playlists?part=snippet,status,localizations", json={
                "snippet": snippet, "status": {"privacyStatus": "public"}, "localizations": loc})
            have[key] = made["id"]
            log(f"Oynatma listesi açıldı: {key}")
        elif sent.get(key) != digest([snippet, loc]):
            yt.call("PUT", "playlists?part=snippet,localizations",
                    json={"id": have[key], "snippet": snippet, "localizations": loc})
            log(f"Oynatma listesi çevrildi: {key}")
        ids[key] = have[key]
        sent[key] = digest([snippet, loc])
    report["playlists"] = ids


def description(ep, lang, state):
    lab = CONF["labels"][lang]
    up = state.get("uploads", {})
    parts = [ep["summary"][lang]]
    if lab.get("note"):
        parts.append(lab["note"])
    parts.append(lab["facts"] + "\n" + "\n".join(f"• {f[lang]}\n  {f['url']}" for f in ep["facts"]))
    # up to three other public episodes, same series first
    rel = [o for o in CONF["episodes"] if o["id"] != ep["id"] and up.get(o["id"], {}).get("privacy") == "public"]
    rel.sort(key=lambda o: o.get("playlist") != ep.get("playlist"))
    links = [f"▶ {o['title'][lang]}\n  https://youtube.com/shorts/{up[o['id']]['video_id']}" for o in rel[:3]]
    pl = state.get("playlists", {}).get(ep.get("playlist"))
    block = ([lab["related"]] + links if links else []) + ([f"{lab['all']} https://www.youtube.com/playlist?list={pl}"] if pl else [])
    if block:
        parts.append("\n".join(block))
    sub = f"https://www.youtube.com/{state['handle']}?sub_confirmation=1" if state.get("handle") else ""
    parts.append(f"{lab['cta']}\n{lab['subscribe']} {sub}".rstrip())
    parts.append(lab["hashtags"])
    return "\n\n".join(parts)


def video_meta(ep, state):
    snippet = {"title": ep["title"]["tr"], "description": description(ep, "tr", state), "tags": ep["tags"],
               "categoryId": "27", "defaultLanguage": "tr", "defaultAudioLanguage": "tr"}
    loc = {l: {"title": ep["title"][l], "description": description(ep, l, state)} for l in LANGS}
    return snippet, loc


def upload(yt, ep, state):
    path = HERE / ep["video"]
    size = path.stat().st_size
    snippet, loc = video_meta(ep, state)
    meta = {"snippet": snippet, "localizations": loc,
            "status": {"privacyStatus": ep.get("privacy", "private"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": False, "embeddable": True, "license": "youtube"}}
    r = yt.s.post(f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status,localizations", json=meta, timeout=60,
                  headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Yükleme başlatılamadı ({r.status_code}): {r.text[:400]}")
    with path.open("rb") as f:
        r = yt.s.put(r.headers["Location"], data=f, timeout=900,
                     headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Video gönderilemedi ({r.status_code}): {r.text[:400]}")
    return r.json()["id"], digest([snippet, loc])


# ---------- subtitles ----------
def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(cues):
    fixed = []
    for i, (a, b, text) in enumerate(cues):
        nxt = cues[i + 1][0] if i + 1 < len(cues) else b + 2
        fixed.append((a, min(max(b, a + 0.8), nxt - 0.02), text))  # readable but never overlapping
    return "\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(fixed, 1))


def balanced(items, size, limit=42):
    """Split a sentence into the fewest chunks of at most ~limit characters, of similar length."""
    total = sum(size(x) for x in items)
    n = max(1, -(-total // limit))
    chunks, cur, acc = [], [], 0
    for x in items:
        if cur and acc + size(x) / 2 > total * (len(chunks) + 1) / n and len(chunks) < n - 1:
            chunks.append(cur)
            cur = []
        cur.append(x)
        acc += size(x)
    return chunks + [cur]


def spoken_cues(timings):
    """Turkish cues from the exact word timings: one sentence per cue, long ones split evenly."""
    cues = []
    for line in timings:
        sent = []
        for i, w in enumerate(line["words"]):
            sent.append(w)
            if w["w"][-1] in ".?!" or i == len(line["words"]) - 1:
                for ch in balanced(sent, lambda x: len(x["w"]) + 1):
                    cues.append((ch[0]["t0"], ch[-1]["t1"], " ".join(x["w"] for x in ch)))
                sent = []
    return cues


def translated_cues(timings, lines):
    """A translated line spread over the time its Turkish line is spoken, one sentence per cue."""
    assert len(lines) == len(timings), "altyazı satır sayısı replik sayısıyla aynı olmalı"
    cues = []
    for L, text in zip(timings, lines):
        parts = []
        for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
            parts += [" ".join(c) for c in balanced(sent.split(" "), lambda x: len(x) + 1)]
        weight = lambda p: len(p) + 8  # a short "No." still gets time to be read
        total, t = sum(weight(p) for p in parts), L["t0"]
        for p in parts:
            d = (L["t1"] - L["t0"]) * weight(p) / total
            cues.append((t, t + d, p))
            t += d
    return cues


def captions(yt, ep, rec):
    timings = json.loads((HERE / ep["timings"]).read_text(encoding="utf-8"))
    have = rec.setdefault("captions", {})
    out = HERE / "out"
    for lang in CONF["captions"]:
        lines = ep.get("subtitles", {}).get(lang)
        if lang != "tr" and not lines:
            continue
        text = srt(spoken_cues(timings) if lang == "tr" else translated_cues(timings, lines))
        (out / f"altyazi_{ep['id']}_{lang}.srt").write_text(text, encoding="utf-8")
        h = digest(text)
        if have.get(lang, {}).get("hash") == h:
            continue
        cid = have.get(lang, {}).get("id")
        if not cid:  # a track from an earlier, lost state?
            for c in yt.call("GET", f"captions?part=snippet&videoId={rec['video_id']}").get("items", []):
                if c["snippet"]["language"] == lang and c["snippet"].get("trackKind") == "standard":
                    cid = c["id"]
        meta = {"id": cid, "snippet": {"isDraft": False}} if cid else \
            {"snippet": {"videoId": rec["video_id"], "language": lang, "name": "", "isDraft": False}}
        data, ctype = multipart(meta, text.encode("utf-8"), "application/octet-stream")
        res = yt.call("PUT" if cid else "POST", "captions?part=snippet&uploadType=multipart", base=UPLOAD,
                      headers={"Content-Type": ctype}, data=data)
        have[lang] = {"id": res.get("id", cid), "hash": h}
        log(f"{ep['id']}: {lang} altyazı {'güncellendi' if cid else 'yüklendi'}")


def episodes(yt, state, report):
    done = state.setdefault("uploads", {})
    for ep in CONF["episodes"]:
        rec = done.get(ep["id"])
        if not rec:
            if not (HERE / ep["video"]).exists():
                log(f"{ep['id']}: video dosyası yok, atlandı")
                continue
            vid, meta = upload(yt, ep, state)
            rec = done[ep["id"]] = {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}",
                                    "privacy": ep.get("privacy", "private"), "meta": meta}
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))  # never lose an upload id
            log(f"{ep['id']}: yüklendi ({rec['privacy']}) {rec['url']}")
        vid = rec["video_id"]
        want = ep.get("privacy", "private")
        if rec.get("privacy") != want:  # e.g. private -> public once Ulaş approves the episode
            yt.call("PUT", "videos?part=status", json={"id": vid, "status": {
                "privacyStatus": want, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False,
                "embeddable": True, "license": "youtube", "publicStatsViewable": True}})
            rec["privacy"] = want
            log(f"{ep['id']}: görünürlük -> {want}")
        if not rec.get("playlist") and ep.get("playlist") in state.get("playlists", {}):
            yt.call("POST", "playlistItems?part=snippet", json={"snippet": {
                "playlistId": state["playlists"][ep["playlist"]],
                "resourceId": {"kind": "youtube#video", "videoId": vid}}})
            rec["playlist"] = ep["playlist"]
            log(f"{ep['id']}: '{ep['playlist']}' listesine eklendi")
        if not rec.get("thumbnail") and ep.get("thumbnail"):
            try:
                yt.call("POST", f"thumbnails/set?videoId={vid}", base=UPLOAD,
                        headers={"Content-Type": "image/jpeg"}, data=(HERE / ep["thumbnail"]).read_bytes())
                rec["thumbnail"] = True
                rec.pop("thumbnail_error", None)
                log(f"{ep['id']}: kapak görseli kondu")
            except RuntimeError as e:
                rec["thumbnail_error"] = str(e)
                log(f"{ep['id']}: kapak konamadı (kanal telefonla doğrulanmamış olabilir): {e}")
        if ep.get("timings"):
            try:
                captions(yt, ep, rec)
                rec.pop("captions_error", None)
            except RuntimeError as e:
                rec["captions_error"] = str(e)
                report.setdefault("warnings", []).append(f"{ep['id']} altyazı: {e}")
                log(f"{ep['id']}: altyazı yüklenemedi: {e}")
    # titles, descriptions and translations last: related-video links depend on every episode's final privacy
    for ep in CONF["episodes"]:
        rec = done.get(ep["id"])
        if not rec:
            continue
        snippet, loc = video_meta(ep, state)
        if rec.get("meta") != digest([snippet, loc]):
            yt.call("PUT", "videos?part=snippet,localizations",
                    json={"id": rec["video_id"], "snippet": snippet, "localizations": loc})
            rec["meta"] = digest([snippet, loc])
            log(f"{ep['id']}: başlık, açıklama ve {len(loc)} dil çevirisi güncellendi")
        v = yt.call("GET", f"videos?part=status&id={rec['video_id']}").get("items", [])
        if v:
            st = v[0]["status"]
            rec["status"] = {k: st.get(k) for k in ("uploadStatus", "privacyStatus", "rejectionReason", "failureReason")
                             if st.get(k)}
            if st.get("privacyStatus") != rec["privacy"]:
                # YouTube keeps uploads from unaudited API projects private whatever we ask for
                rec["locked"] = f"istenen {rec['privacy']}, YouTube'daki {st.get('privacyStatus')}"
                log(f"{ep['id']}: YouTube görünürlüğü değiştirmedi ({rec['locked']})")
            else:
                rec.pop("locked", None)
        report.setdefault("episodes", {})[ep["id"]] = rec


def main():
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    report = {}
    try:
        access = token()
        if not access:
            report["skipped"] = "secrets missing"
            return
        yt = YT(access)
        ch = check_channel(yt, report, state)
        fill_channel(yt, ch, state, report)
        playlists(yt, state, report)
        episodes(yt, state, report)
    except (RuntimeError, SystemExit, requests.RequestException, AssertionError) as e:
        # the Actions log is hard to reach, so the reason also lands in the committed report
        report["error"] = str(e)
        log(f"HATA: {e}")
        raise SystemExit(1)
    finally:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        out = HERE / "out"
        out.mkdir(exist_ok=True)
        (out / "youtube_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
