"""
Channel housekeeping for Zirvenin Kanunu.

`audit_channel` lists every upload with its numbers and flags the ones worth
removing: uploads of the old film-clip bot (no "Seri:" marker, so a copyright
risk), repeats of the same title, and public videos nobody watched. It only
reports. `apply_cleanup` changes videos, and only the ids it is given.
"""

from datetime import datetime, timezone
import re

from app.services.channel_metrics import _age_hours

LOW_VIEWS = 50
MIN_AGE_DAYS = 3
MARKER_RE = re.compile(r"Seri: ([a-z]{2}-\d{3,})")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")

REASON_LEGACY = "eski film klibi botu"
REASON_REPEAT = "tekrar başlık"
REASON_UNWATCHED = "izlenmedi"


def list_uploads(youtube, max_pages: int = 20) -> list[dict]:
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
            marker = MARKER_RE.search(snippet.get("description", ""))
            videos.append({
                "video_id": snippet.get("resourceId", {}).get("videoId"),
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "topic_id": marker.group(1) if marker else "",
            })
        token = page.get("nextPageToken")
        if not token:
            break
    return videos


def _seconds(iso_duration: str) -> int:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return 0
    d, h, m, s = (int(x or 0) for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def add_details(youtube, videos: list[dict]) -> list[dict]:
    by_id = {v["video_id"]: v for v in videos if v.get("video_id")}
    ids = list(by_id)
    for start in range(0, len(ids), 50):
        items = youtube.videos().list(
            part="statistics,status,contentDetails", id=",".join(ids[start:start + 50])
        ).execute().get("items", [])
        for item in items:
            video = by_id.get(item.get("id"))
            if video is None:
                continue
            stats = item.get("statistics", {})
            video["privacy"] = item.get("status", {}).get("privacyStatus", "")
            video["views"] = int(stats.get("viewCount", 0))
            video["likes"] = int(stats.get("likeCount", 0))
            video["comments"] = int(stats.get("commentCount", 0))
            video["seconds"] = _seconds(item.get("contentDetails", {}).get("duration", ""))
    return videos


def normalize_title(title: str) -> str:
    text = EMOJI_RE.sub("", re.sub(r"#\w+", "", title.lower()))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())[:45]


def flag_candidates(videos: list[dict], now: datetime | None = None) -> list[dict]:
    """Adds "reasons" to each video and returns the ones that have any, worst first."""
    now = now or datetime.now(timezone.utc)
    groups: dict[str, list[dict]] = {}
    for video in videos:
        video["reasons"] = []
        video["age_days"] = round((_age_hours(video.get("published_at", ""), now) or 0) / 24, 1)
        if not video.get("topic_id"):
            video["reasons"].append(REASON_LEGACY)
        if video.get("privacy") == "public" and video["age_days"] >= MIN_AGE_DAYS and video.get("views", 0) < LOW_VIEWS:
            video["reasons"].append(REASON_UNWATCHED)
        groups.setdefault(normalize_title(video.get("title", "")), []).append(video)
    for group in groups.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda v: v.get("views", 0))
        for video in group:
            if video is not keep:
                video["reasons"].append(REASON_REPEAT)
    flagged = [v for v in videos if v["reasons"]]
    return sorted(flagged, key=lambda v: (-len(v["reasons"]), v.get("views", 0)))


def format_audit(videos: list[dict], flagged: list[dict]) -> str:
    public = [v for v in videos if v.get("privacy") == "public"]
    lines = [
        "### Kanal temizlik raporu",
        "",
        f"Toplam {len(videos)} video, {len(public)} herkese açık, {len(flagged)} aday.",
        "",
        "| Video | Başlık | Görünürlük | Yaş (gün) | Süre | İzlenme | Beğeni | Neden |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for v in flagged:
        title = v.get("title", "").replace("|", "/")[:55]
        lines.append(
            f"| {v['video_id']} | {title} | {v.get('privacy', '?')} | {v.get('age_days', '-')} | "
            f"{v.get('seconds', 0)}s | {v.get('views', 0)} | {v.get('likes', 0)} | {', '.join(v['reasons'])} |"
        )
    keep = [v for v in videos if not v.get("reasons")]
    if keep:
        lines += ["", "Kalanlar:", ""]
        lines += [f"- {v['video_id']} | {v.get('title', '')[:55]} | {v.get('privacy', '?')} | {v.get('views', 0)} izlenme"
                  for v in sorted(keep, key=lambda v: -v.get("views", 0))]
    return "\n".join(lines) + "\n"


def audit_channel(youtube) -> tuple[list[dict], list[dict], str]:
    videos = add_details(youtube, list_uploads(youtube))
    flagged = flag_candidates(videos)
    return videos, flagged, format_audit(videos, flagged)


def apply_cleanup(youtube, video_ids: list[str], action: str) -> list[str]:
    """action "private" hides a video (reversible), "delete" removes it for good."""
    if action not in {"private", "delete"}:
        raise ValueError(f"unknown cleanup action: {action}")
    results = []
    for video_id in video_ids:
        try:
            if action == "delete":
                youtube.videos().delete(id=video_id).execute()
            else:
                items = youtube.videos().list(part="status", id=video_id).execute().get("items", [])
                if not items:
                    results.append(f"{video_id}: bulunamadı")
                    continue
                # videos.update clears status fields it is not sent, so send the current ones back.
                status = items[0]["status"]
                status["privacyStatus"] = "private"
                status.setdefault("selfDeclaredMadeForKids", status.get("madeForKids", False))
                status.pop("madeForKids", None)
                status.pop("uploadStatus", None)
                status.pop("failureReason", None)
                status.pop("rejectionReason", None)
                youtube.videos().update(part="status", body={"id": video_id, "status": status}).execute()
            results.append(f"{video_id}: {'silindi' if action == 'delete' else 'gizliye alındı'}")
        except Exception as e:
            results.append(f"{video_id}: hata ({str(e)[:120]})")
    return results
