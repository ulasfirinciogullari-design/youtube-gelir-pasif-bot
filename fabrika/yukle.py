"""Upload one factory episode to the Ya Olsaydı? channel.

Reuses the pilot's YouTube code (pilot-ay/youtube.py: token, channel check, playlists). The description is
written once at upload (related episodes as of that moment) and never rewritten, so each episode costs
about 100 quota units (thumbnail 50 + playlist 50; uploads have their own daily bucket).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pilot-ay"))
import youtube as Y  # noqa: E402

SOURCES = {"tr": "Kaynaklar:", "en": "Sources:", "es": "Fuentes:", "pt": "Fontes:", "ar": "المصادر:", "hi": "स्रोत:",
           "id": "Sumber:", "fr": "Sources :", "de": "Quellen:", "ru": "Источники:", "az": "Mənbələr:"}
FACTS = {"tr": "Videodaki bilgiler:", "en": "In this video:", "es": "En este video:", "pt": "Neste vídeo:",
         "ar": "في هذا الفيديو:", "hi": "इस वीडियो में:", "id": "Di video ini:", "fr": "Dans cette vidéo :",
         "de": "In diesem Video:", "ru": "В этом видео:", "az": "Bu videoda:"}


def connect(state, report):
    access = Y.token()
    if not access:
        raise SystemExit("YA_YT_* anahtarları yok")
    yt = Y.YT(access)
    Y.check_channel(yt, report, state)
    if not all(p["title"]["tr"] in state.get("playlists", {}) for p in Y.CONF["playlists"]):
        Y.playlists(yt, state, report)
    return yt


def description(ep, lang, state, related):
    lab = Y.CONF["labels"][lang]
    tr = lang == "tr"
    summary = ep["summary"] if tr else ep["i18n"][lang]["summary"]
    facts = [f["text"] for f in ep["facts"]] if tr else ep["i18n"][lang]["facts"]
    parts = [summary, FACTS[lang] + "\n" + "\n".join(f"• {f}" for f in facts)]
    if ep.get("sources"):
        parts.append(SOURCES[lang] + "\n" + "\n".join(ep["sources"]))
    links = [f"▶ {r['title'] if tr else r.get('i18n', {}).get(lang, r['title'])}\n  https://youtube.com/shorts/{r['video_id']}"
             for r in related]
    pl = state.get("playlists", {}).get(ep["series"])
    block = ([lab["related"]] + links if links else []) + ([f"{lab['all']} https://www.youtube.com/playlist?list={pl}"] if pl else [])
    if block:
        parts.append("\n".join(block))
    sub = f"https://www.youtube.com/{state['handle']}?sub_confirmation=1" if state.get("handle") else ""
    parts.append(f"{lab['cta']}\n{lab['subscribe']} {sub}".rstrip())
    tags = " ".join(dict.fromkeys(lab["hashtags"].split() + (ep.get("hashtags", []) if tr else []) + ["#shorts"]))
    parts.append(tags)
    text = "\n\n".join(parts)
    return text.replace("<", "‹").replace(">", "›")[:4900]  # YouTube refuses < and > in descriptions


def upload(yt, ep, video, cover, state, report, privacy="public"):
    """-> record with the video id. Related links: the 3 newest public episodes, same series first."""
    done = state.get("fabrika", [])
    rel = sorted(done, key=lambda r: (r.get("series") != ep["series"], -r.get("at", 0)))[:3]
    snippet = {"title": ep["title"][:100], "description": description(ep, "tr", state, rel), "tags": ep["tags"],
               "categoryId": "27", "defaultLanguage": "tr", "defaultAudioLanguage": "tr"}
    loc = {l: {"title": ep["i18n"][l]["title"][:100], "description": description(ep, l, state, rel)}
           for l in Y.LANGS if l in ep.get("i18n", {})}
    size = Path(video).stat().st_size
    meta = {"snippet": snippet, "localizations": loc,
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": False, "embeddable": True, "license": "youtube"}}
    r = yt.s.post(f"{Y.UPLOAD}/videos?uploadType=resumable&part=snippet,status,localizations", json=meta, timeout=60,
                  headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Yükleme başlatılamadı ({r.status_code}): {r.text[:400]}")
    with open(video, "rb") as f:
        r = yt.s.put(r.headers["Location"], data=f, timeout=900,
                     headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if not r.ok:
        raise RuntimeError(f"Video gönderilemedi ({r.status_code}): {r.text[:400]}")
    vid = r.json()["id"]
    rec = {"id": ep["id"], "video_id": vid, "url": f"https://youtube.com/shorts/{vid}", "title": ep["title"],
           "series": ep["series"], "privacy": privacy,
           "i18n": {l: ep["i18n"][l]["title"] for l in ep.get("i18n", {})}}
    warn = report.setdefault("warnings", [])
    pl = state.get("playlists", {}).get(ep["series"])
    if pl:
        try:
            yt.call("POST", "playlistItems?part=snippet", json={"snippet": {
                "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": vid}}})
            rec["playlist"] = True
        except RuntimeError as e:
            warn.append(f"liste: {e}")
    if cover and Path(cover).exists():
        try:
            yt.call("POST", f"thumbnails/set?videoId={vid}", base=Y.UPLOAD,
                    headers={"Content-Type": "image/jpeg"}, data=Path(cover).read_bytes())
            rec["thumbnail"] = True
        except RuntimeError as e:
            warn.append(f"kapak: {e}")
    if not warn:
        report.pop("warnings")
    return rec


def status(yt, recs):
    """Upload / privacy status of up to 50 videos in one call (1 unit)."""
    ids = ",".join(r["video_id"] for r in recs[-50:])
    if not ids:
        return {}
    items = yt.call("GET", f"videos?part=status,statistics&id={ids}").get("items", [])
    return {v["id"]: {"upload": v["status"].get("uploadStatus"), "privacy": v["status"].get("privacyStatus"),
                      "views": v.get("statistics", {}).get("viewCount"),
                      "reason": v["status"].get("rejectionReason") or v["status"].get("failureReason")} for v in items}


if __name__ == "__main__":
    print(json.dumps(Y.CONF["labels"]["tr"], ensure_ascii=False))
