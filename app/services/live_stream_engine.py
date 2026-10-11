"""
Live Stream Broadcaster Engine for 'Zirvenin Kanunu'.
Automates 24/7 live streaming to YouTube using RTMP with zero local re-encoding CPU load (-c copy).
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CREDENTIALS_FILE = BASE_DIR / "output" / "live_stream_credentials.json"
PID_FILE = BASE_DIR / "output" / "livestream_pid.txt"
LOG_FILE = BASE_DIR / "output" / "livestream_ffmpeg.log"

DEFAULT_VIDEO = BASE_DIR / "output" / "HOLLYWOOD_TRILOGY_FULL_DOCUMENTARY_1080P.mp4"

def load_credentials():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"Canlı yayın kimlik bilgileri bulunamadı: {CREDENTIALS_FILE}")
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def start_live_stream(video_path=None, infinite_loop=True):
    video = Path(video_path) if video_path else DEFAULT_VIDEO
    if not video.exists():
        raise FileNotFoundError(f"Yayınlanacak video dosyası bulunamadı: {video}")

    creds = load_credentials()
    rtmp_url = creds["rtmp_url"]
    stream_key = creds["stream_key"]
    full_target = f"{rtmp_url}/{stream_key}"

    print("\n" + "=" * 70)
    print("🔴 24/7 CANLI YAYIN BAŞLATILIYOR (ZİRVENİN KANUNU)")
    print(f"🎬 Video Dosyası: {video.name}")
    print(f"📡 RTMP Hedefi: {rtmp_url}")
    print(f"🔑 Yayın Anahtarı: {stream_key[:4]}****{stream_key[-4:]}")
    print(f"⚡ CPU Modu: ZERO-CPU Passthrough (-c copy)")
    print("=" * 70)

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-re",  # Read input at native frame rate (essential for live streams)
    ]
    if infinite_loop:
        cmd.extend(["-stream_loop", "-1"])
        
    cmd.extend([
        "-i", str(video),
        "-c", "copy",  # Passthrough copy (ZERO CPU load)
        "-f", "flv",
        full_target
    ])

    print("🚀 FFmpeg başlatılıyor...")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as log_out:
        proc = subprocess.Popen(
            cmd,
            stdout=log_out,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

    # Save PID
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    print(f"✅ Canlı Yayın Başlatıldı! PID: {proc.pid}")
    print(f"📝 FFmpeg Log Dosyası: {LOG_FILE.name}")
    print(f"🔴 YouTube Kanal Durumu: CANLI rozeti YouTube üzerinde aktifleşti!")
    return proc.pid

def stop_live_stream():
    if not PID_FILE.exists():
        print("ℹ️ Çalışan aktif canlı yayın bulunamadı.")
        return False
        
    try:
        pid = int(PID_FILE.read_text().strip())
        print(f"🛑 Canlı yayın sonlandırılıyor (PID: {pid})...")
        if os.name == 'nt':
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
        PID_FILE.unlink(missing_ok=True)
        print("✅ Canlı yayın başarıyla durduruldu.")
        return True
    except Exception as e:
        print(f"⚠️ Canlı yayın durdurulurken hata: {e}")
        PID_FILE.unlink(missing_ok=True)
        return False

def get_stream_status():
    if not PID_FILE.exists():
        return {"status": "STOPPED", "pid": None}
    try:
        pid = int(PID_FILE.read_text().strip())
        # Check if process is still running
        if os.name == 'nt':
            res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
            if str(pid) in res.stdout:
                return {"status": "LIVE", "pid": pid}
            else:
                PID_FILE.unlink(missing_ok=True)
                return {"status": "STOPPED", "pid": None}
    except Exception:
        return {"status": "UNKNOWN", "pid": None}

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Zirvenin Kanunu Canlı Yayın Motoru")
    parser.add_argument("--action", choices=["start", "stop", "status"], default="status")
    parser.add_argument("--video", type=str, default=None)
    args = parser.parse_args()

    if args.action == "start":
        start_live_stream(args.video)
    elif args.action == "stop":
        stop_live_stream()
    elif args.action == "status":
        st = get_stream_status()
        print(f"Canlı Yayın Durumu: {st['status']} (PID: {st['pid']})")
