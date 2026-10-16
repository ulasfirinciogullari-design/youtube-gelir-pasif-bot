"""Watch-time and traffic numbers for every Ya Olsaydı? episode, from the YouTube Analytics API.

  python fabrika/metrik.py      writes fabrika/metrikler.json (needs the yt-analytics.readonly permission)

Per video: views, average % watched, average seconds watched, likes, shares, comments, subscribers gained,
and where the views came from (Shorts feed, search, channel page...). YouTube fills these in with a delay of
one to three days, so the newest episodes show up later. Without the permission it only records which
permissions the key has, so a missing re-authorisation is visible in the file.
"""
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pilot-ay"))
import youtube as Y  # noqa: E402

OUT = HERE / "metrikler.json"
STATE = HERE / "durum.json"
API = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = ("views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,shares,comments,"
           "subscribersGained")
START = "2026-10-15"  # the channel's first upload


def report(access, **params):
    r = requests.get(API, headers={"Authorization": f"Bearer {access}"}, timeout=60,
                     params={"ids": "channel==MINE", "startDate": START, "endDate": date.today().isoformat(), **params})
    if not r.ok:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    d = r.json()
    names = [h["name"] for h in d.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in d.get("rows", [])]


def key_info():
    """Access token plus what Google says about the key: a key made while the app was in Testing comes back with
    refresh_token_expires_in (it dies after 7 days); a published app's key has no such field. `parmak_izi` is the
    first 8 hex digits of the key's SHA-256, enough to see that the secret was replaced, useless for anything else."""
    rt = os.environ.get("YA_YT_REFRESH_TOKEN", "").strip()
    r = requests.post(Y.TOKEN_URL, timeout=30, data={
        "client_id": os.environ.get("YA_YT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("YA_YT_CLIENT_SECRET", "").strip(),
        "refresh_token": rt, "grant_type": "refresh_token"})
    if not r.ok:
        raise RuntimeError(f"anahtar reddedildi ({r.status_code}): {r.text[:300]}")
    d = r.json()
    info = {"parmak_izi": hashlib.sha256(rt.encode()).hexdigest()[:8]}
    if "refresh_token_expires_in" in d:
        days = int(d["refresh_token_expires_in"]) / 86400
        info["bitis"] = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="minutes")
        info["kalan_gun"] = round(days, 1)
    else:
        info["bitis"] = "Google süre bildirmedi"
    return d["access_token"], info


def scopes(access):
    r = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": access}, timeout=30)
    return sorted(s.rsplit("/", 1)[-1] for s in r.json().get("scope", "").split()) if r.ok else []


def main():
    out = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        if not os.environ.get("YA_YT_REFRESH_TOKEN"):
            raise RuntimeError("YA_YT_* anahtarları yok")
        access, out["anahtar"] = key_info()
        out["scopes"] = scopes(access)
        if "yt-analytics.readonly" not in out["scopes"]:
            out["skipped"] = "anahtarda yt-analytics.readonly izni yok, OAuth yeniden yapılmalı"
        else:
            titles = {e["video_id"]: e["title"] for e in json.loads(STATE.read_text()).get("episodes", [])}
            rows = report(access, metrics=METRICS, dimensions="video", sort="-views", maxResults=200)
            videos = {r.pop("video"): r for r in rows}
            for vid, r in videos.items():
                r["title"] = titles.get(vid, "")
                try:  # where the views came from; SHORTS = the Shorts feed
                    src = report(access, metrics="views", dimensions="insightTrafficSourceType",
                                 filters=f"video=={vid}", sort="-views")
                    r["sources"] = {s["insightTrafficSourceType"]: s["views"] for s in src}
                except RuntimeError as e:
                    r["sources_error"] = str(e)[:200]
            out["videos"] = videos
            out["days"] = report(access, metrics="views,estimatedMinutesWatched,subscribersGained",
                                 dimensions="day", sort="day")
    except Exception as e:  # noqa: BLE001 - never stop the episode run
        out["error"] = f"{type(e).__name__}: {e}"[:600]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(out.get("videos", {}))
    why = out.get("skipped") or out.get("error") or ("YouTube henüz veri vermedi (1-3 gün gecikme)" if not n else "")
    print(f"Metrikler: {n} video" + (f", {why}" if why else ""))


if __name__ == "__main__":
    main()
