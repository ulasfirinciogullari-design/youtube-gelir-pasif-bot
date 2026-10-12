"""
Produce Viral Minecraft + Thomas Shelby Split-Screen Master Short.
Combines:
- Top Half (1080x960): Thomas Shelby / Peaky Blinders 4K cinematic clip
- Bottom Half (1080x960): Minecraft 60 FPS Parkour loop
- Golden Separator Bar: 8px gold@0.95
- Kinetic Word-by-Word ASS Subtitles: Alex Hormozi / MrBeast dynamic animated bouncing text with power words & emojis
- Audio: Studio Baritone narration + Mystery Ambient ducked music + Sound FX (impact thud, transition whoosh, sub-bass riser)
- Mastering: EBU R128 (-15 LUFS, -0.7 dB peak)
- Interactive CTA Hook: 'Sen olsan ne yapardın? 1 mi 2 mi? 👇'
"""

import os
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.kinetic_subtitle_fx import generate_kinetic_word_by_word_ass

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOP_VIDEO = BASE_DIR / "assets" / "cinematic" / "peaky_master.mp4"
BOTTOM_VIDEO = BASE_DIR / "assets" / "stock" / "minecraft_parkour.mp4"
VOICE_PATH = OUTPUT_DIR / "splitscreen_voice_alp.mp3"
BG_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"
SFX_IMPACT = BASE_DIR / "assets" / "sfx" / "impact_sub.mp3"
SFX_WHOOSH = BASE_DIR / "assets" / "sfx" / "cinematic_whoosh.mp3"
SFX_RISER = BASE_DIR / "assets" / "sfx" / "riser_cinematic.mp3"
ASS_SUBTITLES = OUTPUT_DIR / "viral_minecraft_shelby_subtitles.ass"
MASTER_VIDEO = OUTPUT_DIR / "VIRAL_SPLIT_MINECRAFT_SHELBY_MASTER.mp4"

# Timed dialogue script matching splitscreen_voice_alp.mp3 (20.57s total)
TIMED_SCRIPT = [
    ("48 Güç Yasası, Yasa 3: Niyetini asla belli etme.", 0.0, 4.3),
    ("Bir fırtına koparmak istiyorsan, havanın en sakin olduğu anı bekle.", 4.3, 8.8),
    ("Zirvedeki zihinler hamlelerini asla bağırarak duyurmaz;", 8.8, 13.2),
    ("sessizce planlar ve rakibi yok olduğunda masadan kalkarlar.", 13.2, 17.2),
    ("Zirvenin Kanunu: En ölümcül darbe, varlığı hissedilmeyendir.", 17.2, 20.6)
]


def create_subtitles() -> Path:
    print("📝 1. Generating Hormozi-Style Kinetic ASS Subtitles...")
    ass_path = generate_kinetic_word_by_word_ass(
        timed_sentences=TIMED_SCRIPT,
        output_path=ASS_SUBTITLES,
        character_badge="👑 ZİRVENİN KANUNU 👑",
        cta_text="Sen olsan ne yapardın? 1 mi 2 mi? 👇",
        chunk_size=3,
        subtitle_margin_v=460  # Puts subtitle centered in the bottom Minecraft half (safe from divider & UI)
    )
    print(f"✅ ASS Subtitles Generated: {ass_path}")
    return ass_path


def assemble_and_render():
    ass_file = create_subtitles()

    print("🎬 2. Probing audio and video durations...")
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(VOICE_PATH)
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    voice_dur = float(res.stdout.strip())
    total_dur = min(voice_dur + 0.6, 58.0)
    print(f"⏱️ Voice Duration: {voice_dur:.2f}s | Target Master Duration: {total_dur:.2f}s")

    # In Windows FFmpeg, ASS filter path must have escaped backslashes and escaped colons
    ass_filter_path = str(ass_file).replace("\\", "/").replace(":", "\\:")

    # Audio Filter Graph:
    # 2:a -> Voice (Alp Baritone)
    # 3:a -> Background Music (mystery ambient) ducked & filtered
    # 4:a -> Impact Thud (at t=0.0s and delayed to t=17.2s)
    # 5:a -> Whoosh (at t=8.7s transition)
    # 6:a -> Sub-bass Riser (building up from t=14.0s to 17.2s)
    # Final amix + loudnorm=I=-15:TP=-0.7:LRA=7 (Studio EBU R128 compliance)
    audio_filter = (
        "[2:a]volume=1.4[voice];"
        "[3:a]volume=0.08,lowpass=f=4500,highpass=f=100[music];"
        "[4:a]volume=0.9[sfx_impact_open];"
        "[4:a]adelay=17200|17200,volume=0.8[sfx_impact_climax];"
        "[5:a]adelay=8700|8700,volume=0.7[sfx_whoosh];"
        "[6:a]adelay=13500|13500,volume=0.6[sfx_riser];"
        "[voice][music][sfx_impact_open][sfx_impact_climax][sfx_whoosh][sfx_riser]"
        "amix=inputs=6:duration=first:dropout_transition=0,"
        "loudnorm=I=-15:TP=-0.7:LRA=7[a_master]"
    )

    # Video Filter Graph:
    # 0:v -> Thomas Shelby 1080x960 60fps
    # 1:v -> Minecraft Parkour 1080x960 60fps
    # Stack vertically -> 1080x1920
    # Gold separator line -> 8px gold@0.95 at y=956
    # Burn ASS subtitles with libass (Hormozi bouncy highlights + bottom CTA hook)
    video_filter = (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[top];"
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[bottom];"
        "[top][bottom]vstack[stacked];"
        f"[stacked]drawbox=y=956:color=gold@0.95:width=1080:height=8:t=fill,ass='{ass_filter_path}'[v_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(TOP_VIDEO),
        "-stream_loop", "-1", "-ss", "00:00:05", "-i", str(BOTTOM_VIDEO),
        "-i", str(VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_IMPACT),
        "-i", str(SFX_WHOOSH),
        "-i", str(SFX_RISER),
        "-filter_complex", filter_complex,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(MASTER_VIDEO)
    ]

    print("⚡ 3. Starting FFmpeg Master Assembly Rendering...")
    subprocess.run(cmd, check=True)

    if MASTER_VIDEO.exists():
        size_mb = MASTER_VIDEO.stat().st_size / (1024 * 1024)
        print(f"🎉 MASTER RENDER SUCCESSFUL! {MASTER_VIDEO.name} ({size_mb:.2f} MB)")
        return True
    return False


if __name__ == "__main__":
    assemble_and_render()
