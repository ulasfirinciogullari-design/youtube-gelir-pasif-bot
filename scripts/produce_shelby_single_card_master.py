"""
Master Production Script: REAL_VIRAL_SHELBY_SINGLE_CARD_MASTER.mp4
Channel: Zirvenin Kanunu (@zirveninkanunu)
Sector: Kült Sinema & Güç Psikolojisi (Thomas Shelby / Peaky Blinders)

STRICT PRODUCTION RULES:
1. 100% LOGOLESS FOOTAGE (Zero TV station stamps, zero external channel watermarks).
2. ON-SCREEN BRANDING BADGE: '👑 ZİRVENİN KANUNU • ABONE OL 🔔' at top safe zone.
3. STRICTLY 1 SINGLE VIRAL TWEET CARD (Zero 2nd/3rd cards).
4. Clip dialogue speaks first (0.0s - 9.0s). Narrator is 100% SILENT during clip.
5. Narrator speaks strictly after clip finishes (9.0s - 23.8s) with natural human laughter & delivery.
6. 1080x1920 60 FPS master video with blurred ambient background & EBU R128 (-14.0 LUFS) audio mastering.
7. Guaranteed SINGLE comment under YouTube video (Zero duplicate comments).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Assets
RAW_PEAKY_CLIP = ASSETS_DIR / "cinematic" / "peaky_master.mp4"
CARD_TWEET = OUTPUT_DIR / "shelby_card_tweet.png"
SUBSCRIBE_BADGE = OUTPUT_DIR / "channel_subscribe_badge.png"
VOICE_PATH = OUTPUT_DIR / "shelby_narrator_voice.mp3"
BG_MUSIC = ASSETS_DIR / "music" / "dark_tension_pulse.mp3"
SFX_POP = ASSETS_DIR / "sfx" / "ui_pop.mp3"

MASTER_OUTPUT = OUTPUT_DIR / "REAL_VIRAL_SHELBY_SINGLE_CARD_MASTER.mp4"
MASTER_THUMB = OUTPUT_DIR / "REAL_VIRAL_SHELBY_SINGLE_CARD_MASTER_thumb.jpg"
MASTER_META = OUTPUT_DIR / "REAL_VIRAL_SHELBY_SINGLE_CARD_MASTER_meta.json"

TOTAL_DURATION = 24.0  # seconds
CARD_START_TIME = 9.0  # seconds


def synthesize_voice():
    """Synthesizes natural human voiceover with ElevenLabs Alp."""
    from app.services.elevenlabs_engine import synthesize_elevenlabs_speech

    speech_text = (
        "Haha, yoruma bak: 'Thomas Shelby mekana girdiğinde sigarasını yakana kadar geçen beş saniyede "
        "masadaki herkesin geçmişi gözünün önünden geçti resmen!' Harbi öyle bir ağırlık var adamda. "
        "Sence dizi tarihinin en karizmatik lideri Thomas Shelby mi? Yoruma yaz!"
    )
    if not VOICE_PATH.exists():
        print("🎙️ ElevenLabs Alp sesi sentezleniyor...")
        ok = synthesize_elevenlabs_speech(speech_text, VOICE_PATH)
        if not ok or not VOICE_PATH.exists():
            raise RuntimeError("ElevenLabs ses sentezi başarısız oldu!")
    print(f"✅ Ses dosyası hazır: {VOICE_PATH} ({VOICE_PATH.stat().st_size} bytes)")


def ensure_subscribe_badge():
    """Ensures subscribe badge is rendered."""
    from app.services.branding_badge_renderer import render_channel_subscribe_badge
    if not SUBSCRIBE_BADGE.exists():
        render_channel_subscribe_badge(SUBSCRIBE_BADGE)
    print(f"✅ Subscribe badge hazır: {SUBSCRIBE_BADGE}")


def build_ass_subtitles(output_path: Path):
    """Generates clean ASS subtitles for Shelby intro (0.0s - 9.0s)."""
    ass_content = """[Script Info]
Title: Shelby Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ShelbyDialogue,Arial Black,50,&H00FFFFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,2,40,40,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:04.50,ShelbyDialogue,,0,0,0,,{\\c&H00D7FF&}Konuşmaya başladığında güç sende değildir...
Dialogue: 0,0:00:04.50,0:00:09.00,ShelbyDialogue,,0,0,0,,{\\c&HFFFFFF&}Güç, sessizce odayı yönettiğin andadır.
"""
    output_path.write_text(ass_content, encoding="utf-8")
    print(f"✅ ASS subtitles written to {output_path}")


def prepare_video_timeline() -> Path:
    """
    Builds a 24.0-second seamless sequence from peaky_master.mp4:
    - 0.0s - 9.0s: Shelby cigarette walk (59.0s to 68.0s)
    - 9.0s - 24.0s: Shelby study room tension (68.0s to 83.0s)
    """
    timeline_video = OUTPUT_DIR / "shelby_timeline_base.mp4"
    act1 = OUTPUT_DIR / "act1_shelby.mp4"
    act2 = OUTPUT_DIR / "act2_shelby.mp4"

    # Act 1: 59.0s to 68.0s (9.0s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "59.0", "-t", "9.0", "-i", str(RAW_PEAKY_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act1)
    ], check=True)

    # Act 2: 68.0s to 83.0s (15.0s)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "68.0", "-t", "15.0", "-i", str(RAW_PEAKY_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(act2)
    ], check=True)

    concat_list = OUTPUT_DIR / "concat_shelby.txt"
    concat_list.write_text(f"file '{act1.resolve().as_posix()}'\nfile '{act2.resolve().as_posix()}'\n", encoding="utf-8")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(timeline_video)
    ], check=True)

    print(f"✅ Shelby timeline assembled: {timeline_video}")
    return timeline_video


def build_master_composite(timeline_video: Path):
    """
    Renders 1080x1920 60 FPS master video using FFmpeg filter_complex:
    - Background: Blurred & darkened video loop
    - Foreground: 1040x585 crisp video centered around y=300
    - Overlay 1: On-screen Subscribe & Channel branding badge at top (y=140)
    - Overlay 2: STRICTLY 1 SINGLE TWEET CARD at 9.0s - 24.0s centered around y=1080
    - Subtitles: ASS subtitles burned for 0.0s - 9.0s
    - Audio:
      * Clip ambient: 0.0s - 9.0s (volume=1.6)
      * Narrator: begins at 9.0s sharp (volume=1.4)
      * Background music: -24 dB (volume=0.045)
      * SFX: pop at 9.0s
      * Mastered to -14.0 LUFS
    """
    ass_path = OUTPUT_DIR / "shelby_subs.ass"
    build_ass_subtitles(ass_path)
    ass_escaped = str(ass_path.resolve().as_posix()).replace(":", "\\:")

    # Extract raw clip audio for 0.0s - 9.0s
    clip_audio_part = OUTPUT_DIR / "shelby_clip_ambient.wav"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "59.0", "-t", "9.0", "-i", str(RAW_PEAKY_CLIP),
        "-vn", "-ar", "48000", "-ac", "2", str(clip_audio_part)
    ], check=True)

    filter_complex = f"""
    [0:v]split=2[v_bg_in][v_fg_in];
    [v_bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,colorchannelmixer=aa=1.0,eq=brightness=-0.25:contrast=1.1[v_bg];
    [v_fg_in]scale=1040:585,setsar=1[v_fg];
    [v_bg][v_fg]overlay=20:300[v_composed];
    [1:v]scale=680:-1[v_badge];
    [v_composed][v_badge]overlay=(W-w)/2:140[v_with_badge];
    [2:v]scale=1020:-1[v_card];
    [v_with_badge][v_card]overlay=30:1080:enable='between(t,9.0,24.0)'[v_with_card];
    [v_with_card]subtitles='{ass_escaped}'[v_out];

    [3:a]volume=1.6,apad=pad_dur=16.0[a_clip];
    [4:a]volume=1.4,adelay=9000|9000,apad=pad_dur=2.0[a_voice];
    [5:a]volume=0.045,aloop=loop=-1:size=2e+09,atrim=0:24.0[a_bg];
    [6:a]volume=0.45,adelay=9000|9000,apad=pad_dur=15.0[a_sfx];
    [a_clip][a_voice][a_bg][a_sfx]amix=inputs=4:duration=first:dropout_transition=2,loudnorm=I=-14:LRA=7:TP=-1.0[a_out]
    """

    cmd = [
        "ffmpeg", "-y",
        "-i", str(timeline_video),
        "-i", str(SUBSCRIBE_BADGE),
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
        "-t", str(TOTAL_DURATION),
        str(MASTER_OUTPUT)
    ]

    print("🚀 Rendering REAL_VIRAL_SHELBY_SINGLE_CARD_MASTER.mp4...")
    subprocess.run(cmd, check=True)
    print(f"🎉 SINGLE CARD MASTER CREATED: {MASTER_OUTPUT}")


def generate_thumbnail_and_metadata():
    """Generates custom high-CTR 1080x1920 thumbnail and metadata."""
    # Extract frame at 14.0s (where Shelby + Badge + Tweet Card are visible)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "14.0", "-i", str(MASTER_OUTPUT),
        "-vframes", "1", "-q:v", "2", str(MASTER_THUMB)
    ], check=True)
    print(f"✅ Thumbnail generated: {MASTER_THUMB}")

    metadata = {
        "title": "Thomas Shelby'nin Masada Oturanları Donduran Sessizlik Kuralı 👑 #shorts #peakyblinders",
        "description": (
            "Thomas Shelby bir mekana girdiğinde konuşmadan önce odayı nasıl sessizleştirdiğinin hikayesi! "
            "Gerçek güç bağırmakta değil, sessizce odayı yönetmektedir. "
            "Sence sinema ve dizi tarihinin en karizmatik lideri kim? Yorumlarda tartışalım! 👇\n\n"
            "#peakyblinders #thomasshelby #cillianmurphy #racon #karizma #güç #psikoloji #shorts #viral #zirveninkanunu"
        ),
        "tags": [
            "thomas shelby", "peaky blinders", "cillian murphy", "sessizlik kuralı",
            "güç psikolojisi", "racon", "karizma", "shorts", "viral shorts", "zirvenin kanunu"
        ],
        "category_id": "24",  # Entertainment
        "pinned_comment": (
            "👑 Zirvenin Kanunu: Sence sinema ve dizi tarihinin en karizmatik lideri kim? "
            "1: Thomas Shelby / 2: Michael Corleone / 3: Walter White. Tarafını ve gerekçeni yaz, en iyi yorum sabitlenecek! 👇"
        )
    }

    MASTER_META.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Metadata saved: {MASTER_META}")


def main():
    print("=================================================================")
    print("🎬 PRODUCING REAL VIRAL SHELBY MASTER SHORT (PEAKY BLINDERS)")
    print("=================================================================")
    synthesize_voice()
    ensure_subscribe_badge()
    timeline_video = prepare_video_timeline()
    build_master_composite(timeline_video)
    generate_thumbnail_and_metadata()
    print("\n✅ Single-comment master production completed successfully!")


if __name__ == "__main__":
    main()
