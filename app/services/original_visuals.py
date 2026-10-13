"""
Low-cost original visuals for Shorts.

Every scene is illustrated with either a free stock video (Pexels with
PEXELS_API_KEY, else Pixabay with PIXABAY_API_KEY; both licensed for commercial
use) or a Fal Flux image (FAL_KEY) animated with a slow camera move. No AI
video, no film footage. Scenes are cut into segments of at most ~2.5 s (1.6 s
for the stock-footage curiosity series, like the reference Shorts) so the edit
keeps moving.

Stock search only returns real footage (no animation). For the curiosity series
every clip must also carry the topic's subject in its tags (a cat video shows
cats, never mountains or skeletons), and Gemini looks at the candidates'
thumbnails to give each sentence the clips that show it (clip_matcher.py).

IMAGE_MODEL picks the Flux model: fal-ai/flux/schnell (default, $0.003 per
image) or fal-ai/flux/dev (sharper, $0.025 per image). Images are 720x1280 so
each is billed as one megapixel. Every prompt of a video ends with the same
style line, so the pictures read as one film instead of a random collage.
"""

import json
import math
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

FPS = 60
WIDTH, HEIGHT = 1080, 1920
MAX_SEGMENT_SECONDS = 2.5
IMAGE_SIZE = {"width": 720, "height": 1280}  # 9:16 and under one billed megapixel
DEFAULT_IMAGE_MODEL = "fal-ai/flux/schnell"
DEFAULT_STYLE = (
    "Cinematic vertical composition, dramatic natural lighting, rich detail, film grain"
)
USER_AGENT = "Mozilla/5.0 (zirvenin-kanunu original shorts)"
MAX_CANDIDATES = 48  # thumbnails shown to Gemini per video
# Words that say nothing about what a clip shows; they do not count when ranking clips by their tags.
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "and", "with", "from", "to", "its", "is", "up",
    "slow", "motion", "close", "closeup", "view", "shot", "video", "footage",
}

ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-pix_fmt", "yuv420p", "-r", str(FPS), "-an",
]


def _download(url: str, dest: Path, headers: dict | None = None, timeout: int = 60) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def _pick_rendition(files: list[dict]) -> dict | None:
    """Smallest portrait file that is full HD (then >= 1280 px tall), else the
    smallest landscape file that still survives a 9:16 crop (>= 1080, then 720 px)."""
    files = [f for f in files if f.get("link")]
    for min_height in (HEIGHT, 1280):
        portrait = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0) and (f.get("height") or 0) >= min_height]
        if portrait:
            return min(portrait, key=lambda f: f["height"])
    for min_height in (1080, 720):
        landscape = [f for f in files if (f.get("height") or 0) >= min_height]
        if landscape:
            return min(landscape, key=lambda f: f["height"])
    return None


def _pick_pexels_file(video: dict) -> dict | None:
    return _pick_rendition([f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"])


def _pick_pixabay_file(hit: dict) -> dict | None:
    renditions = (hit.get("videos") or {}).values()
    return _pick_rendition([
        {"link": r.get("url"), "width": r.get("width"), "height": r.get("height")}
        for r in renditions if isinstance(r, dict) and r.get("url") and r.get("size")
    ])


def fetch_pexels_videos(query: str, count: int, dest_dir: Path, used_ids: set,
                        subject: list[str] | None = None) -> list[Path]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not query or count <= 0:
        return []
    params = urllib.parse.urlencode({"query": query, "orientation": "portrait", "per_page": 15})
    req = urllib.request.Request(
        f"https://api.pexels.com/videos/search?{params}",
        headers={"Authorization": key, "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            videos = json.loads(resp.read().decode("utf-8")).get("videos", [])
    except Exception as e:
        print(f"ℹ️ Pexels arama notu ({query}): {e}")
        return []

    results = []
    for video in videos:
        if len(results) >= count:
            break
        if video.get("id") in used_ids or (video.get("duration") or 0) < 3:
            continue
        if not matches_subject(video.get("url", ""), subject):  # Pexels has no tags; its page slug names the scene
            continue
        chosen = _pick_pexels_file(video)
        if not chosen:
            continue
        dest = dest_dir / f"pexels_{video['id']}.mp4"
        try:
            _download(chosen["link"], dest, timeout=120)
        except Exception as e:
            print(f"ℹ️ Pexels indirme notu ({video.get('id')}): {e}")
            continue
        used_ids.add(video.get("id"))
        results.append(dest)
    return results


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def _forms(word: str) -> set[str]:
    forms = {word, word + "s", word + "es"}
    if word.endswith("y"):
        forms.add(word[:-1] + "ies")
    return forms


def matches_subject(tags: str, subject: list[str] | None) -> bool:
    """True when the tags name the topic's subject: any term, and every word of a multi-word term
    ("polar bear" needs both words, so a brown bear does not pass). No subject means no filter."""
    terms = [t.lower().split() for t in subject or [] if t and t.strip()]
    if not terms:
        return True
    words = _words(tags)
    return any(all(_forms(part) & words for part in term) for term in terms)


def tag_score(tags: str, query: str) -> int:
    """How many content words of the query the tags share (prefix match, so jumping ~ jump)."""
    words = _words(tags)
    wanted = [w for w in _words(query) if w not in STOPWORDS and len(w) > 2]
    return sum(1 for w in wanted if any(t.startswith(w[:4]) or (len(t) > 2 and w.startswith(t[:4])) for t in words))


def _is_vertical(hit: dict) -> bool:
    return any((r.get("height") or 0) > (r.get("width") or 0)
               for r in (hit.get("videos") or {}).values() if isinstance(r, dict))


def search_pixabay(query: str, per_page: int = 50, video_type: str = "film") -> list[dict]:
    """video_type=film leaves out animations: CGI clocks and skeletons looked random next to real footage."""
    key = os.environ.get("PIXABAY_API_KEY")
    if not key or not query:
        return []
    params = urllib.parse.urlencode({"key": key, "q": query[:100], "per_page": per_page, "safesearch": "true",
                                     "video_type": video_type})
    req = urllib.request.Request(f"https://pixabay.com/api/videos/?{params}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("hits", [])
    except Exception as e:
        print(f"ℹ️ Pixabay arama notu ({query}): {e}")
        return []


def pixabay_candidates(query: str, used_ids: set, subject: list[str] | None = None, limit: int = 50) -> list[dict]:
    """Usable clips for a query, best first: real footage of at least 3 s, not used yet, tagged with the
    subject, ranked by how many query words their tags share, vertical clips first among equals."""
    hits = [
        h for h in search_pixabay(query)
        if h.get("type", "film") == "film" and (h.get("duration") or 0) >= 3
        and f"pixabay:{h.get('id')}" not in used_ids and matches_subject(h.get("tags", ""), subject)
        and _pick_pixabay_file(h)
    ]
    hits.sort(key=lambda h: (-tag_score(h.get("tags", ""), query), not _is_vertical(h)))
    return hits[:limit]


def download_pixabay_hit(hit: dict, dest_dir: Path, used_ids: set) -> Path | None:
    chosen = _pick_pixabay_file(hit)
    if not chosen:
        return None
    dest = dest_dir / f"pixabay_{hit['id']}.mp4"
    try:
        _download(chosen["link"], dest, timeout=120)
    except Exception as e:
        print(f"ℹ️ Pixabay indirme notu ({hit.get('id')}): {e}")
        return None
    used_ids.add(f"pixabay:{hit.get('id')}")
    return dest


def fetch_pixabay_videos(query: str, count: int, dest_dir: Path, used_ids: set,
                         subject: list[str] | None = None) -> list[Path]:
    if count <= 0:
        return []
    results = []
    for hit in pixabay_candidates(query, used_ids, subject):
        if len(results) >= count:
            break
        path = download_pixabay_hit(hit, dest_dir, used_ids)
        if path:
            results.append(path)
    return results


def fetch_stock_videos(query: str, count: int, dest_dir: Path, used_ids: set,
                       subject: list[str] | None = None) -> list[Path]:
    """Pexels first, Pixabay for whatever is still missing. Either key may be absent."""
    found = fetch_pexels_videos(query, count, dest_dir, used_ids, subject=subject)
    if len(found) < count:
        found += fetch_pixabay_videos(query, count - len(found), dest_dir, used_ids, subject=subject)
    return found


def stock_queries(query: str, fallback: str = "", subject: list[str] | None = None) -> list[str]:
    """The scene's query, the topic's general hint, the query cut to its first two words, then each subject term.

    Long model-written queries ("a curious cat landing on its feet") often return
    nothing on Pixabay, while two plain words almost always do."""
    words = query.split()
    candidates = [query, fallback, " ".join(words[:2]) if len(words) > 2 else "", *(subject or [])]
    out: list[str] = []
    for q in candidates:
        q = " ".join(q.split())
        if q and q.lower() not in [o.lower() for o in out]:
            out.append(q)
    return out


def fetch_stock_with_fallbacks(query: str, fallback: str, count: int, dest_dir: Path, used_ids: set,
                               subject: list[str] | None = None) -> list[Path]:
    found: list[Path] = []
    for q in stock_queries(query, fallback, subject):
        if len(found) >= count:
            break
        found += fetch_stock_videos(q, count - len(found), dest_dir, used_ids, subject=subject)
    return found


def collect_matched_stock(scenes: list[dict], plans: list[list[float]], assets_dir: Path, used_ids: set,
                          subject: list[str] | None = None, fallback_query: str = "", topic: str = "",
                          match=None) -> tuple[list[list[tuple[str, Path]]], str]:
    """Stock-only scenes. Gathers on-subject Pixabay candidates for every scene, lets Gemini pick from their
    thumbnails the clips that show each sentence, and downloads only those. A scene Gemini found nothing for
    stays empty and later reuses the video's other (also on-subject) clips. Without Gemini the clips come
    in keyword order. Returns (assets per scene, how they were matched)."""
    from app.services import clip_matcher

    pool: list[dict] = []
    seen: set = set()

    def add(hits: list[dict]) -> None:
        for h in hits:
            if len(pool) < MAX_CANDIDATES and h.get("id") not in seen:
                seen.add(h.get("id"))
                pool.append(h)

    for scene in scenes:
        query = scene.get("stock_query", "")
        hits = pixabay_candidates(query, used_ids, subject, limit=6)
        if len(hits) < 3 and len(query.split()) > 2:
            hits += pixabay_candidates(" ".join(query.split()[:2]), used_ids, subject, limit=6)
        add(hits)
    for q in [fallback_query, *(subject or [])]:
        if q:
            add(pixabay_candidates(q, used_ids, subject, limit=12))

    wanted = [len(plan) for plan in plans]
    picks = None
    if pool:
        try:
            picks = (match or clip_matcher.match_clips)(topic, scenes, wanted, pool)
        except Exception as e:  # matching is an upgrade; keyword order still gives on-subject clips
            print(f"ℹ️ Görüntü eşleştirme notu: {str(e)[:200]}")

    scene_assets: list[list[tuple[str, Path]]] = []
    for idx, (scene, want) in enumerate(zip(scenes, wanted)):
        if picks is not None:
            paths = [download_pixabay_hit(pool[i], assets_dir, used_ids) for i in picks[idx]]
            assets = [("video", p) for p in paths if p]
            print(f"       Sahne {idx + 1}: {len(assets)} eşleşen klip")
        else:
            assets = [("video", p) for p in fetch_stock_with_fallbacks(
                scene.get("stock_query", ""), fallback_query, want, assets_dir, used_ids, subject)]
        scene_assets.append(assets)
    return scene_assets, "gemini" if picks is not None else "anahtar kelime"


def image_model() -> str:
    model = (os.environ.get("IMAGE_MODEL") or DEFAULT_IMAGE_MODEL).strip()
    return model if model.startswith("fal-ai/") else DEFAULT_IMAGE_MODEL


def generate_flux_images(prompt: str, count: int, dest_dir: Path, tag: str, style: str = DEFAULT_STYLE) -> list[Path]:
    key = os.environ.get("FAL_KEY")
    if not key or not prompt or count <= 0:
        return []
    full_prompt = f"{prompt.rstrip('. ')}. {style}, no text, no letters, no watermark, no logo"
    results = []
    while len(results) < count:
        batch = min(4, count - len(results))
        payload = {
            "prompt": full_prompt,
            "image_size": IMAGE_SIZE,
            "num_images": batch,
            "enable_safety_checker": True,
        }
        req = urllib.request.Request(
            f"https://fal.run/{image_model()}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                images = json.loads(resp.read().decode("utf-8")).get("images", [])
        except Exception as e:
            print(f"ℹ️ Fal görsel notu: {e}")
            break
        if not images:
            break
        for img in images:
            dest = dest_dir / f"flux_{tag}_{len(results):02d}.jpg"
            try:
                _download(img["url"], dest)
                results.append(dest)
            except Exception as e:
                print(f"ℹ️ Fal indirme notu: {e}")
    return results


def _motion_expr(motion: int, frames: int) -> tuple[str, str, str]:
    p = f"(on/{max(1, frames - 1)})"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if motion % 4 == 0:  # slow push in
        return f"1+0.16*{p}", center_x, center_y
    if motion % 4 == 1:  # slow pull out
        return f"1.16-0.16*{p}", center_x, center_y
    if motion % 4 == 2:  # pan left to right
        return "1.14", f"(iw-iw/zoom)*{p}", center_y
    return "1.14", center_x, f"(ih-ih/zoom)*(1-{p})"  # tilt up


# One grade for every segment so stock clips and images sit in the same look.
GRADE = "eq=contrast=1.08:saturation=0.92:gamma=0.97,vignette=angle=PI/5"


def render_image_segment(image: Path, out: Path, duration: float, motion: int) -> Path:
    frames = max(2, round(duration * FPS))
    z, x, y = _motion_expr(motion, frames)
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        f"{GRADE},setsar=1"
    )
    cmd = ["ffmpeg", "-y", "-i", str(image), "-vf", vf, "-frames:v", str(frames), *ENCODE_ARGS, str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def render_video_segment(video: Path, out: Path, duration: float, offset: float) -> Path:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fps={FPS},{GRADE},setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", str(video),
        "-vf", vf, "-t", f"{duration:.3f}", *ENCODE_ARGS, str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def split_duration(duration: float, max_len: float = MAX_SEGMENT_SECONDS) -> list[float]:
    count = max(1, math.ceil(duration / max_len))
    return [duration / count] * count


def collect_scene_assets(scene: dict, count: int, assets_dir: Path, tag: str, used_ids: set,
                         style: str = DEFAULT_STYLE, ai_only: bool = False) -> list[tuple[str, Path]]:
    """Returns up to `count` (kind, path) assets, trying the scene's preferred source first.

    ai_only skips stock video unless no image can be generated: keyword stock
    footage rarely matches a film character's world and made those videos look random."""
    assets: list[tuple[str, Path]] = []
    if scene.get("source") == "stock" and not ai_only:
        assets += [("video", p) for p in fetch_stock_videos(scene.get("stock_query", ""), count, assets_dir, used_ids)]
    if len(assets) < count:
        assets += [("image", p) for p in generate_flux_images(scene["visual"], count - len(assets), assets_dir, tag, style)]
    if len(assets) < count and (scene.get("source") != "stock" or ai_only):
        assets += [("video", p) for p in fetch_stock_videos(scene.get("stock_query", ""), count - len(assets), assets_dir, used_ids)]
    return assets


def build_visual_track(scenes: list[dict], durations: list[float], work_dir: Path,
                       style: str = DEFAULT_STYLE, ai_only: bool = False, stock_only: bool = False,
                       segment_seconds: float = MAX_SEGMENT_SECONDS, fallback_query: str = "",
                       subject: list[str] | None = None, topic: str = "") -> dict:
    """
    Renders one clip per segment and returns
    {"segments": [Path], "cut_times": [float], "ai_images": int, "stock_videos": int, "thumbnail": Path | None,
     "clip_match": str | None}.
    stock_only (curiosity series): real footage only, matched to each sentence; Flux images are drawn
    only if no clip at all could be found for the video.
    Raises RuntimeError if no scene could be illustrated at all.
    """
    assets_dir = work_dir / "assets"
    seg_dir = work_dir / "segments"
    assets_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)

    used_ids: set = set()
    bank: list[tuple[str, Path]] = []
    segments: list[Path] = []
    cut_times: list[float] = []
    ai_images = stock_videos = 0
    thumbnail = None
    timeline = 0.0
    motion = 0

    plans = [split_duration(d, segment_seconds) for d in durations]
    clip_match = None
    if stock_only:
        scene_assets, clip_match = collect_matched_stock(scenes, plans, assets_dir, used_ids, subject,
                                                         fallback_query, topic)
        if not any(scene_assets):
            scene_assets = [
                [("image", p) for p in generate_flux_images(scene["visual"], len(plan), assets_dir, f"s{idx:02d}", style)]
                for idx, (scene, plan) in enumerate(zip(scenes, plans))
            ]
    else:
        scene_assets = [
            collect_scene_assets(scene, len(plan), assets_dir, f"s{idx:02d}", used_ids, style, ai_only)
            for idx, (scene, plan) in enumerate(zip(scenes, plans))
        ]
    for assets in scene_assets:
        bank += assets
    if not bank:
        raise RuntimeError("no stock video or AI image could be fetched for any scene")

    for idx, (plan, assets) in enumerate(zip(plans, scene_assets)):
        if not assets:
            # Reuse earlier material with a different camera move rather than leave a gap.
            assets = [bank[(idx + k) % len(bank)] for k in range(len(plan))]
        for seg_idx, seg_dur in enumerate(plan):
            kind, path = assets[seg_idx % len(assets)]
            out = seg_dir / f"seg_{idx:02d}_{seg_idx:02d}.mp4"
            if kind == "image":
                render_image_segment(path, out, seg_dur, motion)
                ai_images += 1
                thumbnail = thumbnail or path
            else:
                render_video_segment(path, out, seg_dur, offset=0.3 + 0.7 * (seg_idx % 3))
                stock_videos += 1
            motion += 1
            if segments:
                cut_times.append(round(timeline, 3))
            segments.append(out)
            timeline += seg_dur

    return {
        "segments": segments,
        "cut_times": cut_times,
        "ai_images": ai_images,
        "stock_videos": stock_videos,
        "thumbnail": thumbnail,
        "clip_match": clip_match,
    }
