"""
Viral Visual FX Toolkit for 'Zirvenin Kanunu' (@zirveninkanunu).
Generates high-CTR X (Twitter) Dark Mode post cards, neon glowing statistic pills,
meme reaction pop-ups, and FFmpeg filter chains for dynamic vertical video overlays.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEMES_DIR = BASE_DIR / "assets" / "memes"
OUTPUT_DIR = BASE_DIR / "output"

def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Tries Windows font locations with fallback to default PIL font."""
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts") / name.lower(),
        Path("assets/fonts") / name,
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

def render_tweet_card(
    author_name: str = "Elon Musk",
    handle: str = "@elonmusk",
    tweet_text: str = "Asistanım maaşına zam istediğinde ona 2 hafta izin verdim. Döndüğünde ise kovdum.",
    avatar_path: Optional[str] = None,
    likes: str = "582K",
    retweets: str = "64K",
    views: str = "3.8M",
    replies: str = "18.4K",
    verified: bool = True,
    output_path: str = "output/tweet_card.png",
    width: int = 920
) -> str:
    """
    Renders an authentic, pixel-perfect X / Twitter Dark Mode post card.
    Uses 2x supersampling for ultra-crisp typography and graphics.
    """
    scale_factor = 2
    sw = width * scale_factor
    
    font_name = _get_font("segoeuib.ttf", int(30 * scale_factor))
    font_handle = _get_font("segoeui.ttf", int(26 * scale_factor))
    font_body = _get_font("segoeui.ttf", int(34 * scale_factor))
    font_body_bold = _get_font("segoeuib.ttf", int(34 * scale_factor))
    font_meta = _get_font("segoeui.ttf", int(24 * scale_factor))
    font_metrics = _get_font("segoeuib.ttf", int(24 * scale_factor))
    
    pad_x = int(36 * scale_factor)
    avail_w = sw - pad_x * 2
    
    # Word wrap
    words = tweet_text.split()
    lines = []
    cur_line = []
    for w in words:
        test_line = " ".join(cur_line + [w])
        bbox = font_body.getbbox(test_line)
        line_w = bbox[2] - bbox[0]
        if line_w <= avail_w:
            cur_line.append(w)
        else:
            if cur_line:
                lines.append(cur_line)
            cur_line = [w]
    if cur_line:
        lines.append(cur_line)
        
    line_h = int(50 * scale_factor)
    body_h = len(lines) * line_h
    
    avatar_s = int(88 * scale_factor)
    header_h = avatar_s
    meta_h = int(32 * scale_factor)
    div_h = int(24 * scale_factor)
    metrics_h = int(48 * scale_factor)
    pad_y = int(36 * scale_factor)
    gap_y = int(22 * scale_factor)
    
    sh = pad_y + header_h + gap_y + body_h + gap_y + meta_h + div_h + metrics_h + pad_y
    
    canvas = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    
    # Background card with rounded corners
    bg_color = (0, 0, 0, 245)
    border_color = (47, 51, 54, 255)
    corner_r = int(26 * scale_factor)
    draw.rounded_rectangle((0, 0, sw - 1, sh - 1), radius=corner_r, fill=bg_color, outline=border_color, width=int(2 * scale_factor))
    
    # Avatar
    av_x = pad_x
    av_y = pad_y
    av_target = avatar_path or "assets/channel_avatar_logo.jpg"
    if not os.path.exists(av_target):
        av_target = str(BASE_DIR / "assets" / "channel_avatar_logo.jpg")
        
    if os.path.exists(av_target):
        try:
            av_img = Image.open(av_target).convert("RGBA").resize((avatar_s, avatar_s), Image.Resampling.LANCZOS)
            mask = Image.new("L", (avatar_s, avatar_s), 0)
            md = ImageDraw.Draw(mask)
            md.ellipse((0, 0, avatar_s, avatar_s), fill=255)
            canvas.paste(av_img, (av_x, av_y), mask)
            draw.ellipse((av_x, av_y, av_x + avatar_s, av_y + avatar_s), outline=(47, 51, 54, 200), width=int(1.5 * scale_factor))
        except Exception:
            pass
    else:
        # Default placeholder circular avatar
        draw.ellipse((av_x, av_y, av_x + avatar_s, av_y + avatar_s), fill=(29, 155, 240, 255))
        draw.text((av_x + int(24*scale_factor), av_y + int(18*scale_factor)), author_name[:1].upper(), fill=(255, 255, 255, 255), font=font_name)
        
    # Name & Handle
    text_x = av_x + avatar_s + int(18 * scale_factor)
    draw.text((text_x, av_y + int(4 * scale_factor)), author_name, fill=(247, 249, 249, 255), font=font_name)
    
    if verified:
        name_bbox = font_name.getbbox(author_name)
        name_w = name_bbox[2] - name_bbox[0]
        badge_x = text_x + name_w + int(12 * scale_factor)
        badge_y = av_y + int(10 * scale_factor)
        badge_r = int(14 * scale_factor)
        draw.ellipse((badge_x, badge_y, badge_x + badge_r * 2, badge_y + badge_r * 2), fill=(29, 155, 240, 255))
        chk_cx = badge_x + badge_r
        chk_cy = badge_y + badge_r
        draw.line([
            (chk_cx - int(6 * scale_factor), chk_cy),
            (chk_cx - int(2 * scale_factor), chk_cy + int(5 * scale_factor)),
            (chk_cx + int(7 * scale_factor), chk_cy - int(5 * scale_factor))
        ], fill=(255, 255, 255, 255), width=int(2.5 * scale_factor))
        
    draw.text((text_x, av_y + int(46 * scale_factor)), handle, fill=(113, 118, 123, 255), font=font_handle)
    
    # X Logo at top right
    x_logo_s = int(30 * scale_factor)
    x_logo_x = sw - pad_x - x_logo_s
    x_logo_y = av_y + int(10 * scale_factor)
    draw.line([(x_logo_x, x_logo_y), (x_logo_x + x_logo_s, x_logo_y + x_logo_s)], fill=(113, 118, 123, 255), width=int(3 * scale_factor))
    draw.line([(x_logo_x + x_logo_s, x_logo_y), (x_logo_x, x_logo_y + x_logo_s)], fill=(113, 118, 123, 255), width=int(3 * scale_factor))
    
    # Body
    cur_y = pad_y + header_h + gap_y
    for line_words in lines:
        curr_x = pad_x
        for w in line_words:
            color = (29, 155, 240, 255) if (w.startswith('#') or w.startswith('@')) else (247, 249, 249, 255)
            font_to_use = font_body_bold if (w.startswith('#') or w.startswith('@')) else font_body
            draw.text((curr_x, cur_y), w, fill=color, font=font_to_use)
            w_bbox = font_to_use.getbbox(w + " ")
            curr_x += (w_bbox[2] - w_bbox[0])
        cur_y += line_h
        
    cur_y += int(10 * scale_factor)
    meta_str = "23:40 · 12 Eki 2026 · " + views + " Görüntülenme"
    draw.text((pad_x, cur_y), meta_str, fill=(113, 118, 123, 255), font=font_meta)
    cur_y += int(34 * scale_factor)
    
    # Divider line
    draw.line([(pad_x, cur_y), (sw - pad_x, cur_y)], fill=(47, 51, 54, 255), width=int(1.5 * scale_factor))
    cur_y += int(18 * scale_factor)
    
    # Metrics row
    metrics = [
        ('reply', replies),
        ('retweet', retweets),
        ('like', likes),
        ('views', views),
        ('share', '')
    ]
    step_w = (sw - pad_x * 2) // len(metrics)
    s = scale_factor
    for idx, (m_type, m_val) in enumerate(metrics):
        col_x = pad_x + idx * step_w
        if m_type == 'reply':
            rx, ry = col_x, cur_y + int(4 * s)
            rw, rh = int(22 * s), int(16 * s)
            draw.rounded_rectangle((rx, ry, rx + rw, ry + rh), radius=int(4 * s), outline=(113, 118, 123, 255), width=int(2 * s))
            draw.polygon([(rx + int(3*s), ry + rh), (rx, ry + rh + int(5*s)), (rx + int(9*s), ry + rh)], fill=(113, 118, 123, 255))
        elif m_type == 'retweet':
            rtx, rty = col_x, cur_y + int(4 * s)
            rw, rh = int(24 * s), int(16 * s)
            draw.line([(rtx, rty + int(4*s)), (rtx + rw, rty + int(4*s)), (rtx + rw, rty + int(8*s))], fill=(113, 118, 123, 255), width=int(2*s))
            draw.polygon([(rtx + rw - int(3*s), rty + int(7*s)), (rtx + rw + int(4*s), rty + int(7*s)), (rtx + rw, rty + int(12*s))], fill=(113, 118, 123, 255))
            draw.line([(rtx + rw, rty + rh - int(4*s)), (rtx, rty + rh - int(4*s)), (rtx, rty + rh - int(8*s))], fill=(113, 118, 123, 255), width=int(2*s))
            draw.polygon([(rtx + int(3*s), rty + rh - int(7*s)), (rtx - int(4*s), rty + rh - int(7*s)), (rtx, rty + rh - int(12*s))], fill=(113, 118, 123, 255))
        elif m_type == 'like':
            hx, hy = col_x + int(10 * s), cur_y + int(12 * s)
            draw.polygon([
                (hx, hy + 11*s), (hx - 9*s, hy + 2*s), (hx - 9*s, hy - 5*s), (hx - 5*s, hy - 9*s),
                (hx, hy - 5*s), (hx + 5*s, hy - 9*s), (hx + 9*s, hy - 5*s), (hx + 9*s, hy + 2*s)
            ], fill=(249, 24, 128, 255))
        elif m_type == 'views':
            ax, ay = col_x, cur_y + int(4 * s)
            draw.rectangle((ax, ay + 10*s, ax + 2*s, ay + 18*s), fill=(113, 118, 123, 255))
            draw.rectangle((ax + 5*s, ay + 4*s, ax + 7*s, ay + 18*s), fill=(113, 118, 123, 255))
            draw.rectangle((ax + 10*s, ay + 7*s, ax + 12*s, ay + 18*s), fill=(113, 118, 123, 255))
            draw.rectangle((ax + 15*s, ay + 0*s, ax + 17*s, ay + 18*s), fill=(113, 118, 123, 255))
        elif m_type == 'share':
            sx, sy = col_x, cur_y + int(4 * s)
            draw.line([(sx + 8*s, sy), (sx + 8*s, sy + 14*s)], fill=(113, 118, 123, 255), width=int(2*s))
            draw.polygon([(sx + 5*s, sy + 4*s), (sx + 11*s, sy + 4*s), (sx + 8*s, sy)], fill=(113, 118, 123, 255))
            draw.arc((sx, sy + 8*s, sx + 16*s, sy + 18*s), start=0, end=180, fill=(113, 118, 123, 255), width=int(2*s))
            
        if m_val:
            m_color = (249, 24, 128, 255) if m_type == 'like' else (113, 118, 123, 255)
            draw.text((col_x + int(32 * s), cur_y), m_val, fill=m_color, font=font_metrics)
            
    final_img = canvas.resize((width, sh // scale_factor), Image.Resampling.LANCZOS)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(str(out_p), "PNG")
    return str(out_p)

def render_statistic_pill(
    text: str = "🚨 İNSANLARIN %99'U BU TUZAĞA DÜŞÜYOR",
    highlight_num: str = "%99'U",
    output_path: str = "output/stat_pill.png",
    width: int = 860
) -> str:
    """
    Renders a glowing neon dark pill badge with high-contrast text and stats.
    """
    scale_factor = 2
    sw = width * scale_factor
    
    font_text = _get_font("segoeuib.ttf", int(36 * scale_factor))
    font_hl = _get_font("segoeuib.ttf", int(40 * scale_factor))
    
    # Text breakdown
    parts = []
    if highlight_num and highlight_num in text:
        idx = text.index(highlight_num)
        pre = text[:idx]
        post = text[idx + len(highlight_num):]
        if pre: parts.append((pre, False))
        parts.append((highlight_num, True))
        if post: parts.append((post, False))
    else:
        parts.append((text, False))
        
    total_w = 0
    measured = []
    for s_str, is_hl in parts:
        fnt = font_hl if is_hl else font_text
        bbox = fnt.getbbox(s_str)
        w = bbox[2] - bbox[0]
        measured.append((s_str, is_hl, fnt, bbox, w))
        total_w += w
        
    pad_h = int(40 * scale_factor)
    card_w = max(int(700 * scale_factor), total_w + pad_h * 2)
    card_h = int(98 * scale_factor)
    
    glow_m = int(20 * scale_factor)
    canvas_w = card_w + glow_m * 2
    canvas_h = card_h + glow_m * 2
    
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    x0, y0 = glow_m, glow_m
    x1, y1 = x0 + card_w, y0 + card_h
    corner_r = card_h // 2
    
    # Outer glow
    glow_layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.rounded_rectangle(
        (x0 - 4*scale_factor, y0 - 4*scale_factor, x1 + 4*scale_factor, y1 + 4*scale_factor),
        radius=corner_r + 4*scale_factor,
        outline=(255, 69, 0, 180),
        width=int(10*scale_factor)
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=int(12 * scale_factor)))
    
    glow_inner = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    gid = ImageDraw.Draw(glow_inner)
    gid.rounded_rectangle(
        (x0 - 2*scale_factor, y0 - 2*scale_factor, x1 + 2*scale_factor, y1 + 2*scale_factor),
        radius=corner_r + 2*scale_factor,
        outline=(255, 215, 0, 240),
        width=int(5*scale_factor)
    )
    glow_inner = glow_inner.filter(ImageFilter.GaussianBlur(radius=int(5 * scale_factor)))
    
    canvas.alpha_composite(glow_layer)
    canvas.alpha_composite(glow_inner)
    
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=corner_r, fill=(10, 12, 18, 235), outline=(255, 215, 0, 255), width=int(3 * scale_factor))
    draw.rounded_rectangle((x0 + 3*scale_factor, y0 + 3*scale_factor, x1 - 3*scale_factor, y1 - 3*scale_factor),
                           radius=corner_r - 3*scale_factor, outline=(255, 255, 255, 50), width=int(1 * scale_factor))
                           
    start_x = x0 + (card_w - total_w) // 2
    center_y = y0 + card_h // 2
    
    curr_x = start_x
    for s_str, is_hl, fnt, bbox, w in measured:
        t_y = center_y - (bbox[3] + bbox[1]) // 2
        if is_hl:
            draw.text((curr_x + int(2*scale_factor), t_y + int(2*scale_factor)), s_str, fill=(0, 0, 0, 220), font=fnt)
            draw.text((curr_x, t_y), s_str, fill=(255, 235, 59, 255), font=fnt)
        else:
            draw.text((curr_x + int(2*scale_factor), t_y + int(2*scale_factor)), s_str, fill=(0, 0, 0, 180), font=fnt)
            draw.text((curr_x, t_y), s_str, fill=(255, 255, 255, 255), font=fnt)
        curr_x += w
        
    final_w = canvas_w // scale_factor
    final_h = canvas_h // scale_factor
    final_img = canvas.resize((final_w, final_h), Image.Resampling.LANCZOS)
    
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    final_img.save(str(out_p), "PNG")
    return str(out_p)

def get_meme_path(meme_name: str) -> Optional[Path]:
    """Returns the path of a transparent reaction meme."""
    name_clean = meme_name.lower().replace("-", "_").replace(" ", "_")
    candidates = [
        MEMES_DIR / f"{name_clean}.png",
        MEMES_DIR / f"{name_clean}.jpg",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None
