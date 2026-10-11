"""
Assembles the complete 1080p Full HD Master Longform Documentary:
'GÖRÜNMEZ GÜCÜN 7 YASASI: Dünyayı Yöneten Zihinlerin Psikolojisi'
Combines:
1. ElevenLabs Alp Native Baritone Voice (DOCUMENTARY_MASTER_VOICE.mp3)
2. Cinematic Documentary Soundtrack (mystery_documentary.mp3 ducked to 18%)
3. Cinematic 1080p footage from Shelby, Walter White, Godfather, Marcus Aurelius, Interstellar
"""

import os
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent

VOICE_AUDIO = BASE_DIR / "output" / "DOCUMENTARY_MASTER_VOICE.mp3"
BGM_MUSIC = BASE_DIR / "assets" / "music" / "mystery_documentary.mp3"
OUTPUT_VIDEO = BASE_DIR / "output" / "GORUNMEZ_GUCUN_7_YASASI_1080P_FULL_DOCUMENTARY.mp4"

def assemble_documentary():
    if not VOICE_AUDIO.exists():
        raise FileNotFoundError(f"Voice track not found: {VOICE_AUDIO}")

    # Gather available cinematic video clips
    clips = [
        BASE_DIR / "output" / "VIRAL_MASTER_HOLLYWOOD_shelby_power_1791495886.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_HOLLYWOOD_breaking_bad_1791496814.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_godfather_rules_1791420227.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_gladiator_stoic_1791425949.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_interstellar_abyss_1791425318.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_HOLLYWOOD_fight_club_tyler.mp4",
        BASE_DIR / "output" / "VIRAL_MASTER_oppenheimer_doom_1791425857.mp4",
    ]
    valid_clips = [c for c in clips if c.exists()]
    if not valid_clips:
        # Fallback to any mp4 in output
        valid_clips = list((BASE_DIR / "output").glob("*.mp4"))[:5]

    print(f"🎬 Kullanılan Sinematik Klipler: {len(valid_clips)} adet")

    # Create concat list for video loops
    concat_txt = BASE_DIR / "output" / "doc_video_concat.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        # Loop clips multiple times to cover documentary duration
        for _ in range(4):
            for c in valid_clips:
                f.write(f"file '{c.resolve().as_posix()}'\n")

    # First, mix voice + music
    mixed_audio = BASE_DIR / "output" / "DOCUMENTARY_MIXED_AUDIO.aac"
    print("\n🎛️ 1. Aşama: Bariton Anlatım + Sinematik Müzik Miksleniyor...")
    mix_cmd = [
        "ffmpeg", "-y",
        "-i", str(VOICE_AUDIO),
        "-stream_loop", "-1", "-i", str(BGM_MUSIC),
        "-filter_complex",
        "[1:a]volume=0.18[bgm]; [0:a]volume=1.05[vox]; [vox][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "256k",
        str(mixed_audio)
    ]
    subprocess.run(mix_cmd, check=True)
    print("✅ Mikslenmiş ses hazır!")

    # Second, combine looped video with mixed audio into 1080p masterpiece
    print("\n🎬 2. Aşama: 1080p Video + Mikslenmiş Ses Bütünleştiriliyor...")
    final_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-i", str(mixed_audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",  # Super fast encoding with zero strain
        "-crf", "20",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p",
        "-c:a", "copy",
        "-shortest",
        str(OUTPUT_VIDEO)
    ]
    subprocess.run(final_cmd, check=True)

    print("\n" + "=" * 70)
    print("👑 1080p FULL METRAJ BELGESEL BAŞARIYLA ÜRETİLDİ!")
    print(f"📁 Dosya: {OUTPUT_VIDEO.name} ({OUTPUT_VIDEO.stat().st_size} bayt)")
    print("=" * 70)
    return OUTPUT_VIDEO

if __name__ == "__main__":
    assemble_documentary()
