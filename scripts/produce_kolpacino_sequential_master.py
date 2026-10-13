"""
Produce Sequential Balanced Master Short: Kolpaçino - Hedef Ben Miyim Tayfun?
Implements the user's core feedback:
1. "ÖNCE KLİP KONUŞUR, SONRA YORUM":
   - 0.0s - 13.0s: Film sahnesi konuşur, dış ses 0.0 sessiz, film sesi kristal netlikte!
   - 13.0s - 37.5s: Dış ses yorumları teker teker ve sıralı okur.
2. "HAFİF VİDEOYA UYACAK BİR FON MÜZİĞİ":
   - Monkeys Spinning Monkeys (hafif, neşeli, tatlı komedi fon müziği).
   - -24 dB ses seviyesinde, konuşmaların arkasında yumuşacık akıyor.
3. "YORUMLARI DAHA DENGELİ VE SIRALI OKUMA":
   - 13.0s - 22.3s: Yorum 1 (sabri_magduru @user-kd9sm2pl) ekrana gelir ve Alp tam olarak bu yorumu okur.
   - 22.3s - 29.7s: Yorum 2 (mertcanxd @kedi_seven_biri) ekrana gelir ve Alp bu yorumu okur.
   - 29.7s - 37.5s: Yorum 3 (Kral Baba @kral_baba_34) ekrana gelir ve Alp okuyup izleyiciye soru sorar.
4. "ORGANİK İNTERNET NİCKLERİ VE GERÇEK EMOJİLER":
   - Gerçek Apple/Twemoji SVG emojiler, gerçek dark-mode UI, organik kullanıcı isimleri.
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
ASSETS_DIR = BASE_DIR / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"

# Source files
REAL_VIDEO = OUTPUT_DIR / "kolpacino_1080p_hedef.mp4"
COMMENTARY_VOICE = OUTPUT_DIR / "voice_alp_sequential_comments.mp3"
BACKGROUND_MUSIC = MUSIC_DIR / "monkeys_spinning_monkeys.mp3"
SFX_POP = SFX_DIR / "ui_pop.mp3"

HEADER_IMG = OUTPUT_DIR / "kolpacino_header_playwright.png"
FOOTER_IMG = OUTPUT_DIR / "kolpacino_footer_playwright.png"

C1_IMG = OUTPUT_DIR / "real_social_c1.png"
C2_IMG = OUTPUT_DIR / "real_social_c2.png"
C3_IMG = OUTPUT_DIR / "real_social_c3.png"

SUBTITLE_PATH = OUTPUT_DIR / "kolpacino_subtitles_sequential.ass"
MASTER_AUDIO = OUTPUT_DIR / "kolpacino_sequential_audio_master.mp3"
MASTER_VIDEO = OUTPUT_DIR / "REAL_VIRAL_KOLPACINO_SEQUENTIAL_MASTER.mp4"


def build_audio_master(total_duration: float = 37.5) -> Path:
    """Builds the perfectly balanced sequential audio track."""
    print("🎙️ 1. Master Ses Kanalları Dengeleniyor...")
    clip_end = 12.8
    vo_start_ms = 13000

    cmd = [
        "ffmpeg", "-y",
        "-i", str(REAL_VIDEO),
        "-i", str(COMMENTARY_VOICE),
        "-stream_loop", "-1", "-i", str(BACKGROUND_MUSIC),
        "-i", str(SFX_POP),
        "-filter_complex", (
            # 1. Clip dialogue padded with silence
            f"[0:a]highpass=f=85,equalizer=f=3000:t=q:w=1.2:g=3.5,equalizer=f=5200:t=q:w=1.0:g=2.2,"
            f"volume=enable='between(t,0,{clip_end})':volume=1.0,"
            f"volume=enable='gte(t,{clip_end})':volume=0.04,"
            f"apad=whole_dur={total_duration}[clip_clean];"
            # 2. Alp sequential voice
            f"[1:a]adelay={vo_start_ms}|{vo_start_ms},volume=2.2,highpass=f=80,equalizer=f=3200:t=q:w=1.2:g=2.5,"
            f"apad=whole_dur={total_duration},asplit=2[vo_main][vo_sc];"
            # 3. Soft background comedy music (ducked under both speech stems)
            f"[2:a]aformat=channel_layouts=stereo,volume=0.016,lowpass=f=2800,highpass=f=80,atrim=0:{total_duration},asetpts=PTS-STARTPTS[m_raw];"
            f"[m_raw][clip_clean]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=300[m_duck1];"
            f"[m_duck1][vo_sc]sidechaincompress=threshold=0.03:ratio=10:attack=15:release=300[m_ducked];"
            # 4. Timed UI pops
            f"[3:a]adelay=13000|13000,volume=0.16[sfx1];"
            f"[3:a]adelay=22300|22300,volume=0.16[sfx2];"
            f"[3:a]adelay=29700|29700,volume=0.16[sfx3];"
            # 5. Mix all 6 streams with EBU R128 mastering
            f"[clip_clean][vo_main][m_ducked][sfx1][sfx2][sfx3]amix=inputs=6:duration=longest:dropout_transition=0,atrim=0:{total_duration},loudnorm=I=-14:TP=-0.7:LRA=6[a_out]"
        ),
        "-map", "[a_out]",
        "-t", f"{total_duration}",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        str(MASTER_AUDIO)
    ]
    subprocess.run(cmd, check=True)
    print(f"✅ Master Audio Tamamlandı: {MASTER_AUDIO} ({MASTER_AUDIO.stat().st_size} bytes)")
    return MASTER_AUDIO


def build_video_master(total_duration: float = 37.5) -> Path:
    """Builds the 1080x1920 60 FPS master Short with sequential cards and subtitles."""
    print("🎬 2. Master Dikey Video Kurgusu Başlatılıyor...")
    ass_escaped = str(SUBTITLE_PATH.resolve()).replace("\\", "/").replace(":", "\\:")

    # Video Filter Graph:
    # 0:v = REAL_VIDEO (extended to 37.5s via tpad)
    # 1:v = HEADER_IMG
    # 2:v = FOOTER_IMG
    # 3:v = C1_IMG (sabri_magduru @user-kd9sm2pl)
    # 4:v = C2_IMG (mertcanxd @kedi_seven_biri)
    # 5:v = C3_IMG (Kral Baba @kral_baba_34)
    # 6:a = MASTER_AUDIO

    video_filter = (
        # 0. Extend video to 37.5s by cloning last frame
        "[0:v]tpad=stop_mode=clone:stop_duration=15,fps=60[v_ext];"
        # 1. Ambient Background blur
        "[v_ext]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,drawbox=0:0:1080:1920:color=black@0.45:t=fill[v_bg];"
        # 2. Sharp center video (1080x608)
        "[v_ext]scale=1080:608:force_original_aspect_ratio=decrease[v_center];"
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
        # 8. Timed sequential comment reveals:
        # Card 1: 13.0s to 22.3s
        # Card 2: 22.3s to 29.7s
        # Card 3: 29.7s to 37.5s
        "[v_foot][c1]overlay=60:1030:enable='between(t,13.0,22.3)'[v_c1];"
        "[v_c1][c2]overlay=60:1030:enable='between(t,22.3,29.7)'[v_c2];"
        f"[v_c2][c3]overlay=60:1030:enable='between(t,29.7,{total_duration})'[v_c3];"
        # 9. Burn synchronized ASS Subtitles
        f"[v_c3]subtitles='{ass_escaped}'[v_master]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(REAL_VIDEO),
        "-i", str(HEADER_IMG),
        "-i", str(FOOTER_IMG),
        "-i", str(C1_IMG),
        "-i", str(C2_IMG),
        "-i", str(C3_IMG),
        "-i", str(MASTER_AUDIO),
        "-filter_complex", video_filter,
        "-map", "[v_master]",
        "-map", "6:a",
        "-t", f"{total_duration}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        str(MASTER_VIDEO)
    ]

    print("🚀 Video FFmpeg ile 60 FPS olarak render ediliyor...")
    subprocess.run(cmd, check=True)
    print(f"\n🎉 SIRALI VE DENGELİ MASTER VİDEO TAMAMLANDI: {MASTER_VIDEO}")
    print(f"📁 Boyut: {MASTER_VIDEO.stat().st_size / (1024*1024):.2f} MB")
    return MASTER_VIDEO


def main():
    total_duration = 37.5
    build_audio_master(total_duration)
    build_video_master(total_duration)


if __name__ == "__main__":
    main()
