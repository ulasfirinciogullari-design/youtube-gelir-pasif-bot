"""Diagnostic: where the Shorts' views come from, day by day, written to channel-data/views_report.json.

Stdlib only. Refreshes the key without naming scopes; YouTube Analytics lags 1-3 days.
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def call(url, token=None, form=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form else None,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


token = call("https://oauth2.googleapis.com/token", form={
    "client_id": os.environ["YOUTUBE_CLIENT_ID"], "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
    "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"], "grant_type": "refresh_token"})["access_token"]
YT = "https://www.googleapis.com/youtube/v3/"
out = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

channel = call(YT + "channels?part=contentDetails,statistics&mine=true", token)["items"][0]
out["channel"] = {"id": channel["id"], **channel["statistics"]}
uploads, ids, page = channel["contentDetails"]["relatedPlaylists"]["uploads"], [], ""
while len(ids) < 150:
    data = call(YT + f"playlistItems?part=contentDetails&maxResults=50&playlistId={uploads}&pageToken={page}", token)
    ids += [i["contentDetails"]["videoId"] for i in data["items"]]
    page = data.get("nextPageToken")
    if not page:
        break
videos = []
for i in range(0, len(ids), 50):
    data = call(YT + "videos?part=snippet,statistics,contentDetails,status&id=" + ",".join(ids[i:i + 50]), token)
    for v in data["items"]:
        videos.append({"id": v["id"], "published": v["snippet"]["publishedAt"], "title": v["snippet"]["title"][:70],
                       "privacy": v["status"]["privacyStatus"], "duration": v["contentDetails"]["duration"],
                       "views": int(v["statistics"].get("viewCount", 0)),
                       "likes": int(v["statistics"].get("likeCount", 0)),
                       "comments": int(v["statistics"].get("commentCount", 0))})
out["videos"] = sorted(videos, key=lambda v: v["published"], reverse=True)

end, start = date.today(), date.today() - timedelta(days=28)
A = "https://youtubeanalytics.googleapis.com/v2/reports?ids=channel%3D%3DMINE&"


def report(**q):
    try:
        data = call(A + urllib.parse.urlencode({"startDate": start.isoformat(), "endDate": end.isoformat(), **q}),
                    token)
        return {"columns": [h["name"] for h in data.get("columnHeaders", [])], "rows": data.get("rows", [])}
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:300]}


out["daily"] = report(dimensions="day", metrics="views,estimatedMinutesWatched,averageViewPercentage,"
                                            "subscribersGained,likes", sort="day")
out["daily_sources"] = report(dimensions="day,insightTrafficSourceType", metrics="views", sort="day")
out["by_video"] = report(dimensions="video", metrics="views,averageViewPercentage,averageViewDuration,likes,"
                                                     "subscribersGained", sort="-views", maxResults="200")
out["daily_content_type"] = report(dimensions="day,creatorContentType", metrics="views", sort="day")
os.makedirs("channel-data", exist_ok=True)
with open("channel-data/views_report.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print({k: (len(v) if isinstance(v, list) else v if not isinstance(v, dict) else list(v)) for k, v in out.items()})


# Channel audit (2026-11-01): what a viewer sees on the channel page and per Short, where viewers drop off, and who
# watches. Written to channel-data/kanal_denetim.json plus the avatar and banner images under channel-data/denetim/.
def get(path):
    try:
        return call(YT + path, token)
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:300]}


audit = {"checked_at": out["checked_at"]}
ch = get("channels?part=snippet,brandingSettings,localizations,status,topicDetails&mine=true")
audit["channel"] = ch.get("items", [ch])[0]
audit["playlists"] = [{"title": p["snippet"]["title"], "items": p["contentDetails"]["itemCount"],
                       "privacy": p["status"]["privacyStatus"], "description": p["snippet"]["description"][:300]}
                      for p in get("playlists?part=snippet,contentDetails,status&mine=true&maxResults=50").get("items", [])]
audit["sections"] = get("channelSections?part=snippet,contentDetails&mine=true").get("items", [])
details = {}
for i in range(0, len(ids), 50):
    for v in get("videos?part=snippet,status,contentDetails&id=" + ",".join(ids[i:i + 50])).get("items", []):
        details[v["id"]] = {"title": v["snippet"]["title"], "description": v["snippet"].get("description", ""),
                            "tags": v["snippet"].get("tags", []), "category": v["snippet"].get("categoryId"),
                            "lang": v["snippet"].get("defaultLanguage"),
                            "audio_lang": v["snippet"].get("defaultAudioLanguage"),
                            "made_for_kids": v["status"].get("madeForKids"),
                            "self_declared_kids": v["status"].get("selfDeclaredMadeForKids"),
                            "privacy": v["status"]["privacyStatus"], "license": v["status"].get("license"),
                            "embeddable": v["status"].get("embeddable"),
                            "region_blocked": v["contentDetails"].get("regionRestriction"),
                            "content_rating": v["contentDetails"].get("contentRating")}
audit["videos"] = details
newest = [v["id"] for v in out["videos"] if v["privacy"] == "public"][:6]
audit["top_comments"] = {vid: [{"author": t["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"],
                                "text": t["snippet"]["topLevelComment"]["snippet"]["textOriginal"][:300],
                                "likes": t["snippet"]["topLevelComment"]["snippet"]["likeCount"],
                                "replies": t["snippet"]["totalReplyCount"]}
                               for t in get(f"commentThreads?part=snippet&videoId={vid}&maxResults=5&order=relevance")
                               .get("items", [])] for vid in newest}
audit["by_video_more"] = report(dimensions="video", metrics="views,shares,comments,subscribersGained,subscribersLost,"
                                                            "averageViewPercentage", sort="-views", maxResults="200")
audit["subscribed_status"] = report(dimensions="subscribedStatus", metrics="views,averageViewPercentage")
audit["age_gender"] = report(dimensions="ageGroup,gender", metrics="viewerPercentage")
audit["countries"] = report(dimensions="country", metrics="views,subscribersGained", sort="-views", maxResults="15")
audit["devices"] = report(dimensions="deviceType", metrics="views")
audit["sources"] = report(dimensions="insightTrafficSourceType", metrics="views,subscribersGained", sort="-views")
# Retention curves: the share of viewers still watching at each 1 % of the video, for the 45 newest public videos.
audit["retention"] = {}
for v in [v for v in out["videos"] if v["privacy"] == "public"][:45]:
    r = report(dimensions="elapsedVideoTimeRatio", metrics="audienceWatchRatio,relativeRetentionPerformance",
               filters=f"video=={v['id']}")
    audit["retention"][v["id"]] = r.get("rows", r)
os.makedirs("channel-data/denetim", exist_ok=True)
images = {"avatar.jpg": audit["channel"].get("snippet", {}).get("thumbnails", {}).get("high", {}).get("url"),
          "banner.jpg": audit["channel"].get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl")}
for name, url in images.items():
    if url:
        try:
            with urllib.request.urlopen(url + ("=w2120" if name == "banner.jpg" and "=" not in url else ""),
                                        timeout=60) as r, open(f"channel-data/denetim/{name}", "wb") as f:
                f.write(r.read())
        except Exception as e:  # noqa: BLE001 - a missing image is reported, not fatal
            audit[f"{name}_error"] = str(e)[:200]
with open("channel-data/kanal_denetim.json", "w") as f:
    json.dump(audit, f, ensure_ascii=False, indent=1)
print("kanal_denetim:", {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in audit.items()})
