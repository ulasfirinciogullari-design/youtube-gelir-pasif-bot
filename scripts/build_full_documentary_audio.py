import sys
import json
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.services.global_dubbing_engine import GlobalDubbingEngine

def build_full_documentary_audio():
    spec_file = BASE_DIR / "output" / "master_longform_documentary_spec.json"
    with open(spec_file, "r", encoding="utf-8") as f:
        spec = json.load(f)

    engine = GlobalDubbingEngine()
    audio_dir = BASE_DIR / "output" / "longform_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    chapter_files = []
    print("\n" + "=" * 70)
    print("👑 UZUN METRAJ BELGESEL SESLENDİRMESİ BAŞLATILIYOR (ELEVENLABS ALP)")
    print(f"🎬 Belgesel: {spec['title']}")
    print("=" * 70)

    for ch in spec["chapters"]:
        c_num = ch["chapter_num"]
        c_title = ch["title"]
        c_script = ch["script"]
        out_file = audio_dir / f"chapter_{c_num:02d}.mp3"
        
        if out_file.exists() and out_file.stat().st_size > 10000:
            print(f"⏩ Bölüm {c_num} zaten mevcut, atlanıyor: {out_file.name}")
        else:
            print(f"\n🎙️ Bölüm {c_num}: {c_title}")
            engine.synthesize(c_script, lang="tr", output_path=str(out_file))
            time.sleep(1.0)
            
        chapter_files.append(out_file)

    # Concat all chapters into one master audio track using FFmpeg concat filter
    concat_list = audio_dir / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for cf in chapter_files:
            # Escape path for ffmpeg
            f.write(f"file '{cf.resolve().as_posix()}'\n")

    master_audio = BASE_DIR / "output" / "DOCUMENTARY_MASTER_VOICE.mp3"
    print(f"\n🎛️ Tüm bölümler tek bir master ses dosyasında birleştiriliyor...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(master_audio)
    ], check=True)

    print(f"\n🎉 MASTER BELGESEL SESİ TAMAMLANDI!")
    print(f"📁 Dosya: {master_audio.name} ({master_audio.stat().st_size} bayt)")
    return master_audio

if __name__ == "__main__":
    build_full_documentary_audio()
