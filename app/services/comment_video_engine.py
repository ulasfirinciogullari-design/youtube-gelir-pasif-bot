"""
Comment & Review Video Engine for 'Zirvenin Kanunu' (@zirveninkanunu).
Generates authentic, high-CTR delivery review cards (Yemeksepeti / Trendyol / Getir style),
channel header banners, and dynamic social comment bubbles for viral 'Yorum Okuma' Shorts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def _get_font(name: str = "arialbd.ttf", size: int = 32) -> ImageFont.FreeTypeFont:
    """Finds Windows fonts with fallback."""
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts") / name.lower(),
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for c in candidates:
        if c.exists():
            try:
                return ImageFont.truetype(str(c), size)
            except Exception:
                pass
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()

def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> List[str]:
    """Wraps text into lines that fit within max_width."""
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def render_channel_header(
    title_hook: str = "YEMEKSEPETİ'NİN EN ABSÜRT 1 YILDIZ YORUMLARI 😂",
    channel_name: str = "ZİRVENİN YORUMLARI",
    handle: str = "@zirveninkanunu",
    output_path: Optional[str] = None
) -> Path:
    """Renders the top header banner (1080x230)."""
    if output_path is None:
        output_path = str(OUTPUT_DIR / "comment_header_banner.png")
    
    w, h = 1080, 230
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background gradient slate
    draw.rounded_rectangle([(15, 12), (w - 15, h - 12)], radius=24, fill=(18, 19, 28, 245), outline=(55, 58, 80, 255), width=2)
    
    # Top golden accent line
    draw.rounded_rectangle([(30, 16), (w - 30, 22)], radius=3, fill=(255, 215, 0, 255))

    # Avatar Circle
    avatar_x, avatar_y, avatar_r = 75, 75, 42
    draw.ellipse([(avatar_x - avatar_r, avatar_y - avatar_r), (avatar_x + avatar_r, avatar_y + avatar_r)], fill=(35, 30, 15, 255), outline=(255, 215, 0, 255), width=3)
    
    # Crown in avatar
    crown_font = _get_font("arialbd.ttf", 36)
    draw.text((avatar_x - 18, avatar_y - 22), "👑", fill=(255, 215, 0, 255), font=crown_font)

    # Channel Name & Verified
    name_font = _get_font("arialbd.ttf", 34)
    draw.text((140, 42), channel_name, fill=(255, 255, 255, 255), font=name_font)
    
    # Verified badge (blue circle with white check)
    badge_x = 140 + int(draw.textlength(channel_name, font=name_font)) + 15
    draw.ellipse([(badge_x, 48), (badge_x + 24, 72)], fill=(29, 155, 240, 255))
    check_font = _get_font("arialbd.ttf", 16)
    draw.text((badge_x + 6, 51), "✓", fill=(255, 255, 255, 255), font=check_font)

    # Handle
    handle_font = _get_font("segoeui.ttf", 22)
    draw.text((140, 82), f"{handle} • Abone Ol", fill=(160, 165, 185, 255), font=handle_font)

    # Category / Hook Pill at bottom
    pill_font = _get_font("arialbd.ttf", 26)
    pill_text = title_hook.upper()
    pill_bbox = draw.textbbox((0, 0), pill_text, font=pill_font)
    pill_w = pill_bbox[2] - pill_bbox[0] + 40
    pill_h = 44
    pill_x1 = int((w - pill_w) / 2)
    pill_y1 = 150
    pill_x2 = pill_x1 + pill_w
    pill_y2 = pill_y1 + pill_h

    # Glow under pill
    draw.rounded_rectangle([(pill_x1 - 2, pill_y1 - 2), (pill_x2 + 2, pill_y2 + 2)], radius=18, fill=(255, 107, 107, 80))
    # Pill body
    draw.rounded_rectangle([(pill_x1, pill_y1), (pill_x2, pill_y2)], radius=16, fill=(235, 47, 47, 240), outline=(255, 180, 180, 255), width=2)
    draw.text((pill_x1 + 20, pill_y1 + 8), pill_text, fill=(255, 255, 255, 255), font=pill_font)

    img.save(output_path, "PNG")
    return Path(output_path)

def render_restaurant_review_card(
    restaurant_name: str = "Öz Hakiki Urfa Lahmacun & Kebap",
    rating: str = "1.0",
    scores: Tuple[int, int, int] = (10, 1, 1),  # (Hız, Servis, Lezzet)
    customer_name: str = "Mehmet K.",
    date_str: str = "Dün 20:45",
    review_text: str = "Lahmacun o kadar acıydı ki evin kedisi ezan okumaya başladı. Ayranla ateşi söndürelim dedik, ayran kaynıyordu!",
    restaurant_reply: str = "Kedinin hidayete ermesine vesile olduysak ne mutlu bize kardeşim. Afiyet olsun, acımız tazedir.",
    highlight_punchline: bool = False,
    output_path: Optional[str] = None
) -> Path:
    """Renders the authentic Delivery App Review Card (1000px wide)."""
    if output_path is None:
        output_path = str(OUTPUT_DIR / "review_card_master.png")

    card_w = 1000
    # Temporary draw to calculate exact height
    temp_img = Image.new("RGBA", (100, 100))
    temp_draw = ImageDraw.Draw(temp_img)
    
    body_font = _get_font("arialbd.ttf", 32)
    reply_font = _get_font("arialbd.ttf", 28)

    review_lines = _wrap_text(review_text, body_font, 900, temp_draw)
    reply_lines = _wrap_text(restaurant_reply, reply_font, 840, temp_draw)

    # Dynamic height calculation
    review_h = len(review_lines) * 44 + 30
    reply_h = len(reply_lines) * 38 + 75
    total_h = 160 + review_h + reply_h + 40

    img = Image.new("RGBA", (card_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer Card Background & Shadow Glow
    draw.rounded_rectangle([(6, 6), (card_w - 6, total_h - 6)], radius=28, fill=(22, 24, 34, 250), outline=(60, 65, 88, 255), width=2)

    # 1. Header Section: Restaurant Name + Rating
    food_icon_font = _get_font("arialbd.ttf", 36)
    draw.text((40, 32), "🌯", fill=(255, 255, 255, 255), font=food_icon_font)

    name_font = _get_font("arialbd.ttf", 32)
    draw.text((95, 32), restaurant_name, fill=(255, 255, 255, 255), font=name_font)

    # Star Badge (Red for 1-star)
    star_badge_x1 = card_w - 180
    draw.rounded_rectangle([(star_badge_x1, 28), (card_w - 40, 72)], radius=14, fill=(225, 45, 57, 255))
    star_font = _get_font("arialbd.ttf", 26)
    draw.text((star_badge_x1 + 14, 34), f"⭐ {rating}", fill=(255, 255, 255, 255), font=star_font)

    # 2. Score Pills: Hız, Servis, Lezzet
    pill_y = 90
    # Hız (Speed)
    speed_color = (46, 204, 113, 255) if scores[0] >= 8 else (231, 76, 60, 255)
    draw.rounded_rectangle([(40, pill_y), (180, pill_y + 36)], radius=10, fill=(35, 40, 52, 255), outline=speed_color, width=2)
    score_font = _get_font("arialbd.ttf", 19)
    draw.text((52, pill_y + 7), f"Hız: {scores[0]}/10 ⚡", fill=speed_color, font=score_font)

    # Servis
    serv_color = (46, 204, 113, 255) if scores[1] >= 8 else (231, 76, 60, 255)
    draw.rounded_rectangle([(200, pill_y), (355, pill_y + 36)], radius=10, fill=(35, 40, 52, 255), outline=serv_color, width=2)
    draw.text((212, pill_y + 7), f"Servis: {scores[1]}/10 ❌", fill=serv_color, font=score_font)

    # Lezzet
    lez_color = (46, 204, 113, 255) if scores[2] >= 8 else (231, 76, 60, 255)
    draw.rounded_rectangle([(375, pill_y), (535, pill_y + 36)], radius=10, fill=(35, 40, 52, 255), outline=lez_color, width=2)
    draw.text((387, pill_y + 7), f"Lezzet: {scores[2]}/10 ❌", fill=lez_color, font=score_font)

    # Meta: Customer info & Date
    meta_font = _get_font("segoeui.ttf", 20)
    meta_str = f"👤 {customer_name} • {date_str} • ✓ Onaylı Sipariş"
    draw.text((560, pill_y + 8), meta_str, fill=(150, 155, 175, 255), font=meta_font)

    # Divider Line
    draw.line([(40, 142), (card_w - 40, 142)], fill=(45, 48, 65, 255), width=2)

    # 3. Customer Review Text
    curr_y = 160
    for line in review_lines:
        if highlight_punchline and ("ezan" in line.lower() or "kedisi" in line.lower() or "kaynıyordu" in line.lower()):
            # Highlight glow box behind punchline
            bbox = draw.textbbox((40, curr_y), line, font=body_font)
            draw.rounded_rectangle([(bbox[0] - 6, bbox[1] - 4), (bbox[2] + 6, bbox[3] + 4)], radius=8, fill=(255, 215, 0, 70))
            draw.text((40, curr_y), line, fill=(255, 245, 160, 255), font=body_font)
        else:
            draw.text((40, curr_y), line, fill=(245, 245, 252, 255), font=body_font)
        curr_y += 44

    # 4. Restaurant Reply Box (Restoran Yanıtı)
    curr_y += 20
    reply_box_y1 = curr_y
    reply_box_y2 = curr_y + reply_h
    
    # Orange rounded background box
    draw.rounded_rectangle([(40, reply_box_y1), (card_w - 40, reply_box_y2)], radius=18, fill=(38, 28, 30, 255), outline=(235, 120, 35, 255), width=2)
    # Left thick orange bar
    draw.rounded_rectangle([(40, reply_box_y1), (50, reply_box_y2)], radius=6, fill=(255, 125, 30, 255))

    # Header in Reply Box
    chef_font = _get_font("arialbd.ttf", 24)
    draw.text((68, reply_box_y1 + 16), "👨‍🍳 RESTORAN YANITI:", fill=(255, 150, 50, 255), font=chef_font)

    # Text in Reply Box
    reply_text_y = reply_box_y1 + 54
    for r_line in reply_lines:
        draw.text((68, reply_text_y), r_line, fill=(255, 240, 220, 255), font=reply_font)
        reply_text_y += 38

    img.save(output_path, "PNG")
    return Path(output_path)

def render_social_comment_bubble(
    username: str = "@berkay_06",
    comment_text: str = "Kedinin din değiştirmesi şoku beni bitirdi dsklfjsdk 😭😂",
    likes: str = "14.2K",
    time_ago: str = "1 saat önce",
    avatar_color: Tuple[int, int, int] = (255, 107, 107),
    output_path: Optional[str] = None
) -> Path:
    """Renders an authentic social comment reaction bubble (880px wide)."""
    if output_path is None:
        output_path = str(OUTPUT_DIR / "comment_bubble_sample.png")

    w = 880
    temp_img = Image.new("RGBA", (100, 100))
    temp_draw = ImageDraw.Draw(temp_img)
    
    comm_font = _get_font("arialbd.ttf", 28)
    lines = _wrap_text(comment_text, comm_font, 720, temp_draw)
    
    h = 80 + len(lines) * 38 + 25

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Bubble background
    draw.rounded_rectangle([(4, 4), (w - 4, h - 4)], radius=22, fill=(24, 26, 38, 245), outline=(65, 70, 95, 255), width=2)

    # Avatar Circle with initial
    av_x, av_y, av_r = 55, 48, 28
    draw.ellipse([(av_x - av_r, av_y - av_r), (av_x + av_r, av_y + av_r)], fill=avatar_color)
    init_letter = username.strip("@")[0].upper() if username.strip("@") else "U"
    init_font = _get_font("arialbd.ttf", 26)
    draw.text((av_x - 9, av_y - 15), init_letter, fill=(255, 255, 255, 255), font=init_font)

    # Username
    user_font = _get_font("arialbd.ttf", 26)
    draw.text((105, 24), username, fill=(0, 229, 255, 255), font=user_font)

    # Time ago
    time_font = _get_font("segoeui.ttf", 19)
    time_x = 105 + int(draw.textlength(username, font=user_font)) + 14
    draw.text((time_x, 28), f"• {time_ago}", fill=(140, 145, 165, 255), font=time_font)

    # Comment Lines
    cy = 68
    for line in lines:
        draw.text((105, cy), line, fill=(255, 255, 255, 255), font=comm_font)
        cy += 38

    # Bottom Right Likes
    likes_font = _get_font("arialbd.ttf", 22)
    like_str = f"❤️ {likes}"
    like_w = int(draw.textlength(like_str, font=likes_font))
    draw.text((w - like_w - 28, h - 36), like_str, fill=(255, 107, 129, 255), font=likes_font)

    img.save(output_path, "PNG")
    return Path(output_path)

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("Testing Comment Video Engine renderers...")
    h_path = render_channel_header()
    print("Rendered Header Banner:", h_path)
    r_path = render_restaurant_review_card()
    print("Rendered Review Card:", r_path)
    c_path = render_social_comment_bubble()
    print("Rendered Comment Bubble:", c_path)
