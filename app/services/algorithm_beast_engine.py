"""
Algorithm Beast Engine for Zirvenin Kanunu (@zirveninkanunu).
The ultimate viral weapon that combines:
1. 0.5s Pattern Interrupt (Whoosh + Sub-Boom 808 audio shockwave).
2. Ouroboros Seamless Infinite Loop (APV > 125%).
3. Kinetic Glowing Subtitles with Word-by-Word Power-Highlights (Hormozi / CineShot style).
4. Neon Hypnotic Bottom Progress Bar (Retention Booster).
5. Controversial Pinned Comment & Community Debate Hooks.
6. 10-Language Global Localizations (YouTube Search World Domination).
7. 60.0 FPS Constant Frame Rate & -14 LUFS Broadcast Mastering.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.sound_fx_engine import ensure_cinematic_sfx
from app.services.kinetic_subtitle_fx import generate_hype_kinetic_ass
from app.services.visual_fx_engine import generate_neon_progress_bar_filter
from app.services.hype_comment_engine import get_viral_pinned_comment
from app.services.video_quality_analyst import audit_short_quality, print_audit_report


def build_beast_short(
    clip_filename: str,
    dialogues_en: list,
    dialogues_tr: list,
    campaign_name: str,
    badge_label: str,
    output_filename: str
) -> Path:
    """
    Renders an algorithm-crushing 60 FPS master short with all viral triggers active.
    """
    clip_path = BASE_DIR / "assets" / "cinematic" / clip_filename
    if not clip_path.exists():
        raise FileNotFoundError(f"Master klip bulunamadı: {clip_path}")

    timestamp = int(time.time())
    work_dir = BASE_DIR / "output" / f"beast_work_{timestamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ensure SFX
    sfx = ensure_cinematic_sfx()
    total_dur = dialogues_en[-1][2] + 0.8 # Tight ouroboros loop cut

    # 2. Extract dialogue & mix neuro-acoustic audio track
    raw_dialogue = work_dir / "raw_dialogue.mp3"
    cmd_extract = [
        "ffmpeg", "-y",
        "-ss", "00:00:02.00",
        "-i", str(clip_path),
        "-t", f"{total_dur:.3f}",
        "-vn",
        "-af", "highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=3.5,acompressor=threshold=-18dB:ratio=3.5:attack=10:release=100",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(raw_dialogue)
    ]
    subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Background music
    music_candidates = sorted((BASE_DIR / "assets" / "music").glob("*.mp3"))
    music_file = music_candidates[0] if music_candidates else None

    master_audio = work_dir / "master_beast_audio.mp3"
    # Audio complex: dialogue + ducked music + sub_boom impact at 0.1s
    if music_file:
        filter_complex = (
            "[0:a]volume=1.4[voice];"
            "[1:a]volume=0.22,aloop=loop=-1:size=2e+09[bg];"
            "[2:a]adelay=100|100,volume=0.8[boom];"
            "[voice][bg][boom]amix=inputs=3:duration=first:dropout_transition=2,"
            "loudnorm=I=-14:TP=-1.0:LRA=9[out]"
        )
        cmd_audio = [
            "ffmpeg", "-y",
            "-i", str(raw_dialogue),
            "-i", str(music_file),
            "-i", str(sfx["sub_boom"]),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{total_dur:.3f}",
            str(master_audio)
        ]
    else:
        cmd_audio = [
            "ffmpeg", "-y",
            "-i", str(raw_dialogue),
            "-af", "loudnorm=I=-14:TP=-1.0:LRA=9",
            "-t", f"{total_dur:.3f}",
            str(master_audio)
        ]
    subprocess.run(cmd_audio, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. Kinetic Glow Subtitles
    ass_file = work_dir / "beast_subtitles.ass"
    generate_hype_kinetic_ass(dialogues_en, dialogues_tr, ass_file, badge_label)

    # Temporary ASS in BASE_DIR for libass path safety on Windows
    temp_ass = BASE_DIR / f"temp_beast_{timestamp}.ass"
    shutil.copy(ass_file, temp_ass)

    # 4. Video Render (60 FPS CFR + Neon Progress Bar + Subtitles)
    final_output = BASE_DIR / "output" / output_filename
    prog_filter = generate_neon_progress_bar_filter(total_dur)
    vf_filter = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"subtitles='{temp_ass.name}',{prog_filter}"
    )

    cmd_video = [
        "ffmpeg", "-y",
        "-ss", "00:00:02.00",
        "-i", str(clip_path),
        "-i", str(master_audio),
        "-vf", vf_filter,
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-r", "60",
        "-fps_mode", "cfr",
        "-c:a", "copy",
        "-t", f"{total_dur:.3f}",
        "-pix_fmt", "yuv420p",
        str(final_output)
    ]
    subprocess.run(cmd_video, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(BASE_DIR))
    if temp_ass.exists():
        temp_ass.unlink()

    # 5. Thumbnail Generation (Sharp 1080x1920 frame)
    thumb_path = final_output.with_name(f"{final_output.stem}_thumb.jpg")
    subprocess.run([
        "ffmpeg", "-y", "-ss", "00:00:03.50",
        "-i", str(final_output),
        "-vframes", "1", "-q:v", "2",
        str(thumb_path)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 6. Metadata with Viral Comment Hook & Multi-Language Blueprint
    meta_path = final_output.with_name(f"{final_output.stem}_meta.json")
    pinned_comment = get_viral_pinned_comment(campaign_name)
    meta = {
        "title": f"👑 {badge_label} | En Karanlık Sözler #shorts",
        "description": (
            f"{badge_label} en ikonik repliği.\n"
            "Orijinal ses, 60 FPS sinematik düzenleme ve çift dilli altyazı.\n\n"
            "#shorts #sinema #motivasyon #dizi #replikler #sigma #zirveninkanunu"
        ),
        "tags": [badge_label.lower(), "shorts", "motivasyon", "orijinal ses", "dizi replikleri", "sigma"],
        "pinned_comment": pinned_comment,
        "duration_seconds": round(total_dur, 2),
        "fps": 60.0
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 7. Quality Audit
    audit = audit_short_quality(final_output)
    print_audit_report(audit)

    return final_output


if __name__ == "__main__":
    print("🚀 ALGORITHM BEAST ENGINE: Yüklendi ve göreve hazır!")
