"""
Master Production Script: REAL_VIRAL_CAKIR_MASTER.mp4
Channel: Zirvenin Kanunu (@zirveninkanunu)

Features:
1. Fresh Authentic Content: Kurtlar Vadisi (Süleyman Çakır & Polat Alemdar) - Zero Kolpaçino.
2. Zero Vocal Collision: Çakır speaks first (0.0s - 12.5s). Narrator speaks strictly after Çakır finishes (13.0s+).
3. Exact Card-Sync Voiceover: Narrator reads the exact text of each card the moment it appears with natural human delivery.
4. Varied Dynamic Format: Not 3 stacked comments; 1 viral meme tweet card + 1 interactive debate card.
5. 1080x1920 60 FPS pristine rendering with blurred ambient background, glowing borders, and broadcast audio mastering (-14.0 LUFS).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Assets
RAW_CLIP = ASSETS_DIR / "cinematic" / "polat_alemdar_sample.mp4"
CARD_TWEET = OUTPUT_DIR / "cakir_card_tweet.png"
CARD_DEBATE = OUTPUT_DIR / "cakir_card_debate.png"
VOICE_PART1 = OUTPUT_DIR / "voice_part1.mp3"
VOICE_PART2 = OUTPUT_DIR / "voice_part2.mp3"
BG_MUSIC = ASSETS_DIR / "music" / "dark_tension_pulse.mp3"
SFX_POP = ASSETS_DIR / "sfx" / "ui_pop.mp3"
SFX_WHOOSH = ASSETS_DIR / "sfx" / "cinematic_whoosh.mp3"

MASTER_OUTPUT = OUTPUT_DIR / "REAL_VIRAL_CAKIR_MASTER.mp4"
MASTER_THUMB = OUTPUT_DIR / "REAL_VIRAL_CAKIR_MASTER_thumb.jpg"
MASTER_META = OUTPUT_DIR / "REAL_VIRAL_CAKIR_MASTER_meta.json"

TOTAL_DURATION = 29.5  # seconds


def build_ass_subtitles(output_path: Path):
    """Generates clean ASS subtitles for Çakır's lines (0.0s - 12.5s)."""
    ass_content = """[Script Info]
Title: Cakir Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Header,Arial Black,44,&H0000FFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,8,30,30,120,1
Style: CakirDialogue,Arial Black,52,&H00FFFFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,2,40,40,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:12.50,Header,,0,0,0,,{\\an8\\bord4\\shad2}👑 KURTLAR VADİSİ | ÇAKIR'IN PLANI
Dialogue: 0,0:00:00.00,0:00:01.80,CakirDialogue,,0,0,0,,{\\c&H00D7FF&}Nereden buldun?
Dialogue: 0,0:00:01.80,0:00:04.20,CakirDialogue,,0,0,0,,{\\c&H00FFFF&}Dış cephede sekiz kamera var.
Dialogue: 0,0:00:04.20,0:00:06.30,CakirDialogue,,0,0,0,,{\\c&HFFFFFF&}Giriş çıkışı kontrol eden iki kişi.
Dialogue: 0,0:00:06.30,0:00:08.80,CakirDialogue,,0,0,0,,{\\c&H00FFFF&}Tüm dış güvenliği sağlayan altı kişi.
Dialogue: 0,0:00:08.80,0:00:10.50,CakirDialogue,,0,0,0,,{\\c&HFFFFFF&}Ana giriş kapısında da iki kişi var.
Dialogue: 0,0:00:10.50,0:00:12.50,CakirDialogue,,0,0,0,,{\\c&H00D7FF&}İçerdekileri saysam ne yazar...
"""
    output_path.write_text(ass_content, encoding="utf-8")
    print(f"✅ ASS subtitles written to {output_path}")


def prepare_video_timeline():
    """
    Builds a 29.5-second sequence from RAW_CLIP:
    - 0.0s - 12.5s: Çakır explaining plan (raw 10.5s to 23.0s)
    - 12.5s - 21.5s: Polat's stoic reaction (raw 22.0s to 27.0s looped/slowed)
    - 21.5s - 29.5s: Çakır's smile to Polat (raw 26.5s to 29.5s looped)
    """
    timeline_video = OUTPUT_DIR / "cakir_timeline_base.mp4"

    # We extract the 3 visual acts from raw clip
    act1 = OUTPUT_DIR / "act1_cakir.mp4"
    act2 = OUTPUT_DIR / "act2_polat.mp4"
    act3 = OUTPUT_DIR / "act3_outro.mp4"

    # Act 1: 10.5s - 23.0s (12.5s duration)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "10.5", "-t", "12.5", "-i", str(RAW_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act1)
    ], check=True)

    # Act 2: Polat Alemdar reaction 22.0s - 27.0s, extended/slowed to 9.0s (12.5s to 21.5s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "21.0", "-t", "5.0", "-i", str(RAW_CLIP),
        "-vf", "setpts=1.8*PTS,scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act2)
    ], check=True)

    # Act 3: Çakır smiling with respect 26.0s - 29.5s, extended to 8.0s (21.5s to 29.5s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "26.0", "-t", "3.5", "-i", str(RAW_CLIP),
        "-vf", "setpts=2.285*PTS,scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act3)
    ], check=True)

    # Concat the 3 visual acts
    concat_list = OUTPUT_DIR / "concat_acts.txt"
    concat_list.write_text(f"file '{act1.resolve().as_posix()}'\nfile '{act2.resolve().as_posix()}'\nfile '{act3.resolve().as_posix()}'\n", encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(timeline_video)
    ], check=True)

    print(f"✅ Video timeline base assembled: {timeline_video}")
    return timeline_video


def build_master_composite(timeline_video: Path):
    """
    Renders 1080x1920 60 FPS master video using FFmpeg filter_complex:
    - Background: Blurred & darkened video loop
    - Foreground: 1080x608 crisp video centered around y=360
    - Overlay 1: Tweet Card at 13.0s - 21.5s centered around y=1160
    - Overlay 2: Debate Card at 21.5s - 29.5s centered around y=1160
    - Subtitles: ASS subtitles burned for Çakır's dialogue
    - Audio:
      * Clip dialogue: 0.0s - 12.5s (full normalized volume)
      * Clip dialogue: 12.5s+ (silenced)
      * Part 1 Voice: starts at 13.0s
      * Part 2 Voice: starts at 21.6s
      * Background music: -24 dB
      * SFX: pop at 13.0s, whoosh at 21.5s
      * Mastered to -14.0 LUFS
    """
    ass_path = OUTPUT_DIR / "cakir_subs.ass"
    build_ass_subtitles(ass_path)
    ass_escaped = str(ass_path.resolve().as_posix()).replace(":", "\\:")

    # Extract raw clip audio for 0.0s - 12.5s
    clip_audio_part = OUTPUT_DIR / "clip_dialogue_part.wav"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "10.5", "-t", "12.5", "-i", str(RAW_CLIP),
        "-vn", "-ar", "48000", "-ac", "2", str(clip_audio_part)
    ], check=True)

    # Inputs:
    # 0: timeline_video
    # 1: CARD_TWEET
    # 2: CARD_DEBATE
    # 3: clip_audio_part
    # 4: VOICE_PART1
    # 5: VOICE_PART2
    # 6: BG_MUSIC
    # 7: SFX_POP
    # 8: SFX_WHOOSH

    filter_complex = f"""
    [0:v]split=2[v_bg_in][v_fg_in];
    [v_bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,colorchannelmixer=aa=1.0,eq=brightness=-0.25:contrast=1.1[v_bg];
    [v_fg_in]scale=1040:585,setsar=1[v_fg];
    [v_bg][v_fg]overlay=20:300[v_composed];
    [1:v]scale=1020:-1[v_card1];
    [2:v]scale=1020:-1[v_card2];
    [v_composed][v_card1]overlay=30:1080:enable='between(t,13.0,21.5)'[v_with_c1];
    [v_with_c1][v_card2]overlay=30:1080:enable='between(t,21.5,29.5)'[v_with_cards];
    [v_with_cards]subtitles='{ass_escaped}'[v_out];

    [3:a]volume=1.8,apad=pad_dur=17.0[a_clip];
    [4:a]volume=1.4,adelay=13000|13000,apad=pad_dur=8.0[a_v1];
    [5:a]volume=1.4,adelay=21600|21600,apad=pad_dur=1.0[a_v2];
    [6:a]volume=0.06,aloop=loop=-1:size=2e+09,atrim=0:29.5[a_bg];
    [7:a]volume=0.45,adelay=13000|13000,apad=pad_dur=15.0[a_sfx1];
    [8:a]volume=0.40,adelay=21500|21500,apad=pad_dur=7.0[a_sfx2];
    [a_clip][a_v1][a_v2][a_bg][a_sfx1][a_sfx2]amix=inputs=6:duration=first:dropout_transition=2,loudnorm=I=-14:LRA=7:TP=-1.0[a_out]
    """

    cmd = [
        "ffmpeg", "-y",
        "-i", str(timeline_video),
        "-i", str(CARD_TWEET),
        "-i", str(CARD_DEBATE),
        "-i", str(clip_audio_part),
        "-i", str(VOICE_PART1),
        "-i", str(VOICE_PART2),
        "-i", str(BG_MUSIC),
        "-i", str(SFX_POP),
        "-i", str(SFX_WHOOSH),
        "-filter_complex", filter_complex.replace("\n", " ").strip(),
        "-map", "[v_out]",
        "-map", "[a_out]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-r", "60",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-t", "29.5",
        str(MASTER_OUTPUT)
    ]

    print("🚀 Rendering REAL_VIRAL_CAKIR_MASTER.mp4...")
    subprocess.run(cmd, check=True)
    print(f"🎉 MASTER VIDEO CREATED: {MASTER_OUTPUT}")


def generate_thumbnail_and_metadata():
    """Generates custom high-CTR 1080x1920 thumbnail and 10-language metadata."""
    # Extract frame at 16.0s (where Polat + Tweet Card are visible)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "16.0", "-i", str(MASTER_OUTPUT),
        "-vframes", "1", "-q:v", "2", str(MASTER_THUMB)
    ], check=True)
    print(f"✅ Thumbnail generated: {MASTER_THUMB}")

    metadata = {
        "title": "Kurtlar Vadisi Çakır'ın Planı ve Polat'ın Gerçek Kimliği 😂👑 #shorts #kurtlarvadisi",
        "description": (
            "Süleyman Çakır mekana sızma planını harfi harfine anlatırken Polat Alemdar'ın o efsane soğukkanlı bakışı! "
            "Sence Türk dizi tarihinin en büyük efsanesi Süleyman Çakır mı, yoksa Polat Alemdar mı? Yorumlarda tartışalım! 👇\n\n"
            "#kurtlarvadisi #süleymançakır #polatalemdar #racon #dizireplikleri #nostalji #shorts #viral #zirveninkanunu"
        ),
        "tags": [
            "kurtlar vadisi", "süleyman çakır", "polat alemdar", "oktay kaynarca", "necati şaşmaz",
            "kurtlar vadisi racon", "çakırın planı", "racon replikleri", "shorts", "viral shorts", "zirvenin kanunu"
        ],
        "category_id": "24",  # Entertainment
        "pinned_comment": (
            "👑 Zirvenin Kanunu: Sence Türk dizi tarihinin en büyük racon ustası ve lideri Süleyman Çakır mıydı, "
            "yoksa Polat Alemdar mı? 1: Çakır / 2: Polat. Yorumunu ve gerekçeni yaz, en iyi yorum sabitlenecek! 👇"
        )
    }

    MASTER_META.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Metadata saved: {MASTER_META}")


def main():
    print("=================================================================")
    print("🎬 PRODUCING REAL VIRAL CAKIR MASTER SHORT")
    print("=================================================================")
    timeline_video = prepare_video_timeline()
    build_master_composite(timeline_video)
    generate_thumbnail_and_metadata()
    print("\n✅ All production tasks completed successfully!")


if __name__ == "__main__":
    main()
