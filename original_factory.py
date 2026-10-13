"""
ORIGINAL LOW-COST SHORTS FACTORY (Zirvenin Kanunu).

One run = one original Short, end to end:
topic queue -> LLM script -> free edge-tts narration -> Pexels stock / Flux images
-> kinetic subtitles + sound design -> 1080x1920 60 FPS render -> YouTube upload.

No film footage, no film characters. Typical paid spend is a few Flux images
(~$0.003 each) plus one short LLM call; rendering runs on free GitHub Actions.

The channel itself is the queue state: every upload carries "Seri: <topic id>"
in its description, and the next run picks the first topic not seen there.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.original_script import write_script
from app.services.original_visuals import build_visual_track

OUTPUT_DIR = BASE_DIR / "output"
TOPICS_FILE = BASE_DIR / "app" / "services" / "original_topics_tr.json"
MUSIC_DIR = BASE_DIR / "assets" / "music"
SFX_DIR = BASE_DIR / "assets" / "sfx"

VOICE = {"name": "tr-TR-AhmetNeural", "rate": "+6%", "pitch": "-2Hz"}
MARKER_PREFIX = "Seri: "
MARKER_RE = re.compile(r"Seri: ([a-z]{2}-\d{3})")
BASE_TAGS = ["shorts", "psikoloji", "tarih", "güç", "karar verme", "zirvenin kanunu"]
BASE_HASHTAGS = "#shorts #psikoloji #tarih #keşfet"


# ---------------------------------------------------------------------------
# Topic queue
# ---------------------------------------------------------------------------

def load_topics(path: Path = TOPICS_FILE) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def published_topic_ids(youtube, max_pages: int = 10) -> set[str]:
    """Reads 'Seri: <id>' markers from the channel's uploaded video descriptions."""
    channels = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if not channels:
        return set()
    uploads = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    found, token = set(), None
    for _ in range(max_pages):
        page = youtube.playlistItems().list(
            playlistId=uploads, part="snippet", maxResults=50, pageToken=token
        ).execute()
        for item in page.get("items", []):
            found.update(MARKER_RE.findall(item.get("snippet", {}).get("description", "")))
        token = page.get("nextPageToken")
        if not token:
            break
    return found


def pick_topic(topics: list[dict], published: set[str], topic_id: str | None = None) -> dict:
    if topic_id:
        match = next((t for t in topics if t["id"] == topic_id), None)
        if not match:
            raise SystemExit(f"Konu bulunamadı: {topic_id}")
        return match
    for topic in topics:
        if topic["id"] not in published:
            return topic
    raise SystemExit("Konu listesi bitti: app/services/original_topics_tr.json dosyasına yeni konular ekleyin.")


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True,
    ).strip()
    return float(out)


async def _edge_tts(text: str, out: Path):
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE["name"], rate=VOICE["rate"], pitch=VOICE["pitch"])
    await comm.save(str(out))


def synthesize_line(text: str, out: Path, attempts: int = 3) -> Path:
    try:
        from app.services.turkish_phonetics import normalize_turkish_tts_text
        text = normalize_turkish_tts_text(text)
    except Exception:
        pass
    last_error = None
    for attempt in range(attempts):
        try:
            asyncio.run(_edge_tts(text, out))
            if out.exists() and out.stat().st_size > 1000:
                return out
        except Exception as e:
            last_error = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"seslendirme başarısız: {last_error}")


def build_narration(scenes: list[dict], work_dir: Path, synthesize=synthesize_line) -> tuple[Path, list[float], list[dict]]:
    """Returns (voice_track, per_scene_durations, subtitle_events)."""
    from app.services.multi_voice_engine import build_micro_timed_subtitles

    voice_dir = work_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    files, durations, subtitles, timeline = [], [], [], 0.0
    for idx, scene in enumerate(scenes):
        seg = synthesize(scene["text"], voice_dir / f"line_{idx:02d}.mp3")
        dur = _probe_duration(seg)
        subtitles += build_micro_timed_subtitles(scene["text"], timeline, dur, speaker="male_alpha")
        files.append(seg)
        durations.append(dur)
        timeline += dur

    concat_list = voice_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{f.resolve()}'\n" for f in files), encoding="utf-8")
    voice = work_dir / "voice.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:a", "aac", "-b:a", "192k", str(voice)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return voice, durations, subtitles


# ---------------------------------------------------------------------------
# Sound design and render
# ---------------------------------------------------------------------------

def pick_music(seed: str) -> Path | None:
    tracks = sorted(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return None
    return tracks[int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16) % len(tracks)]


def mix_soundtrack(voice: Path, out: Path, duration: float, scene_cuts: list[float], music: Path | None) -> Path:
    """Voice + ducked music + sub hit on the hook + whoosh on scene changes + riser into the ending."""
    inputs = ["-i", str(voice)]
    filters = ["[0:a]highpass=f=70,acompressor=threshold=-16dB:ratio=3:attack=5:release=60,volume=1.1[v]"]
    labels = ["[v]"]

    def add(path: Path, chain: str, label: str, loop: bool = False):
        if loop:
            inputs.extend(["-stream_loop", "-1"])
        inputs.extend(["-i", str(path)])
        n = sum(1 for a in inputs if a == "-i") - 1
        filters.append(f"[{n}:a]{chain}[{label}]")
        labels.append(f"[{label}]")

    if music and music.exists():
        fade = max(0.5, duration - 1.5)
        add(music, f"volume=0.11,afade=t=in:d=0.4,afade=t=out:st={fade:.2f}:d=1.5", "m", loop=True)
    impact, whoosh, riser = SFX_DIR / "impact_sub.mp3", SFX_DIR / "whoosh_synth.mp3", SFX_DIR / "riser_cinematic.mp3"
    if impact.exists():
        add(impact, "volume=0.6", "imp")
    if whoosh.exists():
        for i, t in enumerate(scene_cuts[:10]):
            ms = max(0, int((t - 0.15) * 1000))
            add(whoosh, f"volume=0.3,adelay={ms}|{ms}", f"w{i}")
    if riser.exists() and duration > 8:
        ms = int((duration - 2.5) * 1000)
        add(riser, f"volume=0.28,adelay={ms}|{ms}", "ris")

    mix = f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,loudnorm=I=-14:TP=-1.0:LRA=9[aout]"
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters + [mix]),
        "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}", str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def render_final(segments: list[Path], audio: Path, subtitles_file: Path, duration: float, work_dir: Path, final_mp4: Path) -> Path:
    from app.services.visual_fx_engine import generate_neon_progress_bar_filter

    concat_list = work_dir / "segments.txt"
    concat_list.write_text("".join(f"file '{s.resolve()}'\n" for s in segments), encoding="utf-8")
    raw = work_dir / "visual_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(raw)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    vf = f"subtitles={subtitles_file.name},{generate_neon_progress_bar_filter(duration)}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw.resolve()), "-i", str(audio.resolve()), "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "60", "-fps_mode", "cfr",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{duration:.3f}", "-movflags", "+faststart",
            str(final_mp4.resolve()),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(subtitles_file.parent),
    )
    return final_mp4


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------

def produce(topic: dict, script: dict, provider: str, synthesize=synthesize_line) -> dict:
    from app.services.subtitle_engine import generate_shorts_ass_subtitles

    stamp = int(time.time())
    work_dir = OUTPUT_DIR / f"work_original_{topic['id']}_{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)
    scenes = script["scenes"]

    print(f"[1/4] 🎙️ Seslendirme: {len(scenes)} sahne")
    voice, durations, subtitle_events = build_narration(scenes, work_dir, synthesize=synthesize)
    duration = sum(durations)
    scene_cuts, t = [], 0.0
    for d in durations[:-1]:
        t += d
        scene_cuts.append(round(t, 3))

    print(f"[2/4] 🎞️ Görseller ({duration:.1f}s)")
    visuals = build_visual_track(scenes, durations, work_dir)

    print("[3/4] 🎵 Ses tasarımı ve altyazı")
    subs = work_dir / "subtitles.ass"
    generate_shorts_ass_subtitles(
        sentences=subtitle_events, output_path=subs, font_name="Arial Black", font_size=74,
        highlight_color="&H0000D7FF", outline_width=8, margin_v=860,
    )
    audio = mix_soundtrack(voice, work_dir / "soundtrack.m4a", duration, scene_cuts, pick_music(topic["id"]))

    print("[4/4] 🚀 Render")
    final_mp4 = OUTPUT_DIR / f"ORIGINAL_{topic['id']}_{stamp}.mp4"
    render_final(visuals["segments"], audio, subs, duration, work_dir, final_mp4)

    thumb = final_mp4.with_name(f"{final_mp4.stem}_thumb.jpg")
    if visuals["thumbnail"]:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(visuals["thumbnail"]), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", "-q:v", "2", str(thumb)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.8", "-i", str(final_mp4), "-vframes", "1", "-q:v", "2", str(thumb)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    title = script["title"]
    full_title = f"{title} #shorts" if len(title) <= 92 else title[:100]
    description = "\n\n".join(part for part in [
        script["description"],
        f"💬 {script['pinned_comment']}" if script["pinned_comment"] else "",
        BASE_HASHTAGS,
        f"{MARKER_PREFIX}{topic['id']}",
    ] if part)
    tags = list(dict.fromkeys(script["tags"] + BASE_TAGS))[:30]
    meta = {
        "topic_id": topic["id"],
        "topic": topic["topic"],
        "script_provider": provider,
        "title": full_title,
        "description": description,
        "tags": tags,
        "pinned_comment": script["pinned_comment"],
        "en_title": script["en_title"],
        "en_description": script["en_description"],
        "scenes": scenes,
        "duration_seconds": round(duration, 2),
        "ai_images": visuals["ai_images"],
        "stock_videos": visuals["stock_videos"],
        "contains_synthetic_media": visuals["ai_images"] > 0,
    }
    meta_path = final_mp4.with_name(f"{final_mp4.stem}_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from app.services.video_quality_analyst import audit_short_quality, print_audit_report
        print_audit_report(audit_short_quality(final_mp4))
    except Exception as e:
        print(f"ℹ️ Kalite raporu notu: {e}")

    print(f"✅ Hazır: {final_mp4.name} | {duration:.1f}s | AI görsel: {visuals['ai_images']} | stok video: {visuals['stock_videos']}")
    return {"video": final_mp4, "thumbnail": thumb, "meta": meta}


def upload(youtube, result: dict, privacy: str) -> str:
    from googleapiclient.http import MediaFileUpload

    meta = result["meta"]
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"][:5000],
            "tags": meta["tags"],
            "categoryId": "27",
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": bool(meta["contains_synthetic_media"]),
        },
    }
    parts = "snippet,status"
    if meta.get("en_title"):
        body["localizations"] = {"en": {
            "title": f"{meta['en_title']} #shorts"[:100],
            "description": f"{meta.get('en_description', '')}\n\n{MARKER_PREFIX}{meta['topic_id']}"[:5000],
        }}
        parts += ",localizations"

    media = MediaFileUpload(str(result["video"]), chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part=parts, body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"   Yükleniyor: %{int(status.progress() * 100)}")
    video_id = response["id"]
    print(f"🎉 Yüklendi ({privacy}): https://youtube.com/shorts/{video_id}")

    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(result["thumbnail"]), mimetype="image/jpeg")).execute()
    except Exception as e:
        print(f"ℹ️ Kapak görseli notu: {e}")
    if meta.get("pinned_comment"):
        try:
            youtube.commentThreads().insert(part="snippet", body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": meta["pinned_comment"]}},
            }}).execute()
        except Exception as e:
            print(f"ℹ️ Yorum notu: {e}")
    return video_id


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Original low-cost Shorts factory")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--topic-id", default=None, help="Belirli bir konu (ör. zk-003)")
    parser.add_argument("--no-upload", action="store_true", help="Sadece üret, YouTube'a yükleme")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    youtube = None
    published: set[str] = set()
    if not args.no_upload:
        from publish_to_youtube import get_authenticated_service
        youtube = get_authenticated_service()
        published = published_topic_ids(youtube)
        print(f"📋 Kanalda yayınlanmış konu sayısı: {len(published)}")

    topic = pick_topic(load_topics(), published, args.topic_id)
    print(f"🎯 Konu {topic['id']}: {topic['topic']}")

    script, provider = write_script(topic["topic"])
    print(f"🧠 Senaryo ({provider}): {script['title']}")

    result = produce(topic, script, provider)
    if youtube is not None:
        upload(youtube, result, args.privacy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
