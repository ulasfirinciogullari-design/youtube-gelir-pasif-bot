"""
COMEDY DUB SHORTS FACTORY (Zirvenin Kanunu).

One run = one Short: an unused free Pixabay animal clip -> Gemini watches it and
writes a timed, funny Turkish inner voice -> ElevenLabs (edge-tts fallback)
voices per character -> blurred-background 9:16 frame with subtitles and a
hook line -> YouTube upload.

When no usable clip or script comes out (no Gemini key, API down, every clip
rejected), the run falls back to the original story format instead of
skipping a slot.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import original_factory as story
from app.services.channel_memory import record_upload, used_topic_ids
from app.services.channel_metrics import report_channel
from app.services.seo_pack import build_seo_pack
from app.services.comedy_dub import (
    ClipUnusable, ScriptError, candidate_clips, download_clip, place_lines, write_comedy_script,
)

OUTPUT_DIR = story.OUTPUT_DIR
MAX_CLIP_ATTEMPTS = 4
MAX_TOTAL_SECONDS = 59.0
BASE_TAGS = ["shorts", "komik", "hayvanlar", "dublaj", "mizah", "komik hayvanlar", "zirvenin kanunu"]
BASE_HASHTAGS = "#shorts #komik #hayvanlar #keşfet"
# (ElevenLabs voice id, edge-tts fallback) per character, in order of first appearance.
SPEAKER_VOICES = [
    ("HV5rAif1q1ITHdvxqyMw", {"name": "tr-TR-AhmetNeural", "rate": "+8%", "pitch": "-4Hz"}),    # Alp: deadpan baritone
    ("G5sdncpgy3DkDmvKJnFb", {"name": "tr-TR-EmelNeural", "rate": "+10%", "pitch": "+6Hz"}),    # Taner
    ("Mcb3pz5eWjKXcBhMLF4v", {"name": "tr-TR-AhmetNeural", "rate": "+14%", "pitch": "+10Hz"}),  # Ozgur
]


def _has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


def voice_lines(lines: list[dict], work_dir: Path, synthesize=story.synthesize_line) -> tuple[list[Path], list[float], dict]:
    voice_dir = work_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    speakers: dict[str, int] = {}
    files, durations = [], []
    for idx, line in enumerate(lines):
        slot = speakers.setdefault(line["speaker"], len(speakers) % len(SPEAKER_VOICES))
        voice_id, edge_voice = SPEAKER_VOICES[slot]
        out = synthesize(
            line["text"], voice_dir / f"line_{idx:02d}.mp3",
            previous_text=lines[idx - 1]["text"] if idx else "",
            next_text=lines[idx + 1]["text"] if idx + 1 < len(lines) else "",
            voice_id=voice_id, edge_voice=edge_voice,
        )
        files.append(out)
        durations.append(story._probe_duration(out))
    return files, durations, speakers


def mix_dub(clip: Path, line_files: list[Path], placed: list[dict], total: float, out: Path) -> Path:
    inputs, filters, labels = [], [], []
    if _has_audio(clip):
        inputs += ["-stream_loop", "-1", "-i", str(clip)]
        filters.append("[0:a]volume=0.28[clip]")
        labels.append("[clip]")
    for path, line in zip(line_files, placed):
        n = sum(1 for a in inputs if a == "-i")
        inputs += ["-i", str(path)]
        ms = int(line["start"] * 1000)
        filters.append(f"[{n}:a]highpass=f=70,volume=1.15,adelay={ms}|{ms}[l{n}]")
        labels.append(f"[l{n}]")
    pop = story.SFX_DIR / "impact_sub.mp3"
    if pop.exists():
        n = sum(1 for a in inputs if a == "-i")
        inputs += ["-i", str(pop)]
        filters.append(f"[{n}:a]volume=0.45[pop]")
        labels.append("[pop]")
    mix = (
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
        f"apad,atrim=0:{total:.3f},loudnorm=I=-14:TP=-1.0:LRA=9[aout]"
    )
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters + [mix]), "-map", "[aout]",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{total:.3f}", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return out


def render_comedy(clip: Path, audio: Path, subtitles_file: Path, total: float, clip_seconds: float, final_mp4: Path) -> Path:
    from app.services.visual_fx_engine import generate_neon_progress_bar_filter

    loop = ["-stream_loop", "-1"] if total > clip_seconds + 0.05 else []
    graph = (
        "[0:v]fps=60,split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:4,eq=brightness=-0.08[bg];"
        "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,subtitles={subtitles_file.name},"
        f"{generate_neon_progress_bar_filter(total)}[v]"
    )
    subprocess.run(
        ["ffmpeg", "-y", *loop, "-i", str(clip.resolve()), "-i", str(audio.resolve()),
         "-filter_complex", graph, "-map", "[v]", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "60", "-fps_mode", "cfr",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{total:.3f}", "-movflags", "+faststart",
         str(final_mp4.resolve())],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(subtitles_file.parent),
    )
    return final_mp4


def produce_comedy(hit: dict, query: str, script: dict, clip: Path, synthesize=story.synthesize_line) -> dict:
    from app.services.subtitle_engine import generate_shorts_ass_subtitles

    topic_id = f"kd-{hit['id']}"
    stamp = int(time.time())
    work_dir = OUTPUT_DIR / f"work_comedy_{topic_id}_{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_seconds = story._probe_duration(clip)

    print(f"[1/4] 🎙️ Dublaj: {len(script['lines'])} replik")
    files, durations, speakers = voice_lines(script["lines"], work_dir, synthesize=synthesize)
    placed = place_lines(script["lines"], durations)
    total = min(MAX_TOTAL_SECONDS, max(clip_seconds, placed[-1]["end"] + 0.7))
    engines = ", ".join(f"{name} x{n}" for name, n in story.voice_usage.items() if n) or "test"
    print(f"       Ses motoru: {engines} | konuşanlar: {', '.join(speakers)}")

    print("[2/4] ✍️ Altyazı ve kanca")
    subs = work_dir / "subtitles.ass"
    events = [
        {"text": p["text"], "start": p["start"], "end": p["end"],
         "speaker": "female" if speakers[p["speaker"]] == 1 else "male"}
        for p in placed
    ]
    generate_shorts_ass_subtitles(
        sentences=events, output_path=subs, font_name="Arial Black", font_size=70,
        highlight_color="&H0000D7FF", outline_width=8, margin_v=470,
    )
    story.add_hook_overlay(subs, script["hook_text"])

    print("[3/4] 🎵 Ses miksajı")
    audio = mix_dub(clip, files, placed, total, work_dir / "soundtrack.m4a")

    print(f"[4/4] 🚀 Render ({total:.1f}s)")
    final_mp4 = OUTPUT_DIR / f"COMEDY_{topic_id}_{stamp}.mp4"
    render_comedy(clip, audio, subs, total, clip_seconds, final_mp4)
    thumb = final_mp4.with_name(f"{final_mp4.stem}_thumb.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(final_mp4), "-vframes", "1", "-q:v", "2", str(thumb)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    title = script["title"]
    # Pixabay's license needs no credit line, and Ulaş found it cheapened the description.
    seo = build_seo_pack(
        title, f"Komik hayvan dublajı ({query})", " ".join(p["text"] for p in placed), script,
        BASE_TAGS, BASE_HASHTAGS, f"{story.MARKER_PREFIX}{topic_id}",
    )
    meta = {
        "topic_id": topic_id,
        "format": "comedy",
        "pixabay_id": hit["id"],
        "pixabay_page": hit.get("pageURL", ""),
        "query": query,
        "title": f"{title} #shorts" if len(title) <= 92 else title[:100],
        "hook_text": script["hook_text"],
        "description": seo["description"],
        "tags": seo["tags"],
        "localizations": seo["localizations"],
        "seo_engine": seo["seo_engine"],
        "category_id": "23",
        "pinned_comment": script["pinned_comment"],
        "lines": placed,
        "voice_engine": engines,
        "duration_seconds": round(total, 2),
        "contains_synthetic_media": False,
    }
    final_mp4.with_name(f"{final_mp4.stem}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        from app.services.video_quality_analyst import audit_short_quality, print_audit_report
        print_audit_report(audit_short_quality(final_mp4))
    except Exception as e:
        print(f"ℹ️ Kalite raporu notu: {e}")
    print(f"✅ Hazır: {final_mp4.name} | {total:.1f}s | klip: Pixabay {hit['id']} ({query})")
    return {"video": final_mp4, "thumbnail": thumb, "meta": meta}


def find_clip_and_script(published: set[str], work_dir: Path, seed: str):
    """Returns (hit, query, script, clip) or None when the comedy format cannot run."""
    for attempt, (hit, query) in enumerate(candidate_clips(published, seed)):
        if attempt >= MAX_CLIP_ATTEMPTS:
            break
        print(f"🎯 Klip adayı: Pixabay {hit.get('id')} ({query}, {hit.get('duration')} sn)")
        try:
            preview, clip = download_clip(hit, work_dir)
            script = write_comedy_script(preview, story._probe_duration(clip))
            return hit, query, script, clip
        except ClipUnusable as e:
            print(f"ℹ️ Klip atlandı: {e}")
        except (urllib.error.URLError, TimeoutError, subprocess.CalledProcessError) as e:
            print(f"ℹ️ Klip indirilemedi: {e}")
        except ScriptError as e:
            print(f"ℹ️ Dublaj senaryosu çıkmadı: {e}")
            return None
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Comedy dub Shorts factory")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    parser.add_argument("--no-upload", action="store_true", help="Sadece üret, YouTube'a yükleme")
    args = parser.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    youtube, published = None, set()
    if not args.no_upload:
        from publish_to_youtube import get_authenticated_service
        youtube = get_authenticated_service()
        published, _ = report_channel(youtube, story.MARKER_RE, OUTPUT_DIR)
        published |= used_topic_ids(story.DATA_DIR)

    seed = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    found = find_clip_and_script(published, OUTPUT_DIR / f"clips_{seed}", seed)
    if not found:
        print("↩️ Komik dublaj bu sefer çıkmadı, özgün hikâye formatına geçiliyor.")
        story_args = ["--privacy", args.privacy] + (["--no-upload"] if args.no_upload else [])
        return story.main(story_args)

    hit, query, script, clip = found
    print(f"🧠 Dublaj senaryosu: {script['title']}")
    result = produce_comedy(hit, query, script, clip)
    if youtube is not None:
        video_id = story.upload(youtube, result, args.privacy)
        record_upload(story.DATA_DIR, video_id, result["meta"]["topic_id"], result["meta"]["title"], "comedy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
