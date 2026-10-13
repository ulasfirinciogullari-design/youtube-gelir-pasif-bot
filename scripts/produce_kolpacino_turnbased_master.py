"""
Produce Turn-Based Master Short: Kolpaçino - Hedef Ben Miyim Tayfun?
Implements the absolute rule:
1. ÖNCE KLİP KONUŞUR (0.0s -> 14.3s): Dış ses %100 sessiz, film diyaloğu en önde ve net!
2. SONRA YORUM SESLENDİRİLİR (14.3s -> 24.12s): Klip diyaloğu biter, Alp dış sesi kahkaha ve yorum analiziyle girer.
3. İKİ SES BİRBİRİNE ASLA KARIŞMAZ (Sıfır çakışma, kusursuz denge).
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.turn_based_reaction_engine import TurnBasedReactionEngine, DialogueTurnConfig

OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

# Inputs
REAL_VIDEO = OUTPUT_DIR / "kolpacino_1080p_hedef.mp4"
COMMENTARY_VOICE = OUTPUT_DIR / "voice_alp_turnbased_outro.mp3"
MASTER_AUDIO = OUTPUT_DIR / "cinema_turnbased_audio_master.mp3"

HEADER_IMG = OUTPUT_DIR / "kolpacino_header_playwright.png"
FOOTER_IMG = OUTPUT_DIR / "kolpacino_footer_playwright.png"

C1_IMG = OUTPUT_DIR / "real_social_c1.png"
C2_IMG = OUTPUT_DIR / "real_social_c2.png"
C3_IMG = OUTPUT_DIR / "real_social_c3.png"
C4_IMG = OUTPUT_DIR / "real_social_c4.png"
C5_IMG = OUTPUT_DIR / "real_social_c5.png"

SUBTITLE_PATH = OUTPUT_DIR / "kolpacino_subtitles_turnbased.ass"
OUTPUT_VIDEO = OUTPUT_DIR / "REAL_VIRAL_KOLPACINO_TURNBASED_MASTER.mp4"


def probe_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def main():
    print("🎬 ========================================================")
    print("🎬 SIRALI KONUŞMA (TURN-BASED) MASTER ÜRETİMİ BAŞLADI")
    print("🎬 ========================================================")

    # 1. Build Turn-Based Master Audio
    cfg = DialogueTurnConfig(
        video_path=REAL_VIDEO,
        commentary_voice_path=COMMENTARY_VOICE,
        output_audio_master=MASTER_AUDIO,
        clip_dialogue_end_sec=14.3,
        commentary_start_sec=14.3
    )
    engine = TurnBasedReactionEngine(cfg)
    master_audio_path = engine.build_turn_based_audio_master()

    # 2. Assemble Master Vertical Video
    total_dur = probe_duration(REAL_VIDEO)
    ass_escaped = str(SUBTITLE_PATH.resolve()).replace("\\", "/").replace(":", "\\:")

    # Video Filter Graph:
    # 0:v = REAL_VIDEO
    # 1:v = HEADER_IMG
    # 2:v = FOOTER_IMG
    # 3:v = C1_IMG (Cat avatar)
    # 4:v = C2_IMG (TikTok)
    # 5:v = C3_IMG (X tweet)
    # 6:v = C4_IMG (Dog avatar)
    # 7:v = C5_IMG (Car avatar)
    # 8:a = MASTER_AUDIO

    video_filter = (
        # 1. Ambient Background blur
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,drawbox=0:0:1080:1920:color=black@0.45:t=fill,fps=60[v_bg];"
        # 2. Sharp center video (1080x608)
        "[0:v]scale=1080:608:force_original_aspect_ratio=decrease,fps=60[v_center];"
        # 3. Golden border lines
        "[v_bg]drawbox=0:374:1080:6:color=0xFFD700@0.9:t=fill,drawbox=0:988:1080:6:color=0xFFD700@0.9:t=fill[v_bg_bordered];"
        # 4. Overlay center video onto background at y=380
        "[v_bg_bordered][v_center]overlay=0:380[v_comp];"
        # 5. Header scale & overlay at y=40
        "[1:v]scale=1000:-1[v_head_scaled];"
        "[v_comp][v_head_scaled]overlay=40:40[v_head];"
        # 6. Footer scale & overlay at y=1640
        "[2:v]scale=1000:-1[v_foot_scaled];"
        "[v_head][v_foot_scaled]overlay=40:1640[v_foot];"
        # 7. Scale each comment card to 960px width
        "[3:v]scale=960:-1[c1];"
        "[4:v]scale=960:-1[c2];"
        "[5:v]scale=960:-1[c3];"
        "[6:v]scale=960:-1[c4];"
        "[7:v]scale=960:-1[c5];"
        # 8. Timed comment reveals across the video:
        "[v_foot][c1]overlay=60:1030:enable='between(t,3.5,7.5)'[v_c1];"
        "[v_c1][c2]overlay=60:1030:enable='between(t,7.5,11.0)'[v_c2];"
        "[v_c2][c3]overlay=60:1030:enable='between(t,11.0,14.3)'[v_c3];"
        "[v_c3][c4]overlay=60:1030:enable='between(t,14.3,19.0)'[v_c4];"
        f"[v_c4][c5]overlay=60:1030:enable='between(t,19.0,{total_dur:.2f})'[v_c5];"
        # 9. Burn synchronized Turn-Based ASS Subtitles
        f"[v_c5]subtitles='{ass_escaped}'[v_master]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(REAL_VIDEO),
        "-i", str(HEADER_IMG),
        "-i", str(FOOTER_IMG),
        "-i", str(C1_IMG),
        "-i", str(C2_IMG),
        "-i", str(C3_IMG),
        "-i", str(C4_IMG),
        "-i", str(C5_IMG),
        "-i", str(master_audio_path),
        "-filter_complex", video_filter,
        "-map", "[v_master]",
        "-map", "8:a",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        str(OUTPUT_VIDEO)
    ]

    print("🚀 Video FFmpeg ile render ediliyor...")
    subprocess.run(cmd, check=True)

    print(f"\n🎉 SIRALI DİYALOG MASTER VİDEOSU TAMAMLANDI!")
    print(f"📁 Dosya: {OUTPUT_VIDEO} ({OUTPUT_VIDEO.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    main()
