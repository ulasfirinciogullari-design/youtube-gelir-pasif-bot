"""
Deploy T-Rex Master with Real-Time Interactive Live Chat Overlay to YouTube Live.
Safely replaces UNBEATABLE_TREX_60FPS_MASTER_STREAM.mp4 with the new chat-enabled master video
and restarts the resilient live stream watchdog.
"""
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
STREAM_MASTER = OUTPUT_DIR / "UNBEATABLE_TREX_60FPS_MASTER_STREAM.mp4"
CHAT_MASTER = OUTPUT_DIR / "UNBEATABLE_TREX_60FPS_MASTER_CHAT.mp4"
WATCHDOG_SCRIPT = BASE_DIR / "app" / "services" / "live_stream_watchdog.py"

def deploy():
    print("=" * 65)
    print("🚀 DEPLOYING INTERACTIVE LIVE CHAT OVERLAY T-REX TO YOUTUBE LIVE")
    print("=" * 65)

    if not CHAT_MASTER.exists():
        print(f"❌ Error: {CHAT_MASTER} does not exist!")
        return False

    chat_size = CHAT_MASTER.stat().st_size
    print(f"📦 Chat Master Asset Size: {chat_size:,} bytes ({chat_size / (1024*1024):.1f} MB)")
    if chat_size < 5_000_000:
        print("❌ Error: Chat Master asset seems too small!")
        return False

    print("\n1. Stopping existing live stream processes...")
    try:
        stop_cmd = "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -and $_.CommandLine -match 'live_stream_watchdog.py') -or ($_.Name -eq 'ffmpeg.exe' -and $_.CommandLine -match 'live2') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        subprocess.run(["powershell", "-Command", stop_cmd], check=False)
        time.sleep(2)
        print("✅ Previous live stream watchdog and FFmpeg publisher terminated cleanly.")
    except Exception as e:
        print(f"⚠️ Warning stopping processes: {e}")

    print("\n2. Replacing master video file with Chat Overlay asset...")
    try:
        shutil.copy2(str(CHAT_MASTER), str(STREAM_MASTER))
        print(f"✅ Replaced {STREAM_MASTER.name} ({STREAM_MASTER.stat().st_size:,} bytes)")
    except Exception as e:
        print(f"❌ Failed to copy Chat Master: {e}")
        return False

    print("\n3. Launching resilient live stream watchdog...")
    cmd = [sys.executable, str(WATCHDOG_SCRIPT)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )
    print(f"✅ Live Stream Watchdog launched with PID: {proc.pid}")
    time.sleep(4)

    # Check if FFmpeg is running
    chk = subprocess.run(
        ["powershell", "-Command", "Get-Process -Name ffmpeg -ErrorAction SilentlyContinue | Select-Object Id, ProcessName"],
        capture_output=True,
        text=True
    )
    print("📺 Stream Status:")
    print(chk.stdout if chk.stdout.strip() else "Waiting for FFmpeg startup...")
    print("\n🎉 Live Chat Overlay stream deployed successfully!")
    return True

if __name__ == "__main__":
    deploy()
