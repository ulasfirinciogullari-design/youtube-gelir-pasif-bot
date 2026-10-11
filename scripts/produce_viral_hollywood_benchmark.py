"""
PRODUCE HOLLYWOOD BENCHMARK MASTER SHORT.
Zirvenin Kanunu (@zirveninkanunu) - Cinema-Grade Masterpiece.

Combines:
1. Multi-Scene 1080x960 60 FPS Cinematic Top Montage:
   - Act 1 (0.0s - 10.4s): Thomas Shelby Intense Stare & Presence
   - Act 2 (10.4s - 23.4s): The Godfather Unshakeable Silence & Eye Contact
   - Act 3 (23.4s - 40.8s): Thomas Shelby Cinematic Smoke & Cigarette Climax
2. Bottom Screen (1080x960): 60 FPS Minecraft Parkour Speedrun
3. 10px Golden Neon Metallic Divider
4. MrBeast / Alex Hormozi Kinetic ASS Subtitles:
   - Neon yellow active word highlight (&H0000FFFF)
   - Dynamic bounce scale {\t(0,80,\fscx118\fscy118)\t(80,160,\fscx104\fscy104)}
   - Top HUD: 👑 ZİRVENİN KANUNU | 3 KARANLIK GÜÇ KURALI
   - Bottom Comment Magnet: 💬 Sen olsan ne yapardın? 1 mi 2 mi? Yoruma yaz! 👇
5. 8-Track Hollywood Cinema Audio Mastering:
   - Voice: ElevenLabs Alp Deep Baritone + EQ presence boost
   - Music: Dark Tension Pulse ducked (-17dB)
   - 0.0s: Sub-bass 808 Boom impact
   - 5.7s: Cinematic Whoosh (Rule 1)
   - 14.1s: Cinematic Whoosh (Rule 2)
   - 23.4s: Cinematic Whoosh (Rule 3)
   - 28.5s: Tension Riser crescendo
   - 37.0s: Sub Impact on CTA
   - EBU R128 Studio Mastering (-14.5 LUFS, -0.7 dBTP)
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

VOICE_PATH = OUTPUT_DIR / "hollywood_benchmark_voice_raw.mp3"
TOP_VIDEO_PEAKY = BASE_DIR / "assets" / "cinematic" / "peaky_master.mp4"
TOP_VIDEO_GODFATHER = BASE_DIR / "assets" / "cinematic" / "godfather_master.mp4"
BOTTOM_VIDEO = BASE_DIR / "assets" / "stock" / "minecraft_parkour.mp4"

BG_MUSIC = BASE_DIR / "assets" / "music" / "dark_tension_pulse.mp3"
SFX_BOOM_808 = BASE_DIR / "assets" / "sfx" / "sub_boom_808.mp3"
SFX_IMPACT = BASE_DIR / "assets" / "sfx" / "impact_sub.mp3"
SFX_WHOOSH = BASE_DIR / "assets" / "sfx" / "cinematic_whoosh.mp3"
SFX_RISER = BASE_DIR / "assets" / "sfx" / "tension_riser.mp3"

ASS_SUBTITLES = OUTPUT_DIR / "hollywood_benchmark_subtitles.ass"
MASTER_VIDEO = OUTPUT_DIR / "VIRAL_HOLLYWOOD_BENCHMARK_MASTER.mp4"

TIMED_SCRIPT = [
    ("Zeki insanlar asla bağırmaz.", 0.0, 2.1),
    ("Çünkü gerçek güç,", 2.1, 3.5),
    ("odadaki en sessiz adamın cebindedir.", 3.5, 5.7),
    ("Kural bir: Gözlerini asla", 5.7, 8.0),
    ("ilk kaçıran sen olma.", 8.0, 10.4),
    ("Bir saniyelik tereddüt bile,", 10.4, 12.4),
    ("rakibine zayıflığını fısıldar.", 12.4, 14.1),
    ("Kural iki: Bir soru sorulduğunda", 14.1, 16.5),
    ("hemen cevap verme.", 16.5, 18.4),
    ("Üç saniye bekle.", 18.4, 20.2),
    ("Bu üç saniye, karşındakini", 20.2, 21.8),
    ("kendi zihninde savunmasız bırakır.", 21.8, 23.4),
    ("Kural üç: Haklı olduğunda bile", 23.4, 25.8),
    ("kendini savunma.", 25.8, 27.7),
    ("Kendini açıklamak acizlerin,", 27.7, 30.1),
    ("sessiz kalmak ise kralların tavrıdır.", 30.1, 32.4),
    ("Peki sen bir ortamda hangisisin?", 32.4, 34.6),
    ("Çok konuşan mı, sessizce izleyen mi?", 34.6, 37.0),
    ("Bir mi iki mi? Seçimini yoruma yaz.", 37.0, 39.0),
    ("Çünkü zeki insanlar asla bağırmaz.", 39.0, 40.77)
]


def create_subtitles() -> Path:
    print("📝 1. Generating Hollywood Kinetic ASS Subtitles...")
    ass_path = generate_kinetic_word_by_word_ass(
        timed_sentences=TIMED_SCRIPT,
        output_path=ASS_SUBTITLES,
        character_badge="👑 ZİRVENİN KANUNU | 3 GÜÇ KURALI 👑",
        cta_text="💬 Sen olsan ne yapardın? 1 mi 2 mi? Yoruma yaz! 👇",
        chunk_size=3,
        subtitle_margin_v=460
    )
    print(f"✅ ASS Subtitles Generated: {ass_path}")
    return ass_path


def assemble_and_render():
    ass_file = create_subtitles()

    # Total duration
    total_dur = 40.77
    print(f"⏱️ Master Video Duration: {total_dur:.2f}s")

    ass_filter_path = str(ass_file).replace("\\", "/").replace(":", "\\:")

    # Top Half Multi-Scene Montage:
    # 0:v -> Peaky Master (start at 0s, length 10.4s)
    # 1:v -> Godfather Master (start at 2s, length 13.0s: 10.4s -> 23.4s)
    # 2:v -> Peaky Master (start at 35s, length 17.37s: 23.4s -> 40.77s)
    # 3:v -> Bottom Video Minecraft 60 FPS
    # Concatenate 3 scenes for top:
    # Stack with Minecraft:
    # Add gold divider box:
    # Apply color grading curve:
    # Burn subtitles:
    video_filter = (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60,trim=0:10.4,setpts=PTS-STARTPTS[top_act1];"
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60,trim=2:15.0,setpts=PTS-STARTPTS[top_act2];"
        "[2:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60,trim=35:52.37,setpts=PTS-STARTPTS[top_act3];"
        "[top_act1][top_act2][top_act3]concat=n=3:v=1:a=0[top_montage];"
        "[top_montage]curves=all='0/0 0.1/0.08 0.5/0.48 0.8/0.85 1/1':red='0/0 1/0.96':blue='0/0.04 1/0.98',eq=contrast=1.12:saturation=1.18[top_graded];"
        "[3:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60,trim=0:40.77,setpts=PTS-STARTPTS,eq=saturation=1.22:contrast=1.08[bottom_graded];"
        "[top_graded][bottom_graded]vstack[stacked];"
        f"[stacked]drawbox=y=955:color=gold@0.95:width=1080:height=10:t=fill,ass='{ass_filter_path}'[v_master]"
    )

    # Audio Filter Graph:
    # 4:a -> Voice (Alp Baritone)
    # 5:a -> Music (dark tension pulse)
    # 6:a -> SFX Boom 808
    # 7:a -> SFX Impact Sub
    # 8:a -> SFX Whoosh
    # 9:a -> SFX Tension Riser
    audio_filter = (
        "[4:a]volume=1.35,equalizer=f=110:t=q:w=1:g=2.5,equalizer=f=3600:t=q:w=1:g=2.0[voice];"
        "[5:a]volume=0.07,lowpass=f=4200,highpass=f=90[music];"
        "[6:a]volume=0.9[sfx_boom_open];"
        "[8:a]adelay=5700|5700,volume=0.75[sfx_whoosh_rule1];"
        "[8:a]adelay=14100|14100,volume=0.75[sfx_whoosh_rule2];"
        "[8:a]adelay=23400|23400,volume=0.75[sfx_whoosh_rule3];"
        "[9:a]adelay=28500|28500,volume=0.65[sfx_tension];"
        "[7:a]adelay=37000|37000,volume=0.85[sfx_impact_climax];"
        "[voice][music][sfx_boom_open][sfx_whoosh_rule1][sfx_whoosh_rule2][sfx_whoosh_rule3][sfx_tension][sfx_impact_climax]"
        "amix=inputs=8:duration=first:dropout_transition=0,"
        "loudnorm=I=-14.5:TP=-0.7:LRA=7[a_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:00", "-i", str(TOP_VIDEO_PEAKY),        # 0:v
        "-ss", "00:00:00", "-i", str(TOP_VIDEO_GODFATHER),    # 1:v
        "-ss", "00:00:00", "-i", str(TOP_VIDEO_PEAKY),        # 2:v
        "-ss", "00:00:10", "-i", str(BOTTOM_VIDEO),           # 3:v
        "-i", str(VOICE_PATH),                                # 4:a
        "-stream_loop", "-1", "-i", str(BG_MUSIC),            # 5:a
        "-i", str(SFX_BOOM_808),                              # 6:a
        "-i", str(SFX_IMPACT),                                # 7:a
        "-i", str(SFX_WHOOSH),                                # 8:a
        "-i", str(SFX_RISER),                                 # 9:a
        "-filter_complex", filter_complex,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "17",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(MASTER_VIDEO)
    ]

    print("⚡ 2. Executing FFmpeg Hollywood Master Assembly...")
    res = subprocess.run(cmd, check=True)

    if MASTER_VIDEO.exists():
        size_mb = MASTER_VIDEO.stat().st_size / (1024 * 1024)
        print(f"🎉 MASTER RENDER SUCCESSFUL! {MASTER_VIDEO.name} ({size_mb:.2f} MB)")
        return True
    return False


if __name__ == "__main__":
    assemble_and_render()
