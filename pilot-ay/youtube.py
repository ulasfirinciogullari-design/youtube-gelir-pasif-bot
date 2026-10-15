"""YA OLSAYDI? YouTube step: check the channel, fill its empty settings, make the series playlists and
upload finished episodes (private by default).

Settings and episodes come from youtube.json. What has been done is kept in youtube_state.json, so every
step runs once: re-running after a push changes nothing that is already on YouTube. Without the
YA_YT_* secrets the step only says so and exits cleanly.
"""
import json
import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CONF = json.loads((HERE / "youtube.json").read_text(encoding="utf-8"))
STATE_PATH = HERE / "youtube_state.json"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


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
            raise RuntimeError(f"{method} {path.split('?')[0]} -> {r.status_code}: {detail}")
        return r.json() if r.content else {}


def keywords(words):
    return " ".join(f'"{w}"' if " " in w else w for w in words)


def check_channel(yt, report):
    items = yt.call("GET", "channels?part=snippet,brandingSettings,status,statistics&mine=true").get("items", [])
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
    return ch


def fill_channel(yt, ch, state, report):
    """Fill only the settings that are still empty, so nothing typed in Studio is overwritten."""
    c = CONF["channel"]
    bs = {k: v for k, v in ch.get("brandingSettings", {}).items() if k in ("channel", "image", "watch")}
    cur = bs.setdefault("channel", {})
    changed = []
    for key, val in (("description", c["description"]), ("keywords", keywords(c["keywords"])),
                     ("country", c["country"]), ("defaultLanguage", c["language"])):
        if not cur.get(key):
            cur[key] = val
            changed.append(key)
    if not state.get("banner") and not bs.get("image", {}).get("bannerExternalUrl"):
        img = (HERE / c["banner"]).read_bytes()
        url = yt.call("POST", "channelBanners/insert?uploadType=media", base=UPLOAD,
                      headers={"Content-Type": "image/jpeg"}, data=img)["url"]
        bs.setdefault("image", {})["bannerExternalUrl"] = url
        changed.append("banner")
    if changed:
        yt.call("PUT", "channels?part=brandingSettings", json={"id": ch["id"], "brandingSettings": bs})
        log(f"Kanal ayarları dolduruldu: {', '.join(changed)}")
    if "banner" in changed or bs.get("image", {}).get("bannerExternalUrl"):
        state["banner"] = True
    if not state.get("audience") and ch.get("status", {}).get("selfDeclaredMadeForKids") is None:
        try:
            yt.call("PUT", "channels?part=status", json={"id": ch["id"], "status": {"selfDeclaredMadeForKids": False}})
            state["audience"] = True
            changed.append("kitle: çocuklara özel değil")
            log("Kitle ayarı: çocuklara özel değil")
        except RuntimeError as e:
            log(f"Kitle ayarı API'den yapılamadı, Studio'dan elle yapılmalı: {e}")
    report["channel_filled"] = changed


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
    for p in CONF["playlists"]:
        if p["title"] in have:
            ids[p["title"]] = have[p["title"]]
            continue
        made = yt.call("POST", "playlists?part=snippet,status", json={
            "snippet": {"title": p["title"], "description": p["description"], "defaultLanguage": "tr"},
            "status": {"privacyStatus": "public"}})
        ids[p["title"]] = made["id"]
        log(f"Oynatma listesi açıldı: {p['title']}")
    report["playlists"] = ids


def upload(yt, ep):
    path = HERE / ep["video"]
    size = path.stat().st_size
    meta = {
        "snippet": {"title": ep["title"], "description": ep["description"], "tags": ep["tags"],
                    "categoryId": "27", "defaultLanguage": "tr", "defaultAudioLanguage": "tr"},
        "status": {"privacyStatus": ep.get("privacy", "private"), "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False, "embeddable": True, "license": "youtube"},
    }
    r = yt.s.post(f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status", json=meta, timeout=60,
                  headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Yükleme başlatılamadı ({r.status_code}): {r.text[:400]}")
    with path.open("rb") as f:
        r = yt.s.put(r.headers["Location"], data=f, timeout=900,
                     headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Video gönderilemedi ({r.status_code}): {r.text[:400]}")
    return r.json()["id"]


def episodes(yt, state, report):
    done = state.setdefault("uploads", {})
    for ep in CONF["episodes"]:
        rec = done.get(ep["id"])
        if not rec:
            if not (HERE / ep["video"]).exists():
                log(f"{ep['id']}: video dosyası yok, atlandı")
                continue
            vid = upload(yt, ep)
            rec = done[ep["id"]] = {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}",
                                    "privacy": ep.get("privacy", "private")}
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
        v = yt.call("GET", f"videos?part=status,processingDetails&id={vid}").get("items", [])
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
        ch = check_channel(yt, report)
        state["channel_id"] = ch["id"]
        fill_channel(yt, ch, state, report)
        playlists(yt, state, report)
        episodes(yt, state, report)
    except (RuntimeError, SystemExit, requests.RequestException) as e:
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
