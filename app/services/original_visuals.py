"""
Low-cost original visuals for Shorts.

Every scene is illustrated with either a free Pexels stock video (licensed for
commercial use, PEXELS_API_KEY) or a Fal Flux Schnell image (~$0.003 each,
FAL_KEY) animated with a slow camera move. No AI video, no film footage.
Scenes are cut into segments of at most ~2.8 s so the edit keeps moving.
"""

import json
import math
import os
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

FPS = 60
WIDTH, HEIGHT = 1080, 1920
MAX_SEGMENT_SECONDS = 2.8
USER_AGENT = "Mozilla/5.0 (zirvenin-kanunu original shorts)"

ENCODE_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-pix_fmt", "yuv420p", "-r", str(FPS), "-an",
]


def _download(url: str, dest: Path, headers: dict | None = None, timeout: int = 60) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest


def _pick_pexels_file(video: dict) -> dict | None:
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
    portrait = [f for f in files if (f.get("height") or 0) > (f.get("width") or 0) and (f.get("height") or 0) >= 1280]
    if portrait:
        return min(portrait, key=lambda f: f["height"])
    landscape = [f for f in files if (f.get("height") or 0) >= 720]
    return min(landscape, key=lambda f: f["height"]) if landscape else None


def fetch_pexels_videos(query: str, count: int, dest_dir: Path, used_ids: set) -> list[Path]:
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


def generate_flux_images(prompt: str, count: int, dest_dir: Path, tag: str) -> list[Path]:
    key = os.environ.get("FAL_KEY")
    if not key or not prompt or count <= 0:
        return []
    full_prompt = (
        f"{prompt}. Cinematic vertical composition, dramatic natural lighting, rich detail, "
        "film grain, no text, no letters, no watermark, no logo"
    )
    results = []
    while len(results) < count:
        batch = min(4, count - len(results))
        payload = {
            "prompt": full_prompt,
            "image_size": {"width": 768, "height": 1344},
            "num_images": batch,
            "enable_safety_checker": True,
        }
        req = urllib.request.Request(
            "https://fal.run/fal-ai/flux/schnell",
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


def render_image_segment(image: Path, out: Path, duration: float, motion: int) -> Path:
    frames = max(2, round(duration * FPS))
    z, x, y = _motion_expr(motion, frames)
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "eq=contrast=1.06:saturation=1.05,setsar=1"
    )
    cmd = ["ffmpeg", "-y", "-i", str(image), "-vf", vf, "-frames:v", str(frames), *ENCODE_ARGS, str(out)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def render_video_segment(video: Path, out: Path, duration: float, offset: float) -> Path:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        f"fps={FPS},eq=contrast=1.06:saturation=1.05,setsar=1"
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


def collect_scene_assets(scene: dict, count: int, assets_dir: Path, tag: str, used_ids: set) -> list[tuple[str, Path]]:
    """Returns up to `count` (kind, path) assets, trying the scene's preferred source first."""
    assets: list[tuple[str, Path]] = []
    if scene.get("source") == "stock":
        assets += [("video", p) for p in fetch_pexels_videos(scene.get("stock_query", ""), count, assets_dir, used_ids)]
    if len(assets) < count:
        assets += [("image", p) for p in generate_flux_images(scene["visual"], count - len(assets), assets_dir, tag)]
    if len(assets) < count and scene.get("source") != "stock":
        assets += [("video", p) for p in fetch_pexels_videos(scene.get("stock_query", ""), count - len(assets), assets_dir, used_ids)]
    return assets


def build_visual_track(scenes: list[dict], durations: list[float], work_dir: Path) -> dict:
    """
    Renders one clip per segment and returns
    {"segments": [Path], "cut_times": [float], "ai_images": int, "stock_videos": int, "thumbnail": Path | None}.
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

    plans = [split_duration(d) for d in durations]
    scene_assets = []
    for idx, (scene, plan) in enumerate(zip(scenes, plans)):
        assets = collect_scene_assets(scene, len(plan), assets_dir, f"s{idx:02d}", used_ids)
        scene_assets.append(assets)
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
    }
