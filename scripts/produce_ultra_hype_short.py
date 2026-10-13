"""
Produce Next-Gen Ultra Hype Master Short for 'Zirvenin Kanunu' (@zirveninkanunu).
Features:
- Dual Cinematic Top Layer (Wolf of Wall Street + The Matrix high status cuts)
- 60 FPS Minecraft Parkour bottom layer
- Glowing golden separator bar
- Pixel-perfect X (Twitter) Dark Mode post card pop-up (Elon Musk)
- Transparent Gigachad meme reaction pop-up
- Timed multi-SFX cues (UI Pop, Vine Boom x2, Cash Register, Cinematic Whoosh)
- Brazilian Phonk background track (ducked & mastered)
- Kinetic word-by-word ASS subtitles with glowing power words
- EBU R128 studio loudness mastering (-15.0 LUFS, -0.7 dBTP)
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
from app.services.viral_visual_fx_toolkit import render_tweet_card, get_meme_path

OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOP_VIDEO_1 = BASE_DIR / "assets" / "cinematic" / "wolf_master.mp4"
TOP_VIDEO_2 = BASE_DIR / "assets" / "cinematic" / "matrix_master.mp4"
BOTTOM_VIDEO = BASE_DIR / "assets" / "stock" / "minecraft_parkour.mp4"
VOICE_PATH = OUTPUT_DIR / "hype_01_voice.mp3"
BG_MUSIC = BASE_DIR / "assets" / "music" / "brazilian_phonk_energy.mp3"

SFX_UI_POP = BASE_DIR / "assets" / "sfx" / "ui_pop.mp3"
SFX_VINE_BOOM = BASE_DIR / "assets" / "sfx" / "vine_boom.mp3"
SFX_CASH = BASE_DIR / "assets" / "sfx" / "cash_register.mp3"
SFX_WHOOSH = BASE_DIR / "assets" / "sfx" / "cinematic_whoosh.mp3"

TWEET_IMAGE = OUTPUT_DIR / "elon_tweet_card.png"
GIGACHAD_IMAGE = BASE_DIR / "assets" / "memes" / "gigachad.png"
ASS_SUBTITLES = OUTPUT_DIR / "ultra_hype_subtitles.ass"
MASTER_VIDEO = OUTPUT_DIR / "ULTRA_HYPE_INNOVATION_MASTER_01.mp4"

TIMED_SCRIPT = [
    ("Elon Musk'ın 12 yıllık sağ kolu Mary Beth Brown...", 0.0, 3.4),
    ("bir gün odasına girdi ve ciddi bir maaş zammı istedi.", 3.4, 6.5),
    ("Musk ne yaptı biliyor musunuz? Ona kızmadı. Tartışmadı.", 6.5, 10.3),
    ("Sadece şunu söyledi: İki hafta tatile çık! İşlerini ben yapacağım.", 10.3, 17.7),
    ("İki hafta boyunca Musk, asistanının tüm görevlerini üstlendi.", 17.7, 22.0),
    ("Ve kadın geri döndüğünde Musk kararını vermişti:", 22.0, 26.0),
    ("Yokluğunu hiç hissetmedim. Ve onu tek hamlede kovdu!", 26.0, 32.8),
    ("Acımasızca gelebilir. Ama zirvenin kanunu nettir:", 32.8, 36.3),
    ("Eğer yokluğun hissedilmiyorsa, varlığın sadece bir yüktür.", 36.3, 39.9),
    ("Peki sen: Vazgeçilmez olduğunu düşünüyor musun?", 39.9, 42.0),
]


def prepare_visual_assets():
    print("🎨 1. Generating / Verifying Visual Overlays...")
    render_tweet_card(
        author_name="Elon Musk",
        handle="@elonmusk",
        tweet_text="Asistanım dev bir zam istediğinde ona 2 hafta tatil verdim. Döndüğünde ise kovdum.",
        likes="842K",
        retweets="92K",
        views="5.4M",
        replies="24.6K",
        verified=True,
        output_path=str(TWEET_IMAGE),
        width=940
    )
    print(f"✅ Tweet Card Ready: {TWEET_IMAGE}")

    print("📝 2. Generating Kinetic ASS Subtitles...")
    generate_kinetic_word_by_word_ass(
        timed_sentences=TIMED_SCRIPT,
        output_path=ASS_SUBTITLES,
        character_badge="🔥 ACIMASIZ ZİRVE KANUNU 🔥",
        cta_text="Sen olsan ne yapardın? 1: Kov / 2: Zam ver 👇",
        chunk_size=3,
        subtitle_margin_v=460
    )
    print(f"✅ ASS Subtitles Ready: {ASS_SUBTITLES}")


def render_master():
    prepare_visual_assets()

    print("⏱️ 3. Probing voice duration...")
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(VOICE_PATH)
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    voice_dur = float(res.stdout.strip())
    total_dur = min(voice_dur + 0.5, 42.5)
    print(f"Total Master Target Duration: {total_dur:.2f}s")

    # Copy ASS subtitles to local working dir to avoid path encoding issues
    local_ass = Path("output_ultra_hype.ass")
    local_ass.write_bytes(ASS_SUBTITLES.read_bytes())
    ass_filter_path = "output_ultra_hype.ass"

    # Video Filter complex:
    # Dynamic top cut: 0:v for 0-22s, 1:v for 22-end with setsar=1
    # Stack with 2:v (Minecraft)
    # Draw golden bar
    # Overlay Tweet Card (t=2.0 to 7.5, with alpha fade)
    # Overlay Gigachad (t=27.0 to 30.5, scale to 380px wide, centered in top half)
    # Burn ASS subtitles
    video_filter = (
        "[0:v]trim=0:22,setpts=PTS-STARTPTS,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,fps=60[top1];"
        "[1:v]trim=0:22,setpts=PTS-STARTPTS,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,fps=60[top2];"
        "[top1][top2]concat=n=2:v=1:a=0[top_dyn];"
        "[2:v]trim=5:50,setpts=PTS-STARTPTS,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,fps=60[bottom];"
        "[top_dyn][bottom]vstack[base];"
        "[base]drawbox=y=956:color=gold@0.95:width=1080:height=8:t=fill[base_bar];"
        "[3:v]format=rgba,fade=t=in:st=2.0:d=0.25:alpha=1,fade=t=out:st=7.2:d=0.3:alpha=1[tweet_faded];"
        "[base_bar][tweet_faded]overlay=x=70:y=240:enable='between(t,2.0,7.5)'[with_tweet];"
        "[4:v]scale=360:-1,format=rgba,fade=t=in:st=27.0:d=0.3:alpha=1,fade=t=out:st=30.2:d=0.3:alpha=1[chad_faded];"
        "[with_tweet][chad_faded]overlay=x=360:y=280:enable='between(t,27.0,30.5)'[with_chad];"
        f"[with_chad]ass='{ass_filter_path}'[v_master]"
    )

    # Audio Filter complex:
    # SordumDede / Wandays Broadcast Audio Standard:
    # 5:a -> voice (volume=2.0, highpass 80Hz, presence boost @ 3kHz, split for sidechain)
    # 6:a -> music (ducked volume=0.035, lowpass 2800Hz, sidechain compressed against voice)
    # 7:a -> ui_pop (volume=0.45)
    # 8:a -> vine boom 1 at 7.5s, vine boom 2 at 26.5s (volume=0.45)
    # 9:a -> cash register at 16.5s (volume=0.40)
    # 10:a -> whoosh at 32.8s (volume=0.45)
    audio_filter = (
        "[5:a]volume=2.0,highpass=f=80,equalizer=f=3000:t=q:w=1:g=2.5,asplit=2[voice_main][voice_sc];"
        "[6:a]volume=0.035,lowpass=f=2800,highpass=f=100[music_raw];"
        "[music_raw][voice_sc]sidechaincompress=threshold=0.03:ratio=10:attack=10:release=250[music_ducked];"
        "[7:a]adelay=2000|2000,volume=0.45[sfx_pop];"
        "[8:a]adelay=7500|7500,volume=0.45[sfx_boom1];"
        "[8:a]adelay=26500|26500,volume=0.45[sfx_boom2];"
        "[9:a]adelay=16500|16500,volume=0.40[sfx_cash];"
        "[10:a]adelay=32800|32800,volume=0.45[sfx_whoosh];"
        "[voice_main][music_ducked][sfx_pop][sfx_boom1][sfx_boom2][sfx_cash][sfx_whoosh]"
        "amix=inputs=7:duration=first:dropout_transition=0,"
        "loudnorm=I=-13:TP=-0.8:LRA=5[a_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(TOP_VIDEO_1),
        "-stream_loop", "-1", "-i", str(TOP_VIDEO_2),
        "-stream_loop", "-1", "-ss", "00:00:10", "-i", str(BOTTOM_VIDEO),
        "-i", str(TWEET_IMAGE),
        "-i", str(GIGACHAD_IMAGE),
        "-i", str(VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_UI_POP),
        "-i", str(SFX_VINE_BOOM),
        "-i", str(SFX_CASH),
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

    print("🚀 4. Executing FFmpeg Ultra Hype Short Assembly...")
    subprocess.run(cmd, check=True)

    if MASTER_VIDEO.exists():
        size = MASTER_VIDEO.stat().st_size
        print(f"🎉 MASTER VIDEO CREATED! Path: {MASTER_VIDEO} | Size: {size} bytes")
        # Extract preview screenshot at 4.0s (showing tweet card) and 28.0s (showing gigachad)
        preview_tweet = OUTPUT_DIR / "preview_ultra_hype_tweet.jpg"
        preview_chad = OUTPUT_DIR / "preview_ultra_hype_chad.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", "00:00:04", "-i", str(MASTER_VIDEO), "-vframes", "1", str(preview_tweet)], check=True)
        subprocess.run(["ffmpeg", "-y", "-ss", "00:00:28", "-i", str(MASTER_VIDEO), "-vframes", "1", str(preview_chad)], check=True)
        print(f"📸 Previews generated: {preview_tweet} and {preview_chad}")
        return True
    return False

if __name__ == "__main__":
    render_master()
