"""
Cinema Sound FX Engine.
Generates broadcast-grade neuro-acoustic impacts (808 Sub-Boom, Cinematic Whoosh,
and Pattern-Interrupt Tension Risers) dynamically using pure FFmpeg audio synthesis.
Zero external audio files required, 100% offline, $0 cost.
"""

from pathlib import Path
import subprocess
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SFX_DIR = BASE_DIR / "assets" / "sfx"
SFX_DIR.mkdir(parents=True, exist_ok=True)


def ensure_cinematic_sfx():
    """Generates broadcast sound FX if they do not exist yet."""
    sub_boom = SFX_DIR / "sub_boom_808.mp3"
    whoosh = SFX_DIR / "cinematic_whoosh.mp3"
    riser = SFX_DIR / "tension_riser.mp3"

    # 1. 808 Sub-Bass Impact Boom (50Hz sinewave with exponential decay + lowpass)
    if not sub_boom.exists():
        cmd_boom = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=55:duration=1.8",
            "-af", "afade=t=out:st=0.1:d=1.7,lowpass=f=120,volume=2.5",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(sub_boom)
        ]
        subprocess.run(cmd_boom, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. Cinematic Swoosh / Pattern Interrupt (White noise sweep with bandpass filter)
    if not whoosh.exists():
        cmd_whoosh = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "anoisesrc=d=0.7:c=white:r=44100:a=0.5",
            "-af", "bandpass=f=1200:w=800,afade=t=in:st=0:d=0.3,afade=t=out:st=0.3:d=0.4,volume=1.8",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(whoosh)
        ]
        subprocess.run(cmd_whoosh, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. Tension Riser (Pitch ascending frequency sweep for cliffhanger)
    if not riser.exists():
        cmd_riser = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=80:duration=2.2",
            "-af", "asetrate=44100*1.5,atempo=1.5,afade=t=in:st=0:d=1.5,afade=t=out:st=1.5:d=0.7,volume=1.5",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(riser)
        ]
        subprocess.run(cmd_riser, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return {
        "sub_boom": sub_boom,
        "whoosh": whoosh,
        "riser": riser
    }


if __name__ == "__main__":
    effects = ensure_cinematic_sfx()
    print("🔊 Ses Efektleri Motoru Hazır:")
    for k, v in effects.items():
        print(f"  - {k}: {v.name} (Boyut: {v.stat().st_size} bytes)")
