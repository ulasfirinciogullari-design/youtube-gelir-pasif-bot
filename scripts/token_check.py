"""Writes when the YouTube refresh token expires to channel-data/token_check.json, never the token itself.

Google adds refresh_token_expires_in to the refresh answer for tokens of an OAuth app in Testing (they die 7 days
after issue); a token of a published app gets none.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import urllib.parse
import urllib.request

form = urllib.parse.urlencode({"client_id": os.environ["YOUTUBE_CLIENT_ID"],
                               "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
                               "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
                               "grant_type": "refresh_token"}).encode()
now = datetime.now(timezone.utc)
try:
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", data=form, timeout=30) as r:
        data = json.loads(r.read())
    left = data.get("refresh_token_expires_in")
    out = {"checked_at": now.isoformat(timespec="seconds"), "ok": True, "scope": data.get("scope"),
           "refresh_token_expires_in": left,
           "expires_at": (now + timedelta(seconds=int(left))).isoformat(timespec="seconds") if left else None}
    # Which channel the key belongs to: two keys were made in one Playground session (2026-10-16).
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {data['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        items = json.loads(r.read()).get("items") or []
    out["channels"] = [{"id": c["id"], "title": c["snippet"].get("title"), "handle": c["snippet"].get("customUrl")}
                       for c in items]
except urllib.error.HTTPError as e:
    out = {"checked_at": now.isoformat(timespec="seconds"), "ok": False, "error": e.read().decode()[:300]}
os.makedirs("channel-data", exist_ok=True)
with open("channel-data/token_check.json", "w") as f:
    json.dump(out, f, indent=1)
print({k: v for k, v in out.items()})
