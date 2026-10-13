"""
Competitor scan: who wins Turkish Shorts in our niche, and how.

Searches the niche for the most viewed Shorts of the last 90 days, groups them
by channel, then reads the strongest channels' recent uploads: cadence,
length, views, and their best titles. Gemini turns that into each channel's
"DNA" (hooks, title formulas, topics, weaknesses), the gaps nobody covers, and
rules for our scripts so the channel stands apart instead of copying. The
rules and topic ideas are saved and read back by the story factory.

Quota: each search costs 100 of the 10,000 daily units, everything else 1-2,
so one scan is about 650 units.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import statistics

from app.services.channel_audit import _seconds
from app.services.original_script import EMOJI_RE, _extract_json, _post_json

NICHE_QUERIES = [
    "stoacılık", "tarihin ilginç olayları", "psikoloji bilgileri", "güç kanunları", "felsefe shorts", "tarihi kişilikler",
]
TOP_CHANNELS = 6
RECENT_UPLOADS = 30
DNA_FILE = "competitor_dna.json"

ANALYSIS_PROMPT = """Bir Türkçe YouTube Shorts kanalının ("Zirvenin Kanunu": tarihten gerçek insanların gerilimli anları,
dahice hamleleri ve psikoloji; belgesel tonunda Türkçe anlatım) rakip analizini yapıyorsun.

Bizim başlıklarımız:
{ours}

Rakip kanalların verisi (JSON):
{data}

Her rakip kanalın DNA'sını çıkar: hangi kanca kalıplarıyla açıyor, başlık formülleri, konu seçimi, video süresi,
yayın sıklığı, ses/anlatım tarzı (başlıklardan çıkarılabildiği kadarıyla), güçlü ve zayıf yanları.
Sonra bizim için: rakiplerin dolduramadığı boşluklar, onları kopyalamadan ayrışmamızı sağlayacak senaryo kuralları
ve rakiplerin işlemediği 12 yeni konu önerisi (her biri tek cümlelik soru, gerçek ve doğrulanabilir tarih/psikoloji).

Sadece şu JSON'u döndür:
{{
  "channels": [{{"name": "...", "dna": {{"hooks": ["..."], "title_formulas": ["..."], "topics": ["..."],
    "length": "...", "cadence": "...", "strengths": ["..."], "weaknesses": ["..."]}}}}],
  "gaps": ["..."],
  "our_rules": ["5-8 kısa, uygulanabilir senaryo kuralı"],
  "topic_ideas": ["12 konu"]
}}
"""


def search_shorts(youtube, query: str, published_after: str, max_results: int = 25) -> list[str]:
    items = youtube.search().list(
        part="id", q=query, type="video", videoDuration="short", regionCode="TR", relevanceLanguage="tr",
        order="viewCount", publishedAfter=published_after, maxResults=max_results,
    ).execute().get("items", [])
    return [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]


def video_details(youtube, ids: list[str]) -> list[dict]:
    out = []
    for start in range(0, len(ids), 50):
        items = youtube.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(ids[start:start + 50])
        ).execute().get("items", [])
        for item in items:
            snippet, stats = item.get("snippet", {}), item.get("statistics", {})
            out.append({
                "video_id": item.get("id"),
                "title": snippet.get("title", ""),
                "channel_id": snippet.get("channelId", ""),
                "channel": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt", ""),
                "tags": snippet.get("tags", [])[:15],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "seconds": _seconds(item.get("contentDetails", {}).get("duration", "")),
            })
    return out


def channel_details(youtube, channel_ids: list[str]) -> dict[str, dict]:
    out = {}
    for start in range(0, len(channel_ids), 50):
        items = youtube.channels().list(
            part="snippet,statistics,contentDetails", id=",".join(channel_ids[start:start + 50])
        ).execute().get("items", [])
        for item in items:
            stats = item.get("statistics", {})
            out[item["id"]] = {
                "channel_id": item["id"],
                "name": item.get("snippet", {}).get("title", ""),
                "subscribers": int(stats.get("subscriberCount", 0) or 0),
                "video_count": int(stats.get("videoCount", 0) or 0),
                "uploads": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
            }
    return out


def rank_channels(videos: list[dict], our_channel_id: str = "") -> list[tuple[str, int, int]]:
    """(channel_id, niche hits, total views of those hits), strongest first."""
    totals: dict[str, list[int]] = {}
    for video in videos:
        if video["channel_id"] and video["channel_id"] != our_channel_id:
            totals.setdefault(video["channel_id"], []).append(video["views"])
    ranked = [(cid, len(views), sum(views)) for cid, views in totals.items()]
    return sorted(ranked, key=lambda r: (r[2], r[1]), reverse=True)


def recent_uploads(youtube, uploads_playlist: str, count: int = RECENT_UPLOADS) -> list[dict]:
    page = youtube.playlistItems().list(playlistId=uploads_playlist, part="contentDetails", maxResults=count).execute()
    ids = [i["contentDetails"]["videoId"] for i in page.get("items", []) if i.get("contentDetails", {}).get("videoId")]
    return video_details(youtube, ids)


def title_features(titles: list[str]) -> dict:
    if not titles:
        return {}
    n = len(titles)
    return {
        "avg_chars": round(sum(len(t) for t in titles) / n),
        "emoji_pct": round(100 * sum(bool(EMOJI_RE.search(t)) for t in titles) / n),
        "question_pct": round(100 * sum("?" in t for t in titles) / n),
        "number_pct": round(100 * sum(bool(re.search(r"\d", t)) for t in titles) / n),
        "caps_word_pct": round(100 * sum(bool(re.search(r"\b[A-ZÇĞİÖŞÜ]{4,}\b", t)) for t in titles) / n),
    }


def channel_profile(channel: dict, uploads: list[dict], now: datetime) -> dict:
    dates = sorted(u["published_at"] for u in uploads if u.get("published_at"))
    weeks = 1.0
    if len(dates) >= 2:
        first = datetime.fromisoformat(dates[0].replace("Z", "+00:00"))
        weeks = max(1.0, (now - first).days / 7)
    best = sorted(uploads, key=lambda u: -u["views"])
    shorts = [u for u in uploads if 0 < u["seconds"] <= 180]
    return {
        **{k: channel[k] for k in ("name", "subscribers", "video_count")},
        "uploads_per_week": round(len(uploads) / weeks, 1),
        "median_views": int(statistics.median([u["views"] for u in uploads])) if uploads else 0,
        "avg_seconds": round(statistics.mean([u["seconds"] for u in shorts])) if shorts else 0,
        "shorts_pct": round(100 * len(shorts) / len(uploads)) if uploads else 0,
        "title_features": title_features([u["title"] for u in uploads]),
        "best_titles": [f"{u['title']} ({u['views']} izlenme, {u['seconds']} sn)" for u in best[:8]],
        "common_tags": [t for t, _ in _top_counts(tag for u in uploads for tag in u.get("tags", []))][:12],
    }


def _top_counts(items) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item).lower()
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _call_gemini_text(prompt: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    model = os.environ.get("GEMINI_SEO_MODEL", "gemini-2.5-flash")
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": [{"text": prompt}]}],
         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6}},
        {"x-goog-api-key": key},
        timeout=150,
    )
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def analyze(profiles: list[dict], our_titles: list[str], call=_call_gemini_text) -> dict:
    prompt = ANALYSIS_PROMPT.format(
        ours="\n".join(f"- {t}" for t in our_titles[:15]) or "- (henüz yok)",
        data=json.dumps(profiles, ensure_ascii=False, indent=1),
    )
    raw = call(prompt)
    if not raw:
        return {}
    data = _extract_json(raw)
    return {
        "channels": [c for c in data.get("channels", []) if isinstance(c, dict)][:TOP_CHANNELS],
        "gaps": [str(g) for g in data.get("gaps", [])][:10],
        "our_rules": [str(r) for r in data.get("our_rules", [])][:8],
        "topic_ideas": [str(t) for t in data.get("topic_ideas", [])][:15],
    }


def format_report(profiles: list[dict], dna: dict) -> str:
    lines = [
        "### Rakip kanal taraması",
        "",
        "| Kanal | Abone | Haftalık video | Medyan izlenme | Ort. süre | Başlıkta emoji | Başlıkta soru |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in profiles:
        f = p.get("title_features", {})
        lines.append(
            f"| {p['name']} | {p['subscribers']} | {p['uploads_per_week']} | {p['median_views']} | "
            f"{p['avg_seconds']}s | %{f.get('emoji_pct', 0)} | %{f.get('question_pct', 0)} |"
        )
    for channel in dna.get("channels", []):
        d = channel.get("dna", {})
        lines += ["", f"**{channel.get('name', '?')}**: kanca {', '.join(d.get('hooks', [])[:2])}; "
                      f"zayıf yanı {', '.join(d.get('weaknesses', [])[:2])}"]
    for title, key in (("Boşluklar", "gaps"), ("Bizim kurallarımız", "our_rules"), ("Yeni konu fikirleri", "topic_ideas")):
        if dna.get(key):
            lines += ["", f"**{title}**", ""] + [f"- {item}" for item in dna[key]]
    return "\n".join(lines) + "\n"


def run_scan(youtube, output_dir: Path, our_titles: list[str], queries: list[str] | None = None,
             call=_call_gemini_text) -> dict:
    now = datetime.now(timezone.utc)
    after = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mine = youtube.channels().list(part="id", mine=True).execute().get("items", [])
    our_id = mine[0]["id"] if mine else ""

    ids: list[str] = []
    for query in queries or NICHE_QUERIES:
        try:
            ids += search_shorts(youtube, query, after)
        except Exception as e:
            print(f"ℹ️ Arama notu ({query}): {str(e)[:160]}")
    niche = video_details(youtube, list(dict.fromkeys(ids)))
    ranked = rank_channels(niche, our_id)[:TOP_CHANNELS]
    channels = channel_details(youtube, [cid for cid, _, _ in ranked])

    profiles = []
    for cid, hits, views in ranked:
        channel = channels.get(cid)
        if not channel or not channel["uploads"]:
            continue
        uploads = recent_uploads(youtube, channel["uploads"])
        profile = channel_profile(channel, uploads, now)
        profile["niche_hits"], profile["niche_views"] = hits, views
        profiles.append(profile)

    try:
        dna = analyze(profiles, our_titles, call=call)
    except Exception as e:
        print(f"ℹ️ Rakip analizi notu: {str(e)[:200]}")
        dna = {}
    result = {"scanned_at": now.isoformat(), "queries": queries or NICHE_QUERIES, "profiles": profiles, **dna}
    (output_dir / DNA_FILE).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report = format_report(profiles, dna)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report)
    return result


def load_dna(*dirs: Path) -> dict:
    for directory in dirs:
        path = Path(directory) / DNA_FILE
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
    return {}
