"""Upload one animated episode to the Zirvenin Kanunu channel with the channel's own key (YOUTUBE_* secrets).

The key is checked against the channel id before anything is sent, so a key issued for another channel
(Ya Olsaydı? keys went into the wrong secret twice in October) can never upload here.
"""
import os
from pathlib import Path

import requests

ZK_CHANNEL = "UCgvESYtYbn2w9R2ExBOF_cw"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"


def token():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"], "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    if not r.ok:
        raise RuntimeError(f"YouTube anahtarı yenilenemedi ({r.status_code}): {r.text[:200]}")
    return r.json()["access_token"]


def session():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token()}"
    items = s.get(f"{API}/channels?part=id,snippet&mine=true", timeout=30).json().get("items", [])
    ch = items[0] if items else {}
    if ch.get("id") != ZK_CHANNEL:
        raise RuntimeError(f"Anahtar Zirvenin Kanunu'na ait değil: {ch.get('id')} {ch.get('snippet', {}).get('title')}")
    return s, {"id": ch["id"], "title": ch["snippet"]["title"]}


def description(ep):
    parts = [ep.get("summary", "")]
    if ep.get("facts"):
        parts.append("Videodaki bilgiler:\n" + "\n".join(f"• {f}" for f in ep["facts"]))
    if ep.get("tablolar"):
        parts.append("Tablolar (kamu malı):\n" + "\n".join(f"• {t['eser']}" + (f" ({t['yil']})" if t.get("yil") else "")
                                                     for t in ep["tablolar"] if t.get("zorunlu", True)))
    if ep.get("sources"):
        parts.append("Kaynaklar:\n" + "\n".join(ep["sources"]))
    parts.append(" ".join(ep.get("hashtags", []) + ["#shorts"]))
    return "\n\n".join(p for p in parts if p).replace("<", "‹").replace(">", "›")[:4900]


def upload(ep, video, privacy="private"):
    s, channel = session()
    size = Path(video).stat().st_size
    meta = {"snippet": {"title": ep["yt_title"][:100], "description": description(ep), "tags": ep.get("tags", []),
                        "categoryId": "27", "defaultLanguage": "tr", "defaultAudioLanguage": "tr"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False,
                       "embeddable": True, "license": "youtube"}}
    r = s.post(f"{UPLOAD}/videos?uploadType=resumable&part=snippet,status", json=meta, timeout=60,
               headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Yükleme başlatılamadı ({r.status_code}): {r.text[:400]}")
    with open(video, "rb") as f:
        r = s.put(r.headers["Location"], data=f, timeout=900, headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Video gönderilemedi ({r.status_code}): {r.text[:400]}")
    vid = r.json()["id"]
    return {"video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": privacy, "channel": channel}
