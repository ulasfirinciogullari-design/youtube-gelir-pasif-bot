"""The week's most watched Zirvenin Kanunu Shorts in one 16:9 video with chapters.

Shorts views do not count toward YouTube's 4,000 watch hours; a long video does. Once a week this takes the public
Shorts of the last seven days that no earlier compilation used, ranks them by YouTube views, downloads the top ones
from the artifacts of the runs that published them (kept 14 days), and joins them full height on a blurred copy of
themselves, best first, with one chapter per Short. Nothing is generated, so it costs no voice or image credits.

    python scripts/compilation.py --data channel-data --out output/derleme [--privacy private] [--count 15]
    python scripts/compilation.py ... --no-upload   # build the video and its storyboard, upload nothing

Standard library plus ffmpeg. The record of every upload goes to <data>/compilations.json, which the workflow
commits to channel-data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))
import comment_replies  # noqa: E402

STATE_FILE = "compilations.json"
UPLOADS_FILE = "uploads.json"
WINDOW = timedelta(days=7)
COUNT = 15
MIN_COUNT = 5  # fewer than this is not worth a video
WIDTH, HEIGHT, FPS = 1920, 1080, 30
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3"
CATEGORY = "27"  # Education, like the Shorts
FIXED_TAGS = ["zirvenin kanunu", "merak", "bilgi", "ilginç bilgiler", "bilim", "derleme", "haftanın en iyileri"]
TAGS_CHARS = 450  # YouTube allows 500 characters of tags, counting separators
HASHTAGS = "#bilgi #merak #bilim #ilginçbilgiler #keşfet"
CHANNEL_SHORTS = "https://www.youtube.com/@zirveninkanunu/shorts"
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
          "Aralık")
# The blurred background is drawn small and scaled up: a blur at full size costs several times as much.
BACKGROUND = (f"scale=480:270:force_original_aspect_ratio=increase,crop=480:270,boxblur=12:2,"
              f"scale={WIDTH}:{HEIGHT},eq=brightness=-0.18:saturation=0.8")
ENCODE = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2")
FONT = "Montserrat Black"


class CompilationError(RuntimeError):
    pass


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _uploaded(upload: dict) -> datetime:
    return datetime.fromisoformat(upload["uploaded_at"])


def candidates(data_dir: Path, now: datetime, window: timedelta = WINDOW) -> list[dict]:
    """Public Shorts uploaded within `window` that no earlier published compilation used, newest first. A private
    compilation is a sample and uses nothing up."""
    used = {vid for c in load_json(data_dir / STATE_FILE, []) if c.get("privacy") != "private"
            for vid in c.get("shorts", [])}
    found = []
    for upload in load_json(data_dir / UPLOADS_FILE, []):
        if not isinstance(upload, dict) or not upload.get("video_id") or not upload.get("uploaded_at"):
            continue
        if upload.get("privacy") != "public" or upload["video_id"] in used:
            continue
        try:
            age = now - _uploaded(upload)
        except (TypeError, ValueError):  # a malformed or zone-less time
            continue
        if not timedelta(0) <= age <= window:
            continue
        found.append(upload)
    return sorted(found, key=_uploaded, reverse=True)


def youtube_views(ids: list[str], env=os.environ) -> dict:
    """Public views of the videos, 50 to a call (1 YouTube unit each)."""
    tok = comment_replies.access_token(env)
    views = {}
    for i in range(0, len(ids), 50):
        query = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids[i:i + 50])})
        data = comment_replies._request(f"{comment_replies.YOUTUBE_API}/videos?{query}",
                                        headers={"Authorization": f"Bearer {tok}"})
        views.update({v["id"]: int((v.get("statistics") or {}).get("viewCount") or 0)
                      for v in data.get("items") or []})
    return views


def rank(uploads: list[dict], views: dict) -> list[dict]:
    """Most viewed first; a tie goes to the newer Short."""
    return sorted(uploads, key=lambda u: (views.get(u["video_id"], 0), _uploaded(u)), reverse=True)


def clean_title(title: str) -> str:
    """The title without emoji and hashtags, for a chapter line."""
    text = re.sub(r"#\S+", "", title)
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace() or ch in ".,:;!?'\"-()%&")
    return re.sub(r"\s+", " ", text).strip()


def stamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def chapters(parts: list[tuple[str, float]]) -> list[str]:
    """One "m:ss Title" line per Short from its title and length; YouTube wants the first at 0:00."""
    lines, at = [], 0.0
    for title, length in parts:
        lines.append(f"{stamp(at)} {clean_title(title)}")
        at += length
    return lines


def title_for(count: int, now: datetime) -> str:
    return f"Haftanın En Çok İzlenen {count} Merak Sorusu 🤯 | {now.day} {MONTHS[now.month - 1]} Derlemesi"[:100]


def description_for(count: int, chapter_lines: list[str]) -> str:
    return "\n".join([
        f"Bu hafta Zirvenin Kanunu'nda en çok izlenen {count} Short tek videoda. Her biri bir soru, her cevap "
        "yarım dakikadan kısa.",
        "",
        "Bölümler:",
        *chapter_lines,
        "",
        f"Her gün yeni bir merak sorusu: {CHANNEL_SHORTS}",
        "Hangisini bilmiyordun? Yorumlara yaz 👇",
        "",
        HASHTAGS,
    ])[:5000]


def tags_for(metas: list[dict]) -> list[str]:
    tags, seen, size = [], set(), 0
    for tag in FIXED_TAGS + [t for m in metas for t in m.get("tags") or []]:
        tag = str(tag).strip()
        key = tag.lower()
        if not tag or key in seen or key == "shorts" or "#" in tag:
            continue
        if size + len(tag) + 1 > TAGS_CHARS:
            break
        seen.add(key)
        tags.append(tag)
        size += len(tag) + 1
    return tags


def run(cmd: list[str], timeout: int = 900) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True, timeout=60).stdout
    return float(out.strip())


def widescreen(short: Path, out: Path) -> Path:
    """The vertical Short full height in the middle of a 16:9 frame, on a blurred, darker copy of itself."""
    graph = (f"[0:v]split=2[a][b];[a]{BACKGROUND}[bg];[b]scale=-2:{HEIGHT}[fg];"
             f"[bg][fg]overlay=(W-w)/2:0,setsar=1[v]")
    run(["ffmpeg", "-y", "-i", str(short), "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0",
         *ENCODE, str(out)])
    return out


def join(segments: list[Path], out: Path) -> Path:
    """The segments share one encoding, so they are joined without encoding again."""
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{s.resolve()}'\n" for s in segments), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
         "-movflags", "+faststart", str(out)])
    return out


def thumbnail(covers: list[Path], count: int, frame_from: Path, out: Path) -> Path:
    """The three best Shorts' covers side by side with the count on top; a frame of the video without covers."""
    covers = [c for c in covers if c.exists()][:3]
    text = f"{count} SORU"
    draw = (f"drawtext=font='{FONT}':text='{text}':fontsize=150:fontcolor=#FFD400:borderw=10:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-text_h-60")
    try:
        if len(covers) == 3:
            inputs = [arg for c in covers for arg in ("-i", str(c))]
            tiles = "".join(f"[{i}:v]scale=427:720:force_original_aspect_ratio=increase,crop=427:720,setsar=1[t{i}];"
                            for i in range(3))
            graph = f"{tiles}[t0][t1][t2]hstack=inputs=3,scale=1280:720,{draw}[v]"
            run(["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
                 "-q:v", "2", str(out)], timeout=120)
            return out
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ℹ️ Kapak kolajı çizilemedi, videodan kare alınıyor: {e}")
    run(["ffmpeg", "-y", "-ss", "1", "-i", str(frame_from), "-vf", f"scale=1280:720,{draw}", "-frames:v", "1",
         "-q:v", "2", str(out)], timeout=120)
    return out


def storyboard(video: Path, out: Path, length: float, every: float = 5.0) -> Path | None:
    """One frame every `every` seconds, six to a row, for a session that cannot open YouTube or the artifacts."""
    cols = 6
    rows = max(1, math.ceil(math.ceil(length / every) / cols))
    try:
        run(["ffmpeg", "-y", "-i", str(video), "-vf", f"fps=1/{every},scale=320:180,tile={cols}x{rows}",
             "-frames:v", "1", "-q:v", "5", str(out)], timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"ℹ️ Kare özeti çıkarılamadı: {e}")
        return None
    return out


def _upload_request(url: str, data, headers: dict, method: str = "POST"):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        return urllib.request.urlopen(req, timeout=1800)
    except urllib.error.HTTPError as e:
        raise CompilationError(f"YouTube {e.code}: {e.read().decode('utf-8', 'replace')[:500]}") from None


def upload(video: Path, cover: Path | None, body: dict, env=os.environ, send=_upload_request) -> str:
    """A resumable upload in one piece, then the cover. Returns the video id."""
    tok = comment_replies.access_token(env)
    auth = {"Authorization": f"Bearer {tok}"}
    size = video.stat().st_size
    params = urllib.parse.urlencode({"uploadType": "resumable", "part": ",".join(body)})
    with send(f"{UPLOAD_URL}/videos?{params}", json.dumps(body).encode("utf-8"),
              {**auth, "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
               "X-Upload-Content-Length": str(size)}) as resp:
        location = resp.headers["Location"]
    with video.open("rb") as f, send(location, f, {**auth, "Content-Type": "video/mp4",
                                                   "Content-Length": str(size)}, method="PUT") as resp:
        video_id = json.loads(resp.read().decode("utf-8"))["id"]
    if cover and cover.exists():
        # The video is up by now: a failed cover must not read as a failed upload and send it up twice.
        try:
            with send(f"{UPLOAD_URL}/thumbnails/set?videoId={video_id}", cover.read_bytes(),
                      {**auth, "Content-Type": "image/jpeg"}):
                pass
        except (CompilationError, urllib.error.URLError, OSError) as e:
            print(f"ℹ️ Kapak görseli notu: {e}")
    return video_id


def build(data_dir: Path, out_dir: Path, now: datetime, count: int = COUNT, env=os.environ, find=None,
          views=youtube_views) -> dict:
    """Downloads and joins the week's top Shorts -> {"video", "cover", "story", "body", "shorts"}."""
    if find is None:
        from app.services import tiktok
        find = tiktok.find_short
    pool = candidates(data_dir, now)
    if len(pool) < MIN_COUNT:
        raise CompilationError(f"son 7 günde derlemeye girmemiş {len(pool)} Short var, en az {MIN_COUNT} gerekli")
    ranked = rank(pool, views([u["video_id"] for u in pool], env))
    out_dir.mkdir(parents=True, exist_ok=True)
    picked, segments, metas, covers, parts = [], [], [], [], []
    for upload_ in ranked:
        if len(picked) >= count:
            break
        try:
            found = find(upload_, out_dir / upload_["video_id"], env.get("GITHUB_REPOSITORY", ""),
                         env.get("GITHUB_TOKEN", ""), extras=("_thumb.jpg",))
        except (urllib.error.URLError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
            print(f"ℹ️ {upload_['video_id']} indirilemedi: {e}")
            continue
        if found is None:
            print(f"ℹ️ {upload_['video_id']} için yayın paketi yok (paketler 14 gün saklanıyor)")
            continue
        short, meta = found
        try:
            segment = widescreen(short, out_dir / f"parca_{len(picked) + 1:02d}.mp4")
        except (OSError, subprocess.SubprocessError) as e:
            print(f"ℹ️ {upload_['video_id']} yatay kareye çizilemedi: {e}")
            continue
        picked.append(upload_["video_id"])
        segments.append(segment)
        metas.append(meta)
        covers.append(short.with_name(f"{short.stem}_thumb.jpg"))
        parts.append((meta.get("title") or upload_.get("title", ""), duration(segment)))
        print(f"   {len(picked)}. {upload_.get('title', '')} ({parts[-1][1]:.1f} sn)")
    if len(picked) < MIN_COUNT:
        raise CompilationError(f"sadece {len(picked)} Short indirilebildi, en az {MIN_COUNT} gerekli")
    stem = f"DERLEME_{now:%Y%m%d}"
    video = join(segments, out_dir / f"{stem}.mp4")
    length = duration(video)
    cover = thumbnail(covers, len(picked), segments[0], out_dir / f"{stem}_thumb.jpg")
    story = storyboard(video, out_dir / f"{stem}_story.jpg", length)
    body = {
        "snippet": {"title": title_for(len(picked), now), "description": description_for(len(picked), chapters(parts)),
                    "tags": tags_for(metas), "categoryId": CATEGORY, "defaultLanguage": "tr",
                    "defaultAudioLanguage": "tr"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": any(bool(m.get("contains_synthetic_media")) for m in metas)},
    }
    print(f"🎞️ Derleme hazır: {len(picked)} Short, {length:.0f} sn")
    return {"video": video, "cover": cover, "story": story, "body": body, "shorts": picked, "seconds": round(length)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("channel-data"))
    parser.add_argument("--out", type=Path, default=Path("output/derleme"))
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--count", type=int, default=COUNT)
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        result = build(args.data, args.out, now, args.count)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(f"❌ Derleme yapılamadı: {e}")
        return 1
    print(result["body"]["snippet"]["title"])
    print(result["body"]["snippet"]["description"])
    history = args.data / "history"
    if result["story"]:
        history.mkdir(parents=True, exist_ok=True)
        (history / result["story"].name).write_bytes(result["story"].read_bytes())
    if args.no_upload:
        print("ℹ️ Yükleme istenmedi, video sadece Artifact olarak kalıyor")
        return 0
    result["body"]["status"]["privacyStatus"] = args.privacy
    try:
        video_id = upload(result["video"], result["cover"], result["body"])
    except (RuntimeError, OSError) as e:
        print(f"❌ Yükleme başarısız: {e}")
        return 1
    print(f"🎉 Derleme yüklendi ({args.privacy}): https://www.youtube.com/watch?v={video_id}")
    state = load_json(args.data / STATE_FILE, [])
    state.append({"at": now.isoformat(timespec="seconds"), "video_id": video_id, "privacy": args.privacy,
                  "title": result["body"]["snippet"]["title"], "seconds": result["seconds"],
                  "shorts": result["shorts"]})
    (args.data / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
