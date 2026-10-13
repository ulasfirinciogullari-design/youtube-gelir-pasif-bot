"""
Performance of the original Shorts, read back from the channel on every run.

Each run lists the series videos (those whose description carries a
"Seri: <topic id>" marker), adds their public statistics and, when the OAuth
grant includes yt-analytics.readonly, their average view percentage. The table
goes to the job summary, and the best performers' titles are handed to the
script writer as style examples, so hooks that work get reinforced.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

MIN_AGE_HOURS_FOR_RANKING = 12


def list_series_videos(youtube, marker_re: re.Pattern, max_pages: int = 10) -> list[dict]:
    channels = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if not channels:
        return []
    uploads = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    videos, token = [], None
    for _ in range(max_pages):
        page = youtube.playlistItems().list(
            playlistId=uploads, part="snippet", maxResults=50, pageToken=token
        ).execute()
        for item in page.get("items", []):
            snippet = item.get("snippet", {})
            match = marker_re.search(snippet.get("description", ""))
            if not match:
                continue
            videos.append({
                "video_id": snippet.get("resourceId", {}).get("videoId"),
                "topic_id": match.group(1),
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
            })
        token = page.get("nextPageToken")
        if not token:
            break
    return videos


def add_statistics(youtube, videos: list[dict]) -> list[dict]:
    by_id = {v["video_id"]: v for v in videos if v.get("video_id")}
    ids = list(by_id)
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        items = youtube.videos().list(part="statistics,status", id=",".join(batch)).execute().get("items", [])
        for item in items:
            stats, video = item.get("statistics", {}), by_id.get(item.get("id"))
            if video is None:
                continue
            video["privacy"] = item.get("status", {}).get("privacyStatus", "")
            video["views"] = int(stats.get("viewCount", 0))
            video["likes"] = int(stats.get("likeCount", 0))
            video["comments"] = int(stats.get("commentCount", 0))
    return videos


def add_retention(videos: list[dict]) -> str | None:
    """Adds avg_view_pct from YouTube Analytics. Returns a note when it is unavailable."""
    ids = [v["video_id"] for v in videos if v.get("video_id")]
    if not ids:
        return None
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # No scopes on purpose: the refreshed token carries whatever the grant allows.
        creds = Credentials(
            token=None,
            refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.environ["YOUTUBE_CLIENT_ID"],
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        )
        analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        start = min((v["published_at"][:10] for v in videos if v.get("published_at")), default="2026-01-01")
        report = analytics.reports().query(
            ids="channel==MINE", startDate=start, endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            metrics="averageViewDuration,averageViewPercentage", dimensions="video",
            filters="video==" + ",".join(ids[:200]), maxResults=200,
        ).execute()
    except Exception as e:
        return f"İzlenme oranı alınamadı (YouTube Analytics izni gerekebilir): {str(e)[:160]}"
    by_id = {v["video_id"]: v for v in videos}
    for video_id, avg_sec, avg_pct in report.get("rows", []):
        if video_id in by_id:
            by_id[video_id]["avg_view_seconds"] = round(float(avg_sec), 1)
            by_id[video_id]["avg_view_pct"] = round(float(avg_pct), 1)
    return None


def _age_hours(published_at: str, now: datetime) -> float | None:
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (now - published).total_seconds() / 3600)


def rank_videos(videos: list[dict], now: datetime | None = None) -> list[dict]:
    """Public videos old enough to judge, best first by views per hour (retention breaks ties)."""
    now = now or datetime.now(timezone.utc)
    ranked = []
    for video in videos:
        age = _age_hours(video.get("published_at", ""), now)
        if video.get("privacy") != "public" or age is None or age < MIN_AGE_HOURS_FOR_RANKING:
            continue
        video["views_per_hour"] = round(video.get("views", 0) / age, 2)
        ranked.append(video)
    return sorted(ranked, key=lambda v: (v["views_per_hour"], v.get("avg_view_pct", 0)), reverse=True)


def style_examples(videos: list[dict], count: int = 3) -> list[str]:
    ranked = rank_videos(videos)
    return [v["title"] for v in ranked[:count]] if len(ranked) >= count else []


def format_report(videos: list[dict], note: str | None = None) -> str:
    lines = [
        "### Zirvenin Kanunu: seri videoların durumu",
        "",
        "| Konu | Başlık | Görünürlük | İzlenme | Beğeni | Yorum | Saatlik izlenme | Ort. izlenme oranı |",
        "|---|---|---|---|---|---|---|---|",
    ]
    ranked_ids = {v["video_id"] for v in rank_videos(videos)}
    for v in sorted(videos, key=lambda v: v.get("published_at", ""), reverse=True):
        pct = f"%{v['avg_view_pct']}" if "avg_view_pct" in v else "-"
        vph = str(v.get("views_per_hour", "-")) if v["video_id"] in ranked_ids else "-"
        title = v.get("title", "").replace("|", "/")[:60]
        lines.append(
            f"| {v['topic_id']} | {title} | {v.get('privacy', '?')} | {v.get('views', 0)} | "
            f"{v.get('likes', 0)} | {v.get('comments', 0)} | {vph} | {pct} |"
        )
    if note:
        lines += ["", f"_{note}_"]
    return "\n".join(lines) + "\n"


def report_channel(youtube, marker_re: re.Pattern, output_dir: Path) -> tuple[set[str], list[str]]:
    """Returns (published topic ids, style example titles) and writes the metrics report."""
    videos = list_series_videos(youtube, marker_re)
    published = {v["topic_id"] for v in videos}
    try:
        add_statistics(youtube, videos)
        note = add_retention(videos)
        report = format_report(videos, note)
        print(report)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(report)
        (output_dir / "metrics.json").write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
        return published, style_examples(videos)
    except Exception as e:
        print(f"ℹ️ Metrik raporu notu: {e}")
        return published, []
