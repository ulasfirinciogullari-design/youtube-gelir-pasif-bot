"""Which Turkish science / "what if" Shorts got the most views in the last months, from the YouTube Data API.

  python fabrika/rakip.py      writes fabrika/rakip.json

Searches a fixed list of Turkish queries for short videos (order=viewCount, Turkish region and language),
then reads each hit's views, likes, comments, length and channel size. The point is to see which topics,
titles and lengths break out in this niche, so our own format can learn from them. Only public metadata is
read (title, numbers, thumbnail address); no video is downloaded. Costs ~100 quota units per query.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pilot-ay"))
import youtube as Y  # noqa: E402
from metrik import key_info  # noqa: E402

OUT = HERE / "rakip.json"
QUERIES = ["olsaydı ne olurdu", "ne olurdu shorts", "gerçekte ne kadar büyük", "uzay bilgileri",
           "evren shorts", "kara delik", "vücudun ilginç", "beyin bilgi", "bilim shorts türkçe",
           "bunu biliyor muydun", "ilginç bilgiler", "dünya yok olsaydı"]
SINCE_DAYS = 180


def get(access, path, **params):
    r = requests.get(f"{Y.API}/{path}", params=params, timeout=30, headers={"Authorization": f"Bearer {access}"})
    if not r.ok:
        raise RuntimeError(f"{path} {r.status_code}: {r.text[:200]}")
    return r.json()


def seconds(iso):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return sum(int(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1))) if m else None


def main():
    access, _ = key_info()
    since = (datetime.now(timezone.utc) - timedelta(days=SINCE_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    found, errors = {}, []
    for q in QUERIES:
        try:
            d = get(access, "search", part="id", q=q, type="video", videoDuration="short", order="viewCount",
                    regionCode="TR", relevanceLanguage="tr", publishedAfter=since, maxResults=50)
        except Exception as e:  # one failed query must not lose the others
            errors.append(f"{q}: {e}")
            continue
        for it in d.get("items", []):
            found.setdefault(it["id"]["videoId"], []).append(q)
    ids, videos = list(found), []
    for i in range(0, len(ids), 50):
        for v in get(access, "videos", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            videos.append({"id": v["id"], "baslik": sn.get("title"), "kanal": sn.get("channelTitle"),
                           "kanal_id": sn.get("channelId"), "tarih": sn.get("publishedAt", "")[:10],
                           "sure": seconds(v.get("contentDetails", {}).get("duration")),
                           "izlenme": int(st.get("viewCount", 0)), "begeni": int(st.get("likeCount", 0)),
                           "yorum": int(st.get("commentCount", 0)), "dil": sn.get("defaultAudioLanguage"),
                           "kapak": (sn.get("thumbnails", {}).get("high") or {}).get("url"), "arama": found[v["id"]]})
    chans = sorted({v["kanal_id"] for v in videos})
    subs = {}
    for i in range(0, len(chans), 50):
        for c in get(access, "channels", part="statistics", id=",".join(chans[i:i + 50])).get("items", []):
            st = c.get("statistics", {})
            subs[c["id"]] = {"abone": int(st.get("subscriberCount", 0)), "video": int(st.get("videoCount", 0)),
                             "izlenme": int(st.get("viewCount", 0))}
    for v in videos:
        v["kanal_abone"] = subs.get(v["kanal_id"], {}).get("abone")
    videos.sort(key=lambda v: -v["izlenme"])
    OUT.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sorgular": QUERIES,
                               "gun": SINCE_DAYS, "hatalar": errors, "kanallar": subs, "videolar": videos},
                              ensure_ascii=False, indent=1) + "\n")
    print(f"Rakip: {len(videos)} video, {len(subs)} kanal, {len(errors)} hata")


if __name__ == "__main__":
    main()
