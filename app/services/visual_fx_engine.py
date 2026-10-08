"""
Visual FX Engine for Zirvenin Kanunu.
Injects high-retention visual hooks:
1. Hypnotic Neon Bottom Progress Bar (drawbox filter animated with time t)
2. Premium Series Badge HUD at top (Pill badge in ASS)
3. Subtle Cinematic vignette or lighting accents
"""

from pathlib import Path
import re


def generate_neon_progress_bar_filter(total_duration: float, color: str = "0x00D7FF@0.85", height: int = 8) -> str:
    """
    Generates an FFmpeg video filter expression that draws an animated neon progress bar
    at the very bottom of a 1080x1920 Short.
    width = 1080 * (t / total_duration)
    """
    dur_safe = max(1.0, total_duration)
    # y=1912 puts it just above the absolute bottom edge
    y_pos = 1920 - height - 2
    # Background subtle dark track
    bg_bar = f"drawbox=x=0:y={y_pos}:w=1080:h={height}:color=0x000000@0.45:t=fill"
    # Active glowing neon fill
    active_bar = f"drawbox=x=0:y={y_pos}:w='1080*(t/{dur_safe:.3f})':h={height}:color={color}:t=fill"
    return f"{bg_bar},{active_bar}"


def generate_series_badge_event(badge_text: str, start_time: str, end_time: str) -> str:
    """
    Generates an ASS subtitle event line for a fixed, elegant top series badge HUD.
    Positioned at Alignment 8 (top center), Y=120px.
    """
    # Stylized clean frosted badge
    # \\an8 = Top Center, \\pos(540, 120), font size 36, semi-bold, subtle gold/silver tint
    clean_badge = badge_text.strip().upper()
    return (
        f"Dialogue: 1,{start_time},{end_time},SeriesBadge,,0,0,0,,"
        f"{{\\an8\\pos(540,125)\\bord2\\shad1\\c&H00E0E0E0&\\3c&H00000000&}}[ ⚡ {clean_badge} ]"
    )


def inject_badge_into_ass(ass_file: Path, badge_title: str, total_duration: float):
    """
    Appends the series badge style and full-length event to an existing ASS file.
    """
    if not ass_file.exists():
        return

    with open(ass_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Define SeriesBadge style if not present
    badge_style = (
        "Style: SeriesBadge,Arial,34,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,1,2,0,8,10,10,10,1\n"
    )

    if "Style: SeriesBadge" not in content:
        content = content.replace("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n",
                                f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n{badge_style}")

    # Format end time
    mins = int(total_duration // 60)
    secs = int(total_duration % 60)
    centis = int(round((total_duration - int(total_duration)) * 100))
    end_str = f"0:{mins:02d}:{secs:02d}.{centis:02d}"

    badge_event = generate_series_badge_event(badge_title, "0:00:00.00", end_str)

    # Append event to [Events] section
    content += f"\n{badge_event}\n"

    with open(ass_file, "w", encoding="utf-8") as f:
        f.write(content)
