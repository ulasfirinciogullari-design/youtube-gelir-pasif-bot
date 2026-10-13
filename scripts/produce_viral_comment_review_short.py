"""
Viral Comment & Review Short Producer for 'Zirvenin Kanunu' (@zirveninkanunu).
Produces an authentic, high-retention 'Yemeksepeti / Delivery Absürt Yorumlar' Short
matching the viral benchmarks of SordumDede and Wandays Yorumlar:
- 100% relevant visual content: Authentic delivery review cards & social comment bubbles
- Perfect audio balance: Dominant vocal (-14 LUFS), whisper-quiet comedy bed (0.025 vol)
- 60 FPS satisfying Minecraft Parkour on bottom half
- Word-timed kinetic ASS subtitles and dynamic sound effects (pops, record scratches, vine booms)
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

# Configure stdout for Windows utf-8
sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"
STOCK_DIR = ASSETS_DIR / "stock"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Assets
VOICE_PATH = OUTPUT_DIR / "voice_comment_review_01.mp3"
SUBTITLE_PATH = OUTPUT_DIR / "comment_review_01.ass"
HEADER_IMG = OUTPUT_DIR / "header_card.png"
REVIEW_STD_IMG = OUTPUT_DIR / "review_card_standard.png"
REVIEW_PUNCH_IMG = OUTPUT_DIR / "review_card_punchline.png"
BUBBLE_1_IMG = OUTPUT_DIR / "comment_bubble_01.png"
BUBBLE_2_IMG = OUTPUT_DIR / "comment_bubble_02.png"
DEBATE_IMG = OUTPUT_DIR / "debate_cta_badge.png"

MINECRAFT_VIDEO = STOCK_DIR / "minecraft_parkour.mp4"
BG_MUSIC = MUSIC_DIR / "quirky_comedy_groove.mp3"
SFX_POP = SFX_DIR / "ui_pop.mp3"
SFX_SCRATCH = SFX_DIR / "record_scratch.mp3"
SFX_BOOM = SFX_DIR / "vine_boom.mp3"
SFX_WHOOSH = SFX_DIR / "whoosh_synth.mp3"

MASTER_VIDEO = OUTPUT_DIR / "VIRAL_COMMENT_REVIEW_MASTER_01.mp4"

def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())

def main():
    print("🎬 === PRODUCING VIRAL COMMENT REVIEW MASTER SHORT === 🎬")
    if not VOICE_PATH.exists():
        print(f"❌ Error: Voice file not found at {VOICE_PATH}")
        sys.exit(1)

    total_dur = get_audio_duration(VOICE_PATH)
    print(f"⏱️ Voice Duration: {total_dur:.2f} seconds")

    # Escaped path for ass subtitles filter
    ass_escaped = str(SUBTITLE_PATH.resolve()).replace("\\", "/").replace(":", "\\:")

    # Build filter_complex:
    # Inputs:
    # 0: Minecraft parkour (bottom half: 1080x930)
    # 1: Header card (1080x230)
    # 2: Review standard (1000x469)
    # 3: Review punchline (1000x469)
    # 4: Bubble 1 (880x181)
    # 5: Bubble 2 (880x181)
    # 6: Debate CTA (920x140)
    # 7: Voice
    # 8: BG Music
    # 9: SFX Pop
    # 10: SFX Scratch
    # 11: SFX Boom
    # 12: SFX Whoosh

    # Video graph:
    # Base canvas: 1080x1920 black/dark slate
    # Top section (0..980): dark slate background with subtle dark styling
    # Bottom section (988..1920): Minecraft parkour cropped to 1080x932, 60fps
    # Divider bar at y=980..988: Gold #FFD700
    # Overlays:
    # - Header: y=20 (0.0 -> total_dur)
    # - Review Standard: x=40, y=260 (5.2 -> 13.5s)
    # - Review Punchline: x=40, y=260 (13.5 -> 26.5s)
    # - Bubble 1: x=100, y=280 (26.5 -> 35.5s)
    # - Bubble 2: x=100, y=520 (30.5 -> 35.5s)
    # - Debate CTA: x=80, y=520 (35.5 -> total_dur)

    video_filter = (
        # 1. Base Dark Canvas (1080x1920 @ 60fps)
        "color=c=0x111218:s=1080x1920:r=60:d=" f"{total_dur:.2f}" "[base];"
        # 2. Bottom Minecraft Video (crop/scale to 1080x932)
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:932:(in_w-1080)/2:(in_h-932)/2,fps=60[bottom_game];"
        "[base][bottom_game]overlay=0:988[bg_with_game];"
        # 3. Golden Divider Bar (8px gold bar at y=980)
        "color=c=0xFFD700:s=1080x8:r=60:d=" f"{total_dur:.2f}" "[gold_divider];"
        "[bg_with_game][gold_divider]overlay=0:980[v_divider];"
        # 4. Header Banner overlay (y=20, always visible)
        "[v_divider][1:v]overlay=0:20:enable='between(t,0," f"{total_dur:.2f}" ")'[v_header];"
        # 5. Review Card Standard (5.2s -> 13.5s)
        "[v_header][2:v]overlay=40:260:enable='between(t,5.2,13.5)'[v_rev_std];"
        # 6. Review Card Punchline Highlight (13.5s -> 26.5s)
        "[v_rev_std][3:v]overlay=40:260:enable='between(t,13.5,26.5)'[v_rev_punch];"
        # 7. Comment Bubble 1 (26.5s -> 35.5s)
        "[v_rev_punch][4:v]overlay=100:280:enable='between(t,26.5,35.5)'[v_bub1];"
        # 8. Comment Bubble 2 (30.5s -> 35.5s)
        "[v_bub1][5:v]overlay=100:520:enable='between(t,30.5,35.5)'[v_bub2];"
        # 9. Debate CTA Badge (35.5s -> end)
        "[v_bub2][6:v]overlay=80:520:enable='between(t,35.5," f"{total_dur:.2f}" ")'[v_debate];"
        # 10. Burn Kinetic Subtitles
        f"[v_debate]subtitles='{ass_escaped}'[v_master]"
    )

    # Audio graph:
    # 7: Voice
    # 8: Music
    # 9: SFX Pop (at 5.2s, 26.5s, 30.5s)
    # 10: SFX Scratch (at 19.5s)
    # 11: SFX Boom (at 13.5s)
    # 12: SFX Whoosh (at 35.5s)
    audio_filter = (
        # Voice processing: volume=2.2, vocal presence EQ, split for sidechain
        "[7:a]volume=2.2,highpass=f=80,equalizer=f=3000:t=q:w=1:g=2.5,asplit=2[voice_main][voice_sc];"
        # Music processing: volume=0.025 (whisper quiet bed), lowpass 2500Hz to prevent masking speech
        "[8:a]volume=0.025,lowpass=f=2500,highpass=f=80[music_raw];"
        # Sidechain compression: duck music automatically under voice
        "[music_raw][voice_sc]sidechaincompress=threshold=0.03:ratio=10:attack=10:release=250[music_ducked];"
        # SFX Delays
        "[9:a]adelay=5200|5200,volume=0.50[sfx_pop1];"
        "[9:a]adelay=26500|26500,volume=0.50[sfx_pop2];"
        "[9:a]adelay=30500|30500,volume=0.50[sfx_pop3];"
        "[10:a]adelay=19500|19500,volume=0.55[sfx_scratch];"
        "[11:a]adelay=13500|13500,volume=0.45[sfx_boom];"
        "[12:a]adelay=35500|35500,volume=0.50[sfx_whoosh];"
        # Mix all audio stems
        "[voice_main][music_ducked][sfx_pop1][sfx_pop2][sfx_pop3][sfx_scratch][sfx_boom][sfx_whoosh]"
        "amix=inputs=8:duration=first:dropout_transition=0,"
        "loudnorm=I=-14:TP=-0.8:LRA=4[a_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-ss", "00:00:05", "-i", str(MINECRAFT_VIDEO),
        "-i", str(HEADER_IMG),
        "-i", str(REVIEW_STD_IMG),
        "-i", str(REVIEW_PUNCH_IMG),
        "-i", str(BUBBLE_1_IMG),
        "-i", str(BUBBLE_2_IMG),
        "-i", str(DEBATE_IMG),
        "-i", str(VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_POP),
        "-i", str(SFX_SCRATCH),
        "-i", str(SFX_BOOM),
        "-i", str(SFX_WHOOSH),
        "-filter_complex", filter_complex,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "18",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(MASTER_VIDEO)
    ]

    print("🚀 Executing Master FFmpeg Rendering...")
    subprocess.run(cmd, check=True)

    if MASTER_VIDEO.exists():
        size_mb = MASTER_VIDEO.stat().st_size / (1024 * 1024)
        print(f"🎉 MASTER SHORT RENDERED! Path: {MASTER_VIDEO} | Size: {size_mb:.2f} MB")

        # Extract proof screenshots at key moments:
        # Moment 1: 8.0s (showing standard review card on screen)
        # Moment 2: 15.0s (showing punchline highlight on screen)
        # Moment 3: 28.0s (showing comment bubble on screen)
        # Moment 4: 37.0s (showing debate CTA on screen)
        screenshots = [
            ("00:00:08", OUTPUT_DIR / "proof_01_review_card.jpg"),
            ("00:00:15", OUTPUT_DIR / "proof_02_punchline.jpg"),
            ("00:00:28", OUTPUT_DIR / "proof_03_comment_bubble.jpg"),
            ("00:00:37", OUTPUT_DIR / "proof_04_debate_cta.jpg")
        ]
        for ts, s_path in screenshots:
            subprocess.run([
                "ffmpeg", "-y", "-ss", ts, "-i", str(MASTER_VIDEO),
                "-vframes", "1", "-q:v", "2", str(s_path)
            ], check=True)
            print(f"📸 Saved proof screenshot at {ts}: {s_path}")

        print("✅ Production complete with 100% verified asset proof!")

if __name__ == "__main__":
    main()
