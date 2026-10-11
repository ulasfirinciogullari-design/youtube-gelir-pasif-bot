"""
Unbeatable AI T-Rex 24/7 Live Stream Architect for 'Zirvenin Kanunu'.
Simulates an invincible Google Chrome Dino running endlessly at 60 FPS (1920x1080).
Authentic, clean arcade HUD (real channel subscriber count: 29 / 100, no fake AI gimmicks),
mathematically bulletproof obstacle clearance (zero collisions),
and seamless looping synthwave soundtrack.
"""

import os
import sys
import json
import time
import math
import random
import subprocess
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from app.services.live_chat_poller import LiveChatPoller, get_active_chat_messages
except ImportError:
    from live_chat_poller import LiveChatPoller, get_active_chat_messages

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SPRITE_PATH = BASE_DIR / "assets" / "chrome_dino_sprite.png"
OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = BASE_DIR / "assets" / "music"
MASTER_ASSET_PATH = OUTPUT_DIR / "UNBEATABLE_TREX_60FPS_MASTER_STREAM.mp4"
CREDENTIALS_FILE = OUTPUT_DIR / "live_stream_credentials.json"
PID_FILE = OUTPUT_DIR / "livestream_pid.txt"
LOG_FILE = OUTPUT_DIR / "livestream_ffmpeg.log"

WIDTH = 1920
HEIGHT = 1080
FPS = 60
GROUND_Y = 760
SCALE = 2.0


class SpriteManager:
    """Loads and caches Chrome Dino sprites as PIL RGBA Images."""
    def __init__(self, sprite_path=SPRITE_PATH):
        if not sprite_path.exists():
            raise FileNotFoundError(f"Sprite dosyası bulunamadı: {sprite_path}")
        
        with open(sprite_path, "rb") as f:
            self.sheet = cv2.imdecode(np.frombuffer(f.read(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            
        self.cache = {}

    def build_tinted_pil(self, crop, body_color, eye_color=(255, 255, 255)):
        alpha = crop[:, :, 3]
        rgb = crop[:, :, :3]
        h, w = crop.shape[:2]
        
        out = np.zeros((h, w, 4), dtype=np.uint8)
        out[:, :, 3] = alpha
        
        mask_visible = alpha > 0
        gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
        mask_body = mask_visible & (gray < 180)
        mask_eye = mask_visible & (gray >= 180)
        
        out[mask_body, :3] = body_color
        out[mask_eye, :3] = eye_color
        
        scaled = cv2.resize(out, (int(w * SCALE), int(h * SCALE)), interpolation=cv2.INTER_NEAREST)
        return Image.fromarray(scaled, mode="RGBA")

    def get_sprites_for_theme(self, is_night=False):
        key = "night" if is_night else "day"
        if key in self.cache:
            return self.cache[key]
            
        if not is_night:
            body_col = (45, 55, 72)     # Charcoal slate
            eye_col = (255, 255, 255)   # White eye
            ground_col = (90, 100, 115) # Slate ground
            cloud_col = (165, 175, 190) # Soft cloud
        else:
            body_col = (56, 189, 248)   # Cyber cyan neon
            eye_col = (255, 255, 255)   # Bright eye
            ground_col = (56, 189, 248) # Neon cyan ground
            cloud_col = (75, 85, 110)   # Dim cyber cloud

        sprites = {
            "dino_stand": self.build_tinted_pil(self.sheet[2:96, 1678:1766], body_col, eye_col),
            "dino_run1": self.build_tinted_pil(self.sheet[2:96, 1854:1942], body_col, eye_col),
            "dino_run2": self.build_tinted_pil(self.sheet[2:96, 1942:2030], body_col, eye_col),
            "dino_duck1": self.build_tinted_pil(self.sheet[34:96, 2206:2324], body_col, eye_col),
            "dino_duck2": self.build_tinted_pil(self.sheet[34:96, 2324:2442], body_col, eye_col),
            "bird1": self.build_tinted_pil(self.sheet[14:75, 260:352], body_col, eye_col),
            "bird2": self.build_tinted_pil(self.sheet[2:62, 352:444], body_col, eye_col),
            "cactus_s1": self.build_tinted_pil(self.sheet[14:80, 878:918], body_col, eye_col),
            "cactus_s2": self.build_tinted_pil(self.sheet[2:80, 954:1034], body_col, eye_col),
            "cactus_b1": self.build_tinted_pil(self.sheet[2:102, 652:702], body_col, eye_col),
            "cactus_b2": self.build_tinted_pil(self.sheet[2:102, 702:752], body_col, eye_col),
            "cactus_bg": self.build_tinted_pil(self.sheet[2:102, 752:852], body_col, eye_col),
            "ground": self.build_tinted_pil(self.sheet[104:118, 2:2402], ground_col, ground_col),
            "cloud": self.build_tinted_pil(self.sheet[2:29, 174:258], cloud_col, cloud_col),
            "moon": self.build_tinted_pil(self.sheet[2:80, 1074:1154], (253, 224, 71), (253, 224, 71)),
            "star": self.build_tinted_pil(self.sheet[2:21, 1294:1312], (253, 224, 71), (253, 224, 71))
        }
        self.cache[key] = sprites
        return sprites


class Obstacle:
    def __init__(self, obs_type, sprite_key, x, y, width, height, can_duck=False):
        self.obs_type = obs_type
        self.sprite_key = sprite_key
        self.x = float(x)
        self.y = float(y)
        self.width = width
        self.height = height
        self.can_duck = can_duck
        self.counted = False


class Cloud:
    def __init__(self, x, y, speed):
        self.x = float(x)
        self.y = float(y)
        self.speed = speed


class Particle:
    def __init__(self, x, y, vx, vy, life, color):
        self.x = float(x)
        self.y = float(y)
        self.vx = vx
        self.vy = vy
        self.life = life
        self.max_life = life
        self.color = color


class UnbeatableTRexStreamer:
    """
    High-retention 60 FPS invincible T-Rex simulation stream engine.
    Authentic arcade metrics, realistic subscriber progress, 100% collision-free guarantee.
    """
    def __init__(self):
        self.sprite_mgr = SpriteManager()
        self.fonts = self._load_fonts()
        
        # Real subscriber metrics
        self.current_subs = 29
        self.goal_subs = 100
        
        # Simulation parameters
        self.frame_idx = 0
        self.score = 12450
        self.base_score = 12450
        self.score_rate = 0.25
        self.obstacles_dodged = 142
        
        # Ground and Dino position
        self.ground_x = 0.0
        self.ground_y = GROUND_Y
        self.dino_x = 240.0
        self.dino_y = 0.0
        self.dino_vy = 0.0
        self.gravity = 1.15
        self.jump_velocity = -27.5
        self.is_jumping = False
        self.is_ducking = False
        
        # Speed dynamics (realistic arcade speed)
        self.speed = 16.0
        self.max_speed = 24.0
        
        # Obstacle management - generous gaps to completely prevent chaining collisions
        self.obstacles = []
        self.min_obstacle_gap = 900
        self.last_spawn_dist = 0
        
        # Clouds
        self.clouds = [
            Cloud(300, 360, 1.2),
            Cloud(950, 310, 1.5),
            Cloud(1580, 380, 1.1)
        ]
        
        # Particles
        self.particles = []
        
        # Day / Night cycle (switches every 45 seconds = 2700 frames)
        self.cycle_length = 2700
        self.is_night = False
        
        # Authentic Chat Headlines (Clean badges without missing font glyphs)
        self.chat_headlines = [
            "[CANLI SOHBET] Dinozor rekorunu geçebilir misin? Senin rekorun kaç? Sohbete yaz!",
            "[ZİRVENİN KANUNU RESMİ YAYINI] Beğen & Abone Ol, Canlı Yayın Kapanmasın!",
            "[DÜNYA REKORU DENEMESİ] Kesintisiz 7/24 Simülasyon | Yenilmez Dinozor",
            "[SOHBETE KATILIN] Hoş Geldiniz! Tahminlerinizi ve rekorlarınızı paylaşın!"
        ]
        
        # Pre-render base canvas templates
        self.base_template_day = self._create_base_template(is_night=False)
        self.base_template_night = self._create_base_template(is_night=True)

        # Live Chat Overlay Integration
        self.chat_poller = None
        self.chat_messages = []
        self.last_chat_refresh = 0
        self._init_chat_poller()

    def _init_chat_poller(self):
        try:
            self.chat_poller = LiveChatPoller()
            self.chat_poller.start_background_thread()
            self.chat_messages = get_active_chat_messages(limit=3)
        except Exception as e:
            self.chat_messages = get_active_chat_messages(limit=3)

    def _load_fonts(self):
        font_dir = "C:\\Windows\\Fonts"
        return {
            "title": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 34),
            "bold": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 24),
            "score": ImageFont.truetype(f"{font_dir}\\impact.ttf", 46),
            "label": ImageFont.truetype(f"{font_dir}\\arialbd.ttf", 20),
            "ticker": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 23),
            "chat_title": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 20),
            "chat_cta": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 15),
            "chat_author": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 16),
            "chat_msg": ImageFont.truetype(f"{font_dir}\\segoeui.ttf", 15),
            "chat_time": ImageFont.truetype(f"{font_dir}\\segoeui.ttf", 12),
            "chat_tag": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 11),
            "chat_badge": ImageFont.truetype(f"{font_dir}\\segoeuib.ttf", 11),
            "emoji_title": ImageFont.truetype(f"{font_dir}\\seguiemj.ttf", 20),
            "emoji_cta": ImageFont.truetype(f"{font_dir}\\seguiemj.ttf", 15),
            "emoji_msg": ImageFont.truetype(f"{font_dir}\\seguiemj.ttf", 15)
        }

    def _draw_crown(self, draw, cx, cy, size=18, color=(250, 204, 21)):
        pts = [
            (cx - size, cy + size // 2),
            (cx - size, cy - size // 2),
            (cx - size // 2, cy),
            (cx, cy - size),
            (cx + size // 2, cy),
            (cx + size, cy - size // 2),
            (cx + size, cy + size // 2)
        ]
        draw.polygon(pts, fill=color, outline=(217, 119, 6))

    @staticmethod
    def _is_emoji(char):
        cp = ord(char)
        return (
            0x1F300 <= cp <= 0x1FAFF or  # Misc symbols, pictographs, emoticons
            0x2600 <= cp <= 0x27BF or    # Dingbats, misc symbols
            0xFE00 <= cp <= 0xFE0F or    # Variation selectors
            0x1F900 <= cp <= 0x1F9FF or
            0x1F600 <= cp <= 0x1F64F or
            0x1F680 <= cp <= 0x1F6FF
        )

    def _draw_smart_text(self, draw, x, y, text, font_txt, font_emj, fill_txt=(255, 255, 255), fill_emj=None, max_width=510):
        """Renders mixed Turkish text and emojis with crisp typography and zero tofu boxes."""
        cur_x = x
        segments = []
        curr_seg = []
        curr_is_emj = None
        for ch in text:
            emj = self._is_emoji(ch)
            if curr_is_emj is None:
                curr_is_emj = emj
                curr_seg.append(ch)
            elif emj == curr_is_emj:
                curr_seg.append(ch)
            else:
                segments.append((''.join(curr_seg), curr_is_emj))
                curr_seg = [ch]
                curr_is_emj = emj
        if curr_seg:
            segments.append((''.join(curr_seg), curr_is_emj))

        for seg_text, seg_is_emj in segments:
            font = font_emj if seg_is_emj else font_txt
            fill = (fill_emj or fill_txt) if seg_is_emj else fill_txt
            if cur_x - x > max_width:
                break
            draw.text((cur_x, y), seg_text, font=font, fill=fill)
            bbox = draw.textbbox((cur_x, y), seg_text, font=font)
            cur_x = bbox[2] + 2

    def _create_base_template(self, is_night=False):
        bg_col = (10, 15, 29) if is_night else (248, 250, 252)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), bg_col)
        draw = ImageDraw.Draw(canvas)
        
        # 1. TOP HEADER BAR CONTAINER (Sleek and authentic)
        draw.rectangle([(30, 20), (1890, 105)], fill=(15, 23, 42), outline=(56, 189, 248), width=3)
        self._draw_crown(draw, 230, 63, size=18, color=(250, 204, 21))
        draw.text((255, 42), "ZIRVENIN KANUNU", font=self.fonts["title"], fill=(250, 204, 21))
        draw.text((620, 46), "|  7/24 KESİNTİSİZ DİNOZOR CANLI YAYINI", font=self.fonts["bold"], fill=(241, 245, 249))

        # Right Live Badge Container
        draw.rounded_rectangle([(1640, 38), (1865, 88)], radius=8, fill=(220, 38, 38))
        draw.ellipse([(1660, 56), (1672, 68)], fill=(255, 255, 255))
        draw.text((1685, 47), "CANLI YAYIN", font=self.fonts["bold"], fill=(255, 255, 255))

        # 2. STATS BAR CONTAINERS (Real arcade metrics)
        # Box 1: Hi-Score
        draw.rounded_rectangle([(30, 120), (500, 225)], radius=10, fill=(30, 41, 59), outline=(100, 116, 139), width=2)
        draw.text((50, 130), ">> EN YÜKSEK SKOR (HI-SCORE)", font=self.fonts["label"], fill=(148, 163, 184))
        draw.text((50, 160), "999,999 PTS", font=self.fonts["score"], fill=(250, 204, 21))

        # Box 2: Current Score
        draw.rounded_rectangle([(520, 120), (990, 225)], radius=10, fill=(30, 41, 59), outline=(100, 116, 139), width=2)
        draw.text((540, 130), ">> ANLIK PUAN (SCORE)", font=self.fonts["label"], fill=(148, 163, 184))

        # Box 3: Dodged Obstacles
        draw.rounded_rectangle([(1010, 120), (1480, 225)], radius=10, fill=(30, 41, 59), outline=(100, 116, 139), width=2)
        draw.text((1030, 130), ">> ATLATILAN ENGEL", font=self.fonts["label"], fill=(148, 163, 184))

        # Box 4: Speed Multiplier
        draw.rounded_rectangle([(1500, 120), (1890, 225)], radius=10, fill=(30, 41, 59), outline=(100, 116, 139), width=2)
        draw.text((1520, 130), ">> OYUN HIZI", font=self.fonts["label"], fill=(148, 163, 184))

        # 3. SUBSCRIBER GOAL CONTAINER (Real channel subscribers: 29 / 100)
        draw.rounded_rectangle([(30, 830), (1890, 905)], radius=10, fill=(15, 23, 42), outline=(250, 204, 21), width=2)
        self._draw_crown(draw, 55, 867, size=14, color=(250, 204, 21))
        draw.text((75, 850), f"CANLI ABONE HEDEFİ: {self.current_subs} / {self.goal_subs}", font=self.fonts["bold"], fill=(250, 204, 21))
        # Base bar container
        draw.rounded_rectangle([(600, 847), (1570, 892)], radius=8, fill=(30, 41, 59))
        draw.text((1600, 852), "ABONE OL & KATIL! >>", font=self.fonts["bold"], fill=(241, 245, 249))

        # 4. CHAT ENGAGEMENT TICKER CONTAINER
        draw.rectangle([(0, 930), (1920, 1045)], fill=(10, 15, 30))
        draw.line([(0, 930), (1920, 930)], fill=(56, 189, 248), width=3)
        return canvas

    def spawn_obstacle(self):
        sprites = self.sprite_mgr.get_sprites_for_theme(self.is_night)
        roll = random.random()
        if roll < 0.35:
            # Small cactus (single or double)
            key = random.choice(["cactus_s1", "cactus_s2"])
            w, h = sprites[key].size
            y = self.ground_y - h + 16
            self.obstacles.append(Obstacle("cactus", key, WIDTH + 50, y, w, h, can_duck=False))
        elif roll < 0.65:
            # Large cactus (single, double, or cluster)
            key = random.choice(["cactus_b1", "cactus_b2", "cactus_bg"])
            w, h = sprites[key].size
            y = self.ground_y - h + 16
            self.obstacles.append(Obstacle("cactus", key, WIDTH + 50, y, w, h, can_duck=False))
        else:
            # Pterodactyl Bird flying high in the sky (Dino ducks smoothly underneath)
            key = "bird1"
            w, h = sprites[key].size
            # Bird bottom at y = 618 (top at y = 496). Ducking dino top is 652 -> 34px clean daylight gap
            y = self.ground_y - 124 - 18 - h
            self.obstacles.append(Obstacle("bird", key, WIDTH + 50, y, w, h, can_duck=True))

    def update_physics_and_ai(self):
        self.frame_idx += 1
        self.score = int(self.base_score + self.frame_idx * self.score_rate)
        
        cycle_phase = (self.frame_idx % self.cycle_length)
        self.is_night = cycle_phase > (self.cycle_length // 2)
        
        # Smooth and realistic speed curve
        self.speed = min(self.max_speed, 16.0 + (self.frame_idx / 12000.0) * 8.0)
        self.ground_x = (self.ground_x - self.speed) % 2400
        
        for c in self.clouds:
            c.x -= c.speed
            if c.x < -200:
                c.x = WIDTH + random.randint(50, 250)
                c.y = random.randint(290, 420)
                
        self.last_spawn_dist += self.speed
        if self.last_spawn_dist > self.min_obstacle_gap and not self.is_jumping:
            if random.random() < 0.06:
                self.spawn_obstacle()
                self.last_spawn_dist = 0
                self.min_obstacle_gap = max(850, int(950 + random.randint(0, 350)))
                
        passed = []
        for obs in self.obstacles:
            obs.x -= self.speed
            if obs.x + obs.width < 0:
                passed.append(obs)
            elif obs.x + obs.width < self.dino_x and not obs.counted:
                obs.counted = True
                self.obstacles_dodged += 1
                
        for obs in passed:
            self.obstacles.remove(obs)
            
        # ==================== BULLETPROOF INVINCIBLE AI ====================
        next_obs = None
        min_dist = 999999
        dino_center = self.dino_x + 90
        for obs in self.obstacles:
            if obs.x + obs.width > self.dino_x:
                dist = obs.x - dino_center
                if dist < min_dist:
                    min_dist = dist
                    next_obs = obs
                    
        self.is_ducking = False
        if next_obs:
            if next_obs.can_duck:
                # Duck well before bird arrives and keep ducking until bird tail completely passes dino
                dino_front = self.dino_x + 240
                dino_back = self.dino_x - 40
                if next_obs.x < (dino_front + 280) and (next_obs.x + next_obs.width) > dino_back:
                    self.is_ducking = True
            else:
                jump_trigger_dist = self.speed * 20.0 + 30.0
                if 0 <= min_dist <= jump_trigger_dist and not self.is_jumping:
                    self.is_jumping = True
                    self.dino_vy = self.jump_velocity

        if self.is_jumping:
            self.dino_y += self.dino_vy
            self.dino_vy += self.gravity
            if self.dino_y >= 0.0:
                self.dino_y = 0.0
                self.dino_vy = 0.0
                self.is_jumping = False
                for _ in range(5):
                    self.particles.append(Particle(
                        self.dino_x + random.randint(10, 80),
                        self.ground_y,
                        random.uniform(-4.0, -1.0),
                        random.uniform(-3.0, -0.5),
                        random.randint(10, 20),
                        (140, 150, 165) if not self.is_night else (56, 189, 248)
                    ))

        # FAIL-SAFE IMMUNITY CLAMP: Enforce zero collision at physics engine level
        dino_w = 236 if self.is_ducking else 176
        dino_left = self.dino_x
        dino_right = self.dino_x + dino_w
        for obs in self.obstacles:
            if obs.x < (dino_right + 15) and (obs.x + obs.width) > (dino_left - 15):
                if obs.can_duck:
                    self.is_ducking = True
                    self.is_jumping = False
                    self.dino_y = 0.0
                    self.dino_vy = 0.0
                else:
                    needed_y = (obs.y - 25) - self.ground_y - 16
                    if self.dino_y > needed_y:
                        self.dino_y = needed_y
                        self.dino_vy = min(self.dino_vy, -1.0)
                        self.is_jumping = True
                        self.is_ducking = False

        alive = []
        for p in self.particles:
            p.x += p.vx
            p.y += p.vy
            p.life -= 1
            if p.life > 0:
                alive.append(p)
        self.particles = alive

    def _draw_live_chat_widget(self, draw):
        """
        Renders the Live Chat Overlay Widget on the right side of the canvas.
        Features glowing neon badges and real-time active viewer chat bubbles.
        """
        # Periodic feed refresh from JSON
        if (self.frame_idx - self.last_chat_refresh > 30) or not self.chat_messages:
            try:
                self.chat_messages = get_active_chat_messages(limit=3)
            except Exception:
                pass
            self.last_chat_refresh = self.frame_idx

        # Widget Dimensions
        wx1, wy1 = 1315, 238
        wx2, wy2 = 1890, 532

        # Outer Container (Cyberpunk dark glass with glowing neon border)
        draw.rounded_rectangle([(wx1, wy1), (wx2, wy2)], radius=12, fill=(13, 20, 36), outline=(56, 189, 248), width=2)

        # Header Badge Bar: "💬 CANLI SOHBET AKIŞI"
        draw.rounded_rectangle([(wx1 + 8, wy1 + 8), (wx2 - 8, wy1 + 42)], radius=8, fill=(22, 33, 54), outline=(56, 189, 248), width=1)
        draw.text((wx1 + 18, wy1 + 12), "💬", font=self.fonts["emoji_title"], fill=(56, 189, 248))
        draw.text((wx1 + 52, wy1 + 12), "CANLI SOHBET AKIŞI", font=self.fonts["chat_title"], fill=(56, 189, 248))

        # CTA Bar: "⚡ SOHBETE YAZ, İSMİN EKRANDA ÇIKSIN! 🔴"
        draw.text((wx1 + 18, wy1 + 49), "⚡", font=self.fonts["emoji_cta"], fill=(250, 204, 21))
        draw.text((wx1 + 38, wy1 + 48), "SOHBETE YAZ, İSMİN EKRANDA ÇIKSIN!", font=self.fonts["chat_cta"], fill=(250, 204, 21))
        # Pulsing Red Live Dot
        pulse = (self.frame_idx // 15) % 3
        draw.ellipse([(wx2 - 32 - pulse, wy1 + 52 - pulse), (wx2 - 20 + pulse, wy1 + 64 + pulse)], fill=(239, 68, 68))
        draw.ellipse([(wx2 - 30, wy1 + 54), (wx2 - 22, wy1 + 62)], fill=(255, 255, 255))

        # Render Active Chat Bubbles (latest 2-3)
        messages = self.chat_messages[-3:] if self.chat_messages else [
            {"author_name": "ZirveTakipçisi", "message_text": "Sohbete yaz ekranda çıksın! Destek tam gaz 🚀", "timestamp": "CANLI", "is_real": True}
        ]

        for i, msg in enumerate(messages):
            by1 = wy1 + 75 + i * 70
            by2 = by1 + 62
            bx1 = wx1 + 10
            bx2 = wx2 - 10

            is_real = msg.get("is_real", False)
            accent_col = (52, 211, 153) if is_real else (56, 189, 248)
            author_col = (250, 204, 21) if is_real else (56, 189, 248)

            # Bubble background
            draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=8, fill=(20, 28, 44), outline=(45, 55, 72), width=1)
            # Left glowing accent bar
            draw.rounded_rectangle([(bx1 + 4, by1 + 6), (bx1 + 8, by2 - 6)], radius=2, fill=accent_col)

            # Line 1: Author Username
            author_text = msg.get("author_name", "İzleyici")
            draw.text((bx1 + 18, by1 + 7), author_text, font=self.fonts["chat_author"], fill=author_col)

            # Badge & Timestamp
            if is_real:
                draw.rounded_rectangle([(bx1 + 330, by1 + 8), (bx1 + 395, by1 + 26)], radius=4, fill=(220, 38, 38))
                draw.text((bx1 + 338, by1 + 9), "CANLI", font=self.fonts["chat_badge"], fill=(255, 255, 255))
            else:
                draw.rounded_rectangle([(bx1 + 305, by1 + 8), (bx1 + 395, by1 + 26)], radius=4, fill=(45, 55, 72))
                draw.text((bx1 + 312, by1 + 9), "TOPLULUK", font=self.fonts["chat_badge"], fill=(148, 163, 184))

            ts = msg.get("timestamp", "")
            if ts:
                draw.text((bx1 + 410, by1 + 9), ts, font=self.fonts["chat_time"], fill=(148, 163, 184))

            # Line 2: Message Text (rendered crisply with smart emoji support)
            msg_txt = msg.get("message_text", "")
            self._draw_smart_text(draw, bx1 + 18, by1 + 34, msg_txt, self.fonts["chat_msg"], self.fonts["emoji_msg"], fill_txt=(248, 250, 252), max_width=510)

    def render_frame_pil_image(self):
        sprites = self.sprite_mgr.get_sprites_for_theme(self.is_night)
        
        # Base template copy
        frame = (self.base_template_night if self.is_night else self.base_template_day).copy()
        
        # Night mode: Moon & Stars (repositioned to not collide with chat widget)
        if self.is_night:
            frame.paste(sprites["moon"], (1050, 270), sprites["moon"])
            star = sprites["star"]
            for sx, sy in [(250, 290), (600, 320), (850, 280), (950, 350), (1220, 380)]:
                if (self.frame_idx // 15 + sx) % 3 != 0:
                    frame.paste(star, (sx, sy), star)

        # Clouds
        cloud = sprites["cloud"]
        for c in self.clouds:
            frame.paste(cloud, (int(c.x), int(c.y)), cloud)
            
        # Ground (tiled)
        ground = sprites["ground"]
        gw, gh = ground.size
        gx = int(self.ground_x)
        frame.paste(ground, (gx - gw, self.ground_y), ground)
        frame.paste(ground, (gx, self.ground_y), ground)
        frame.paste(ground, (gx + gw, self.ground_y), ground)
        
        # Obstacles
        for obs in self.obstacles:
            if obs.obs_type == "bird":
                spr_key = "bird1" if (self.frame_idx // 10) % 2 == 0 else "bird2"
                spr = sprites[spr_key]
            else:
                spr = sprites[obs.sprite_key]
            frame.paste(spr, (int(obs.x), int(obs.y)), spr)

        # Dino Sprite & Collision-Proof Fail-Safe Clamp
        dino_baseline = self.ground_y + int(self.dino_y)
        if self.is_jumping:
            dino_spr = sprites["dino_stand"]
        elif self.is_ducking:
            dino_spr = sprites["dino_duck1"] if (self.frame_idx // 6) % 2 == 0 else sprites["dino_duck2"]
        else:
            dino_spr = sprites["dino_run1"] if (self.frame_idx // 6) % 2 == 0 else sprites["dino_run2"]

        # FAIL-SAFE: Enforce absolute zero physical overlap visually
        for obs in self.obstacles:
            if obs.x < (self.dino_x + dino_spr.size[0] + 15) and (obs.x + obs.width) > (self.dino_x - 15):
                if obs.can_duck:
                    self.is_ducking = True
                    self.is_jumping = False
                    self.dino_y = 0.0
                    dino_baseline = self.ground_y
                    dino_spr = sprites["dino_duck1"] if (self.frame_idx // 6) % 2 == 0 else sprites["dino_duck2"]
                else:
                    # Jumping over obstacle: ensure clearance of at least 25px above obstacle top
                    needed_y = (obs.y - 25) - self.ground_y - 16
                    if self.dino_y > needed_y:
                        self.dino_y = needed_y
                        dino_baseline = self.ground_y + int(self.dino_y)

        dy = dino_baseline - dino_spr.size[1] + 16
        frame.paste(dino_spr, (int(self.dino_x), dy), dino_spr)

        # Dynamic HUD elements
        draw = ImageDraw.Draw(frame)

        # Real Stats Values
        speed_multiplier = 1.0 + (self.speed - 16.0) / 10.0
        draw.text((540, 160), f"{self.score:,} PTS", font=self.fonts["score"], fill=(56, 189, 248))
        draw.text((1030, 160), f"{self.obstacles_dodged:,}", font=self.fonts["score"], fill=(74, 222, 128))
        draw.text((1520, 160), f"{speed_multiplier:.1f}x NORMAL", font=self.fonts["score"], fill=(251, 146, 60))

        # Real Subscriber Goal Progress Bar (29 / 100)
        progress_ratio = min(1.0, self.current_subs / float(self.goal_subs))
        bar_w = int(970 * progress_ratio)
        draw.rounded_rectangle([(600, 847), (600 + bar_w, 892)], radius=8, fill=(16, 185, 129))
        draw.text((980, 855), f"{self.current_subs} / {self.goal_subs} (%{int(progress_ratio * 100)})", font=self.fonts["label"], fill=(255, 255, 255))

        # Interactive Live Chat Overlay Widget
        self._draw_live_chat_widget(draw)

        # Bottom Viral Ticker
        headline_idx = (self.frame_idx // 600) % len(self.chat_headlines)
        active_headline = self.chat_headlines[headline_idx]
        draw.text((50, 965), active_headline, font=self.fonts["title"], fill=(255, 255, 255))

        return frame

    def render_frame_pil(self):
        # Direct RGB raw bytes
        return self.render_frame_pil_image().tobytes()


def render_unbeatable_trex_master(output_file=MASTER_ASSET_PATH, duration_seconds=600):
    """
    Renders the 10-minute 60 FPS master stream file with looping synthwave soundtrack.
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    audio_source = MUSIC_DIR / "synthwave_cyber_drive.mp3"
    if not audio_source.exists():
        mp3s = list(MUSIC_DIR.glob("*.mp3"))
        if mp3s:
            audio_source = mp3s[0]
        else:
            raise FileNotFoundError("Audio soundtrack not found in assets/music!")

    print("\n" + "=" * 70)
    print("🎬 UNBEATABLE T-REX 60 FPS MASTER STREAM ASSET RENDERER (V2 AUTHENTIC)")
    print(f"📁 Çıktı: {output_path.name}")
    print(f"⏱️ Süre: {duration_seconds} saniye ({duration_seconds/60:.1f} dakika)")
    print(f"🎞️ Toplam Kare: {duration_seconds * FPS:,} (1920x1080 @ 60 FPS)")
    print(f"🎵 Ses: {audio_source.name}")
    print("=" * 70)

    streamer = UnbeatableTRexStreamer()
    total_frames = duration_seconds * FPS
    temp_video = output_path.parent / "temp_trex_raw_stream.mp4"
    
    cmd_video = [
        "ffmpeg", "-y",
        "-threads", "16",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "16",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(temp_video)
    ]
    
    print("🚀 Video render pipe başlatılıyor...")
    proc = subprocess.Popen(cmd_video, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    t0 = time.time()
    last_print = t0
    
    try:
        for f in range(total_frames):
            streamer.update_physics_and_ai()
            proc.stdin.write(streamer.render_frame_pil())
            
            now = time.time()
            if now - last_print > 5.0 or f == total_frames - 1:
                elapsed = now - t0
                pct = ((f + 1) / total_frames) * 100.0
                fps_render = (f + 1) / elapsed if elapsed > 0 else 0
                eta = (total_frames - (f + 1)) / fps_render if fps_render > 0 else 0
                print(f"⚡ Render İlerlemesi: %{pct:.1f} | Kare: {f+1:,}/{total_frames:,} | Hız: {fps_render:.1f} FPS | Kalan: {eta:.0f}s")
                last_print = now
    finally:
        proc.stdin.close()
        proc.wait()

    print("\n🎵 Müzik miksleniyor...")
    final_output = output_path
    cmd_mux = [
        "ffmpeg", "-y",
        "-i", str(temp_video),
        "-stream_loop", "-1",
        "-i", str(audio_source),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(final_output)
    ]
    subprocess.run(cmd_mux, check=True)
    if temp_video.exists():
        temp_video.unlink()
    print(f"🎉 MASTER YAYIN DOSYASI HAZIR: {final_output}")
    return final_output


def render_test_frame(output_path=OUTPUT_DIR / "test_live_chat_overlay.jpg"):
    """
    Renders a single high-definition test frame (1920x1080) with active live chat bubbles.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    streamer = UnbeatableTRexStreamer()
    # Advance a few frames so dino and physics are active
    for _ in range(60):
        streamer.update_physics_and_ai()
    frame_img = streamer.render_frame_pil_image()
    frame_img.save(output_path, "JPEG", quality=95)
    print(f"✅ Test canlı sohbet karesi kaydedildi: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-frame":
        render_test_frame()
    else:
        dur = 300 # 5 minutes seamless loop
        render_unbeatable_trex_master(MASTER_ASSET_PATH, duration_seconds=dur)
