"""
Production Script: REAL_VIRAL_TYLER_SINGLE_CARD_MASTER.mp4
Channel: Zirvenin Kanunu (@zirveninkanunu)
Sector: Kült Sinema & Varoluşsal Tüketim (Fight Club / Tyler Durden)

STRICT RULE IMPLEMENTATION:
- EXACTLY 1 SINGLE VIRAL COMMENT CARD (Zero 2nd/3rd cards).
- Clip dialogue speaks first (0.0s - 9.0s). Narrator is 100% SILENT.
- Narrator speaks strictly after clip dialogue finishes (9.0s - 21.0s).
- Narrator reads the exact card text with natural human laughter & conversational tone.
- 1080x1920 60 FPS master video with blurred ambient background & EBU R128 (-14.0 LUFS) audio mastering.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Assets
RAW_FC_CLIP = ASSETS_DIR / "cinematic" / "fight_club_master.mp4"
RAW_TYLER_CLIP = ASSETS_DIR / "cinematic" / "tyler_logoless_4k.mp4.webm"
CARD_TWEET = OUTPUT_DIR / "tyler_card_tweet.png"
VOICE_PATH = OUTPUT_DIR / "tyler_narrator_voice.mp3"
BG_MUSIC = ASSETS_DIR / "music" / "dark_tension_pulse.mp3"
SFX_POP = ASSETS_DIR / "sfx" / "ui_pop.mp3"

MASTER_OUTPUT = OUTPUT_DIR / "REAL_VIRAL_TYLER_SINGLE_CARD_MASTER.mp4"
MASTER_THUMB = OUTPUT_DIR / "REAL_VIRAL_TYLER_SINGLE_CARD_MASTER_thumb.jpg"
MASTER_META = OUTPUT_DIR / "REAL_VIRAL_TYLER_SINGLE_CARD_MASTER_meta.json"

TOTAL_DURATION = 22.0  # seconds


def build_ass_subtitles(output_path: Path):
    """Generates clean ASS subtitles for Tyler's dialogue (0.0s - 9.0s)."""
    ass_content = """[Script Info]
Title: Fight Club Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Header,Arial Black,44,&H0000FFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,8,30,30,120,1
Style: TylerDialogue,Arial Black,52,&H00FFFFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,2,40,40,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:09.00,Header,,0,0,0,,{\\an8\\bord4\\shad2}🥊 FIGHT CLUB | TYLER DURDEN
Dialogue: 0,0:00:00.00,0:00:02.80,TylerDialogue,,0,0,0,,{\\c&H00D7FF&}Altı aydır uyuyamıyordum...
Dialogue: 0,0:00:02.80,0:00:05.80,TylerDialogue,,0,0,0,,{\\c&HFFFFFF&}Uykusuzlukla hiçbir şey gerçek değil.
Dialogue: 0,0:00:05.80,0:00:09.00,TylerDialogue,,0,0,0,,{\\c&H00FFFF&}Her şey kopyanın kopyasının kopyası.
"""
    output_path.write_text(ass_content, encoding="utf-8")
    print(f"✅ ASS subtitles written to {output_path}")


def prepare_video_timeline():
    """
    Builds a 22.0-second sequence:
    - 0.0s - 9.0s: Edward Norton insomnia dialogue (fight_club_master.mp4 from 36.5s to 45.5s)
    - 9.0s - 22.0s: Brad Pitt as Tyler Durden smoking/talking at the bar (tyler_logoless_4k.mp4.webm from 56.0s to 69.0s)
    """
    timeline_video = OUTPUT_DIR / "tyler_timeline_base.mp4"
    act1 = OUTPUT_DIR / "act1_fc.mp4"
    act2 = OUTPUT_DIR / "act2_tyler.mp4"

    # Act 1: 36.5s - 45.5s (9.0s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "36.5", "-t", "9.0", "-i", str(RAW_FC_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act1)
    ], check=True)

    # Act 2: Tyler bar scene 56.0s - 69.0s (13.0s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "56.0", "-t", "13.0", "-i", str(RAW_TYLER_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act2)
    ], check=True)

    concat_list = OUTPUT_DIR / "concat_tyler.txt"
    concat_list.write_text(f"file '{act1.resolve().as_posix()}'\nfile '{act2.resolve().as_posix()}'\n", encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(timeline_video)
    ], check=True)

    print(f"✅ Tyler video timeline base assembled: {timeline_video}")
    return timeline_video


def build_master_composite(timeline_video: Path):
    """
    Renders 1080x1920 60 FPS master video using FFmpeg filter_complex:
    - Background: Blurred & darkened video loop
    - Foreground: 1040x585 crisp video centered around y=300
    - Overlay: STRICTLY 1 SINGLE TWEET CARD at 9.0s - 22.0s centered around y=1080
    - Zero second card!
    - Subtitles: ASS subtitles burned for 0.0s - 9.0s
    - Audio:
      * Clip dialogue: 0.0s - 9.0s (volume=1.8)
      * Clip dialogue: 9.0s+ (silenced)
      * Narrator: begins at 9.0s sharp (volume=1.4)
      * Background music: -24 dB
      * SFX: pop at 9.0s
      * Mastered to -14.0 LUFS
    """
    ass_path = OUTPUT_DIR / "tyler_subs.ass"
    build_ass_subtitles(ass_path)
    ass_escaped = str(ass_path.resolve().as_posix()).replace(":", "\\:")

    # Extract raw clip audio for 0.0s - 9.0s
    clip_audio_part = OUTPUT_DIR / "fc_clip_dialogue.wav"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "36.5", "-t", "9.0", "-i", str(RAW_FC_CLIP),
        "-vn", "-ar", "48000", "-ac", "2", str(clip_audio_part)
    ], check=True)

    filter_complex = f"""
    [0:v]split=2[v_bg_in][v_fg_in];
    [v_bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,colorchannelmixer=aa=1.0,eq=brightness=-0.25:contrast=1.1[v_bg];
    [v_fg_in]scale=1040:585,setsar=1[v_fg];
    [v_bg][v_fg]overlay=20:300[v_composed];
    [1:v]scale=1020:-1[v_card];
    [v_composed][v_card]overlay=30:1080:enable='between(t,9.0,22.0)'[v_with_card];
    [v_with_card]subtitles='{ass_escaped}'[v_out];

    [2:a]volume=1.8,apad=pad_dur=14.0[a_clip];
    [3:a]volume=1.4,adelay=9000|9000,apad=pad_dur=2.0[a_voice];
    [4:a]volume=0.06,aloop=loop=-1:size=2e+09,atrim=0:22.0[a_bg];
    [5:a]volume=0.45,adelay=9000|9000,apad=pad_dur=13.0[a_sfx];
    [a_clip][a_voice][a_bg][a_sfx]amix=inputs=4:duration=first:dropout_transition=2,loudnorm=I=-14:LRA=7:TP=-1.0[a_out]
    """

    cmd = [
        "ffmpeg", "-y",
        "-i", str(timeline_video),
        "-i", str(CARD_TWEET),
        "-i", str(clip_audio_part),
        "-i", str(VOICE_PATH),
        "-i", str(BG_MUSIC),
        "-i", str(SFX_POP),
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
        "-t", "22.0",
        str(MASTER_OUTPUT)
    ]

    print("🚀 Rendering REAL_VIRAL_TYLER_SINGLE_CARD_MASTER.mp4...")
    subprocess.run(cmd, check=True)
    print(f"🎉 SINGLE CARD MASTER CREATED: {MASTER_OUTPUT}")


def generate_thumbnail_and_metadata():
    """Generates custom high-CTR 1080x1920 thumbnail and metadata."""
    # Extract frame at 14.0s (where Tyler + Tweet Card are visible)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "14.0", "-i", str(MASTER_OUTPUT),
        "-vframes", "1", "-q:v", "2", str(MASTER_THUMB)
    ], check=True)
    print(f"✅ Thumbnail generated: {MASTER_THUMB}")

    metadata = {
        "title": "Tyler Durden Tüketim Çılgınlığını Tek Cümleyle Bitirdi 👑 #shorts #fightclub",
        "description": (
            "Fight Club ve Tyler Durden'ın 1999 yılında günümüz tüketim çılgınlığını özetlediği o efsane sahne! "
            "Sahip oldukların sonunda sana sahip mi oluyor? Sence Tyler Durden haklı mı? Yorumlarda tartışalım! 👇\n\n"
            "#fightclub #tylerdurden #bradpitt #sinema #felsefe #motivasyon #tüketim #shorts #viral #zirveninkanunu"
        ),
        "tags": [
            "fight club", "tyler durden", "brad pitt", "edward norton", "fight club replikleri",
            "tüketim çılgınlığı", "felsefe", "shorts", "viral shorts", "zirvenin kanunu"
        ],
        "category_id": "24",
        "pinned_comment": (
            "🥊 Zirvenin Kanunu: Sence Tyler Durden haklı mı? 1: Evet, modern insan eşyaların kölesi oldu / "
            "2: Hayır, tüketim olmadan dünya dönmez. Tarafını ve gerekçeni yaz, en iyi yorum sabitlenecek! 👇"
        )
    }

    MASTER_META.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Metadata saved: {MASTER_META}")


def main():
    print("=================================================================")
    print("🎬 PRODUCING REAL VIRAL TYLER DURDEN (STRICT SINGLE COMMENT)")
    print("=================================================================")
    timeline_video = prepare_video_timeline()
    build_master_composite(timeline_video)
    generate_thumbnail_and_metadata()
    print("\n✅ Single-comment production completed successfully!")


if __name__ == "__main__":
    main()
