"""
Live Chat Poller & Community Engagement Engine for 'Zirvenin Kanunu'.
Polls real live YouTube chat using pytchat with auto-detection of active live streams.
Prioritizes 100% authentic real viewer messages and triggers real-time voice responder.
"""

import os
import sys
import json
import time
import re
import urllib.request
import threading
from datetime import datetime
from pathlib import Path
import pytchat

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LIVE_CHAT_FEED_FILE = OUTPUT_DIR / "live_chat_feed.json"
DEFAULT_VIDEO_ID = "gS_MHw3kvOo"
CHANNEL_HANDLE = "@zirveninkanunu"


def detect_active_live_video_id(channel_handle=CHANNEL_HANDLE, fallback_id=DEFAULT_VIDEO_ID):
    """
    Attempts to auto-detect the active YouTube live stream video ID for the channel.
    """
    url = f"https://www.youtube.com/{channel_handle}/live"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            m = re.search(r'"liveStreamabilityRenderer".*?"videoId":"([^"]+)"', html)
            if m:
                vid = m.group(1)
                print(f"🎯 [LiveChatPoller] Aktif yayın tespit edildi (Renderer): {vid}")
                return vid
            m = re.search(r'<link rel="canonical" href="https://www.youtube.com/watch\?v=([^"]+)">', html)
            if m:
                vid = m.group(1)
                print(f"🎯 [LiveChatPoller] Aktif yayın tespit edildi (Canonical): {vid}")
                return vid
            m = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
            if m:
                vid = m.group(1)
                print(f"🎯 [LiveChatPoller] Aktif yayın tespit edildi (Regex): {vid}")
                return vid
    except Exception as e:
        print(f"⚠️ [LiveChatPoller] Canlı yayın ID tespit uyarısı: {e}")

    print(f"ℹ️ [LiveChatPoller] Varsayılan yayın ID kullanılıyor: {fallback_id}")
    return fallback_id


class LiveChatPoller:
    """
    Polls YouTube Live Chat with 100% fidelity.
    Maintains output/live_chat_feed.json prioritizing REAL viewers.
    Notifies listeners when real viewer messages arrive for AI voice responses.
    """
    def __init__(self, video_id=None, output_file=LIVE_CHAT_FEED_FILE, max_history=10, on_real_message=None):
        self.video_id = video_id or detect_active_live_video_id()
        self.output_file = Path(output_file)
        self.max_history = max_history
        self.messages = []
        self.lock = threading.Lock()
        self.running = False
        self.on_real_message = on_real_message
        self.seen_message_ids = set()
        self.real_messages_count = 0

        # Load existing feed if present
        self._load_existing_feed()

    def _load_existing_feed(self):
        if self.output_file.exists():
            try:
                with open(self.output_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        self.messages = data[-self.max_history:]
                        for m in self.messages:
                            if m.get("is_real"):
                                self.real_messages_count += 1
                            self.seen_message_ids.add(f"{m.get('author_name')}_{m.get('message_text')}")
            except Exception:
                pass

        if not self.messages:
            # Clean welcoming banner if feed is completely empty
            self.messages = [
                {
                    "author_name": "Zirvenin Kanunu",
                    "message_text": "Canlı yayına hoş geldiniz! Sohbete yazın, canlı yayında cevaplayalım!",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "is_real": False,
                    "is_system": True
                }
            ]
            self._save_feed()

    def _save_feed(self):
        """Atomic write to prevent race conditions."""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.output_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.messages[-self.max_history:], f, ensure_ascii=False, indent=2)
            temp_file.replace(self.output_file)
        except Exception as e:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    def add_message(self, author_name, message_text, is_real=True, msg_id=None):
        with self.lock:
            # Dedup key
            dedup_key = msg_id or f"{author_name}_{message_text}"
            if dedup_key in self.seen_message_ids:
                return None
            self.seen_message_ids.add(dedup_key)

            # Clean message text
            clean_msg = message_text.strip().replace("\n", " ")
            if len(clean_msg) > 90:
                clean_msg = clean_msg[:87] + "..."

            msg_entry = {
                "author_name": author_name[:25],
                "message_text": clean_msg,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "is_real": is_real,
                "msg_id": dedup_key
            }

            if is_real:
                self.real_messages_count += 1
                # When real message arrives, purge any old system/simulated placeholders
                self.messages = [m for m in self.messages if m.get("is_real")]

            self.messages.append(msg_entry)
            if len(self.messages) > self.max_history:
                self.messages = self.messages[-self.max_history:]

            self._save_feed()

        # Trigger callback outside lock
        if is_real and self.on_real_message:
            try:
                self.on_real_message(msg_entry)
            except Exception as e:
                print(f"⚠️ [LiveChatPoller] Callback error: {e}")

        return msg_entry

    def poll_loop(self):
        self.running = True
        print(f"📡 [LiveChatPoller] Başlatıldı -> Video ID: {self.video_id}")
        print(f"📁 [LiveChatPoller] Feed Dosyası: {self.output_file}")

        reconnect_attempts = 0
        while self.running:
            chat = None
            try:
                chat = pytchat.create(video_id=self.video_id, interruptable=False)
                print(f"✅ [LiveChatPoller] pytchat bağlantısı kuruldu (is_alive={chat.is_alive()})")
                reconnect_attempts = 0

                while self.running and chat.is_alive():
                    for c in chat.get().sync_items():
                        print(f"🔴 [GERÇEK CANLI İZLEYİCİ] {c.datetime} - {c.author.name}: {c.message}")
                        self.add_message(
                            author_name=c.author.name,
                            message_text=c.message,
                            is_real=True,
                            msg_id=f"{c.id}" if hasattr(c, 'id') else None
                        )
                    time.sleep(0.8)

            except Exception as e:
                reconnect_attempts += 1
                print(f"⚠️ [LiveChatPoller] pytchat hatası ({reconnect_attempts}): {e}")

            finally:
                if chat:
                    try:
                        chat.terminate()
                    except Exception:
                        pass

            # In case stream was not reachable or stopped, check for updated active live stream
            if self.running:
                time.sleep(5.0)
                if reconnect_attempts % 3 == 0:
                    detected = detect_active_live_video_id(fallback_id=self.video_id)
                    if detected != self.video_id:
                        print(f"🔄 [LiveChatPoller] Yeni yayın ID'ye geçiliyor: {detected}")
                        self.video_id = detected

    def start_background_thread(self):
        t = threading.Thread(target=self.poll_loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self.running = False


def get_active_chat_messages(feed_file=LIVE_CHAT_FEED_FILE, limit=3):
    """Utility function to safely read the latest active chat bubbles."""
    feed_path = Path(feed_file)
    if not feed_path.exists():
        return [
            {
                "author_name": "Zirvenin Kanunu",
                "message_text": "Canlı yayına hoş geldiniz! Sohbete yazın, ekranda canlı cevaplayalım!",
                "timestamp": "CANLI",
                "is_real": True
            }
        ][-limit:]
    try:
        with open(feed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data[-limit:]
    except Exception:
        pass
    return []


if __name__ == "__main__":
    def print_alert(m):
        print(f"🔔 YENİ İZLEYİCİ MESAJI ALINDI: {m['author_name']} -> {m['message_text']}")

    poller = LiveChatPoller(on_real_message=print_alert)
    try:
        poller.poll_loop()
    except KeyboardInterrupt:
        print("\n🛑 Poller durduruldu.")
        poller.stop()
