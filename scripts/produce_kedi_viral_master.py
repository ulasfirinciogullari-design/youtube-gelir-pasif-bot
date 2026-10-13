"""
Master Production Script: REAL_VIRAL_KEDI_SINGLE_CARD_MASTER.mp4
Channel: Zirvenin Kanunu (@zirveninkanunu)
Sector: Pet Psychology & Turkish Street Comedy (Kasap Kedi)

STRICT PRODUCTION RULES:
1. STRICTLY 1 SINGLE VIRAL TWEET CARD (Zero 2nd/3rd cards).
2. Clip ambient speaks first (0.0s - 7.5s). Narrator is 100% SILENT during clip.
3. Narrator speaks strictly after clip dialogue finishes (7.5s - 19.5s).
4. Narrator reads the exact card text with natural human laughter & conversational tone (ElevenLabs Alp).
5. 1080x1920 60 FPS master video with blurred ambient background & EBU R128 (-14.0 LUFS) audio mastering.
6. Guaranteed SINGLE comment under YouTube video (Zero duplicate comments).
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
RAW_CAT_CLIP = OUTPUT_DIR / "kasap_kedi_raw.mp4"
CARD_TWEET = OUTPUT_DIR / "kedi_card_tweet.png"
VOICE_PATH = OUTPUT_DIR / "kedi_narrator_voice.mp3"
BG_MUSIC = ASSETS_DIR / "music" / "quirky_comedy_groove.mp3"
SFX_POP = ASSETS_DIR / "sfx" / "ui_pop.mp3"

MASTER_OUTPUT = OUTPUT_DIR / "REAL_VIRAL_KEDI_SINGLE_CARD_MASTER.mp4"
MASTER_THUMB = OUTPUT_DIR / "REAL_VIRAL_KEDI_SINGLE_CARD_MASTER_thumb.jpg"
MASTER_META = OUTPUT_DIR / "REAL_VIRAL_KEDI_SINGLE_CARD_MASTER_meta.json"

TOTAL_DURATION = 20.0  # seconds
CARD_START_TIME = 7.5  # seconds


def synthesize_voice():
    """Synthesizes natural human voiceover with ElevenLabs Alp."""
    from app.services.elevenlabs_engine import synthesize_elevenlabs_speech

    speech_text = (
        "Haha, yoruma gel: 'Kasabın önünde iki ayağının üstünde sıra bekleyen kedi, "
        "bankamatik sırasında önüme geçen teyzelerden daha medeni yemin ederim!' "
        "Şu asalet ve sabra bakar mısın? Sen hiç böyle sırasını bekleyen kedi gördün mü? Yoruma yaz!"
    )
    print("🎙️ ElevenLabs Alp sesi sentezleniyor...")
    ok = synthesize_elevenlabs_speech(speech_text, VOICE_PATH)
    if not ok or not VOICE_PATH.exists():
        raise RuntimeError("ElevenLabs ses sentezi başarısız oldu!")
    print(f"✅ Ses dosyası oluşturuldu: {VOICE_PATH} ({VOICE_PATH.stat().st_size} bytes)")


def build_ass_subtitles(output_path: Path):
    """Generates clean ASS subtitles for cat scene (0.0s - 7.5s)."""
    ass_content = """[Script Info]
Title: Kasap Kedi Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Header,Arial Black,44,&H0000FFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,2,8,30,30,120,1
Style: CatDialogue,Arial Black,50,&H00FFFFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,3,2,40,40,680,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:07.50,Header,,0,0,0,,{\\an8\\bord4\\shad2}🐱 KEDİ RACONU | KASAP KEDİSİ
Dialogue: 0,0:00:00.00,0:00:03.50,CatDialogue,,0,0,0,,{\\c&H00D7FF&}Kasabın önünde iki ayağının üstünde bekleyen kedi...
Dialogue: 0,0:00:03.50,0:00:07.50,CatDialogue,,0,0,0,,{\\c&HFFFFFF&}Saygıyla sırasını bekliyor, acele etmiyor.
"""
    output_path.write_text(ass_content, encoding="utf-8")
    print(f"✅ ASS subtitles written to {output_path}")


def prepare_video_timeline() -> Path:
    """Prepares clean 20-second video from kasap_kedi_raw.mp4."""
    timeline_video = OUTPUT_DIR / "kedi_timeline_base.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "0.0", "-t", str(TOTAL_DURATION), "-i", str(RAW_CAT_CLIP),
        "-vf", "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", str(timeline_video)
    ], check=True)
    print(f"✅ Video timeline base assembled: {timeline_video}")
    return timeline_video


def build_master_composite(timeline_video: Path):
    """
    Renders 1080x1920 60 FPS master video using FFmpeg filter_complex:
    - Background: Blurred & darkened video loop
    - Foreground: 1040x585 crisp video centered around y=300
    - Overlay: STRICTLY 1 SINGLE TWEET CARD at 7.5s - 20.0s centered around y=1080
    - Subtitles: ASS subtitles burned for 0.0s - 7.5s
    - Audio:
      * Clip ambient: 0.0s - 7.5s (volume=1.5)
      * Narrator: begins at 7.5s sharp (volume=1.4)
      * Background music: -24 dB (volume=0.045)
      * SFX: pop at 7.5s
      * Mastered to -14.0 LUFS
    """
    ass_path = OUTPUT_DIR / "kedi_subs.ass"
    build_ass_subtitles(ass_path)
    ass_escaped = str(ass_path.resolve().as_posix()).replace(":", "\\:")

    # Extract raw clip audio for 0.0s - 7.5s
    clip_audio_part = OUTPUT_DIR / "kedi_clip_ambient.wav"
    subprocess.run([
        "ffmpeg", "-y", "-ss", "0.0", "-t", "7.5", "-i", str(RAW_CAT_CLIP),
        "-vn", "-ar", "48000", "-ac", "2", str(clip_audio_part)
    ], check=True)

    filter_complex = f"""
    [0:v]split=2[v_bg_in][v_fg_in];
    [v_bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,colorchannelmixer=aa=1.0,eq=brightness=-0.25:contrast=1.1[v_bg];
    [v_fg_in]scale=1040:585,setsar=1[v_fg];
    [v_bg][v_fg]overlay=20:300[v_composed];
    [1:v]scale=1020:-1[v_card];
    [v_composed][v_card]overlay=30:1080:enable='between(t,7.5,20.0)'[v_with_card];
    [v_with_card]subtitles='{ass_escaped}'[v_out];

    [2:a]volume=1.5,apad=pad_dur=13.0[a_clip];
    [3:a]volume=1.4,adelay=7500|7500,apad=pad_dur=2.0[a_voice];
    [4:a]volume=0.045,aloop=loop=-1:size=2e+09,atrim=0:20.0[a_bg];
    [5:a]volume=0.45,adelay=7500|7500,apad=pad_dur=13.0[a_sfx];
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
        "-t", str(TOTAL_DURATION),
        str(MASTER_OUTPUT)
    ]

    print("🚀 Rendering REAL_VIRAL_KEDI_SINGLE_CARD_MASTER.mp4...")
    subprocess.run(cmd, check=True)
    print(f"🎉 SINGLE CARD MASTER CREATED: {MASTER_OUTPUT}")


def generate_thumbnail_and_metadata():
    """Generates custom high-CTR 1080x1920 thumbnail and metadata."""
    # Extract frame at 12.0s (where Cat + Tweet Card are clearly visible)
    subprocess.run([
        "ffmpeg", "-y", "-ss", "12.0", "-i", str(MASTER_OUTPUT),
        "-vframes", "1", "-q:v", "2", str(MASTER_THUMB)
    ], check=True)
    print(f"✅ Thumbnail generated: {MASTER_THUMB}")

    metadata = {
        "title": "Kasap Kedisinin Bankamatik Sırasındaki Teyzelerden Daha Medeni Olması 😂🐱 #shorts #kedi",
        "description": (
            "Kasabın önünde iki ayağının üstünde sabırla sırasını bekleyen esnaf kedi! "
            "Şu asalet ve medeniyete bakar mısın? Sence sokak hayvanları insanlardan daha mı saygılı? "
            "Yorumlarda tartışalım! 👇\n\n"
            "#kedi #kediler #komikvideolar #esnaf #sokakhayvanları #mizah #komedi #shorts #viral #zirveninkanunu"
        ),
        "tags": [
            "kedi", "kediler", "komik kedi", "kasap kedisi", "komik videolar",
            "esnaf kedi", "hayvanlar alemi", "shorts", "viral shorts", "zirvenin kanunu"
        ],
        "category_id": "24",  # Entertainment
        "pinned_comment": (
            "🐱 Zirvenin Kanunu: Kasabın önünde sırasını bekleyen kedi mi daha sabırlı, "
            "yoksa indirim görünce kapıya koşan insanlar mı? 😂 Sence sokak hayvanları insanlardan daha mı medeni? Yoruma yaz! 👇"
        )
    }

    MASTER_META.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Metadata saved: {MASTER_META}")


def main():
    print("=================================================================")
    print("🎬 PRODUCING REAL VIRAL KEDI SHORT (PET PSYCHOLOGY & COMEDY)")
    print("=================================================================")
    synthesize_voice()
    timeline_video = prepare_video_timeline()
    build_master_composite(timeline_video)
    generate_thumbnail_and_metadata()
    print("\n✅ Single-comment master production completed successfully!")


if __name__ == "__main__":
    main()
