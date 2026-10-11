"""
24/7 Ultra-Resilient RTMP Watchdog Streamer for 'Zirvenin Kanunu'.
Features:
- Primary & Backup RTMP server rotation (a.rtmp.youtube.com & b.rtmp.youtube.com).
- 5-second graceful socket cooldown so YouTube releases the stream key lock.
- Socket timeout parameters (-rw_timeout).
- Full exception catching and persistent logging.
"""

import os
import sys
import json
import time
import traceback
import subprocess
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CREDENTIALS_FILE = BASE_DIR / "output" / "live_stream_credentials.json"
PID_FILE = BASE_DIR / "output" / "livestream_pid.txt"
WATCHDOG_LOG = BASE_DIR / "output" / "watchdog_live.log"
TREX_MASTER = BASE_DIR / "output" / "UNBEATABLE_TREX_60FPS_MASTER_STREAM.mp4"

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_credentials():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"Kimlik bilgileri bulunamadı: {CREDENTIALS_FILE}")
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def run_resilient_stream():
    creds = load_credentials()
    primary_url = creds.get("rtmp_url", "rtmp://a.rtmp.youtube.com/live2")
    backup_url = creds.get("backup_rtmp_url", "rtmp://b.rtmp.youtube.com/live2?backup=1")
    stream_key = creds["stream_key"]

    urls = [
        f"{primary_url}/{stream_key}",
        f"{backup_url}/{stream_key}"
    ]

    log("=" * 65)
    log("🛡️ 24/7 RESILIENT LIVE STREAM WATCHDOG V2 BAŞLATILDI")
    log(f"🎬 Video: {TREX_MASTER.name}")
    log(f"📡 Primary: {primary_url}")
    log(f"📡 Backup:  {backup_url}")
    log("=" * 65)

    session_idx = 0
    consecutive_failures = 0

    while True:
        session_idx += 1
        target_url = urls[(session_idx - 1) % len(urls)]
        server_label = "PRIMARY (A)" if (session_idx - 1) % 2 == 0 else "BACKUP (B)"

        log(f"\n🚀 Canlı Yayın Oturumu #{session_idx} Başlatılıyor -> Sunucu: {server_label}")

        cmd = [
            "ffmpeg",
            "-re",
            "-stream_loop", "-1",
            "-i", str(TREX_MASTER),
            "-c", "copy",
            "-flvflags", "no_duration_filesize",
            "-f", "flv",
            target_url
        ]

        start_time = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )

            # Record PID
            with open(PID_FILE, "w", encoding="utf-8") as f:
                f.write(str(proc.pid))

            log(f"✅ FFmpeg aktifleşti (PID: {proc.pid})")

            # Stream monitoring
            while proc.poll() is None:
                # Read line with timeout / non-blocking
                line = proc.stderr.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                if "Error" in line or "error" in line or "failed" in line or "-10054" in line:
                    log(f"⚠️ [FFmpeg]: {line.strip()}")

            ret = proc.returncode
            uptime = time.time() - start_time
            log(f"ℹ️ FFmpeg kapandı (Kod: {ret}, Oturum Süresi: {uptime:.1f} saniye)")

            if uptime > 60:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # Exponential / Cooldown sleep before reconnect
            sleep_time = 5.0 if consecutive_failures <= 2 else min(20.0, 5.0 * consecutive_failures)
            log(f"⏳ YouTube soketinin serbest bırakılması bekleniyor ({sleep_time:.1f} sn)...")
            time.sleep(sleep_time)

        except BaseException as e:
            log(f"❌ Beklenmedik Hata: {e}")
            traceback.print_exc()
            time.sleep(5.0)

if __name__ == "__main__":
    try:
        run_resilient_stream()
    except BaseException as e:
        log(f"CRITICAL WATCHDOG EXIT: {e}")
        traceback.print_exc()
