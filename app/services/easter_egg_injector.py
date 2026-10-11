"""
Easter Egg Flash Frame Injector.
Injects a 0.05-second (3 frames @ 60 FPS) mysterious golden glyph / crown cipher
into the video around 50-60% duration to skyrocket viewer retention, re-watches, and comment engagement.
Also provides on-screen viral HUD overlays:
- Top: "🎧 KULAKLIKLA DİNLE: 8D SES"
- Bottom: "👁️ GİZLİ ŞİFREYİ BULUP DAKİKASINI YAZAN İLK KİŞİ SABİTLENECEK 👇"
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def generate_golden_cipher_glyph(
    output_path: Path,
    cipher_code: str = "#ZIRVE777-ALPHA",
    width: int = 1080,
    height: int = 1920
) -> Path:
    """
    Renders a stunning 1080x1920 mysterious golden glyph and crown cipher card
    used as the viral 0.05s flash frame.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Base dark background with vignette
    img = Image.new("RGBA", (width, height), (8, 9, 14, 255))
    draw = ImageDraw.Draw(img)

    # Draw dark gold radial gradient / glows
    center_x, center_y = width // 2, height // 2
    for r in range(480, 50, -30):
        alpha = int(45 * (1 - r / 480))
        draw.ellipse(
            [center_x - r, center_y - r, center_x + r, center_y + r],
            fill=(218, 165, 32, alpha)
        )

    # 2. Ornate Golden Borders and Sacred Geometry
    gold_outer = (255, 215, 0, 240)
    gold_inner = (218, 165, 32, 200)
    gold_glow = (255, 235, 120, 255)

    # Outer decorative frame
    margin = 40
    draw.rectangle(
        [margin, margin, width - margin, height - margin],
        outline=gold_outer,
        width=4
    )
    draw.rectangle(
        [margin + 12, margin + 12, width - margin - 12, height - margin - 12],
        outline=gold_inner,
        width=2
    )

    # Center Cipher Box
    box_w, box_h = 920, 680
    box_x0 = center_x - box_w // 2
    box_y0 = center_y - box_h // 2
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h

    # Translucent dark luxury backing
    draw.rectangle([box_x0, box_y0, box_x1, box_y1], fill=(12, 14, 22, 230), outline=gold_outer, width=5)
    draw.rectangle([box_x0 + 8, box_y0 + 8, box_x1 - 8, box_y1 - 8], outline=gold_glow, width=2)

    # Corner corner ornaments
    corner_size = 35
    for cx, cy in [(box_x0, box_y0), (box_x1, box_y0), (box_x0, box_y1), (box_x1, box_y1)]:
        draw.rectangle([cx - 8, cy - 8, cx + 8, cy + 8], fill=gold_outer)

    # Font setup
    font_bold_path = r"C:\Windows\Fonts\arialbd.ttf"
    font_impact_path = r"C:\Windows\Fonts\impact.ttf"

    font_title = ImageFont.truetype(font_bold_path, 42) if os.path.exists(font_bold_path) else ImageFont.load_default()
    font_cipher = ImageFont.truetype(font_impact_path, 80) if os.path.exists(font_impact_path) else font_title
    font_sub = ImageFont.truetype(font_bold_path, 34) if os.path.exists(font_bold_path) else font_title
    font_crown = ImageFont.truetype(font_bold_path, 110) if os.path.exists(font_bold_path) else font_title

    # Header in box
    header_text = "GIZLI SIFRE DEDEKTORU"
    draw.text((center_x, box_y0 + 70), header_text, fill=gold_glow, font=font_title, anchor="mm")

    # Render Handcrafted Radiant Golden Crown Vector Icon
    crown_base_y = box_y0 + 260
    crown_pts = [
        (center_x - 130, crown_base_y),
        (center_x + 130, crown_base_y),
        (center_x + 130, crown_base_y - 70),
        (center_x + 85, crown_base_y - 35),
        (center_x + 60, crown_base_y - 110),
        (center_x + 25, crown_base_y - 45),
        (center_x, crown_base_y - 135),
        (center_x - 25, crown_base_y - 45),
        (center_x - 60, crown_base_y - 110),
        (center_x - 85, crown_base_y - 35),
        (center_x - 130, crown_base_y - 70),
    ]
    draw.polygon(crown_pts, fill=gold_outer, outline=gold_glow)
    # Crown jewels
    for jx, jy in [(center_x, crown_base_y - 145), (center_x - 60, crown_base_y - 120), (center_x + 60, crown_base_y - 120), (center_x - 130, crown_base_y - 80), (center_x + 130, crown_base_y - 80)]:
        draw.ellipse([jx - 9, jy - 9, jx + 9, jy + 9], fill=(255, 255, 230, 255), outline=gold_outer, width=2)
    # Crown headband band
    draw.rectangle([center_x - 135, crown_base_y - 18, center_x + 135, crown_base_y + 8], fill=(180, 130, 20, 255), outline=gold_glow, width=2)
    for bx in range(center_x - 110, center_x + 120, 45):
        draw.ellipse([bx - 6, crown_base_y - 11, bx + 6, crown_base_y + 1], fill=(255, 50, 50, 255))

    # Secret Code
    draw.text((center_x, box_y0 + 340), cipher_code, fill=(255, 245, 160, 255), font=font_cipher, anchor="mm")

    # Call to action text
    sub_text1 = "TEBRIKLER! BU KAREYI YAKALAYAN ILK KISISIN."
    sub_text2 = "DAKIKASINI VE KODU YORUMA YAZ!"
    draw.text((center_x, box_y0 + 450), sub_text1, fill=(255, 255, 255, 255), font=font_sub, anchor="mm")
    draw.text((center_x, box_y0 + 520), sub_text2, fill=gold_glow, font=font_sub, anchor="mm")

    # Watermark / Brand Badge
    draw.text((center_x, height - 120), "ZIRVENIN KANUNU * RESMI SIFRE ODASI", fill=(180, 150, 60, 200), font=font_sub, anchor="mm")

    # Save artifact
    rgb_img = img.convert("RGB")
    rgb_img.save(str(output_path), "JPEG", quality=98)
    return output_path


def get_easter_egg_timing(
    total_duration: float,
    pct_position: float = 0.54,
    flash_duration: float = 0.05
) -> Tuple[float, float]:
    """
    Computes exact start and end seconds for the 0.05s flash frame (3 frames @ 60 FPS).
    Places it strictly around 50% - 60% of total video runtime.
    """
    clamped_pct = max(0.50, min(pct_position, 0.60))
    start_t = round(total_duration * clamped_pct, 3)
    end_t = round(start_t + flash_duration, 3)
    return start_t, end_t


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_8d_retention_ass(
    timed_sentences: list,
    output_path: Path,
    total_duration: float,
    top_cta: str = "🎧 KULAKLIKLA DİNLE: 8D SES",
    bottom_cta: str = "👁️ GİZLİ ŞİFREYİ BULUP DAKİKASINI YAZAN İLK KİŞİ SABİTLENECEK 👇",
    chunk_size: int = 3,
    subtitle_margin_v: int = 460
) -> Path:
    """
    Renders ASS subtitles combining:
    1. Persistent Top 8D Audio CTA Banner: "🎧 KULAKLIKLA DİNLE: 8D SES"
    2. Persistent / Pulsing Bottom Easter Egg Challenge Magnet:
       "👁️ GİZLİ ŞİFREYİ BULUP DAKİKASINI YAZAN İLK KİŞİ SABİTLENECEK 👇"
    3. Kinetic word-by-word bouncing subtitle events.
    """
    from app.services.kinetic_subtitle_fx import split_into_word_chunks, get_power_word_info
    
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    font_name = "Montserrat ExtraBold"
    ass_header = f"""[Script Info]
Title: Zirvenin Kanunu 8D Audio & Easter Egg Master Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HormoziActive,{font_name},52,&H0000FFFF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,105,105,2,0,1,6,2,2,40,40,{subtitle_margin_v},1
Style: HormoziNormal,{font_name},48,&H00FFFFFF,&H00000000,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,5,2,2,40,40,{subtitle_margin_v},1
Style: Top8DHUD,{font_name},36,&H0000D7FF,&H00000000,&H00000000,&HC0000000,-1,0,0,0,104,104,2,0,1,5,2,8,30,30,120,1
Style: EasterEggHUD,{font_name},28,&H0000FFFF,&H00000000,&H00000000,&HD0000000,-1,0,0,0,100,100,1,0,1,4,2,2,24,24,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [ass_header]
    end_time_str = format_ass_time(total_duration)
    
    # 1. Top HUD (8D Audio Callout)
    lines.append(
        f"Dialogue: 1,0:00:00.00,{end_time_str},Top8DHUD,,0,0,0,,{{\\c&H00D7FF&\\b1}}{top_cta}\n"
    )
    
    # 2. Bottom HUD (Easter Egg Cipher Challenge)
    lines.append(
        f"Dialogue: 1,0:00:00.50,{end_time_str},EasterEggHUD,,0,0,0,,{{\\c&H00FFFF&\\b1}}{bottom_cta}\n"
    )
    
    # 3. Kinetic Word-by-Word Subtitles
    chunks = split_into_word_chunks(timed_sentences, chunk_size=chunk_size)
    for chunk in chunks:
        words = chunk["words"]
        for active_idx, active_item in enumerate(words):
            w_start = format_ass_time(active_item["start"])
            w_end = format_ass_time(active_item["end"])
            
            chunk_fragments = []
            for idx, item in enumerate(words):
                w_text = item["word"]
                p_info = get_power_word_info(w_text)
                
                if idx == active_idx:
                    if p_info:
                        emoji, color_bgr = p_info
                        chunk_fragments.append(
                            f"{{\\c&H{color_bgr}&\\t(0,90,\\fscx124\\fscy124)\\t(90,180,\\fscx108\\fscy108)\\b1\\bord7}}{emoji} {w_text.upper()}{{\\r\\c&HFFFFFF&\\b1}}"
                        )
                    else:
                        chunk_fragments.append(
                            f"{{\\c&H002EFFFF&\\t(0,90,\\fscx122\\fscy122)\\t(90,180,\\fscx106\\fscy106)\\b1\\bord7}}{w_text.upper()}{{\\r\\c&HFFFFFF&\\b1}}"
                        )
                else:
                    chunk_fragments.append(
                        f"{{\\c&HFFFFFF&\\b1}}{w_text.upper()}"
                    )
            rendered_chunk = " ".join(chunk_fragments)
            lines.append(f"Dialogue: 2,{w_start},{w_end},HormoziActive,,0,0,0,,{rendered_chunk}\n")
            
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
        
    return output_path


def extract_easter_egg_proof(
    video_path: Path,
    output_image: Path,
    timestamp_sec: float
) -> Path:
    """
    Takes a high-res screenshot directly at the easter egg frame timestamp
    to prove exact injection.
    """
    output_image = Path(output_image).resolve()
    output_image.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{timestamp_sec:.3f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(output_image)
    ]
    subprocess.run(cmd, check=True)
    return output_image


if __name__ == "__main__":
    glyph_file = Path("output/golden_cipher_glyph.jpg")
    generate_golden_cipher_glyph(glyph_file)
    print(f"✅ Golden Cipher Glyph Generated: {glyph_file} ({glyph_file.stat().st_size} bytes)")
