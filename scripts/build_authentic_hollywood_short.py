"""
Authentic Hollywood Cinema Short Generator.
Uses REAL original actor voices (Bryan Cranston, Cillian Murphy) from master clips,
adds cinematic dual-language subtitles (EN spoken + TR meaning),
neon bottom progress bar, series badge HUD, and broadcast sound design.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.cinema_voice_master import ICONIC_CINEMA_CLIPS, build_dual_language_ass, extract_original_hollywood_audio
from app.services.visual_fx_engine import generate_neon_progress_bar_filter, inject_badge_into_ass
from app.services.video_quality_analyst import audit_short_quality, print_audit_report


def build_authentic_short(campaign_id: str = "shelby_power") -> Path:
    if campaign_id not in ICONIC_CINEMA_CLIPS:
        campaign_id = "shelby_power"

    info = ICONIC_CINEMA_CLIPS[campaign_id]
    clip_path = BASE_DIR / "assets" / "cinematic" / info["source"]
    if not clip_path.exists():
        raise FileNotFoundError(f"Master klip bulunamadı: {clip_path}")

    timestamp = int(time.time())
    work_dir = BASE_DIR / "output" / f"work_authentic_{campaign_id}_{timestamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print(f"🎬 GERÇEK HOLLYWOOD OYUNCU SESİ VE DUBLAJ ŞAHESERİ: {info['character']}")
    print(f"📁 Kaynak: {clip_path.name} | Mod: Orijinal Aktör Sesi + Çift Dilli Altyazı")
    print("=" * 70)

    # 1. Extract and polish original actor dialogue
    dialogues_en = info["iconic_dialogue_en"]
    total_dur = dialogues_en[-1][2] + 1.0  # seconds

    raw_voice = work_dir / "actor_voice_raw.mp3"
    extract_original_hollywood_audio(clip_path, raw_voice, start_sec=2.0, duration=total_dur)
    print(f"🎙️ [1/4] Orijinal Hollywood Aktör Sesi İzolasyonu: {total_dur:.1f}s")

    # 2. Mix with Hans Zimmer / Dark Ambient Music (-14 LUFS Loudness)
    music_candidates = sorted((BASE_DIR / "assets" / "music").glob("*.mp3"))
    music_file = music_candidates[0] if music_candidates else None

    master_audio = work_dir / "master_hollywood_soundtrack.mp3"
    if music_file and music_file.exists():
        # Mix voice (dominant) + music (-16dB ducked) + loudnorm
        filter_complex = (
            "[0:a]volume=1.4[voice];"
            "[1:a]volume=0.25,aloop=loop=-1:size=2e+09[bg];"
            "[voice][bg]amix=inputs=2:duration=first:dropout_transition=2,"
            "loudnorm=I=-14:TP=-1.0:LRA=9[out]"
        )
        cmd_mix = [
            "ffmpeg", "-y",
            "-i", str(raw_voice),
            "-i", str(music_file),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", f"{total_dur:.3f}",
            str(master_audio)
        ]
        subprocess.run(cmd_mix, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # Loudnorm single voice
        cmd_mix = [
            "ffmpeg", "-y",
            "-i", str(raw_voice),
            "-af", "loudnorm=I=-14:TP=-1.0:LRA=9",
            "-t", f"{total_dur:.3f}",
            str(master_audio)
        ]
        subprocess.run(cmd_mix, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("🎵 [2/4] Nöro-Akustik Miksaj ve -14 LUFS Yayın Seviyesi Tamamlandı.")

    # 3. Build Dual-Language Kinetik Subtitles (EN Spoken + TR Meaning)
    ass_file = work_dir / "dual_lang_subtitles.ass"
    build_dual_language_ass(dialogues_en, info["dialogue_tr_meaning"], ass_file)
    badge_title = info["character"].split("(")[0].strip()
    inject_badge_into_ass(ass_file, badge_title, total_dur)
    print("✍️ [3/4] Çift Dilli Kinetik Altyazı & Seri Rozeti Hazırlandı.")

    # Copy ASS to BASE_DIR for libass path safety
    temp_ass = BASE_DIR / f"temp_burn_hollywood_{timestamp}.ass"
    shutil.copy(ass_file, temp_ass)

    # 4. 60 FPS Cut & Assemble Video
    final_mp4 = BASE_DIR / "output" / f"VIRAL_MASTER_HOLLYWOOD_{campaign_id}_{timestamp}.mp4"
    prog_bar = generate_neon_progress_bar_filter(total_dur)
    vf_filter = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"subtitles='{temp_ass.name}',{prog_bar}"
    )

    cmd_render = [
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
        str(final_mp4)
    ]
    subprocess.run(cmd_render, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(BASE_DIR))
    if temp_ass.exists():
        temp_ass.unlink()

    print(f"🚀 [4/4] 60 FPS Master Render Alındı: {final_mp4.name}")

    # Thumbnail
    thumb_path = final_mp4.with_name(f"{final_mp4.stem}_thumb.jpg")
    subprocess.run([
        "ffmpeg", "-y", "-ss", "00:00:03.00",
        "-i", str(final_mp4),
        "-vframes", "1", "-q:v", "2",
        str(thumb_path)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Metadata
    meta_path = final_mp4.with_name(f"{final_mp4.stem}_meta.json")
    meta = {
        "title_options": [
            f"👑 {info['character']} | Orijinal Ses & Güç Yasası #shorts",
            f"🔥 {info['character']} | The Cold Truth #shorts"
        ],
        "description": (
            f"Hollywood tarihinin en unutulmaz sahnesi. {info['character']} orijinal sesiyle.\n"
            "İki dilli özel çeviri ve -14 LUFS sinematik miksaj.\n\n"
            "#shorts #cillianmurphy #peakyblinders #thomasshelby #motivasyon #sinema #sigma"
        ),
        "tags": ["thomas shelby", "peaky blinders", "cillian murphy", "orijinal ses", "dizi replikleri", "shorts", "motivasyon"],
        "lang": "en",
        "defaultLanguage": "en",
        "defaultAudioLanguage": "en",
        "pinned_comment": "💬 \"Korku seni tahmin edilebilir yapar.\" Thomas Shelby'nin bu sözüne katılıyor musunuz? Fikrinizi yazın!",
        "duration_seconds": round(total_dur, 2),
        "fps": 60.0
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 5. Quality Audit
    audit = audit_short_quality(final_mp4)
    print_audit_report(audit)

    return final_mp4


if __name__ == "__main__":
    cid = sys.argv[1] if len(sys.argv) > 1 else "shelby_power"
    build_authentic_short(cid)
