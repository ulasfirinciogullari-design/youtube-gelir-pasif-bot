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
