"""
Produce Viral Trending Short: TUSAŞ AI Defense Strategy & Rule 6.
Combines:
- Top Half (1080x960): Matrix / Cyber Master 4K cinematic clip
- Bottom Half (1080x960): Dark Cyber Tunnel / 60 FPS loop
- Golden Separator Bar: 8px gold@0.95
- Kinetic Word-by-Word ASS Subtitles: Alex Hormozi dynamic animated bouncing text
- Audio: Studio Alp Baritone narration + Ducked Ambient Music + Sound FX (impact, whoosh, riser)
- Mastering: EBU R128 (-15 LUFS, -0.7 dB peak)
- Interactive CTA: 'Yapay zeka esaret mi özgürlük mü? Yoruma yaz! 👇'
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

TOP_VIDEO = BASE_DIR / "assets" / "cinematic" / "matrix_master.mp4"
BOTTOM_VIDEO = BASE_DIR / "assets" / "stock" / "beamng_crash.mp4"
VOICE_PATH = OUTPUT_DIR / "tusas_voice_alp.mp3"
BG_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"
SFX_IMPACT = BASE_DIR / "assets" / "sfx" / "impact_sub.mp3"
SFX_WHOOSH = BASE_DIR / "assets" / "sfx" / "cinematic_whoosh.mp3"
SFX_RISER = BASE_DIR / "assets" / "sfx" / "riser_cinematic.mp3"
ASS_SUBTITLES = OUTPUT_DIR / "viral_tusas_trend_subtitles.ass"
MASTER_VIDEO = OUTPUT_DIR / "VIRAL_TUSAS_TREND_MASTER.mp4"

# Timed dialogue script matching tusas_voice_alp.mp3 (30.51s total)
TIMED_SCRIPT = [
    ("Tusaş gündemi kasıp kavururken, arkasındaki acımasız güç hamlesini kimse görmedi.", 0.0, 5.0),
    ("Herkes manşetleri tartışıyor, ancak kalabalıklar sadece bir illüzyonu izliyor.", 5.0, 10.0),
    ("Zirvedekiler için krizler felaket değil, güç pekiştirme aracıdır.", 10.0, 14.8),
    ("Kural altı: Her ne pahasına olursa olsun dikkat çek ve tekelleş.", 14.8, 19.5),
    ("Zirvenin Kanunu nettir: Bilgiyi kontrol eden, kitlelerin kaderini kontrol eder.", 19.5, 24.5),
    ("Yapay zeka insanlığı özgürleştirecek mi, yoksa yeni bir dijital esaret mi?", 24.5, 28.5),
    ("Kararını yoruma yaz.", 28.5, 30.5)
]

def create_subtitles() -> Path:
    print("📝 Generating Kinetic ASS Subtitles for TUSAŞ Trend...")
    ass_path = generate_kinetic_word_by_word_ass(
        timed_sentences=TIMED_SCRIPT,
        output_path=ASS_SUBTITLES,
        character_badge="♟️ GÜÇ VE STRATEJİ ♟️",
        cta_text="Esaret mi özgürlük mü? Yoruma yaz! 👇",
        chunk_size=3,
        subtitle_margin_v=460
    )
    print(f"✅ ASS Subtitles: {ass_path}")
    return ass_path

def assemble_and_render():
    ass_file = create_subtitles()

    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(VOICE_PATH)
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    voice_dur = float(res.stdout.strip())
    total_dur = voice_dur + 0.4
    print(f"⏱️ Voice Duration: {voice_dur:.2f}s | Target Duration: {total_dur:.2f}s")

    ass_filter_path = str(ass_file).replace("\\", "/").replace(":", "\\:")

    audio_filter = (
        "[2:a]volume=1.4[voice];"
        "[3:a]volume=0.08,lowpass=f=4500,highpass=f=100[music];"
        "[4:a]volume=0.9[sfx_impact_open];"
        "[4:a]adelay=19500|19500,volume=0.8[sfx_impact_climax];"
        "[5:a]adelay=10000|10000,volume=0.7[sfx_whoosh];"
        "[6:a]adelay=14800|14800,volume=0.6[sfx_riser];"
        "[voice][music][sfx_impact_open][sfx_impact_climax][sfx_whoosh][sfx_riser]"
        "amix=inputs=6:duration=first:dropout_transition=0,"
        "loudnorm=I=-15:TP=-0.7:LRA=7[a_master]"
    )

    video_filter = (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[top];"
        "[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,fps=60[bottom];"
        "[top][bottom]vstack[stacked];"
        f"[stacked]drawbox=y=956:color=gold@0.95:width=1080:height=8:t=fill,ass='{ass_filter_path}'[v_master]"
    )

    filter_complex = f"{video_filter};{audio_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(TOP_VIDEO),
        "-stream_loop", "-1", "-i", str(BOTTOM_VIDEO),
        "-i", str(VOICE_PATH),
        "-stream_loop", "-1", "-i", str(BG_MUSIC),
        "-i", str(SFX_IMPACT),
        "-i", str(SFX_WHOOSH),
        "-i", str(SFX_RISER),
        "-filter_complex", filter_complex,
        "-map", "[v_master]",
        "-map", "[a_master]",
        "-t", f"{total_dur:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-r", "60",
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(MASTER_VIDEO)
    ]

    print("⚡ Starting FFmpeg Master Assembly Rendering...")
    subprocess.run(cmd, check=True)

    if MASTER_VIDEO.exists():
        size_mb = MASTER_VIDEO.stat().st_size / (1024 * 1024)
        print(f"🎉 MASTER TUSAŞ SHORT READY: {MASTER_VIDEO.name} ({size_mb:.2f} MB)")
        return True
    return False

if __name__ == "__main__":
    assemble_and_render()
