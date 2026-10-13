"""
Generate 1080x1920 High-CTR Custom Thumbnail for Sequential Kolpacino Master Short.
Features:
- Sharp center movie frame with iconic facial expression
- Ambient blurred background
- Gold border accents
- Channel Header badge
- Authentic Comment Card preview
- Glassmorphism Footer
"""

import sys
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

def generate_thumbnail():
    frame_path = OUTPUT_DIR / "kolpa_scene_frame_18.jpg"
    if not frame_path.exists():
        frame_path = OUTPUT_DIR / "kolpacino_1080p_f10.jpg"
    base_frame = Image.open(frame_path).convert("RGBA")

    # 1. Background blurred vertical canvas (1080x1920)
    bg = base_frame.resize((1080, 1920), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(25))
    dark_tint = Image.new("RGBA", (1080, 1920), (0, 0, 0, 115))
    bg.paste(dark_tint, (0, 0), dark_tint)

    # 2. Sharp center video frame (1080x608)
    center = base_frame.resize((1080, 608), Image.Resampling.LANCZOS)
    bg.paste(center, (0, 380))

    # 3. Gold border lines
    draw = ImageDraw.Draw(bg)
    draw.rectangle([(0, 374), (1080, 380)], fill=(255, 215, 0))
    draw.rectangle([(0, 988), (1080, 994)], fill=(255, 215, 0))

    # 4. Header (Playwright Glassmorphism)
    header_path = OUTPUT_DIR / "kolpacino_header_playwright.png"
    if header_path.exists():
        header_img = Image.open(header_path).convert("RGBA")
        h_w, h_h = header_img.size
        target_w = 1000
        target_h = int(h_h * (target_w / h_w))
        header_resized = header_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        bg.paste(header_resized, (40, 40), header_resized)

    # 5. Authentic Social Card 1 (sabri_magduru @user-kd9sm2pl)
    c1_path = OUTPUT_DIR / "real_social_c1.png"
    if c1_path.exists():
        c1_img = Image.open(c1_path).convert("RGBA")
        c_w, c_h = c1_img.size
        target_w = 960
        target_h = int(c_h * (target_w / c_w))
        c1_resized = c1_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        bg.paste(c1_resized, (60, 1040), c1_resized)

    # 6. Clean Footer (Playwright Glassmorphism)
    footer_path = OUTPUT_DIR / "kolpacino_footer_playwright.png"
    if footer_path.exists():
        footer_img = Image.open(footer_path).convert("RGBA")
        f_w, f_h = footer_img.size
        target_w = 1000
        target_h = int(f_h * (target_w / f_w))
        footer_resized = footer_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        bg.paste(footer_resized, (40, 1640), footer_resized)

    out_thumb = OUTPUT_DIR / "REAL_VIRAL_KOLPACINO_SEQUENTIAL_MASTER_thumb.jpg"
    bg.convert("RGB").save(out_thumb, "JPEG", quality=95)
    print(f"✅ Generated Sequential Master Thumbnail: {out_thumb} ({out_thumb.stat().st_size} bytes)")
    return out_thumb

if __name__ == "__main__":
    generate_thumbnail()
