"""
Live Chat Poller & Community Engagement Engine for 'Zirvenin Kanunu'.
Polls live YouTube chat using pytchat (video_id='9-xGXUwkCFM') and maintains
active viewer messages in output/live_chat_feed.json.
Features intelligent simulated community fallback when live chat is quiet.
"""

import os
import sys
import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
import pytchat

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LIVE_CHAT_FEED_FILE = OUTPUT_DIR / "live_chat_feed.json"
DEFAULT_VIDEO_ID = "zCeqo0WTWJs"

# Authentic simulated Turkish viewer community messages
SIMULATED_COMMUNITY_POOL = [
    ("Emre_Pro", "Bot hiç yanmıyor helal olsun haha 🦖"),
    ("Oğuzhan K.", "Dinozor 15.000 puanı devirdi süper gidiyor!"),
    ("ZirveTakipçisi", "Abone oldum destek tam gaz! 🚀"),
    ("Kaan_Gamer", "Hız 2.0x oldu dino uçuyor resmen 🔥"),
    ("Caner_99", "Arkada çalan synthwave müzik harika"),
    ("Mehmet_TR", "Sohbete yazınca ekranda çıkması çok iyi olmuş"),
    ("Berk_Apex", "100 aboneye az kaldı beyler destekleyin!"),
    ("Ahmet B.", "Benim rekorum 4.200 dü buradaki skor efsane"),
    ("Selin_G", "Kuşların altından eğilerek geçişi çok akıcı"),
    ("Yusuf_Dev", "Gece modu teması efsane olmuş 🌙"),
    ("Burak_99", "Selamlar herkese, iyi yayınlar!"),
    ("Eren_T", "Dino rekoru kaç olacak tahminleri alalım"),
    ("Mert_Kral", "7/24 kesintisiz mi yayın? Harika proje"),
    ("Canan_A", "T-Rex dünya rekorunu kıracak bugün 🏆")
]


class LiveChatPoller:
    """
    Polls YouTube Live Chat with automatic fallback to high-engagement simulated feed.
    Writes latest messages to output/live_chat_feed.json.
    """
    def __init__(self, video_id=DEFAULT_VIDEO_ID, output_file=LIVE_CHAT_FEED_FILE, max_history=10):
        self.video_id = video_id
        self.output_file = Path(output_file)
        self.max_history = max_history
        self.messages = []
        self.lock = threading.Lock()
        self.running = False
        self.last_message_time = time.time()
        self.last_simulated_time = time.time()
        self.simulated_idx = 0

        # Seed with initial engaging messages so overlay is never blank
        self._seed_initial_feed()

    def _seed_initial_feed(self):
        sample = random.sample(SIMULATED_COMMUNITY_POOL, 3)
        now = datetime.now()
        for i, (author, msg) in enumerate(sample):
            self.messages.append({
                "author_name": author,
                "message_text": msg,
                "timestamp": now.strftime("%H:%M:%S"),
                "is_real": False
            })
        self._save_feed()

    def _save_feed(self):
        """Atomic write to prevent partial reads by the streamer."""
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

    def add_message(self, author_name, message_text, is_real=True):
        with self.lock:
            # Clean message text
            clean_msg = message_text.strip().replace("\n", " ")
            if len(clean_msg) > 75:
                clean_msg = clean_msg[:72] + "..."

            msg_entry = {
                "author_name": author_name[:25],
                "message_text": clean_msg,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "is_real": is_real
            }

            self.messages.append(msg_entry)
            if len(self.messages) > self.max_history * 2:
                self.messages = self.messages[-self.max_history:]

            self.last_message_time = time.time()
            self._save_feed()

    def check_simulated_fallback(self):
        """Injects simulated message if live chat has been quiet."""
        now = time.time()
        # If quiet for more than 12-18 seconds, drop an engaging simulated comment
        silence_threshold = 14.0 + random.uniform(0, 6)
        if now - self.last_message_time > silence_threshold and now - self.last_simulated_time > silence_threshold:
            pool_item = SIMULATED_COMMUNITY_POOL[self.simulated_idx % len(SIMULATED_COMMUNITY_POOL)]
            self.simulated_idx += 1
            author, msg = pool_item
            self.add_message(author, msg, is_real=False)
            self.last_simulated_time = now

    def poll_loop(self):
        self.running = True
        print(f"📡 [LiveChatPoller] Başlatıldı -> Video ID: {self.video_id}")
        print(f"📁 [LiveChatPoller] Feed Dosyası: {self.output_file}")

        while self.running:
            chat = None
            try:
                chat = pytchat.create(video_id=self.video_id, interruptable=False)
                print(f"✅ [LiveChatPoller] pytchat bağlantısı kuruldu (is_alive={chat.is_alive()})")

                while self.running and chat.is_alive():
                    items = chat.get().sync_items()
                    for c in items:
                        print(f"💬 [GERÇEK CANLI MESAJ] {c.datetime} - {c.author.name}: {c.message}")
                        self.add_message(c.author.name, c.message, is_real=True)

                    self.check_simulated_fallback()
                    time.sleep(1.0)

            except Exception as e:
                print(f"⚠️ [LiveChatPoller] pytchat döngü uyarısı: {e}")

            finally:
                if chat:
                    try:
                        chat.terminate()
                    except Exception:
                        pass

            # In case stream was not reachable or crashed, check simulated fallback during cooldown
            for _ in range(5):
                if not self.running:
                    break
                self.check_simulated_fallback()
                time.sleep(1.0)

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
        # Fallback defaults if feed file doesn't exist yet
        return [
            {"author_name": "Kaan_Gamer", "message_text": "Dinozor 15.000 puanı devirdi süper gidiyor!", "timestamp": "12:00:00", "is_real": False},
            {"author_name": "Emre_Pro", "message_text": "Bot hiç yanmıyor helal olsun haha 🦖", "timestamp": "12:00:15", "is_real": False},
            {"author_name": "ZirveTakipçisi", "message_text": "Abone oldum destek tam gaz! 🚀", "timestamp": "12:00:30", "is_real": False}
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
    poller = LiveChatPoller()
    try:
        poller.poll_loop()
    except KeyboardInterrupt:
        print("\n🛑 Poller durduruldu.")
        poller.stop()
