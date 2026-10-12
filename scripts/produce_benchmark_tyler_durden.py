"""
PRODUCE ULTRA-PREMIUM CINEMATIC SHORT: TYLER DURDEN (BENCHMARK CINEMA ESSAY).
Channel: Zirvenin Kanunu (@zirveninkanunu)
Benchmark: Enver Gülşen / Sinemovie / cineshot_tr Standard.

Specifications:
1. Native Fullscreen 1080x1920 (9:16) - ZERO split screen, ZERO gaming footage.
2. Authentic 1080p/4K logoless source footage from Fight Club.
3. Calm, authoritative ElevenLabs Alp baritone narration (documentary cadence).
4. Deeply ducked ambient score (-26 dB under voice) with lowpass filter.
5. Minimalist centered subtitles (Montserrat Black, white text + subtle gold emphasis).
6. High-value psychological cinema essay: "Tyler Durden Neden Asla Saat Takmaz?".
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VOICE_PATH = OUTPUT_DIR / "tyler_durden_benchmark_voice.mp3"
MASTER_VIDEO = OUTPUT_DIR / "BENCHMARK_TYLER_DURDEN_MASTERPIECE.mp4"
ASS_SUBTITLES = OUTPUT_DIR / "benchmark_tyler_subtitles.ass"
PREVIEW_IMG = OUTPUT_DIR / "preview_benchmark_tyler.jpg"
META_PATH = OUTPUT_DIR / "BENCHMARK_TYLER_DURDEN_MASTERPIECE_meta.json"

TYLER_SRC = BASE_DIR / "assets" / "cinematic" / "tyler_logoless_4k.mp4.webm"
FC_SRC = BASE_DIR / "assets" / "cinematic" / "fight_club_master.mp4"
BG_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"
SFX_SUB = BASE_DIR / "assets" / "sfx" / "sub_boom_808.mp3"

TOTAL_DUR = 36.32


def generate_subtitles():
    print("✍️ 1. Minimalist Sinematik Altyazı İnşa Ediliyor (Montserrat Black / Centered)...")

    # Mapped directly to silence boundaries of Alp voiceover
    chunks = [
        # Cümle 1 (0.0 - 5.5s)
        (0.15, 1.55, "FIGHT CLUB'I", False),
        (1.55, 2.20, "DİKKATLE İZLEDİYSENİZ,", False),
        (2.20, 3.40, "TYLER DURDEN'IN KOLUNDA", False),
        (3.40, 4.40, "ASLA BİR SAAT", True),
        (4.40, 5.50, "GÖREMEZSİNİZ.", False),

        # Cümle 2 (5.5 - 7.0s)
        (5.70, 7.00, "BU BİR TESADÜF DEĞİL.", True),

        # Cümle 3 (7.0 - 14.5s)
        (7.20, 8.25, "MODERN İNSAN,", False),
        (8.25, 9.30, "SEVMEDİĞİ İŞLERDE ÇALIŞIR;", False),
        (10.05, 11.65, "İHTİYACI OLMAYAN ŞEYLERİ SATIN ALIR;", False),
        (12.20, 13.95, "VE HOŞLANMADIĞI İNSANLARI", False),
        (13.95, 14.70, "ETKİLEMEYE ÇALIŞIR.", True),

        # Cümle 4 (14.7 - 19.0s)
        (14.90, 16.40, "ZAMANI KONTROL ETTİĞİNİ SANIRKEN,", False),
        (16.85, 18.45, "ASLINDA EŞYALARININ", False),
        (18.45, 19.20, "KÖLESİ HALİNE GELİR.", True),

        # Cümle 5 (19.2 - 25.8s)
        (19.40, 21.00, "TYLER DURDEN BU YANILSAMAYI", False),
        (21.00, 22.50, "TEK BİR CÜMLEYLE YIKAR:", False),
        (22.75, 24.20, "\"SAHİP OLDUĞUN ŞEYLER,", True),
        (24.20, 25.80, "GÜN GELİR SENİN SAHİBİN OLUR.\"", True),

        # Cümle 6 (25.8 - 31.8s)
        (26.25, 27.80, "GERÇEK GÜÇ,", False),
        (27.80, 29.50, "DAHA FAZLASINA SAHİP OLMAKTA DEĞİL;", False),
        (29.75, 31.60, "HİÇBİR ŞEYE MUHTAÇ OLMAMAKTA SAKLIDIR.", True),

        # Cümle 7 (31.8 - 36.32s)
        (32.05, 33.30, "PEKİ SEN...", False),
        (33.35, 34.60, "EŞYALARININ EFENDİSİ MİSİN,", False),
        (34.60, 36.32, "YOKSA KÖLESİ Mİ?", True)
    ]

    def fmt_time(t):
        m = int(t // 60)
        s = t % 60
        return f"0:{m:02d}:{s:05.2f}"

    ass_lines = [
        "[Script Info]",
        "Title: Benchmark Tyler Durden Masterpiece",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: CineMain,Montserrat Black,50,&H00FFFFFF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,3.2,1.5,2,40,40,440,1",
        "Style: CineGold,Montserrat Black,52,&H003AD5FF,&H00000000,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,3.5,2.0,2,40,40,440,1",
        "Style: CineWatermark,Montserrat Medium,26,&H60FFFFFF,&H00000000,&H40000000,&H00000000,0,0,0,0,100,100,4,0,1,1.5,0,2,30,30,220,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 1,0:00:00.00,{fmt_time(TOTAL_DUR)},CineWatermark,,0,0,0,,{{\\an2}}Z İ R V E N İ N   K A N U N U"
    ]

    for start_t, end_t, text, is_gold in chunks:
        style = "CineGold" if is_gold else "CineMain"
        ass_lines.append(
            f"Dialogue: 0,{fmt_time(start_t)},{fmt_time(end_t)},{style},,0,0,0,,{text}"
        )

    with open(ASS_SUBTITLES, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

    print(f"✅ Altyazı hazır: {ASS_SUBTITLES.name}")


def render_master_video():
    print("🎬 2. 1080x1920 Fullscreen Sinematik Kurgu & Render Başlatılıyor...")

    ass_path_escaped = str(ASS_SUBTITLES).replace("\\", "/").replace(":", "\\:")

    # 6 Dynamic Shot Cuts (Authentic 1080p source clips):
    shots = [
        (TYLER_SRC, 58.0, 5.5),    # Shot 1: Tyler in bar, smoking cigarette, calm authority
        (FC_SRC, 41.5, 6.0),       # Shot 2: Cubicle office rat race, photocopying, drinking coffee
        (FC_SRC, 21.8, 7.5),       # Shot 3: Norton in chair looking at illuminated skyscrapers at night
        (TYLER_SRC, 118.0, 6.5),   # Shot 4: Tyler extreme close-up with chemical burn hand smirk
        (TYLER_SRC, 177.5, 6.0),   # Shot 5: Tyler in leather jacket, Raymond Hessel alley confrontation
        (TYLER_SRC, 62.5, 4.82)    # Shot 6: Tyler looking straight into camera with intense piercing stare
    ]

    shot_files = []
    for idx, (src, ss, dur) in enumerate(shots, 1):
        shot_path = OUTPUT_DIR / f"bench_fc_shot_{idx}.mp4"
        shot_files.append(shot_path)
        print(f"   ✂️ Kesit {idx}/6 hazırlanıyor ({dur:.1f}s)...")
        cut_cmd = [
            "ffmpeg", "-y",
            "-ss", str(ss),
            "-t", str(dur),
            "-i", str(src),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-an", str(shot_path)
        ]
        subprocess.run(cut_cmd, check=True)

    print("   🎞️ 6 Kesit Birleştiriliyor ve Sinematik Renk & Altyazı Giydiriliyor...")
    concat_inputs = []
    filter_inputs = ""
    for idx, sp in enumerate(shot_files):
        concat_inputs.extend(["-i", str(sp)])
        filter_inputs += f"[{idx}:v]"

    audio_idx = len(shot_files)
    video_filter = (
        f"{filter_inputs}concat=n={len(shot_files)}:v=1:a=0[raw_montage];"
        f"[raw_montage]curves=all='0/0 0.5/0.46 1/1',vignette=PI/4,subtitles='{ass_path_escaped}'[v_master];"
        f"[{audio_idx}:a]volume=1.05,equalizer=f=120:t=q:w=1:g=1.5,equalizer=f=3400:t=q:w=1:g=1.2[v_voice];"
        f"[{audio_idx+1}:a]volume=0.045,lowpass=f=4000,highpass=f=80[v_music];"
        f"[{audio_idx+2}:a]volume=0.45[v_sub];"
        f"[v_voice][v_music][v_sub]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-15.0:TP=-1.0:LRA=7[a_master]"
    )

    cmd = [
        "ffmpeg", "-y"
    ] + concat_inputs + [
        "-i", str(VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_SUB),
        "-filter_complex", video_filter,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{TOTAL_DUR:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        str(MASTER_VIDEO)
    ]

    subprocess.run(cmd, check=True)
    print(f"✅ Video başarıyla render edildi: {MASTER_VIDEO.name}")

    # Extract preview thumbnail frame at t=4.0s (Tyler in bar)
    print("📸 3. Önizleme Ekran Görüntüsü Alınıyor (t=4.0s)...")
    preview_cmd = [
        "ffmpeg", "-y", "-ss", "4.0", "-i", str(MASTER_VIDEO),
        "-vframes", "1", "-q:v", "2", str(PREVIEW_IMG)
    ]
    subprocess.run(preview_cmd, check=True)
    print(f"✅ Önizleme kaydedildi: {PREVIEW_IMG.name}")

    # Save metadata
    meta = {
        "title": "Tyler Durden Neden Asla Saat Takmaz? (Modern Kölelik ve Zihinsel Özgürlük) #shorts",
        "title_tr": "Tyler Durden Neden Asla Saat Takmaz?",
        "hook": "Sahip olduğun şeyler, gün gelir senin sahibin olur.",
        "concept": "Enver Gülşen / Sinemovie Formatı (Dark Cinema Essay)",
        "video_specs": {
            "resolution": "1080x1920",
            "fps": 30,
            "duration": f"{TOTAL_DUR:.2f}s",
            "source": "1080p Fight Club Master & 4K Logoless Scenepack (Fullscreen Vertical 9:16)",
            "cuts": 6,
            "color_grading": "High-contrast Film Curves + Vignette"
        },
        "audio_specs": {
            "mastering": "EBU R128 (-15.0 LUFS, -1.0 dBTP)",
            "voice": "ElevenLabs Alp (Deep Baritone, Documentary Pace)",
            "music": "Mystery Documentary Ambient (-26 dB under voice)",
            "pumping": "Zero (Normalize=0, Dual Bus Mixing)"
        },
        "description": (
            "Fight Club'ı dikkatle izlediyseniz, Tyler Durden'ın kolunda asla bir saat göremezsiniz.\n\n"
            "Bu bir tesadüf değil. Modern insan, sevmediği işlerde çalışıp ihtiyacı olmayan şeyleri satın alırken zamanın kölesi olur.\n\n"
            "Tyler Durden bu yanılsamayı tek bir cümleyle yıkar: 'Sahip olduğun şeyler, gün gelir senin sahibin olur.'\n\n"
            "Gerçek güç, daha fazlasına sahip olmakta değil; hiçbir şeye muhtaç olmamakta saklıdır.\n\n"
            "#tylerdurden #fightclub #bradpitt #karanlıkpsikoloji #stoacılık #özgürlük #felsefe #sinema #shorts"
        ),
        "tags": [
            "Tyler Durden", "Fight Club", "Brad Pitt", "Dövüş Kulübü", "Karanlık Psikoloji",
            "Stoacılık", "48 Güç Yasası", "Zirvenin Kanunu", "Özgürlük", "Modern Kölelik",
            "Film Analizi", "Sinema", "Shorts"
        ],
        "master_file": str(MASTER_VIDEO),
        "preview_image": str(PREVIEW_IMG),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"✅ Metadata hazırlandı: {META_PATH.name}")


def main():
    generate_subtitles()
    render_master_video()
    print("\n👑 [TAMAMLANDI] Benchmark Tyler Durden Sinematik Başyapıtı Hazır!")


if __name__ == "__main__":
    main()
