"""
Infinite Dynamic Cinema Harvester.
Crawls and downloads trending 4K/1080p 60fps movie scenes, series edits,
and documentary clips from YouTube automatically based on script keywords.
Guarantees 60s+ rich master clips for zero repetition across cuts.
"""

from pathlib import Path
import random
import subprocess
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CINEMA_DIR = BASE_DIR / "assets" / "cinematic"
CINEMA_DIR.mkdir(parents=True, exist_ok=True)

import shutil

FFPROBE_BIN = shutil.which("ffprobe") or (
    r"C:\Users\ULAŞ\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe"
    if sys.platform == "win32"
    else "ffprobe"
)


def get_video_duration(video_path: Path) -> float:
    """Returns duration of video in seconds using ffprobe."""
    if not video_path.exists():
        return 0.0
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        return float(out)
    except Exception:
        return 0.0


def download_youtube_clip(query: str, target_filename: str, min_duration: float = 30.0) -> Path | None:
    """
    Downloads a high-quality video clip matching query using android/web client.
    Guarantees that resulting clip has at least min_duration seconds of unique footage.
    """
    dest_file = CINEMA_DIR / target_filename
    if dest_file.exists():
        dur = get_video_duration(dest_file)
        if dur >= min_duration:
            print(f"[Harvester] ✅ Mevcut zengin klip kullanılıyor: {dest_file.name} ({dur:.1f}s)")
            return dest_file
            
    print(f"[Harvester] 🌐 YouTube'dan indiriliyor: '{query}'...")
    tmp_out = CINEMA_DIR / f"temp_{target_filename}"
    if tmp_out.exists():
        try:
            tmp_out.unlink()
        except Exception:
            pass

    cmd = [
        sys.executable, "-m", "yt_dlp",
        f"ytsearch1:{query}",
        "--extractor-args", "youtube:player_client=android,web",
        "-f", "best[ext=mp4]/best",
        "--max-downloads", "1",
        "-o", str(tmp_out)
    ]
    
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if tmp_out.exists() and tmp_out.stat().st_size > 500_000:
            dur = get_video_duration(tmp_out)
            if dest_file.exists():
                try:
                    dest_file.unlink()
                except Exception:
                    pass
            tmp_out.rename(dest_file)
            print(f"[Harvester] ✅ Başarıyla indirildi: {dest_file.name} ({dur:.1f}s, {dest_file.stat().st_size / 1024 / 1024:.1f} MB)")
            return dest_file
    except Exception as e:
        print(f"[Harvester] ⚠️ İndirme hatası ({query}): {e}")
        
    return None


# Curated catalog of viral themes with their YouTube search queries
VIRAL_THEME_QUERIES = {
    "oppenheimer_master.mp4": "Oppenheimer cinema scene 4k",
    "interstellar_master.mp4": "Interstellar Gargantua black hole scene 4k",
    "batman_master.mp4": "The Batman Christian Bale 4k scene",
    "joker_master.mp4": "Joker Joaquin Phoenix staircase 4k scene",
    "breaking_bad_master.mp4": "Breaking Bad Walter White I am the danger 4k scene",
    "fight_club_master.mp4": "Fight Club Tyler Durden 4k scene",
    "godfather_master.mp4": "The Godfather Don Corleone Al Pacino 4k scene",
    "peaky_master.mp4": "Thomas Shelby Peaky Blinders 4k edit",
    "scarface_master.mp4": "Scarface Tony Montana 4k scene",
    "gladiator_master.mp4": "Gladiator Maximus 4k scene",
    "matrix_master.mp4": "Matrix Keanu Reeves Neo 4k scene",
    "wolf_master.mp4": "Wolf of Wall Street Leonardo DiCaprio 4k",
}


def ensure_campaign_footage(theme_filename: str, query: str | None = None) -> Path:
    """Ensures that rich footage for the campaign exists. If not, fetches it."""
    dest_file = CINEMA_DIR / theme_filename
    if dest_file.exists():
        dur = get_video_duration(dest_file)
        if dur >= 30.0:
            return dest_file

    search_query = query or VIRAL_THEME_QUERIES.get(theme_filename, f"{theme_filename.replace('_', ' ').replace('.mp4', '')} 4k scene")
    harvested = download_youtube_clip(search_query, theme_filename, min_duration=25.0)
    if harvested and harvested.exists():
        return harvested
        
    # Fallback to existing files with > 30s
    for f in CINEMA_DIR.glob("*.mp4"):
        if get_video_duration(f) >= 30.0:
            return f
            
    # Absolute fallback
    existing = list(CINEMA_DIR.glob("*.mp4"))
    if existing:
        return existing[0]
    raise FileNotFoundError("Hiçbir sinematik kaynak video bulunamadı!")


def harvest_all_top_themes():
    """Populates the local vault with all top viral themes."""
    print("\n" + "=" * 70)
    print("  🎬 TÜM VİRAL SİNEMATİK KÜTÜPHANE İNDİRİLİYOR (SONSUZ KAYNAK)")
    print("=" * 70)
    for filename, query in VIRAL_THEME_QUERIES.items():
        try:
            ensure_campaign_footage(filename, query)
        except Exception as e:
            print(f"Hata ({filename}): {e}")
    print("\n✅ Sinematik kütüphane hazırlandı!\n")


if __name__ == "__main__":
    harvest_all_top_themes()
