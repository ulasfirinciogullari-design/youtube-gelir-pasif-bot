"""
Channel housekeeping for Zirvenin Kanunu.

`audit_channel` lists every upload with its numbers and sorts the ones worth
removing by reason: film and series clips (copyright and "reused content"
risk), live stream recordings, old English uploads, repeats of the same title,
and public videos nobody watched. It only reports. `apply_cleanup` changes
videos, and only the ids it is given.
"""

from datetime import datetime, timezone
import re

from app.services.channel_metrics import _age_hours
from app.services.original_script import BANNED_REFERENCES, EMOJI_RE

LOW_VIEWS = 50
MIN_AGE_DAYS = 3
LIVE_SECONDS = 15 * 60
MARKER_RE = re.compile(r"Seri: ([a-z]{2}-\d{3,})")
FILM_NAMES = BANNED_REFERENCES + [
    "oppenheimer", "gladyatör", "gladiator", "maximus", "the matrix", "matrix illusion", "don corleone",
    "cillian murphy", "hollywood", "şafak sezer", "hedef ben miyim tayfun", "polat", "minecraft",
]
ENGLISH_WORDS = {"why", "how", "the", "before", "part", "law", "of", "and", "its", "with", "decisions", "code"}
TURKISH_CHARS = set("çğıöşüÇĞİÖŞÜ")

REASON_FILM = "film/dizi klibi (telif riski)"
REASON_LIVE = "canlı yayın kaydı"
REASON_ENGLISH = "İngilizce eski içerik"
REASON_REPEAT = "tekrar başlık"
REASON_UNWATCHED = "izlenmedi"
JUNK_REASONS = {REASON_LIVE, REASON_REPEAT, REASON_UNWATCHED}


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


def is_film_clip(title: str) -> bool:
    low = title.lower()
    return any(name in low for name in FILM_NAMES)


def is_english(title: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", EMOJI_RE.sub("", title).lower())
    return not (set(title) & TURKISH_CHARS) and len(words) >= 3 and any(w in ENGLISH_WORDS for w in words)


def flag_candidates(videos: list[dict], now: datetime | None = None, keep_ids: frozenset = frozenset()) -> list[dict]:
    """Adds "reasons" to each video and returns the ones that have any, worst first.

    Our own series videos (a "Seri:" marker, or listed in keep_ids because a Studio edit removed the
    marker) are never flagged.
    """
    now = now or datetime.now(timezone.utc)
    groups: dict[str, list[dict]] = {}
    for video in videos:
        video["reasons"] = []
        video["age_days"] = round((_age_hours(video.get("published_at", ""), now) or 0) / 24, 1)
        if video.get("topic_id") or video.get("video_id") in keep_ids:
            continue
        title = video.get("title", "")
        if is_film_clip(title):
            video["reasons"].append(REASON_FILM)
        # str.lower() turns "CANLI" into "canli", so match both spellings.
        if video.get("seconds", 0) >= LIVE_SECONDS or re.search(r"\bcanl[ıi]\b", title.lower()):
            video["reasons"].append(REASON_LIVE)
        if is_english(title):
            video["reasons"].append(REASON_ENGLISH)
        if video.get("privacy") == "public" and video["age_days"] >= MIN_AGE_DAYS and video.get("views", 0) < LOW_VIEWS:
            video["reasons"].append(REASON_UNWATCHED)
        groups.setdefault(normalize_title(title), []).append(video)
    for group in groups.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda v: v.get("views", 0))
        for video in group:
            if video is not keep:
                video["reasons"].append(REASON_REPEAT)
    flagged = [v for v in videos if v["reasons"]]
    return sorted(flagged, key=lambda v: (-len(v["reasons"]), v.get("views", 0)))


def junk_ids(flagged: list[dict]) -> list[str]:
    """Clear junk: repeats, live recordings and public videos nobody watched."""
    return [v["video_id"] for v in flagged if JUNK_REASONS & set(v["reasons"])]


def format_audit(videos: list[dict], flagged: list[dict]) -> str:
    public = [v for v in videos if v.get("privacy") == "public"]
    counts: dict[str, int] = {}
    for v in flagged:
        for reason in v["reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    lines = [
        "### Kanal temizlik raporu",
        "",
        f"Toplam {len(videos)} video, {len(public)} herkese açık, {len(flagged)} aday.",
        "",
        *[f"- {reason}: {n}" for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])],
        f"- Kesin çöp (tekrar, izlenmeyen, canlı yayın): {len(junk_ids(flagged))}",
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


def audit_channel(youtube, keep_ids: frozenset = frozenset()) -> tuple[list[dict], list[dict], str]:
    videos = add_details(youtube, list_uploads(youtube))
    flagged = flag_candidates(videos, keep_ids=keep_ids)
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
