"""
Real-Time Dynamic Live Stream & Interactive Voice AI Engine for 'Zirvenin Kanunu'.
Pipes dynamically rendered 1080p 30 FPS game frames + live chat overlays,
alongside real-time ducked audio mixing (Synthwave music + AI voice responses)
directly to YouTube RTMP.
"""

import os
import sys
import json
import time
import queue
import socket
import select
import threading
import subprocess
import numpy as np
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.services.unbeatable_trex_streamer import UnbeatableTRexStreamer
from app.services.live_chat_poller import LiveChatPoller
from app.services.live_voice_responder import get_voice_responder

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = BASE_DIR / "assets" / "music"
CREDENTIALS_FILE = OUTPUT_DIR / "live_stream_credentials.json"
PID_FILE = OUTPUT_DIR / "livestream_pid.txt"
LOG_FILE = OUTPUT_DIR / "livestream_dynamic.log"

WIDTH = 1920
HEIGHT = 1080
FPS = 25
SAMPLE_RATE = 44100
CHANNELS = 2
SAMPLES_PER_FRAME = int(SAMPLE_RATE / FPS)  # 1764 samples (exact integer)


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_credentials():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"Credentials not found: {CREDENTIALS_FILE}")
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_audio_file(file_path):
    """
    Decodes any audio file into 44.1kHz 16-bit stereo PCM numpy float32 [-1.0, 1.0].
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(file_path),
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "-f", "s16le",
        "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    raw = proc.stdout
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.reshape(-1, CHANNELS)


class DynamicAudioMixer:
    """
    Continuous real-time audio mixer with intelligent ducking.
    Mixes looping background synthwave with voice AI responses.
    """
    def __init__(self, bg_music_path=None):
        if bg_music_path is None:
            bg_music_path = MUSIC_DIR / "synthwave_cyber_drive.mp3"
            if not bg_music_path.exists():
                mp3s = list(MUSIC_DIR.glob("*.mp3"))
                if mp3s:
                    bg_music_path = mp3s[0]
                else:
                    raise FileNotFoundError("Synthwave background music not found!")

        log(f"🎵 [AudioMixer] Arka plan müziği yükleniyor: {bg_music_path.name}")
        self.bg_audio = load_audio_file(bg_music_path)
        self.bg_len = len(self.bg_audio)
        self.bg_pos = 0

        self.active_voice_audio = None
        self.voice_pos = 0
        self.current_voice_info = None

        # Professional Streamer Audio Architecture
        # Ambient Synthwave Music: 0.22 (gentle, chill background groove)
        # Deep Ducked Music: 0.03 (-30 dB drop during voiceover)
        # Voice Gain: 1.45x (crisp, clear, punchy broadcast presence)
        self.normal_music_vol = 0.22
        self.ducked_music_vol = 0.03
        self.music_vol = 0.22
        self.target_music_vol = 0.22
        self.duck_down_speed = 0.22
        self.duck_up_speed = 0.04

        self.voice_queue = queue.Queue()
        self.gap_frames_remaining = 0

    def push_voice_response(self, item):
        """Enqueues a new voice response into the internal playback queue."""
        self.voice_queue.put(item)

    def _start_next_queued_voice(self):
        """Pulls next queued audio item if active speech is finished."""
        if not self.voice_queue.empty() and self.active_voice_audio is None and self.gap_frames_remaining <= 0:
            try:
                item = self.voice_queue.get_nowait()
                audio_path = item["audio_path"]
                voice_arr = load_audio_file(audio_path)
                self.active_voice_audio = voice_arr
                self.voice_pos = 0
                self.current_voice_info = item
                log(f"🎙️ [AudioMixer] Yayıncı konuşuyor: '{item['reply_text']}'")
            except Exception as e:
                log(f"⚠️ [AudioMixer] Ses yükleme hatası: {e}")

    def get_next_frame_chunk(self):
        """Returns 1764 samples of mixed 16-bit stereo PCM bytes at 25 FPS."""
        # Check if new voice should start
        self._start_next_queued_voice()

        # 1. Background Music slice
        end_bg = self.bg_pos + SAMPLES_PER_FRAME
        if end_bg <= self.bg_len:
            bg_slice = self.bg_audio[self.bg_pos:end_bg].copy()
            self.bg_pos = end_bg % self.bg_len
        else:
            first_part = self.bg_audio[self.bg_pos:]
            rem = SAMPLES_PER_FRAME - len(first_part)
            second_part = self.bg_audio[:rem]
            bg_slice = np.vstack((first_part, second_part))
            self.bg_pos = rem

        # 2. Check Voice state and ducking
        if self.active_voice_audio is not None:
            self.target_music_vol = self.ducked_music_vol  # 0.03 (-30 dB deep ducking)
            curr_duck_speed = self.duck_down_speed

            v_rem = len(self.active_voice_audio) - self.voice_pos
            if v_rem > 0:
                take = min(v_rem, SAMPLES_PER_FRAME)
                voice_slice = self.active_voice_audio[self.voice_pos:self.voice_pos + take]
                self.voice_pos += take

                # Pad if voice ends midway through frame
                if take < SAMPLES_PER_FRAME:
                    pad = np.zeros((SAMPLES_PER_FRAME - take, CHANNELS), dtype=np.float32)
                    voice_slice = np.vstack((voice_slice, pad))
                    self.active_voice_audio = None
                    self.current_voice_info = None
                    # Natural 1.2s breathing pause (30 frames @ 25 FPS) before next queued speech
                    self.gap_frames_remaining = 30
            else:
                self.active_voice_audio = None
                self.current_voice_info = None
                self.gap_frames_remaining = 30
                voice_slice = np.zeros((SAMPLES_PER_FRAME, CHANNELS), dtype=np.float32)
        else:
            if self.gap_frames_remaining > 0:
                self.gap_frames_remaining -= 1

            self.target_music_vol = self.normal_music_vol  # 0.22 (gentle ambient music)
            curr_duck_speed = self.duck_up_speed
            voice_slice = np.zeros((SAMPLES_PER_FRAME, CHANNELS), dtype=np.float32)

        # Smooth volume transition
        self.music_vol += (self.target_music_vol - self.music_vol) * curr_duck_speed

        # Mix: Background (ducked) + Voice (boosted loud & clear with soft saturation)
        voice_boosted = np.tanh(voice_slice * 1.45)
        mixed = bg_slice * self.music_vol + voice_boosted * 0.95
        np.clip(mixed, -1.0, 1.0, out=mixed)

        return (mixed * 32767.0).astype(np.int16).tobytes()


class LiveDynamicStreamer:
    """
    24/7 Real-Time Interactive Live Streamer for 'Zirvenin Kanunu'.
    Renders canvas + chat overlay live at 30 FPS, mixes audio in real time,
    and feeds directly into RTMP.
    """
    def __init__(self, rtmp_target=None):
        creds = load_credentials()
        primary_url = creds.get("rtmp_url", "rtmp://a.rtmp.youtube.com/live2")
        stream_key = creds["stream_key"]
        self.rtmp_url = rtmp_target or f"{primary_url}/{stream_key}"

        self.streamer = UnbeatableTRexStreamer()
        self.mixer = DynamicAudioMixer()
        self.voice_responder = get_voice_responder()
        self.running = False
        self.audio_server = None
        self.audio_conn = None
        self.audio_port = 9988

        # Setup Live Chat Poller
        self.chat_poller = LiveChatPoller(on_real_message=self._on_real_chat_message)

    def _on_real_chat_message(self, msg_entry):
        """Called immediately when a real viewer sends a message."""
        author = msg_entry["author_name"]
        text = msg_entry["message_text"]
        log(f"🔥 [DynamicStreamer] Gerçek izleyici mesajı tespit edildi: {author} -> '{text}'")

        # Clean display name (no '@' or ugly numbers)
        from app.services.live_voice_responder import clean_streamer_username
        clean_name = clean_streamer_username(author)

        # Trigger golden celebration alert on canvas
        self.streamer.trigger_alert(
            alert_type="new_sub",
            title=f"💬 {clean_name}",
            subtitle=f"⚡ {text}",
            duration=360
        )

        # Dispatch async voice synthesis
        self.voice_responder.enqueue_async(author, text, msg_entry.get("msg_id"))

    def _setup_audio_server(self):
        """Initializes TCP server for streaming raw PCM audio to FFmpeg."""
        self.audio_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.audio_server.bind(('127.0.0.1', self.audio_port))
        self.audio_server.listen(1)
        log(f"🎧 [DynamicStreamer] Ses TCP sunucusu hazır: 127.0.0.1:{self.audio_port}")

    def run_stream(self):
        self.running = True
        log("=" * 70)
        log("🚀 ZIRVENIN KANUNU REAL-TIME VOICE & DYNAMIC CHAT STREAMER BAŞLATILIYOR")
        log(f"📡 Hedef RTMP: {self.rtmp_url[:35]}***")
        log(f"🎮 Çözünürlük: {WIDTH}x{HEIGHT} @ {FPS} FPS")
        log("=" * 70)

        # 1. Start Chat Poller
        self.chat_poller.start_background_thread()

        # Background Feed Watcher to catch any incoming real messages
        def feed_watcher():
            seen_ids = set()
            while self.running:
                try:
                    msgs = get_active_chat_messages(limit=5)
                    for m in msgs:
                        if m.get("is_real"):
                            m_id = m.get("msg_id") or f"{m.get('author_name')}_{m.get('message_text')}"
                            if m_id not in seen_ids:
                                seen_ids.add(m_id)
                                self._on_real_chat_message(m)
                except Exception:
                    pass
                time.sleep(1.5)

        t_feed = threading.Thread(target=feed_watcher, daemon=True)
        t_feed.start()

        # 2. Setup Audio Socket
        self._setup_audio_server()

        # 3. Build FFmpeg Command
        cmd = [
            "ffmpeg",
            "-y",
            # Video Input: rawvideo from stdin
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{WIDTH}x{HEIGHT}",
            "-r", str(FPS),
            "-i", "-",
            # Audio Input: s16le stereo from TCP socket
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-i", f"tcp://127.0.0.1:{self.audio_port}",
            # Encoders & RTMP tuning
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-b:v", "3500k",
            "-maxrate", "4000k",
            "-bufsize", "8000k",
            "-pix_fmt", "yuv420p",
            "-g", "50",
            "-c:a", "aac",
            "-b:a", "160k",
            "-ar", str(SAMPLE_RATE),
            "-flvflags", "no_duration_filesize",
            "-f", "flv",
            self.rtmp_url
        ]

        log("🎬 FFmpeg RTMP process başlatılıyor...")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False
        )

        # Record PID
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
        log(f"✅ FFmpeg aktifleşti (PID: {proc.pid})")

        # Audio Worker Thread
        def audio_worker():
            log("⏳ FFmpeg ses bağlantısı bekleniyor...")
            try:
                self.audio_conn, addr = self.audio_server.accept()
                log(f"✅ FFmpeg ses soketi bağlandı: {addr}")
                audio_frame_interval = 1.0 / FPS
                next_audio_time = time.perf_counter()
                last_streamer_talk = time.time()

                while self.running and proc.poll() is None:
                    # Check for ready human streamer responses
                    voice_item = self.voice_responder.get_next_audio_response()
                    if voice_item:
                        self.mixer.push_voice_response(voice_item)
                        last_streamer_talk = time.time()
                    else:
                        # Spontaneous streamer hype talk if chat is quiet for 210 seconds (3.5 mins)
                        if (time.time() - last_streamer_talk >= 210.0 and 
                            self.mixer.voice_queue.empty() and 
                            self.mixer.active_voice_audio is None):
                            threading.Thread(target=self.voice_responder.get_periodic_idle_speech, daemon=True).start()
                            last_streamer_talk = time.time()

                    chunk = self.mixer.get_next_frame_chunk()
                    self.audio_conn.sendall(chunk)

                    next_audio_time += audio_frame_interval
                    s_time = next_audio_time - time.perf_counter()
                    if s_time > 0:
                        time.sleep(s_time)
                    elif s_time < -0.2:
                        next_audio_time = time.perf_counter()

            except Exception as e:
                if self.running:
                    log(f"⚠️ [AudioWorker] Ses iletim uyarısı: {e}")

        t_audio = threading.Thread(target=audio_worker, daemon=True)
        t_audio.start()

        # Monitor FFmpeg stderr in background to catch disconnects
        def monitor_stderr():
            while self.running and proc.poll() is None:
                try:
                    line = proc.stderr.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    text = line.decode('utf-8', errors='ignore').strip()
                    if "Error" in text or "error" in text or "Connection reset" in text:
                        log(f"⚠️ [FFmpeg RTMP]: {text}")
                except Exception:
                    break

        t_err = threading.Thread(target=monitor_stderr, daemon=True)
        t_err.start()

        frame_duration = 1.0 / FPS
        next_frame_time = time.perf_counter()
        frame_count = 0
        last_metric_time = time.time()

        try:
            while self.running and proc.poll() is None:
                # 1. Advance Game Physics & Render Frame
                self.streamer.update_physics_and_ai()
                frame_bytes = self.streamer.render_frame_pil()

                # 2. Write Video to stdin
                try:
                    proc.stdin.write(frame_bytes)
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    log(f"❌ Video akış borusu koptu: {e}")
                    break

                frame_count += 1
                if time.time() - last_metric_time >= 30.0:
                    fps_real = frame_count / (time.time() - last_metric_time)
                    log(f"⚡ [CANLI YAYIN AKTİF] Son 30 sn FPS: {fps_real:.1f} | Toplam Kare: {self.streamer.frame_idx:,} | Skor: {self.streamer.score:,}")
                    frame_count = 0
                    last_metric_time = time.time()

                # 3. Precise Frame Rate Timing
                next_frame_time += frame_duration
                sleep_time = next_frame_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    if sleep_time < -0.2:
                        next_frame_time = time.perf_counter()

        except KeyboardInterrupt:
            log("🛑 Canlı yayın kullanıcı tarafından durduruldu.")
        except Exception as e:
            log(f"❌ Canlı yayın beklenmedik hata: {e}")
            import traceback
            traceback.print_exc()

        finally:
            self.running = False
            if self.audio_conn:
                try:
                    self.audio_conn.close()
                except Exception:
                    pass
            if self.audio_server:
                try:
                    self.audio_server.close()
                except Exception:
                    pass
            if proc:
                try:
                    proc.stdin.close()
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            self.chat_poller.stop()
            log("ℹ️ Dynamic Streamer sonlandı.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    streamer = LiveDynamicStreamer(rtmp_target=target)
    streamer.run_stream()
